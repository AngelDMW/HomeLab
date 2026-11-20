import json, os, re, socket, subprocess, time
from datetime import datetime, timezone
from urllib.parse import urljoin
import httpx
import dns.flags, dns.message, dns.query, dns.rcode, dns.resolver
from .config import settings
from .db import add_sample, kv_get, kv_set
from .twin_engine import docker_client, node2_metrics, get_node2_mac

TIMEOUT = 8.0

def _http_get(url, headers=None, max_text=200000):
    t0 = time.time()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, verify=False) as c:
            r = c.get(url, headers=headers or {})
        ms = round((time.time()-t0)*1000,1)
        text = r.text or ''
        if max_text and len(text) > max_text:
            text = text[:max_text]
        return {'ok': r.status_code < 400, 'status_code': r.status_code, 'latency_ms': ms, 'text': text}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'latency_ms': round((time.time()-t0)*1000,1)}

def probe_threadfin():
    base = 'http://192.168.1.3:34400'
    m3u = _http_get(base + '/m3u/threadfin.m3u')
    xml = _http_get(base + '/xmltv/threadfin.xml')
    channels = 0
    if m3u.get('ok') and m3u.get('text'):
        # count EXTINF entries
        channels = len(re.findall(r'^#EXTINF', m3u['text'], re.M))
    empty = (not m3u.get('ok')) or channels == 0 or (not xml.get('ok')) or len((xml.get('text') or '').strip()) < 100
    # read last_run.json
    last_run = None
    path = os.path.join(settings.threadfin_logs_dir, 'last_run.json')
    try:
        if os.path.exists(path):
            with open(path) as f:
                last_run = json.load(f)
    except Exception as e:
        last_run = {'error': str(e)}
    prev = kv_get('threadfin_channels')
    dropped = False
    if prev and channels and isinstance(prev, int) and prev > 0:
        # alert if dropped significantly (>10% or to zero-ish)
        if channels < prev * 0.9:
            dropped = True
    if channels:
        kv_set('threadfin_channels', channels)
    sync_failed = False
    if isinstance(last_run, dict):
        # look for failure indicators
        txt = json.dumps(last_run).lower()
        if 'fail' in txt or 'error' in txt or last_run.get('ok') is False or last_run.get('success') is False:
            # but avoid false positives; check explicit rc/status
            if last_run.get('rc') not in (None, 0) or last_run.get('status') in ('failed','error') or last_run.get('errors'):
                sync_failed = True
    return {
        'name': 'Threadfin', 'port': 34400,
        'm3u_ok': m3u.get('ok'), 'xml_ok': xml.get('ok'),
        'channels': channels, 'channels_dropped': dropped, 'prev_channels': prev,
        'empty': empty, 'sync_failed': sync_failed,
        'last_run': last_run,
        'status': 'ok' if (m3u.get('ok') and xml.get('ok') and channels>0 and not sync_failed and not dropped) else 'degraded' if (m3u.get('ok') or xml.get('ok')) else 'down'
    }

def probe_pihole():
    # UDP DNS query to Pi-hole
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ['192.168.1.3']
    resolver.timeout = 3.0
    resolver.lifetime = 3.0
    def q(name):
        t0=time.time()
        try:
            ans = resolver.resolve(name, 'A')
            ms = round((time.time()-t0)*1000,1)
            ips = [r.to_text() for r in ans]
            return {'ok': True, 'ips': ips, 'latency_ms': ms}
        except Exception as e:
            ms = round((time.time()-t0)*1000,1)
            return {'ok': False, 'error': str(e), 'latency_ms': ms}
    good = q('google.com')
    blocked = q('doubleclick.net')
    # Pi-hole often returns 0.0.0.0 or blocks; filtering state ok if blocked is 0.0.0.0 or refused/nxdomain depending
    bips = blocked.get('ips') or []
    filtering = any(ip.startswith('0.0.0.0') or ip == '::' for ip in bips) or (not blocked.get('ok'))
    # but NXDOMAIN/refused can also mean blocking; treat resolution to 0.0.0.0 as clear signal
    if '0.0.0.0' in bips: filtering = True
    status = 'ok' if good.get('ok') else 'down'
    return {
        'name': 'Pi-hole', 'port': 53,
        'resolve_ok': good.get('ok'), 'resolve_latency_ms': good.get('latency_ms'),
        'blocked_test': blocked, 'filtering_active': filtering,
        'status': status
    }

def probe_unbound_dnssec():
    """Unbound recursivo de raíz: resolución normal + validación DNSSEC real."""
    def q(name):
        t0 = time.time()
        try:
            msg = dns.message.make_query(name, 'A', want_dnssec=True)
            resp = dns.query.udp(msg, '127.0.0.1', port=5335, timeout=4.0)
            answers = []
            for rrset in resp.answer[:2]:
                answers.extend(rr.to_text() for rr in rrset)
            return {
                'ok': resp.rcode() == dns.rcode.NOERROR,
                'rcode': dns.rcode.to_text(resp.rcode()),
                'ad': bool(resp.flags & dns.flags.AD),
                'answers': answers[:4],
                'latency_ms': round((time.time() - t0) * 1000, 1),
            }
        except Exception as e:
            return {'ok': False, 'rcode': None, 'ad': False, 'error': str(e),
                    'latency_ms': round((time.time() - t0) * 1000, 1)}

    normal = q('github.com')
    sigfail = q('sigfail.verteiltesysteme.net')
    sigok = q('sigok.verteiltesysteme.net')
    resolve_ok = normal.get('rcode') == 'NOERROR'
    # Firma inválida => SERVFAIL; firma válida => NOERROR con flag AD.
    dnssec_reject_ok = sigfail.get('rcode') == 'SERVFAIL'
    dnssec_accept_ok = sigok.get('rcode') == 'NOERROR' and sigok.get('ad') is True
    dnssec_ok = dnssec_reject_ok and dnssec_accept_ok
    if not resolve_ok:
        status = 'down'
    elif not dnssec_ok:
        status = 'degraded'
    else:
        status = 'ok'
    return {
        'name': 'Unbound DNSSEC', 'port': 5335,
        'resolve_ok': resolve_ok, 'resolve': normal,
        'dnssec_ok': dnssec_ok,
        'dnssec_reject_ok': dnssec_reject_ok, 'sigfail': sigfail,
        'dnssec_accept_ok': dnssec_accept_ok, 'sigok': sigok,
        'status': status,
    }


def _crowdsec_exec(args, timeout=8):
    cli = docker_client()
    if not cli:
        return None, 'docker unavailable'
    try:
        c = cli.containers.get('crowdsec')
        if c.status != 'running':
            return None, f'container {c.status}'
        res = c.exec_run(args)
        out = (res.output or b'').decode('utf-8', 'ignore')
        if res.exit_code != 0:
            return None, (out.strip() or f'exit {res.exit_code}')
        return out, None
    except Exception as e:
        return None, str(e)


def _cscli_json(args):
    out, err = _crowdsec_exec(['cscli'] + args + ['-o', 'json'])
    if out is None:
        return None, err
    try:
        return json.loads(out), None
    except Exception as e:
        return None, f'json parse: {e}'


def probe_crowdsec_ips():
    """CrowdSec LAPI/IPS: motor activo, bouncer, whitelist y amenazas bloqueadas."""
    decisions, dec_err = _cscli_json(['decisions', 'list'])
    alerts, alerts_err = _cscli_json(['alerts', 'list', '--limit', '100'])
    bouncers, bouncers_err = _cscli_json(['bouncers', 'list'])
    parsers, parsers_err = _cscli_json(['parsers', 'list'])
    metrics, metrics_err = _cscli_json(['metrics'])

    errors = {k: v for k, v in {
        'decisions': dec_err, 'alerts': alerts_err, 'bouncers': bouncers_err,
        'parsers': parsers_err, 'metrics': metrics_err,
    }.items() if v}

    decisions = decisions if isinstance(decisions, list) else []
    alerts = alerts if isinstance(alerts, list) else []
    bouncers = bouncers if isinstance(bouncers, list) else []
    parser_list = (parsers or {}).get('parsers') if isinstance(parsers, dict) else []
    parser_list = parser_list if isinstance(parser_list, list) else []

    whitelist_loaded = any(
        p.get('name') == 'crowdsecurity/mywhitelists'
        and 'enabled' in str(p.get('status', '')).lower()
        for p in parser_list
    )

    active_bouncers = []
    now = datetime.now(timezone.utc)
    for b in bouncers:
        if not isinstance(b, dict) or b.get('revoked'):
            continue
        age = None
        lp = b.get('last_pull')
        if lp:
            try:
                dt = datetime.fromisoformat(str(lp).replace('Z', '+00:00'))
                age = round((now - dt).total_seconds(), 1)
            except Exception:
                age = None
        active_bouncers.append({'name': b.get('name'), 'type': b.get('type'),
                                'last_pull_age_s': age,
                                'validated': b.get('validated', True)})
    bouncer_ok = any(
        (b.get('last_pull_age_s') is not None and b.get('last_pull_age_s') <= 180)
        for b in active_bouncers
    )

    acq = (metrics or {}).get('acquisition') if isinstance(metrics, dict) else {}
    acq = acq if isinstance(acq, dict) else {}
    lines_read = sum(int(v.get('reads') or 0) for v in acq.values() if isinstance(v, dict))
    lines_parsed = sum(int(v.get('parsed') or 0) for v in acq.values() if isinstance(v, dict))
    lines_whitelisted = sum(int(v.get('whitelisted') or 0) for v in acq.values() if isinstance(v, dict))

    engine_active = bool(metrics is not None and not metrics_err)
    if not engine_active:
        status = 'down'
    elif not bouncer_ok or not whitelist_loaded:
        status = 'degraded'
    else:
        status = 'ok'
    return {
        'name': 'CrowdSec IPS', 'port': 8085,
        'engine_active': engine_active, 'lapi_ok': bool(metrics is not None),
        'decisions': len(decisions), 'alerts': len(alerts),
        'alerts_truncated': len(alerts) >= 100,
        'bouncers': active_bouncers, 'bouncer_ok': bouncer_ok,
        'whitelist_loaded': whitelist_loaded,
        'acquisition': {'lines_read': lines_read, 'lines_parsed': lines_parsed,
                        'lines_whitelisted': lines_whitelisted},
        'status': status, 'errors': errors or None,
    }


def probe_nextcloud():
    # try localhost/127.0.0.1 first to avoid trusted-domain issues; fallback to LAN IP
    r = None
    for url in ['http://127.0.0.1:8080/status.php', 'http://localhost:8080/status.php', 'http://192.168.1.3:8080/status.php']:
        rr = _http_get(url, max_text=4000)
        r = rr
        body = {}
        try:
            if rr.get('text'):
                body = json.loads(rr['text'])
        except Exception:
            body = {}
        ok = rr.get('ok') and (body.get('installed') in (True, 'true', 1))
        if ok:
            return {'name':'Nextcloud','port':8080,'endpoint':'/status.php','url':url,'ok':True,'details':{k: body.get(k) for k in ('installed','maintenance','needsDbUpgrade','versionstring') if k in body},'status':'ok'}
        # if we got explicit installed json even with oddities, accept
        if isinstance(body, dict) and body.get('installed') is True:
            return {'name':'Nextcloud','port':8080,'endpoint':'/status.php','url':url,'ok':True,'details':body,'status':'ok'}
    # last result
    body = {}
    try:
        if r and r.get('text'): body = json.loads(r['text'])
    except Exception:
        body = {}
    ok = bool(r and r.get('ok') and (body.get('installed') in (True, 'true', 1)))
    return {'name':'Nextcloud','port':8080,'endpoint':'/status.php','ok':ok,'details':body or ({'status_code': r.get('status_code')} if r else {}),'status':'ok' if ok else 'down','error':(r or {}).get('error')}

def probe_immich():
    r = _http_get('http://192.168.1.3:2283/api/server/ping')
    ok = r.get('ok') and ('pong' in (r.get('text') or '').lower() or r.get('status_code')==200)
    # check immich external mount
    mount_ok = None; mount_info = None
    try:
        cli = docker_client()
        if cli:
            c = cli.containers.get('immich-server')
            mounts = c.attrs.get('Mounts') or []
            for m in mounts:
                if m.get('Destination') == '/usr/src/app/external/nextcloud-photos':
                    mount_ok = (m.get('RW') is False)
                    mount_info = {'source': m.get('Source'), 'ro': (m.get('RW') is False), 'type': m.get('Type')}
                    break
            if mount_ok is None:
                mount_ok = False; mount_info = {'error': 'mount not found'}
    except Exception as e:
        mount_info = {'error': str(e)}
    return {'name':'Immich','port':2283,'endpoint':'/api/server/ping','ok':ok,'mount_nextcloud_photos_ro': mount_ok,'mount_info': mount_info,'status':'ok' if ok else 'down','error':r.get('error')}

def probe_jellyfin():
    r = _http_get('http://192.168.1.3:8096/System/Info/Public')
    body = {}
    try:
        if r.get('text'): body = json.loads(r['text'])
    except Exception: pass
    ok = r.get('ok') and (body.get('ServerName') or r.get('status_code')==200)
    return {'name':'Jellyfin','port':8096,'endpoint':'/System/Info/Public','ok':ok,'details':{k: body.get(k) for k in ('ServerName','Version','ProductName') if isinstance(body,dict)},'status':'ok' if ok else 'down','error':r.get('error')}

def check_containers_and_logs():
    cli = docker_client()
    issues = []
    summaries = []
    if not cli:
        return {'issues': [{'component':'docker','issue':'docker unavailable'}], 'summaries': []}
    try:
        for c in cli.containers.list(all=True):
            st = c.attrs.get('State',{})
            name = c.name
            health = (st.get('Health') or {}).get('Status')
            restarting = st.get('Restarting', False)
            status = c.status
            if health == 'unhealthy' or restarting or status == 'restarting':
                issues.append({'component': name, 'issue': f'state={status} health={health} restarting={restarting}', 'type':'container'})
            # scan recent logs
            try:
                logs = c.logs(tail=60, since=int(time.time())-600).decode('utf-8','ignore')
            except Exception:
                logs = ''
            log_text = logs
            # optiplex-chatops: exclude transient Telegram polling false positives
            if name == 'optiplex-chatops':
                filtered_lines = []
                for line in logs.splitlines():
                    ll = line.lower()
                    if '[warn]' in ll and 'efatal' in ll and ('api.telegram.org' in ll or 'etimedout' in ll or 'econnreset' in ll or 'enotfound' in ll or 'econnrefused' in ll):
                        continue
                    filtered_lines.append(line)
                log_text = '\n'.join(filtered_lines)
            # immich-postgres: el FATAL de arranque ("database system is starting up" /
            # "in recovery mode") es transitorio; la salud real ya la cubren el healthcheck
            # del contenedor y la sonda L7 /api/server/ping.
            if name == 'immich-postgres':
                lines = logs.splitlines()
                log_text = '\n'.join(l for l in lines
                    if not ('fatal' in l.lower() and (
                        'database system is starting up' in l.lower() or
                        'in recovery mode' in l.lower())))
            # seerr-theme: nginx puede registrar 502 mientras jellyseerr arranca.
            # Solo se considera ruido si existe una respuesta 200 posterior.
            if name == 'seerr-theme':
                lines = logs.splitlines()
                last_ok = max((i for i, l in enumerate(lines) if ' 200 ' in l), default=-1)
                log_text = '\n'.join(l for i, l in enumerate(lines)
                    if not (last_ok >= 0 and i < last_ok and
                            'connect() failed (111: connection refused)' in l.lower()))
            patterns = []
            for pat in ['OOM', 'Out of memory', 'FATAL', 'I/O error', 'Input/output error', 'connection refused', 'Connection refused']:
                if pat.lower() in log_text.lower():
                    patterns.append(pat)
            if patterns:
                # de-dup
                patterns = sorted(set(patterns))
                issues.append({'component': name, 'issue': 'log patterns: '+', '.join(patterns), 'type':'logs', 'patterns': patterns})
                summaries.append({'name': name, 'patterns': patterns, 'tail': logs.splitlines()[-60:]})
    except Exception as e:
        issues.append({'component':'docker','issue':str(e)})
    return {'issues': issues, 'summaries': summaries}

def disk_margin_check():
    # /dev/sda1 margin via hostfs root (df-style percentage)
    import shutil
    base = settings.hostfs if os.path.isdir(settings.hostfs) else '/'
    try:
        u = shutil.disk_usage(base)
        used_pct = round(((u.total-u.free)/u.total)*100,1) if u.total else None
        return {'ok': used_pct is not None and used_pct < 95, 'used_pct': used_pct, 'free_gb': round(u.free/(1024**3),2), 'warn_92': used_pct is not None and used_pct >= 92, 'critical_95': used_pct is not None and used_pct >= 95}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def probe_nfs_node2_storage():
    path = '/mnt/node2-storage'
    res = {
        'name': 'NFS /mnt/node2-storage',
        'path': path,
        'mounted': False,
        'accessible': False,
        'mount_type': None,
        'status': 'down',
    }
    try:
        mounted = os.path.ismount(path)
        res['mounted'] = mounted
        # detect fstype
        try:
            with open('/proc/mounts') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == path:
                        res['mount_type'] = parts[2]
                        break
        except Exception:
            pass
        if mounted:
            # accessibility/read test
            try:
                os.listdir(path)
                testfile = os.path.join(path, '.sentinel_nfs_probe')
                # write test may fail on :ro bind mount inside container; try read-only checks primarily
                try:
                    with open(testfile, 'w') as tf:
                        tf.write('ok')
                    with open(testfile) as tf:
                        _ = tf.read()
                    os.remove(testfile)
                    res['write_ok'] = True
                except Exception as we:
                    res['write_ok'] = False
                    res['write_error'] = str(we)
                res['accessible'] = True
            except Exception as e:
                res['accessible'] = False
                res['error'] = str(e)
        else:
            res['error'] = 'not mounted'
        # consider mounted+accessible as ok even if write fails due to ro mount in sentinel
        if res['mounted'] and res['accessible']:
            res['ok'] = True
            res['status'] = 'ok'
        else:
            res['ok'] = False
            res['status'] = 'down'
    except Exception as e:
        res['ok'] = False
        res['status'] = 'error'
        res['error'] = str(e)
    return res



def probe_node2_portainer():
    url = settings.node2_portainer_url.rstrip('/') if hasattr(settings, 'node2_portainer_url') else 'https://192.168.1.4:9001'
    # availability TCP/TLS: any HTTP response means reachable
    r = _http_get(url, max_text=500)
    ok = r.get('status_code') is not None  # got response (even 401/404) => TCP/TLS up
    status = 'ok' if ok else 'down'
    return {
        'name': 'Nodo 2 Portainer Agent',
        'host': settings.node2_host,
        'port': 9001,
        'endpoint': '/ (TCP/TLS)',
        'ok': ok,
        'status_code': r.get('status_code'),
        'latency_ms': r.get('latency_ms'),
        'status': status,
        'error': r.get('error') if not ok else None,
    }

def probe_node2_immich_ml():
    base = settings.node2_ml_url.rstrip('/') if hasattr(settings, 'node2_ml_url') else 'http://192.168.1.4:3003'
    r = _http_get(base + '/ping', max_text=500)
    text = (r.get('text') or '').strip().lower()
    ok = r.get('ok') and ('pong' in text or r.get('status_code') == 200)
    return {
        'name': 'Nodo 2 Immich ML',
        'host': settings.node2_host,
        'port': 3003,
        'endpoint': '/ping',
        'url': base + '/ping',
        'ok': ok,
        'response': (r.get('text') or '')[:200],
        'latency_ms': r.get('latency_ms'),
        'status': 'ok' if ok else 'down',
        'error': r.get('error'),
    }

def probe_node2_metrics():
    host_up = _http_get(f"http://{settings.node2_host}:3003/ping", max_text=10).get('status_code') is not None
    # better: rely on ping_host via twin cached? do quick check
    try:
        import subprocess as _sp
        pr = _sp.run(['ping','-c','1','-W','1', settings.node2_host], capture_output=True, timeout=3)
        online = pr.returncode == 0
    except Exception:
        online = False
    m = node2_metrics(online=online)
    mac = get_node2_mac()
    ram = m.get('ram') or {}
    storage = m.get('storage') or {}
    ram_alert = bool(ram.get('alert_85'))
    status = 'ok'
    if not online:
        status = 'down'
    elif ram_alert or not m.get('ssh_ok', False):
        status = 'degraded' if m.get('ssh_ok') else 'degraded'
    if ram_alert:
        status = 'warning'
    return {
        'name': 'Nodo 2 (métricas)',
        'host': settings.node2_host,
        'mac': mac,
        'online': online,
        'ssh_ok': m.get('ssh_ok'),
        'ssh_error': m.get('ssh_error'),
        'ram': ram,
        'storage': storage,
        'ram_alert_85': ram_alert,
        'status': status,
    }


def run_all_probes():
    results = {}
    for key, fn in [
        ('threadfin', probe_threadfin),
        ('pihole', probe_pihole),
        ('unbound_dnssec', probe_unbound_dnssec),
        ('crowdsec_ips', probe_crowdsec_ips),
        ('nextcloud', probe_nextcloud),
        ('immich', probe_immich),
        ('jellyfin', probe_jellyfin),
        ('node2_portainer', probe_node2_portainer),
        ('node2_immich_ml', probe_node2_immich_ml),
        ('node2_metrics', probe_node2_metrics),
    ]:
        try:
            results[key] = fn()
        except Exception as e:
            results[key] = {'name': key, 'status': 'error', 'error': str(e)}
        try:
            add_sample(f'probe:{key}', results[key].get('status','unknown'), {'ok': results[key].get('ok') or results[key].get('status')=='ok'})
        except Exception:
            pass
    try:
        results['containers_logs'] = check_containers_and_logs()
    except Exception as e:
        results['containers_logs'] = {'issues': [{'component':'docker','issue':str(e)}], 'summaries': []}
    try:
        results['disk'] = disk_margin_check()
    except Exception as e:
        results['disk'] = {'ok': False, 'error': str(e)}
    try:
        results['nfs_node2_storage'] = probe_nfs_node2_storage()
    except Exception as e:
        results['nfs_node2_storage'] = {'name':'NFS /mnt/node2-storage','ok': False, 'status': 'error', 'error': str(e)}
    kv_set('last_probes', results)
    return results

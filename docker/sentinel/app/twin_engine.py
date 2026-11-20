import json, os, re, shutil, socket, subprocess, time
from pathlib import Path
import psutil
import docker
from .config import settings
from .db import add_sample, kv_get, kv_set

_docker = None

def docker_client():
    global _docker
    if _docker is None:
        try:
            _docker = docker.from_env()
        except Exception:
            _docker = None
    return _docker

def _host_path(p):
    # prefer hostfs mount for host files
    if p.startswith('/'):
        hp = os.path.join(settings.hostfs, p.lstrip('/'))
        if os.path.exists(hp):
            return hp
    return p

def _read_first(paths):
    for p in paths:
        try:
            with open(p, 'r') as f:
                return f.read().strip()
        except Exception:
            continue
    return None

def cpu_temp_c():
    # try host /sys mounts
    bases = [os.path.join(settings.hostfs, 'sys'), '/host/sys', '/sys']
    for b in bases:
        tdir = os.path.join(b, 'class', 'thermal')
        if os.path.isdir(tdir):
            for name in sorted(os.listdir(tdir)):
                if not name.startswith('thermal_zone'):
                    continue
                t = _read_first([os.path.join(tdir, name, 'temp')])
                typ = _read_first([os.path.join(tdir, name, 'type')]) or ''
                if t and t.isdigit():
                    val = int(t) / 1000.0 if int(t) > 1000 else float(t)
                    if 0 < val < 120:
                        return round(val, 1)
        hdir = os.path.join(b, 'class', 'hwmon')
        if os.path.isdir(hdir):
            for hw in sorted(os.listdir(hdir)):
                for fn in sorted(os.listdir(os.path.join(hdir, hw))):
                    if fn.startswith('temp') and fn.endswith('_input'):
                        t = _read_first([os.path.join(hdir, hw, fn)])
                        if t and t.lstrip('-').isdigit():
                            val = int(t) / 1000.0 if abs(int(t)) > 1000 else float(t)
                            if 0 < val < 120:
                                return round(val, 1)
    return None

def disk_info():
    path = settings.hostfs if os.path.isdir(settings.hostfs) else '/'
    try:
        u = shutil.disk_usage(path)
        # df-style used percentage based on available space to avoid under-reporting
        used_pct = round(((u.total - u.free) / u.total) * 100, 1) if u.total else None
        used_gb = round((u.total - u.free) / (1024**3), 2)
        return {
            'mount': '/dev/sda1 (/)',
            'total_gb': round(u.total / (1024**3), 2),
            'used_gb': used_gb,
            'free_gb': round(u.free / (1024**3), 2),
            'used_pct': used_pct,
            'alert_92': bool(used_pct is not None and used_pct >= 92),
            'critical_95': bool(used_pct is not None and used_pct >= 95),
        }
    except Exception as e:
        return {'error': str(e)}

def ram_info():
    try:
        # prefer host /proc/meminfo via hostfs
        meminfo = os.path.join(settings.hostfs, 'proc', 'meminfo') if settings.hostfs else '/proc/meminfo'
        if os.path.exists(meminfo):
            info = {}
            with open(meminfo) as f:
                for line in f:
                    if ':' in line:
                        k,v = line.split(':',1)
                        info[k.strip()] = v.strip()
            def kb(key):
                try: return int(info.get(key,'0 kB').split()[0])
                except: return 0
            total = kb('MemTotal'); avail = kb('MemAvailable')
            used = total - avail if total and avail else kb('MemTotal') - kb('MemFree') - kb('Buffers') - kb('Cached')
            pct = round((used/total)*100,1) if total else None
            return {'total_mb': round(total/1024,1), 'used_mb': round(used/1024,1), 'available_mb': round(avail/1024,1), 'used_pct': pct}
        vm = psutil.virtual_memory()
        return {'total_mb': round(vm.total/(1024**2),1), 'used_mb': round(vm.used/(1024**2),1), 'available_mb': round(vm.available/(1024**2),1), 'used_pct': vm.percent}
    except Exception as e:
        return {'error': str(e)}

def tailscale_info():
    # check tailscale interface presence via hostfs /sys or ip
    candidates = ['tailscale0']
    present = False
    ips = []
    for b in [os.path.join(settings.hostfs, 'sys/class/net'), '/sys/class/net']:
        if os.path.isdir(b) and 'tailscale0' in os.listdir(b):
            present = True
            break
    try:
        # try reading address
        addr4 = os.path.join(settings.hostfs, 'sys/class/net/tailscale0')
        if os.path.isdir(addr4):
            present = True
    except Exception:
        pass
    # try `tailscale ip` via hostfs not easy; parse /proc/net/fib_trie no; use `ip -4 addr` in hostfs namespace not possible, fallback check common ip
    if not present:
        present = os.path.exists(os.path.join(settings.hostfs, 'var/run/tailscale/tailscaled.sock'))
    return {'interface': 'tailscale0', 'present': present}

def quicksync_info():
    dev = '/dev/dri/renderD128'
    exists = os.path.exists(dev)
    return {'device': dev, 'present': exists}

def security_posture():
    """Postura de defensa activa: motores y cadena del bouncer en el host."""
    posture = {'dns_mode': 'recursive-unbound-dnssec', 'containers': {}, 'firewall_bouncer': {}}
    cli = docker_client()
    if cli:
        for name in ('unbound', 'crowdsec'):
            try:
                c = cli.containers.get(name)
                st = c.attrs.get('State') or {}
                posture['containers'][name] = {
                    'status': c.status,
                    'health': (st.get('Health') or {}).get('Status'),
                    'restarting': st.get('Restarting', False),
                }
            except Exception as e:
                posture['containers'][name] = {'status': 'missing', 'error': str(e)}
    rules = []
    fw = posture['firewall_bouncer']
    try:
        r = subprocess.run(['iptables', '-S', 'CROWDSEC_CHAIN'],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            rules = [ln for ln in r.stdout.splitlines() if ln.strip()]
            fw.update({
                'chain_present': True,
                'rules': len([ln for ln in rules if ln.startswith('-A')]),
                'deny_rule_active': any('-j DROP' in ln for ln in rules),
            })
        else:
            fw.update({'chain_present': False, 'error': (r.stderr or '').strip()})
    except Exception as e:
        fw.update({'chain_present': False, 'error': str(e)})
    cfg = os.path.join(settings.hostfs, 'etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml')
    fw['config_present'] = os.path.exists(cfg)
    return posture



def _read_node2_mac():
    # try ARP/neigh
    try:
        r = subprocess.run(['ip', 'neigh', 'show', settings.node2_host], capture_output=True, text=True, timeout=3)
        m = re.search(r'lladdr\s+([0-9a-f:]{17})', r.stdout, re.I)
        if m:
            return m.group(1).lower()
    except Exception:
        pass
    # try hostfs bot config
    for path in [
        os.path.join(settings.hostfs, 'home/mrdavid/HomeLab/docker/telegram-bot/.env'),
        '/homelab/docker/telegram-bot/.env',
    ]:
        try:
            if os.path.exists(path):
                with open(path) as f:
                    for line in f:
                        if line.startswith('NODE2_MAC='):
                            mac = line.strip().split('=', 1)[1].strip().strip('"').strip("'")
                            if mac:
                                return mac.lower()
        except Exception:
            pass
    return (settings.node2_mac or '').lower() or None

def get_node2_mac():
    mac = _read_node2_mac()
    if mac:
        kv_set('node2_mac', mac)
        return mac
    return kv_get('node2_mac') or settings.node2_mac

def ssh_node2(cmd, timeout=8):
    """Run command on node2 via host ssh (nsenter). Returns (ok, stdout, stderr)."""
    base = ['ssh', '-i', settings.node2_ssh_key,
            '-o', 'BatchMode=yes', '-o', f'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null', '-p', '22',
            f'{settings.node2_ssh_user}@{settings.node2_host}', cmd]
    cmds = []
    if shutil.which('nsenter'):
        cmds.append(['nsenter', '-t', '1', '-m', '-u', '-n', '-i', '--'] + base)
    cmds.append(base)
    last = None
    for c in cmds:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return True, r.stdout.strip(), r.stderr.strip()
            last = (r.returncode, r.stdout.strip(), r.stderr.strip())
        except Exception as e:
            last = (-1, '', str(e))
    if last:
        return False, last[1], last[2]
    return False, '', 'ssh failed'

def node2_metrics(online=True):
    info = {'online': online}
    if not online:
        return info
    ok, out, err = ssh_node2("free -b | awk '/^Mem:/ {print $2, $3, $7}'; echo '---'; df -B1 --output=size,used,avail,pcent,target /mnt/storage 2>/dev/null | tail -1", timeout=10)
    if not ok:
        info['ssh_ok'] = False
        info['ssh_error'] = err or out
        return info
    info['ssh_ok'] = True
    try:
        parts = out.split('---')
        mem = parts[0].strip().split()
        if len(mem) >= 3:
            total = int(mem[0]); used = int(mem[1]); avail = int(mem[2])
            info['ram'] = {
                'total_mb': round(total/1024/1024, 1),
                'used_mb': round(used/1024/1024, 1),
                'available_mb': round(avail/1024/1024, 1),
                'used_pct': round((used/total)*100, 1) if total else None,
                'alert_85': bool(total and (used/total)*100 >= 85),
            }
        if len(parts) > 1:
            st = parts[1].strip().split()
            if len(st) >= 5:
                total = int(st[0]); used = int(st[1]); avail = int(st[2])
                pct = round((used/total)*100, 1) if total else None
                info['storage'] = {
                    'path': '/mnt/storage',
                    'mount': st[4],
                    'total_gb': round(total/1024/1024/1024, 2),
                    'used_gb': round(used/1024/1024/1024, 2),
                    'free_gb': round(avail/1024/1024/1024, 2),
                    'used_pct': pct,
                }
    except Exception as e:
        info['parse_error'] = str(e)
    return info

def send_wol(mac=None, broadcast='192.168.1.255'):
    mac = (mac or get_node2_mac() or '').strip()
    if not mac:
        return False, 'MAC not found'
    try:
        mac_bytes = bytes.fromhex(mac.replace(':', '').replace('-', ''))
        if len(mac_bytes) != 6:
            return False, 'invalid MAC'
        packet = b'\xff' * 6 + mac_bytes * 16
        targets = [broadcast, '255.255.255.255']
        sent = 0
        for target in targets:
            for port in (9, 7):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(packet, (target, port))
                    s.close()
                    sent += 1
                except Exception:
                    pass
        return sent > 0, f'WoL enviado a {mac} ({sent} paquetes)'
    except Exception as e:
        return False, str(e)


def ping_host(ip, timeout=1.0):
    try:
        r = subprocess.run(['ping','-c','1','-W',str(int(timeout)), ip], capture_output=True, text=True, timeout=timeout+1)
        ok = r.returncode == 0
        ms = None
        m = re.search(r'time[=<]([0-9.]+)\s*ms', r.stdout)
        if m: ms = float(m.group(1))
        return {'ip': ip, 'up': ok, 'latency_ms': ms}
    except Exception as e:
        return {'ip': ip, 'up': False, 'error': str(e)}

def _calc_cpu_pct(stats):
    try:
        cpu = stats.get('cpu_stats',{}); prec = stats.get('precpu_stats',{})
        cpu_total = ((cpu.get('cpu_usage') or {}).get('total_usage') or 0) - ((prec.get('cpu_usage') or {}).get('total_usage') or 0)
        sys_total = (cpu.get('system_cpu_usage') or 0) - (prec.get('system_cpu_usage') or 0)
        ncpu = cpu.get('online_cpus') or len(((cpu.get('cpu_usage') or {}).get('percpu_usage')) or []) or 1
        if sys_total > 0 and cpu_total > 0:
            return round((cpu_total / sys_total) * ncpu * 100.0, 2)
    except Exception:
        pass
    return None

def _container_stats(cli, cid):
    try:
        s = cli.api.stats(cid, stream=False)
        mem = s.get('memory_stats',{}) or {}
        usage = mem.get('usage') or 0
        # subtract cache when available
        cache = 0
        st = mem.get('stats') or {}
        for k in ('cache','inactive_file'):
            if isinstance(st.get(k), (int,float)):
                cache = st.get(k); break
        mem_used = max(0, usage - cache) if cache else usage
        limit = mem.get('limit') or 0
        mem_pct = round((mem_used/limit)*100,2) if limit else None
        return {
            'cpu_pct': _calc_cpu_pct(s),
            'mem_used_mb': round(mem_used/1024/1024,1) if mem_used else 0,
            'mem_limit_mb': round(limit/1024/1024,1) if limit else None,
            'mem_pct': mem_pct,
        }
    except Exception:
        return {}

def collect_containers():
    cli = docker_client()
    if not cli:
        return {'error': 'docker unavailable', 'containers': []}
    out = []
    try:
        containers = cli.containers.list(all=True)
        # prefetch stats for running containers (best effort)
        stats_map = {}
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            running = [c for c in containers if c.status == 'running']
            if running:
                with ThreadPoolExecutor(max_workers=min(8, len(running))) as ex:
                    futs = {ex.submit(_container_stats, cli, c.id): c.id for c in running}
                    for f in as_completed(futs, timeout=8):
                        try:
                            stats_map[futs[f]] = f.result()
                        except Exception:
                            pass
        except Exception:
            pass
        for c in containers:
            attrs = c.attrs or {}
            state = attrs.get('State', {})
            health = (state.get('Health') or {}).get('Status')
            net_settings = (attrs.get('NetworkSettings') or {})
            networks = list((net_settings.get('Networks') or {}).keys())
            mounts = []
            for m in attrs.get('Mounts') or []:
                mounts.append({'src': m.get('Source'), 'dst': m.get('Destination'), 'mode': m.get('Mode'), 'rw': m.get('RW')})
            info = {
                'id': c.short_id,
                'name': c.name,
                'image': (c.image.tags[0] if c.image.tags else str(c.image.id)) if getattr(c,'image',None) else attrs.get('Config',{}).get('Image'),
                'status': c.status,
                'running': state.get('Running', False),
                'health': health,
                'restarting': state.get('Restarting', False),
                'started_at': state.get('StartedAt'),
                'restart_count': attrs.get('RestartCount'),
                'networks': networks,
                'mounts': mounts[:12],
            }
            st = stats_map.get(c.id)
            if st:
                info['stats'] = st
            out.append(info)
    except Exception as e:
        return {'error': str(e), 'containers': out}
    return {'containers': out, 'count': len(out), 'running': sum(1 for x in out if x.get('running'))}

def collect_docker_resources():
    cli = docker_client()
    if not cli:
        return {'networks': [], 'volumes': []}
    nets = vols = []
    try:
        nets = [{'name': n.name, 'driver': n.attrs.get('Driver'), 'id': n.short_id} for n in cli.networks.list()]
    except Exception: nets = []
    try:
        vols = [{'name': v.name, 'driver': v.attrs.get('Driver')} for v in cli.volumes.list()]
    except Exception: vols = []
    return {'networks': nets, 'volumes': vols}

def collect_twin():
    host = {
        'name': 'OptiPlex 5040 (Nodo 1)',
        'ip': '192.168.1.3',
        'cpu_temp_c': cpu_temp_c(),
        'ram': ram_info(),
        'ssd': disk_info(),
        'tailscale': tailscale_info(),
        'quicksync': quicksync_info(),
        'security': security_posture(),
    }
    node2_ping = ping_host(settings.node2_host)
    node2_mac = get_node2_mac()
    node2 = {
        'name': 'homelab-node2 (Nodo 2)',
        'ip': settings.node2_host,
        'mac': node2_mac,
        'specs': 'AMD FX-8300 · 4 GB RAM · GPU RX 550 · SSD 240 GB + HDD 1 TB /mnt/storage · BIOS auto-encendido',
        'role': 'critical_24x7',
        **node2_ping,
    }
    try:
        node2['metrics'] = node2_metrics(online=bool(node2_ping.get('up')))
    except Exception as e:
        node2['metrics_error'] = str(e)
    nodes = {
        'gateway_zte': {'name': 'Gateway ZTE F6201B', 'ip': '192.168.1.1', **ping_host('192.168.1.1')},
        'ap_nexxt': {'name': 'AP Nexxt Amp300', 'ip': '192.168.1.254', **ping_host('192.168.1.254')},
        'host': {'name': host['name'], **host, **ping_host('192.168.1.3')},
        'node2': node2,
        'tcl_tv': {'name': 'TCL Android TV 12', 'ip': '192.168.1.8', **ping_host('192.168.1.8')},
    }
    docker_info = collect_containers()
    resources = collect_docker_resources()
    twin = {
        'ts': time.time(),
        'nodes': nodes,
        'docker': docker_info,
        'resources': resources,
    }
    # persist compact health sample
    try:
        add_sample('twin', 'ok', {
            'ssd_used_pct': (host.get('ssd') or {}).get('used_pct'),
            'ram_used_pct': (host.get('ram') or {}).get('used_pct'),
            'cpu_temp_c': host.get('cpu_temp_c'),
            'containers_running': docker_info.get('running'),
            'containers_total': docker_info.get('count'),
            'node2_up': bool(node2_ping.get('up')),
        })
    except Exception:
        pass
    kv_set('last_twin', twin)
    return twin

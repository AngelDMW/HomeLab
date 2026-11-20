import hashlib, hmac, json, os, subprocess, time, uuid, shutil
import httpx
from .config import settings
from .db import add_repair, count_actions, save_pending_action, get_pending_action, set_pending_status, kv_set, find_active_tier2
from .twin_engine import docker_client, send_wol, ssh_node2, get_node2_mac

def _sig(action_id, decision):
    msg = f"{action_id}:{decision}".encode()
    return hmac.new(settings.action_secret.encode(), msg, hashlib.sha256).hexdigest()

def verify_sig(action_id, decision, sig):
    try:
        return hmac.compare_digest(_sig(action_id, decision), sig or '')
    except Exception:
        return False

def can_auto(component):
    return count_actions(component, 1800) < 2

def restart_container(name):
    cli = docker_client()
    if not cli:
        return False, 'docker unavailable'
    try:
        c = cli.containers.get(name)
        c.restart(timeout=20)
        return True, f'restarted {name}'
    except Exception as e:
        return False, str(e)

def run_threadfin_publish():
    script = os.path.join(settings.threadfin_base, 'threadfin_sync.sh')
    # try direct execution (works if path mounted and deps present)
    cmds = [
        ['bash', script, '--publish'],
    ]
    # fallback via nsenter to host namespace if available (privileged/pid host)
    if os.path.exists('/usr/bin/nsenter') or shutil.which('nsenter'):
        cmds.insert(0, ['nsenter','-t','1','-m','-u','-n','-i','--','bash', script, '--publish'])
    last_err = None
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                out = (r.stdout + '\n' + r.stderr).strip()
                return True, out[-2000:] if out else 'publish executed'
            last_err = (r.stdout + '\n' + r.stderr).strip()[-1000:]
        except Exception as e:
            last_err = str(e)
    return False, last_err or 'failed'

def clean_safe_caches():
    freed = []
    errors = []
    # prefer host namespace cleanup (safe temporary caches)
    if shutil.which('nsenter'):
        try:
            r = subprocess.run(['nsenter','-t','1','-m','-u','-n','-i','--','bash','-lc','apt-get clean >/dev/null 2>&1 || true; rm -rf /var/tmp/* /tmp/sentinel-tmp-* 2>/dev/null || true; echo APT_OK'],
                               capture_output=True, text=True, timeout=120)
            if 'APT_OK' in (r.stdout + r.stderr):
                freed.append('apt-get clean ejecutado en host (cachés de paquetes)')
        except Exception as e:
            errors.append(f'nsenter-apt:{e}')
    hostfs = settings.hostfs or '/hostfs'
    # apt archives fallback if hostfs writable path
    apt_dir = os.path.join(hostfs, 'var/cache/apt/archives')
    try:
        if os.path.isdir(apt_dir) and os.access(apt_dir, os.W_OK):
            n=0; sz=0
            for fn in os.listdir(apt_dir):
                fp = os.path.join(apt_dir, fn)
                if os.path.isfile(fp) and fn.endswith('.deb'):
                    try:
                        sz += os.path.getsize(fp)
                        os.remove(fp); n += 1
                    except Exception as e:
                        errors.append(f'apt:{fn}:{e}')
            if n:
                freed.append(f'apt archives: {n} debs, ~{round(sz/1024/1024,1)} MB')
    except Exception as e:
        errors.append(f'apt:{e}')
    # docker dangling build cache/images (safe temporary/cache artifacts)
    try:
        cli = docker_client()
        if cli:
            try:
                pr = cli.images.prune(filters={'dangling': True})
                if pr and pr.get('SpaceReclaimed'):
                    freed.append(f"docker dangling images: {round(pr.get('SpaceReclaimed',0)/1024/1024,1)} MB")
            except Exception as e:
                errors.append(f'docker image prune:{e}')
            try:
                pr = cli.api.prune_builds()
                if pr and pr.get('SpaceReclaimed'):
                    freed.append(f"docker build cache: {round(pr.get('SpaceReclaimed',0)/1024/1024,1)} MB")
            except Exception as e:
                errors.append(f'docker builder prune:{e}')
    except Exception as e:
        errors.append(f'docker:{e}')
    ok = len(freed) > 0 or len(errors)==0
    detail = '; '.join(freed) if freed else 'no safe caches reclaimed'
    if errors:
        detail += ' | errors: ' + '; '.join(errors[:5])
    return ok, detail

def wake_node2_action(params=None):
    mac = get_node2_mac()
    ok, detail = send_wol(mac)
    return ok, detail

def restart_node2_ml(params=None):
    cmd = "cd /home/mrdavid/HomeLab/docker/immich-ml && docker compose restart immich-machine-learning 2>&1 || docker restart immich-machine-learning 2>&1 || (docker compose up -d immich-machine-learning 2>&1)"
    ok, out, err = ssh_node2(cmd, timeout=60)
    detail = (out or err or '').strip()
    if ok:
        return True, detail[-2000:] if detail else 'immich-machine-learning reiniciado en homelab-node2'
    return False, detail[-2000:] if detail else 'falló reinicio remoto de immich-machine-learning'

ACTION_HANDLERS = {
    'restart_container': lambda p: restart_container(p.get('container') or p.get('component')),
    'threadfin_publish': lambda p: run_threadfin_publish(),
    'clean_caches': lambda p: clean_safe_caches(),
    'wake_node2': lambda p: wake_node2_action(p),
    'restart_node2_ml': lambda p: restart_node2_ml(p),
}

def execute_action(action, params):
    fn = ACTION_HANDLERS.get(action)
    if not fn:
        return False, f'unknown action {action}'
    return fn(params or {})

def try_tier1(component, action, params, diagnosis=None):
    if not can_auto(component):
        add_repair(component, action, 1, 'rate_limited', {'params': params}, diagnosis)
        return {'status': 'rate_limited'}
    ok, detail = execute_action(action, params)
    status = 'ok' if ok else 'failed'
    rid = add_repair(component, action, 1, status, {'detail': detail, 'params': params}, diagnosis)
    return {'status': status, 'detail': detail, 'repair_id': rid}

def send_ntfy(title, message, actions=None, tags=None, priority=4):
    if not settings.ntfy_topic:
        return False, 'NTFY_TOPIC not configured'
    url = f"{settings.ntfy_server}/{settings.ntfy_topic}"
    headers = {'Title': title.encode('utf-8') if isinstance(title,str) else title,
               'Priority': str(priority)}
    if tags:
        headers['Tags'] = ','.join(tags)
    # ntfy Actions header format
    if actions:
        # list of dicts {action,label,url,clear}
        parts = []
        for a in actions:
            # http, Label, URL, method=POST, clear=true
            parts.append(f"http, {a['label']}, {a['url']}, method=GET, clear=true")
        headers['Actions'] = '; '.join(parts)
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(url, content=message.encode('utf-8'), headers={k:(v if isinstance(v,str) else v) for k,v in headers.items()})
        return r.status_code < 300, f'status {r.status_code}'
    except Exception as e:
        return False, str(e)

def request_tier2(component, action, params, diagnosis):
    # dedupe: cooldown 30 min por (servicio, tipo_anomalia) mientras pendiente o ignorada
    active = find_active_tier2(component, action, cooldown_sec=1800)
    if active:
        state = active.get('state')
        row = active.get('row') or {}
        if state == 'pending':
            return {'status': 'pending_existing', 'action_id': row.get('id'), 'notified': False, 'notify_detail': 'deduped: pending action exists'}
        if state == 'ignored_cooldown':
            return {'status': 'suppressed_ignored', 'action_id': row.get('id'), 'notified': False, 'notify_detail': 'deduped: ignored within cooldown'}
        if state == 'recent_tier2':
            return {'status': 'suppressed_recent', 'notified': False, 'notify_detail': 'deduped: recent tier2 notification in cooldown'}
    action_id = uuid.uuid4().hex[:16]
    save_pending_action(action_id, component, action, params, diagnosis)
    base = settings.public_url.rstrip('/')
    approve_url = f"{base}/api/actions/approve?id={action_id}&sig={_sig(action_id,'approve')}"
    ignore_url = f"{base}/api/actions/ignore?id={action_id}&sig={_sig(action_id,'ignore')}"
    title = f"Sentinel: aprobación requerida ({component})"
    msg = f"Componente: {component}\nAcción propuesta: {action}\n\nDiagnóstico IA:\n{diagnosis}\n\nParámetros: {json.dumps(params, ensure_ascii=False)}"
    ok, detail = send_ntfy(title, msg, actions=[{'label':'Aprobar','url':approve_url},{'label':'Ignorar','url':ignore_url}], tags=['warning','robot'], priority=4)
    add_repair(component, action, 2, 'pending_approval' if ok else 'notify_failed', {'notify': detail, 'action_id': action_id, 'params': params}, diagnosis)
    kv_set(f'pending:{action_id}', {'component':component,'action':action})
    return {'status': 'pending', 'action_id': action_id, 'notified': ok, 'notify_detail': detail}

def approve_pending(action_id):
    pa = get_pending_action(action_id)
    if not pa:
        return False, 'not found'
    if pa.get('status') != 'pending':
        return False, f'already {pa.get("status")}'
    params = {}
    try: params = json.loads(pa.get('params') or '{}')
    except Exception: pass
    ok, detail = execute_action(pa['action'], params)
    status = 'approved_ok' if ok else 'approved_failed'
    set_pending_status(action_id, status)
    add_repair(pa['component'], pa['action'], 2, status, {'detail': detail, 'action_id': action_id, 'params': params}, pa.get('diagnosis'))
    return ok, detail

def ignore_pending(action_id):
    pa = get_pending_action(action_id)
    if not pa:
        return False, 'not found'
    set_pending_status(action_id, 'ignored')
    add_repair(pa['component'], pa['action'], 2, 'ignored', {'action_id': action_id}, pa.get('diagnosis'))
    return True, 'ignored'

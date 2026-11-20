import json, os, time
import httpx
from .config import settings
from .db import kv_get
from .twin_engine import docker_client

# --- log helpers ---

def get_container_logs(name, tail=60):
    cli = docker_client()
    if not cli:
        return ''
    try:
        c = cli.containers.get(name)
        logs = c.logs(tail=tail).decode('utf-8', 'ignore')
        return logs
    except Exception as e:
        return f'[error reading logs: {e}]'

# --- deterministic analysis ---

def analyze(probes, twin):
    anomalies = []  # list of {component, severity, summary, details, tier, action, params}
    # disk
    disk = probes.get('disk') or {}
    ssd = ((twin.get('nodes') or {}).get('host') or {}).get('ssd') or {}
    used_pct = disk.get('used_pct') or ssd.get('used_pct')
    if used_pct is not None and used_pct >= 95:
        anomalies.append({'component':'host-disk','severity':'critical','summary':f'/dev/sda1 al {used_pct}% (>=95%)','tier':1,'action':'clean_caches','params':{},'details':{'used_pct':used_pct}})
    elif used_pct is not None and used_pct >= 92:
        anomalies.append({'component':'host-disk','severity':'warning','summary':f'/dev/sda1 al {used_pct}% (alerta temprana >=92%)','tier':0,'action':None,'params':{},'details':{'used_pct':used_pct}})
    # probes
    tf = probes.get('threadfin') or {}
    if tf.get('empty'):
        anomalies.append({'component':'threadfin','severity':'critical','summary':'Threadfin M3U/EPG vacío o inaccesible','tier':1,'action':'threadfin_publish','params':{},'details':{'channels':tf.get('channels'),'m3u_ok':tf.get('m3u_ok'),'xml_ok':tf.get('xml_ok')}})
    elif tf.get('channels_dropped') or tf.get('sync_failed'):
        msg = 'caída de canales activos' if tf.get('channels_dropped') else 'falló la sincronización diaria de Threadfin'
        anomalies.append({'component':'threadfin','severity':'warning','summary':f'Threadfin: {msg}','tier':2,'action':'threadfin_publish','params':{},'details':{'channels':tf.get('channels'),'prev':tf.get('prev_channels'),'sync_failed':tf.get('sync_failed')}})
    for key in ['pihole','nextcloud','immich','jellyfin']:
        p = probes.get(key) or {}
        st = p.get('status')
        if st in ('down','error'):
            anomalies.append({'component':p.get('name',key),'severity':'critical','summary':f"{p.get('name',key)} no responde ({p.get('endpoint') or p.get('port')})",'tier':2,'action':'restart_container','params':{'container': key if key!='pihole' else 'pihole'},'details':{'error':p.get('error'),'status':st}})
    # immich mount / disk margin note
    imm = probes.get('immich') or {}
    if imm.get('mount_nextcloud_photos_ro') is False:
        anomalies.append({'component':'immich','severity':'warning','summary':'Montaje /nextcloud-photos:ro no verificado en immich-server','tier':2,'action':None,'params':{},'details':imm.get('mount_info')})
    # containers & logs
    cl = probes.get('containers_logs') or {}
    for iss in (cl.get('issues') or []):
        comp = iss.get('component','unknown')
        itype = iss.get('type')
        text = iss.get('issue','')
        if itype == 'container' and ('unhealthy' in text or 'restarting' in text):
            anomalies.append({'component':comp,'severity':'critical','summary':f'Contenedor {comp} en mal estado: {text}','tier':1,'action':'restart_container','params':{'container':comp},'details':iss})
        elif itype == 'logs':
            anomalies.append({'component':comp,'severity':'warning','summary':f'Patrones críticos en logs de {comp}: {text}','tier':2,'action':'restart_container','params':{'container':comp},'details':iss})
    # Node 2 (activo 24/7)
    n2 = probes.get('node2_metrics') or {}
    n2_online = n2.get('online')
    if n2_online is False:
        anomalies.append({'component':'homelab-node2','severity':'critical','summary':'homelab-node2 (192.168.1.4) no responde (nodo crítico 24/7 caído)','tier':2,'action':'wake_node2','params':{},'details':{'probe': n2}})
    else:
        # Portainer Agent
        n2p = probes.get('node2_portainer') or {}
        if n2p.get('status') in ('down','error'):
            anomalies.append({'component':'node2-portainer','severity':'critical','summary':'Portainer Agent en homelab-node2 (:9001) no disponible','tier':2,'action':None,'params':{},'details':n2p})
        # Immich ML
        n2ml = probes.get('node2_immich_ml') or {}
        if n2ml.get('status') in ('down','error') and n2_online:
            anomalies.append({'component':'node2-immich-ml','severity':'critical','summary':'Immich Machine Learning en homelab-node2 (:3003 /ping) no responde','tier':1,'action':'restart_node2_ml','params':{},'details':n2ml})
        # RAM alerta temprana >85%
        ram = n2.get('ram') or {}
        if ram.get('alert_85'):
            anomalies.append({'component':'homelab-node2','severity':'warning','summary':f"RAM Nodo 2 al {ram.get('used_pct')}% (alerta temprana >85% de 4 GB)",'tier':0,'action':None,'params':{},'details':{'ram': ram}})
        # storage info note (no hard threshold specified, but report if ssh metrics unavailable?)
        if n2_online and n2.get('ssh_ok') is False and n2.get('ssh_error'):
            anomalies.append({'component':'homelab-node2','severity':'warning','summary':'No se pudieron obtener métricas SSH de homelab-node2','tier':0,'action':None,'params':{},'details':{'ssh_error': n2.get('ssh_error')}})
    return anomalies

# --- offline rule engine / LLM escalation ---

def _compose_context(anomaly, probes, twin):
    comp = anomaly.get('component')
    # map component to container name for logs
    cmap = {'Threadfin':'threadfin','Pi-hole':'pihole','Nextcloud':'nextcloud','Immich':'immich-server','Jellyfin':'jellyfin','host-disk':None,'homelab-node2':None,'node2-portainer':None,'node2-immich-ml':None}
    cname = cmap.get(comp, comp)
    logs = get_container_logs(cname, 60) if cname else ''
    # compact twin
    twin_compact = {
        'host': ((twin.get('nodes') or {}).get('host') or {}),
        'containers': [{'name':c.get('name'),'status':c.get('status'),'health':c.get('health')} for c in ((twin.get('docker') or {}).get('containers') or [])[:40]],
        'probes_summary': {k: (v.get('status') if isinstance(v,dict) else None) for k,v in probes.items() if k!='containers_logs'},
    }
    return twin_compact, logs

def offline_diagnosis(anomaly, probes, twin):
    comp = anomaly.get('component')
    sev = anomaly.get('severity')
    summary = anomaly.get('summary')
    action = anomaly.get('action')
    if action == 'clean_caches':
        return f"Diagnóstico (motor offline): {summary}. Se recomienda limpieza de cachés temporales seguras (apt cache, build cache huérfana de Docker) para recuperar espacio en /dev/sda1 sin tocar datos de servicios."
    if comp == 'threadfin' or 'Threadfin' in comp:
        if anomaly.get('details',{}).get('channels') == 0 or 'vacío' in summary:
            return "Diagnóstico (motor offline): La lista M3U/EPG de Threadfin no está disponible o quedó vacía. Acción sugerida: re-publicar la curaduría canónica con threadfin_sync.sh --publish para restaurar canales sin cambiar numeración ni grupos."
        return f"Diagnóstico (motor offline): {summary}. Puede requerir re-sincronización/publicación de la lista curada o revisión de fuentes; se sugiere validar last_run.json y sync.log."
    if action == 'restart_container':
        return f"Diagnóstico (motor offline): {summary}. Un reinicio controlado del contenedor suele recuperar el servicio si está colgado/unhealthy. Revisar logs recientes para causa raíz (OOM, I/O, conexión rechazada)."
    return f"Diagnóstico (motor offline): {summary}. Se recomienda revisión manual y verificación de dependencias del servicio."

async def llm_diagnosis(anomaly, probes, twin):
    provider = settings.llm_provider
    key = settings.api_key
    if not provider or not key:
        return None
    twin_ctx, logs = _compose_context(anomaly, probes, twin)
    prompt = (
        "Eres HomeLab Sentinel AI. Analiza este incidente del homelab y responde en español, breve y accionable.\n"
        "Incluye: causa probable, impacto y acción recomendada (tier 1 automática o tier 2 con aprobación).\n\n"
        f"Incidente: {json.dumps(anomaly, ensure_ascii=False, default=str)}\n\n"
        f"Estado Gemelo Digital (resumen): {json.dumps(twin_ctx, ensure_ascii=False, default=str)[:6000]}\n\n"
        f"Últimas 60 líneas de logs del servicio afectado:\n{logs[-6000:]}"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if provider == 'gemini':
                models = [settings.llm_model] if settings.llm_model else ['gemini-flash-latest', 'gemini-flash-lite-latest']
                body = {"contents":[{"parts":[{"text": prompt}]}]}
                headers = {'X-goog-api-key': key}
                for model in models:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                    try:
                        r = await c.post(url, json=body, headers=headers)
                        if r.status_code >= 400:
                            continue
                        data = r.json()
                        return data['candidates'][0]['content']['parts'][0]['text']
                    except Exception:
                        continue
                return None
            elif provider == 'openai':
                model = settings.llm_model or 'gpt-4o-mini'
                r = await c.post('https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}'}, json={
                        'model': model,
                        'messages':[{'role':'user','content':prompt}],
                        'temperature':0.2
                    })
                if r.status_code >= 400: return None
                return r.json()['choices'][0]['message']['content']
            elif provider == 'openrouter':
                model = settings.llm_model or 'google/gemini-flash-1.5'
                r = await c.post('https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}'}, json={
                        'model': model,
                        'messages':[{'role':'user','content':prompt}],
                        'temperature':0.2
                    })
                if r.status_code >= 400: return None
                return r.json()['choices'][0]['message']['content']
    except Exception:
        return None
    return None

async def diagnose(anomaly, probes, twin):
    # try LLM, fallback offline
    txt = await llm_diagnosis(anomaly, probes, twin)
    if txt:
        return txt.strip(), 'llm'
    return offline_diagnosis(anomaly, probes, twin), 'offline'

async def answer_question(question, twin, probes):
    # console Q&A: try LLM with context, else offline summary
    q = (question or '').strip()
    if not q:
        return 'Escribe una pregunta sobre el estado o logs del servidor.'
    provider = settings.llm_provider
    key = settings.api_key
    ctx = {'twin': twin, 'probes': probes}
    if provider and key:
        prompt = (
            "Eres HomeLab Sentinel AI, asistente del homelab. Responde en español usando el contexto proporcionado. "
            "Si preguntan por logs de un servicio específico, resume el estado conocido y patrones relevantes.\n\n"
            f"Pregunta: {q}\n\nContexto: {json.dumps(ctx, ensure_ascii=False, default=str)[:12000]}"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                if provider == 'gemini':
                    models = [settings.llm_model] if settings.llm_model else ['gemini-flash-latest', 'gemini-flash-lite-latest']
                    body = {"contents":[{"parts":[{"text": prompt}]}]}
                    headers = {'X-goog-api-key': key}
                    for model in models:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                        try:
                            r = await c.post(url, json=body, headers=headers)
                            if r.status_code < 400:
                                data = r.json()
                                return data['candidates'][0]['content']['parts'][0]['text']
                        except Exception:
                            continue
                elif provider == 'openai':
                    model = settings.llm_model or 'gpt-4o-mini'
                    r = await c.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {key}'}, json={'model': model,'messages':[{'role':'user','content':prompt}]})
                    if r.status_code < 400: return r.json()['choices'][0]['message']['content']
                elif provider == 'openrouter':
                    model = settings.llm_model or 'google/gemini-flash-1.5'
                    r = await c.post('https://openrouter.ai/api/v1/chat/completions', headers={'Authorization': f'Bearer {key}'}, json={'model': model,'messages':[{'role':'user','content':prompt}]})
                    if r.status_code < 400: return r.json()['choices'][0]['message']['content']
        except Exception:
            pass
    # offline answer
    lines = []
    host = ((twin or {}).get('nodes') or {}).get('host') or {}
    ssd = host.get('ssd') or {}
    lines.append(f"Disco /dev/sda1: {ssd.get('used_pct')}% usado ({ssd.get('free_gb')} GB libres).")
    ram = host.get('ram') or {}
    lines.append(f"RAM: {ram.get('used_pct')}% usada.")
    docker = (twin or {}).get('docker') or {}
    lines.append(f"Contenedores: {docker.get('running')}/{docker.get('count')} en ejecución.")
    # probe summary
    for k,v in (probes or {}).items():
        if isinstance(v, dict) and 'status' in v:
            lines.append(f"- {v.get('name',k)}: {v.get('status')}")
    # if asking about logs, try to fetch
    ql = q.lower()
    for name in ['threadfin','pihole','nextcloud','immich-server','jellyfin','seerr-theme','radarr','sonarr']:
        if name in ql or name.replace('-',' ') in ql:
            logs = get_container_logs(name, 40)
            lines.append(f"\nÚltimas líneas de {name}:\n{logs[-3000:]}")
            break
    return "\n".join(lines)

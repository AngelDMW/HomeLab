import asyncio, json, time, traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import settings
from .db import init_db, kv_get, kv_set, add_incident, add_repair, recent_repairs, recent_incidents, get_pending_action
from .twin_engine import collect_twin
from .probes import run_all_probes
from .ai_brain import analyze, diagnose, answer_question
from .healer import try_tier1, request_tier2, verify_sig, approve_pending, ignore_pending
from .corp_api import router as corp_router
from .twin_engine import send_wol, get_node2_mac

STATE = {'twin': None, 'probes': None, 'anomalies': [], 'last_cycle': None, 'last_audit': None}
_scheduler = None
_lock = asyncio.Lock()

def _collect_and_analyze():
    try:
        twin = collect_twin()
    except Exception as e:
        twin = {'error': str(e), 'ts': time.time()}
    try:
        probes = run_all_probes()
    except Exception as e:
        probes = {'error': str(e)}
    try:
        anomalies = analyze(probes, twin)
    except Exception as e:
        anomalies = []
    return twin, probes, anomalies

async def process_cycle(manual=False):
    async with _lock:
        # run heavy sync collection/probing in a worker thread so /api/state stays responsive
        twin, probes, anomalies = await asyncio.to_thread(_collect_and_analyze)
        # handle anomalies
        handled = []
        for a in anomalies:
            comp = a.get('component','unknown')
            sev = a.get('severity')
            tier = a.get('tier', 0)
            action = a.get('action')
            params = a.get('params') or {}
            summary = a.get('summary')
            try:
                add_incident(comp, sev, summary, a.get('details'))
            except Exception:
                pass
            # deterministic detector is zero-token; escalate to AI diagnosis when there is an actionable/important anomaly
            diagnosis_text = None; diag_src = 'deterministic'
            if sev in ('critical','warning') or manual:
                try:
                    diagnosis_text, diag_src = await diagnose(a, probes, twin)
                except Exception as e:
                    diagnosis_text = f'Diagnóstico no disponible: {e}'
                    diag_src = 'error'
            a['diagnosis'] = diagnosis_text
            a['diagnosis_source'] = diag_src
            # Tier 1 auto
            if tier == 1 and action:
                res = try_tier1(comp, action, params, diagnosis_text)
                handled.append({**a, 'result': res})
            elif tier == 2 and action:
                # approval required
                res = request_tier2(comp, action, params, diagnosis_text or summary)
                handled.append({**a, 'result': res})
            else:
                # info/warning only (e.g. disk 92%)
                if sev == 'warning' and diagnosis_text:
                    add_repair(comp, 'observation', 0, 'logged', {'summary': summary}, diagnosis_text)
                handled.append({**a, 'result': {'status': 'observed'}})
        STATE['twin'] = twin
        STATE['probes'] = probes
        STATE['anomalies'] = anomalies
        STATE['handled'] = handled
        STATE['last_cycle'] = time.time()
        kv_set('last_state_summary', {'ts': STATE['last_cycle'], 'anomaly_count': len(anomalies)})
        return {'ts': STATE['last_cycle'], 'twin': twin, 'probes': probes, 'anomalies': anomalies, 'handled': handled}

async def scheduled_cycle():
    try:
        await process_cycle(manual=False)
    except Exception:
        traceback.print_exc()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    global _scheduler
    # run initial cycle in background shortly after start
    async def _initial():
        await asyncio.sleep(2)
        await process_cycle(manual=False)
    asyncio.create_task(_initial())
    _scheduler = AsyncIOScheduler(timezone=settings.tz)
    _scheduler.add_job(scheduled_cycle, 'interval', seconds=30, id='cycle30', max_instances=1, coalesce=True)
    _scheduler.start()
    yield
    try:
        if _scheduler:
            _scheduler.shutdown(wait=False)
    except Exception:
        pass

app = FastAPI(title='HomeLab Sentinel AI', lifespan=lifespan)
app.include_router(corp_router)
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')

@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse('index.html', {'request': request})

@app.get('/api/state')
async def api_state():
    return JSONResponse({
        'twin': STATE.get('twin') or kv_get('last_twin'),
        'probes': STATE.get('probes') or kv_get('last_probes'),
        'anomalies': STATE.get('anomalies', []),
        'handled': STATE.get('handled', []),
        'last_cycle': STATE.get('last_cycle'),
        'repairs': recent_repairs(50),
        'incidents': recent_incidents(50),
        'config': {
            'llm_provider': settings.llm_provider or None,
            'llm_configured': bool(settings.api_key),
            'ntfy_configured': bool(settings.ntfy_topic),
            'public_url': settings.public_url,
        }
    })

@app.post('/api/audit')
async def api_audit():
    result = await process_cycle(manual=True)
    STATE['last_audit'] = result
    return JSONResponse({'ok': True, 'audit': result})

@app.get('/api/audit/last')
async def api_audit_last():
    return JSONResponse({'audit': STATE.get('last_audit')})

@app.post('/api/ask')
async def api_ask(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    q = body.get('question') or body.get('q') or ''
    twin = STATE.get('twin') or kv_get('last_twin') or {}
    probes = STATE.get('probes') or kv_get('last_probes') or {}
    ans = await answer_question(q, twin, probes)
    return JSONResponse({'ok': True, 'question': q, 'answer': ans})

@app.get('/api/actions/approve')
async def api_approve(id: str = Query(...), sig: str = Query(...)):
    if not verify_sig(id, 'approve', sig):
        raise HTTPException(status_code=403, detail='invalid signature')
    ok, detail = approve_pending(id)
    return HTMLResponse(f"<html><body style='font-family:sans-serif;background:#0b0b0d;color:#eee;padding:2rem'><h2>{'✅ Acción aprobada y ejecutada' if ok else '⚠️ Acción aprobada con error'}</h2><p>{detail}</p><p>ID: {id}</p></body></html>")

@app.get('/api/actions/ignore')
async def api_ignore(id: str = Query(...), sig: str = Query(...)):
    if not verify_sig(id, 'ignore', sig):
        raise HTTPException(status_code=403, detail='invalid signature')
    ok, detail = ignore_pending(id)
    return HTMLResponse(f"<html><body style='font-family:sans-serif;background:#0b0b0d;color:#eee;padding:2rem'><h2>🚫 Acción ignorada</h2><p>{detail}</p><p>ID: {id}</p></body></html>")



@app.post('/api/node2/wol')
async def api_node2_wol():
    mac = get_node2_mac()
    ok, detail = send_wol(mac)
    if ok:
        add_repair('homelab-node2', 'wake_node2', 2, 'manual_wol', {'detail': detail, 'mac': mac}, 'Wake-on-LAN manual solicitado desde Mission Control')
    return JSONResponse({'ok': ok, 'detail': detail, 'mac': mac})


@app.get('/health')
async def health():
    return {'ok': True, 'ts': time.time(), 'last_cycle': STATE.get('last_cycle')}

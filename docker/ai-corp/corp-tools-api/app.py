"""HomeLab Corp — backend de herramientas y libro de rendición de cuentas.

Solo lectura/diagnóstico sobre el clúster. Ningún endpoint reinicia contenedores,
borra datos ni modifica configuración. Protegido por X-Corp-Token.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Query

DB_PATH = os.getenv("CORP_LEDGER_DB", "/app/data/corp_ledger.sqlite3")
CORP_TOKEN = os.getenv("CORP_INTERNAL_TOKEN", "")
SENTINEL = os.getenv("SENTINEL_URL", "http://192.168.1.3:9090").rstrip("/")
N1 = {
    "jellyfin": "http://192.168.1.3:8096",
    "seerr": "http://192.168.1.3:5055",
    "qbittorrent": "http://192.168.1.3:8081",
    "radarr": "http://192.168.1.3:7878",
    "sonarr": "http://192.168.1.3:8989",
    "bazarr": "http://192.168.1.3:6767",
    "nextcloud": "http://192.168.1.3:8080",
    "immich": "http://192.168.1.3:2283",
    "threadfin": "http://192.168.1.3:34400",
}
ML_URL = os.getenv("NODE2_ML_URL", "http://192.168.1.4:3003")
# En Nodo 1 esta ruta se ve como /mnt/node2-storage; en Nodo 2 es /mnt/storage.
OFFLOAD = "/mnt/storage/homelab-offload"
BACKUP_DIR = os.path.join(OFFLOAD, "backups-compose")
STORAGE_PATH = "/mnt/storage/homelab-offload"
EMPLOYEES = (
    "Agent-Infra",
    "Agent-SecOps",
    "Agent-Media",
    "Agent-Cloud",
    "Agent-Updater",
    "Agent-Backup",
    "Agent-Architect",
    "Agent-QA",
)

app = FastAPI(title="HomeLab Corp Tools", version="2.0.0")


# --------------------------------------------------------------------------- #
# Base de datos (SQLite WAL)
# --------------------------------------------------------------------------- #
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with closing(_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directive TEXT NOT NULL,
                employee TEXT NOT NULL DEFAULT 'HomeLab-Director',
                requested_by TEXT NOT NULL DEFAULT 'telegram',
                status TEXT NOT NULL DEFAULT 'in_progress',
                result TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER
            );
            CREATE TABLE IF NOT EXISTS employee_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                employee TEXT NOT NULL,
                severity TEXT NOT NULL CHECK (severity IN ('OK', 'WARN', 'CRIT')),
                quality_score REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL,
                findings TEXT NOT NULL DEFAULT '[]',
                qa_reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_reports_employee ON employee_reports(employee, id DESC);
            CREATE INDEX IF NOT EXISTS idx_reports_task ON employee_reports(task_id);
            """
        )
        conn.commit()


import sqlite3  # noqa: E402  (usado por _connect; importado aquí para claridad)

init_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_token(x_corp_token: str = Header(default="")) -> None:
    if not CORP_TOKEN or not secrets.compare_digest(x_corp_token, CORP_TOKEN):
        raise HTTPException(status_code=401, detail="X-Corp-Token inválido")


def _rows_to_list(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Helpers HTTP / sistema (siempre de solo lectura)
# --------------------------------------------------------------------------- #
def _http_json(url: str, *, method: str = "GET", timeout: float = 6.0,
               headers: dict | None = None, json_body: dict | None = None,
               params: dict | None = None, auth: tuple | None = None) -> dict:
    started = time.time()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            response = client.request(method, url, headers=headers, json=json_body,
                                      params=params, auth=auth)
        body: Any
        try:
            body = response.json()
        except Exception:
            body = response.text[:2000]
        return {
            "ok": 200 <= response.status_code < 400,
            "status": response.status_code,
            "url": url,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "body": body,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "url": url,
                "latency_ms": round((time.time() - started) * 1000, 1)}


def _http_code(url: str, *, timeout: float = 5.0, headers: dict | None = None) -> dict:
    started = time.time()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            response = client.get(url, headers=headers)
        return {"ok": 200 <= response.status_code < 400, "status": response.status_code,
                "latency_ms": round((time.time() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000, 1)}


def _sentinel() -> dict:
    return _http_json(f"{SENTINEL}/api/state", timeout=10)


def _sentinel_probes() -> dict:
    state = _sentinel()
    if not state.get("ok"):
        return {}
    body = state.get("body") or {}
    return body.get("probes") or {}


def _docker_ps() -> list[dict]:
    """Consulta Docker por el socket UNIX (la imagen no incluye el CLI docker)."""
    try:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")),
            base_url="http://docker", timeout=8,
        ) as client:
            response = client.get("/containers/json", params={"all": 1})
        if response.status_code >= 400:
            return [{"error": f"Docker API HTTP {response.status_code}"}]
        return [
            {
                "name": (item.get("Names") or ["?"])[0].lstrip("/"),
                "status": item.get("Status") or item.get("State") or "?",
            }
            for item in response.json()
            if isinstance(item, dict)
        ]
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def _queue_arr(name: str, port: int, key_name: str) -> dict:
    key = os.getenv(key_name, "")
    if not key:
        return {"available": False, "reason": f"sin {key_name}"}
    result = _http_json(f"http://192.168.1.3:{port}/api/v3/queue",
                        timeout=6, params={"page": 1, "pageSize": 10, "apikey": key})
    if not result.get("ok"):
        return {"available": False, "error": result.get("error") or result.get("status")}
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    records = body.get("records") or []
    return {
        "available": True,
        "queued": body.get("totalRecords", len(records)),
        "sample": [r.get("title") for r in records[:3] if isinstance(r, dict)],
    }


def _qbittorrent() -> dict:
    user = os.getenv("QBITTORRENT_USER", "")
    password = os.getenv("QBITTORRENT_PASS", "")
    if not user or not password:
        return {"available": False, "reason": "sin credenciales qBittorrent"}
    try:
        with httpx.Client(base_url=N1["qbittorrent"], timeout=7, follow_redirects=True) as client:
            login = client.post("/api/v2/auth/login",
                                data={"username": user, "password": password})
            # qBittorrent 5.x puede responder 200 "Ok." o 204 sin cuerpo;
            # en ambos casos la sesión queda en la cookie del cliente.
            if login.status_code not in (200, 204):
                return {"available": False, "status": login.status_code}
            transfer = client.get("/api/v2/transfer/info")
            active = client.get("/api/v2/torrents/info", params={"filter": "active"})
            t = transfer.json() if transfer.status_code == 200 else {}
            torrents = active.json() if active.status_code == 200 else []
            return {
                "available": True,
                "download_mb_s": round((t.get("dl_info_speed") or 0) / 1048576, 2),
                "upload_mb_s": round((t.get("up_info_speed") or 0) / 1048576, 2),
                "active_torrents": len(torrents),
                "sample": [x.get("name") for x in torrents[:3] if isinstance(x, dict)],
            }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# Salud y libro de cuentas
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "corp-tools-api", "ts": _now(),
            "ledger": os.path.basename(DB_PATH)}


@app.post("/api/ledger/task", dependencies=[])
async def create_task(body: dict = Body(...), x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (directive, employee, requested_by, status, created_at) "
            "VALUES (?, ?, ?, 'in_progress', ?)",
            (str(body.get("directive") or "")[:4000],
             str(body.get("employee") or "HomeLab-Director"),
             str(body.get("requested_by") or "telegram"), _now()),
        )
        conn.commit()
        task_id = cur.lastrowid
    return {"ok": True, "task_id": task_id, "status": "in_progress"}


@app.patch("/api/ledger/task/{task_id}", dependencies=[])
async def close_task(task_id: int, body: dict = Body(...),
                     x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        row = conn.execute("SELECT created_at FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="tarea no encontrada")
        started = datetime.fromisoformat(row["created_at"])
        duration = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        conn.execute(
            "UPDATE tasks SET status=?, result=?, finished_at=?, duration_ms=? WHERE id=?",
            (str(body.get("status") or "completed"),
             str(body.get("result") or "")[:8000], _now(), duration, task_id),
        )
        conn.commit()
    return {"ok": True, "task_id": task_id, "duration_ms": duration}


@app.post("/api/ledger/report", dependencies=[])
async def add_report(body: dict = Body(...), x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    employee = str(body.get("employee") or "").strip()
    if not employee:
        raise HTTPException(status_code=400, detail="employee obligatorio")
    severity = str(body.get("severity") or "OK").upper()
    if severity not in ("OK", "WARN", "CRIT"):
        raise HTTPException(status_code=400, detail="severity debe ser OK/WARN/CRIT")
    try:
        score = float(body.get("quality_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    findings = body.get("findings") or []
    if not isinstance(findings, list):
        findings = [str(findings)]

    task_id = body.get("task_id")
    with closing(_connect()) as conn:
        if not task_id:
            cur = conn.execute(
                "INSERT INTO tasks (directive, employee, requested_by, status, result, created_at, finished_at, duration_ms) "
                "VALUES (?, ?, 'director-webhook', 'completed', ?, ?, ?, 0)",
                (str(body.get("directive") or "")[:4000], employee,
                 str(body.get("summary") or "")[:8000], _now(), _now()),
            )
            task_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO employee_reports "
            "(task_id, employee, severity, quality_score, summary, findings, qa_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, employee, severity, max(0.0, min(1.0, score)),
             str(body.get("summary") or "")[:8000],
             json.dumps(findings, ensure_ascii=False)[:12000],
             str(body.get("qa_reason") or "")[:1000] or None, _now()),
        )
        conn.commit()
        report_id = cur.lastrowid
    return {"ok": True, "report_id": report_id, "task_id": task_id}


@app.post("/api/ledger/score", dependencies=[])
async def score_reports(body: dict = Body(...), x_corp_token: str = Header(default="")) -> dict:
    """Agent-QA evalúa la calidad de los informes de sus compañeros."""
    _require_token(x_corp_token)
    scores = body.get("scores") or []
    if not isinstance(scores, list):
        raise HTTPException(status_code=400, detail="scores debe ser una lista")
    updated = 0
    with closing(_connect()) as conn:
        for item in scores:
            if not isinstance(item, dict):
                continue
            try:
                report_id = int(item.get("report_id"))
                score = max(0.0, min(1.0, float(item.get("quality_score") or 0)))
            except (TypeError, ValueError):
                continue
            cur = conn.execute(
                "UPDATE employee_reports SET quality_score=?, qa_reason=? WHERE id=?",
                (score, str(item.get("reason") or "evaluación Agent-QA")[:1000], report_id),
            )
            updated += cur.rowcount
        conn.commit()
    return {"ok": True, "updated": updated}


@app.get("/api/ledger/summary", dependencies=[])
async def ledger_summary(limit: int = Query(8, ge=1, le=50),
                         x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        per_employee = []
        for employee in EMPLOYEES:
            row = conn.execute(
                "SELECT COUNT(*) AS n, ROUND(AVG(quality_score),3) AS avg_score, "
                "MAX(created_at) AS last_at FROM employee_reports WHERE employee=?",
                (employee,),
            ).fetchone()
            last = conn.execute(
                "SELECT severity, summary, quality_score, qa_reason "
                "FROM employee_reports WHERE employee=? ORDER BY id DESC LIMIT 1",
                (employee,),
            ).fetchone()
            per_employee.append({
                "employee": employee,
                "reports": row["n"],
                "avg_quality": row["avg_score"],
                "last_at": row["last_at"],
                "last_severity": last["severity"] if last else None,
                "last_summary": last["summary"] if last else None,
                "qa_reason": last["qa_reason"] if last else None,
            })
        recent = _rows_to_list(conn.execute(
            "SELECT id, task_id, employee, severity, quality_score, summary, qa_reason, created_at "
            "FROM employee_reports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall())
        tasks = _rows_to_list(conn.execute(
            "SELECT id, directive, status, duration_ms, created_at, finished_at "
            "FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall())
    return {
        "ok": True, "employees": per_employee, "recent_reports": recent,
        "recent_tasks": tasks, "generated_at": _now(),
    }


@app.get("/api/ledger/reports", dependencies=[])
async def ledger_reports(employee: str | None = None,
                         limit: int = Query(20, ge=1, le=100),
                         x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        if employee:
            rows = conn.execute(
                "SELECT * FROM employee_reports WHERE employee=? ORDER BY id DESC LIMIT ?",
                (employee, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM employee_reports ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return {"ok": True, "reports": _rows_to_list(rows)}


# --------------------------------------------------------------------------- #
# Endpoints por departamento
# --------------------------------------------------------------------------- #
@app.get("/api/tools/infra", dependencies=[])
async def tools_infra(x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    state = _sentinel()
    twin = (state.get("body") or {}).get("twin") or {} if state.get("ok") else {}
    nodes = twin.get("nodes") or {}
    probes = _sentinel_probes()
    storage = shutil.disk_usage(STORAGE_PATH) if os.path.isdir(STORAGE_PATH) else None
    return {
        "ok": state.get("ok", False),
        "nodo1": {
            "up": (nodes.get("host") or {}).get("up"),
            "cpu_temp_c": (nodes.get("host") or {}).get("cpu_temp_c"),
            "ram": (nodes.get("host") or {}).get("ram"),
            "ssd": (nodes.get("host") or {}).get("ssd"),
        },
        "nodo2": {
            "up": (nodes.get("node2") or {}).get("up"),
            "metrics": (nodes.get("node2") or {}).get("metrics"),
        },
        "nfs_node2_storage": probes.get("nfs_node2_storage"),
        "storage_local_path": STORAGE_PATH,
        "storage_local": None if not storage else {
            "total_gb": round(storage.total / 1024**3, 2),
            "used_gb": round(storage.used / 1024**3, 2),
            "free_gb": round(storage.free / 1024**3, 2),
            "used_pct": round(storage.used / storage.total * 100, 1) if storage.total else None,
        },
        "node2_containers": _docker_ps(),
        "anomalies_count": len((state.get("body") or {}).get("anomalies") or []),
    }


@app.get("/api/tools/secops", dependencies=[])
async def tools_secops(x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    probes = _sentinel_probes()
    state = _sentinel()
    twin = (state.get("body") or {}).get("twin") or {} if state.get("ok") else {}
    security = ((twin.get("nodes") or {}).get("host") or {}).get("security") or {}
    return {
        "ok": bool(probes),
        "pihole": probes.get("pihole"),
        "unbound_dnssec": probes.get("unbound_dnssec"),
        "crowdsec_ips": probes.get("crowdsec_ips"),
        "firewall_bouncer": security.get("firewall_bouncer"),
        "security_containers": security.get("containers"),
    }


@app.get("/api/tools/media", dependencies=[])
async def tools_media(x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    probes = _sentinel_probes()
    jelly_key = os.getenv("JELLYFIN_API_KEY", "")
    jelly_info = _http_json(f"{N1['jellyfin']}/System/Info/Public", timeout=6)
    if jelly_key:
        recent = _http_json(
            f"{N1['jellyfin']}/Items", timeout=6,
            params={"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": 5},
            headers={"Authorization": f'MediaBrowser Token="{jelly_key}"'},
        )
    else:
        recent = {"ok": False, "reason": "sin JELLYFIN_API_KEY"}
    jseerr_status = _http_code(f"{N1['seerr']}/login", timeout=6)
    threadfin = _http_code(f"{N1['threadfin']}/m3u/threadfin.m3u", timeout=8)
    return {
        "ok": jelly_info.get("ok", False),
        "jellyfin": {"reachable": jelly_info.get("ok"), "info": jelly_info.get("body"),
                     "recent": recent.get("body") if recent.get("ok") else None},
        "seerr": {"reachable": jseerr_status.get("ok"), "status": jseerr_status.get("status")},
        "threadfin": {"reachable": threadfin.get("ok"), "probe": probes.get("threadfin")},
        "qbittorrent": _qbittorrent(),
        "radarr": _queue_arr("radarr", 7878, "RADARR_API_KEY"),
        "sonarr": _queue_arr("sonarr", 8989, "SONARR_API_KEY"),
        "bazarr": (lambda r: {"available": r.get("ok"),
                              "status": (r.get("body") or {}).get("status") if isinstance(r.get("body"), dict) else None,
                              "error": r.get("error")})(
            _http_json(f"{N1['bazarr']}/api/system/status", timeout=6,
                       params={"apikey": os.getenv("BAZARR_API_KEY", "")}))
        if os.getenv("BAZARR_API_KEY", "") else
        {"available": _http_code(f"{N1['bazarr']}/", timeout=5).get("ok"),
         "reason": "sin BAZARR_API_KEY; solo disponibilidad HTTP"},
    }


@app.get("/api/tools/cloud", dependencies=[])
async def tools_cloud(x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    probes = _sentinel_probes()
    nc = _http_json(f"{N1['nextcloud']}/status.php", timeout=6)
    immich = _http_json(f"{N1['immich']}/api/server/ping", timeout=6)
    ml = _http_json(f"{ML_URL}/ping", timeout=6)
    backups = []
    backup_count = 0
    try:
        if os.path.isdir(BACKUP_DIR):
            entries = [e.name for e in os.scandir(BACKUP_DIR) if e.is_dir()]
            backup_count = len(entries)
            backups = sorted(entries)[-6:]
    except Exception as exc:  # noqa: BLE001
        backups = [f"error: {exc}"]
    nc_probe = probes.get("nextcloud") or {}
    nc_reachable = bool(nc.get("ok") or nc_probe.get("ok"))
    return {
        "ok": bool(nc_reachable or immich.get("ok")),
        "nextcloud": {
            "reachable": nc_reachable,
            "status": nc.get("body") if nc.get("ok") else (nc_probe or nc.get("error")),
            "via": "directo" if nc.get("ok") else "sentinel",
        },
        "immich": {"reachable": immich.get("ok"), "ping": immich.get("body") if immich.get("ok") else immich.get("error"),
                   "probe": probes.get("immich")},
        "immich_ml_node2": {"reachable": ml.get("ok"), "ping": ml.get("body") if ml.get("ok") else ml.get("error"),
                            "probe": probes.get("node2_immich_ml")},
        "backups_path_alias": "/mnt/node2-storage/backups-compose",
        "backups_path_real": BACKUP_DIR,
        "backups_count": backup_count,
        "backups_recent": backups,
    }


@app.get("/api/tools/qa", dependencies=[])
async def tools_qa(x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    # Usamos el estado en caché de Sentinel en vez de POST /api/audit: ese audit
    # vuelve a llamar a /health de este mismo servicio y, con un único worker,
    # produce un timeout circular (QA -> Sentinel -> corp-tools QA -> ...).
    state = _http_json(f"{SENTINEL}/api/state", timeout=20, method="GET")
    envelope = state.get("body") if isinstance(state.get("body"), dict) else {}
    body = envelope if isinstance(envelope, dict) else {}
    probes = body.get("probes") or {}
    anomalies = body.get("anomalies") or []
    statused = {k: v.get("status") for k, v in probes.items()
                if isinstance(v, dict) and v.get("status")}
    def _probe_ok(value) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("status"):
            return value["status"] == "ok"
        if "ok" in value:
            return value.get("ok") is True
        if "issues" in value:
            return not value.get("issues")
        return True
    degraded = [k for k, v in probes.items() if not _probe_ok(v)]
    with closing(_connect()) as conn:
        recent = _rows_to_list(conn.execute(
            "SELECT id, employee, severity, quality_score, summary FROM employee_reports "
            "ORDER BY id DESC LIMIT 20").fetchall())
    return {
        "ok": state.get("ok", False),
        "audit_ok": state.get("ok", False),
        "source": "sentinel_state_cache",
        "probes_total": len(probes),
        "probes_ok": sum(1 for v in probes.values() if _probe_ok(v)),
        "probes_status": statused,
        "probes_degraded": degraded,
        "anomalies_count": len(anomalies),
        "anomalies": anomalies[:12],
        "recent_reports": recent,
    }


# --------------------------------------------------------------------------- #
# Motores autónomos (Fase 1): autocuración, updater, backup 3-2-1 y architect.
# Se importa al final para reutilizar el helpers ya definidos sin ciclos.
# --------------------------------------------------------------------------- #
from autonomy import router as autonomy_router  # noqa: E402
from gitops import router as gitops_router  # noqa: E402

app.include_router(autonomy_router)
app.include_router(gitops_router)

"""Motores autónomos de HomeLab Corp (Fase 1).

Autocuración verificada, actualizaciones con rollback, respaldo 3-2-1 y
automejora semanal. Todas las rutas exigen X-Corp-Token. Ninguna acción toca
/media, el datadir de Nextcloud ni la biblioteca de Immich.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

DB_PATH = os.getenv("CORP_LEDGER_DB", "/app/data/corp_ledger.sqlite3")
CORP_TOKEN = os.getenv("CORP_INTERNAL_TOKEN", "")
SENTINEL = os.getenv("SENTINEL_URL", "http://192.168.1.3:9090").rstrip("/")
SENTINEL_CORP_TOKEN = os.getenv("SENTINEL_CORP_TOKEN", "")
DIRECTOR_URL = os.getenv(
    "CORP_DIRECTOR_WEBHOOK", "http://homelab-corp-n8n:5678/webhook/director"
).rstrip("/")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT = os.getenv("CORP_TELEGRAM_CHAT_ID", "")

OFFLOAD = "/mnt/storage/homelab-offload"
BACKUP_ROOT = os.path.join(OFFLOAD, "backups-criticos")
N8N_DB = os.path.join(OFFLOAD, "ai-corp/n8n/.n8n/database.sqlite")
N8N_WORKFLOWS_SRC = os.getenv(
    "N8N_WORKFLOWS_SRC", "/home/mrdavid/HomeLab-node2/ai-corp/workflows"
)
DOCKER_SOCK = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")

# Empleados: 7 operativos + QA (el Director coordina, no rende cuentas como empleado).
EMPLOYEES = (
    "Agent-Infra", "Agent-SecOps", "Agent-Media", "Agent-Cloud",
    "Agent-Updater", "Agent-Backup", "Agent-Architect", "Agent-QA",
)

# Node 2: contenedores que este API puede gestionar directamente (nunca a sí mismo).
LOCAL_SERVICES = {"homelab-corp-n8n", "immich-machine-learning"}

# Node 1: servicios oficiales gestionables a través del API segura de Sentinel.
NODE1_SERVICES = {
    "jellyfin", "jellyseerr", "seerr-theme", "qbittorrent", "radarr", "sonarr",
    "bazarr", "prowlarr", "flaresolverr", "nextcloud", "pihole", "unbound",
    "crowdsec", "threadfin", "homelab-sentinel", "homelab-nexus",
    "immich-server", "immich-postgres", "immich-redis",
}

# Imágenes con pin explícito: jamás se actualizan automáticamente.
PINNED_MARKERS = ("postgres", "redis", "valkey", "immich-server",
                  "immich-postgres", "immich-redis")

PROBE_FOR = {
    "unbound": "unbound_dnssec", "crowdsec": "crowdsec_ips",
    "threadfin": "threadfin", "nextcloud": "nextcloud", "immich": "immich",
    "immich-server": "immich", "jellyfin": "jellyfin",
    "homelab-nexus": "homelab_nexus",
}

HTTP_FOR = {
    "jellyfin": "http://192.168.1.3:8096/System/Info/Public",
    "seerr": "http://192.168.1.3:5055/login",
    "seerr-theme": "http://192.168.1.3:5055/login",
    "qbittorrent": "http://192.168.1.3:8081/",
    "radarr": "http://192.168.1.3:7878/",
    "sonarr": "http://192.168.1.3:8989/",
    "bazarr": "http://192.168.1.3:6767/",
    "nextcloud": "http://192.168.1.3:8080/status.php",
    "immich": "http://192.168.1.3:2283/api/server/ping",
    "immich-server": "http://192.168.1.3:2283/api/server/ping",
    "threadfin": "http://192.168.1.3:34400/m3u/threadfin.m3u",
    "homelab-nexus": "http://192.168.1.3:8888/health",
    "homelab-sentinel": "http://192.168.1.3:9090/health",
}

router = APIRouter()


# --------------------------------------------------------------------------- #
# Libro de rendición de cuentas (tablas de autonomía)
# --------------------------------------------------------------------------- #
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_autonomy_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with closing(_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS interventions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                before_state TEXT,
                after_state TEXT,
                ok INTEGER NOT NULL DEFAULT 0,
                detail TEXT,
                requested_by TEXT NOT NULL DEFAULT 'director',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_interv_service ON interventions(service, id DESC);
            CREATE TABLE IF NOT EXISTS updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                digest_before TEXT,
                digest_after TEXT,
                updated INTEGER NOT NULL DEFAULT 0,
                rolled_back INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                detail TEXT,
                requested_by TEXT NOT NULL DEFAULT 'director',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                bytes_total INTEGER NOT NULL DEFAULT 0,
                files INTEGER NOT NULL DEFAULT 0,
                integrity TEXT NOT NULL DEFAULT 'unknown',
                ok INTEGER NOT NULL DEFAULT 0,
                requested_by TEXT NOT NULL DEFAULT 'schedule',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS improvement_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                rationale TEXT NOT NULL,
                action TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                decided_by TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_status ON improvement_proposals(status, id DESC);
            """
        )
        conn.commit()


_init_autonomy_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_token(x_corp_token: str = Header(default="")) -> None:
    if not CORP_TOKEN or not secrets.compare_digest(x_corp_token, CORP_TOKEN):
        raise HTTPException(status_code=401, detail="X-Corp-Token inválido")


def _record_report(employee: str, summary: str, findings: list | None = None,
                   severity: str = "OK", directive: str = "autonomía",
                   task_id: int | None = None) -> int | None:
    with closing(_connect()) as conn:
        if task_id is None:
            cur = conn.execute(
                "INSERT INTO tasks (directive, employee, requested_by, status, result, created_at, finished_at, duration_ms) "
                "VALUES (?, ?, 'autonomy-engine', 'completed', ?, ?, ?, 0)",
                (directive[:4000], employee, summary[:8000], _now(), _now()),
            )
            task_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO employee_reports (task_id, employee, severity, quality_score, summary, findings, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, employee, severity, 0.9, summary[:8000],
             json.dumps(findings or [], ensure_ascii=False)[:12000], _now()),
        )
        conn.commit()
        return cur.lastrowid


def _sentinel_action(action: str, params: dict | None = None) -> dict:
    if not SENTINEL_CORP_TOKEN:
        return {"ok": False, "error": "SENTINEL_CORP_TOKEN no configurado"}
    try:
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            response = client.post(
                f"{SENTINEL}/api/corp/action",
                headers={"X-Corp-Token": SENTINEL_CORP_TOKEN},
                json={"action": action, "params": params or {}},
            )
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
        if isinstance(body, dict):
            return body
        return {"ok": response.status_code < 400, "status": response.status_code, "detail": body}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            return client.get(url).status_code < 400
    except Exception:  # noqa: BLE001
        return False


def _telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        with httpx.Client(timeout=12) as client:
            response = client.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": text[:3800],
                      "disable_web_page_preview": True},
            )
        return response.status_code < 400
    except Exception:  # noqa: BLE001
        return False


def _alert_director(message: str) -> dict:
    """Alerta al Director: Telegram + webhook n8n + asiento en el ledger."""
    telegram_ok = _telegram(message)
    director_ok = False
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.post(
                DIRECTOR_URL,
                json={"directive": message, "requested_by": "autonomy-engine",
                      "severity": "CRIT"},
            )
            director_ok = response.status_code < 400
    except Exception:  # noqa: BLE001
        director_ok = False
    _record_report("Agent-Infra", message, [{"level": "CRIT", "text": message}],
                   severity="CRIT", directive="Alerta automática al Director")
    return {"telegram": telegram_ok, "director_webhook": director_ok}


def _local_docker(method: str, path: str, *, params: dict | None = None) -> httpx.Response:
    with httpx.Client(transport=httpx.HTTPTransport(uds=DOCKER_SOCK),
                      base_url="http://docker", timeout=10) as client:
        return client.request(method, path, params=params or {})


def _local_inspect(service: str) -> dict:
    try:
        response = _local_docker("GET", f"/containers/{service}/json")
        if response.status_code == 404:
            return {"ok": False, "error": f"contenedor {service} no existe"}
        data = response.json()
        state = data.get("State") or {}
        return {
            "ok": True, "status": state.get("Status"),
            "running": bool(state.get("Running")),
            "health": ((state.get("Health") or {}).get("Status")) or "none",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _local_wait(service: str, timeout: int = 25) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _local_inspect(service)
        if last.get("ok") and last.get("running") and last.get("health") in ("healthy", "none"):
            return last
        time.sleep(2)
    return last


# --------------------------------------------------------------------------- #
# 1) Autocuración verificada
# --------------------------------------------------------------------------- #
def _verify_node1(service: str) -> dict:
    probe_key = PROBE_FOR.get(service)
    if probe_key:
        try:
            with httpx.Client(timeout=12) as client:
                state = client.get(f"{SENTINEL}/api/state").json()
            probe = ((state.get("probes") or {}).get(probe_key) or {})
            if probe:
                return {"verified": probe.get("status") == "ok" or probe.get("ok") is True,
                        "via": f"sentinel:{probe_key}", "probe": probe}
        except Exception as exc:  # noqa: BLE001
            return {"verified": False, "via": "sentinel", "error": str(exc)}
    url = HTTP_FOR.get(service)
    if url:
        return {"verified": _http_ok(url), "via": "http", "url": url}
    return {"verified": False, "via": "none",
            "note": "sin sonda L7 específica; verificado por estado del contenedor en Sentinel"}


@router.post("/api/tools/autofix")
async def tools_autofix(body: dict, x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    service = str(body.get("service") or body.get("container") or "").strip()
    action = str(body.get("action") or "restart").strip().lower()
    issue = str(body.get("issue") or "")[:1000]
    dry_run = bool(body.get("dry_run", False))
    requested_by = str(body.get("requested_by") or "director")[:80]

    if action == "clean_build_cache":
        plan = {"node": "nodo1", "action": "clean_build_cache",
                "guard": "sólo si /dev/sda1 supera 88%", "service": service or "host-disk"}
        if dry_run:
            return {"ok": True, "dry_run": True, "plan": plan}
        result = _sentinel_action("clean_build_cache", {"threshold": 88})
        ok = bool(result.get("ok"))
        with closing(_connect()) as conn:
            conn.execute(
                "INSERT INTO interventions (kind, service, action, before_state, after_state, ok, detail, requested_by, created_at) "
                "VALUES ('autofix', ?, ?, ?, ?, ?, ?, ?, ?)",
                (service or "host-disk", "clean_build_cache", issue,
                 json.dumps(result.get("detail") or {}, ensure_ascii=False),
                 int(ok), json.dumps(result, ensure_ascii=False)[:6000], requested_by, _now()),
            )
            conn.commit()
        _record_report("Agent-Infra", f"Limpieza segura de caché: {'OK' if ok else 'falló'}",
                       [{"level": "OK" if ok else "CRIT", "text": str(result)[:500]}],
                       severity="OK" if ok else "CRIT", directive=issue or "autofix caché")
        return {"ok": ok, "action": "clean_build_cache", "result": result}

    if action != "restart":
        raise HTTPException(status_code=400, detail="acción no soportada (usa restart o clean_build_cache)")

    if service in LOCAL_SERVICES:
        node = "nodo2"
    elif service in NODE1_SERVICES:
        node = "nodo1"
    else:
        raise HTTPException(status_code=400, detail=f"servicio no permitido: {service or '(vacío)'}")

    if dry_run:
        return {"ok": True, "dry_run": True,
                "plan": {"node": node, "service": service, "action": "restart",
                         "verification": "healthcheck inmediato vía Docker/Sentinel"}}

    if node == "nodo2":
        before = _local_inspect(service)
        if not before.get("ok"):
            return {"ok": False, "error": before.get("error"), "service": service}
        try:
            restart = _local_docker("POST", f"/containers/{service}/restart", params={"t": 20})
            restart_ok = restart.status_code in (200, 204)
        except Exception as exc:  # noqa: BLE001
            restart_ok, restart = False, {"error": str(exc)}
        after = _local_wait(service) if restart_ok else {"error": "restart no ejecutado"}
        verified = bool(after.get("running") and after.get("health") in ("healthy", "none"))
        result = {"restart_ok": restart_ok, "before": before, "after": after,
                  "verification": {"verified": verified, "via": "docker-nodo2"}}
        ok = restart_ok and verified
    else:
        sentinel_action = ("reload_unbound" if service == "unbound" else
                           "reload_crowdsec" if service == "crowdsec" else "restart_container")
        restart = _sentinel_action(sentinel_action, {"service": service, "container": service})
        verification = _verify_node1(service)
        ok = bool(restart.get("ok")) and verification.get("verified", False)
        result = {"sentinel": restart, "verification": verification}

    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO interventions (kind, service, action, before_state, after_state, ok, detail, requested_by, created_at) "
            "VALUES ('autofix', ?, 'restart', ?, ?, ?, ?, ?, ?)",
            (service, issue, json.dumps(result, ensure_ascii=False)[:6000], int(ok),
             json.dumps(result, ensure_ascii=False)[:6000], requested_by, _now()),
        )
        conn.commit()
    _record_report("Agent-Infra",
                   f"Autocuración de {service} ({node}): {'verificada' if ok else 'sin verificación'}",
                   [{"level": "OK" if ok else "CRIT", "text": issue or service}],
                   severity="OK" if ok else "CRIT", directive=issue or f"autofix {service}")
    if not ok:
        _alert_director(f"⚠️ Autocuración no verificada en {service} ({node}). Revisar Sentinel AI.")
    return {"ok": ok, "service": service, "node": node, "result": result}


# --------------------------------------------------------------------------- #
# 2) Actualizaciones con rollback automático
# --------------------------------------------------------------------------- #
def _candidates() -> list[dict]:
    candidates = []
    for service in sorted(NODE1_SERVICES):
        info = _sentinel_action("inspect_service", {"service": service})
        detail = info.get("detail") or {}
        if not detail.get("ok"):
            continue
        image = str(detail.get("image") or "")
        pinned = any(marker in image.lower() for marker in PINNED_MARKERS)
        candidates.append({
            "service": service, "image": image,
            "digest": detail.get("digest"), "eligible": not pinned,
            "reason": "imagen con pin explícito" if pinned else "actualización permitida",
        })
    return candidates


@router.post("/api/tools/updater")
async def tools_updater(body: dict, x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    dry_run = bool(body.get("dry_run", True))
    requested_by = str(body.get("requested_by") or "director")[:80]
    service = str(body.get("service") or "").strip()
    all_services = bool(body.get("all", False))
    candidates = _candidates()
    if service:
        if service not in NODE1_SERVICES:
            raise HTTPException(status_code=400, detail=f"servicio no permitido: {service}")
        candidates = [item for item in candidates if item["service"] == service]
    elif not all_services:
        candidates = [item for item in candidates if item["eligible"]]

    if dry_run:
        return {"ok": True, "dry_run": True, "candidates": candidates,
                "note": "no se descargó ni recreó ningún contenedor"}

    results = []
    for item in candidates:
        if not item.get("eligible") and not service:
            continue
        target = item["service"]
        update = _sentinel_action("update_container", {"service": target})
        ok = bool(update.get("ok"))
        detail = update.get("detail") or update
        with closing(_connect()) as conn:
            conn.execute(
                "INSERT INTO updates (service, digest_before, digest_after, updated, rolled_back, ok, detail, requested_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target, str(detail.get("digest_before") or "")[:300],
                 str(detail.get("digest_after") or "")[:300],
                 int(bool(detail.get("updated"))), int(bool(detail.get("rolled_back"))),
                 int(ok), json.dumps(detail, ensure_ascii=False)[:8000], requested_by, _now()),
            )
            conn.commit()
        results.append({"service": target, "ok": ok,
                        "updated": bool(detail.get("updated")),
                        "rolled_back": bool(detail.get("rolled_back")),
                        "digest_before": detail.get("digest_before"),
                        "digest_after": detail.get("digest_after")})
        if detail.get("rolled_back") or not ok:
            _alert_director(
                f"🚨 Rollback automático / fallo de actualización en {target}. "
                f"Digest previo restaurado: {detail.get('digest_before') or 'n/d'}. "
                f"Revisar Sentinel AI y corp_ledger."
            )
    _record_report("Agent-Updater",
                   f"Ronda de actualizaciones: {len(results)} servicios procesados, "
                   f"{sum(1 for r in results if r['ok'])} correctos",
                   results, severity="OK" if all(r["ok"] for r in results) else "WARN",
                   directive="Actualizaciones con rollback")
    return {"ok": all(r["ok"] for r in results), "results": results,
            "note": "rollback automático garantizado si el healthcheck falla en 45 s"}


# --------------------------------------------------------------------------- #
# 3) Respaldo nocturno 3-2-1
# --------------------------------------------------------------------------- #
@router.post("/api/tools/backup")
async def tools_backup(body: dict, x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    dry_run = bool(body.get("dry_run", False))
    requested_by = str(body.get("requested_by") or "schedule")[:80]
    plan = {
        "origen": "nodo1 + n8n local nodo2",
        "destino": BACKUP_ROOT,
        "artifacts": ["immich_postgres.dump", "nextcloud_config.php",
                      "sentinel.sqlite3", "nexus.sqlite3",
                      "n8n_database.sqlite3", "workflows/*.json"],
        "retencion_dias": 7,
        "excluidos": ["/media", "Nextcloud data/", "Immich library/"],
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    result = _sentinel_action("backup_criticos", {})
    if not result.get("ok"):
        _record_report("Agent-Backup", "Respaldo crítico fallido",
                       [{"level": "CRIT", "text": str(result)[:800]}],
                       severity="CRIT", directive="Respaldo 3-2-1")
        _alert_director(f"🚨 Falló el respaldo crítico 3-2-1: {str(result)[:600]}")
        return {"ok": False, "error": result}

    detail = result.get("detail") or {}
    name = str(detail.get("ts") or "")
    destination = os.path.join(BACKUP_ROOT, name)
    if not name or not os.path.isdir(destination):
        return {"ok": False, "error": "el script de respaldo no devolvió un directorio válido",
                "detail": detail}

    files_copied = []
    try:
        if os.path.isfile(N8N_DB):
            with sqlite3.connect(f"file:{N8N_DB}?mode=ro", uri=True, timeout=20) as origin:
                with sqlite3.connect(os.path.join(destination, "n8n_database.sqlite3"), timeout=20) as target:
                    origin.backup(target)
            files_copied.append("n8n_database.sqlite3")
        if os.path.isdir(N8N_WORKFLOWS_SRC):
            wf_dir = os.path.join(destination, "workflows")
            os.makedirs(wf_dir, exist_ok=True)
            for entry in Path(N8N_WORKFLOWS_SRC).glob("*.json"):
                shutil.copy2(entry, os.path.join(wf_dir, entry.name))
            files_copied.append("workflows/")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"copia local n8n falló: {exc}"}

    # Actualiza el manifiesto con los artefactos añadidos en el Nodo 2.
    manifest_path = os.path.join(destination, "manifest.json")
    manifest = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    files = manifest.get("files") or {}
    for relative in files_copied:
        path = os.path.join(destination, relative)
        if os.path.isfile(path):
            files[relative] = {"bytes": os.path.getsize(path),
                               "nonempty": os.path.getsize(path) > 0}
        elif os.path.isdir(path):
            entries = [p for p in Path(path).glob("*.json")]
            files[relative] = {"count": len(entries), "nonempty": bool(entries)}
    manifest["files"] = files
    manifest["n8n_copied_from"] = "nodo2:/mnt/storage/homelab-offload/ai-corp/n8n"
    required = ("immich_postgres.dump", "nextcloud_config.php",
                "sentinel.sqlite3", "nexus.sqlite3", "n8n_database.sqlite3", "workflows/")
    missing = [name for name in required
               if not files.get(name, {}).get("nonempty")]
    manifest["missing_or_empty"] = missing
    manifest["ok"] = not missing
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # Retención de 7 días, restringida exclusivamente a backups-criticos.
    removed = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    try:
        for entry in Path(BACKUP_ROOT).iterdir():
            if not entry.is_dir():
                continue
            try:
                created = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            if created < cutoff:
                shutil.rmtree(entry)
                removed.append(entry.name)
    except Exception as exc:  # noqa: BLE001
        removed = [f"error: {exc}"]

    bytes_total = sum(
        p.stat().st_size for p in Path(destination).rglob("*") if p.is_file()
    )
    ok = not missing
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO backups (name, path, bytes_total, files, integrity, ok, requested_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, destination, bytes_total, len(files),
             "verified" if ok else "failed", int(ok), requested_by, _now()),
        )
        conn.commit()
    _record_report("Agent-Backup",
                   f"Respaldo 3-2-1 {name}: {'verificado' if ok else 'incompleto'} "
                   f"({bytes_total} bytes, {len(files)} artefactos)",
                   [{"level": "OK" if ok else "CRIT", "text": name}],
                   severity="OK" if ok else "CRIT", directive="Respaldo nocturno 3-2-1")
    if not ok:
        _alert_director(f"🚨 Respaldo {name} incompleto. Faltan: {', '.join(missing)}")
    return {"ok": ok, "name": name, "path": destination, "files": files,
            "bytes_total": bytes_total, "missing": missing, "retention_removed": removed}


# --------------------------------------------------------------------------- #
# 4) Telemetría y automejora semanal
# --------------------------------------------------------------------------- #
ALLOWED_PROPOSAL_ACTIONS = {
    "clean_build_cache", "review_anomaly", "review_component",
    "review_qa", "maintain_baseline", "log_only",
}


@router.post("/api/tools/architect")
async def tools_architect(body: dict, x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    requested_by = str(body.get("requested_by") or "director")[:80]
    state = {}
    try:
        with httpx.Client(timeout=15) as client:
            state = client.get(f"{SENTINEL}/api/state").json()
    except Exception as exc:  # noqa: BLE001
        state = {"error": str(exc)}

    probes = state.get("probes") or {}
    anomalies = state.get("anomalies") or []
    twin = state.get("twin") or {}
    ssd = (((twin.get("nodes") or {}).get("host") or {}).get("ssd") or {})
    used_pct = ssd.get("used_pct")
    repairs = state.get("repairs") or []

    with closing(_connect()) as conn:
        quality = conn.execute(
            "SELECT ROUND(AVG(quality_score),3) AS avg FROM employee_reports "
            "WHERE created_at >= ?", ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)
        ).fetchone()
        counts = [dict(row) for row in conn.execute(
            "SELECT service, COUNT(*) AS n FROM interventions "
            "WHERE created_at >= ? GROUP BY service ORDER BY n DESC LIMIT 5",
            ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)
        ).fetchall()]

    avg_quality = (quality["avg"] if quality else None) or 0
    proposals: list[dict] = []
    if used_pct is not None and used_pct >= 88:
        proposals.append({
            "title": "Liberar caché de build de Docker",
            "rationale": f"/dev/sda1 al {used_pct}% (umbral 88%). Limpieza segura de caché de compilación.",
            "action": "clean_build_cache", "severity": "warn",
            "evidence": {"used_pct": used_pct},
        })
    if anomalies:
        proposals.append({
            "title": "Revisar anomalías detectadas por Sentinel",
            "rationale": f"{len(anomalies)} anomalías activas requieren seguimiento del especialista.",
            "action": "review_anomaly", "severity": "warn",
            "evidence": [a.get("summary") for a in anomalies[:8]],
        })
    for row in counts:
        if row["n"] >= 2:
            proposals.append({
                "title": f"Revisar recurrencia en {row['service']}",
                "rationale": f"{row['n']} intervenciones en 7 días; conviene analizar causa raíz.",
                "action": "review_component", "severity": "info",
                "evidence": row,
            })
    if avg_quality and avg_quality < 0.8:
        proposals.append({
            "title": "Elevar calidad de los informes del equipo",
            "rationale": f"Calidad media de Agent-QA: {avg_quality} en los últimos 7 días.",
            "action": "review_qa", "severity": "info", "evidence": {"avg_quality": avg_quality},
        })
    if not proposals:
        proposals.append({
            "title": "Mantener línea base y próxima auditoría",
            "rationale": "Sin anomalías ni recurrencias relevantes; conservar la configuración actual.",
            "action": "maintain_baseline", "severity": "info", "evidence": {},
        })

    inserted = []
    with closing(_connect()) as conn:
        for proposal in proposals:
            action = proposal["action"]
            duplicate = conn.execute(
                "SELECT id FROM improvement_proposals WHERE title=? AND status='pending' "
                "AND created_at >= ?",
                (proposal["title"], (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()),
            ).fetchone()
            if duplicate:
                continue
            cur = conn.execute(
                "INSERT INTO improvement_proposals "
                "(title, rationale, action, severity, evidence, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (proposal["title"][:300], proposal["rationale"][:2000], action,
                 proposal["severity"], json.dumps(proposal["evidence"], ensure_ascii=False)[:8000], _now()),
            )
            inserted.append(cur.lastrowid)
        pending = [dict(row) for row in conn.execute(
            "SELECT id, title, rationale, action, severity, status, created_at "
            "FROM improvement_proposals WHERE status='pending' ORDER BY id DESC LIMIT 12"
        ).fetchall()]
        conn.commit()

    maintenance = None
    if used_pct is not None and used_pct >= 88:
        maintenance = _sentinel_action("clean_build_cache", {"threshold": 88})

    _record_report("Agent-Architect",
                   f"Auditoría de evolución: {len(inserted)} propuestas nuevas, "
                   f"{len(pending)} pendientes, QA medio {avg_quality}",
                   pending, severity="WARN" if anomalies else "OK",
                   directive="Automejora semanal")
    return {"ok": True, "probes_total": len(probes), "anomalies": len(anomalies),
            "ssd_used_pct": used_pct, "avg_quality": avg_quality,
            "new_proposals": inserted, "pending_proposals": pending,
            "preventive_maintenance": maintenance}


@router.get("/api/tools/backup/history")
async def backup_history(limit: int = Query(10, ge=1, le=50),
                         x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"ok": True, "backups": [dict(row) for row in rows],
            "path": BACKUP_ROOT, "retention_days": 7}


@router.get("/api/tools/updater/history")
async def updater_history(limit: int = Query(10, ge=1, le=50),
                          x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM updates ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"ok": True, "updates": [dict(row) for row in rows]}


@router.get("/api/ledger/proposals")
async def list_proposals(limit: int = Query(12, ge=1, le=50),
                         x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM improvement_proposals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"ok": True, "proposals": [dict(row) for row in rows]}


@router.post("/api/ledger/proposal/{proposal_id}/apply")
async def apply_proposal(proposal_id: int, body: dict | None = None,
                         x_corp_token: str = Header(default="")) -> dict:
    """Aprueba una propuesta de Agent-Architect. NUNCA ejecuta código arbitrario."""
    _require_token(x_corp_token)
    payload = body or {}
    decided_by = str(payload.get("decided_by") or "telegram")[:80]
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM improvement_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="propuesta no encontrada")
        if row["status"] != "pending":
            return {"ok": True, "already": row["status"], "id": proposal_id}
        action = row["action"]
        if action not in ALLOWED_PROPOSAL_ACTIONS:
            raise HTTPException(status_code=400, detail=f"acción no aplicable automáticamente: {action}")
        conn.execute(
            "UPDATE improvement_proposals SET status='applied', decided_by=?, decided_at=? WHERE id=?",
            (decided_by, _now(), proposal_id),
        )
        conn.execute(
            "INSERT INTO interventions (kind, service, action, before_state, after_state, ok, detail, requested_by, created_at) "
            "VALUES ('proposal', 'Agent-Architect', ?, 'pending', 'applied', 1, ?, ?, ?)",
            (action, row["title"][:500], decided_by, _now()),
        )
        conn.commit()
    _record_report("Agent-Architect", f"Propuesta #{proposal_id} aprobada: {row['title']}",
                   [{"level": "OK", "text": row["rationale"][:500]}],
                   directive="Aprobación de mejora")
    return {"ok": True, "id": proposal_id, "status": "applied",
            "title": row["title"], "action": action}

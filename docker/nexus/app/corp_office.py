"""Puente en tiempo real entre HomeLab Nexus, HomeLab Corp y Sentinel AI.

Solo lectura para construir el estado de la oficina. Las acciones se limitan a
dos webhooks explícitos y predefinidos de n8n (ronda del Director y revisión de
un empleado). El token interno de Corp nunca sale del proceso: se envía únicamente
como cabecera X-Corp-Token a corp-tools-api y jamás se devuelve al navegador.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

CORP_API_URL = os.environ.get("CORP_API_URL", "http://192.168.1.4:8006").rstrip("/")
CORP_TOKEN = os.environ.get("CORP_INTERNAL_TOKEN", "")
SENTINEL_URL = os.environ.get("SENTINEL_URL", "http://192.168.1.3:9090").rstrip("/")

# URL canónica del Director; base derivada para los webhooks de los 8 empleados.
DIRECTOR_URL = os.environ.get(
    "CORP_DIRECTOR_URL", "http://192.168.1.4:5678/webhook/director"
).rstrip("/")
WEBHOOK_BASE = (
    DIRECTOR_URL.rsplit("/webhook/", 1)[0] if "/webhook/" in DIRECTOR_URL
    else "http://192.168.1.4:5678"
)

TIMEOUT = 2.0

# id, agente del ledger, nombre, rol, biografía operativa y webhook n8n.
EMPLOYEES: tuple[dict[str, str], ...] = (
    {
        "id": "infra", "agent": "Agent-Infra", "name": "Infra", "station": "01",
        "role": "Infraestructura & Nodos",
        "bio": "Vigila ambos nodos, el disco del OptiPlex, el montaje NFS de offload y los contenedores. Es la primera línea cuando el gemelo digital reporta degradación.",
        "webhook": "agent-infra",
        "hint": ("disk", "node2", "nfs", "containers"),
    },
    {
        "id": "secops", "agent": "Agent-SecOps", "name": "SecOps", "station": "02",
        "role": "Red Soberana & Defensa",
        "bio": "Guarda Pi-hole, Unbound DNSSEC, CrowdSec y el bouncer iptables. Nunca bloquea redes privadas; solo interpreta telemetría de seguridad.",
        "webhook": "agent-secops",
        "hint": ("pihole", "unbound", "dnssec", "crowdsec"),
    },
    {
        "id": "media", "agent": "Agent-Media", "name": "Media", "station": "03",
        "role": "Multimedia & Streaming",
        "bio": "Supervisa Jellyfin HomeFlix, Threadfin, Seerr, qBittorrent y las colas Radarr/Sonarr. No modifica /media ni descargas activas.",
        "webhook": "agent-media",
        "hint": ("jellyfin", "threadfin", "seerr"),
    },
    {
        "id": "cloud", "agent": "Agent-Cloud", "name": "Cloud", "station": "04",
        "role": "Nube & Fotos",
        "bio": "Cuida Nextcloud, Immich y el ML delegado al Nodo 2. La librería externa de fotos se mantiene solo-lectura para no duplicar datos.",
        "webhook": "agent-cloud",
        "hint": ("nextcloud", "immich", "node2_immich_ml"),
    },
    {
        "id": "updater", "agent": "Agent-Updater", "name": "Updater", "station": "05",
        "role": "Actualizaciones seguras",
        "bio": "Prepara actualizaciones con verificación y rollback. Las imágenes con pin (Postgres/Redis/Immich) jamás se actualizan automáticamente.",
        "webhook": "agent-updater",
        "hint": ("updates", "rollback"),
    },
    {
        "id": "backup", "agent": "Agent-Backup", "name": "Backup", "station": "06",
        "role": "Respaldo 3-2-1",
        "bio": "Ejecuta y verifica respaldos críticos en el offload. Nunca toca volúmenes de Nextcloud, /media ni la base de datos de Immich.",
        "webhook": "agent-backup",
        "hint": ("backup",),
    },
    {
        "id": "architect", "agent": "Agent-Architect", "name": "Architect", "station": "07",
        "role": "Arquitectura & Evolución",
        "bio": "Analiza anomalías recurrentes y propone mejoras de línea base. Las propuestas requieren aprobación; nunca ejecuta código arbitrario.",
        "webhook": "agent-architect",
        "hint": ("proposal", "architecture"),
    },
    {
        "id": "qa", "agent": "Agent-QA", "name": "QA", "station": "08",
        "role": "Calidad & Auditoría",
        "bio": "Califica cada informe del ledger y contrasta el estado con las sondas L7 de Sentinel. Su dictamen es la nota de calidad de la oficina.",
        "webhook": "agent-qa",
        "hint": ("qa", "anomal", "probe"),
    },
)

DIRECTOR: dict[str, str] = {
    "id": "director",
    "agent": "HomeLab-Director",
    "name": "HomeLab-Director",
    "station": "00",
    "role": "Dirección & Coordinación",
    "bio": "Coordina a los 8 empleados IA, consolida entregas, convoca rondas generales y presenta el reporte ejecutivo al Canal de Telegram autorizado.",
}

BY_ID: dict[str, dict[str, str]] = {e["id"]: e for e in EMPLOYEES}
BY_ID[DIRECTOR["id"]] = DIRECTOR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds()
    except Exception:
        return None


async def _get_json(client: httpx.AsyncClient, url: str,
                    params: dict | None = None) -> dict:
    """GET tolerante a fallos; nunca expone URLs con credenciales."""
    try:
        resp = await client.get(url, params=params)
        if 200 <= resp.status_code < 400:
            payload = resp.json()
            return {"ok": True, "data": _as_dict(payload)}
        return {"ok": False, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001 - degradación controlada
        return {"ok": False, "error": type(exc).__name__}


async def fetch_sources() -> dict:
    """Consulta todas las fuentes en paralelo con un único cliente HTTP."""
    headers = {"User-Agent": "HomeLab-Nexus/1.1"}
    if CORP_TOKEN:
        headers["X-Corp-Token"] = CORP_TOKEN
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=False,
                                 follow_redirects=True, headers=headers) as client:
        results = await asyncio.gather(
            _get_json(client, f"{CORP_API_URL}/api/ledger/summary", {"limit": 16}),
            _get_json(client, f"{CORP_API_URL}/api/tools/backup/history", {"limit": 3}),
            _get_json(client, f"{CORP_API_URL}/api/tools/updater/history", {"limit": 3}),
            _get_json(client, f"{CORP_API_URL}/api/ledger/proposals", {"limit": 6}),
            _get_json(client, f"{CORP_API_URL}/api/tools/qa"),
            _get_json(client, f"{SENTINEL_URL}/api/state"),
            return_exceptions=True,
        )
    keys = ("ledger", "backup", "updater", "proposals", "qa", "sentinel")
    out: dict[str, dict] = {}
    for index, key in enumerate(keys):
        value = results[index]
        out[key] = value if isinstance(value, dict) else {"ok": False, "error": "unreachable"}
    return out


def _probe_ok(probes: dict, name: str) -> bool | None:
    probe = probes.get(name)
    if not isinstance(probe, dict):
        return None
    status = probe.get("status")
    if status:
        return str(status) == "ok"
    if "ok" in probe:
        return probe.get("ok") is True
    return True


def _short(text: Any, limit: int = 150) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/d"


def _mb(value: Any) -> str:
    try:
        return f"{float(value) / 1048576:.0f} MB"
    except (TypeError, ValueError):
        return "n/d"


def _bubble(cfg: dict, ctx: dict) -> str:
    """Texto corto con métricas reales del ledger / Sentinel."""
    probes = ctx["probes"]
    led = ctx["ledger"]
    rec = ctx["records"].get(cfg["agent"], {})

    if cfg["id"] == "director":
        if ctx["anomaly_count"]:
            return f"{ctx['anomaly_count']} anomalía(s) · {ctx['probes_ok']}/{ctx['probes_total']} sondas OK"
        if ctx["in_progress"]:
            return f"{ctx['in_progress']} directiva(s) en curso · {ctx['probes_ok']}/{ctx['probes_total']} sondas OK"
        return f"Clúster estable · {ctx['probes_ok']}/{ctx['probes_total']} sondas OK"

    if cfg["id"] == "infra":
        disk = _as_dict(probes.get("disk"))
        node2 = _as_dict(probes.get("node2_metrics"))
        return (
            f"SSD {_percent(disk.get('used_pct'))} · {disk.get('free_gb', 'n/d')} GB libres · "
            f"Nodo2 {'OK' if node2.get('online') else 'FUERA'}"
        )

    if cfg["id"] == "secops":
        dnssec = _as_dict(probes.get("unbound_dnssec"))
        crowd = _as_dict(probes.get("crowdsec_ips"))
        pihole = _as_dict(probes.get("pihole"))
        return (
            f"DNSSEC {'OK' if dnssec.get('dnssec_ok') else 'ALERTA'} · "
            f"CrowdSec {crowd.get('decisions', 'n/d')} decisiones · "
            f"Pi-hole {'filtrando' if pihole.get('filtering_active') else 'revisar'}"
        )

    if cfg["id"] == "media":
        jelly = _as_dict(probes.get("jellyfin"))
        details = _as_dict(jelly.get("details"))
        version = details.get("Version") or ""
        return (
            f"Jellyfin {version or ('OK' if jelly.get('ok') else 'revisar')} · "
            f"Threadfin {'OK' if _probe_ok(probes, 'threadfin') else 'revisar'}"
        )

    if cfg["id"] == "cloud":
        nc = _as_dict(probes.get("nextcloud"))
        ml = _as_dict(probes.get("node2_immich_ml"))
        return (
            f"Nextcloud {'OK' if _probe_ok(probes, 'nextcloud') else 'revisar'} · "
            f"Immich {'OK' if _probe_ok(probes, 'immich') else 'revisar'} · "
            f"ML Nodo2 {'OK' if _probe_ok(probes, 'node2_immich_ml') or ml.get('ok') else 'revisar'}"
            + (f" · {nc.get('version', '')}" if nc.get("version") else "")
        )

    if cfg["id"] == "updater":
        updates = ctx["updates"]
        last = updates[0] if updates else {}
        if last:
            return f"Última actualización: {last.get('service', '?')} · {last.get('result') or last.get('status') or 'registrada'}"
        return "Sin actualizaciones registradas · línea base estable"

    if cfg["id"] == "backup":
        backups = ctx["backups"]
        last = backups[0] if backups else {}
        if last:
            state = "verificado" if last.get("integrity") == "verified" else "revisar"
            return f"Backup {last.get('name', '?')} · {state} · {_mb(last.get('bytes_total'))}"
        return "Sin respaldos recientes en el ledger"

    if cfg["id"] == "architect":
        pending = sum(1 for p in ctx["proposals"] if p.get("status") == "pending")
        return f"{pending} propuesta(s) pendiente(s) · {len(ctx['proposals'])} evaluadas"

    if cfg["id"] == "qa":
        score = ctx["qa_score"]
        return f"Calidad {score:.2f} · {ctx['probes_ok']}/{ctx['probes_total']} sondas OK · {ctx['anomaly_count']} anomalías"

    return _short(rec.get("last_summary") or "En guardia", 150)


def _character_state(cfg: dict, ctx: dict, probe_states: list[bool | None]) -> str:
    rec = ctx["records"].get(cfg["agent"], {})
    severity = str(rec.get("last_severity") or "").upper()
    age = _age_seconds(rec.get("last_at"))
    role_anomalies = [
        a for a in ctx["anomalies"]
        if any(hint in str(a).lower() for hint in cfg.get("hint", ()))
    ]
    if severity == "CRIT" or (role_anomalies and severity != "OK"):
        return "alert"
    if False in probe_states:
        return "alert"
    if severity == "WARN" or role_anomalies:
        return "healing"
    active = [t for t in ctx["tasks"]
              if str(t.get("employee") or "") == cfg["agent"]
              and t.get("status") == "in_progress"]
    if active:
        return "working"
    if age is not None and age < 900:
        return "reporting"
    return "idle_guard"


def build_state(sources: dict) -> dict:
    """Convierte las respuestas crudas en el estado vivo de los 9 personajes."""
    ledger = _as_dict(sources["ledger"].get("data"))
    qa_data = _as_dict(sources["qa"].get("data"))
    sentinel = _as_dict(sources["sentinel"].get("data"))
    probes = _as_dict(sentinel.get("probes"))
    anomalies = sentinel.get("anomalies") or []
    repairs = sentinel.get("repairs") or []
    if not isinstance(anomalies, list):
        anomalies = []
    if not isinstance(repairs, list):
        repairs = []

    records = {
        str(item.get("employee")): _as_dict(item)
        for item in (ledger.get("employees") or [])
        if isinstance(item, dict)
    }
    tasks = [t for t in (ledger.get("recent_tasks") or []) if isinstance(t, dict)]
    backups = [b for b in (_as_dict(sources["backup"].get("data")).get("backups") or [])
               if isinstance(b, dict)]
    updates = [u for u in (_as_dict(sources["updater"].get("data")).get("updates") or [])
               if isinstance(u, dict)]
    proposals = [p for p in (_as_dict(sources["proposals"].get("data")).get("proposals") or [])
                 if isinstance(p, dict)]

    def probe_ok(name: str) -> bool | None:
        return _probe_ok(probes, name)

    probe_values = [probe_ok(name) for name in probes]
    probes_total = len(probes)
    probes_ok = sum(1 for value in probe_values if value is True)

    scores = []
    for rec in records.values():
        try:
            scores.append(float(rec.get("avg_quality") or 0))
        except (TypeError, ValueError):
            continue
    fallback_score = round(sum(scores) / len(scores), 3) if scores else 0.0

    ctx: dict[str, Any] = {
        "probes": probes,
        "ledger": ledger,
        "records": records,
        "tasks": tasks,
        "backups": backups,
        "updates": updates,
        "proposals": proposals,
        "anomalies": anomalies,
        "repairs": repairs,
        "anomaly_count": len(anomalies),
        "probes_ok": probes_ok,
        "probes_total": probes_total,
        "in_progress": sum(1 for t in tasks if t.get("status") == "in_progress"),
        "qa_score": fallback_score,
    }

    characters: list[dict[str, Any]] = []
    for cfg in (DIRECTOR, *EMPLOYEES):
        rec = records.get(cfg["agent"], {})
        hints = cfg.get("hint", ())
        probe_states = [probe_ok(name) for name in probes if any(h in name for h in hints)]
        if cfg["id"] == "director":
            probe_states = probe_values
        state = _character_state(cfg, ctx, probe_states)
        try:
            score = round(float(rec.get("avg_quality") or fallback_score), 3)
        except (TypeError, ValueError):
            score = fallback_score
        if cfg["id"] == "qa":
            try:
                qa_score = round(float(qa_data.get("avg_quality") or score), 3)
            except (TypeError, ValueError):
                qa_score = score
            score = qa_score
        characters.append({
            "id": cfg["id"],
            "name": cfg["name"],
            "agent": cfg["agent"],
            "station": cfg["station"],
            "role": cfg["role"],
            "bio": cfg["bio"],
            "state": state,
            "bubble_text": _bubble(cfg, ctx),
            "last_report": _short(rec.get("last_summary") or "", 1800),
            "qa_score": score,
            "last_seen": rec.get("last_at") or sentinel.get("last_cycle") or None,
            "report_count": rec.get("reports") or 0,
            "severity": rec.get("last_severity") or ("CRIT" if state == "alert" else None),
            "active_links": [],
        })

    by_state = {c["id"]: c["state"] for c in characters}
    links: list[dict[str, str]] = []

    def link(src: str, dst: str, kind: str) -> None:
        if src in by_state and dst in by_state:
            links.append({"from": src, "to": dst, "kind": kind})

    for char in characters:
        if char["id"] == "director":
            continue
        state = by_state[char["id"]]
        if state in ("working", "reporting", "healing", "alert"):
            link("director", char["id"], "order" if state in ("working", "reporting") else "escalation")
    if by_state.get("qa") in ("working", "reporting", "alert", "healing"):
        link("qa", "cloud", "audit")
    if any(p.get("status") == "pending" for p in proposals):
        link("architect", "director", "proposal")
    if by_state.get("infra") in ("healing", "alert"):
        link("infra", "secops", "coordination")
    if by_state.get("backup") in ("working", "reporting"):
        link("backup", "director", "report")

    for char in characters:
        char["active_links"] = [
            item for item in links
            if item["from"] == char["id"] or item["to"] == char["id"]
        ]

    last_backup = backups[0] if backups else {}
    return {
        "ok": True,
        "generated_at": _now(),
        "characters": characters,
        "active_links": links,
        "summary": {
            "corp_online": bool(sources["ledger"].get("ok")),
            "sentinel_online": bool(sources["sentinel"].get("ok")),
            "completed_tasks": sum(1 for t in tasks if t.get("status") == "completed"),
            "in_progress_tasks": ctx["in_progress"],
            "last_backup": {
                "name": last_backup.get("name"),
                "created_at": last_backup.get("created_at"),
                "integrity": last_backup.get("integrity"),
                "ok": bool(last_backup.get("ok")),
                "size_mb": _mb(last_backup.get("bytes_total")),
            } if last_backup else None,
            "probes_ok": probes_ok,
            "probes_total": probes_total,
            "anomalies": len(anomalies),
            "pending_proposals": sum(1 for p in proposals if p.get("status") == "pending"),
            "last_cycle": sentinel.get("last_cycle"),
        },
    }


async def get_office_state() -> dict:
    return build_state(await fetch_sources())


async def dispatch_command(action: str, employee: str | None = None) -> dict:
    """Dispara únicamente webhooks predefinidos; nunca URLs arbitrarias."""
    if action == "director_round":
        url = f"{WEBHOOK_BASE}/webhook/director"
        target = "HomeLab-Director"
        payload = {
            "directive": "Ronda general de inspección solicitada desde HomeLab Nexus",
            "requested_by": "nexus-ui",
            "source": "nexus-office",
        }
    elif action == "assign_review":
        cfg = BY_ID.get(str(employee or ""))
        if not cfg or cfg["id"] == "director":
            raise ValueError("Empleado no válido")
        url = f"{WEBHOOK_BASE}/webhook/{cfg['webhook']}"
        target = cfg["agent"]
        payload = {
            "directive": f"Revisión de estado solicitada desde Nexus para {cfg['name']}",
            "requested_by": "nexus-ui",
            "review_only": True,
            "task_id": None,
            "round_id": None,
        }
    else:
        raise ValueError("Acción no válida")

    try:
        async with httpx.AsyncClient(timeout=6.0, verify=False,
                                     follow_redirects=True,
                                     headers={"User-Agent": "HomeLab-Nexus/1.1"}) as client:
            resp = await client.post(url, json=payload)
        return {
            "ok": 200 <= resp.status_code < 400,
            "action": action,
            "target": target,
            "status_code": resp.status_code,
            "response": resp.text[:500],
            "requested_at": _now(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "action": action,
            "target": target,
            "error": type(exc).__name__,
            "requested_at": _now(),
        }

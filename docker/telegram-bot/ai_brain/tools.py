"""Herramientas reales de Function Calling para el HomeLab.

Todas las consultas son de solo lectura salvo `manage_container`, que NUNCA
reinicia directamente: devuelve una petición de confirmación que el bot
presenta con botones inline antes de ejecutar la acción.
"""
import html
import os
import struct

import httpx

SENTINEL = os.environ.get("SENTINEL_URL", "http://127.0.0.1:9090").rstrip("/")
DOCKER_SOCK = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
CORP_DIRECTOR_URL = os.environ.get(
    "CORP_DIRECTOR_URL", "http://192.168.1.4:5678/webhook/director"
).rstrip("/")
CORP_LEDGER_URL = os.environ.get(
    "CORP_LEDGER_URL", "http://192.168.1.4:8006/api/ledger/summary"
).rstrip("/")
CORP_TOKEN = os.environ.get("CORP_INTERNAL_TOKEN", "")
CORP_API_URL = os.environ.get("CORP_API_URL", "http://192.168.1.4:8006").rstrip("/")

# Lista blanca explícita solicitada. El bot aplica la misma lista.
ALLOWED_SERVICES = (
    "jellyfin", "jellyseerr", "seerr-theme", "qbittorrent", "radarr", "sonarr",
    "bazarr", "nextcloud", "immich-server", "immich-microservices", "pihole",
    "unbound", "crowdsec", "threadfin", "homelab-sentinel",
)


def _ok(summary, data=None):
    return {"ok": True, "summary": str(summary), "data": data or {}}


def _err(message):
    return {"ok": False, "summary": f"No pude completar la consulta: {message}", "data": {}}


def _get_json(url, *, timeout=8, method="GET", json_body=None):
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, url, json=json_body)
        response.raise_for_status()
        return response.json()


def _state():
    return _get_json(f"{SENTINEL}/api/state", timeout=12)


def get_cluster_status():
    """CPU, RAM, disco, contenedores y sondas de Sentinel en tiempo real."""
    try:
        state = _state()
        twin = state.get("twin") or {}
        nodes = twin.get("nodes") or {}
        host = nodes.get("host") or {}
        node2 = nodes.get("node2") or {}
        docker = twin.get("docker") or {}
        probes = state.get("probes") or {}
        anomalies = state.get("anomalies") or []

        ssd = host.get("ssd") or {}
        ram = host.get("ram") or {}
        containers = docker.get("containers") or []
        total = int(docker.get("count") or len(containers))
        running = int(docker.get("running") or sum(
            1 for item in containers if item.get("running")
        ))
        statused = [
            (k, v) for k, v in probes.items()
            if isinstance(v, dict) and v.get("status") is not None
        ]
        up = [k for k, v in statused if v.get("status") == "ok"]
        down = [k for k, v in statused if v.get("status") != "ok"]
        n2ram = node2.get("metrics", {}).get("ram") or {}
        lines = [
            f"Nodo 1 (OptiPlex): CPU {host.get('cpu_temp_c', 'n/d')} °C, "
            f"RAM {ram.get('used_pct', 'n/d')}% "
            f"({ram.get('used_mb', 'n/d')}/{ram.get('total_mb', 'n/d')} MB), "
            f"disco /dev/sda1 {ssd.get('used_pct', 'n/d')}% "
            f"({ssd.get('free_gb', 'n/d')} GB libres).",
            f"Contenedores Nodo 1: {running}/{total} activos.",
            f"Nodo 2: {'online' if (node2.get('metrics') or {}).get('online') else 'estado no confirmado'}"
            + (f", RAM {n2ram.get('used_pct', 'n/d')}%" if n2ram else "."),
            f"Sentinel: {len(up)}/{len(statused)} sondas con estado OK "
            f"({len(probes)} sondas en total), "
            f"{len(anomalies)} anomalías" + (f"; fallos: {', '.join(down)}" if down else "."),
        ]
        return _ok("\n".join(lines), {
            "host": host, "node2": node2, "containers_total": total,
            "containers_running": running, "probes_ok": len(up),
            "probes_total": len(probes), "probes_statused": len(statused),
            "anomalies": len(anomalies),
        })
    except Exception as exc:  # noqa: BLE001 - se reporta al modelo
        return _err(str(exc))


def run_sentinel_audit():
    """Dispara POST /api/audit y resume hallazgos y anomalías."""
    try:
        payload = _get_json(
            f"{SENTINEL}/api/audit", timeout=90, method="POST", json_body={}
        )
        audit = payload.get("audit") or {}
        probes = audit.get("probes") or {}
        anomalies = audit.get("anomalies") or []
        handled = audit.get("handled") or []
        statused = [
            (k, v) for k, v in probes.items()
            if isinstance(v, dict) and v.get("status") is not None
        ]
        ok = [k for k, v in statused if v.get("status") == "ok"]
        bad = [k for k, v in statused if v.get("status") != "ok"]
        head = (
            f"Auditoría Sentinel: ok={payload.get('ok')}, "
            f"{len(ok)}/{len(statused)} sondas con estado OK "
            f"({len(probes)} sondas en total), {len(anomalies)} anomalías, "
            f"{len(handled)} acciones ya gestionadas."
        )
        if bad:
            head += " Sondas no OK: " + ", ".join(bad) + "."
        for anomaly in anomalies[:8]:
            head += (
                f" [{anomaly.get('severity', '?')}] "
                f"{anomaly.get('component', '?')}: {anomaly.get('summary', '')}"
            )
        return _ok(head, {
            "ok": payload.get("ok"), "probes": list(probes),
            "anomalies": anomalies, "handled_count": len(handled),
        })
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def _docker_client():
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=DOCKER_SOCK),
        base_url="http://docker", timeout=8,
    )


def _demux(raw: bytes) -> str:
    if not raw:
        return ""
    first = raw[0]
    if first in (0, 1, 2) and len(raw) >= 8:
        out = bytearray()
        pos = 0
        while pos + 8 <= len(raw):
            size = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
            pos += 8
            if size > len(raw) - pos:
                break
            out.extend(raw[pos:pos + size])
            pos += size
        if out:
            return out.decode("utf-8", "ignore")
    return raw.decode("utf-8", "ignore")


def manage_container(service_name, action):
    """logs | health | restart de un servicio permitido.

    `restart` solo devuelve require_confirmation=True; el bot muestra botones
    inline y ejecuta la orden únicamente tras confirmación explícita.
    """
    service = str(service_name or "").strip()
    action = str(action or "health").strip().lower()
    if service not in ALLOWED_SERVICES:
        return _err(f"servicio '{service}' fuera de la lista blanca")
    if action not in ("logs", "health", "restart"):
        return _err(f"acción '{action}' no soportada (usa logs, health o restart)")

    try:
        with _docker_client() as client:
            info = client.get(f"/containers/{service}/json")
            if info.status_code == 404:
                return _err(f"el contenedor '{service}' no existe")
            info.raise_for_status()
            data = info.json()
        state = data.get("State") or {}
        health = (state.get("Health") or {}).get("Status") or "sin healthcheck"

        if action == "health":
            return _ok(
                f"{service}: {state.get('Status', '?')}, health={health}, "
                f"reinicios={data.get('RestartCount', 0)}, "
                f"OOM={bool(state.get('OOMKilled'))}.",
                {"service": service, "status": state.get("Status"),
                 "health": health, "restarts": data.get("RestartCount", 0)},
            )

        if action == "logs":
            with _docker_client() as client:
                response = client.get(
                    f"/containers/{service}/logs",
                    params={"tail": 40, "stdout": True, "stderr": True},
                )
                response.raise_for_status()
            logs = _demux(response.content).strip()[-6000:]
            return _ok(f"Últimos logs de {service}:\n{logs or '(sin salida)'}", {
                "service": service, "logs": logs,
            })

        # restart: confirmación obligatoria en Telegram
        return _ok(
            f"Se solicita reiniciar {service}. Estado actual: "
            f"{state.get('Status', '?')} / {health}. "
            "El bot debe pedir confirmación con botones inline antes de ejecutar.",
            {
                "service": service, "action": "restart",
                "require_confirmation": True,
                "confirmation_callback": f"ai:restart_menu:{service}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def get_security_report():
    """Pi-hole, DNSSEC de Unbound y decisiones/alertas de CrowdSec."""
    try:
        payload = _get_json(f"{SENTINEL}/api/audit", timeout=90, method="POST", json_body={})
        probes = (payload.get("audit") or {}).get("probes") or {}
        dns = probes.get("unbound_dnssec") or {}
        cs = probes.get("crowdsec_ips") or {}
        ph = probes.get("pihole") or {}

        lines = [
            "Pi-hole: "
            + ("filtrado activo, resolución OK" if ph.get("filtering_active") and ph.get("resolve_ok")
               else f"estado {ph.get('status', 'desconocido')}")
            + (f"; bloqueo de prueba {ph.get('blocked_test', {}).get('ips')}" if ph.get("blocked_test") else ""),
            "Unbound DNSSEC: "
            + (f"validación OK (sigfail={dns.get('sigfail', {}).get('rcode')}, "
               f"sigok={dns.get('sigok', {}).get('rcode')})"
               if dns.get("dnssec_ok") else f"validación NO OK ({dns.get('status', '?')})"),
            "CrowdSec: "
            + f"{cs.get('decisions', '?')} decisiones activas, {cs.get('alerts', '?')} alertas, "
            + f"bouncer {'OK' if cs.get('bouncer_ok') else 'sin pull'}, "
            + f"whitelist {'cargada' if cs.get('whitelist_loaded') else 'NO cargada'}.",
        ]
        # Métricas directas de Pi-hole v6 si hay password disponible
        password = os.environ.get("PIHOLE_PASSWORD", "")
        if password:
            try:
                with httpx.Client(base_url="http://127.0.0.1", timeout=5) as client:
                    auth = client.post("/api/auth", json={"password": password})
                    sid = (auth.json().get("session") or {}).get("sid")
                    if sid:
                        client.headers["X-FTL-SID"] = sid
                        summary = client.get("/api/stats/summary").json()
                        queries = summary.get("queries") or {}
                        lines.append(
                            f"Consultas Pi-hole: {queries.get('total', '?')} totales, "
                            f"{queries.get('blocked', '?')} bloqueadas "
                            f"({queries.get('percent_blocked', '?')}%)."
                        )
            except Exception as exc:  # noqa: BLE001
                lines.append(f"(métricas directas Pi-hole no disponibles: {exc})")
        return _ok("\n".join(lines), {"pihole": ph, "dnssec": dns, "crowdsec": cs})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def get_media_activity():
    """Descargas activas (qBittorrent) y cola/reciente de Radarr, Sonarr y Jellyfin."""
    lines = []
    data = {}

    try:
        with httpx.Client(base_url="http://127.0.0.1:8081", timeout=6) as client:
            login = client.post(
                "/api/v2/auth/login",
                data={
                    "username": os.environ.get("QBITTORRENT_USER", ""),
                    "password": os.environ.get("QBITTORRENT_PASS", ""),
                },
                headers={
                    "Referer": "http://127.0.0.1:8081",
                    "Origin": "http://127.0.0.1:8081",
                },
            )
            if login.status_code < 400 and "fails" not in login.text.lower():
                torrents = client.get("/api/v2/torrents/info").json()
                active = [t for t in torrents if t.get("state") in ("downloading", "uploading", "stalledDL", "stalledUP", "queuedDL")]
                lines.append(
                    f"qBittorrent: {len(active)} activos de {len(torrents)} torrents."
                )
                for torrent in active[:6]:
                    lines.append(
                        f"  • {torrent.get('name', '?')} "
                        f"({float(torrent.get('progress', 0)) * 100:.0f}%, {torrent.get('state', '?')})"
                    )
                data["torrents"] = len(torrents)
            else:
                lines.append("qBittorrent: no se pudo autenticar.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"qBittorrent: no disponible ({exc}).")

    for label, port, env_key in (
        ("Radarr", 7878, "RADARR_API_KEY"), ("Sonarr", 8989, "SONARR_API_KEY")
    ):
        key = os.environ.get(env_key, "")
        if not key:
            lines.append(f"{label}: sin API key en .env.")
            continue
        try:
            queue = _get_json(
                f"http://127.0.0.1:{port}/api/v3/queue?page=1&pageSize=10&apikey={key}",
                timeout=6,
            )
            total = queue.get("totalRecords", len(queue.get("records") or []))
            lines.append(f"{label}: {total} elementos en cola.")
            for record in (queue.get("records") or [])[:4]:
                lines.append(f"  • {record.get('title', '?')}")
            data[label.lower()] = total
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{label}: no disponible ({exc}).")

    jelly_key = os.environ.get("JELLYFIN_API_KEY", "")
    if jelly_key:
        try:
            with httpx.Client(timeout=6) as client:
                recent = client.get(
                    "http://127.0.0.1:8096/Items",
                    params={"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": 5},
                    headers={"Authorization": f'MediaBrowser Token="{jelly_key}"'},
                ).json()
            items = recent.get("Items") or []
            lines.append(f"Jellyfin: {len(items)} incorporaciones recientes.")
            for item in items[:4]:
                lines.append(f"  • {item.get('Name', '?')}")
            data["jellyfin_recent"] = len(items)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Jellyfin: no disponible ({exc}).")
    else:
        lines.append("Jellyfin: sin API key en .env.")

    return _ok("\n".join(lines) if lines else "Sin actividad disponible.", data)


def _esc(value):
    return html.escape(str(value or ""), quote=False)


def _corp_headers():
    return {"X-Corp-Token": CORP_TOKEN} if CORP_TOKEN else {}


def _corp_extras():
    """Historial de backup/updates y propuestas pendientes de Agent-Architect."""
    result = {"backups": [], "updates": [], "proposals": []}
    for key, path in (
        ("backups", "/api/tools/backup/history"),
        ("updates", "/api/tools/updater/history"),
        ("proposals", "/api/ledger/proposals"),
    ):
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                response = client.get(CORP_API_URL + path, headers=_corp_headers())
                if response.status_code < 400:
                    body = response.json()
                    result[key] = body.get(key) or []
        except Exception:  # noqa: BLE001
            continue
    return result


def consult_homelab_director(directive):
    """Envía una directiva al HomeLab-Director y muestra la rendición de cuentas.

    El Director coordina a 8 empleados (7 operativos + Agent-QA). Esta
    herramienta NUNCA reinicia contenedores ni borra datos.
    """
    directive = str(directive or "").strip()
    if not directive:
        directive = "Entrega un reporte de rendición de cuentas de los 8 empleados"
    try:
        with httpx.Client(timeout=240, follow_redirects=True) as client:
            launched = client.post(
                CORP_DIRECTOR_URL,
                json={"directive": directive, "requested_by": "telegram"},
            )
            if launched.status_code >= 400:
                return _err(f"Director respondió HTTP {launched.status_code}")
            report = launched.json() if "json" in launched.headers.get("content-type", "") else {}
            ledger = client.get(CORP_LEDGER_URL, headers=_corp_headers())
            ledger.raise_for_status()
            payload = ledger.json()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    employees = payload.get("employees") or []
    extras = _corp_extras()
    lines = [f"Directiva enviada al HomeLab-Director: «{directive}»."]
    narrative = str(report.get("report") or "").strip()
    if narrative:
        lines.append(narrative[:2200])
    lines.append("Empleados (8):")
    for item in employees:
        lines.append(
            f"• {item.get('employee')}: {item.get('reports', 0)} informes, "
            f"calidad media {item.get('avg_quality', 'n/d')}, "
            f"último estado {item.get('last_severity') or 'sin datos'}."
        )
    if extras["backups"]:
        last = extras["backups"][0]
        lines.append(
            f"Último respaldo: {last.get('name')} · integridad {last.get('integrity')} · "
            f"{last.get('bytes_total')} bytes."
        )
    if extras["updates"]:
        last = extras["updates"][0]
        lines.append(
            f"Última actualización: {last.get('service')} · rollback "
            f"{'sí' if last.get('rolled_back') else 'no'} · ok {last.get('ok')}."
        )
    pending = [p for p in extras["proposals"] if p.get("status") == "pending"]
    if pending:
        lines.append(f"Propuestas de Agent-Architect pendientes: {len(pending)}.")
    tasks = payload.get("recent_tasks") or []
    if tasks:
        lines.append(f"Última orden: #{tasks[0].get('id')} · {tasks[0].get('status')}.")
    return _ok("\n".join(lines), {
        "launched": launched.status_code,
        "director_report": narrative,
        "round_id": report.get("round_id"),
        "employees": employees,
        "recent_tasks": tasks[:3],
        "recent_reports": (payload.get("recent_reports") or [])[:5],
        **extras,
    })


def get_corp_accountability():
    """Libro de rendición de cuentas de los 8 empleados + autonomía (solo lectura)."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(CORP_LEDGER_URL, headers=_corp_headers())
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    employees = payload.get("employees") or []
    extras = _corp_extras()
    lines = ["📊 <b>HomeLab Corp — rendición de cuentas (8 empleados)</b>"]
    for item in employees:
        lines.append(
            f"• {item.get('employee')}: {item.get('reports', 0)} informes · "
            f"calidad {item.get('avg_quality', 'n/d')} · "
            f"último {item.get('last_severity') or 'sin datos'}"
        )
    if extras["backups"]:
        last = extras["backups"][0]
        lines.append(
            f"💾 Último respaldo: <b>{_esc(last.get('name'))}</b> · "
            f"{last.get('integrity')} · {last.get('bytes_total')} bytes"
        )
    if extras["updates"]:
        last = extras["updates"][0]
        lines.append(
            f"🔄 Última actualización: <b>{_esc(last.get('service'))}</b> · "
            f"rollback {'sí' if last.get('rolled_back') else 'no'}"
        )
    pending = [p for p in extras["proposals"] if p.get("status") == "pending"]
    if pending:
        lines.append(f"🚀 Propuestas pendientes de Agent-Architect: {len(pending)}")
        for proposal in pending[:3]:
            lines.append(f"   #{proposal.get('id')} · {_esc(proposal.get('title'))}")
    tasks = payload.get("recent_tasks") or []
    if tasks:
        lines.append(f"Órdenes recientes: {len(tasks)} · última #{tasks[0].get('id')}.")
    return _ok("\n".join(lines), {**payload, **extras})


def apply_corp_proposal(proposal_id):
    """Aplica una propuesta aprobada por el usuario (nunca borra datos)."""
    try:
        proposal_id = int(proposal_id)
    except (TypeError, ValueError):
        return _err("ID de propuesta inválido")
    try:
        with httpx.Client(timeout=90, follow_redirects=True) as client:
            response = client.post(
                f"{CORP_API_URL}/api/ledger/proposal/{proposal_id}/apply",
                headers=_corp_headers(),
            )
            if response.status_code >= 400:
                return _err(f"API respondió HTTP {response.status_code}: {response.text[:200]}")
            body = response.json()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    return _ok(
        f"🚀 Propuesta <b>#{proposal_id}</b> aplicada: {_esc(body.get('status') or body.get('result') or 'ok')}.",
        body,
    )


def apply_github_change(directive):
    """Pide al HomeLab-Director un cambio versionado y devuelve el hash del commit.

    El Director delega en `/api/tools/repo/apply-change`, que aplica guardrails
    físicos, valida, verifica Sentinel y hace commit/push con rollback.
    """
    directive = str(directive or "").strip()
    if len(directive) < 5:
        return _err("falta la instrucción de cambio")
    try:
        with httpx.Client(timeout=420, follow_redirects=True) as client:
            response = client.post(
                CORP_DIRECTOR_URL,
                json={
                    "directive": directive,
                    "mode": "code_change",
                    "requested_by": "telegram_user",
                    "notify": False,
                },
            )
            if response.status_code >= 400:
                return _err(f"Director respondió HTTP {response.status_code}")
            body = response.json() if "json" in response.headers.get("content-type", "") else {}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    if body.get("ok") is False:
        return _err(str(body.get("error") or body.get("report") or "cambio rechazado")[:500])
    gitops = body.get("gitops") or {}
    commit = str(gitops.get("commit_hash") or body.get("commit_hash") or "sin hash")
    rollback = bool(gitops.get("rollback"))
    summary = (
        ("⚠️ Cambio revertido por rollback automático. " if rollback else
         "✅ Cambio aplicado y subido a origin/main. ")
        + f"Commit <code>{_esc(commit)}</code>. "
        + f"Sondas Sentinel: {_esc(str(gitops.get('probes_total') or 'n/d'))}; "
        + f"anomalías nuevas: {_esc(str(gitops.get('anomaly_count') or 0))}. "
        + _esc(str(body.get("summary") or body.get("report") or "")[:900])
    )
    return _ok(summary, body)


DISPATCH = {
    "apply_github_change": lambda **kwargs: apply_github_change(
        kwargs.get("directive", "")
    ),
    "consult_homelab_director": lambda **kwargs: consult_homelab_director(
        kwargs.get("directive", "")
    ),
    "get_corp_accountability": lambda **_: get_corp_accountability(),
    "apply_corp_proposal": lambda proposal_id: apply_corp_proposal(proposal_id),
    "get_cluster_status": lambda **_: get_cluster_status(),
    "run_sentinel_audit": lambda **_: run_sentinel_audit(),
    "manage_container": lambda service_name, action: manage_container(service_name, action),
    "get_security_report": lambda **_: get_security_report(),
    "get_media_activity": lambda **_: get_media_activity(),
}


def dispatch(name, arguments):
    fn = DISPATCH.get(str(name))
    if not fn:
        return _err(f"herramienta desconocida: {name}")
    try:
        return fn(**(arguments or {}))
    except TypeError as exc:
        return _err(f"argumentos inválidos para {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

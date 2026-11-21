"""Acciones operativas seguras para el motor de autonomía de HomeLab Corp.

Este router sólo es alcanzable desde corp-tools-api (Nodo 2) mediante un token
compartido. Todas las acciones están en lista blanca, se registran en el libro
de reparaciones de Sentinel y NUNCA tocan /media, Nextcloud data ni Immich data.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time

from fastapi import APIRouter, Header, HTTPException

from .config import settings
from .db import add_repair
from .twin_engine import docker_client

router = APIRouter()

# Servicios oficiales sobre los que se permite actuar. Ninguno almacena datos
# personales: sólo contenedores de plataforma, red, seguridad y multimedia
# (aplicación, nunca bibliotecas).
SAFE_SERVICES = {
    "jellyfin", "jellyseerr", "seerr-theme", "qbittorrent", "radarr", "sonarr",
    "bazarr", "prowlarr", "flaresolverr", "nextcloud", "pihole", "unbound",
    "crowdsec", "threadfin", "homelab-sentinel", "optiplex-chatops",
    "homelab-nexus", "immich-server", "immich-postgres", "immich-redis",
    "corp-tools-api", "homelab-corp-n8n", "immich-machine-learning",
}

# Imágenes con pin explícito: el actualizador jamás las re-despliega.
PINNED_IMAGES = ("postgres", "redis", "immich-server", "immich-postgres",
                 "immich-redis", "valkey")

CORP_SCRIPT = "/home/mrdavid/HomeLab/scripts/backup_corp_criticos.sh"


def _require_token(x_corp_token: str = Header(default="")) -> None:
    expected = settings.corp_token
    if not expected or x_corp_token != expected:
        raise HTTPException(status_code=401, detail="X-Corp-Token inválido")


def _host(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """Ejecuta en el namespace del host (Sentinel corre privilegiado, pid host)."""
    return subprocess.run(
        ["nsenter", "-t", "1", "-m", "-u", "-n", "-i", "--"] + cmd,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _host_sh(script: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return _host(["bash", "-lc", script], timeout=timeout)


def _wait_healthy(name: str, timeout: int = 45) -> tuple[bool, dict]:
    cli = docker_client()
    if not cli:
        return False, {"error": "docker unavailable"}
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            c = cli.containers.get(name)
            c.reload()
            state = c.attrs.get("State") or {}
            health = ((state.get("Health") or {}).get("Status")) or "none"
            last = {
                "status": state.get("Status"), "running": bool(state.get("Running")),
                "health": health, "restarts": c.attrs.get("RestartCount"),
                "oom": bool(state.get("OOMKilled")),
            }
            if state.get("Running") and health in ("healthy", "none"):
                return True, last
        except Exception as exc:  # noqa: BLE001
            last = {"error": str(exc)}
        time.sleep(2)
    return False, last


def _inspect(name: str) -> dict:
    cli = docker_client()
    if not cli:
        return {"ok": False, "error": "docker unavailable"}
    try:
        c = cli.containers.get(name)
        attrs = c.attrs
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        state = attrs.get("State") or {}
        image = (attrs.get("Config") or {}).get("Image") or ""
        digest = (attrs.get("RepoDigests") or [None])[0]
        if not digest and image:
            # Contenedores creados desde una tag local pueden no traer RepoDigests;
            # el digest de la imagen en el host es la referencia de rollback.
            try:
                ref = _host(["docker", "image", "inspect", image,
                             "--format", "{{index .RepoDigests 0}}"], timeout=20)
                candidate = (ref.stdout or "").strip()
                if candidate.startswith("sha256:") or "@sha256:" in candidate:
                    digest = candidate
            except Exception:  # noqa: BLE001
                pass
        return {
            "ok": True, "name": name,
            "status": state.get("Status"), "running": bool(state.get("Running")),
            "health": ((state.get("Health") or {}).get("Status")) or "none",
            "image": image,
            "digest": digest,
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
            "config_files": labels.get("com.docker.compose.project.config_files"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _restart(name: str) -> tuple[bool, dict]:
    if name not in SAFE_SERVICES:
        return False, {"error": f"servicio no permitido: {name}"}
    info = _inspect(name)
    if not info.get("ok"):
        return False, info
    try:
        cli = docker_client()
        cli.containers.get(name).restart(timeout=20)
    except Exception as exc:  # noqa: BLE001
        return False, {"error": str(exc)}
    ok, after = _wait_healthy(name, timeout=30)
    return ok, {"before": info, "after": after}


def _disk_used_pct() -> int:
    try:
        result = _host_sh("df -P / | awk 'NR==2 {gsub(/%/,\"\",$5); print $5}'", timeout=20)
        return int((result.stdout or "0").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return 0


def _clean_build_cache(threshold: int = 88) -> tuple[bool, dict]:
    used = _disk_used_pct()
    if used < threshold:
        return True, {"skipped": True, "used_pct": used, "threshold": threshold}
    result = _host_sh("docker builder prune -f", timeout=180)
    after = _disk_used_pct()
    return result.returncode == 0, {
        "skipped": False, "used_pct_before": used, "used_pct_after": after,
        "output": (result.stdout or result.stderr or "").strip()[-800:],
    }


def _update(name: str) -> dict:
    """Actualización con digest guardado y rollback automático.

    Utiliza el compose original del contenedor (labels de Compose), sin
    recrear configuración a mano. Si el healthcheck falla en 45 s, re-etiqueta
    el digest anterior y fuerza el recreado.
    """
    if name not in SAFE_SERVICES:
        return {"ok": False, "error": f"servicio no permitido: {name}"}
    info = _inspect(name)
    if not info.get("ok"):
        return info
    image = info.get("image") or ""
    if any(token in image.lower() for token in PINNED_IMAGES):
        return {"ok": False, "skipped": True, "reason": f"imagen con pin explícito: {image}",
                "digest_before": info.get("digest")}
    if not (info.get("working_dir") and info.get("config_files") and info.get("project")
            and info.get("service")):
        return {"ok": False, "error": "el contenedor no tiene labels de Compose utilizables"}

    wd = shlex.quote(info["working_dir"])
    cfg = shlex.quote(info["config_files"])
    project = shlex.quote(info["project"])
    service = shlex.quote(info["service"])
    base = f"cd {wd} && docker compose -f {cfg} -p {project}"
    old_digest = info.get("digest")

    result = _host_sh(f"{base} up -d --no-deps --pull always {service}", timeout=240)
    if result.returncode != 0:
        return {"ok": False, "stage": "pull_up",
                "error": (result.stderr or result.stdout or "").strip()[-1000:],
                "digest_before": old_digest}

    after_info = _inspect(name)
    new_digest = after_info.get("digest")
    if old_digest and new_digest == old_digest:
        return {"ok": True, "updated": False, "reason": "sin cambio de digest remoto",
                "digest_before": old_digest, "digest_after": new_digest}

    healthy, state = _wait_healthy(name, timeout=45)
    if healthy:
        return {"ok": True, "updated": True, "digest_before": old_digest,
                "digest_after": new_digest, "health": state}

    # Rollback inmediato al digest guardado.
    rollback = {"ok": False, "updated": True, "rolled_back": False,
                "digest_before": old_digest, "digest_after": new_digest,
                "health_failed": state}
    if not old_digest:
        rollback["error"] = "no había digest previo guardado; no se puede revertir"
        return rollback
    commands = [
        f"docker tag {shlex.quote(old_digest)} {shlex.quote(image)}",
        f"{base} up -d --no-deps --force-recreate {service}",
    ]
    rb = _host_sh(" && ".join(commands), timeout=240)
    rb_ok, rb_state = _wait_healthy(name, timeout=30)
    rollback.update({
        "ok": rb_ok and rb.returncode == 0,
        "rolled_back": rb.returncode == 0,
        "health_after_rollback": rb_state,
        "rollback_output": (rb.stderr or rb.stdout or "").strip()[-1000:],
    })
    return rollback


@router.post("/api/corp/action")
async def corp_action(body: dict, x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    action = str(body.get("action") or "")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    name = str(params.get("service") or params.get("container") or "")

    if action in ("restart_container", "reload_unbound", "reload_crowdsec"):
        target = {"reload_unbound": "unbound", "reload_crowdsec": "crowdsec"}.get(action, name)
        ok, detail = _restart(target)
    elif action == "inspect_service":
        detail = _inspect(name)
        ok = bool(detail.get("ok"))
    elif action == "clean_build_cache":
        ok, detail = _clean_build_cache(int(params.get("threshold") or 88))
    elif action == "update_container":
        detail = _update(name)
        ok = bool(detail.get("ok"))
    elif action == "backup_criticos":
        # La comprobación debe hacerse en el namespace del host: Sentinel no ve
        # /home/mrdavid directamente dentro de su propio filesystem.
        try:
            exists = _host(["test", "-f", CORP_SCRIPT], timeout=20).returncode == 0
        except Exception:  # noqa: BLE001
            exists = False
        if not exists:
            ok, detail = False, {"error": f"script no encontrado en el host: {CORP_SCRIPT}"}
        else:
            try:
                result = _host(["bash", CORP_SCRIPT], timeout=300)
                payload = None
                for line in reversed((result.stdout or "").splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            payload = json.loads(line)
                            break
                        except ValueError:
                            continue
                ok = result.returncode == 0 and bool(payload and payload.get("ok"))
                detail = payload or {
                    "error": (result.stderr or result.stdout or "").strip()[-1500:],
                }
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, {"error": str(exc)}
    else:
        raise HTTPException(status_code=400, detail=f"acción no soportada: {action}")

    try:
        add_repair(name or action, action, 1, "ok" if ok else "failed",
                   {"detail": detail, "source": "corp-tools-api"}, None)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": ok, "action": action, "detail": detail,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

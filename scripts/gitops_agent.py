#!/usr/bin/env python3
"""Agente GitOps de HomeLab (se ejecuta en Nodo 1, dentro del repo).

Recibe un JSON por stdin y devuelve un único JSON por stdout. Es la capa de
ejecución con guardrails físicos: NUNCA confía en el prompt del modelo para
decidir qué se versiona. El orquestador (corp-tools-api, Nodo 2) llega a este
script únicamente por SSH con una llave dedicada.

Operaciones:
  inspect  -> lista/lee archivos permitidos del repositorio
  apply    -> respaldo, validación, anti-secretos, commit, push y rollback
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(os.getenv("GITOPS_REPO_PATH", "/home/mrdavid/HomeLab")).resolve()
BACKUP_ROOT = Path(
    os.getenv("GITOPS_BACKUP_ROOT", "/home/mrdavid/backups-compose/auto_gitops")
)
SENTINEL_URL = os.getenv("GITOPS_SENTINEL_URL", "http://127.0.0.1:9090")
REMOTE = "origin"
BRANCH = "main"

# --------------------------------------------------------------------------- #
# Blindaje: nada de esto se decide en un prompt.
# --------------------------------------------------------------------------- #
ALLOWED_PREFIXES = (
    "docker/", "scripts/", "README.md", "GEMINI.md", "AGENTS.md", ".github/",
)
ALLOWED_SUFFIXES = (
    ".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".md", ".sh", ".css",
    ".html", ".xml", ".toml", ".service", ".conf", ".example",
)
BLOCKED_NAMES = {
    ".env", ".env.example.local", "credentials.json", "id_rsa", "id_ed25519",
    "known_hosts", "authorized_keys", "git-credentials",
}
BLOCKED_PARTS = ("/data/", "/database/", "/library/", "/backups", "/node_modules/",
                 "/__pycache__/", "/.git/", "/media/", "/logs/", "/temp/")
BLOCKED_SUFFIXES = (".env", ".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm",
                    ".key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".p8",
                    ".log", ".pid", ".sqlite-journal")
# La firma se construye troceada para que el propio guardrail no se detecte a sí mismo.
SECRET_SIGNATURES = (
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "-----BEGIN " + "EC PRIVATE KEY-----",
    "-----BEGIN " + "PRIVATE KEY-----",
    "ghp_" + "A",
    "github_pat_" + "A",
    "AIza" + "A",
    "sk-proj-" + "A",
    "xoxb-" + "A",
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
    r"password|passwd|passphrase|private[_-]?key|client[_-]?secret)\b"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"
)
BEARER_VALUE = re.compile(r"(?i)\b(?:bearer|authorization)\s+[A-Za-z0-9_\-./+=]{20,}")
PROTECTED_PATHS = {
    "scripts/gitops_agent.py",
    "docker/ai-corp/corp-tools-api/gitops.py",
    "docker/ai-corp/corp-tools-api/app.py",
    "docker/ai-corp/corp-tools-api/autonomy.py",
}

SERVICE_MAP = {
    "docker/nexus/": ("docker/nexus", "nexus", "homelab-nexus"),
    "docker/sentinel/": ("docker/sentinel", "sentinel", "homelab-sentinel"),
    "docker/telegram-bot/": ("docker/telegram-bot", "bot", "optiplex-chatops"),
    "docker/media-stack/": ("docker/media-stack", "seerr-theme", "seerr-theme"),
    "docker/pihole/": ("docker/pihole", "pihole", "pihole"),
    "docker/security/": ("docker/security", "crowdsec", "crowdsec"),
    "docker/nextcloud/": ("docker/nextcloud", "nextcloud", "nextcloud"),
    "docker/threadfin/": ("docker/threadfin", "threadfin", "threadfin"),
    "docker/jellyfin/": ("docker/jellyfin", "jellyfin", "jellyfin"),
    "docker/immich/": ("docker/immich", "immich-server", "immich-server"),
}
# Nunca reiniciar corp-tools-api desde dentro de su propia petición.
NO_AUTO_RESTART = ("docker/ai-corp/",)


def out(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def log(message: str) -> None:
    sys.stderr.write(f"[gitops-agent] {message}\n")
    sys.stderr.flush()


def run(cmd: list[str], *, timeout: int = 120, stdin_data: str | None = None,
        extra_env: dict | None = None) -> dict:
    started = time.time()
    try:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            cmd, cwd=str(REPO), input=stdin_data, text=True, env=env,
            capture_output=True, timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            # rstrip (no strip): conserva la columna de estado de `git status --porcelain`.
            "out": (proc.stdout or "").rstrip(),
            "err": (proc.stderr or "").strip()[:3000],
            "ms": round((time.time() - started) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "out": "", "err": f"timeout {timeout}s", "ms": timeout * 1000}


def git(*args: str, timeout: int = 60, stdin_data: str | None = None) -> dict:
    return run(["git", *args], timeout=timeout, stdin_data=stdin_data)


def safe_path(value: str) -> str:
    path = str(value or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ".." in path.split("/") or "\x00" in path:
        raise ValueError("ruta no permitida")
    while path.startswith("./"):
        path = path[2:]
    if not path.startswith(ALLOWED_PREFIXES) or not path.endswith(ALLOWED_SUFFIXES):
        raise ValueError("ruta fuera del allowlist")
    parts = f"/{path}/".lower()
    if any(blocked in parts for blocked in BLOCKED_PARTS):
        raise ValueError("ruta sensible")
    name = path.rsplit("/", 1)[-1].lower()
    if name in BLOCKED_NAMES or name.endswith(BLOCKED_SUFFIXES):
        raise ValueError("nombre sensible")
    return path


def scan_content(path: str, content: str) -> list[str]:
    findings = []
    for signature in SECRET_SIGNATURES:
        if signature.lower() in content.lower():
            findings.append(f"firma sensible en {path}")
            break
    if SECRET_ASSIGNMENT.search(content) or BEARER_VALUE.search(content):
        findings.append(f"posible token/contraseña en {path}")
    return findings


def _audit_once() -> dict:
    """Una pasada de la auditoría real de Sentinel (15 sondas)."""
    try:
        import urllib.request

        request = urllib.request.Request(
            f"{SENTINEL_URL}/api/audit", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=150) as response:
            payload = json.loads(response.read().decode("utf-8"))
        body = payload.get("audit") if isinstance(payload, dict) else None
        body = body if isinstance(body, dict) else (payload if isinstance(payload, dict) else {})
        probes = body.get("probes") or {}
        anomalies = body.get("anomalies") or []
        bad = []
        for key, value in probes.items():
            status = str((value or {}).get("status", "")).lower() if isinstance(value, dict) else ""
            if status and status not in {"ok", "healthy", "up"}:
                bad.append(f"{key}({status})")
        fingerprints = sorted(
            hashlib.sha256(
                json.dumps(a, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()[:12]
            for a in anomalies
        )
        return {
            "ok": bool(payload.get("ok")) and not bad,
            "sentinel_ok": bool(payload.get("ok")),
            "probes_total": len(probes),
            "probes_bad": bad,
            "anomalies": fingerprints,
            "anomalies_count": len(anomalies),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "sentinel_ok": False,
                "probes_total": 0, "probes_bad": [], "anomalies": [], "anomalies_count": -1}


def audit() -> dict:
    """Auditoría con un reintento breve: tolera reinicios transitorios de n8n/API."""
    result = _audit_once()
    if not result.get("ok"):
        time.sleep(5)
        result = _audit_once()
    return result


def file_tree(limit: int = 2000) -> list[str]:
    found = []
    for root, dirs, files in os.walk(REPO):
        rel_root = os.path.relpath(root, REPO).replace(os.sep, "/")
        dirs[:] = [
            d for d in dirs
            if d not in {".git", "data", "database", "library", "node_modules",
                         "__pycache__", "temp", "logs", "backups"}
            and not d.startswith(".")
        ]
        for name in files:
            rel = f"{rel_root}/{name}" if rel_root != "." else name
            try:
                safe_path(rel)
                found.append(rel)
            except ValueError:
                continue
            if len(found) >= limit:
                return sorted(found)
    return sorted(found)


def do_inspect(request: dict) -> dict:
    path = request.get("path")
    if path:
        rel = safe_path(str(path))
        target = REPO / rel
        if not target.is_file():
            raise ValueError("archivo inexistente")
        raw = target.read_bytes()
        if len(raw) > 160_000:
            raise ValueError("archivo demasiado grande para lectura")
        return {"ok": True, "path": rel, "content": raw.decode("utf-8", "replace"),
                "bytes": len(raw)}
    return {"ok": True, "root": str(REPO), "files": file_tree(),
            "head": git("rev-parse", "HEAD").get("out", "")[:64],
            "dirty": bool(git("status", "--porcelain=v1").get("out", ""))}


def validate(path: str) -> dict:
    target = REPO / path
    if not target.is_file():
        return {"ok": False, "error": "archivo no existe tras aplicar"}
    suffix = target.suffix.lower()
    if suffix in {".yml", ".yaml"}:
        if target.name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            result = run(["docker", "compose", "-f", path, "config", "-q"], timeout=120)
            if result["ok"]:
                return {"ok": True, "checker": "docker compose config"}
        result = run(["python3", "-c",
                      "import sys,yaml;yaml.safe_load(open(sys.argv[1],encoding='utf-8'))", path],
                     timeout=40)
        return {**result, "checker": "yaml.safe_load"}
    if suffix == ".py":
        # PYTHONPYCACHEPREFIX evita que py_compile ensucie el árbol Git.
        return {**run(["python3", "-m", "py_compile", path], timeout=60,
                      extra_env={"PYTHONPYCACHEPREFIX": "/tmp/homelab-gitops-pyc"}),
                "checker": "py_compile"}
    if suffix in {".js", ".mjs"}:
        content = target.read_text(encoding="utf-8", errors="replace")
        if re.match(r"^\s*(?:import|export)\s", content):
            result = run(["node", "--input-type=module", "--check"], timeout=40,
                         stdin_data=content)
        else:
            result = run(["node", "--check", path], timeout=40)
        return {**result, "checker": "node --check"}
    if suffix == ".json":
        return {**run(["python3", "-m", "json.tool", path], timeout=40),
                "checker": "json.tool"}
    if suffix == ".sh":
        return {**run(["bash", "-n", path], timeout=40), "checker": "bash -n"}
    return {"ok": True, "checker": "not-executable"}


def restart_service(paths: list[str]) -> dict:
    for prefix, mapping in SERVICE_MAP.items():
        if any(p.startswith(prefix) for p in paths):
            compose_dir, service, container = mapping
            log(f"recreando servicio {service} ({compose_dir})")
            result = run(["docker", "compose", "-f", f"{compose_dir}/docker-compose.yml",
                          "up", "-d", "--build", "--no-deps", service], timeout=420)
            time.sleep(6)
            inspect = run(["docker", "inspect", "--format",
                           "{{.State.Health.Status}}|{{.State.Running}}", container], timeout=30)
            healthy = inspect["ok"] and not inspect["out"].startswith("unhealthy") \
                and "false" not in inspect["out"]
            return {"ok": result["ok"] and healthy, "service": service,
                    "container": container, "compose_dir": compose_dir,
                    "compose": result, "health": inspect["out"] or "sin healthcheck"}
    return {"ok": True, "service": None, "detail": "sin servicio asociado"}


def restore(backup_dir: Path, paths: list[str], restart: dict) -> dict:
    restored, errors = [], []
    for rel in paths:
        src = backup_dir / rel
        dst = REPO / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            restored.append(rel)
        else:
            try:
                (REPO / rel).unlink()
            except FileNotFoundError:
                pass
    checkout = git("checkout", "--", ".", timeout=60)
    redo = {"ok": True, "detail": "sin servicio"}
    if restart.get("service"):
        compose_dir = restart.get("compose_dir")
        service = restart.get("service")
        if compose_dir:
            redo = run(["docker", "compose", "-f", f"{compose_dir}/docker-compose.yml",
                        "up", "-d", "--build", "--no-deps", service], timeout=420)
    return {"ok": checkout["ok"] and redo["ok"], "restored": restored,
            "checkout": checkout.get("err", "")[:300], "redo": redo.get("err", "")[:300]}


def do_apply(request: dict) -> dict:
    requested_by = str(request.get("requested_by") or "director")
    if requested_by not in {"director", "updater", "telegram_user"}:
        raise ValueError("requested_by inválido")
    files = request.get("files")
    if not isinstance(files, list) or not files or len(files) > 8:
        raise ValueError("plan de archivos inválido")

    # 1) El árbol debe estar limpio: nunca pisamos trabajo pendiente.
    status = git("status", "--porcelain=v1").get("out", "")
    if status:
        return {"ok": False, "error": "repositorio con cambios pendientes", "status": status[:2000]}

    before = git("rev-parse", "HEAD")
    if not before["ok"]:
        return {"ok": False, "error": "no se pudo leer HEAD"}
    before_hash = before["out"][:64]
    baseline = audit()
    log(f"auditoría previa: {baseline}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    directive_hash = hashlib.sha256(
        str(request.get("directive") or "").encode("utf-8")
    ).hexdigest()[:16]
    backup_dir = BACKUP_ROOT / f"{stamp}_{directive_hash}"
    changed: list[str] = []
    findings: list[str] = []

    # 2) Validación y anti-secretos ANTES de escribir nada.
    for item in files:
        rel = safe_path(str(item.get("path") or ""))
        if rel in PROTECTED_PATHS:
            raise ValueError("archivo de guardrail protegido")
        mode = str(item.get("mode") or "replace")
        if mode not in {"append", "replace", "replace_exact"}:
            raise ValueError("modo inválido")
        target = REPO / rel
        original = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if mode == "append":
            new_content = original + ("\n" if original and not original.endswith("\n") else "") \
                + str(item.get("content") or "")
        elif mode == "replace":
            new_content = str(item.get("content") or "")
        else:
            needle = str(item.get("find") or "")
            if not needle or needle not in original:
                raise ValueError(f"texto a reemplazar no encontrado en {rel}")
            if original.count(needle) > 1 and not item.get("replace_all"):
                raise ValueError(f"texto ambiguo en {rel}")
            new_content = original.replace(needle, str(item.get("replacement") or ""))
            if item.get("replace_all"):
                new_content = original.replace(needle, str(item.get("replacement") or ""))
        if len(new_content.encode("utf-8")) > 180_000:
            raise ValueError(f"archivo demasiado grande: {rel}")
        if not new_content.strip():
            raise ValueError(f"contenido vacío: {rel}")
        if original and len(new_content) < len(original) * 0.4 and mode != "replace_exact":
            raise ValueError(f"el cambio reduciría demasiado {rel}")
        findings.extend(scan_content(rel, new_content))
        changed.append(rel)
        item["_new_content"] = base64.b64encode(new_content.encode("utf-8")).decode("ascii")

    if findings:
        return {"ok": False, "blocked_by_guardrail": True, "findings": findings[:10]}

    # 3) Respaldos ligeros antes de modificar.
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"requested_by": requested_by, "directive_sha256": directive_hash,
                    "before_commit": before_hash, "files": changed,
                    "created_at": datetime.now().isoformat(timespec="seconds")}
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for rel in changed:
            src = REPO / rel
            if src.is_file():
                dst = backup_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"falló el respaldo: {exc}"}

    # 4) Aplicación (binaria, sin interpolaciones de shell).
    for item in files:
        rel = safe_path(str(item.get("path") or ""))
        payload = base64.b64decode(item["_new_content"])
        # Escritura atómica desde Python, sin shell.
        target = REPO / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".gitops.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)

    # 5) Validación de sintaxis + comprobación de que solo cambiaron los archivos previstos.
    checks = {rel: validate(rel) for rel in changed}
    failed_checks = {k: v for k, v in checks.items() if not v.get("ok")}
    status_after = git("status", "--porcelain=v1").get("out", "")
    actual = []
    for line in status_after.splitlines():
        if not line.strip():
            continue
        # porcelain:v1 -> "XY path" (X=índice, Y=árbol de trabajo)
        rel = line[2:].strip()
        if " -> " in rel:  # renombrados
            rel = rel.split(" -> ", 1)[1]
        actual.append(rel)
    actual = sorted(actual)
    unexpected = [p for p in actual if p not in changed]
    if failed_checks or unexpected:
        rollback = restore(backup_dir, changed, {})
        return {"ok": False, "phase": "validación", "failed_checks": failed_checks,
                "unexpected": unexpected, "rollback": rollback}

    # 6) Recrear el servicio afectado (nunca corp-tools-api a sí mismo).
    restart = {"ok": True, "service": None}
    if not any(p.startswith(NO_AUTO_RESTART) for p in changed):
        restart = restart_service(changed)

    # 7) Verificación con Sentinel: 15 sondas y sin anomalías nuevas.
    after = audit()
    new_anomalies = sorted(set(after.get("anomalies") or []) - set(baseline.get("anomalies") or []))
    healthy = (
        after.get("ok") and after.get("probes_total", 0) >= 15
        and not new_anomalies and restart.get("ok")
    )
    if not healthy:
        rollback = restore(backup_dir, changed, restart)
        return {"ok": False, "phase": "healthcheck", "baseline": baseline, "audit": after,
                "new_anomalies": new_anomalies, "restart": restart, "rollback": rollback}

    # 8) Blindaje final sobre el índice: se revisa CADA archivo que se va a commitear.
    add = git("add", "--", *changed)
    staged = git("diff", "--cached", "--name-only").get("out", "").splitlines()
    staged = [s.strip() for s in staged if s.strip()]
    staged_findings = []
    for rel in staged:
        try:
            safe_path(rel)
        except ValueError:
            staged_findings.append(f"ruta bloqueada en índice: {rel}")
        blob = git("show", f":{rel}").get("out", "")
        staged_findings.extend(scan_content(rel, blob))
    if not add["ok"] or not staged or staged_findings:
        git("reset", "-q")
        rollback = restore(backup_dir, changed, restart)
        return {"ok": False, "phase": "guardrail final", "findings": staged_findings[:10],
                "rollback": rollback}

    summary = str(request.get("summary") or "gitops: cambio autónomo").strip()[:180]
    message = f"gitops({requested_by}): {summary}"
    commit = git("commit", "-m", message, timeout=120)
    if not commit["ok"]:
        git("reset", "-q")
        rollback = restore(backup_dir, changed, restart)
        return {"ok": False, "phase": "commit", "error": commit.get("err", "")[:500],
                "rollback": rollback}
    commit_hash = git("rev-parse", "HEAD").get("out", "")[:64]
    push = git("push", REMOTE, f"HEAD:{BRANCH}", timeout=180)
    if not push["ok"]:
        remote_hash = git("ls-remote", REMOTE, f"refs/heads/{BRANCH}").get("out", "").split()
        if remote_hash and remote_hash[0] == commit_hash:
            push = {**push, "ok": True, "note": "remoto ya actualizado"}
        else:
            revert = git("reset", "--hard", before_hash, timeout=60)
            rollback = restore(backup_dir, changed, restart)
            return {"ok": False, "phase": "push", "error": push.get("err", "")[:500],
                    "revert": revert.get("ok"), "rollback": rollback}

    return {
        "ok": True,
        "commit_hash": commit_hash,
        "before_commit": before_hash,
        "commit_message": message,
        "files": staged,
        "backup": str(backup_dir),
        "validation": checks,
        "service": restart,
        "audit_baseline": baseline,
        "audit_after": after,
        "new_anomalies": new_anomalies,
        "pushed_to": f"{REMOTE}/{BRANCH}",
    }


def main() -> None:
    try:
        request = json.loads(sys.stdin.read())
        op = request.get("op")
        if op == "inspect":
            out(do_inspect(request))
        elif op == "apply":
            out(do_apply(request))
        elif op == "history":
            limit = min(int(request.get("limit") or 10), 30)
            result = git("log", f"-{limit}", "--pretty=format:%H|%s",
                         "--no-merges").get("out", "")
            out({"ok": True, "commits": [line.split("|", 1) for line in result.splitlines()]})
        else:
            out({"ok": False, "error": f"operación desconocida: {op}"})
    except Exception as exc:  # noqa: BLE001
        out({"ok": False, "error": str(exc)[:500]})


if __name__ == "__main__":
    main()

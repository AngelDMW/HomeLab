"""GitOps autónomo de HomeLab Corp (corp-tools-api, Nodo 2).

Orquesta la lectura/edición/validación/commit/push del repositorio de Nodo 1
mediante un puente SSH con llave dedicada. El trabajo físico (guardrails,
respaldos, validación de sintaxis, commit, push y rollback) lo ejecuta
`scripts/gitops_agent.py` DENTRO de Nodo 1; aquí solo se generan los planes con
IA, se vuelve a verificar el anti-secretos y se asienta todo en el ledger.

Todos los endpoints exigen X-Corp-Token. Ninguno lee ni escribe secretos.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Query

DB_PATH = os.getenv("CORP_LEDGER_DB", "/app/data/corp_ledger.sqlite3")
CORP_TOKEN = os.getenv("CORP_INTERNAL_TOKEN", "")
SENTINEL = os.getenv("SENTINEL_URL", "http://192.168.1.3:9090").rstrip("/")

SSH_HOST = os.getenv("GITOPS_SSH_HOST", "192.168.1.3")
SSH_USER = os.getenv("GITOPS_SSH_USER", "mrdavid")
SSH_PORT = int(os.getenv("GITOPS_SSH_PORT", "22"))
SSH_KEY = os.getenv("GITOPS_SSH_KEY", "/app/ssh/id_ed25519")
SSH_KNOWN_HOSTS = os.getenv("GITOPS_SSH_KNOWN_HOSTS", "/app/ssh/known_hosts")
AGENT_PATH = os.getenv("GITOPS_AGENT_PATH", "/home/mrdavid/HomeLab/scripts/gitops_agent.py")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OR_KEY = os.getenv("OPENROUTER_API_KEY", "")
OR_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

REQUESTERS = {"director", "updater", "telegram_user"}
BLOCKED_SUFFIXES = (".env", ".sqlite", ".sqlite3", ".db", ".key", ".pem", ".p12", ".pfx")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
    r"password|passwd|passphrase|private[_-]?key|client[_-]?secret)\b"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"
)
SECRET_SIGNATURE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9_\-]{20,}"
)
PLANNER_PROMPT = """Eres el planificador GitOps de un HomeLab. Devuelve SOLO JSON válido, sin texto alrededor.
Formato: {"summary":"<=140 chars","files":[{"path":"README.md","mode":"append","content":"linea a añadir"}],"service":"nexus|sentinel|bot|null"}
Modos: append (añade content al final), replace (contenido COMPLETO nuevo), replace_exact (find -> replacement).
Reglas absolutas:
- Solo rutas del repositorio bajo docker/, scripts/, README.md, GEMINI.md o .github/. Nunca .env, *.sqlite*, *.db, *.key, *.pem, datos ni credenciales.
- Nunca escribas tokens, claves, contraseñas ni valores secretos.
- Haz el cambio MÁS PEQUEÑO posible. Para documentación usa append o replace_exact.
- No reescribas archivos grandes salvo que la instrucción lo exija de forma explícita.
- Si la instrucción no puede cumplirse con seguridad, devuelve {"summary":"...","files":[]}.
"""
CANDIDATES = {
    "nexus": ["docker/nexus/README.md", "docker/nexus/app/main.py"],
    "sentinel": ["docker/sentinel/README.md", "docker/sentinel/app/config.py"],
    "bot": ["docker/telegram-bot/bot.js", "docker/telegram-bot/ai_brain/tools.py"],
    "docs": ["README.md", "GEMINI.md", "docker/nexus/README.md"],
    "corp": ["docker/ai-corp/README.md", "docker/ai-corp/corp-tools-api/autonomy.py"],
}

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with closing(_connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS git_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_by TEXT NOT NULL,
                directive_hash TEXT NOT NULL,
                summary TEXT,
                files_json TEXT NOT NULL DEFAULT '[]',
                commit_hash TEXT,
                before_commit TEXT,
                status TEXT NOT NULL,
                probes_ok INTEGER,
                new_anomalies INTEGER,
                rollback INTEGER NOT NULL DEFAULT 0,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_git_changes_requested
                ON git_changes(requested_by, id DESC);
            """
        )
        conn.commit()


_init_db()


def _require_token(x_corp_token: str = Header(default="")) -> None:
    if not CORP_TOKEN or not secrets.compare_digest(x_corp_token, CORP_TOKEN):
        raise HTTPException(status_code=401, detail="X-Corp-Token inválido")


def _record(requested_by: str, directive_hash: str, summary: str, files: list[str],
            result: dict) -> int:
    detail = {k: v for k, v in result.items() if k not in {"files"}}
    with closing(_connect()) as conn:
        cur = conn.execute(
            """INSERT INTO git_changes
               (requested_by, directive_hash, summary, files_json, commit_hash,
                before_commit, status, probes_ok, new_anomalies, rollback, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                requested_by, directive_hash, str(summary)[:300],
                json.dumps(files, ensure_ascii=False)[:4000],
                str(result.get("commit_hash") or "")[:64],
                str(result.get("before_commit") or "")[:64],
                "ok" if result.get("ok") else "failed",
                (result.get("audit_after") or {}).get("probes_total"),
                len(result.get("new_anomalies") or []),
                1 if result.get("rollback") else 0,
                json.dumps(detail, ensure_ascii=False, default=str)[:12000],
                _now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


# --------------------------------------------------------------------------- #
# Puente SSH (paramiko) hacia Nodo 1
# --------------------------------------------------------------------------- #
def _ssh_client():  # pragma: no cover - depende de paramiko en la imagen
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko no instalado en corp-tools-api") from exc
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.load_host_keys(SSH_KNOWN_HOSTS)
    key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY)
    client.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, pkey=key,
                   look_for_keys=False, allow_agent=False, timeout=12)
    return client


def _agent(request: dict, timeout: int = 300) -> dict:
    client = _ssh_client()
    try:
        stdin, stdout, stderr = client.exec_command(
            f"python3 {AGENT_PATH}", timeout=timeout
        )
        stdin.write(json.dumps(request, ensure_ascii=False))
        stdin.channel.shutdown_write()
        raw = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        try:
            payload = json.loads(raw.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            payload = {"ok": False, "error": (raw or err).strip()[:600] or f"exit {code}"}
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": "respuesta inválida del agente GitOps"}
        if err.strip():
            payload["agent_log"] = err.strip()[-1200:]
        return payload
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Planificación con IA
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("el modelo no devolvió JSON")
    cleaned = match.group(0).replace("```json", "").replace("```", "")
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("plan inválido")
    return data


def _llm_plan(directive: str, context: dict) -> dict:
    user = (
        f"INSTRUCCIÓN: {directive}\n\n"
        f"SOLICITADO POR: {context.get('requested_by')}\n\n"
        f"ARCHIVOS DISPONIBLES (fragmento):\n{json.dumps(context.get('tree', [])[:120], ensure_ascii=False)}\n\n"
        f"CONTENIDO DE CANDIDATOS:\n{json.dumps(context.get('candidates', {}), ensure_ascii=False)[:28000]}"
    )
    if OR_KEY:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            response = client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OR_KEY}",
                         "HTTP-Referer": "http://192.168.1.4:5678",
                         "X-Title": "HomeLab Corp GitOps",
                         "Content-Type": "application/json"},
                json={"model": OR_MODEL, "temperature": 0.15, "max_tokens": 3000,
                      "messages": [{"role": "system", "content": PLANNER_PROMPT},
                                   {"role": "user", "content": user}]},
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            return _extract_json(choices[0]["message"]["content"])
    if GEMINI_KEY:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            response = client.post(
                GEMINI_URL.format(model=GEMINI_MODEL),
                headers={"X-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": PLANNER_PROMPT}]},
                      "contents": [{"role": "user", "parts": [{"text": user}]}],
                      "generationConfig": {"temperature": 0.15, "maxOutputTokens": 3000}},
            )
            response.raise_for_status()
            parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return _extract_json("".join(p.get("text", "") for p in parts))
    raise RuntimeError("sin proveedor de IA configurado para generar el plan")


def _validate_plan(plan: dict) -> list[dict]:
    files = plan.get("files")
    if not isinstance(files, list) or len(files) > 8:
        raise ValueError("plan sin archivos o con demasiados archivos")
    clean = []
    findings = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("entrada de plan inválida")
        path = str(item.get("path") or "")
        if (".." in path or path.startswith("/") or
                path.endswith(BLOCKED_SUFFIXES) or ".env" in path.lower() or
                not (path.startswith(("docker/", "scripts/", "README.md", "GEMINI.md", ".github/")))):
            raise ValueError(f"ruta bloqueada por guardrail: {path}")
        mode = str(item.get("mode") or "replace")
        if mode not in {"append", "replace", "replace_exact"}:
            raise ValueError(f"modo inválido: {mode}")
        text = str(item.get("content") or "")
        if mode == "replace_exact":
            text = str(item.get("replacement") or "")
        if len(text.encode("utf-8")) > 180_000:
            raise ValueError(f"contenido demasiado grande: {path}")
        if SECRET_ASSIGNMENT.search(text) or SECRET_SIGNATURE.search(text):
            findings.append(f"secreto detectado por guardrail en {path}")
        clean.append({
            "path": path, "mode": mode,
            "content": str(item.get("content") or ""),
            "find": str(item.get("find") or ""),
            "replacement": str(item.get("replacement") or ""),
            "replace_all": bool(item.get("replace_all")),
        })
    if findings:
        raise ValueError("; ".join(findings))
    return clean


def _pick_candidates(tree: list[str], directive: str) -> dict:
    lower = directive.lower()
    keys = [key for key, paths in CANDIDATES.items() if key in lower or
            any(word in lower for word in {
                "nexus": ("nexus",), "sentinel": ("sentinel", "auditor"),
                "bot": ("telegram", "bot"), "docs": ("readme", "document", "documentación", "docs"),
                "corp": ("corp", "updater", "director"),
            }.get(key, ()))]
    if not keys:
        keys = ["docs"]
    chosen: dict[str, str] = {}
    for key in keys:
        for path in CANDIDATES[key]:
            if path in tree and path not in chosen:
                try:
                    result = _agent({"op": "inspect", "path": path}, timeout=60)
                except Exception:  # noqa: BLE001
                    continue
                if result.get("ok"):
                    chosen[path] = str(result.get("content") or "")[:12000]
            if len(chosen) >= 4:
                return chosen
    return chosen


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/api/tools/repo/inspect")
async def repo_inspect(body: dict = Body(default={}),
                       x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    path = str(body.get("path") or "")
    if path and (".." in path or path.startswith("/") or ".env" in path.lower()):
        raise HTTPException(status_code=400, detail="ruta no permitida")
    try:
        result = _agent({"op": "inspect", "path": path}, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:400]}
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "inspección fallida")[:400]}
    if path:
        return {"ok": True, "path": path, "bytes": result.get("bytes"),
                "content": str(result.get("content") or "")[:160000]}
    return {"ok": True, "root": result.get("root"), "head": result.get("head"),
            "dirty": result.get("dirty"), "files": result.get("files") or []}


@router.post("/api/tools/repo/apply-change")
async def repo_apply_change(body: dict = Body(...),
                            x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    directive = str(body.get("directive") or "").strip()
    if len(directive) < 5 or len(directive) > 4000:
        raise HTTPException(status_code=400, detail="directive inválida")
    requested_by = str(body.get("requested_by") or "director")
    if requested_by not in REQUESTERS:
        raise HTTPException(status_code=400, detail="requested_by inválido")

    directive_hash = hashlib.sha256(
        f"{requested_by}\n{directive}".encode("utf-8")
    ).hexdigest()
    # Idempotencia: un doble envío no puede generar dos commits.
    with closing(_connect()) as conn:
        prior = conn.execute(
            "SELECT * FROM git_changes WHERE directive_hash=? AND status='ok' "
            "ORDER BY id DESC LIMIT 1", (directive_hash,)
        ).fetchone()
    if prior and prior["commit_hash"]:
        return {"ok": True, "already_applied": True, "commit_hash": prior["commit_hash"],
                "files": json.loads(prior["files_json"]), "requested_by": requested_by,
                "summary": prior["summary"], "audit": (json.loads(prior["detail"] or "{}")
                                                       ).get("audit_after")}

    try:
        inspection = _agent({"op": "inspect"}, timeout=60)
        if not inspection.get("ok"):
            raise RuntimeError(str(inspection.get("error") or "inspección fallida"))
        tree = [str(p) for p in (inspection.get("files") or [])]
        context = {"requested_by": requested_by, "tree": tree,
                   "candidates": _pick_candidates(tree, directive)}
        plan = _llm_plan(directive, context)
        files = _validate_plan(plan)
        if not files:
            return {"ok": False, "error": "el planificador no propuso cambios seguros",
                    "summary": plan.get("summary")}
    except Exception as exc:  # noqa: BLE001
        _record(requested_by, directive_hash, "falló la planificación", [],
                {"ok": False, "error": str(exc)[:600]})
        return {"ok": False, "error": str(exc)[:600], "requested_by": requested_by}

    result = _agent({
        "op": "apply", "requested_by": requested_by,
        "directive": directive_hash[:16],
        "summary": str(plan.get("summary") or directive[:140])[:180],
        "files": files,
    }, timeout=300)
    result["requested_by"] = requested_by
    result["summary"] = str(plan.get("summary") or directive[:140])[:180]
    _record(requested_by, directive_hash, result["summary"],
            [f["path"] for f in files], result)
    return result


@router.get("/api/tools/repo/history")
async def repo_history(limit: int = Query(10, ge=1, le=50),
                       x_corp_token: str = Header(default="")) -> dict:
    _require_token(x_corp_token)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, requested_by, directive_hash, summary, files_json, commit_hash, "
            "before_commit, status, probes_ok, new_anomalies, rollback, created_at "
            "FROM git_changes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    changes = []
    for row in rows:
        item = dict(row)
        item["files"] = json.loads(item.pop("files_json") or "[]")
        changes.append(item)
    return {"ok": True, "changes": changes, "count": len(changes)}

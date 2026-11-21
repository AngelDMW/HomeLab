import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import SESSION_COOKIE, clear_session, create_session, require_auth, verify_credentials
from .crypto import decrypt_text, encrypt_text
from .db import _utcnow, db, init_db, row_to_dict
from .seed import seed_if_empty
from .status import check_many

APP_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(APP_DIR, "static")

app = FastAPI(title="HomeLab Nexus", docs_url=None, redoc_url=None)


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class VaultItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="Infraestructura & SSH", max_length=80)
    icon: str = Field(default="🔗", max_length=16)
    lan_url: str = Field(default="", max_length=500)
    vpn_url: str = Field(default="", max_length=500)
    health_url: str = Field(default="", max_length=500)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=8000)
    is_favorite: bool = False


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20000)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_if_empty()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/health" or path == "/api/login" or not path.startswith("/api/"):
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE)
    from .auth import get_session

    sess = get_session(token)
    if not sess:
        return JSONResponse({"detail": "No autenticado"}, status_code=401)
    request.state.session = sess
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "homelab-nexus"}


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    username = (body.username or "").strip()
    password = body.password or ""
    if not verify_credentials(username, password):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    create_session(response)
    return {"ok": True, "user": username}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    clear_session(response, token)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"ok": True, "user": os.environ.get("NEXUS_USER", "angel")}


def _item_public(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "icon": row["icon"],
        "lan_url": row["lan_url"],
        "vpn_url": row["vpn_url"],
        "health_url": row["health_url"],
        "username": row["username"],
        "has_password": bool(row.get("encrypted_password")),
        "notes": row.get("notes") or "",
        "is_favorite": bool(row.get("is_favorite")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@app.get("/api/items")
def list_items():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM vault_items ORDER BY is_favorite DESC, category ASC, name ASC"
        ).fetchall()
    return {"items": [_item_public(dict(r)) for r in rows]}


@app.post("/api/items")
def create_item(body: VaultItemIn):
    now = _utcnow()
    encrypted = encrypt_text(body.password) if body.password else encrypt_text("Configurar en bóveda")
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO vault_items
            (name, category, icon, lan_url, vpn_url, health_url, username, encrypted_password, notes, is_favorite, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.name.strip(),
                body.category.strip() or "Infraestructura & SSH",
                body.icon.strip() or "🔗",
                body.lan_url.strip(),
                body.vpn_url.strip(),
                body.health_url.strip(),
                body.username.strip(),
                encrypted,
                body.notes or "",
                1 if body.is_favorite else 0,
                now,
                now,
            ),
        )
        item_id = cur.lastrowid
        row = conn.execute("SELECT * FROM vault_items WHERE id = ?", (item_id,)).fetchone()
    return {"ok": True, "item": _item_public(dict(row))}


@app.put("/api/items/{item_id}")
def update_item(item_id: int, body: VaultItemIn):
    now = _utcnow()
    with db() as conn:
        existing = conn.execute("SELECT * FROM vault_items WHERE id = ?", (item_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Elemento no encontrado")
        encrypted = existing["encrypted_password"]
        if body.password != "":
            encrypted = encrypt_text(body.password)
        conn.execute(
            """
            UPDATE vault_items SET
                name=?, category=?, icon=?, lan_url=?, vpn_url=?, health_url=?,
                username=?, encrypted_password=?, notes=?, is_favorite=?, updated_at=?
            WHERE id=?
            """,
            (
                body.name.strip(),
                body.category.strip() or "Infraestructura & SSH",
                body.icon.strip() or "🔗",
                body.lan_url.strip(),
                body.vpn_url.strip(),
                body.health_url.strip(),
                body.username.strip(),
                encrypted,
                body.notes or "",
                1 if body.is_favorite else 0,
                now,
                item_id,
            ),
        )
        row = conn.execute("SELECT * FROM vault_items WHERE id = ?", (item_id,)).fetchone()
    return {"ok": True, "item": _item_public(dict(row))}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM vault_items WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Elemento no encontrado")
    return {"ok": True}


@app.post("/api/items/{item_id}/reveal")
def reveal_item(item_id: int):
    with db() as conn:
        row = conn.execute("SELECT id, encrypted_password FROM vault_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Elemento no encontrado")
    password = decrypt_text(row["encrypted_password"])
    return {"ok": True, "id": item_id, "password": password}


@app.get("/api/status-all")
async def status_all():
    with db() as conn:
        rows = conn.execute("SELECT id, health_url, lan_url FROM vault_items").fetchall()
    items = [dict(r) for r in rows]
    statuses = await check_many(items, timeout=1.5)
    return {"ok": True, "statuses": statuses}


@app.get("/api/runbooks")
def list_runbooks():
    with db() as conn:
        rows = conn.execute("SELECT * FROM runbooks ORDER BY sort_order ASC, id ASC").fetchall()
    return {"runbooks": [dict(r) for r in rows]}


@app.post("/api/runbooks/{runbook_id}/run")
async def run_runbook(runbook_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Runbook no encontrado")
    rb = dict(row)
    if rb.get("action_type") != "http":
        return {"ok": True, "type": "copy", "command": rb.get("command", "")}

    url = (rb.get("action_url") or "").strip()
    method = (rb.get("action_method") or "POST").upper()
    payload_raw = rb.get("action_payload") or ""
    headers = {"User-Agent": "HomeLab-Nexus/1.0"}
    json_payload = None
    data_payload = None
    if payload_raw.strip():
        try:
            json_payload = json.loads(payload_raw)
            headers["Content-Type"] = "application/json"
        except Exception:
            data_payload = payload_raw

    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0, follow_redirects=True) as client:
            resp = await client.request(method, url, json=json_payload, content=data_payload if json_payload is None else None, headers=headers)
        body_text = resp.text[:2000]
        return {
            "ok": 200 <= resp.status_code < 400,
            "status_code": resp.status_code,
            "response": body_text,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@app.get("/api/notes")
def list_notes():
    with db() as conn:
        rows = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC, id DESC").fetchall()
    return {"notes": [dict(r) for r in rows]}


@app.post("/api/notes")
def create_note(body: NoteIn):
    now = _utcnow()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (body.title.strip(), body.content or "", now, now),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"ok": True, "note": dict(row)}


@app.put("/api/notes/{note_id}")
def update_note(note_id: int, body: NoteIn):
    now = _utcnow()
    with db() as conn:
        cur = conn.execute(
            "UPDATE notes SET title=?, content=?, updated_at=? WHERE id=?",
            (body.title.strip(), body.content or "", now, note_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nota no encontrada")
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    return {"ok": True, "note": dict(row)}


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nota no encontrada")
    return {"ok": True}


# Static / PWA
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/manifest.json")
def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"), media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")


@app.get("/icon-192.png")
def icon192():
    return FileResponse(os.path.join(STATIC_DIR, "icon-192.png"), media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(os.path.join(STATIC_DIR, "icon-512.png"), media_type="image/png")

import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Request, Response

from .db import _utcnow, db, row_to_dict

SESSION_COOKIE = "nexus_session"
SESSION_DAYS = int(os.environ.get("NEXUS_SESSION_DAYS", "14"))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def verify_credentials(username: str, password: str) -> bool:
    expected_user = _env("NEXUS_USER")
    expected_pass = _env("NEXUS_PASSWORD")
    if not expected_user or not expected_pass:
        return False
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_pass
    )


def create_session(response: Response) -> str:
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions(token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires.isoformat()),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_DAYS * 24 * 3600,
        path="/",
    )
    return token


def clear_session(response: Response, token: str | None) -> None:
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT token, created_at, expires_at FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
    sess = row_to_dict(row)
    if not sess:
        return None
    try:
        expires = datetime.fromisoformat(sess["expires_at"])
    except Exception:
        return None
    if expires < datetime.now(timezone.utc):
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return sess


async def require_auth(request: Request, nexus_session: str | None = Cookie(default=None)) -> dict:
    sess = get_session(nexus_session)
    if not sess:
        raise HTTPException(status_code=401, detail="No autenticado")
    request.state.session = sess
    return sess

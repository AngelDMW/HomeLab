"""Memoria conversacional corta y persistente para el bot de Telegram.

Guarda como máximo los últimos 12 turnos (24 mensajes: usuario + modelo) por chat.
La base de datos vive en /app/data, ignorada por Git.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("AI_MEMORY_DB", "/app/data/ai_memory.sqlite")
MAX_MESSAGES = 24  # 12 turnos = 12 usuario + 12 modelo


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS turns ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "chat_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, "
        "ts REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_chat ON turns(chat_id, id)")
    return conn


def history(chat_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, text FROM turns WHERE chat_id=? "
            "ORDER BY id DESC LIMIT ?",
            (str(chat_id), MAX_MESSAGES),
        ).fetchall()
        return [{"role": role, "text": text} for role, text in reversed(rows)]
    finally:
        conn.close()


def append(chat_id, role, text):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO turns(chat_id, role, text, ts) VALUES (?,?,?,?)",
            (str(chat_id), role, str(text)[:8000], time.time()),
        )
        conn.execute(
            "DELETE FROM turns WHERE chat_id=? AND id NOT IN ("
            "SELECT id FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
            (str(chat_id), str(chat_id), MAX_MESSAGES),
        )
        conn.commit()
    finally:
        conn.close()


def clear(chat_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM turns WHERE chat_id=?", (str(chat_id),))
        conn.commit()
    finally:
        conn.close()

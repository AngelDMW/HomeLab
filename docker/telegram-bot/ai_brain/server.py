"""Servidor HTTP interno (127.0.0.1) para el cerebro IA del bot de Telegram."""
import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gemini, memory


def _allowed_ids():
    raw = " ".join(filter(None, [
        os.environ.get("CHAT_ID", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
        os.environ.get("ALLOWED_CHAT_IDS", ""),
    ]))
    ids = set()
    for item in raw.replace(",", " ").split():
        try:
            ids.add(int(item))
        except ValueError:
            continue
    return ids


ALLOWED = _allowed_ids()
PORT = int(os.environ.get("AI_BRAIN_PORT", "5057"))


def log(message):
    print(f"[ai-brain] {message}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log(fmt % args)

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 12 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _authorized(self, chat_id):
        try:
            value = int(chat_id)
        except (TypeError, ValueError):
            return False
        if value not in ALLOWED:
            log(f"SEGURIDAD: chat no autorizado {value}")
            return False
        return True

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ping"):
            self._send(200, {"ok": True, "model": gemini.MODEL, "allowed": len(ALLOWED)})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/ask"):
            data = self._read_json()
            chat_id = data.get("chat_id")
            if not self._authorized(chat_id):
                self._send(403, {"ok": False, "error": "chat no autorizado"})
                return
            try:
                text = gemini.chat(chat_id, str(data.get("text") or ""))
                self._send(200, {"ok": True, "text": text})
            except Exception as exc:  # noqa: BLE001
                log(f"error en /ask: {exc}")
                self._send(200, {"ok": False, "error": str(exc)})
            return

        if self.path.startswith("/voice"):
            data = self._read_json()
            chat_id = data.get("chat_id")
            if not self._authorized(chat_id):
                self._send(403, {"ok": False, "error": "chat no autorizado"})
                return
            try:
                audio = base64.b64decode(str(data.get("audio_b64") or ""), validate=True)
                if not audio:
                    raise ValueError("audio vacío")
                mime = str(data.get("mime_type") or "audio/ogg")
                text = gemini.chat(chat_id, str(data.get("text") or ""), audio, mime)
                self._send(200, {"ok": True, "text": text})
            except Exception as exc:  # noqa: BLE001
                log(f"error en /voice: {exc}")
                self._send(200, {"ok": False, "error": str(exc)})
            return

        if self.path.startswith("/clear"):
            data = self._read_json()
            chat_id = data.get("chat_id")
            if not self._authorized(chat_id):
                self._send(403, {"ok": False, "error": "chat no autorizado"})
                return
            memory.clear(chat_id)
            self._send(200, {"ok": True})
            return

        self._send(404, {"ok": False, "error": "not found"})


def main():
    if not ALLOWED:
        log("ERROR: no hay CHAT_ID/ALLOWED_CHAT_IDS válidos; abortando")
        sys.exit(1)
    if not gemini.API_KEY:
        log("ERROR: falta GEMINI_API_KEY; abortando")
        sys.exit(1)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"escuchando en 127.0.0.1:{PORT} · modelo {gemini.MODEL} · chats permitidos {len(ALLOWED)}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

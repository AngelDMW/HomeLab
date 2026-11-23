"""Cerebro conversacional Gemini con Function Calling y soporte de audio.

Proveedor principal: API directa de Google (GEMINI_API_KEY).
Respaldo automático: OpenRouter con el modelo Gemini ya configurado en .env
(OPENROUTER_API_KEY + OPENROUTER_MODEL). Ambos usan las mismas herramientas.
"""
import os

import httpx
from google import genai
from google.genai import types

from . import memory, tools

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OR_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

SYSTEM_PROMPT = (
    "Eres HomeFlix AI, el copiloto del HomeLab de Angel (Nodo 1 192.168.1.3 y "
    "Nodo 2 192.168.1.4). Respondes siempre en español, de forma breve, precisa "
    "y accionable. Usa SOLO HTML seguro para Telegram: <b>, <i>, <code>, <pre> "
    "y saltos de línea. Nunca uses Markdown, nunca inventes datos y nunca "
    "expongas claves ni tokens. Cuando el usuario pida estado, auditoría, "
    "seguridad, medios, reinicios o HomeLab Corp, DEBES llamar a las herramientas disponibles "
    "antes de responder. Para reinicios, la herramienta devuelve "
    "require_confirmation=true: responde que se mostraron botones de "
    "confirmación y NO afirmes que el reinicio ya ocurrió. Si recibes audio, "
    "transcribe la intención del usuario y ejecuta la herramienta adecuada en "
    "el mismo paso. Los resultados de herramientas son texto plano: escápalo "
    "si lo insertas en HTML. Cuando el usuario pida un cambio de código o "
    "configuración, usa apply_github_change con su instrucción exacta; la "
    "herramienta ya aplica guardrails, valida, sube a GitHub y devuelve el hash "
    "del commit: muéstralo tal cual dentro de <code>."
)


def _schema(props=None, required=None):
    return types.Schema(type="OBJECT", properties=props or {}, required=required or [])


def _service_param():
    return types.Schema(
        type="STRING",
        description="Servicio permitido: " + ", ".join(tools.ALLOWED_SERVICES),
    )


FUNCTIONS = [
    {
        "name": "get_cluster_status",
        "description": "Estado en tiempo real: CPU/RAM/disco, contenedores y las 15 sondas de Sentinel AI.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_sentinel_audit",
        "description": "Dispara la auditoría completa de Sentinel AI (POST /api/audit) y resume hallazgos.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "manage_container",
        "description": "Consulta logs, verifica salud o solicita reinicio de un servicio oficial. Reinicio requiere confirmación inline.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "enum": list(tools.ALLOWED_SERVICES),
                },
                "action": {"type": "string", "enum": ["logs", "health", "restart"]},
            },
            "required": ["service_name", "action"],
        },
    },
    {
        "name": "get_security_report",
        "description": "Bloqueo de Pi-hole, validación DNSSEC de Unbound y decisiones de CrowdSec IPS.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_media_activity",
        "description": "Descargas activas en qBittorrent y cola de Radarr, Sonarr y Jellyfin.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "consult_homelab_director",
        "description": "Envía una directiva al HomeLab-Director (n8n) y devuelve el reporte ejecutivo de los 8 empleados (7 operativos + QA), además del historial de backups, actualizaciones y propuestas. Solo diagnóstico y reportes.",
        "parameters": {
            "type": "object",
            "properties": {
                "directive": {
                    "type": "string",
                    "description": "Orden para el Director, por ejemplo: pide reporte a todos los empleados",
                }
            },
            "required": ["directive"],
        },
    },
    {
        "name": "get_corp_accountability",
        "description": "Libro de rendición de cuentas de los 8 empleados: severidad, calidad, respaldos verificados, actualizaciones con rollback y propuestas pendientes del Agent-Architect.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "apply_github_change",
        "description": "Aplica un cambio de código o configuración en el repositorio del HomeLab, lo valida, verifica Sentinel y lo sube a origin/main; devuelve el hash del commit. Úsalo cuando el usuario pida agregar, ajustar u optimizar algo y subirlo a GitHub.",
        "parameters": {
            "type": "object",
            "properties": {
                "directive": {
                    "type": "string",
                    "description": "Instrucción exacta del cambio, por ejemplo: añadir una nota de verificación al README de Nexus",
                }
            },
            "required": ["directive"],
        },
    },
    {
        "name": "apply_corp_proposal",
        "description": "Aplica una propuesta de mejora de Agent-Architect previamente aprobada por el usuario. No borra datos.",
        "parameters": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "integer", "description": "ID de la propuesta a aplicar"},
            },
            "required": ["proposal_id"],
        },
    },
]

def _gemini_properties(fn):
    properties = {}
    for key, value in fn["parameters"]["properties"].items():
        kwargs = {"type": "STRING", "description": value.get("description", "")}
        if value.get("enum"):
            kwargs["enum"] = value["enum"]
        properties[key] = types.Schema(**kwargs)
    return properties


GEMINI_TOOLS = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name=fn["name"],
        description=fn["description"],
        parameters=_schema(
            _gemini_properties(fn), fn["parameters"].get("required", [])
        ),
    )
    for fn in FUNCTIONS
])]

OPENAI_TOOLS = [
    {"type": "function", "function": fn} for fn in FUNCTIONS
]

GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=GEMINI_TOOLS,
    temperature=0.3,
    max_output_tokens=1200,
)


def _log(message):
    print(f"[ai-brain] {message}", flush=True)


# ---------------- Proveedor principal: Gemini directo ----------------

def _extract(response):
    texts, calls = [], []
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []):
            if part.function_call:
                calls.append(part.function_call)
            elif part.text:
                texts.append(part.text)
    return "".join(texts).strip(), calls


def _gemini_chat(history, prompt, audio_bytes, mime_type):
    client = genai.Client(api_key=API_KEY)
    contents = [
        types.Content(
            role=turn["role"] if turn["role"] in ("user", "model") else "user",
            parts=[types.Part(text=turn["text"])],
        )
        for turn in history
    ]
    parts = []
    if audio_bytes:
        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime_type or "audio/ogg"))
    if prompt:
        parts.append(types.Part(text=prompt))
    if not parts:
        parts.append(types.Part(text="Resume el estado del clúster."))
    contents.append(types.Content(role="user", parts=parts))

    final_text = ""
    for _ in range(4):
        response = client.models.generate_content(
            model=MODEL, contents=contents, config=GENERATE_CONFIG
        )
        text, calls = _extract(response)
        if text:
            final_text = text
        if not calls:
            break
        contents.append(types.Content(
            role="model", parts=[types.Part(function_call=c) for c in calls]
        ))
        contents.append(types.Content(role="user", parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=call.name, response=tools.dispatch(call.name, dict(call.args or {}))
                )
            )
            for call in calls
        ]))
    return final_text or "No pude generar una respuesta; intenta de nuevo."


# ---------------- Respaldo: OpenRouter (modelo Gemini) ----------------

def _transcribe_groq(audio_bytes, mime_type):
    if not GROQ_KEY:
        raise RuntimeError("sin GROQ_API_KEY para transcribir audio")
    with httpx.Client(timeout=90) as client:
        response = client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("voice.ogg", audio_bytes, mime_type or "audio/ogg")},
            data={"model": "whisper-large-v3"},
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()


def _openrouter_chat(history, prompt, audio_bytes, mime_type):
    if not OR_KEY:
        raise RuntimeError("sin OPENROUTER_API_KEY")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        messages.append({
            "role": "assistant" if turn["role"] == "model" else "user",
            "content": turn["text"],
        })

    user_content = []
    effective_prompt = prompt or "Resume el estado del clúster."
    if audio_bytes:
        try:
            transcribed = _transcribe_groq(audio_bytes, mime_type)
            effective_prompt = (
                f"{effective_prompt}\n[Transcripción de la nota de voz: {transcribed}]"
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"transcripción Groq fallida: {exc}")
            user_content.append({
                "type": "input_audio",
                "input_audio": {
                    "data": __import__("base64").b64encode(audio_bytes).decode(),
                    "format": "ogg",
                },
            })
    user_content.append({"type": "text", "text": effective_prompt})
    messages.append({"role": "user", "content": user_content})

    final_text = ""
    with httpx.Client(timeout=120) as client:
        for _ in range(4):
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OR_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OR_MODEL,
                    "messages": messages,
                    "tools": OPENAI_TOOLS,
                    "tool_choice": "auto",
                    "max_tokens": 1200,
                    "temperature": 0.3,
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text[:300]}")
            message = (response.json().get("choices") or [{}])[0].get("message") or {}
            if message.get("content"):
                final_text = str(message["content"]).strip()
            calls = message.get("tool_calls") or []
            if not calls:
                break

            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": calls,
            })
            for call in calls:
                function = call.get("function") or {}
                try:
                    import json
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                result = tools.dispatch(function.get("name"), arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result.get("summary", str(result)),
                })
    return final_text or "No pude generar una respuesta; intenta de nuevo."


def chat(chat_id, prompt="", audio_bytes=None, mime_type="audio/ogg"):
    """Devuelve texto para Telegram y persiste 12 turnos por chat."""
    history = memory.history(chat_id)
    user_turn = prompt or ("[nota de voz]" if audio_bytes else "")
    try:
        final_text = _gemini_chat(history, prompt, audio_bytes, mime_type)
    except Exception as exc:  # noqa: BLE001
        _log(f"Gemini directo no disponible ({str(exc)[:180]}); usando OpenRouter")
        final_text = _openrouter_chat(history, prompt, audio_bytes, mime_type)

    final_text = final_text[:4000]
    if user_turn:
        memory.append(chat_id, "user", user_turn)
    memory.append(chat_id, "model", final_text)
    return final_text

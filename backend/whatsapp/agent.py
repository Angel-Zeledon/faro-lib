"""
The WhatsApp tool-calling agent. One LLM completion per non-confirming turn
routes the message to a query tool, a write-tool proposal, or a free-text
reply. The confirmation gate is system-controlled: a turn that confirms a
stored pending_action executes it WITHOUT calling the LLM; any non-affirmative
message discards the pending action and is handled as a fresh intent.

The LLM is used only for intent routing / small talk; it never touches the DB
and never decides whether a write executes.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from backend.ai.local_llm import get_local_llm_client
from backend.whatsapp import tools as wt
from backend.whatsapp.tools import ToolContext, ToolError

log = logging.getLogger(__name__)

MAX_TOKENS = 400

_AFFIRMATIVE = {
    "si", "sisi", "s", "y", "yes", "ok", "oka", "okay", "dale", "listo",
    "confirmo", "confirmar", "confirmado", "aprobar", "apruebo", "correcto",
    "deacuerdo", "vale", "hazlo", "adelante", "sip",
}
_NEGATIVE = {"no", "cancela", "cancelar", "mejorno", "nop", "negativo"}

_HELP = ("Puedo ayudarte con tu inventario: pregúntame por el semáforo "
         "(qué pedir), tus órdenes pendientes o el pronóstico de un SKU. "
         "También puedo aprobar una orden o registrar una recepción.")

_APOLOGY = "Perdón, tuve un problema procesando tu mensaje. ¿Puedes intentarlo de nuevo?"


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(text: str) -> str:
    return _strip_accents((text or "").strip().lower())


def is_affirmative(text: str) -> bool:
    norm = _normalize(text)
    words = set(re.findall(r"[a-z]+", norm))
    if words & _NEGATIVE:
        return False
    if words & _AFFIRMATIVE:
        return True
    # A bare "si ..." start also counts (e.g. "si confirmo la orden").
    return norm.split(" ", 1)[0] in _AFFIRMATIVE if norm else False


def _system_prompt() -> str:
    lines = [
        "Eres el asistente de inventario de Faro por WhatsApp. Decide qué "
        "herramienta usar para responder al usuario. Responde SOLO con un "
        "objeto JSON, sin texto adicional.",
        'Formato: {"tool": <nombre|null>, "args": {...}, "reply": <texto|null>}.',
        "Si ninguna herramienta aplica, usa tool=null y escribe una respuesta breve en 'reply'.",
        "Herramientas disponibles:",
    ]
    for spec in wt.TOOL_SPECS:
        lines.append(f'- {spec["name"]} ({spec["kind"]}): {spec["description"]} args={spec["args"]}')
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _route(ctx: ToolContext, text: str, history: list[dict]) -> dict:
    client = get_local_llm_client()
    messages = [{"role": t["role"], "content": t["content"]} for t in (history or [])[-6:]]
    messages.append({"role": "user", "content": text})
    resp = client.messages.create(
        model="whatsapp-agent",
        max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=messages,
    )
    raw = resp.content[0].text if resp and resp.content else ""
    m = _JSON_RE.search(raw or "")
    if not m:
        return {"tool": None, "args": {}, "reply": None}
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {"tool": None, "args": {}, "reply": None}
    return {
        "tool": obj.get("tool"),
        "args": obj.get("args") or {},
        "reply": obj.get("reply"),
    }


def run_turn(ctx: ToolContext, incoming_text: str, state: dict):
    """
    Returns (reply_text, new_history, new_pending_action). Pure orchestration;
    the caller loads `state` and persists the returned history/pending action.
    """
    history = list(state.get("history") or [])
    pending = state.get("pending_action")

    reply, new_pending = _handle(ctx, incoming_text, history, pending)

    history = history + [
        {"role": "user", "content": incoming_text},
        {"role": "assistant", "content": reply},
    ]
    return reply, history, new_pending


def _handle(ctx, incoming_text, history, pending):
    # 1. Confirmation gate — system-controlled, no LLM call.
    if pending:
        if is_affirmative(incoming_text):
            try:
                return wt.execute_pending_action(ctx, pending), None
            except ToolError as e:
                return str(e), None
            except Exception:  # noqa: BLE001 — never leave a half-applied write ambiguous
                log.exception("[whatsapp] execute_pending_action failed")
                return _APOLOGY, None
        # Non-confirming: discard and treat as a fresh intent below.
        pending = None

    # 2. Fresh intent routing (one LLM completion).
    try:
        decision = _route(ctx, incoming_text, history)
    except Exception:  # noqa: BLE001 — LLM/timeout: apologize, mutate nothing
        log.exception("[whatsapp] routing failed")
        return _APOLOGY, None

    tool = decision.get("tool")
    args = decision.get("args") or {}

    if tool in wt.QUERY_TOOLS:
        try:
            return wt.QUERY_TOOLS[tool](ctx, args), None
        except ToolError as e:
            return str(e), None
        except Exception:  # noqa: BLE001
            log.exception("[whatsapp] query tool failed: %s", tool)
            return _APOLOGY, None

    if tool in wt.WRITE_TOOLS:
        if not ctx.is_analyst_or_above:
            return ("Tu perfil es de solo lectura, así que no puedo ejecutar acciones. "
                    "Puedo darte información de inventario si quieres."), None
        try:
            proposal = wt.WRITE_TOOLS[tool](ctx, args)
        except ToolError as e:
            return str(e), None
        return proposal["summary"], proposal

    # 3. No tool — free-text reply from the LLM, or default help.
    reply = decision.get("reply")
    return (reply or _HELP), None

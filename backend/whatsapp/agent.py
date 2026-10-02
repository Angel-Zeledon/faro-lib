"""
The WhatsApp channel adapter for the assistant core (`backend/assistant/`).

A fresh message is answered by the SAME core the web chat uses — same account
context, same read-only tools, same persona and grounding guard — with the
"whatsapp" channel's formatting (short plain text, absolute links). The bot used
to run its own JSON router over three canned query tools; it answered from a
different, poorer picture of the account than the app (it even read the newest
completed session at a daily grain while `/compras` read the active one at the
tenant's planning grain), so the same question got two answers.

What stays here is WhatsApp-specific:

* The confirmation gate. A turn that confirms a stored `pending_action`
  executes it WITHOUT calling the LLM; any non-affirmative message discards it
  and is handled as a fresh question. The core proposes nothing — it is
  read-only by charter (`docs/assistant-actions.md`) — so a pending action can
  only be one stored by older code, and the two that existed (`approve_po`,
  `register_reception`) are suspended and dropped (see `whatsapp/tools.py`).
* Generic mode (`whatsapp_bot_generic_mode`): a canned reply, no LLM at all.
* Spanish: WhatsApp is a Spanish-only channel for this product.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from backend.notifications.locale import render_es
from backend.whatsapp import tools as wt
from backend.whatsapp.tools import ToolContext, ToolError

log = logging.getLogger(__name__)

# The turn runs after the webhook has answered Twilio (api/v1/whatsapp.py), so
# Twilio's 15 s timeout does not bound it; this does. Measured against DeepSeek
# a turn with one tool round takes 6-13 s.
WHATSAPP_BUDGET_S = 25.0

# These two match what the USER types, so they are Spanish on purpose — the same
# exemption as the CSV header aliases: values read from real user input, not copy.
_AFFIRMATIVE = {
    "si", "sisi", "s", "y", "yes", "ok", "oka", "okay", "dale", "listo",
    "confirmo", "confirmar", "confirmado", "aprobar", "apruebo", "correcto",
    "deacuerdo", "vale", "hazlo", "adelante", "sip",
}
_NEGATIVE = {"no", "cancela", "cancelar", "mejorno", "nop", "negativo"}


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


def _answer(ctx: ToolContext, text: str, history: list[dict]) -> str:
    """One question, answered by the shared assistant core."""
    from backend.assistant import answer
    reply = answer(
        ctx.tenant_id, ctx.user_id, "whatsapp", text, history,
        role=ctx.role, language="es", budget_s=WHATSAPP_BUDGET_S,
    )
    return reply.text


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
        # A proposal stored BEFORE these two were suspended must not execute
        # today just because the user answers "sí" now. It is dropped, and the
        # "sí" is answered with where the action actually lives. A message that
        # was NOT a confirmation falls through to a fresh question as always.
        if (pending or {}).get("type") in wt.SUSPENDED_WRITE_TOOLS:
            log.info("[whatsapp] dropped a pending %s: the action is suspended",
                     pending.get("type"))
            if is_affirmative(incoming_text):
                return render_es("wa_write_in_app"), None
            pending = None
        elif is_affirmative(incoming_text):
            try:
                return wt.execute_pending_action(ctx, pending), None
            except ToolError as e:
                return str(e), None
            except Exception:  # noqa: BLE001 — never leave a half-applied write ambiguous
                log.exception("[whatsapp] execute_pending_action failed")
                return render_es("wa_apology"), None
        # Non-confirming: discard and treat as a fresh question below.
        pending = None

    # Generic mode: no hosted LLM available — reply fast and honest instead of
    # hanging. Confirmations above already executed.
    from backend.service_config.resolver import effective
    # Read in the tenant's scope: `whatsapp_bot_generic_mode` is offered per
    # tenant in the panel, and reading it at instance scope would store a
    # choice nothing acts on.
    if effective(ctx.tenant_id).whatsapp_bot_generic_mode:
        return render_es("wa_generic_mode"), None

    # 2. A fresh question — the shared assistant core. It never raises for a
    # missing key or a slow model (it answers from the data by rules and says
    # so); anything else is a bug, and the user still gets an apology.
    try:
        return _answer(ctx, incoming_text, history), None
    except Exception:  # noqa: BLE001
        log.exception("[whatsapp] assistant core failed")
        return render_es("wa_apology"), None

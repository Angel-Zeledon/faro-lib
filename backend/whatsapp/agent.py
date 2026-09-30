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
from backend.notifications.locale import render_es
from backend.whatsapp import tools as wt
from backend.whatsapp.tools import ToolContext, ToolError

log = logging.getLogger(__name__)

MAX_TOKENS = 400

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


# The pseudo-tool the routing prompt asks for when the user requests one of the
# suspended actions. It runs no code and touches nothing — it only selects the
# "do that in the app" answer.
_IN_APP_TOOL = "not_available_here"


def _system_prompt() -> str:
    # English prompt, Spanish answer: WhatsApp is a Spanish-only channel for this
    # product, and the free-text `reply` goes straight to the user's phone.
    lines = [
        "You are StockAI's inventory assistant on WhatsApp. Decide which tool to "
        "use to answer the user. Reply with ONLY a JSON object, no other text.",
        'Format: {"tool": <name|null>, "args": {...}, "reply": <text|null>}.',
        "If no tool applies, use tool=null and write a short answer in 'reply'.",
        "The 'reply' text is sent to the user as-is, so write it in Spanish.",
        "Available tools:",
    ]
    for spec in wt.TOOL_SPECS:
        lines.append(f'- {spec["name"]} ({spec["kind"]}): {spec["description"]} args={spec["args"]}')
    if wt.SUSPENDED_TOOL_SPECS:
        # Named, but NOT offered: the model must be able to recognise the
        # request in order to say where it is done. Without this it routes to
        # tool=null and the user who asked to register a reception gets the
        # help menu, which reads as "the bot did not understand".
        lines.append(
            "These actions CANNOT be performed here (they are not reversible "
            "from WhatsApp). If the user asks for one of them, reply with "
            f'tool="{_IN_APP_TOOL}" and args={{}}:'
        )
        for spec in wt.SUSPENDED_TOOL_SPECS:
            lines.append(f'- {spec["name"]}: {spec["description"]}')
    return "\n".join(lines)


def _first_json_object(raw: str) -> dict | None:
    """The first COMPLETE JSON object in the model's answer, or None.

    Was `re.compile(r"\\{.*\\}", re.DOTALL)` — greedy, so it spanned from the
    first brace to the last one in the whole reply. A model that wrote a
    sentence containing a brace before the object, or emitted two objects,
    produced a slice that does not parse; the turn then fell through to the
    no-tool path and the user got a menu instead of what they asked for.

    `raw_decode` from each opening brace stops at the end of the first valid
    object, so trailing prose or a second object is simply ignored.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw, i)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


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
    obj = _first_json_object(raw or "")
    if obj is None:
        # This used to return silently. The user still got an answer — the help
        # text — so nothing looked broken from outside, and nobody operating
        # the bot could measure how often the router simply failed. A fallback
        # that leaves no trace is the exact shape of a silent failure.
        log.warning(
            "[whatsapp] router answer carried no JSON object; falling back to "
            "the no-tool reply. raw=%r", (raw or "")[:400],
        )
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
        # A proposal stored BEFORE these two were suspended must not execute
        # today just because the user answers "sí" now. It is dropped, and the
        # "sí" is answered with where the action actually lives. A message that
        # was NOT a confirmation falls through to fresh intent as always —
        # someone who asks for the semáforo gets the semáforo, not a lecture
        # about an order they mentioned three turns ago.
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
        # Non-confirming: discard and treat as a fresh intent below.
        pending = None

    # Generic mode: no hosted LLM available — reply fast and honest instead of
    # hanging on a slow local model. Confirmations above already executed.
    from backend.service_config.resolver import effective
    # Read in the tenant's scope: `whatsapp_bot_generic_mode` is offered per
    # tenant in the panel, and reading it at instance scope would store a
    # choice nothing acts on.
    if effective(ctx.tenant_id).whatsapp_bot_generic_mode:
        return render_es("wa_generic_mode"), None

    # 2. Fresh intent routing (one LLM completion).
    try:
        decision = _route(ctx, incoming_text, history)
    except Exception:  # noqa: BLE001 — LLM/timeout: apologize, mutate nothing
        log.exception("[whatsapp] routing failed")
        return render_es("wa_apology"), None

    tool = decision.get("tool")
    args = decision.get("args") or {}

    if tool in wt.QUERY_TOOLS:
        try:
            return wt.QUERY_TOOLS[tool](ctx, args), None
        except ToolError as e:
            return str(e), None
        except Exception:  # noqa: BLE001
            log.exception("[whatsapp] query tool failed: %s", tool)
            return render_es("wa_apology"), None

    if tool == _IN_APP_TOOL or tool in wt.SUSPENDED_WRITE_TOOLS:
        return render_es("wa_write_in_app"), None

    if tool in wt.WRITE_TOOLS:
        if not ctx.is_analyst_or_above:
            return render_es("wa_read_only"), None
        try:
            proposal = wt.WRITE_TOOLS[tool](ctx, args)
        except ToolError as e:
            return str(e), None
        return proposal["summary"], proposal

    # 3. No tool — free-text reply from the LLM, or default help.
    reply = decision.get("reply")
    return (reply or render_es("wa_help")), None

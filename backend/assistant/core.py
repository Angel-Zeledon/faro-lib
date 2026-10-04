"""One turn of the assistant: account context -> model (+ read-only tools) ->
grounding guard -> channel formatting. Rule-based answer when there is no model.

See the package header (`backend/assistant/__init__.py`) for the contract every
channel calls and how a new channel plugs in.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from backend.ai.style_rules import MONEY_WORDING_RULE
from backend.assistant import grounding
from backend.assistant import tools as assistant_tools
from backend.assistant.account import AccountData
from backend.assistant.channels import Channel, get_channel
from backend.assistant.context import AccountContext, build_account_context
from backend.auth.guards import CurrentUser
from backend.notifications.locale import render

log = logging.getLogger(__name__)

# The whole turn — context, every model call, every tool — fits in this. The
# web caller passes its own (chats.LLM_BUDGET_S, under the proxy's 30 s cut);
# this default is for channels with no proxy in front, like WhatsApp's webhook.
DEFAULT_BUDGET_S = 23.0
# Tool rounds before the model is made to answer with what it has.
MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 4
# A model call is not started with less than this left: it would be cut, and a
# cut call costs the whole turn instead of falling back to what we know.
MIN_CALL_S = 4.0
# Tools are offered only while at least this much is left: a tool round is a
# second model call, and the answer after it needs its own time.
TOOL_ROUND_MIN_S = 10.0
MAX_HISTORY_MESSAGES = 8

LANGUAGE_NAMES = {"es": "Spanish", "en": "English"}

# Screens the model may point at — the only "actions" it has. Paths are the
# app's routes (deliberately Spanish, CLAUDE.md); descriptions are prompt text.
DEEP_LINKS: dict[str, str] = {
    "/compras": "today's purchasing: what to order now and building a purchase order",
    "/pedidos": "purchase orders: send to the supplier, register receptions, track arrivals",
    "/inventario": "stock levels, unit costs, lead times and order minimums per product; load stock",
    "/proveedores": "suppliers, their declared and real lead times, scorecard",
    "/pronosticos": "each product's forecast chart",
    "/ventas": "upload sales history and retrain the forecast",
    "/impacto": "money saved and the impact of following the recommendations",
}


@dataclass
class AssistantReply:
    text: str
    # "assistant": the model answered; "rules": the rule-based answer.
    source: str
    language: str
    channel: str
    # True when every figure in the reply was found in the account's data.
    grounded: bool = True
    unverified: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    context_sections: list[str] = field(default_factory=list)
    # Why the rule-based answer was used: "not_configured" | "llm_error" | "timeout".
    reason: str | None = None
    first_name: str = ""


def _resolve_language(data: AccountData, language: str | None) -> str:
    if language in LANGUAGE_NAMES:
        return language
    pref = (data.preferences or {}).get("language")
    return pref if pref in LANGUAGE_NAMES else "es"


def _system_prompt(ctx: AccountContext, data: AccountData, channel: Channel) -> str:
    name = ctx.first_name or "the user"
    company = ctx.company or "their company"
    links = "; ".join(f"{path} ({what})" for path, what in DEEP_LINKS.items())
    return f"""\
You are StockAI's purchasing assistant for {name} at {company}. You work ONLY with this \
account's real data: the ACCOUNT DATA below and what your tools return.

Voice: warm, direct and personal — a trusted purchasing advisor who knows this business \
by heart. Use {name}'s first name naturally (not in every sentence). Talk about their \
business in the second person: "your products", "your supplier X" (in Spanish use the \
informal "tu" form, e.g. "tus productos"). Lead with the answer, then the reason, \
then one concrete next step. No filler, no generic advice that ignores their numbers.

Language: always answer in {LANGUAGE_NAMES[ctx.language]}. In Spanish, write neutral Latin \
American Spanish with "tu" forms — never voseo ("decime", "contame", "sabes" stressed on \
the last syllable): say "dime", "cuentame", "sabes". {MONEY_WORDING_RULE}

Facts — these rules are checked after you answer:
- Every number you write must appear in the ACCOUNT DATA or in a tool result of this \
conversation. Never estimate, extrapolate or compute new figures (no sums, no unit \
conversions). Coverage is in {data.coverage_unit}s; say it in that unit.
- If you need a figure that is not there, call a tool. If no tool has it, say plainly that \
you do not have that data and which screen gives it.
- Never invent products, suppliers, orders or dates; use names exactly as written.
- Signals: PEDIR_YA = order now (red), PEDIR_PRONTO = order soon (amber), OK = fine, \
SOBRESTOCK = overstock, SIN_DATOS = no stock on record, which is UNKNOWN and never "safe". \
Say them in plain words.
- A value whose source is "default" is StockAI's assumption, not the user's own figure: say \
so when it matters. If the data is stale, say it when it affects the answer.

Actions: you can read, never change anything — you cannot create, send, approve or receive \
orders, nor edit stock, costs or suppliers. When the user wants an action, say what you \
would do and point to the screen where they do it. Never claim you did something. \
Screens: {links}.

Format: {channel.style}

ACCOUNT DATA (live, as of this message):
{ctx.text}"""


def _history_messages(history: list[dict] | None) -> list[dict]:
    out = []
    for turn in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out


# ── The rule-based answer ────────────────────────────────────────────────────

def rules_answer(data: AccountData, language: str, reason: str) -> str:
    """What the data says, with no model: greeting, counts, top risks, late
    orders, and where to look. Opens by saying WHY there is no AI answer, so a
    missing key is never mistaken for the assistant's considered reply."""
    from backend.inventory.roi_service import format_po_number

    r = lambda key, **p: render(language, key, **p)  # noqa: E731
    lines: list[str] = []
    intro = r("assistant_intro_not_configured" if reason == "not_configured" else "assistant_intro_failed")
    greet = r("assistant_greeting", name=data.first_name) if data.first_name else ""
    lines.append(greet + intro)

    if not data.planning.get("active_session_id"):
        lines.append(r("assistant_no_forecast"))
        return "\n".join(lines)

    k = data.briefing.get("kpis") or {}
    lines.append(r("assistant_counts", order_now=k.get("order_now", 0),
                   order_soon=k.get("order_soon", 0), overstock=k.get("overstock", 0)))
    unit = r(f"assistant_unit_{data.coverage_unit}") if data.coverage_unit in ("day", "week", "month") \
        else data.coverage_unit
    risks = sorted(data.briefing.get("risks") or [], key=lambda i: i.get("coverage_days") or 0)
    if not risks:
        lines.append(r("assistant_nothing_urgent"))
    for i in risks[:5]:
        line = r("assistant_risk_line", name=i.get("display_name") or i.get("sku"),
                 stock=f"{float(i.get('current_stock') or 0):,.0f}",
                 cover=f"{float(i.get('coverage_days') or 0):,.1f}", unit=unit,
                 qty=f"{float(i.get('recommended_qty') or 0):,.0f}")
        if i.get("supplier"):
            line += r("assistant_risk_supplier", supplier=i["supplier"])
        lines.append(line)
    by_id = {p.get("id"): p for p in data.po_history}
    for o in data.overdue_pos[:3]:
        po = by_id.get(o.get("po_log_id")) or {}
        lines.append(r("assistant_overdue_line",
                       reference=format_po_number(po.get("po_number"), o.get("po_log_id")),
                       supplier=o.get("supplier"), days=o.get("days_overdue")))
    if k.get("sin_datos"):
        lines.append(r("assistant_sin_datos", n=k["sin_datos"]))
    if (data.freshness or {}).get("semaphore") == "degraded":
        lines.append(r("assistant_stale"))
    lines.append(r("assistant_footer"))
    return "\n".join(lines)


# ── The model loop ───────────────────────────────────────────────────────────

class _OutOfTime(Exception):
    pass


def _client(remaining: float):
    # Looked up on the module at call time, so the suite-wide patch in
    # conftest.py (and a test's own patch) is what runs.
    from backend.ai import local_llm
    return local_llm.get_local_llm_client(timeout=max(1.0, remaining - 0.5))


def _call(deadline: float, *, system: str, messages: list, max_tokens: int, tools: list | None):
    remaining = deadline - time.monotonic()
    if remaining < MIN_CALL_S:
        raise _OutOfTime()
    client = _client(remaining)
    kwargs = {"max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    return client.messages.create(**kwargs)


def _text_of(resp) -> str:
    content = getattr(resp, "content", None) or []
    return (getattr(content[0], "text", "") if content else "") or ""


def answer(
    tenant_id: str,
    user_id: str,
    channel: str,
    message: str,
    history: list[dict] | None = None,
    *,
    role: str = "viewer",
    language: str | None = None,
    budget_s: float = DEFAULT_BUDGET_S,
) -> AssistantReply:
    """Answer one message from one user, on one channel, about their account.

    `role` only narrows: every read here is a GET a viewer may make, so the
    least-privileged role is the safe default. The tenant comes from the
    caller's authenticated identity, never from the message.
    """
    started = time.monotonic()
    deadline = started + budget_s
    user = CurrentUser(user_id=user_id, tenant_id=tenant_id, role=role)
    data = AccountData(user)
    chan = get_channel(channel)
    lang = _resolve_language(data, language)
    ctx = build_account_context(data, message, language=lang, channel=chan.name)

    def _rules(reason: str) -> AssistantReply:
        return AssistantReply(
            text=chan.format(rules_answer(data, lang, reason)), source="rules",
            language=lang, channel=chan.name, reason=reason,
            context_sections=ctx.sections, first_name=ctx.first_name,
        )

    from backend.ai.local_llm import LLMNotConfigured

    system = _system_prompt(ctx, data, chan)
    messages = _history_messages(history) + [{"role": "user", "content": message}]
    # Output length is most of a call's wall time; the persona asks for short
    # answers and these keep a verbose model inside the budget.
    max_tokens = 300 if chan.name == "whatsapp" else 600
    evidence: list[str] = [ctx.text, message]
    tools_used: list[str] = []
    specs = assistant_tools.openai_specs()

    try:
        text = ""
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            # Tools are offered only while there is time for another round
            # after this one; the last call must produce prose.
            offer = round_no < MAX_TOOL_ROUNDS and (deadline - time.monotonic()) > TOOL_ROUND_MIN_S
            resp = _call(deadline, system=system, messages=messages,
                         max_tokens=max_tokens, tools=specs if offer else None)
            calls = list(getattr(resp, "tool_calls", None) or [])
            if not calls or not offer:
                text = _text_of(resp)
                break
            assistant_turn = dict(getattr(resp, "message", None) or {})
            assistant_turn.setdefault("role", "assistant")
            assistant_turn["content"] = assistant_turn.get("content") or ""
            if not assistant_turn.get("tool_calls"):
                assistant_turn["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.arguments}} for c in calls]
            messages.append(assistant_turn)
            for c in calls[:MAX_CALLS_PER_ROUND]:
                result, ok = assistant_tools.run_tool(data, c.name, c.arguments)
                tools_used.append(c.name if ok else f"{c.name}!")
                if not ok:
                    log.warning("[assistant] tool %s failed or was refused: %s", c.name, result[:200])
                evidence.append(result)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
            # A call beyond the per-round cap still needs an answer, or the
            # provider rejects the next request for an unanswered tool_call_id.
            for c in calls[MAX_CALLS_PER_ROUND:]:
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": json.dumps({"error": "too many calls in one round"})})

        text = (text or "").strip()
        if not text:
            log.warning("[assistant] model returned an empty answer (tenant=%s, channel=%s)",
                        tenant_id, chan.name)
            return _rules("llm_error")

        unverified = grounding.unverified_numbers(text, *evidence)
        if unverified:
            log.info("[assistant] ungrounded figures %s; asking for a rewrite", unverified)
            try:
                retry = _call(deadline, system=system, max_tokens=max_tokens, tools=None, messages=messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        "Automatic check: these figures in your draft do not appear in the account "
                        f"data or tool results: {', '.join(unverified)}. Rewrite the same answer "
                        "using only figures that appear there; where a figure is missing, say it "
                        "is not available. Reply with the rewritten answer only.")},
                ])
                fixed = _text_of(retry).strip()
                if fixed:
                    still = grounding.unverified_numbers(fixed, *evidence)
                    if len(still) < len(unverified) or not still:
                        text, unverified = fixed, still
            except (_OutOfTime, Exception) as exc:  # noqa: BLE001 — keep the draft, flag it
                log.info("[assistant] grounding rewrite skipped: %s", exc)

        formatted = chan.format(text)
        if unverified:
            formatted += "\n\n" + render(lang, "assistant_unverified", numbers=", ".join(unverified))
        log.info("[assistant] tenant=%s channel=%s tools=%s sections=%s unverified=%d %.1fs",
                 tenant_id, chan.name, tools_used, ctx.sections, len(unverified),
                 time.monotonic() - started)
        return AssistantReply(
            text=formatted, source="assistant", language=lang, channel=chan.name,
            grounded=not unverified, unverified=unverified, tools_used=tools_used,
            context_sections=ctx.sections, first_name=ctx.first_name,
        )
    except LLMNotConfigured:
        return _rules("not_configured")
    except _OutOfTime:
        log.warning("[assistant] out of time after %.1fs (tenant=%s, tools=%s)",
                    time.monotonic() - started, tenant_id, tools_used)
        return _rules("timeout")
    except Exception as exc:  # noqa: BLE001 — the user gets their data, the log gets the trace
        import httpx
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            log.warning("[assistant] model timed out (tenant=%s, channel=%s)", tenant_id, chan.name)
            return _rules("timeout")
        log.exception("[assistant] model call failed (tenant=%s, channel=%s)", tenant_id, chan.name)
        return _rules("llm_error")

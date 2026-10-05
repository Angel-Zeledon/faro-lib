"""
Inbound Twilio WhatsApp webhook. HTTP + wiring only — no business logic:
verify the Twilio signature, dedupe by MessageSid, resolve the sender to a
verified user, rate-limit per number, answer 200, then — in a background
task — run the agent (the shared assistant core, `backend/assistant/`),
persist state, and reply via the existing outbound send_whatsapp().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

from backend.config import settings
from backend.service_config.resolver import effective
from backend.db.connection import execute, query_one
from backend.notifications.whatsapp import send_whatsapp
from backend.notifications.locale import render_es
from backend.whatsapp import agent, conversation_store as cs, identity
from backend.whatsapp.tools import ToolContext

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
log = logging.getLogger(__name__)

# Per-number cap: N inbound messages per rolling window (seconds).
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECS = 60

# Both go straight back to a phone, so their Spanish lives in the backend copy
# catalog like the rest of this channel's wording.
_REJECT_UNKNOWN = render_es("wa_unknown_number")
_RATE_LIMITED = render_es("wa_rate_limited")


def compute_twilio_signature(url: str, params: dict, auth_token: str) -> str:
    """Twilio's request-signature algorithm: URL + sorted(key+value), HMAC-SHA1, base64."""
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_twilio_signature(url: str, params: dict, signature: str, auth_token: str) -> bool:
    if not auth_token or not signature:
        return False
    expected = compute_twilio_signature(url, params, auth_token)
    return hmac.compare_digest(expected, signature)


def _signed_url(request: Request) -> str:
    """Reconstruct the PUBLIC url Twilio signed X-Twilio-Signature over.

    Twilio computes the signature against the exact url it POSTed to. Behind the
    frontend proxy / TLS termination the backend's own ``request.url`` is an
    internal address (e.g. ``http://internal-host:8010/...``) that will not match
    the public ``https://.../api/v1/whatsapp/inbound`` Twilio signed, so the HMAC
    would never match. Resolution order (first wins):

      1. ``WHATSAPP_WEBHOOK_BASE_URL`` — authoritative external base.
      2. ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` set by the proxy.
      3. ``request.url`` — today's behaviour (local/dev, no proxy).

    Path and query always come from the actual request so any suffix/params are
    preserved regardless of which base is chosen.
    """
    path_qs = request.url.path
    if request.url.query:
        path_qs = f"{path_qs}?{request.url.query}"

    base = (effective().whatsapp_webhook_base_url or "").strip()
    if base:
        return base.rstrip("/") + path_qs

    proto = request.headers.get("X-Forwarded-Proto", "")
    host = request.headers.get("X-Forwarded-Host", "")
    if proto and host:
        return f"{proto}://{host}{path_qs}"

    return str(request.url)


def _rate_limited(phone: str) -> bool:
    """True if `phone` exceeded the window; otherwise records this hit. Bypassed
    in testing_mode (matches the auth rate-limit convention)."""
    if settings.testing_mode:
        return False
    key = f"wa:{phone}"
    try:
        execute(
            "DELETE FROM auth_rate_events WHERE key = %s AND created_at < NOW() - make_interval(secs => %s)",
            (key, RATE_LIMIT_WINDOW_SECS),
        )
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        if row and row["n"] >= RATE_LIMIT_MAX:
            return True
        execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (key,))
        return False
    except Exception:  # noqa: BLE001 — a rate-limit store hiccup must not break inbound
        log.exception("[whatsapp] rate-limit check failed; allowing")
        return False


def _tell_plan_locked(phone: str, sender: dict) -> bool:
    """Send the locked-plan notice unless this sender already got one in the
    last 24 hours. Returns whether a message was sent. Never raises.

    The day's slot is claimed BEFORE sending (an insert into the same event
    table the rate limiter uses), so a Twilio retry or a chatty sender cannot
    produce two notices; if the send itself fails the slot stays spent — one
    missed notice beats a loop of them.
    """
    from backend.notifications.locale import render
    key = f"wa_locked:{sender['tenant_id']}:{sender['user_id']}"
    try:
        execute(
            "DELETE FROM auth_rate_events WHERE key = %s AND created_at < NOW() - INTERVAL '24 hours'",
            (key,),
        )
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        if row and int(row["n"]) >= 1:
            return False
        execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (key,))
    except Exception:  # noqa: BLE001 — the store failing must not become a reply loop
        log.exception("[whatsapp] locked-plan notice store failed; not replying")
        return False
    lang = "es"
    try:
        pref = query_one("SELECT language FROM user_preferences WHERE user_id = %s",
                         (sender["user_id"],))
        if pref and pref.get("language") == "en":
            lang = "en"
    except Exception:  # noqa: BLE001
        pass
    return send_whatsapp(phone, render(lang, "wa_plan_locked"),
                         tenant_id=sender["tenant_id"])


@router.post("/inbound")
async def inbound(request: Request, background: BackgroundTasks):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    url = _signed_url(request)

    # 1. Signature — invalid/missing → 403, no processing.
    if not verify_twilio_signature(url, params, signature, effective().twilio_auth_token):
        return Response(status_code=403)

    from_raw = params.get("From", "")
    body = params.get("Body", "") or ""
    message_sid = params.get("MessageSid", "") or ""
    phone = identity.normalize_phone(from_raw)

    # 3. Identity (idempotency needs the resolved user, so resolve first).
    sender = identity.resolve_sender(phone)
    if not sender:
        send_whatsapp(phone, _REJECT_UNKNOWN)
        return Response(status_code=200)

    # The bot is a paid feature (2026-10-05). A locked tenant's sender is told
    # so ONCE per day and nothing else happens: no state read, no model call, no
    # idempotency row. Not answering at all would read as "the bot is broken".
    from backend.entitlements.service import tenant_has_feature
    if not tenant_has_feature(sender["tenant_id"], "whatsapp_bot"):
        _tell_plan_locked(phone, sender)
        return Response(status_code=200)

    ctx = ToolContext(tenant_id=sender["tenant_id"], user_id=sender["user_id"], role=sender["role"])

    # A user limited to some warehouses is not served by the bot: its tools read
    # and write stock tenant-wide, and answering "only with their warehouses"
    # would mean re-scoping every one of them. Said plainly instead of leaking
    # company figures into a chat.
    from backend.auth import warehouse_scope as wscope
    from backend.auth.guards import CurrentUser
    if wscope.is_scoped(CurrentUser(ctx.user_id, ctx.tenant_id, ctx.role)):
        send_whatsapp(phone, render_es("wa_scoped_user"), tenant_id=ctx.tenant_id)
        return Response(status_code=200)

    # 2. Idempotency — a repeated MessageSid (Twilio retry) is a no-op.
    if message_sid and cs.is_duplicate(ctx.tenant_id, ctx.user_id, message_sid):
        return Response(status_code=200)

    # 4. Rate limit — over cap → friendly wait, no LLM call.
    if _rate_limited(phone):
        send_whatsapp(phone, _RATE_LIMITED, tenant_id=ctx.tenant_id)
        return Response(status_code=200)

    # 5-8. The turn runs AFTER Twilio has its 200, in a worker thread.
    #
    # A turn now reads the account and may call the model two or three times
    # (tools), which takes 5-20 s. Run inline, it outlived Twilio's 15 s webhook
    # timeout (logged there as error 11200) and, inside this async handler, it
    # blocked every other request to the API for as long as the model took.
    # The reply already goes out over REST, so nothing needs the HTTP response
    # to wait for it.
    background.add_task(_run_turn_and_reply, ctx, body, phone, message_sid)
    return Response(status_code=200)


def _run_turn_and_reply(ctx: ToolContext, body: str, phone: str, message_sid: str) -> None:
    """Load state, run the agent, persist, reply. A sync function, so Starlette
    runs it in its threadpool. Any failure still answers the person — silence
    on WhatsApp reads as "the bot is broken" with no way to tell why."""
    try:
        state = cs.load(ctx.tenant_id, ctx.user_id)
        reply, history, pending = agent.run_turn(ctx, body, state)
        cs.save(ctx.tenant_id, ctx.user_id, phone, history, pending, message_sid)
    except Exception:  # noqa: BLE001
        log.exception("[whatsapp] turn failed for tenant=%s", ctx.tenant_id)
        reply = render_es("wa_apology")
    # Reply via the existing outbound path (logged no-op without TWILIO creds).
    send_whatsapp(phone, reply, tenant_id=ctx.tenant_id)

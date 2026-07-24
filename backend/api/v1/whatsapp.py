"""
Inbound Twilio WhatsApp webhook. HTTP + wiring only — no business logic:
verify the Twilio signature, dedupe by MessageSid, resolve the sender to a
verified user, rate-limit per number, run the tool-calling agent, persist
state, and reply via the existing outbound send_whatsapp().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response

from backend.config import settings
from backend.db.connection import execute, query_one
from backend.notifications.whatsapp import send_whatsapp
from backend.whatsapp import agent, conversation_store as cs, identity
from backend.whatsapp.tools import ToolContext

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
log = logging.getLogger(__name__)

# Per-number cap: N inbound messages per rolling window (seconds).
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECS = 60

_REJECT_UNKNOWN = (
    "Hola 👋 No reconozco este número. Vincula tu WhatsApp desde tu perfil en "
    "Faro para poder ayudarte por aquí."
)
_RATE_LIMITED = "Vas muy rápido 🙏 Espera un momento y vuelve a escribirme."


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

      1. ``settings.whatsapp_webhook_base_url`` — authoritative external base.
      2. ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` set by the proxy.
      3. ``request.url`` — today's behaviour (local/dev, no proxy).

    Path and query always come from the actual request so any suffix/params are
    preserved regardless of which base is chosen.
    """
    path_qs = request.url.path
    if request.url.query:
        path_qs = f"{path_qs}?{request.url.query}"

    base = settings.whatsapp_webhook_base_url.strip()
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


@router.post("/inbound")
async def inbound(request: Request):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    url = _signed_url(request)

    # 1. Signature — invalid/missing → 403, no processing.
    if not verify_twilio_signature(url, params, signature, settings.twilio_auth_token):
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

    ctx = ToolContext(tenant_id=sender["tenant_id"], user_id=sender["user_id"], role=sender["role"])

    # 2. Idempotency — a repeated MessageSid (Twilio retry) is a no-op.
    if message_sid and cs.is_duplicate(ctx.tenant_id, ctx.user_id, message_sid):
        return Response(status_code=200)

    # 4. Rate limit — over cap → friendly wait, no LLM call.
    if _rate_limited(phone):
        send_whatsapp(phone, _RATE_LIMITED)
        return Response(status_code=200)

    # 5-7. Load state, run agent, persist.
    state = cs.load(ctx.tenant_id, ctx.user_id)
    reply, history, pending = agent.run_turn(ctx, body, state)
    cs.save(ctx.tenant_id, ctx.user_id, phone, history, pending, message_sid)

    # 8. Reply via the existing outbound path (logged no-op without TWILIO creds).
    send_whatsapp(phone, reply)
    return Response(status_code=200)

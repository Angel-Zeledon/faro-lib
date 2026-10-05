"""Sales by e-mail: the management endpoints and the provider webhook.

Two routers on purpose, because they are different kinds of door:

* `/inbound-email` (tag `inbound-email`) is the Configuracion card: the tenant
  admin reads the address and the last messages, edits the allowed senders and
  regenerates the address. Admin only, and NOT callable with an API key (the
  address is a credential).
* `/inbound/email` (tag `inbound-webhook`) is the mail provider's webhook. It
  has no user session; it is authenticated by the shared secret, and is never
  exposed to `sk_live_*` keys.

Webhook contract (see docs/inbound-email.md and `inbound_email/parse.py`):

  POST /api/v1/inbound/email
  Content-Type: application/json | multipart/form-data
  Authentication, either:
    X-StockAI-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
    Authorization: Basic base64("<anything>:" + secret)
  200 {outcome, ...}      processed (a rejected mail is still 200, so the provider does not retry)
  401 inbound_email_unauthorized, 413 inbound_email_too_large,
  415 inbound_email_unsupported_content_type, 400 inbound_email_malformed,
  503 inbound_email_disabled
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.requests import Request as StarletteRequest

from backend import audit
from backend.auth.guards import CurrentUser, require_admin
from backend.config import settings
from backend.errors import AppError
from backend.inbound_email import service as svc
from backend.inbound_email.ingest import process
from backend.inbound_email.parse import MalformedPayload, parse_form, parse_json
from backend.schemas.common import ok

log = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound-email", tags=["inbound-email"])
webhook_router = APIRouter(prefix="/inbound", tags=["inbound-webhook"])

# A signature older (or newer) than this is refused: a captured request cannot
# be replayed later. Idempotency on the message id covers a replay inside it.
REPLAY_WINDOW_SECONDS = 300
SIGNATURE_HEADER = "x-stockai-signature"
ALLOWED_CONTENT_TYPES = ("application/json", "multipart/form-data")


def max_body_bytes() -> int:
    """Largest webhook body: the upload ceiling plus base64's 4/3 and the
    envelope. A plan's own (smaller) ceiling is enforced per file later."""
    return int(settings.max_upload_size_mb * 1024 * 1024 * 1.4) + 1024 * 1024


def _disabled() -> AppError:
    return AppError(
        "inbound_email_disabled",
        "This installation cannot receive e-mail yet.",
        status_code=503,
    )


# ── Management (Configuracion > Datos) ────────────────────────────────────────

def _view(tenant_id: str) -> dict:
    if not svc.is_enabled():
        return {"enabled": False, "address": None, "allowed_senders": [],
                "messages": svc.list_messages(tenant_id)}
    row = svc.get_or_create(tenant_id)
    return {
        "enabled": True,
        "address": svc.address_for(row["token"]),
        "allowed_senders": row["allowed_senders"] or [],
        "messages": svc.list_messages(tenant_id),
    }


@router.get("")
def get_inbound_email(user: CurrentUser = Depends(require_admin)):
    return ok(_view(user.tenant_id))


class SendersRequest(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=svc.MAX_ALLOWED_SENDERS * 2)


@router.put("/senders")
def put_allowed_senders(
    body: SendersRequest, request: Request,
    user: CurrentUser = Depends(require_admin),
):
    if not svc.is_enabled():
        raise _disabled()
    before = len((svc.get_or_create(user.tenant_id)["allowed_senders"] or []))
    cleaned = svc.set_allowed_senders(user.tenant_id, body.emails)
    audit.note(request, label="allowed senders",
               before={"count": before}, after={"count": len(cleaned)})
    return ok(_view(user.tenant_id))


@router.post("/regenerate")
def regenerate_address(request: Request, user: CurrentUser = Depends(require_admin)):
    if not svc.is_enabled():
        raise _disabled()
    svc.regenerate(user.tenant_id)
    # Never the token itself: only the fact that the address changed.
    audit.note(request, label="inbound address")
    return ok(_view(user.tenant_id))


# ── Webhook ───────────────────────────────────────────────────────────────────

def verify_signature(secret: str, header: str, body: bytes, now: Optional[float] = None) -> bool:
    """`t=<unix>,v1=<hex>` over `"<t>." + body`. Constant-time compare, and the
    timestamp must be inside the replay window."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        stamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > REPLAY_WINDOW_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def verify_basic(secret: str, header: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password.encode(), secret.encode())


def _authorized(request: Request, body: bytes, secret: str) -> bool:
    signature = request.headers.get(SIGNATURE_HEADER)
    if signature:
        return verify_signature(secret, signature, body)
    basic = request.headers.get("authorization")
    return bool(basic) and verify_basic(secret, basic)


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large() -> AppError:
    return AppError(
        "inbound_email_too_large", "The message is too large.",
        status_code=413, params={"max_mb": round(max_body_bytes() / 1024 / 1024)},
    )


@webhook_router.post("/email")
async def receive_email(request: Request):
    secret = svc.inbound_secret()
    if not svc.is_enabled():
        raise _disabled()

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AppError(
            "inbound_email_unsupported_content_type",
            "Only application/json and multipart/form-data are accepted.",
            status_code=415, params={"content_type": content_type[:80]},
        )

    body = await _read_capped(request, max_body_bytes())

    # Authenticated BEFORE anything is parsed or looked up: an unauthenticated
    # caller learns nothing, not even whether an address exists.
    if not _authorized(request, body, secret):
        raise AppError(
            "inbound_email_unauthorized",
            "Missing or invalid webhook signature.", status_code=401,
        )

    try:
        if content_type == "application/json":
            email = parse_json(json.loads(body.decode("utf-8")))
        else:
            sent = body

            async def _replay():
                nonlocal sent
                chunk, sent = sent, b""
                return {"type": "http.request", "body": chunk, "more_body": False}

            form = await StarletteRequest(request.scope, _replay).form()
            fields: dict = {}
            files: list = []
            for key, value in form.multi_items():
                if hasattr(value, "filename") and hasattr(value, "read"):
                    files.append((value.filename or "", await value.read(),
                                  value.content_type or ""))
                else:
                    fields.setdefault(key, value)
            email = parse_form(fields, files)
    except (MalformedPayload, ValueError, UnicodeDecodeError) as exc:
        raise AppError(
            "inbound_email_malformed", "The message could not be read.",
            status_code=400,
        ) from exc

    result = await process(email, hashlib.sha256(body).hexdigest())
    return ok(result)

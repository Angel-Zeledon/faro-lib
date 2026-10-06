"""
The supplier's confirmation page API — public on purpose, credential = the link.

A supplier gets a link in the purchase-order message and answers it with no
account. Two routes, both unauthenticated:

    GET  /supplier-portal/{token}          what to answer (a whitelist: no prices)
    POST /supplier-portal/{token}/confirm  the answer, one entry per order line

What keeps an open route like this from being a hole (rules in
`inventory/po_confirmation_core.py`, door in `po_confirmation_service.py`):

* the token is 256 random bits and only its hash is stored;
* every bad link — malformed, unknown, revoked, expired, order cancelled —
  answers the identical 404, so nothing can be probed;
* rate limits per address AND per link, counted for every request whether or not
  the link is valid, so the 429 reveals nothing either;
* the body is capped before it is parsed, every field is bounded, and the notes
  are stored as typed and escaped wherever they are printed;
* the page tells the browser not to cache it, not to send the URL as a referrer
  and not to be indexed — the URL is the secret.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from backend.api.v1.auth import _check_rate
from backend.api.v1.trial import _client_address
from backend.config import settings
from backend.errors import AppError
from backend.inventory import po_confirmation_core as core
from backend.inventory import po_confirmation_service as svc
from backend.schemas.common import ok

router = APIRouter(prefix="/supplier-portal", tags=["supplier-portal"])

# (max requests, window seconds). Generous for a person on a phone, hopeless for
# a script: reading the page a few times, answering once or twice.
_READ_PER_ADDRESS = (120, 600)
_READ_PER_LINK = (60, 600)
_WRITE_PER_ADDRESS = (20, 600)
_WRITE_PER_LINK = (10, 600)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


def _limit(request: Request, token: str, kind: str) -> None:
    per_address, per_link = ((_READ_PER_ADDRESS, _READ_PER_LINK) if kind == "read"
                             else (_WRITE_PER_ADDRESS, _WRITE_PER_LINK))
    address = _client_address(request)
    _check_rate(f"supplier_portal:{kind}:ip:{address}",
                max_attempts=per_address[0], window_secs=per_address[1])
    if core.token_is_wellformed(token):
        # The key is a digest, never the token itself: rate rows must not become
        # a second copy of the credential.
        _check_rate(f"supplier_portal:{kind}:link:{core.hash_token(token)[:24]}",
                    max_attempts=per_link[0], window_secs=per_link[1])


@router.get("/{token}")
def get_portal(token: str, request: Request, response: Response):
    """The lines this supplier is asked to answer, and any answer already given."""
    _no_store(response)
    _limit(request, token, "read")
    return ok(svc.portal_view(token))


async def _read_capped(request: Request) -> bytes:
    """The request body, refusing anything over the cap before it is parsed."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > core.MAX_BODY_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > core.MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large() -> AppError:
    return AppError("supplier_portal_body_too_large", "The answer is too large",
                    status_code=413, params={"max_kb": core.MAX_BODY_BYTES // 1024})


@router.post("/{token}/confirm")
async def confirm(token: str, request: Request, response: Response):
    """Record the supplier's answer: for each line `{line_id, decision:
    'confirm'|'decline', confirmed_qty, promised_date, note}`. Every line must be
    answered. After this the page is locked until the buyer reopens it."""
    _no_store(response)
    await run_in_threadpool(_limit, request, token, "write")
    raw = await _read_capped(request)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        raise AppError("supplier_portal_invalid_submission",
                       "The answer could not be accepted", status_code=422,
                       params={"reason": "body_invalid"})
    ip_hash = core.hash_client_value(_client_address(request), settings.secret_key)
    result = await run_in_threadpool(
        svc.submit, token, payload.get("lines"),
        ip_hash=ip_hash, user_agent=request.headers.get("user-agent"),
    )
    return ok(result)

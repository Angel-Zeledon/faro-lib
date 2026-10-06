"""Service-to-service render endpoint for the Rust API's preview route.

`POST /internal/scheduled-reports/render` (note: NOT under `/api/v1`, so the
frontend proxy never forwards it) builds a report snapshot with the same
builder the worker uses and returns it together with the HTML the email would
carry. It exists because the sections stand on code that stays in Python (see
`builder.py`), and a preview that computed anything differently from the mail
would be worse than none.

Authentication is an HMAC of the installation's `SECRET_KEY` over the request
timestamp and the SHA-256 of the body, valid for 60 seconds, compared in
constant time. The caller (the Rust API) has already authenticated the person
and picked the tenant; this endpoint trusts nothing else about the request, and
without the key it answers 401 before reading the body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.scheduled_reports import builder, catalog, render

router = APIRouter(prefix="/internal/scheduled-reports", tags=["internal-render"])

MAX_SKEW_SECONDS = 60


def sign(secret: str, timestamp: str, body: bytes) -> str:
    msg = f"internal-render|{timestamp}|{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _verified(secret: str, timestamp: Optional[str], signature: Optional[str], body: bytes,
              now: Optional[float] = None) -> bool:
    if not timestamp or not signature:
        return False
    try:
        ts = float(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


@router.post("/render")
async def render_report(
    request: Request,
    x_internal_timestamp: Optional[str] = Header(default=None),
    x_internal_signature: Optional[str] = Header(default=None),
):
    body = await request.body()
    if not _verified(settings.secret_key, x_internal_timestamp, x_internal_signature, body):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    try:
        data = json.loads(body)
        tenant_id = data["tenant_id"]
        sections = data["sections"]
        frequency = data["frequency"]
    except (ValueError, KeyError, TypeError):
        return JSONResponse(status_code=422, content={"detail": "tenant_id, sections and frequency are required"})
    if (not isinstance(tenant_id, str) or not tenant_id or frequency not in catalog.FREQUENCIES
            or not isinstance(sections, list) or not sections
            or any(s not in catalog.SECTIONS for s in sections)):
        return JSONResponse(status_code=422, content={"detail": "invalid sections or frequency"})
    # A blocking build: run it off the event loop like every sync route.
    from starlette.concurrency import run_in_threadpool
    report = await run_in_threadpool(builder.build_report, tenant_id, sections, frequency)
    name = str(data.get("schedule_name") or "")
    report["schedule_name"] = name
    _, html = render.render_email(report, schedule_name=name, open_url="#", unsubscribe_url="#")
    return {"report": report, "html": html}

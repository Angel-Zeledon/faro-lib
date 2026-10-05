"""`POST /feedback` -- a signed-in person tells us what happened.

Open to every role, viewers included: reporting a problem does not change the
tenant's business data, and the person most likely to be stuck on a screen they
cannot act on is the one with the least permission. It is also open to a
read-only (expired-trial) tenant, for the same reason `/entitlements/
upgrade-request` is: refusing their message would be refusing to hear them.

It is an INTERNAL router tag (`backend/api/public_surface.py`): an `sk_live_*`
key never files a report, because a report names the person who read the
confirmation step and pressed Send.

The body is read by hand, with a hard cap, instead of letting the framework
buffer whatever arrives: the request carries a base64 screenshot, and "body
size cap" has to hold for a chunked upload that sends no Content-Length too.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

import backend.audit as audit
from backend.auth.guards import CurrentUser, get_current_user
from backend.errors import AppError
from backend.feedback import service
from backend.feedback.validation import MAX_BODY_BYTES, decode_screenshot
from backend.schemas.common import ok

router = APIRouter(prefix="/feedback", tags=["feedback"])
log = logging.getLogger(__name__)


class FeedbackBody(BaseModel):
    message: str = ""
    error_code: str | None = None
    page_path: str | None = None
    user_agent: str | None = None
    app_version: str | None = None
    # Base64 (or a data: URL) of a PNG/JPEG. Absent = the person sent none.
    screenshot: str | None = None
    # Two separate, optional consents. Both default to refused: an omitted
    # field is "no", never "yes".
    consent_reply: bool = False
    consent_news: bool = False


def _too_large() -> AppError:
    return AppError(
        "feedback_too_large", "The report is too large to send.", status_code=413,
    )


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", status_code=201)
async def send_feedback(request: Request, user: CurrentUser = Depends(get_current_user)):
    """Store a report (row + screenshot file), then e-mail the instance contact.

    Response: `{id, notified}`. `notified: false` means the report IS stored but
    the e-mail did not leave (transport off, no CONTACT_EMAIL, or a provider
    error); the screen tells the person it was saved and not that we were told.
    """
    raw = await _read_capped(request)
    try:
        body = FeedbackBody.model_validate(json.loads(raw or b"{}"))
    except (ValueError, ValidationError):
        raise AppError(
            "feedback_invalid", "The report could not be read.", status_code=422,
        )

    # Decoded and sniffed before anything is written.
    shot = decode_screenshot(body.screenshot)

    report = await run_in_threadpool(
        lambda: service.create_report(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            message=body.message,
            error_code=body.error_code,
            page_path=body.page_path,
            user_agent=body.user_agent,
            app_version=body.app_version,
            screenshot=shot,
            consent_reply=body.consent_reply,
            consent_news=body.consent_news,
        )
    )
    # Audit: who sent it and whether it carried a screenshot, never the text.
    audit.note(
        request, target_id=report["id"], label="feedback",
        after={
            "has_screenshot": shot is not None,
            "error_code": report["error_code"],
            "consent_reply": report["consent_reply"],
            "consent_news": report["consent_news"],
        },
    )
    notified = await run_in_threadpool(
        lambda: service.notify_instance_contact(
            report, tenant_id=user.tenant_id, screenshot=shot,
        )
    )
    return ok({"id": report["id"], "notified": notified})

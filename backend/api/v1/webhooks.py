import hashlib
import hmac
import json
import logging
import secrets
import threading

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.auth.guards import CurrentUser, get_current_user
from backend.db.connection import execute, query, query_one
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.schemas.common import ok

router = APIRouter(
    prefix="/webhooks", tags=["webhooks"],
    dependencies=[Depends(require_feature(Feature.WEBHOOKS))],
)
log = logging.getLogger(__name__)

SUPPORTED_EVENTS = {"job.completed", "job.failed", "accuracy.degraded"}


class CreateWebhookRequest(BaseModel):
    url:    str
    events: list[str]

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Webhook URL must start with https://")
        return v

    @field_validator("events")
    @classmethod
    def _valid_events(cls, v: list[str]) -> list[str]:
        bad = [e for e in v if e not in SUPPORTED_EVENTS]
        if bad:
            raise ValueError(f"Unsupported events: {bad}. Allowed: {sorted(SUPPORTED_EVENTS)}")
        if not v:
            raise ValueError("At least one event must be selected")
        return v


@router.post("")
def create_webhook(body: CreateWebhookRequest, user: CurrentUser = Depends(get_current_user)):
    secret = secrets.token_hex(32)
    execute(
        """INSERT INTO webhooks (id, tenant_id, url, events, secret)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s)""",
        (user.tenant_id, body.url, body.events, secret),
    )
    log.info("[webhooks] created url=%s tenant=%s", body.url, user.tenant_id)
    return ok({"url": body.url, "events": body.events})


@router.get("")
def list_webhooks(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        "SELECT id, url, events, created_at FROM webhooks WHERE tenant_id = %s ORDER BY created_at DESC",
        (user.tenant_id,),
    )
    return ok([dict(r) for r in rows])


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: str, user: CurrentUser = Depends(get_current_user)):
    row = query_one(
        "SELECT id FROM webhooks WHERE id = %s AND tenant_id = %s",
        (webhook_id, user.tenant_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Webhook not found")
    execute("DELETE FROM webhooks WHERE id = %s", (webhook_id,))
    return ok({"deleted": webhook_id})


# ── Internal fire helper (called from worker, not an HTTP route) ───────────────

def fire_webhooks(tenant_id: str, event: str, payload: dict) -> None:
    """Dispatch webhook deliveries in a background thread (non-blocking)."""
    def _dispatch():
        hooks = query(
            "SELECT url, secret FROM webhooks WHERE tenant_id = %s AND %s = ANY(events)",
            (tenant_id, event),
        )
        for hook in hooks:
            body_bytes = json.dumps(payload, default=str).encode()
            sig = hmac.new(hook["secret"].encode(), body_bytes, hashlib.sha256).hexdigest()
            try:
                httpx.post(
                    hook["url"],
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature": f"sha256={sig}",
                        "X-Event": event,
                    },
                    timeout=5,
                )
            except Exception as exc:
                log.warning("[webhook] delivery failed url=%s event=%s err=%s", hook["url"], event, exc)

    threading.Thread(target=_dispatch, daemon=True).start()

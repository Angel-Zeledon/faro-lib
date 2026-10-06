"""Outbound webhooks: manage them here, see `backend/webhooks/` for how events
are emitted and delivered (durable queue, signed, retried, SSRF-checked).

Webhooks are part of the paid API door: creating one, rotating its secret and
sending a test go through `ensure_feature(..., "api")`, the same check API keys
use - no separate gate. Reading and deleting stay open, so a tenant that lost the
feature can still see and remove what it had.

A webhook inherits the warehouse scope of whoever created it (or a narrower one
they ask for), and a scoped user manages only the hooks confined to their own
warehouses.
"""

import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
)
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.schemas.common import ok
from backend.webhooks import catalog, policy
from backend.webhooks import service as hook_service
# Kept importable from here: workers/runner.py and the event allowlist test
# have always imported these names from this module.
from backend.webhooks.catalog import SUPPORTED_EVENTS  # noqa: F401
from backend.webhooks.service import fire_webhooks  # noqa: F401

router = APIRouter(
    prefix="/webhooks", tags=["webhooks"],
)
log = logging.getLogger(__name__)

MAX_DELIVERIES_PAGE = 200


class CreateWebhookRequest(BaseModel):
    url:    str
    events: list[str]
    # Restrict the hook to some warehouses; omitted = the creator's own scope.
    warehouse_ids: Optional[list[str]] = None

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


def _not_found() -> AppError:
    return AppError("webhook_not_found", "Webhook not found", status_code=404)


def _present(row: dict) -> dict:
    """What the API shows of a webhook. The secret is never in this."""
    scope = policy.parse_scope(row.get("warehouse_scope"))
    return {
        "id": row["id"], "url": row["url"], "events": row["events"],
        "created_at": row["created_at"],
        "warehouse_ids": scope,
        "disabled_at": row.get("disabled_at"),
        "disabled_reason": row.get("disabled_reason"),
        "failure_days": row.get("failure_days") or 0,
        "secret_rotated_at": row.get("secret_rotated_at"),
    }


_LIST_COLUMNS = """id, url, events, created_at, warehouse_scope, disabled_at,
                   disabled_reason, failure_days, secret_rotated_at"""


def _managed_hook(user: CurrentUser, webhook_id: str) -> dict:
    """The webhook, or 404 - also when it exists but is outside the caller's
    warehouse scope (a scoped user must not learn it is there)."""
    row = query_one(
        f"SELECT {_LIST_COLUMNS} FROM webhooks WHERE id = %s AND tenant_id = %s",
        (webhook_id, user.tenant_id))
    if row is None or not policy.manageable_by(
            wscope.scope_ids(user), policy.parse_scope(row.get("warehouse_scope"))):
        raise _not_found()
    return row


@router.get("/events")
def list_event_types(user: CurrentUser = Depends(get_current_user)):
    """The events a webhook can subscribe to and the exact `data` keys each
    carries (envelope and headers: backend/webhooks/catalog.py)."""
    return ok({
        "api_version": catalog.API_VERSION,
        "events": [
            {"type": e.name, "data_keys": list(e.data_keys),
             "warehouse_aware": e.warehouse_aware}
            for e in catalog.EVENT_TYPES.values() if e.subscribable
        ],
    })


@router.post("")
def create_webhook(body: CreateWebhookRequest, user: CurrentUser = Depends(require_analyst_or_above)):
    from backend.entitlements.service import ensure_feature
    ensure_feature(user.tenant_id, "api")
    hook_service.validate_target(body.url)

    requested = wscope.validate_scope_ids(user.tenant_id, body.warehouse_ids)
    stored, outside = policy.narrow_scope(wscope.scope_ids(user), requested)
    if outside:
        row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                        (outside[0], user.tenant_id))
        raise wscope.denied((row or {}).get("name"))

    secret = secrets.token_hex(32)
    created = query_one(
        """INSERT INTO webhooks (id, tenant_id, url, events, secret, created_by, warehouse_scope)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s::jsonb)
           RETURNING id""",
        (user.tenant_id, body.url, body.events, secret, user.user_id,
         None if stored is None else json.dumps(stored)),
    )
    log.info("[webhooks] created url=%s tenant=%s", body.url, user.tenant_id)
    # The signing secret is returned exactly once; nothing shows it again.
    return ok({"id": created["id"], "url": body.url, "events": body.events,
               "warehouse_ids": stored, "secret": secret})


@router.get("")
def list_webhooks(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        f"SELECT {_LIST_COLUMNS} FROM webhooks WHERE tenant_id = %s ORDER BY created_at DESC",
        (user.tenant_id,),
    )
    mine = wscope.scope_ids(user)
    return ok([_present(dict(r)) for r in rows
               if policy.manageable_by(mine, policy.parse_scope(r.get("warehouse_scope")))])


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    _managed_hook(user, webhook_id)
    execute("DELETE FROM webhook_deliveries WHERE webhook_id = %s AND tenant_id = %s",
            (webhook_id, user.tenant_id))
    execute("DELETE FROM webhooks WHERE id = %s AND tenant_id = %s", (webhook_id, user.tenant_id))
    return ok({"deleted": webhook_id})


@router.post("/{webhook_id}/rotate-secret")
def rotate_secret(webhook_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    """Issue a new signing secret. The old one stops verifying at once: update
    the receiver right after. Returned once, like at creation."""
    from backend.entitlements.service import ensure_feature
    ensure_feature(user.tenant_id, "api")
    _managed_hook(user, webhook_id)
    secret = secrets.token_hex(32)
    execute("UPDATE webhooks SET secret = %s, secret_rotated_at = NOW() "
            "WHERE id = %s AND tenant_id = %s", (secret, webhook_id, user.tenant_id))
    return ok({"id": webhook_id, "secret": secret})


@router.post("/{webhook_id}/test")
def send_test_event(webhook_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    """Queue a `webhook.test` delivery to this webhook. It is delivered like any
    other (signed, logged) and answers with the delivery id to look up under
    `GET /webhooks/{id}/deliveries`."""
    from backend.entitlements.service import ensure_feature
    ensure_feature(user.tenant_id, "api")
    _managed_hook(user, webhook_id)
    delivery_id = hook_service.enqueue_test(user.tenant_id, webhook_id)
    hook_service.kick()
    return ok({"delivery_id": delivery_id})


@router.post("/{webhook_id}/enable")
def enable_webhook(webhook_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    """Switch a webhook back on after it was disabled for failing, and clear its
    failure streak. Events that happened while it was off are not replayed."""
    from backend.entitlements.service import ensure_feature
    ensure_feature(user.tenant_id, "api")
    _managed_hook(user, webhook_id)
    execute(
        """UPDATE webhooks SET disabled_at = NULL, disabled_reason = NULL,
                  failure_days = 0, last_failure_on = NULL
            WHERE id = %s AND tenant_id = %s""", (webhook_id, user.tenant_id))
    return ok({"id": webhook_id, "enabled": True})


@router.get("/{webhook_id}/deliveries")
def list_deliveries(
    webhook_id: str,
    status: Optional[str] = Query(None, pattern="^(pending|delivered|failed|abandoned)$"),
    limit: int = Query(50, ge=1, le=MAX_DELIVERIES_PAGE),
    user: CurrentUser = Depends(get_current_user),
):
    """The delivery log, newest first. The payload is not returned (it can be
    large and the receiver already has it); the event id is."""
    _managed_hook(user, webhook_id)
    clauses, params = ["webhook_id = %s", "tenant_id = %s"], [webhook_id, user.tenant_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    rows = query(
        f"""SELECT id, event_id, event_type, is_test, status, attempts, last_status_code,
                   last_error, next_attempt_at, created_at, last_attempt_at, delivered_at
              FROM webhook_deliveries WHERE {' AND '.join(clauses)}
             ORDER BY created_at DESC LIMIT %s""",
        tuple(params + [limit]))
    return ok([dict(r) for r in rows])

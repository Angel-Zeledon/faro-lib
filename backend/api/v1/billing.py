"""Billing routes.

Three shapes, on purpose:
  · reads      — any signed-in user may see which plan their company is on
  · writes     — starting checkout or opening the portal is admin-only, because
                 it commits the company to a charge
  · the hook   — no user at all: authorised by Stripe's signature, and the only
                 path in the whole app that changes `tenants.plan`
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator

from backend.auth.guards import CurrentUser, get_current_user, require_role
from backend.schemas.common import ok
from backend.billing import service as svc
from backend.config import settings

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = "monthly"

    @field_validator("interval")
    @classmethod
    def _interval(cls, v: str) -> str:
        if v not in ("monthly", "yearly"):
            raise ValueError("interval must be 'monthly' or 'yearly'")
        return v


def _app_url(path: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}{path}"


@router.get("/subscription")
def get_subscription(user: CurrentUser = Depends(get_current_user)):
    """Read-only, open to every role: a viewer being told which plan the company
    is on is not a privilege, and the upgrade prompts need it to render."""
    return ok(svc.subscription_state(user.tenant_id))


@router.post("/checkout")
def start_checkout(
    body: CheckoutRequest,
    # Admin only: this ends in a card being charged to the company.
    user: CurrentUser = Depends(require_role("admin")),
):
    """Returns a Stripe-hosted checkout URL. No card data reaches this server.

    Note what is NOT here: a price id from the caller. The client names a plan
    and an interval, and the server resolves the price from its own
    configuration — otherwise a client could check out against any price it
    liked, including one it created, and the webhook would honour whatever plan
    that price mapped to.
    """
    prices = svc.purchasable_plans().get(body.plan) or {}
    price_id = prices.get(body.interval)
    if not price_id:
        from backend.errors import AppError
        raise AppError(
            "billing_plan_not_for_sale",
            f"Plan '{body.plan}' cannot be bought here on a {body.interval} interval.",
            status_code=400,
            params={"plan": body.plan, "interval": body.interval},
        )

    # CurrentUser carries the id, not the address, and Stripe wants an email for
    # the receipt — so it is read here rather than trusted from the request.
    from backend.db.connection import query_one
    row = query_one("SELECT email FROM users WHERE id = %s", (user.user_id,))

    url = svc.create_checkout_session(
        tenant_id=user.tenant_id,
        email=(row or {}).get("email") or "",
        price_id=price_id,
        success_url=_app_url("/mi-cuenta?checkout=done"),
        cancel_url=_app_url("/mi-cuenta?checkout=cancelled"),
    )
    log.info("[billing] checkout started tenant=%s plan=%s", user.tenant_id, body.plan)
    return ok({"url": url})


@router.post("/portal")
def open_portal(user: CurrentUser = Depends(require_role("admin"))):
    """Stripe's own portal: cards, invoices, cancellation."""
    url = svc.create_portal_session(user.tenant_id, return_url=_app_url("/mi-cuenta"))
    return ok({"url": url})


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe's callback. Authorised by signature, never by a session.

    The raw body is required — Stripe signs the bytes, so parsing first and
    re-serialising would break verification.

    Always answers 200 once the signature is good, even when the event is one we
    ignore. A non-2xx makes Stripe retry, and retrying an event we deliberately
    do not handle achieves nothing except noise.
    """
    payload = await request.body()
    event = svc.verify_event(payload, request.headers.get("stripe-signature"))
    result = svc.handle_event(event)
    return ok(result)

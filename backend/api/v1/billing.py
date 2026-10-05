"""Buying the Full plan online: status, checkout, manage — and the webhooks.

Hosted pages only. `POST /billing/checkout` answers a provider URL the browser
is sent to; no card number, CVC or PayPal password ever reaches this server or
the app's JavaScript. The tier changes ONLY in `backend/billing/service.py`,
reached from a webhook whose signature was verified (or from the hourly sweep
applying what such a webhook already stored).

With no provider configured, `GET /billing/status` says payments are off and
names the variables to set; checkout refuses with `billing_not_configured`; the
app keeps showing the "write to us" dialog. Nothing 500s.

Tags: `billing` (signed-in people) and `billing-webhook` (the providers, and
the public offer the pricing page reads) are both INTERNAL in
`api/public_surface.py`: an API key can never start a payment.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend import audit
from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin, require_verified_admin,
)
from backend.billing import paypal_api, providers, service, stripe_api, webhooks
from backend.billing.entitlement import grants_access, purchase_block
from backend.billing.providers import PAYPAL, STRIPE, ProviderError
from backend.db.connection import query_one
from backend.entitlements.plans import FREE
from backend.errors import AppError
from backend.schemas.common import ok

log = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])
webhook_router = APIRouter(prefix="/billing", tags=["billing-webhook"])

# A provider event is a few kilobytes. Anything past this is not one.
WEBHOOK_MAX_BYTES = 1024 * 1024


def _frontend_base() -> str:
    from backend.service_config.resolver import effective
    return str(effective().frontend_url or "").rstrip("/")


def _tenant(tenant_id: str) -> dict:
    return query_one(
        "SELECT id, name, tier, tier_source FROM tenants WHERE id = %s", (tenant_id,),
    ) or {"tier": FREE, "tier_source": "manual"}


def _payments(cfg: providers.BillingConfig, *, show_missing: bool) -> dict:
    enabled = cfg.enabled_providers()
    return {
        "enabled": bool(enabled),
        "providers": enabled,
        # Variable NAMES only, never a value — and only to a tenant admin, the
        # person who can ask the operator for them. Same line the capabilities
        # endpoint draws: a viewer learns "off", not which credential is absent.
        "missing": ({p: cfg.missing(p) for p in providers.PROVIDERS}
                    if show_missing else None),
        "price_usd_monthly": cfg.price_usd_full if cfg.price_ok else None,
        "currency": providers.CURRENCY,
    }


def _status(tenant_id: str, *, show_missing: bool = False) -> dict:
    cfg = providers.load()
    now = service.utcnow()
    tenant = _tenant(tenant_id)
    rows = service.subscriptions_for(tenant_id)
    governing = service.governing_subscription(rows, now)
    has_access = any(grants_access(service.state_of(r), now) for r in rows)
    payments = _payments(cfg, show_missing=show_missing)
    block = purchase_block(tenant.get("tier") or FREE, tenant.get("tier_source"),
                           has_access, payments["enabled"])
    manageable = bool(governing) and (
        (governing["provider"] == STRIPE and service.customer_id_for(tenant_id, STRIPE))
        or (governing["provider"] == PAYPAL and governing.get("status") != "approval_pending")
    )
    return {
        "tier": tenant.get("tier") or FREE,
        "tier_source": tenant.get("tier_source") or "manual",
        "payments": payments,
        "can_purchase": block is None,
        "purchase_block": block,
        "subscription": service.describe(governing, now),
        "can_manage": bool(manageable),
    }


@router.get("/status")
def billing_status(user: CurrentUser = Depends(get_current_user)):
    """Plan, subscription state and whether online payment is available.
    Any signed-in person: a viewer is told when the account is past due too."""
    return ok(_status(user.tenant_id, show_missing=user.role == "admin"))


class CheckoutRequest(BaseModel):
    provider: Literal["stripe", "paypal"]


def _idempotency_key(*parts: str) -> str:
    # Same person, same tenant, same provider, same minute -> same key, so a
    # double click opens one checkout, not two.
    bucket = str(int(time.time() // 60))
    return hashlib.sha256("|".join((*parts, bucket)).encode()).hexdigest()[:48]


def _provider_unavailable(provider: str) -> AppError:
    return AppError(
        "billing_provider_unavailable",
        "The payment provider could not be reached. Nothing was charged; try again.",
        status_code=502, params={"provider": provider},
    )


@router.post("/checkout")
def create_checkout(
    body: CheckoutRequest, request: Request,
    # An admin with a verified address: the receipt and every later notice go
    # to the account, and paying is the account owner's decision. Not the
    # read-only guard: a suspended tenant paying is the way out of suspension.
    user: CurrentUser = Depends(require_verified_admin),
):
    cfg = providers.load()
    if not cfg.is_configured(body.provider):
        raise AppError(
            "billing_provider_not_configured",
            "This payment provider is not configured on this installation.",
            status_code=409,
            params={"provider": body.provider, "missing": cfg.missing(body.provider)},
        )
    status_now = _status(user.tenant_id)
    if status_now["purchase_block"]:
        raise AppError(
            status_now["purchase_block"],
            "This account cannot start a checkout right now.",
            status_code=409, params={"tier": status_now["tier"]},
        )
    base = _frontend_base()
    success_url = f"{base}/facturacion?checkout=success&provider={body.provider}"
    cancel_url = f"{base}/facturacion?checkout=cancelled&provider={body.provider}"
    key = _idempotency_key(user.tenant_id, user.user_id, body.provider)

    try:
        if body.provider == STRIPE:
            who = query_one("SELECT email FROM users WHERE id = %s", (user.user_id,)) or {}
            session = stripe_api.create_checkout_session(
                cfg, tenant_id=user.tenant_id,
                success_url=success_url, cancel_url=cancel_url,
                customer_id=service.customer_id_for(user.tenant_id, STRIPE),
                customer_email=who.get("email"), idempotency_key=key,
            )
            url = session["url"]
        else:
            created = paypal_api.create_subscription(
                cfg, tenant_id=user.tenant_id,
                return_url=success_url, cancel_url=cancel_url, request_id=key,
            )
            # Pending until PayPal's webhook says otherwise; it lets that
            # webhook find this tenant even before the buyer approves.
            service.record_pending_subscription(user.tenant_id, PAYPAL, created["id"])
            url = created["url"]
    except ProviderError as exc:
        raise _provider_unavailable(exc.provider) from None

    audit.note(request, label=body.provider, after={"provider": body.provider})
    return ok({"provider": body.provider, "url": url})


class PortalRequest(BaseModel):
    provider: Optional[Literal["stripe", "paypal"]] = None


@router.post("/portal")
def open_portal(
    request: Request, body: Optional[PortalRequest] = None,
    user: CurrentUser = Depends(require_admin),
):
    """Where the subscription is managed or cancelled: Stripe's hosted
    Customer Portal, or the subscriber's own PayPal account page."""
    cfg = providers.load()
    now = service.utcnow()
    rows = service.subscriptions_for(user.tenant_id)
    wanted = body.provider if body else None
    if wanted:
        rows = [r for r in rows if r["provider"] == wanted]
    rows = [r for r in rows if r.get("status") != "approval_pending"]
    governing = service.governing_subscription(rows, now)
    if not governing:
        raise AppError("billing_no_subscription",
                       "This account has no subscription to manage.", status_code=404)
    provider = governing["provider"]
    if not cfg.is_configured(provider):
        raise AppError(
            "billing_provider_not_configured",
            "This payment provider is not configured on this installation.",
            status_code=409, params={"provider": provider, "missing": cfg.missing(provider)},
        )
    if provider == STRIPE:
        customer = service.customer_id_for(user.tenant_id, STRIPE)
        if not customer:
            raise AppError("billing_no_subscription",
                           "This account has no subscription to manage.", status_code=404)
        try:
            url = stripe_api.create_portal_session(
                cfg, customer_id=customer, return_url=f"{_frontend_base()}/facturacion")
        except ProviderError as exc:
            raise _provider_unavailable(exc.provider) from None
    else:
        url = cfg.paypal_manage_url
    audit.note(request, label=provider)
    return ok({"provider": provider, "url": url})


# ── Public: what the pricing page may honestly offer ─────────────────────────

@webhook_router.get("/offer")
def billing_offer():
    """Whether the Full plan can be bought online on this installation, and at
    what monthly price. Unauthenticated: the landing reads it to decide whether
    a 'start the Full plan' button would be telling the truth."""
    cfg = providers.load()
    enabled = cfg.enabled_providers()
    return ok({
        "online_checkout": bool(enabled),
        "providers": enabled,
        "price_usd_monthly": cfg.price_usd_full if (enabled and cfg.price_ok) else None,
        "currency": providers.CURRENCY,
    })


# ── Webhooks ──────────────────────────────────────────────────────────────────

async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > WEBHOOK_MAX_BYTES:
        raise AppError("billing_webhook_too_large", "Payload too large.", status_code=413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > WEBHOOK_MAX_BYTES:
            raise AppError("billing_webhook_too_large", "Payload too large.", status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)


def _run_webhook(provider: str, fn) -> dict:
    try:
        outcome = fn()
    except webhooks.NotConfigured:
        raise AppError(
            "billing_provider_not_configured",
            "This payment provider is not configured on this installation.",
            status_code=503, params={"provider": provider},
        ) from None
    except webhooks.InvalidWebhook as exc:
        # Forged, stale or tampered: refused before anything was read or
        # written. 400 so the sender knows it was not accepted.
        log.warning("[billing] %s webhook refused: %s", provider, exc.code)
        raise AppError(exc.code, "Webhook refused.", status_code=400,
                       params={"provider": provider}) from None
    except webhooks.RetryLater:
        raise AppError(
            "billing_provider_unavailable",
            "Could not confirm the event with the provider; deliver it again.",
            status_code=503, params={"provider": provider},
        ) from None
    return {"received": True, "outcome": outcome}


@webhook_router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    body = await _read_capped(request)
    signature = request.headers.get("stripe-signature")
    cfg = providers.load()
    # The handler talks to the database and to Stripe synchronously: off the
    # event loop, so one slow provider call never stalls every other request.
    return await run_in_threadpool(
        _run_webhook, STRIPE, lambda: webhooks.handle_stripe(cfg, body, signature))


@webhook_router.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    body = await _read_capped(request)
    cfg = providers.load()
    headers = dict(request.headers)
    return await run_in_threadpool(
        _run_webhook, PAYPAL, lambda: webhooks.handle_paypal(cfg, body, headers))

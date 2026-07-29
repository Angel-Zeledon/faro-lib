"""Stripe billing: checkout, the customer portal, and the webhook that is the
ONLY thing allowed to change what a tenant may do.

The rule this module exists to enforce: `tenants.plan` is never written from a
request the browser can make. A client that could ask for its own plan to change
is a client that can hand itself the Professional feature set for free, and the
entitlement layer would faithfully honour it. So the plan moves on exactly one
path — a webhook whose signature Stripe signed — and every other endpoint here
either starts a Stripe-hosted flow or reads state back.

Nothing in this module is reachable without STRIPE_SECRET_KEY. With no key the
API reports that billing is not configured, which is the same shape the
notification senders use for a missing RESEND_API_KEY.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.config import settings
from backend.db.connection import execute, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

# Stripe's subscription statuses that mean "this tenant is paid up". Anything
# else and the tenant falls back to what it can have without paying.
_ENTITLING = {"active", "trialing"}

# Which plan a price buys. Read from configuration, because a price ID is
# different in test and live mode and the mapping is a commercial decision.
def _price_to_plan() -> dict[str, str]:
    out: dict[str, str] = {}
    for price in (settings.stripe_price_professional_monthly,
                  settings.stripe_price_professional_yearly):
        if price:
            out[price] = "professional"
    return out


def _require_stripe():
    """The SDK, configured, or a refusal that says which key is missing."""
    if not settings.billing_enabled:
        raise AppError(
            "billing_not_configured",
            "Billing is not configured on this deployment.",
            status_code=503,
        )
    import stripe
    stripe.api_key = settings.stripe_secret_key
    return stripe


def plan_for_price(price_id: Optional[str]) -> Optional[str]:
    return _price_to_plan().get(price_id or "")


def purchasable_plans() -> dict[str, dict]:
    """What this deployment can actually sell today. A plan with no configured
    price is not offered, rather than offered and then failing at checkout."""
    out: dict[str, dict] = {}
    if settings.stripe_price_professional_monthly:
        out.setdefault("professional", {})["monthly"] = \
            settings.stripe_price_professional_monthly
    if settings.stripe_price_professional_yearly:
        out.setdefault("professional", {})["yearly"] = \
            settings.stripe_price_professional_yearly
    return out


# ------ Customer ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _tenant(tenant_id: str) -> dict:
    row = query_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
    if not row:
        raise ValueError(f"Tenant {tenant_id} not found")
    return row


def ensure_customer(tenant_id: str, email: str) -> str:
    """The tenant's Stripe customer id, created on first use.

    `tenant_id` goes into metadata because the webhook has to get back from a
    Stripe object to a row in this database, and an email is not an identity —
    people change them, and two tenants can share one.
    """
    stripe = _require_stripe()
    t = _tenant(tenant_id)
    existing = t.get("stripe_customer_id")
    if existing:
        return existing

    customer = stripe.Customer.create(
        email=email,
        name=t.get("name") or tenant_id,
        metadata={"tenant_id": tenant_id},
    )
    execute("UPDATE tenants SET stripe_customer_id = %s WHERE id = %s",
            (customer.id, tenant_id))
    log.info("[billing] created Stripe customer %s for tenant %s", customer.id, tenant_id)
    return customer.id


# ------ Hosted flows ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def create_checkout_session(tenant_id: str, email: str, price_id: str,
                            success_url: str, cancel_url: str) -> str:
    """A Stripe-hosted checkout. Card details never touch this server.

    The price must be one this deployment is configured to sell: taking an
    arbitrary price id from the caller would let a client check out against a
    price of their choosing — including one they created — and the webhook would
    then map it to whatever plan that price says.
    """
    stripe = _require_stripe()
    if price_id not in _price_to_plan():
        raise AppError(
            "billing_unknown_price",
            "That price is not offered by this deployment.",
            status_code=400,
            params={"price_id": price_id},
        )

    customer_id = ensure_customer(tenant_id, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        # Repeated on the subscription so `customer.subscription.*` events can
        # resolve the tenant without a second API call.
        subscription_data={"metadata": {"tenant_id": tenant_id}},
        metadata={"tenant_id": tenant_id},
        client_reference_id=tenant_id,
    )
    return session.url


def create_portal_session(tenant_id: str, return_url: str) -> str:
    """Stripe's own billing portal: card changes, invoices, cancellation.

    Deliberately not rebuilt in-app. Payment-method UI and dunning are exactly
    the surfaces where a home-grown version is worse and riskier.
    """
    stripe = _require_stripe()
    t = _tenant(tenant_id)
    customer_id = t.get("stripe_customer_id")
    if not customer_id:
        raise AppError(
            "billing_no_customer",
            "This tenant has no billing account yet.",
            status_code=409,
        )
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url)
    return session.url


# ------ Webhook ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def verify_event(payload: bytes, signature: Optional[str]) -> dict:
    """Stripe's signature, or nothing.

    Without STRIPE_WEBHOOK_SECRET this refuses outright instead of parsing the
    body. An endpoint that trusts an unsigned body is an endpoint where anyone
    who can reach it can post `customer.subscription.updated` and give
    themselves the Professional feature set.
    """
    stripe = _require_stripe()
    if not settings.stripe_webhook_secret:
        raise AppError(
            "billing_webhook_not_configured",
            "Webhook signature verification is not configured.",
            status_code=503,
        )
    if not signature:
        raise AppError("billing_signature_missing", "Missing signature.",
                       status_code=400)
    try:
        return stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret)
    except Exception as exc:                      # SignatureVerificationError et al
        log.warning("[billing] rejected webhook: %s", exc)
        raise AppError("billing_signature_invalid", "Invalid signature.",
                       status_code=400)


def _claim_event(event: dict) -> bool:
    """Record the delivery. False means we have seen this event id before.

    Stripe retries until it gets a 2xx and can deliver a success twice, so the
    handler must be idempotent. The primary key does that work: the INSERT is
    the claim, and a conflict means somebody already applied it.
    """
    rows = query_one(
        """INSERT INTO stripe_events (id, type, tenant_id, payload)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING
           RETURNING id""",
        (event["id"], event.get("type", ""), _tenant_id_of(event),
         __import__("json").dumps(event.get("data", {}).get("object", {}))[:200000]),
    )
    return rows is not None


def _tenant_id_of(event: dict) -> Optional[str]:
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if meta.get("tenant_id"):
        return meta["tenant_id"]
    if obj.get("client_reference_id"):
        return obj["client_reference_id"]
    # Subscription events carry the customer, not the tenant.
    customer = obj.get("customer")
    if isinstance(customer, str):
        row = query_one("SELECT id FROM tenants WHERE stripe_customer_id = %s",
                        (customer,))
        if row:
            return row["id"]
    return None


def _apply_subscription(tenant_id: str, sub: dict) -> str:
    """Move the tenant onto (or off) the plan its subscription entitles it to."""
    status = sub.get("status") or ""
    price_id = None
    items = ((sub.get("items") or {}).get("data") or [])
    if items:
        price_id = ((items[0].get("price") or {}).get("id"))

    plan = plan_for_price(price_id)
    if status in _ENTITLING and plan:
        # Paid: the plan the price buys, and the trial clock stops mattering.
        execute(
            """UPDATE tenants
               SET plan = %s, subscription_status = %s,
                   stripe_subscription_id = %s, trial_ends_at = NULL
               WHERE id = %s""",
            (plan, status, sub.get("id"), tenant_id))
        log.info("[billing] tenant %s -> %s (%s)", tenant_id, plan, status)
        return plan

    # Not paying. Fall back to what a tenant gets without paying rather than
    # cutting access off: their data stays readable and their limits shrink,
    # which is a conversation, not a lockout.
    execute(
        """UPDATE tenants
           SET plan = 'starter', subscription_status = %s,
               stripe_subscription_id = %s
           WHERE id = %s""",
        (status or "canceled", sub.get("id"), tenant_id))
    log.info("[billing] tenant %s -> starter (%s)", tenant_id, status or "canceled")
    return "starter"


# Events that can change what a tenant may do. Anything else is recorded and
# ignored, so a webhook configured with more events than this cannot surprise us.
HANDLED = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


def handle_event(event: dict) -> dict:
    """Apply one verified event. Safe to call twice with the same event."""
    if not _claim_event(event):
        return {"status": "duplicate", "event": event["id"]}

    etype = event.get("type", "")
    tenant_id = _tenant_id_of(event)
    result: dict = {"status": "ignored", "type": etype}

    if etype in HANDLED and tenant_id:
        obj = (event.get("data") or {}).get("object") or {}
        result = {"status": "applied", "type": etype,
                  "tenant_id": tenant_id, "plan": _apply_subscription(tenant_id, obj)}
    elif etype in HANDLED:
        # Signed by Stripe but not traceable to a tenant. Recorded, never
        # guessed at: applying it to the wrong tenant is worse than dropping it.
        log.warning("[billing] %s could not be matched to a tenant (event %s)",
                    etype, event["id"])
        result = {"status": "unmatched", "type": etype}

    execute("UPDATE stripe_events SET applied_at = NOW() WHERE id = %s",
            (event["id"],))
    return result


def subscription_state(tenant_id: str) -> dict:
    """What the app should show about this tenant's billing."""
    t = _tenant(tenant_id)
    return {
        "plan": t.get("plan"),
        "subscription_status": t.get("subscription_status"),
        "has_billing_account": bool(t.get("stripe_customer_id")),
        "trial_ends_at": t.get("trial_ends_at").isoformat()
                         if t.get("trial_ends_at") else None,
        "billing_enabled": settings.billing_enabled,
        "purchasable": purchasable_plans(),
    }

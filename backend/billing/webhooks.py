"""Verified provider event -> fresh provider subscription -> service.apply_event.

Two rules every path below keeps:

* **Nothing is read from a request before its signature is verified.** An
  unverified body is not parsed for ids, not looked up, not recorded.
* **The event says WHICH subscription changed; the provider says HOW.** The
  subscription is fetched from the provider's API with our own credentials
  and that copy is what gets stored. Two events delivered in the wrong order
  therefore both apply the same, current truth — and `service.apply_event`
  still refuses an event older than the newest one it applied.

A provider that cannot be reached is a `RetryLater` (HTTP 503): both Stripe
and PayPal deliver again, and nothing was recorded, so the retry is processed
for real.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.billing import paypal_api, service, stripe_api
from backend.billing.providers import PAYPAL, STRIPE, BillingConfig, ProviderError
from backend.billing.signatures import verify_stripe_signature

log = logging.getLogger(__name__)

STRIPE_EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
})

PAYPAL_EVENTS = frozenset({
    "BILLING.SUBSCRIPTION.ACTIVATED",
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
    "BILLING.SUBSCRIPTION.EXPIRED",
    "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
    "BILLING.SUBSCRIPTION.RE-ACTIVATED",
    "BILLING.SUBSCRIPTION.UPDATED",
    "PAYMENT.SALE.COMPLETED",
})


class NotConfigured(Exception):
    """The provider is off here: nothing can be verified, nothing is done."""


class InvalidWebhook(Exception):
    """Signature missing, wrong, stale, or the body is not an event."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RetryLater(Exception):
    """The provider could not be asked; let it deliver again."""


def _stripe_ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _paypal_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _resolve_tenant(provider: str, sub_id: str, customer_id: str,
                    hints: list[str]) -> Optional[str]:
    """Which tenant an event is about. What WE stored wins over what the
    event carries: a subscription or customer already linked to a tenant stays
    linked to it. The hints are the tenant id we put in the provider's own
    metadata / custom_id when the checkout was created."""
    return (
        service.tenant_for_subscription(provider, sub_id)
        or service.tenant_for_customer(provider, customer_id)
        or next((h for h in hints if isinstance(h, str) and h), None)
    )


def _decode(body: bytes) -> dict:
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidWebhook("billing_webhook_malformed") from None
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise InvalidWebhook("billing_webhook_malformed")
    return event


# ── Stripe ───────────────────────────────────────────────────────────────────

def _stripe_target(event_type: str, obj: dict) -> tuple[str, str, list[str]]:
    """(subscription id, customer id, tenant hints) named by a Stripe event."""
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    customer = customer or ""
    if event_type == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "", customer, []
        sub = obj.get("subscription")
        if isinstance(sub, dict):
            sub = sub.get("id")
        return (sub or "", customer,
                [obj.get("client_reference_id") or "",
                 (obj.get("metadata") or {}).get("tenant_id") or ""])
    if event_type.startswith("customer.subscription."):
        return (obj.get("id") or "", customer,
                [(obj.get("metadata") or {}).get("tenant_id") or ""])
    # invoice.*: the subscription moved under `parent` in newer API versions.
    sub = obj.get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    details = ((obj.get("parent") or {}).get("subscription_details") or {})
    sub = sub or details.get("subscription") or ""
    hint = ((details.get("metadata") or {}).get("tenant_id")
            or ((obj.get("subscription_details") or {}).get("metadata") or {}).get("tenant_id")
            or "")
    return sub, customer, [hint]


def handle_stripe(cfg: BillingConfig, body: bytes, signature: Optional[str]) -> str:
    if not cfg.is_configured(STRIPE):
        raise NotConfigured()
    if not verify_stripe_signature(cfg.value("stripe_webhook_secret"), signature, body):
        raise InvalidWebhook("billing_webhook_signature_invalid")
    event = _decode(body)
    event_id = str(event["id"])
    event_type = str(event.get("type") or "")
    created = _stripe_ts(event.get("created"))

    if service.event_seen(STRIPE, event_id):
        return "duplicate"
    if event_type not in STRIPE_EVENTS:
        return service.record_unattributed(STRIPE, event_id, event_type, created,
                                           "ignored_unhandled_type")

    obj = ((event.get("data") or {}).get("object")) or {}
    sub_id, customer, hints = _stripe_target(event_type, obj)
    if not sub_id:
        return service.record_unattributed(STRIPE, event_id, event_type, created,
                                           "ignored_no_subscription")
    try:
        fresh = stripe_api.normalize_subscription(
            stripe_api.retrieve_subscription(cfg, sub_id))
    except ProviderError as exc:
        raise RetryLater() from exc
    if not fresh["provider_subscription_id"]:
        raise RetryLater()
    customer = fresh.get("provider_customer_id") or customer
    fresh["provider_customer_id"] = customer
    tenant_id = _resolve_tenant(STRIPE, sub_id, customer, [fresh.get("tenant_id"), *hints])
    if not tenant_id:
        return service.record_unattributed(STRIPE, event_id, event_type, created,
                                           "ignored_unknown_tenant")
    return service.apply_event(STRIPE, event_id, event_type, created, fresh, tenant_id).outcome


# ── PayPal ───────────────────────────────────────────────────────────────────

def handle_paypal(cfg: BillingConfig, body: bytes, headers: Any) -> str:
    if not cfg.is_configured(PAYPAL):
        raise NotConfigured()
    transmission = paypal_api.webhook_headers(headers)
    if transmission is None:
        raise InvalidWebhook("billing_webhook_signature_invalid")
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidWebhook("billing_webhook_malformed") from None
    if not isinstance(event, dict):
        raise InvalidWebhook("billing_webhook_malformed")
    try:
        token = paypal_api.access_token(cfg)
        verified = paypal_api.verify_webhook(cfg, transmission, event, token)
    except ProviderError as exc:
        raise RetryLater() from exc
    if not verified:
        raise InvalidWebhook("billing_webhook_signature_invalid")

    event_id = str(event.get("id") or "")
    event_type = str(event.get("event_type") or "")
    if not event_id or not event_type:
        raise InvalidWebhook("billing_webhook_malformed")
    created = _paypal_ts(event.get("create_time"))

    if service.event_seen(PAYPAL, event_id):
        return "duplicate"
    if event_type not in PAYPAL_EVENTS:
        return service.record_unattributed(PAYPAL, event_id, event_type, created,
                                           "ignored_unhandled_type")

    resource = event.get("resource") or {}
    if event_type == "PAYMENT.SALE.COMPLETED":
        sub_id = resource.get("billing_agreement_id") or ""
        hint = resource.get("custom") or resource.get("custom_id") or ""
    else:
        sub_id = resource.get("id") or ""
        hint = resource.get("custom_id") or ""
    if not sub_id:
        return service.record_unattributed(PAYPAL, event_id, event_type, created,
                                           "ignored_no_subscription")
    try:
        fresh = paypal_api.normalize_subscription(
            paypal_api.get_subscription(cfg, sub_id, token))
    except ProviderError as exc:
        raise RetryLater() from exc
    if not fresh["provider_subscription_id"]:
        raise RetryLater()
    tenant_id = _resolve_tenant(PAYPAL, sub_id, fresh.get("provider_customer_id") or "",
                                [fresh.get("tenant_id"), hint])
    if not tenant_id:
        return service.record_unattributed(PAYPAL, event_id, event_type, created,
                                           "ignored_unknown_tenant")
    return service.apply_event(PAYPAL, event_id, event_type, created, fresh, tenant_id).outcome

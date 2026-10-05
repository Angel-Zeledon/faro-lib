"""The four Stripe calls billing needs, over plain httpx.

Stripe's API is form-encoded REST with a bearer key; the official SDK would be
a dependency for four requests. Nested parameters are sent with Stripe's own
bracket syntax (`line_items[0][price]`).

Nothing here logs a request or a response body: both can carry the secret key
or a customer's email. Failures raise `ProviderError`, which carries only the
operation and the HTTP status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend.billing.providers import (
    HTTP_TIMEOUT_SECONDS, STRIPE, STRIPE_API_BASE, BillingConfig, ProviderError,
)

log = logging.getLogger(__name__)


def _request(cfg: BillingConfig, method: str, path: str, operation: str, *,
             data: Optional[list[tuple[str, str]]] = None,
             idempotency_key: Optional[str] = None) -> dict:
    headers = {"Authorization": f"Bearer {cfg.value('stripe_secret_key')}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = client.request(method, f"{STRIPE_API_BASE}{path}",
                                  data=data, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("stripe %s unreachable: %s", operation, type(exc).__name__)
        raise ProviderError(STRIPE, operation, kind="unreachable") from None
    if resp.status_code >= 400:
        # The error TYPE only (e.g. invalid_request_error), never the message:
        # Stripe echoes parameters back in it.
        err_type = ""
        try:
            err_type = (resp.json().get("error") or {}).get("type", "")
        except ValueError:
            pass
        log.warning("stripe %s rejected: status=%s type=%s",
                    operation, resp.status_code, err_type)
        raise ProviderError(STRIPE, operation, resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(STRIPE, operation, resp.status_code, kind="malformed") from None


def create_checkout_session(
    cfg: BillingConfig, *, tenant_id: str, success_url: str, cancel_url: str,
    customer_id: Optional[str] = None, customer_email: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """A hosted Checkout Session for the Full plan. Returns {'id', 'url'}.

    The tenant id travels three ways — `client_reference_id`, the session's
    metadata and the SUBSCRIPTION's metadata — because the subscription
    outlives the session and every later webhook is about the subscription.
    """
    data: list[tuple[str, str]] = [
        ("mode", "subscription"),
        ("line_items[0][price]", cfg.value("stripe_price_id_full")),
        ("line_items[0][quantity]", "1"),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("client_reference_id", tenant_id),
        ("metadata[tenant_id]", tenant_id),
        ("subscription_data[metadata][tenant_id]", tenant_id),
        ("allow_promotion_codes", "false"),
    ]
    if customer_id:
        data.append(("customer", customer_id))
    elif customer_email:
        data.append(("customer_email", customer_email))
    body = _request(cfg, "POST", "/v1/checkout/sessions", "checkout",
                    data=data, idempotency_key=idempotency_key)
    url = body.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ProviderError(STRIPE, "checkout", kind="malformed")
    return {"id": body.get("id"), "url": url}


def create_portal_session(cfg: BillingConfig, *, customer_id: str, return_url: str) -> str:
    body = _request(cfg, "POST", "/v1/billing_portal/sessions", "portal",
                    data=[("customer", customer_id), ("return_url", return_url)])
    url = body.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ProviderError(STRIPE, "portal", kind="malformed")
    return url


def retrieve_subscription(cfg: BillingConfig, subscription_id: str) -> dict:
    """The subscription as Stripe holds it NOW — the authoritative state.

    Webhooks are processed against this, not against the event's own copy of
    the object, so two events arriving in the wrong order both end up applying
    the same, current truth.
    """
    if not subscription_id or "/" in subscription_id:
        raise ProviderError(STRIPE, "subscription", kind="malformed")
    return _request(cfg, "GET", f"/v1/subscriptions/{subscription_id}", "subscription")


def _ts(value: Any) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def normalize_subscription(obj: dict) -> dict:
    """Stripe's subscription object -> the fields billing stores.

    Handles both API shapes: `current_period_end` on the subscription (older
    versions) and on its items (2025-03-31 and later).
    """
    items = ((obj.get("items") or {}).get("data") or [])
    first = items[0] if items else {}
    period_end = obj.get("current_period_end") or first.get("current_period_end")
    price = (first.get("price") or {}).get("id") or ""
    cancel_at = _ts(obj.get("cancel_at"))
    current_period_end = _ts(period_end)
    cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    if not cancel_at_period_end and cancel_at is not None and (
        current_period_end is None or cancel_at <= current_period_end
    ):
        # A cancellation scheduled for a date inside the paid period: access
        # ends then. One scheduled further out renews until it is reached and
        # is picked up by the event Stripe sends when the period rolls.
        cancel_at_period_end = True
        current_period_end = cancel_at
    status = str(obj.get("status") or "").lower()
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    return {
        "provider_subscription_id": obj.get("id") or "",
        "provider_customer_id": customer or "",
        "status": status,
        "plan": price,
        "current_period_end": current_period_end,
        "cancel_at_period_end": cancel_at_period_end,
        "ended_at": _ts(obj.get("ended_at")),
        "tenant_id": ((obj.get("metadata") or {}).get("tenant_id") or ""),
    }

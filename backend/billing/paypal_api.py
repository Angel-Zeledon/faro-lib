"""The PayPal Subscriptions calls billing needs, over plain httpx.

Sandbox or live comes from PAYPAL_MODE. Every call first trades the client id
and secret for a short-lived access token (OAuth client credentials).

Webhooks are verified by asking PayPal (`verify_webhook`) — PayPal signs with
a certificate it hosts, and its documented verification is this API call. A
verification we could not complete is NOT a pass: the caller answers 503 so
PayPal retries, and nothing changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend.billing.providers import (
    HTTP_TIMEOUT_SECONDS, PAYPAL, BillingConfig, ProviderError,
)

log = logging.getLogger(__name__)

# The transmission headers PayPal signs a webhook with, as it names them.
WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def _post_or_get(method: str, url: str, operation: str, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        log.warning("paypal %s unreachable: %s", operation, type(exc).__name__)
        raise ProviderError(PAYPAL, operation, kind="unreachable") from None
    if resp.status_code >= 400:
        log.warning("paypal %s rejected: status=%s", operation, resp.status_code)
        raise ProviderError(PAYPAL, operation, resp.status_code)
    return resp


def _json(resp: httpx.Response, operation: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise ProviderError(PAYPAL, operation, resp.status_code, kind="malformed") from None
    if not isinstance(body, dict):
        raise ProviderError(PAYPAL, operation, resp.status_code, kind="malformed")
    return body


def access_token(cfg: BillingConfig) -> str:
    resp = _post_or_get(
        "POST", f"{cfg.paypal_api_base}/v1/oauth2/token", "token",
        data={"grant_type": "client_credentials"},
        auth=(cfg.value("paypal_client_id"), cfg.value("paypal_client_secret")),
        headers={"Accept": "application/json"},
    )
    token = _json(resp, "token").get("access_token")
    if not isinstance(token, str) or not token:
        raise ProviderError(PAYPAL, "token", kind="malformed")
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json"}


def create_subscription(
    cfg: BillingConfig, *, tenant_id: str, return_url: str, cancel_url: str,
    request_id: str, brand_name: str = "StockAI",
) -> dict:
    """A subscription awaiting the buyer's approval. Returns {'id', 'url'},
    where `url` is PayPal's approval page the browser is sent to.

    `custom_id` is the tenant: PayPal echoes it on the subscription in every
    later webhook, which is how an event finds its tenant.
    """
    token = access_token(cfg)
    payload = {
        "plan_id": cfg.value("paypal_plan_id_full"),
        "custom_id": tenant_id,
        "application_context": {
            "brand_name": brand_name[:127],
            "user_action": "SUBSCRIBE_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    headers = _auth(token)
    # PayPal's idempotency header: a retried click does not open a second one.
    headers["PayPal-Request-Id"] = request_id
    resp = _post_or_get("POST", f"{cfg.paypal_api_base}/v1/billing/subscriptions",
                        "checkout", json=payload, headers=headers)
    body = _json(resp, "checkout")
    approve = next(
        (link.get("href") for link in body.get("links") or []
         if isinstance(link, dict) and link.get("rel") == "approve"),
        None,
    )
    if not body.get("id") or not isinstance(approve, str) or not approve.startswith("https://"):
        raise ProviderError(PAYPAL, "checkout", kind="malformed")
    return {"id": body["id"], "url": approve}


def get_subscription(cfg: BillingConfig, subscription_id: str,
                     token: Optional[str] = None) -> dict:
    if not subscription_id or "/" in subscription_id or "?" in subscription_id:
        raise ProviderError(PAYPAL, "subscription", kind="malformed")
    token = token or access_token(cfg)
    resp = _post_or_get(
        "GET", f"{cfg.paypal_api_base}/v1/billing/subscriptions/{subscription_id}",
        "subscription", headers=_auth(token),
    )
    return _json(resp, "subscription")


def webhook_headers(headers: Any) -> Optional[dict]:
    """The five transmission headers, or None when any is missing — in which
    case the request is not a PayPal webhook and is refused without a call."""
    out = {}
    for field, name in WEBHOOK_HEADERS.items():
        value = headers.get(name)
        if not value:
            return None
        out[field] = value
    return out


def verify_webhook(cfg: BillingConfig, transmission: dict, event: dict,
                   token: Optional[str] = None) -> bool:
    """True only when PayPal says SUCCESS for this event and OUR webhook id.

    Raises ProviderError when PayPal could not be asked; the caller must treat
    that as "not verified" (and answer 503 so PayPal delivers again).
    """
    token = token or access_token(cfg)
    payload = dict(transmission)
    payload["webhook_id"] = cfg.value("paypal_webhook_id")
    payload["webhook_event"] = event
    resp = _post_or_get(
        "POST", f"{cfg.paypal_api_base}/v1/notifications/verify-webhook-signature",
        "verify", json=payload, headers=_auth(token),
    )
    return _json(resp, "verify").get("verification_status") == "SUCCESS"


def _iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_STATUS = {
    "ACTIVE": "active",
    # PayPal suspends a subscription when payments fail past the plan's
    # threshold (or by hand). It can be reactivated, so it is the grace state,
    # not the end: the grace window bounds it.
    "SUSPENDED": "past_due",
    "CANCELLED": "canceled",
    "EXPIRED": "expired",
    "APPROVAL_PENDING": "approval_pending",
    "APPROVED": "approval_pending",
}


def normalize_subscription(obj: dict) -> dict:
    """PayPal's subscription resource -> the fields billing stores."""
    raw = str(obj.get("status") or "").upper()
    status = _STATUS.get(raw, raw.lower() or "unknown")
    billing_info = obj.get("billing_info") or {}
    try:
        failed = int(billing_info.get("failed_payments_count") or 0)
    except (TypeError, ValueError):
        failed = 0
    if status == "active" and failed > 0:
        # Still ACTIVE at PayPal, but a payment bounced and is being retried.
        status = "past_due"
    subscriber = obj.get("subscriber") or {}
    return {
        "provider_subscription_id": obj.get("id") or "",
        "provider_customer_id": subscriber.get("payer_id") or "",
        "status": status,
        "plan": obj.get("plan_id") or "",
        "current_period_end": _iso(billing_info.get("next_billing_time")),
        "cancel_at_period_end": False,
        "ended_at": None,
        "tenant_id": obj.get("custom_id") or "",
    }

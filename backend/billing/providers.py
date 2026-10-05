"""Which payment providers this installation can take money through.

Read from `service_config.resolver.effective()` — never from `settings` — so a
key pasted into /instalacion takes effect without a restart. A provider is ON
only when every one of its fields is present; a half-configured provider is
OFF and says which variables are missing. That is the difference between "the
operator has not set this up" and a checkout button that 500s.

Instance scope only: the money goes to the deployment's owner, never to a
tenant, so there is no tenant override of any of these.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STRIPE = "stripe"
PAYPAL = "paypal"
PROVIDERS: tuple[str, ...] = (STRIPE, PAYPAL)

# Every field a provider needs before it may be offered. The webhook secret /
# id is in the list on purpose: a checkout we could sell but never confirm
# would take the customer's money and leave them on the free plan.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    STRIPE: ("stripe_secret_key", "stripe_webhook_secret", "stripe_price_id_full"),
    PAYPAL: ("paypal_client_id", "paypal_client_secret", "paypal_webhook_id",
             "paypal_plan_id_full", "paypal_mode"),
}

PAYPAL_MODES = ("sandbox", "live")
PAYPAL_API_BASE = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}
# Where a PayPal subscriber manages (or cancels) an automatic payment. PayPal
# has no per-merchant hosted portal like Stripe's, so "manage subscription"
# opens the subscriber's own PayPal account page.
PAYPAL_MANAGE_URL = {
    "sandbox": "https://www.sandbox.paypal.com/myaccount/autopay/",
    "live": "https://www.paypal.com/myaccount/autopay/",
}

STRIPE_API_BASE = "https://api.stripe.com"
CURRENCY = "USD"
HTTP_TIMEOUT_SECONDS = 15.0


class ProviderError(Exception):
    """A provider call that did not give us what we needed.

    Carries the provider, the operation and the HTTP status — never the
    request, the response body or a credential, because this message ends up
    in logs and the request carried a secret key.
    """

    def __init__(self, provider: str, operation: str, status: Optional[int] = None,
                 kind: str = "rejected"):
        self.provider = provider
        self.operation = operation
        self.status = status
        # 'rejected' (an HTTP error), 'unreachable' (network), 'malformed'
        self.kind = kind
        super().__init__(f"{provider} {operation} failed ({kind}, status={status})")


@dataclass(frozen=True)
class BillingConfig:
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id_full: str
    paypal_client_id: str
    paypal_client_secret: str
    paypal_webhook_id: str
    paypal_plan_id_full: str
    paypal_mode: str
    price_usd_full: float

    def value(self, field: str) -> str:
        return str(getattr(self, field) or "").strip()

    def missing(self, provider: str) -> list[str]:
        """The ENVIRONMENT variable names this provider still lacks.

        Names, never values: they are shown to the tenant's admin so they can
        tell the operator what to configure, and a name discloses nothing.
        """
        from backend.service_config.registry import all_fields
        registry = all_fields()
        out = []
        for field in REQUIRED_FIELDS[provider]:
            raw = self.value(field)
            if not raw or (field == "paypal_mode" and raw.lower() not in PAYPAL_MODES):
                out.append(registry[field].env)
        if not self.price_ok:
            out.append(registry["billing_price_usd_full"].env)
        return out

    @property
    def price_ok(self) -> bool:
        try:
            return float(self.price_usd_full) > 0
        except (TypeError, ValueError):
            return False

    def is_configured(self, provider: str) -> bool:
        return provider in REQUIRED_FIELDS and not self.missing(provider)

    def enabled_providers(self) -> list[str]:
        return [p for p in PROVIDERS if self.is_configured(p)]

    @property
    def paypal_api_base(self) -> str:
        return PAYPAL_API_BASE.get(self.value("paypal_mode").lower(), PAYPAL_API_BASE["sandbox"])

    @property
    def paypal_manage_url(self) -> str:
        return PAYPAL_MANAGE_URL.get(self.value("paypal_mode").lower(), PAYPAL_MANAGE_URL["sandbox"])


def load(cfg: Optional[object] = None) -> BillingConfig:
    """The billing configuration in effect for the instance."""
    if cfg is None:
        from backend.service_config.resolver import effective
        cfg = effective()
    try:
        price = float(getattr(cfg, "billing_price_usd_full") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return BillingConfig(
        stripe_secret_key=getattr(cfg, "stripe_secret_key") or "",
        stripe_webhook_secret=getattr(cfg, "stripe_webhook_secret") or "",
        stripe_price_id_full=getattr(cfg, "stripe_price_id_full") or "",
        paypal_client_id=getattr(cfg, "paypal_client_id") or "",
        paypal_client_secret=getattr(cfg, "paypal_client_secret") or "",
        paypal_webhook_id=getattr(cfg, "paypal_webhook_id") or "",
        paypal_plan_id_full=getattr(cfg, "paypal_plan_id_full") or "",
        paypal_mode=getattr(cfg, "paypal_mode") or "",
        price_usd_full=price,
    )

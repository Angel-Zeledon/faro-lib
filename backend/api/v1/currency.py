"""Which currency this company's money is shown in.

Two different kinds of money live in this product and they must not share a
setting:

  · What Faro COSTS is priced in USD — the plans are the same price for everyone,
    and that lives in Stripe (backend/billing), not here.
  · What the customer's OWN money is worth — inventory value, unit costs, a
    purchase order's total — is in whatever currency that business actually
    trades in, and only they know which.

So this is a tenant-level setting, not a per-user one: it describes the
company's books, not a display preference. Two people in the same company
looking at the same purchase order must see the same number with the same symbol.

Changing it relabels existing figures; it does NOT convert them. Faro stores what
it was given and never guesses an exchange rate, so switching the currency on a
tenant that already has costs loaded is a mislabel, not a conversion — which is
why the endpoint is admin-only and the UI says so.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user, require_role
from backend.errors import AppError
from backend.schemas.common import ok
from backend.tenants.service import get_settings, update_settings

router = APIRouter(prefix="/tenant/currency", tags=["currency"])
log = logging.getLogger(__name__)

# ISO 4217 codes this deployment supports. Kept deliberately short: every entry
# needs a symbol and a locale the frontend can format with, and an unsupported
# code would render as a bare number with no symbol at all.
SUPPORTED = {
    "CRC": {"symbol": "₡", "locale": "es-CR", "decimals": 0},
    "USD": {"symbol": "$",      "locale": "en-US", "decimals": 2},
    "MXN": {"symbol": "$",      "locale": "es-MX", "decimals": 2},
    "GTQ": {"symbol": "Q",      "locale": "es-GT", "decimals": 2},
    "COP": {"symbol": "$",      "locale": "es-CO", "decimals": 0},
    "PAB": {"symbol": "B/.",    "locale": "es-PA", "decimals": 2},
    "HNL": {"symbol": "L",      "locale": "es-HN", "decimals": 2},
    "NIO": {"symbol": "C$",     "locale": "es-NI", "decimals": 2},
    "DOP": {"symbol": "RD$",    "locale": "es-DO", "decimals": 2},
    "PEN": {"symbol": "S/",     "locale": "es-PE", "decimals": 2},
    "CLP": {"symbol": "$",      "locale": "es-CL", "decimals": 0},
    "ARS": {"symbol": "$",      "locale": "es-AR", "decimals": 0},
    "EUR": {"symbol": "€", "locale": "es-ES", "decimals": 2},
}

# Costa Rica is the anchor market, so an existing tenant that never chose keeps
# reading colones exactly as it did before this setting existed.
DEFAULT_CODE = "CRC"


class CurrencyUpdate(BaseModel):
    code: str


def currency_of(tenant_id: str) -> dict:
    code = (get_settings(tenant_id) or {}).get("currency") or DEFAULT_CODE
    if code not in SUPPORTED:
        # A code that fell out of SUPPORTED must not leave the app with no
        # symbol; fall back rather than render bare numbers.
        log.warning("[currency] tenant %s has unsupported code %s", tenant_id, code)
        code = DEFAULT_CODE
    return {"code": code, **SUPPORTED[code]}


@router.get("")
def get_currency(user: CurrentUser = Depends(get_current_user)):
    """Readable by every role: it is needed to render any figure on any screen."""
    return ok({"current": currency_of(user.tenant_id),
               "supported": [{"code": c, **v} for c, v in SUPPORTED.items()]})


@router.patch("")
def set_currency(
    body: CurrencyUpdate,
    # Admin only. This relabels every figure the company has already loaded.
    user: CurrentUser = Depends(require_role("admin")),
):
    code = body.code.upper().strip()
    if code not in SUPPORTED:
        raise AppError(
            "currency_not_supported",
            f"Currency '{code}' is not supported.",
            status_code=400,
            params={"code": code, "supported": sorted(SUPPORTED)},
        )
    update_settings(user.tenant_id, {"currency": code})
    log.info("[currency] tenant %s -> %s (by %s)", user.tenant_id, code, user.user_id)
    return ok({"current": currency_of(user.tenant_id)})

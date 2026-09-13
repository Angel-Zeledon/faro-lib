"""What this tenant may do, how much of it is left, and how to ask for more.

There is no checkout in Faro. The free tier is a permanent home with short
ceilings; the paid tier lifts them; and the way across is a conversation. So
this module has exactly two jobs: report the ceilings honestly (with usage, so
"100 SKUs" is a number the user can see themselves approaching), and record the
ask when somebody wants more.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from backend.auth.guards import CurrentUser, get_current_user, require_role
from backend.service_config.resolver import effective
from backend.db.connection import query_one
from backend.entitlements.service import (
    is_read_only, tenant_limits, tenant_tier, trial_state,
)
from backend.schemas.common import ok
from backend.tenants.service import get_tenant
from backend.utils.ids import generate_id

router = APIRouter(prefix="/entitlements", tags=["entitlements"])
log = logging.getLogger(__name__)


def _usage(tenant_id: str) -> dict:
    """Current consumption, keyed by the limit it counts against.

    Same keys as `limits`, so the UI pairs them without a mapping table — and
    so a limit that gains a counter later cannot end up displayed against the
    wrong number.
    """
    from backend.inventory import service as inv_svc
    from backend.inventory import warehouse_service as wh_svc
    from backend.sessions import service as session_svc
    from backend.users import service as user_svc

    keys = query_one(
        "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (tenant_id,)
    )
    return {
        "max_skus": inv_svc.count_stock(tenant_id),
        "max_users": user_svc.count_users(tenant_id),
        "max_locations": wh_svc.count_warehouses(tenant_id),
        "max_sessions": session_svc.count_sessions(tenant_id),
        "max_api_keys": int(keys["n"]) if keys else 0,
    }


def _contact() -> dict:
    """The channels that are actually configured. An empty one is omitted, not
    sent as "": a button that opens a blank wa.me link is worse than no button,
    and the frontend decides what to show from what arrives here."""
    cfg = effective()
    return {
        "whatsapp": cfg.contact_whatsapp,
        "email": cfg.contact_email,
    }


@router.get("")
def get_entitlements(user: CurrentUser = Depends(get_current_user)):
    """This tenant's tier, its ceilings, and how close it is to them.

    `plan`, `features` and `feature_plans` are gone with the old tiers: both
    tiers include every feature, and a `features` map that answers True to
    everything only invites the UI to keep asking.
    """
    tenant = get_tenant(user.tenant_id) or {"quota": {}}
    ends = tenant.get("trial_ends_at")
    return ok({
        "tier": tenant_tier(tenant),
        "limits": tenant_limits(tenant),
        "usage": _usage(user.tenant_id),
        "contact": _contact(),
        "trial": {
            "state": trial_state(tenant),
            "ends_at": ends.isoformat() if ends else None,
        },
        "read_only": is_read_only(tenant),
    })


class UpgradeRequest(BaseModel):
    # Which ceiling sent them here, when they came from a blocked action. Free
    # text is fine: it is a label we read, never something the code branches on.
    limit_key: str | None = None
    message: str = ""
    # How they want to be reached — a phone, another email. Empty means "use
    # the account's email", which the notification already carries.
    contact: str = ""

    @field_validator("message", "contact")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "").strip()[:2000]

    @field_validator("limit_key")
    @classmethod
    def _trim_key(cls, v: str | None) -> str | None:
        return (v or "").strip()[:100] or None


@router.post("/upgrade-request", status_code=201)
def create_upgrade_request(
    body: UpgradeRequest,
    # `require_role` and not `require_analyst_or_above`: the latter also
    # enforces the read-only guard, and a suspended tenant is precisely the one
    # most likely to be writing to us. Refusing their message because they are
    # read-only would be refusing the sale. The role bar is unchanged — a
    # viewer still cannot file one.
    user: CurrentUser = Depends(require_role("admin", "analyst")),
):
    """Record that this tenant wants more room, and tell us about it.

    The row is committed before the email is attempted, and the response does
    not depend on the send: an "I want to pay you" that exists only inside a
    failed SMTP call is the most expensive thing this product could drop.
    """
    tenant = get_tenant(user.tenant_id) or {}
    # One open ask per tenant. A second click — or the second person on the same
    # account, hitting the same wall the same morning — updates the one we have
    # instead of filing another, because the funnel is a list we read by hand.
    #
    # Done as ON CONFLICT against the partial unique index, NOT as a SELECT
    # followed by a branch. The read-then-write version filed four rows for six
    # simultaneous clicks: every request found no existing row, because none of
    # them had committed yet. `xmax = 0` is Postgres' own answer to "was this an
    # insert or an update", which is the only source that cannot disagree with
    # what actually happened.
    row = query_one(
        """INSERT INTO upgrade_requests
               (id, tenant_id, user_id, limit_key, message, contact)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id) WHERE status = 'new'
           DO UPDATE SET message   = EXCLUDED.message,
                         contact   = EXCLUDED.contact,
                         limit_key = EXCLUDED.limit_key,
                         created_at = NOW()
           RETURNING id, (xmax = 0) AS inserted""",
        (generate_id("upg"), user.tenant_id, user.user_id,
         body.limit_key, body.message, body.contact),
    )
    request_id, is_new = row["id"], bool(row["inserted"])

    cfg = effective()
    to = cfg.upgrade_notify_email or cfg.contact_email
    notified = False
    if to:
        from backend.notifications.email import send_upgrade_request_email
        # The person, not the id: whoever reads this notification needs
        # somebody to answer, and `usr_9f3c…` is not somebody.
        who = query_one(
            "SELECT email, full_name FROM users WHERE id = %s", (user.user_id,)
        ) or {}
        notified = send_upgrade_request_email(
            to=to,
            tenant_name=tenant.get("name") or user.tenant_id,
            tenant_id=user.tenant_id,
            requester=who.get("email") or who.get("full_name") or user.user_id,
            limit_key=body.limit_key,
            message=body.message,
            contact=body.contact,
        )
    else:
        # Not an error — a deployment without a notify address still keeps the
        # ask. It IS worth a loud log line: nobody is reading the table hourly.
        log.warning(
            "[upgrade] tenant=%s asked for more room and no UPGRADE_NOTIFY_EMAIL "
            "is configured — the request is in upgrade_requests (%s)",
            user.tenant_id, request_id,
        )
    return ok({"id": request_id, "created": is_new, "notified": notified})

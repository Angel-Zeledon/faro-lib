"""
How old the data behind the semáforo is.

A read-only view over `notifications.freshness_service` — the same computation
the daily reminder loop runs, so the passive banner in the app and the email
that arrives when nobody opens the app can never disagree.

Lives in its own router (not on the inventory one) because the answer is about
the tenant's data pipeline as a whole: the sales file, the stock table, and the
verdict the traffic light has to obey.
"""

import logging

from fastapi import APIRouter, Depends

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user
from backend.notifications import freshness_service
from backend.schemas.common import ok

router = APIRouter(tags=["freshness"])
log = logging.getLogger(__name__)


@router.get("/data-freshness")
def get_data_freshness(user: CurrentUser = Depends(get_current_user)):
    """Age of the sales history and of the stock table, plus whether the
    semáforo may still claim a colour.

    Read-only, so `get_current_user` (viewers included) is the right guard:
    a viewer who cannot see that the numbers are two months old is exactly the
    person the feature exists for.
    """
    data = freshness_service.get_tenant_freshness(user.tenant_id)
    if wscope.is_scoped(user):
        # Which warehouses went quiet is a fact about the warehouses the caller
        # may see; the others are not named.
        block = data.get("warehouses") or {}
        items = wscope.filter_rows(user, block.get("items") or [], key="name")
        data["warehouses"] = {
            **block, "items": items, "multi": len(items) > 1,
            "lagging": [i["name"] for i in items if i.get("lagging")],
        }
        data["warn"] = bool(
            data.get("degraded_by") or (data.get("sales") or {}).get("state") == "stale"
            or (data.get("stock") or {}).get("state") == "stale"
            or data["warehouses"]["lagging"])
    return ok(data)

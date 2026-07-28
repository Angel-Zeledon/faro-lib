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
    return ok(freshness_service.get_tenant_freshness(user.tenant_id))

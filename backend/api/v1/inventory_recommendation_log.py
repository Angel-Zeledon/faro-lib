"""
Reads over the daily recommendation log (stability.md — "what did it cost me
to ignore you" / "why is today's number different").

GET /inventory/recommendation-log/cost-of-ignoring — window report across
    every SKU that carried an ordering signal.
GET /inventory/recommendation-log/{sku}/why-changed — one SKU's latest
    recorded recommendation against the previous one, decomposed.

Both are read-only: `get_current_user` is enough (viewer included), matching
every other inventory report endpoint. There is no write endpoint here on
purpose — the log is written by `recommendation_log.record_recommendations`
from inside the code path that already computes `get_inventory_status`, not
through the API.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.auth.guards import CurrentUser, get_current_user
from backend.inventory import recommendation_reports as reports
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/recommendation-log", tags=["inventory-recommendation-log"])

# Sane bound on the report window — the log only exists from the day this
# shipped, so nothing is gained by allowing an enormous range, and it keeps
# the snapshot/PO scans bounded.
_MAX_WINDOW_DAYS = 366


@router.get("/cost-of-ignoring")
def cost_of_ignoring(
    from_date: Optional[date] = Query(
        default=None, description="Window start (default: 30 days before to_date)"),
    to_date: Optional[date] = Query(
        default=None, description="Window end (default: today)"),
    po_window_days: int = Query(
        default=reports.DEFAULT_PO_WINDOW_DAYS, ge=1, le=90,
        description="How many days after the SKU was flagged a PO still counts as a response"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Every SKU that carried an ordering signal (PEDIR_YA/PEDIR_PRONTO) in the
    window: whether a purchase order followed, and — only where it did not
    and stock was later observed at or below zero — the estimated unserved
    units and their value. See `recommendation_reports.cost_of_ignoring` for
    the conservatism rules (no PO + no observed stockout => no loss claimed;
    no sale price => units reported, value null).
    """
    end = to_date or date.today()
    start = from_date or (end - timedelta(days=30))
    if start > end:
        start, end = end, start
    if (end - start).days > _MAX_WINDOW_DAYS:
        start = end - timedelta(days=_MAX_WINDOW_DAYS)

    result = reports.cost_of_ignoring(user.tenant_id, start, end, po_window_days=po_window_days)
    return ok(result)


@router.get("/{sku}/why-changed")
def why_changed(
    sku: str,
    user: CurrentUser = Depends(get_current_user),
):
    """
    The SKU's latest recorded recommendation against the previous recorded
    one, decomposed into avg daily demand, lead time, stock on hand and
    safety stock — each tagged with whether it came from a new training
    session (a model opinion) or the tenant's own operational data. Returns
    `available: false` with a `reason` when there is not yet a previous row
    to compare against.
    """
    return ok(reports.why_changed(user.tenant_id, sku))

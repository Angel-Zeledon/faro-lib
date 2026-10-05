"""Tenant-level readings recomputed over a SUBSET of warehouses.

Only what a warehouse-scoped user is shown instead of a company total
(`backend/auth/warehouse_scope.py`). Kept out of `service.py` on purpose: that
file is the computation, this one is a restriction of it.
"""

from __future__ import annotations

from datetime import date

from backend.inventory import service as svc


def stock_history_over(
    tenant_id: str, sku: str, days: int, warehouses: list[str],
) -> list[dict]:
    """The summed stock level of `sku` across `warehouses`, one point per day.

    Each warehouse contributes its last reading of the day, carried forward over
    days it has none - the same "last of the day, then sum" rule the tenant-wide
    series follows, so the two agree when the subset is every warehouse.
    """
    per_wh: dict[str, dict[date, float]] = {}
    all_days: set[date] = set()
    for wh in warehouses:
        by_day: dict[date, float] = {}
        for point in svc.get_stock_history(tenant_id, sku, days=days, warehouse=wh):
            d = date.fromisoformat(point["date"][:10])
            by_day[d] = float(point["stock"] or 0)  # later points overwrite: last of the day
        per_wh[wh] = by_day
        all_days |= set(by_day)

    out: list[dict] = []
    last: dict[str, float] = {}
    for d in sorted(all_days):
        for wh, by_day in per_wh.items():
            if d in by_day:
                last[wh] = by_day[d]
        out.append({"stock": sum(last.values()), "date": d.isoformat()})
    return out


def po_history_page(user, *, limit: int, offset: int, status: str, q: str | None) -> dict:
    """The purchase-order history restricted to orders destined to the caller's
    warehouses - `total` and `awaiting_reception` included, so neither counts an
    order they cannot see."""
    from backend.auth import warehouse_scope as wscope
    from backend.inventory.roi_service import _OPEN_RECEPTION, get_po_history_page

    everything = get_po_history_page(user.tenant_id, limit=10**6, offset=0,
                                     status=status, q=q)["items"]
    mine = wscope.filter_po_rows(user, everything)
    unfiltered = wscope.filter_po_rows(
        user, get_po_history_page(user.tenant_id, limit=10**6, offset=0,
                                  status="all", q=None)["items"])
    awaiting = sum(
        1 for r in unfiltered
        if r.get("cancelled_at") is None
        and (r.get("reception_status") or "pending") in _OPEN_RECEPTION)
    return {"items": mine[offset:offset + limit], "total": len(mine),
            "limit": limit, "offset": offset, "awaiting_reception": awaiting}

"""
The two reads the recommendation log exists to answer (stability.md):

  * `cost_of_ignoring` — over a window, which SKUs carried an ordering
    signal, whether a purchase order followed within a sensible number of
    days, and — only where it did not and stock later hit zero — the units
    of demand that went unserved and what they were worth.
  * `why_changed` — for one SKU, today's recommendation against the
    previous recorded one, decomposed into the inputs that moved, and
    whether the change came from a new training session (a model opinion)
    or from the tenant's own operational data (stock, lead time).

Both are read-only aggregations over `inventory_recommendation_log`
(written by `recommendation_log.py`), `inventory_po_log` /
`inventory_po_items` (what was actually ordered) and `inventory_snapshots`
(what stock was actually observed at). Nothing here computes a
recommendation or writes anything.

Conservatism is the point, not an afterthought: a SKU only gets `"outcome":
"likely_stockout"` when a snapshot in the data actually recorded stock at or
below zero after the SKU was flagged. Absent that evidence the outcome is
`"no_po_no_stockout_observed"` or `"insufficient_data"` — never a stockout,
and never a manufactured zero. Money is reported only when both the lost
units AND a current sale price are known; otherwise `lost_value` is `None`
with a `lost_value_reason` explaining which one is missing.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from backend.db.connection import query
from backend.inventory import recommendation_log

# Signals that mean "the semaphore asked to order".
_ORDERING_SIGNALS = ("PEDIR_YA", "PEDIR_PRONTO")

# PO line statuses that mean the buyer actually acted on the recommendation
# (as opposed to 'rejected', which is stored for adoption-rate reporting but
# is, from this report's point of view, still "ignored").
_ACTED_ON_STATUSES = ("approved", "modified")

# Default window (days after the SKU was first flagged) within which a PO is
# still "a response to the recommendation" rather than an unrelated later
# order. Callers can widen it; this is a default, not a hardcoded truth.
DEFAULT_PO_WINDOW_DAYS = 14


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _display_names(tenant_id: str, skus: list[str]) -> dict[str, str]:
    """The name the buyer calls each product, for the SKUs in this report.

    The report used to return the bare code, so the screen had to join it
    against whatever `/inventory/status` happened to have loaded and fell back
    to the code whenever a SKU was not on that page. A report that names a
    product only when another screen happens to be open is not a report.

    One query for the batch, not one per SKU. A SKU with no name set is absent
    from the map rather than mapped to its own code — the caller decides how to
    render "unnamed", which is not this function's business.
    """
    if not skus:
        return {}
    placeholders = ", ".join(["%s"] * len(skus))
    rows = query(
        f"""SELECT DISTINCT ON (sku) sku, display_name FROM inventory_stock
            WHERE tenant_id = %s AND sku IN ({placeholders})
              AND display_name IS NOT NULL AND display_name <> ''
            ORDER BY sku, warehouse""",
        (tenant_id, *skus),
    )
    return {r["sku"]: r["display_name"] for r in rows}


def _current_sale_price(tenant_id: str, sku: str) -> Optional[float]:
    """Best-available CURRENT sale price for the SKU (not the price at the
    time of the recommendation — the schema does not carry historical
    pricing, and inventing one would be exactly the kind of number this
    report is built to avoid). None when no warehouse row has one set."""
    rows = query(
        """SELECT sale_price FROM inventory_stock
           WHERE tenant_id = %s AND sku = %s AND sale_price IS NOT NULL
           ORDER BY warehouse LIMIT 1""",
        (tenant_id, sku),
    )
    return float(rows[0]["sale_price"]) if rows else None


def _first_acted_on_po(
    tenant_id: str, sku: str, window_start: date, window_end: date,
) -> Optional[datetime]:
    rows = query(
        """SELECT MIN(l.generated_at) AS first_po
           FROM inventory_po_items i
           JOIN inventory_po_log l ON l.id = i.po_log_id
           WHERE i.tenant_id = %s AND i.sku = %s
             AND i.status = ANY(%s)
             AND l.cancelled_at IS NULL
             AND l.generated_at >= %s AND l.generated_at <= %s""",
        (tenant_id, sku, list(_ACTED_ON_STATUSES),
         _day_start(window_start), _day_start(window_end) + timedelta(days=1)),
    )
    return rows[0]["first_po"] if rows and rows[0]["first_po"] else None


def _stock_snapshots(tenant_id: str, sku: str, since: date) -> list[dict]:
    """The SKU's TENANT-WIDE level since `since`, one point per day.

    This read every snapshot row raw. Since 2026-09-16 rows are per
    warehouse, so one empty branch (Norte at 0 while principal held 500) was
    read as a company-wide stockout and charged as lost sales at the whole
    SKU's demand rate (math audit 2026-10-01). It now reads the same
    carried-forward total `get_stock_history` does.
    """
    from backend.inventory.service import tenant_wide_history
    return [
        {"recorded_at": at, "current_stock": level}
        for at, level in tenant_wide_history(tenant_id, sku, _day_start(since))
    ]


def _demand_rate_as_of(sku_rows: list[dict], as_of: date) -> Optional[float]:
    """Most recently recorded avg_daily_demand at or before `as_of`, falling
    back to the earliest recorded row if nothing qualifies (still a real
    recorded value, just from after the cutoff — better than inventing one)."""
    candidates = [r for r in sku_rows if r["recorded_on"] <= as_of and r.get("avg_daily_demand") is not None]
    if candidates:
        return float(max(candidates, key=lambda r: r["recorded_on"])["avg_daily_demand"])
    with_rate = [r for r in sku_rows if r.get("avg_daily_demand") is not None]
    return float(with_rate[0]["avg_daily_demand"]) if with_rate else None


def cost_of_ignoring(
    tenant_id: str,
    from_date: date,
    to_date: date,
    po_window_days: int = DEFAULT_PO_WINDOW_DAYS,
) -> dict:
    """
    Per-SKU: did the semaphore ask to order, did a PO follow, and — only when
    it did not and stock is later observed at or below zero — the estimated
    unserved units and their value.
    """
    rows = recommendation_log.get_window(tenant_id, from_date, to_date)

    by_sku: dict[str, list[dict]] = {}
    for r in rows:
        by_sku.setdefault(r["sku"], []).append(r)

    # Resolved once for the whole report rather than per SKU inside the loop.
    names = _display_names(tenant_id, list(by_sku))

    results = []
    for sku, sku_rows in by_sku.items():
        flagged = [r for r in sku_rows if r["signal"] in _ORDERING_SIGNALS]
        if not flagged:
            continue  # never carried an ordering signal in this window

        first_flagged = min(r["recorded_on"] for r in flagged)
        last_flagged = max(r["recorded_on"] for r in flagged)
        latest = flagged[-1]  # sku_rows/flagged both come back sorted by recorded_on

        first_po = _first_acted_on_po(
            tenant_id, sku, first_flagged,
            min(to_date, _add_days(last_flagged, po_window_days)),
        )

        entry = {
            "sku": sku,
            "display_name": names.get(sku),
            "times_flagged": len(flagged),
            "first_flagged_on": first_flagged,
            "last_flagged_on": last_flagged,
            "latest_signal": latest["signal"],
            "latest_recommended_qty": latest.get("recommended_qty"),
            "po_window_days": po_window_days,
        }

        if first_po is not None:
            entry.update({
                "outcome": "ordered",
                "po_generated_at": first_po,
                "lost_units": None,
                "lost_value": None,
                "lost_value_reason": None,
            })
            results.append(entry)
            continue

        snapshots = _stock_snapshots(tenant_id, sku, first_flagged)
        stockout_at = next(
            (s["recorded_at"] for s in snapshots if (s["current_stock"] or 0) <= 0), None,
        )
        if stockout_at is None:
            entry.update({
                "outcome": "no_po_no_stockout_observed",
                "lost_units": None,
                "lost_value": None,
                "lost_value_reason": "no_stockout_detected",
            })
            results.append(entry)
            continue

        recovery_at = next(
            (s["recorded_at"] for s in snapshots
             if s["recorded_at"] > stockout_at and (s["current_stock"] or 0) > 0),
            None,
        )
        window_capped = recovery_at is None
        end_at = recovery_at or _day_start(to_date)
        days_out = max((end_at.date() - stockout_at.date()).days, 1)

        avg_daily_demand = _demand_rate_as_of(sku_rows, stockout_at.date())
        lost_units = round(avg_daily_demand * days_out, 2) if avg_daily_demand is not None else None

        sale_price = _current_sale_price(tenant_id, sku) if lost_units is not None else None
        if lost_units is None:
            lost_value = None
            lost_value_reason = "no_demand_rate_recorded"
        elif sale_price is None:
            lost_value = None
            lost_value_reason = "sale_price_unknown"
        else:
            lost_value = round(lost_units * sale_price, 2)
            lost_value_reason = None

        entry.update({
            "outcome": "likely_stockout",
            "stockout_observed_at": stockout_at,
            "recovery_observed_at": recovery_at,
            # True when the window ended before stock was observed to recover —
            # the units/value below are a LOWER BOUND in that case, not the
            # full cost, because we cannot see past the requested window.
            "partial_window": window_capped,
            "days_out_of_stock": days_out,
            "avg_daily_demand_used": avg_daily_demand,
            "lost_units": lost_units,
            "lost_value": lost_value,
            "lost_value_reason": lost_value_reason,
        })
        results.append(entry)

    summary = {
        "skus_flagged": len(results),
        "skus_ordered": sum(1 for r in results if r["outcome"] == "ordered"),
        "skus_likely_stockout": sum(1 for r in results if r["outcome"] == "likely_stockout"),
        "skus_unclear": sum(1 for r in results if r["outcome"] == "no_po_no_stockout_observed"),
        "total_estimated_lost_units": round(
            sum(r["lost_units"] for r in results if r.get("lost_units") is not None), 2
        ) if any(r.get("lost_units") is not None for r in results) else None,
        "total_estimated_lost_value": round(
            sum(r["lost_value"] for r in results if r.get("lost_value") is not None), 2
        ) if any(r.get("lost_value") is not None for r in results) else None,
        "skus_with_lost_units_but_unknown_value": sum(
            1 for r in results if r.get("lost_units") is not None and r.get("lost_value") is None
        ),
    }

    return {
        "from_date": from_date,
        "to_date": to_date,
        "po_window_days": po_window_days,
        "summary": summary,
        "skus": results,
    }


def _add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


# Fields decomposed for `why_changed`, tagged by where the number comes from:
#   "session"     — recomputed by the forecasting engine on each training run
#                    (demand mean/variance -> safety stock, reorder point).
#   "operational" — the tenant's own live data, independent of any session
#                    (stock on hand, the lead time on file for the supplier).
#   "derived"     — the output the other two combine into.
_DECOMPOSED_FIELDS: list[tuple[str, str]] = [
    ("avg_daily_demand", "session"),
    ("safety_stock", "session"),
    ("reorder_point", "session"),
    ("lead_time_days", "operational"),
    ("current_stock", "operational"),
    ("recommended_qty", "derived"),
    ("signal", "derived"),
]


def why_changed(tenant_id: str, sku: str) -> dict:
    """
    Today's recorded recommendation for `sku` against the previous recorded
    one, decomposed into the inputs that moved and whether they moved
    because of a new training session or because the tenant's own data
    (stock, lead time) changed.
    """
    rows = recommendation_log.get_history(tenant_id, sku, limit=2)

    if not rows:
        return {"available": False, "sku": sku, "reason": "no_recorded_recommendations"}
    if len(rows) == 1:
        return {
            "available": False,
            "sku": sku,
            "reason": "no_previous_recommendation",
            "latest": rows[0],
        }

    latest, previous = rows[0], rows[1]
    session_changed = latest["session_id"] != previous["session_id"]

    fields = {}
    for field, origin in _DECOMPOSED_FIELDS:
        prev_v = previous.get(field)
        cur_v = latest.get(field)
        delta = None
        if isinstance(prev_v, (int, float)) and isinstance(cur_v, (int, float)):
            delta = round(cur_v - prev_v, 4)
        fields[field] = {"previous": prev_v, "current": cur_v, "delta": delta, "origin": origin}

    return {
        "available": True,
        "sku": sku,
        "latest_recorded_on": latest["recorded_on"],
        "previous_recorded_on": previous["recorded_on"],
        "latest_session_id": latest["session_id"],
        "previous_session_id": previous["session_id"],
        "session_changed": session_changed,
        "fields": fields,
        "explanation_code": (
            "recommendation_change_new_session" if session_changed
            else "recommendation_change_same_session"
        ),
    }

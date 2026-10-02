"""
Daily recommendation log (stability.md — "what did it cost to ignore you").

Every other inventory table records a fact about the world: stock levels
(`inventory_snapshots`), purchase orders sent (`inventory_po_log` /
`inventory_po_items`), shrinkage, overstock value. None of them record the
RECOMMENDATION itself — what the semaphore said and asked for on a given day
for a given SKU. Without that record the product can never answer the two
questions a buyer actually judges it by: "what did it cost me to ignore you"
and "why is today's number different from last week's". This module is the
write side of that record; `backend/inventory/recommendation_reports.py` is
the read side.

This module does NOT compute a recommendation. `record_recommendations`
takes the rows `backend.inventory.service.get_inventory_status` already
computed and persists them verbatim — the one thing this module owns is
making sure that persistence is honest (one row per SKU per day, upsert not
duplicate) and never breaks the caller (best-effort per row; a bad row is
logged and skipped, not raised, since a status screen that renders correctly
must not fail because the day's history write hiccuped on one SKU).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Mirrors service._DAYS_PER_PERIOD; kept local because service imports this
# module from inside the status call.
_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}


def _as_of(as_of: Optional[date]) -> date:
    """The day to file today's rows under, UTC — matches the convention
    already used for other daily inventory bookkeeping
    (`inventory.cash_service.today`)."""
    return as_of or datetime.now(timezone.utc).date()


def record_recommendations(
    tenant_id: str,
    session_id: str,
    items: list[dict],
    *,
    as_of: Optional[date] = None,
    period: str = "daily",
) -> int:
    """
    Persist today's recommendation for every SKU in `items` (the list
    `get_inventory_status` returns).

    One row per (tenant, sku, recorded_on): a second call the same day
    (e.g. the buyer opens /hoy after the morning digest already ran)
    overwrites that day's row via ON CONFLICT rather than adding a second
    one — enforced by the UNIQUE (tenant_id, sku, recorded_on) constraint in
    the schema, not by a check here.

    Returns the number of rows written. Never raises for a malformed
    individual item — it logs and continues, because this is a recorder
    riding along on a call whose real job (rendering the status page /
    sending the digest) must not fail over a history-logging defect.
    """
    if not items:
        return 0

    day = _as_of(as_of)
    rows: list[tuple] = []
    for item in items:
        sku = item.get("sku")
        signal = item.get("signal")
        if not sku or not signal:
            log.warning(
                "recommendation_log: skipping item with no sku/signal "
                "tenant=%s session=%s item_keys=%s",
                tenant_id, session_id, sorted(item.keys()),
            )
            continue
        calc = item.get("calc_explanation") or {}
        rows.append((
            tenant_id, sku, day, signal,
            item.get("recommended_qty"),
            item.get("current_stock"),
            item.get("reorder_point"),
            calc.get("safety_stock"),
            # The column is a DAILY rate and its readers multiply it by day
            # counts (`recommendation_reports.cost_of_ignoring`: lost units =
            # rate x days out). The status row's `daily_demand` is per bucket
            # of the planning grain, so a weekly tenant's lost units came out
            # 7x too high (math audit 2026-10-01). Stored per day from here on.
            (float(item["daily_demand"]) / _DAYS_PER_PERIOD.get(period or "daily", 1)
             if item.get("daily_demand") is not None else None),
            item.get("lead_time_days"),
            session_id,
        ))

    if not rows:
        return 0

    # ONE statement, not one per SKU. This runs on the status path, which a
    # 2.000-SKU tenant hits on every screen load, from the assistant and from
    # the public API — a round trip per product there is not a logging cost,
    # it is the page.
    values = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"] * len(rows))
    params = tuple(value for row in rows for value in row)
    try:
        execute(
            f"""INSERT INTO inventory_recommendation_log
                    (tenant_id, sku, recorded_on, signal, recommended_qty,
                     current_stock, reorder_point, safety_stock,
                     avg_daily_demand, lead_time_days, session_id, updated_at)
                VALUES {values}
                ON CONFLICT (tenant_id, sku, recorded_on) DO UPDATE
                SET signal           = EXCLUDED.signal,
                    recommended_qty  = EXCLUDED.recommended_qty,
                    current_stock    = EXCLUDED.current_stock,
                    reorder_point    = EXCLUDED.reorder_point,
                    safety_stock     = EXCLUDED.safety_stock,
                    avg_daily_demand = EXCLUDED.avg_daily_demand,
                    lead_time_days   = EXCLUDED.lead_time_days,
                    session_id       = EXCLUDED.session_id,
                    updated_at       = NOW()""",
            params,
        )
    except Exception:
        log.exception(
            "recommendation_log: failed to record %d row(s) tenant=%s day=%s",
            len(rows), tenant_id, day,
        )
        return 0
    return len(rows)


def already_recorded(tenant_id: str, as_of: Optional[date] = None) -> bool:
    """Has this tenant's opinion for the day already been written down?

    The recorder rides on `_compute_inventory_status`, which every screen, the
    assistant, the MCP tools and the public API all call. Without this the log
    would be rewritten on every one of those reads. Once a day is what the
    table's natural key models, so once a day is what gets written: the first
    look of the day is what the product told you that day.
    """
    day = _as_of(as_of)
    try:
        return query_one(
            "SELECT 1 AS present FROM inventory_recommendation_log "
            "WHERE tenant_id = %s AND recorded_on = %s LIMIT 1",
            (tenant_id, day),
        ) is not None
    except Exception:
        log.exception("recommendation_log: presence check failed tenant=%s", tenant_id)
        return True   # on doubt, do NOT write: a missing log beats a broken read


def get_history(tenant_id: str, sku: str, limit: int = 2) -> list[dict]:
    """Most recent recorded rows for one SKU, newest first."""
    rows = query(
        """SELECT id, tenant_id, sku, recorded_on, signal, recommended_qty,
                  current_stock, reorder_point, safety_stock,
                  avg_daily_demand, lead_time_days, session_id, created_at, updated_at
           FROM inventory_recommendation_log
           WHERE tenant_id = %s AND sku = %s
           ORDER BY recorded_on DESC
           LIMIT %s""",
        (tenant_id, sku, limit),
    )
    return [dict(r) for r in rows]


def get_window(tenant_id: str, from_date: date, to_date: date) -> list[dict]:
    """Every recorded row for the tenant (all SKUs) in [from_date, to_date]."""
    rows = query(
        """SELECT id, tenant_id, sku, recorded_on, signal, recommended_qty,
                  current_stock, reorder_point, safety_stock,
                  avg_daily_demand, lead_time_days, session_id, created_at, updated_at
           FROM inventory_recommendation_log
           WHERE tenant_id = %s AND recorded_on BETWEEN %s AND %s
           ORDER BY sku, recorded_on""",
        (tenant_id, from_date, to_date),
    )
    return [dict(r) for r in rows]

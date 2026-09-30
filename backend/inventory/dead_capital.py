"""
Capital parado — money that is not moving.

Distinct from the SOBRESTOCK signal `service.get_inventory_status` computes:
SOBRESTOCK is a COVERAGE judgement (you hold more days of stock than your lead
time and safety stock call for), derived from the forecast. A SKU can sit
comfortably inside its coverage band — signal OK, even PEDIR_PRONTO — and
still be cash that has not moved in a quarter, if demand simply stalled
without stock ever crossing the overstock threshold. This module answers a
different question for every SKU: how long has the stock level gone without
falling, and how much money is sitting in it — worst (oldest, most expensive)
first.

It does NOT require a training session or a forecast. `inventory_snapshots` is
recorded automatically on every `upsert_stock` call that touches
current_stock (see `service._record_snapshot` — every write path funnels
through `upsert_stock`, so this is real, not sampled, movement history) and is
evidence of stillness independent of any model. A SKU the tenant has never
forecast, or has no active session for, is exactly the kind of SKU this view
exists to catch — `get_inventory_status` would report it SIN_DATOS and drop it
from the semáforo entirely.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.db.connection import query
from backend.inventory import service as svc
from backend.inventory import warehouse_service as wh_svc

log = logging.getLogger(__name__)

# 90 days is what the owner asked for ("no se mueven hace 90 días"). It is a
# screen filter (the API's `window_days` query param), never a tenant setting
# — a distributor gets a useful list with zero configuration.
DEFAULT_WINDOW_DAYS = 90

# A stock level counts as "the same" only within floating-point noise; CSV
# imports and unit conversions leave residues like 199.9999997.
_EPS = 1e-6

# Why a unit's value could not be priced, so the screen can say why instead of
# guessing. NEVER priced at 0 — a zero here reads as "no money at risk", which
# is the opposite of what an unpriced SKU means, and is the exact silent
# failure this codebase hunts (CLAUDE.md `silent-failures`).
REASON_NO_UNIT_COST = "no_unit_cost"


def _aggregated_current_stock(tenant_id: str) -> dict[str, dict]:
    """Tenant-wide stock, summed across warehouses, with the same
    representative-row logic `get_inventory_status` uses
    (`service._aggregate_stock_rows_by_sku`) — reused rather than
    reimplemented so a SKU's cost/name/supplier on this screen can never
    disagree with /status for a tenant running several warehouses (that
    exact disagreement, between the demand-share default warehouse and the
    name-ordered one, is what stability 5.4 had to fix once already).
    """
    rows = svc.list_stock(tenant_id)
    default_wh = wh_svc.get_default_warehouse_name(tenant_id)
    return svc._aggregate_stock_rows_by_sku(rows, default_wh)


def _snapshot_series(tenant_id: str) -> dict[str, list[tuple[datetime, float]]]:
    """One tenant-wide daily stock level per SKU, oldest first.

    Mirrors `service.get_stock_history`'s no-`warehouse` branch (one value per
    warehouse per day — the level that warehouse ended the day at — summed
    across warehouses for that day), but for every SKU in a single query
    instead of one query per SKU, since this runs over the whole catalog.
    Legacy rows written before the `warehouse` column existed (2026-09-16)
    were already tenant-wide totals and join the series unchanged.
    """
    rows = query(
        """WITH last_per_day AS (
               SELECT DISTINCT ON (sku, warehouse, date_trunc('day', recorded_at))
                      sku, warehouse, date_trunc('day', recorded_at) AS day,
                      current_stock, recorded_at
               FROM inventory_snapshots
               WHERE tenant_id = %s AND warehouse IS NOT NULL
               ORDER BY sku, warehouse, date_trunc('day', recorded_at), recorded_at DESC
           ),
           per_day_total AS (
               SELECT sku, day, SUM(current_stock) AS current_stock, MAX(recorded_at) AS recorded_at
               FROM last_per_day GROUP BY sku, day
           ),
           legacy AS (
               SELECT sku, current_stock, recorded_at
               FROM inventory_snapshots
               WHERE tenant_id = %s AND warehouse IS NULL
           )
           SELECT sku, current_stock, recorded_at FROM per_day_total
           UNION ALL
           SELECT sku, current_stock, recorded_at FROM legacy
           ORDER BY sku, recorded_at ASC""",
        (tenant_id, tenant_id),
    )
    series: dict[str, list[tuple[datetime, float]]] = {}
    for r in rows:
        series.setdefault(r["sku"], []).append((r["recorded_at"], float(r["current_stock"])))
    return series


def _stillness(
    history: list[tuple[datetime, float]], current_stock: float, now: datetime,
) -> Optional[dict]:
    """How long a SKU's stock has gone without falling.

    `history` is the recorded snapshots, oldest first; `current_stock` is the
    AUTHORITATIVE live level from `inventory_stock`, always appended as the
    "as of right now" point — walking to it (rather than stopping at the last
    recorded snapshot, which can be days old if nothing has been touched
    since) is what lets `days_still` measure against today instead of against
    whenever the row was last written.

    Returns None when there is no snapshot recorded before now — the honest
    answer for a brand-new SKU is "we don't know yet", not zero days and not
    the window.

    Otherwise returns:
      - `days_still`: days since the stock level last fell, walking backward
        from now to the most recent decrease found in the data.
      - `exact`: True when that decrease was actually observed in the data.
        False means no decrease was found anywhere in the recorded history, so
        `days_still` is only the span the data covers (age of the earliest
        snapshot) — a FLOOR, not a claim that stillness runs exactly that long
        or longer. This is what keeps a SKU with two days of snapshots and no
        sale from reading as ninety days quiet: with two days of history and
        no decrease, `days_still` comes out as 2, not 90, and 2 < any
        reasonable window so it is excluded from the ranked list, not flagged
        dead on thin evidence.
    """
    points = list(history) + [(now, current_stock)]
    if len(points) < 2:
        return None

    for i in range(len(points) - 1, 0, -1):
        level, prev_level = points[i][1], points[i - 1][1]
        if level < prev_level - _EPS:
            fell_at = points[i][0]
            return {"days_still": (now - fell_at).days, "exact": True}

    earliest_at = points[0][0]
    return {"days_still": (now - earliest_at).days, "exact": False}


def get_dead_capital(
    tenant_id: str, window_days: int = DEFAULT_WINDOW_DAYS, session_id: Optional[str] = None,
) -> dict:
    """
    Every SKU currently on hand whose stock level has not fallen in at least
    `window_days`, ranked worst first — priced money before unpriced, then by
    how long it has been still — with the tenant's total in money at the top.

    `session_id`: when given, each item's CURRENT semáforo signal is attached
    from `get_inventory_status` so this screen and /hoy never contradict each
    other about a SKU's status. This module's own stillness judgement does not
    depend on it — a SKU with no session, or no forecast, still gets ranked.
    """
    now = datetime.now(timezone.utc)
    stock_map = _aggregated_current_stock(tenant_id)
    series = _snapshot_series(tenant_id)

    signal_by_sku: dict[str, str] = {}
    if session_id:
        try:
            for row in svc.get_inventory_status(tenant_id, session_id):
                signal_by_sku[row["sku"]] = row["signal"]
        except Exception as e:
            log.debug(
                "dead_capital: signal lookup failed tenant=%s session=%s: %s",
                tenant_id, session_id, e,
            )

    items: list[dict] = []
    excluded_no_history = 0
    excluded_too_recent = 0
    total_skus_with_stock = 0

    for sku, stock in stock_map.items():
        current_stock = float(stock.get("current_stock") or 0)
        if current_stock <= 0:
            continue  # nothing on hand — not money sitting still
        total_skus_with_stock += 1

        stillness = _stillness(series.get(sku, []), current_stock, now)
        if stillness is None:
            excluded_no_history += 1
            continue
        if stillness["days_still"] < window_days:
            excluded_too_recent += 1
            continue

        unit_cost = stock.get("unit_cost")
        if unit_cost is None:
            value: Optional[float] = None
            value_unknown_reason: Optional[str] = REASON_NO_UNIT_COST
        else:
            value = round(current_stock * float(unit_cost), 2)
            value_unknown_reason = None

        items.append({
            "sku": sku,
            "display_name": stock.get("display_name"),
            "supplier": stock.get("supplier"),
            "category": stock.get("category"),
            "current_stock": current_stock,
            "unit_cost": float(unit_cost) if unit_cost is not None else None,
            "value": value,
            "value_unknown_reason": value_unknown_reason,
            "days_still": stillness["days_still"],
            "days_still_exact": stillness["exact"],
            # None when there is no active/given session, or the SKU was never
            # forecast in it — this list must not go blank just because the
            # semáforo has nothing to say about the SKU.
            "signal": signal_by_sku.get(sku),
        })

    # Worst first: priced items sorted by money (descending), unpriced items
    # after them sorted by how long they have been still — never dropped and
    # never sorted as if they were worth 0, just grouped where "we don't know
    # the value" can be said plainly instead of implied by a number.
    items.sort(key=lambda i: (i["value"] is None, -(i["value"] or 0), -i["days_still"]))

    priced_total = round(sum(i["value"] for i in items if i["value"] is not None), 2)
    unpriced_count = sum(1 for i in items if i["value"] is None)

    return {
        "window_days": window_days,
        "items": items,
        "total_value": priced_total,
        "sku_count": len(items),
        "unpriced_sku_count": unpriced_count,
        "total_skus_with_stock": total_skus_with_stock,
        "excluded_no_history": excluded_no_history,
        "excluded_too_recent": excluded_too_recent,
    }

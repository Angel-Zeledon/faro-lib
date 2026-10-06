"""
Supplier cost inflation and margin erosion alerts.

stability.md #20, items 5 and 6 of the "financial forecasting as a service"
list: both read history that is ALREADY being written (`inventory_po_items
.unit_cost`, `inventory_stock.sale_price`) and need no new table.

Where the price history comes from
-----------------------------------
`inventory_po_items.unit_cost` is written when a PO is GENERATED (see
`roi_service._insert_lines`) — it is what the buyer had on file at order
time, not a confirmed invoice price. An order that gets edited, rejected, or
never shows up never proves anything was actually paid at that cost. So this
module only treats a line as a price OBSERVATION once its order was actually
received — `inventory_po_log.reception_status IN ('received', 'partial')` —
and dates the observation by `received_at`, the moment the goods (and
presumably the invoice) actually arrived, not by when the order was merely
proposed. `status IN ('approved', 'modified')` mirrors `_ORDERED` everywhere
else in this package: a rejected line was never bought.

Two lines of the SAME po_log/sku/supplier (typical of a multi-warehouse
order) are collapsed into ONE observation, qty-weighted, because they come
from a single negotiated order. Two receptions of the same SKU on the same
calendar day but from DIFFERENT orders stay as two separate observations —
they were two separate purchase decisions, even if the calendar date matches.

Honesty constraint (read this before changing the shape of either payload)
----------------------------------------------------------------------------
StockAI stores exactly ONE sale_price per SKU — the current one. There is no
price-history table (checked: `grep sale_price backend/db/migrations.py`
finds one column, added once in `add_sale_price_to_inventory_stock`, never
logged anywhere else). So a margin "before" reconstructed from today's price
is NOT a historical margin — it is today's price crossed with a past cost,
which is a materially weaker claim. `get_margin_erosion` makes exactly that
weaker claim and says so on every response (`price_history_available:
False`): `margin_pct_then` and `margin_pct_now` are both computed against the
SAME current `sale_price`, so any erosion this function reports is by
construction attributable to cost alone — this module never claims a price
cut, because a price cut is not a fact the data can support; reporting one
would be fabricating a number StockAI does not have. That is narrower than a
"cost up / price down / both" attribution — the wider one needs a stored
price history, which is a schema change and out of scope here.

`service.calc_unit_margin`'s discipline is reused, not reimplemented: `None`
(never 0) when a side is missing, and a negative margin reported as-is,
never clamped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import query
from backend.inventory import service as svc
from backend.inventory import warehouse_service as wh_svc

log = logging.getLogger(__name__)

# A year of receptions is enough to catch the "raised you N times this year"
# pattern this feature exists to name, without the tenant having to configure
# anything before either endpoint answers something useful.
DEFAULT_WINDOW_DAYS = 365

# Fewer than two dated observations means there is nothing to compare — not
# "no change", just no evidence either way.
_MIN_OBSERVATIONS = 2

# Treat cost as unchanged within this relative tolerance so float noise from
# a qty-weighted average (e.g. 100.00000000002) never registers as a rise.
_REL_EPS = 1e-6

# Ignore erosion smaller than this many percentage points — the noise floor
# of a rounded percentage, not a tenant setting.
DEFAULT_MIN_EROSION_PTS = 0.5


def _cost_observations(tenant_id: str, window_days: int) -> tuple[list[dict], int]:
    """
    Every RECEIVED (or partially received) PO in the window, collapsed to one
    dated, qty-weighted unit_cost per (po_log, sku, supplier).

    Returns (observations oldest-first, count of lines skipped for having no
    supplier name — those can't be attributed to anyone, so they are excluded
    and counted rather than silently grouped under a blank key).
    """
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = query(
        """SELECT poi.po_log_id, poi.sku, poi.display_name, poi.supplier,
                  poi.unit_cost, poi.received_qty, pol.received_at
             FROM inventory_po_items poi
             JOIN inventory_po_log pol ON pol.id = poi.po_log_id
            WHERE poi.tenant_id = %s
              AND pol.reception_status IN ('received', 'partial')
              AND poi.status IN ('approved', 'modified')
              AND poi.unit_cost IS NOT NULL
              -- Unit costs are compared across orders; a line priced in another
              -- currency is not comparable to one in the company's own.
              AND poi.currency IS NULL
              AND poi.received_qty IS NOT NULL AND poi.received_qty > 0
              AND pol.received_at IS NOT NULL
              AND pol.received_at >= %s
            ORDER BY pol.received_at ASC""",
        (tenant_id, since),
    )

    lines_no_supplier = 0
    grouped: dict[tuple, dict] = {}
    for r in rows:
        supplier_raw = (r.get("supplier") or "").strip()
        if not supplier_raw:
            lines_no_supplier += 1
            continue
        key = (r["po_log_id"], r["sku"], supplier_raw.lower())
        g = grouped.setdefault(key, {
            "sku": r["sku"], "supplier_key": supplier_raw.lower(),
            # First spelling seen wins (rows arrive received_at-ascending) —
            # purely cosmetic, no reader depends on which case is stored.
            "supplier": supplier_raw,
            "display_name": r.get("display_name"),
            "received_at": r["received_at"],
            "_cost_qty_sum": 0.0, "_qty_sum": 0.0,
        })
        qty = float(r["received_qty"])
        g["_cost_qty_sum"] += float(r["unit_cost"]) * qty
        g["_qty_sum"] += qty

    observations = []
    for g in grouped.values():
        if g["_qty_sum"] <= 0:
            continue
        observations.append({
            "sku": g["sku"], "supplier_key": g["supplier_key"], "supplier": g["supplier"],
            "display_name": g["display_name"], "received_at": g["received_at"],
            "unit_cost": round(g["_cost_qty_sum"] / g["_qty_sum"], 4),
            "qty": g["_qty_sum"],
        })
    observations.sort(key=lambda o: o["received_at"])
    return observations, lines_no_supplier


def get_supplier_cost_inflation(
    tenant_id: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    top_n_products: int = 5,
) -> dict:
    """
    Suppliers who raised a SKU's cost at least once in the window, ranked
    worst first by a qty-weighted cumulative % change across their affected
    SKUs, each with the products where it bit hardest.

    A SKU only qualifies once it has raised in this window at least once
    (`increases_count > 0`); `cumulative_pct` is the NET change from the
    first to the last observation in the window, which can be smaller than
    the sum of individual rises (or, rarely, negative) if a later reception
    came in cheaper again — both numbers are reported so a supplier who
    raised twice and then partly backed off does not read the same as one
    who never did.
    """
    observations, lines_no_supplier = _cost_observations(tenant_id, window_days)

    series: dict[tuple, list[dict]] = {}
    for o in observations:
        series.setdefault((o["supplier_key"], o["sku"]), []).append(o)

    per_supplier: dict[str, dict] = {}
    skus_single_observation = 0
    skus_zero_cost_base = 0

    for (supplier_key, sku), obs_list in series.items():
        if len(obs_list) < _MIN_OBSERVATIONS:
            skus_single_observation += 1
            continue
        first, last = obs_list[0], obs_list[-1]
        if first["unit_cost"] <= 0:
            # Can't express a % change from a zero base — the honest answer
            # is "unknown", not an infinite or undefined percentage.
            skus_zero_cost_base += 1
            continue

        increases_count = sum(
            1 for prev, cur in zip(obs_list, obs_list[1:])
            if cur["unit_cost"] > prev["unit_cost"] * (1 + _REL_EPS)
        )
        if increases_count == 0:
            continue  # flat or only ever cheaper in this window — not an alert

        cumulative_pct = round((last["unit_cost"] - first["unit_cost"]) / first["unit_cost"] * 100, 1)
        total_qty = sum(o["qty"] for o in obs_list)

        sp = per_supplier.setdefault(supplier_key, {
            "supplier": obs_list[0]["supplier"], "products": [],
            "increases_count": 0, "_weight_sum": 0.0, "_weighted_pct_sum": 0.0,
        })
        sp["products"].append({
            "sku": sku,
            "display_name": last.get("display_name") or first.get("display_name"),
            "first_cost": first["unit_cost"], "last_cost": last["unit_cost"],
            "first_observed_at": first["received_at"].isoformat(),
            "last_observed_at": last["received_at"].isoformat(),
            "cumulative_pct": cumulative_pct,
            "increases_count": increases_count,
            "observations_count": len(obs_list),
        })
        sp["increases_count"] += increases_count
        # Weighted by total received qty in-window — a rough money-exposure
        # proxy, so a supplier's headline % leans toward the SKUs actually
        # bought in volume rather than being a flat average across SKUs of
        # wildly different importance.
        sp["_weight_sum"] += total_qty
        sp["_weighted_pct_sum"] += cumulative_pct * total_qty

    suppliers = []
    for sp in per_supplier.values():
        weight = sp["_weight_sum"] or 1.0
        cumulative_pct = round(sp["_weighted_pct_sum"] / weight, 1)
        products = sorted(sp["products"], key=lambda p: p["cumulative_pct"], reverse=True)
        suppliers.append({
            "supplier": sp["supplier"],
            "sku_count_affected": len(products),
            "increases_count": sp["increases_count"],
            "cumulative_pct": cumulative_pct,
            "worst_products": products[:top_n_products],
        })
    suppliers.sort(key=lambda s: s["cumulative_pct"], reverse=True)

    return {
        "window_days": window_days,
        "suppliers": suppliers,
        "supplier_count": len(suppliers),
        # Evidence gaps, named rather than silently dropped — same discipline
        # as dead_capital's excluded_* counters.
        "skus_single_observation": skus_single_observation,
        "skus_zero_cost_base": skus_zero_cost_base,
        "lines_excluded_no_supplier": lines_no_supplier,
    }


def get_margin_erosion(
    tenant_id: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_erosion_pts: float = DEFAULT_MIN_EROSION_PTS,
) -> dict:
    """
    Every SKU whose margin (today's sale_price against a past received cost)
    has eroded by at least `min_erosion_pts` percentage points across the
    window, worst first.

    See the module docstring for why `margin_pct_then` is a hypothetical
    ("if the price had always been what it is today") and not a historical
    fact — `price_history_available: False` in the response says so
    explicitly, once, rather than per item.
    """
    observations, lines_no_supplier = _cost_observations(tenant_id, window_days)

    series: dict[str, list[dict]] = {}
    for o in observations:
        # Merged across suppliers on purpose: the question is what THIS SKU's
        # cost did, regardless of who it was bought from in each period.
        series.setdefault(o["sku"], []).append(o)
    for obs_list in series.values():
        obs_list.sort(key=lambda o: o["received_at"])

    stock_rows = svc.list_stock(tenant_id)
    default_wh = wh_svc.get_default_warehouse_name(tenant_id)
    stock_map = svc._aggregate_stock_rows_by_sku(stock_rows, default_wh)

    items = []
    excluded_no_cost_history = 0
    excluded_no_sale_price = 0
    excluded_invalid_price = 0

    for sku, obs_list in series.items():
        if len(obs_list) < _MIN_OBSERVATIONS:
            excluded_no_cost_history += 1
            continue

        stock = stock_map.get(sku)
        sale_price = stock.get("sale_price") if stock else None
        if sale_price is None:
            excluded_no_sale_price += 1
            continue
        sale_price = float(sale_price)
        if sale_price <= 0:
            excluded_invalid_price += 1
            continue

        cost_then = obs_list[0]["unit_cost"]
        cost_now = obs_list[-1]["unit_cost"]

        # calc_unit_margin's own discipline, reused rather than duplicated:
        # None (never 0) when a side is missing, negative reported as-is.
        # Both sides are floats at this point, so None is not reachable here
        # — the call stays defensive rather than assuming that forever.
        margin_then = svc.calc_unit_margin(sale_price, cost_then)
        margin_now = svc.calc_unit_margin(sale_price, cost_now)
        if margin_then is None or margin_now is None:
            continue

        margin_pct_then = round(margin_then / sale_price * 100, 1)
        margin_pct_now = round(margin_now / sale_price * 100, 1)
        erosion_pts = round(margin_pct_then - margin_pct_now, 1)

        if erosion_pts < min_erosion_pts:
            continue  # margin held or improved in this window — not an alert

        cost_change_pct = (
            round((cost_now - cost_then) / cost_then * 100, 1) if cost_then > 0 else None
        )

        items.append({
            "sku": sku,
            "display_name": obs_list[-1].get("display_name")
                or obs_list[0].get("display_name")
                or (stock or {}).get("display_name"),
            "sale_price": sale_price,
            "cost_then": cost_then, "cost_now": cost_now,
            "cost_change_pct": cost_change_pct,
            "unit_margin_then": margin_then, "unit_margin_now": margin_now,
            "margin_pct_then": margin_pct_then, "margin_pct_now": margin_pct_now,
            "erosion_pts": erosion_pts,
            "first_observed_at": obs_list[0]["received_at"].isoformat(),
            "last_observed_at": obs_list[-1]["received_at"].isoformat(),
        })

    items.sort(key=lambda i: i["erosion_pts"], reverse=True)

    return {
        "window_days": window_days,
        "items": items,
        "sku_count": len(items),
        # See module docstring: margin_pct_then is NOT a historical margin —
        # it is today's price crossed with a past cost, because StockAI stores
        # no price history. Named explicitly rather than left implicit.
        "price_history_available": False,
        "excluded_no_cost_history": excluded_no_cost_history,
        "excluded_no_sale_price": excluded_no_sale_price,
        "excluded_invalid_price": excluded_invalid_price,
        "lines_excluded_no_supplier": lines_no_supplier,
    }

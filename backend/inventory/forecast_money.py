"""
The forecast in money — stability.md #20, item 1 of the "financial
forecasting as a service" list.

The engine predicts units; the product already knows `inventory_stock
.sale_price` and `inventory_stock.unit_cost` per SKU. Nobody had multiplied
them. This module reads the SAME per-SKU forecast the rest of the product
already serves (`session_store.get_forecasts`, collapsed to per-SKU with
`series.rollup_by_sku`, narrowed to the champion model with
`service.best_model_by_sku` — exactly how `service._compute_inventory_status`
picks a SKU's forecast) and projects it into revenue, cost and gross margin
over the session's forecast horizon.

Honesty constraints (read before changing the shape of the payload)
---------------------------------------------------------------------
1. A SKU with no `sale_price` cannot be projected into revenue, and one with
   no `unit_cost` cannot be projected into cost or margin. Neither is ever
   valued at 0 — `service.calc_unit_margin`'s discipline (`None`, never 0,
   for a missing side) is reused here, and every exclusion is COUNTED so the
   total says what it leaves out. This is the exact defect
   `/inventory/dead-stock` had (`unit_cost or 0`, sorted by that value) —
   see `dead_capital.py`'s module docstring for the fuller account.
2. `sale_price` is the CURRENT price — StockAI stores no price history (see
   `cost_alerts.py`'s module docstring for the one column that exists and the
   grep that confirms it). `price_history_available: False` says so on every
   response, the same flag `cost_alerts.get_margin_erosion` already uses for
   the identical caveat, so a projection built on today's price is never
   read as one that knows tomorrow's.
3. A negative margin (selling below cost) is reported as-is, never clamped —
   `calc_unit_margin` already guarantees this; nothing here re-clamps it.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db import session_store
from backend.inventory import service as svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory.series import rollup_by_sku

log = logging.getLogger(__name__)

# Why a SKU's revenue/cost/margin could not be projected, so the screen can
# say why instead of guessing. NEVER priced at 0 — see the module docstring.
REASON_NO_SALE_PRICE = "no_sale_price"
REASON_NO_UNIT_COST = "no_unit_cost"

# How many contributors the screen highlights as "carrying the number" — the
# owner's own phrasing ("these ten products are 70% of it").
TOP_CONTRIBUTORS = 10

# Forecast horizons are configured 1-365 days (`schemas/configuration.py`,
# `ForecastConfig.horizon`); this is comfortably above that ceiling so the
# curve is never truncated — the horizon reported back is the session's real
# one, not a screen default.
_MAX_STEPS = 400


def get_forecast_money(tenant_id: str, session_id: str) -> dict:
    """
    Per-SKU and total projected revenue, cost and gross margin over this
    session's forecast horizon, using each SKU's CHAMPION model (the same one
    `/inventory/status` recommends from) and the CURRENT `sale_price` /
    `unit_cost` on file. Ranked by margin (falling back to revenue when
    margin is unknown) so the top contributors are the ones actually
    carrying the total.
    """
    forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    forecasts = rollup_by_sku(forecasts)

    best_model: dict[str, str] = {}
    try:
        result = session_store.get_training_result(tenant_id, session_id) or {}
        best_model = svc.best_model_by_sku((result.get("metrics") or {}).get("rows") or [])
    except Exception as e:
        log.debug(
            "forecast_money: champion-model lookup failed tenant=%s session=%s: %s",
            tenant_id, session_id, e,
        )

    stock_rows = svc.list_stock(tenant_id)
    default_wh = wh_svc.get_default_warehouse_name(tenant_id)
    stock_map = svc._aggregate_stock_rows_by_sku(stock_rows, default_wh)

    items: list[dict] = []
    excluded_no_curve = 0
    horizon_len = 0
    horizon_start: Optional[str] = None
    horizon_end: Optional[str] = None

    for sku in sorted(forecasts.keys()):
        model_forecasts = forecasts.get(sku) or {}
        curve = svc._avg_forecast_curve(
            model_forecasts, max_steps=_MAX_STEPS, preferred_model=best_model.get(sku),
        )
        if not curve:
            excluded_no_curve += 1
            continue

        # A sales forecast cannot project negative units even if a model's
        # raw point briefly dips below zero near an idle stretch — the same
        # floor `_avg_daily_forecast` applies to the lead-time average.
        units = round(sum(max(0.0, p.get("value") or 0.0) for p in curve), 2)

        if len(curve) > horizon_len:
            horizon_len = len(curve)
        dates = [c["date"] for c in curve if c.get("date")]
        if dates:
            if horizon_start is None or dates[0] < horizon_start:
                horizon_start = dates[0]
            if horizon_end is None or dates[-1] > horizon_end:
                horizon_end = dates[-1]

        stock = stock_map.get(sku) or {}
        sale_price = stock.get("sale_price")
        unit_cost = stock.get("unit_cost")

        if sale_price is None:
            revenue: Optional[float] = None
            revenue_unknown_reason: Optional[str] = REASON_NO_SALE_PRICE
        else:
            revenue = round(units * float(sale_price), 2)
            revenue_unknown_reason = None

        if unit_cost is None:
            cost: Optional[float] = None
            cost_unknown_reason: Optional[str] = REASON_NO_UNIT_COST
        else:
            cost = round(units * float(unit_cost), 2)
            cost_unknown_reason = None

        # Reuses calc_unit_margin's own discipline rather than reimplementing
        # it: None (never 0) when either side is missing, negative as-is.
        unit_margin = svc.calc_unit_margin(sale_price, unit_cost)
        margin = round(unit_margin * units, 2) if unit_margin is not None else None
        margin_pct = (
            round(margin / revenue * 100, 1)
            if margin is not None and revenue not in (None, 0)
            else None
        )

        items.append({
            "sku": sku,
            "display_name": stock.get("display_name"),
            "supplier": stock.get("supplier"),
            "category": stock.get("category"),
            "units_forecast": units,
            "sale_price": float(sale_price) if sale_price is not None else None,
            "unit_cost": float(unit_cost) if unit_cost is not None else None,
            "revenue": revenue,
            "revenue_unknown_reason": revenue_unknown_reason,
            "cost": cost,
            "cost_unknown_reason": cost_unknown_reason,
            "margin": margin,
            "margin_pct": margin_pct,
        })

    # Worst-to-best is meaningless here; best-to-worst is the point: the buyer
    # reads down the list until the total is explained. Ranked by margin
    # first (what actually pays for the warehouse), falling back to revenue
    # for SKUs priced but not costed, and grouping the fully-unknown last —
    # never sorted as if an unknown were worth 0, same discipline as
    # `dead_capital.get_dead_capital`.
    items.sort(key=lambda i: (
        i["margin"] is None, -(i["margin"] or 0.0),
        i["revenue"] is None, -(i["revenue"] or 0.0),
    ))

    priced_items = [i for i in items if i["revenue"] is not None]
    costed_items = [i for i in items if i["margin"] is not None]

    total_revenue = round(sum(i["revenue"] for i in priced_items), 2)
    total_cost = round(sum(i["cost"] for i in costed_items), 2)
    total_margin = round(sum(i["margin"] for i in costed_items), 2)
    revenue_of_costed = round(sum(i["revenue"] for i in costed_items), 2)
    total_margin_pct = (
        round(total_margin / revenue_of_costed * 100, 1) if revenue_of_costed else None
    )

    for item in items:
        item["contribution_pct"] = (
            round(item["margin"] / total_margin * 100, 1)
            if item["margin"] is not None and total_margin
            else None
        )

    top10_margin = sum(i["margin"] for i in items[:TOP_CONTRIBUTORS] if i["margin"] is not None)
    top10_margin_share_pct = (
        round(top10_margin / total_margin * 100, 1) if total_margin else None
    )

    return {
        "session_id": session_id,
        "horizon_days": horizon_len,
        "horizon_start": horizon_start,
        "horizon_end": horizon_end,
        # See module docstring point 2: today's price, not a claim about
        # tomorrow's. Same flag name `cost_alerts.get_margin_erosion` uses
        # for the identical caveat.
        "price_history_available": False,
        "items": items,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_margin": total_margin,
        "total_margin_pct": total_margin_pct,
        "sku_count": len(items),
        "priced_sku_count": len(priced_items),
        "costed_sku_count": len(costed_items),
        "excluded_no_price_count": len(items) - len(priced_items),
        "excluded_no_cost_count": len(priced_items) - len(costed_items),
        "excluded_no_forecast_count": excluded_no_curve,
        "top_contributors": TOP_CONTRIBUTORS,
        "top10_margin_share_pct": top10_margin_share_pct,
    }

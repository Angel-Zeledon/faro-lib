"""
Pareto view of the stock-configuration wall (onboarding-friction plan #1, phase 3).

The problem this solves is not that 2.000 rows are missing; it is that the
product asks for all 2.000 with the same urgency, so the user abandons the
task. Here every unconfigured SKU is weighted by the money it moves
(projected demand x unit price) and returned ordered by that weight, with a
running cumulative share of the tenant's projected spend.

That turns an unreachable goal ("2.000 rows") into a reachable one ("82% of
your monthly purchase covered"), which is the entire point: the progress bar
the UI draws from this endpoint is weighted by MONEY, never by row count.

No pandas here (plain arithmetic over the stored forecast blob), so the
module sits happily on the backend side of the pandas boundary.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# Same conversion factor inventory/service.py uses: a weekly session forecasts
# units/week, a monthly one units/month, so a 30-day horizon is a different
# number of forecast steps at each grain.
_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}

# Which configuration fields we look at per SKU. `stock` and `cost` are the
# two that make a SKU unusable for a purchase decision (no stock -> no signal,
# no cost -> no money figure); `price` and `supplier` are reported so the UI
# can show what else is missing, but they alone do not make a SKU a gap.
BLOCKING_FIELDS = ("stock", "cost")

# Default share of projected spend we ask the user to reach in one sitting.
DEFAULT_TARGET_PCT = 80.0


def _steps_for_horizon(horizon_days: int, period: str) -> int:
    """How many forecast buckets `horizon_days` spans at this planning grain."""
    per = _DAYS_PER_PERIOD.get(period, 1)
    return max(1, round(horizon_days / per))


def projected_demand(model_forecasts: dict, steps: int) -> float:
    """
    Units the session projects for one SKU over `steps` buckets: the sum of
    each model's first `steps` points, averaged ACROSS models (never summed —
    models are alternative opinions of the same demand, not additive demand).
    """
    totals: list[float] = []
    for model_data in (model_forecasts or {}).values():
        pts = (model_data or {}).get("forecast") or []
        if not pts:
            continue
        total = 0.0
        for p in pts[:steps]:
            try:
                total += float(p.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
        totals.append(total)
    if not totals:
        return 0.0
    return max(0.0, sum(totals) / len(totals))


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _first_row_per_sku(stock_rows: list[dict]) -> dict[str, dict]:
    """
    One representative row per SKU. current_stock is summed across warehouses
    (the tenant's real total); the catalog attributes (cost, price, supplier)
    come from the first row seen, which is enough for a ranking — the precise
    per-warehouse precedence lives in inventory/service.py and is not needed
    to decide "is this SKU configured and how much money does it move".
    """
    out: dict[str, dict] = {}
    for r in stock_rows:
        sku = r.get("sku")
        if sku is None:
            continue
        existing = out.get(sku)
        if existing is None:
            out[sku] = dict(r)
            continue
        try:
            existing["current_stock"] = (
                float(existing.get("current_stock") or 0) + float(r.get("current_stock") or 0)
            )
        except (TypeError, ValueError):
            pass
        # Fill catalog attributes a previous (warehouse) row left empty.
        for field in ("unit_cost", "sale_price", "supplier", "display_name", "category"):
            if existing.get(field) in (None, "") and r.get(field) not in (None, ""):
                existing[field] = r[field]
    return out


def _as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _missing_fields(row: Optional[dict]) -> list[str]:
    """
    What this SKU still needs. With no row at all everything is missing.

    Note on `stock`: `inventory_stock.current_stock` is NOT NULL DEFAULT 0, so
    a row that exists cannot be told apart from one the user never touched —
    distinguishing "configured as 0" from "never set" is plan #2's provenance
    migration, not this endpoint's job. Until that lands, a SKU with a row
    counts as having its stock configured.
    """
    if row is None:
        return ["stock", "lead_time", "cost", "price", "supplier"]
    missing: list[str] = []
    if _as_float(row.get("unit_cost")) is None:
        missing.append("cost")
    if _as_float(row.get("sale_price")) is None:
        missing.append("price")
    if not (row.get("supplier") or "").strip():
        missing.append("supplier")
    return missing


def get_setup_gaps(
    tenant_id: str,
    session_id: str,
    horizon_days: int = 30,
    limit: int = 50,
    target_pct: float = DEFAULT_TARGET_PCT,
) -> dict:
    """
    SKUs of `session_id` that still lack stock configuration, ordered by the
    money they move, each carrying its share and the running cumulative share
    of the tenant's projected spend.

    `basis` tells the caller what the ordering actually means:
      - "money": at least one SKU carries a real cost/price, so the ranking is
        spend-weighted (the product promise).
      - "units": the tenant has given us no money figure anywhere, so the
        ranking degrades to projected volume. Saying so is the honest option;
        drawing a money bar over unit counts would not be.
    """
    from backend.db import session_store
    from backend.inventory import service as svc
    from backend.inventory.series import rollup_by_sku
    from backend.sessions import planning_service

    forecasts = rollup_by_sku(session_store.get_forecasts(tenant_id, session_id) or {})
    period = (planning_service.get_planning(tenant_id) or {}).get("period", "daily")
    steps = _steps_for_horizon(horizon_days, period)

    stock_by_sku = _first_row_per_sku(svc.list_stock(tenant_id))

    # Fallback unit price for SKUs with no money of their own: the tenant's own
    # median. Using it keeps a high-volume SKU with no cost from being ranked
    # as if it were free, which would push the biggest gaps to the bottom.
    known_costs = [
        c for c in (_as_float(r.get("unit_cost")) for r in stock_by_sku.values())
        if c is not None and c > 0
    ]
    known_prices = [
        p for p in (_as_float(r.get("sale_price")) for r in stock_by_sku.values())
        if p is not None and p > 0
    ]
    median_money = _median(known_costs) or _median(known_prices)

    entries: list[dict] = []
    for sku in sorted(forecasts.keys()):
        row = stock_by_sku.get(sku)
        demand = projected_demand(forecasts.get(sku) or {}, steps)

        unit_cost = _as_float(row.get("unit_cost")) if row else None
        sale_price = _as_float(row.get("sale_price")) if row else None
        # Purchase spend is what this screen is about, so the SKU's own cost
        # wins; its selling price is the next best proxy, then the tenant's
        # median, and only then "we have no money data at all".
        if unit_cost is not None and unit_cost > 0:
            unit_price, price_source = unit_cost, "unit_cost"
        elif sale_price is not None and sale_price > 0:
            unit_price, price_source = sale_price, "sale_price"
        elif median_money:
            unit_price, price_source = median_money, "tenant_median"
        else:
            unit_price, price_source = None, "none"

        missing = _missing_fields(row)
        entries.append({
            "sku": sku,
            "display_name": (row or {}).get("display_name"),
            "category": (row or {}).get("category"),
            "has_row": row is not None,
            "current_stock": _as_float((row or {}).get("current_stock")),
            "missing": missing,
            "is_gap": any(f in missing for f in BLOCKING_FIELDS),
            "projected_demand": round(demand, 2),
            "unit_price": round(unit_price, 4) if unit_price is not None else None,
            "price_source": price_source,
        })

    basis = "money" if median_money else "units"
    for e in entries:
        # In "units" basis every SKU is worth 1 per unit, so the ranking is by
        # projected volume and every share below is a share of units.
        price = e["unit_price"] if basis == "money" else 1.0
        e["projected_spend"] = round(e["projected_demand"] * (price or 0.0), 2)

    total_spend = sum(e["projected_spend"] for e in entries)
    gaps = [e for e in entries if e["is_gap"]]
    gap_spend = sum(e["projected_spend"] for e in gaps)
    covered_pct = (
        100.0 if total_spend <= 0 else round((total_spend - gap_spend) / total_spend * 100, 2)
    )

    # The whole point: order by MONEY. Ties (and the zero-spend tail) fall back
    # to demand and then to the SKU name so the list is deterministic.
    gaps.sort(key=lambda e: (-e["projected_spend"], -e["projected_demand"], e["sku"]))

    running = total_spend - gap_spend
    recommended_count = 0
    recommended_pct = covered_pct
    for rank, e in enumerate(gaps, start=1):
        e["rank"] = rank
        e["share_pct"] = round(e["projected_spend"] / total_spend * 100, 2) if total_spend > 0 else 0.0
        running += e["projected_spend"]
        e["cumulative_pct"] = round(running / total_spend * 100, 2) if total_spend > 0 else 100.0
        if recommended_count == 0 and e["cumulative_pct"] >= target_pct:
            recommended_count = rank
            recommended_pct = e["cumulative_pct"]
    if recommended_count == 0 and gaps:
        # Covering every gap still does not reach the target only when the
        # target is unreachable by definition (>100%); fall back to all of them.
        recommended_count = len(gaps)
        recommended_pct = gaps[-1]["cumulative_pct"]

    return {
        "session_id": session_id,
        "period": period,
        "horizon_days": horizon_days,
        "basis": basis,
        "target_pct": target_pct,
        "total_skus": len(entries),
        "configured_skus": len(entries) - len(gaps),
        "gap_skus": len(gaps),
        # Money already covered — this is what the progress bar fills, and it is
        # a share of SPEND, not of rows.
        "covered_pct": covered_pct,
        "total_projected_spend": round(total_spend, 2),
        "gap_projected_spend": round(gap_spend, 2),
        # "Configure these N and you reach X% of your monthly purchase."
        "recommended_count": recommended_count,
        "recommended_pct": recommended_pct,
        "items": gaps[:limit],
        "truncated": len(gaps) > limit,
    }

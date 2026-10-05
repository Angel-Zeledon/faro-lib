"""Money at risk: how much sales value a purchase delay puts in danger.

Pure arithmetic, no I/O and no pandas. It never changes a signal or a
quantity; it only ranks the rows that already carry PEDIR_YA / PEDIR_PRONTO.

    money_at_risk = demand_per_period x max(0, exposed_periods) x unit_value

`exposed_periods` is the part of the lead time the stock does not cover:
lead_time - coverage. All time arguments are in the SAME unit (the active
planning period, exactly as `coverage_days` and `daily_demand` already are).

Honesty rules (CLAUDE.md, silent failures):
  * unit value is the sale price when there is a positive one, else the unit
    cost, else unknown. Nothing is invented and unknown is never zero;
  * a row whose amount cannot be computed returns None and sorts AFTER every
    valued row, keeping its previous relative order;
  * a stock-out already happening counts the periods since it started only
    when the data has them, otherwise the whole lead time.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

BASIS_PRICE = "price"
BASIS_COST = "cost"
BASIS_UNKNOWN = "unknown"


def _positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def unit_value(sale_price: Any, unit_cost: Any) -> tuple[Optional[float], str]:
    """(value, basis): price if positive, else cost if positive, else unknown."""
    price = _positive(sale_price)
    if price is not None:
        return price, BASIS_PRICE
    cost = _positive(unit_cost)
    if cost is not None:
        return cost, BASIS_COST
    return None, BASIS_UNKNOWN


def money_at_risk(
    demand_per_period: Any,
    lead_time: Any,
    coverage: Any,
    sale_price: Any = None,
    unit_cost: Any = None,
    *,
    in_stockout: bool = False,
    periods_since_stockout: Any = None,
) -> tuple[Optional[float], str]:
    """Return (amount | None, basis).

    amount is None when it cannot be computed (no value, no demand figure, no
    lead time); 0.0 only when it was computed and nothing is exposed (coverage
    reaches the lead time, or zero demand).
    """
    value, basis = unit_value(sale_price, unit_cost)
    if value is None:
        return None, BASIS_UNKNOWN
    try:
        demand = float(demand_per_period)
        lead = float(lead_time)
    except (TypeError, ValueError):
        return None, basis
    if not (math.isfinite(demand) and math.isfinite(lead)):
        return None, basis
    if demand <= 0:
        return 0.0, basis
    if lead <= 0:
        return 0.0, basis

    if in_stockout:
        since = _positive(periods_since_stockout)
        exposed = since if since is not None else lead
    else:
        if coverage is None:
            return None, basis
        try:
            cov = float(coverage)
        except (TypeError, ValueError):
            return None, basis
        if math.isnan(cov):
            return None, basis
        # Negative stock is a stock-out, not extra exposure: coverage floors at 0.
        exposed = lead - max(0.0, cov)
    amount = demand * max(0.0, exposed) * value
    if not math.isfinite(amount):
        return None, basis
    return round(amount, 2), basis


def sort_by_money_at_risk(items: Iterable[dict]) -> list[dict]:
    """Largest amount first; rows without an amount after valued ones. The sort
    is stable, so ties and tenants with no values keep the incoming order."""
    return sorted(
        items,
        key=lambda i: (
            i.get("money_at_risk") is None,
            -(i.get("money_at_risk") or 0.0),
        ),
    )

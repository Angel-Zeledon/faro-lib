"""Arithmetic of a purchase budget that needs no database: period bounds, how
far through the period we are, how fast the money is going, and which ordered
lines count against a budget's scope.

Pure Python, no I/O (the allocation itself lives in ForecastingCore:
`forecasting_core/business/budget_allocation.py`).
"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Any, Iterable, Optional

PERIOD_TYPES = ("month", "quarter", "custom")
SCOPE_TYPES = ("company", "warehouse", "supplier", "category")
MAX_PERIOD_DAYS = 3660
OPEN_RECEPTION = ("pending", "partial", "not_received")

PACE_ON_TRACK = "on_track"
PACE_AHEAD = "ahead"          # spending faster than the calendar
PACE_OVER = "over"            # already past the amount
# Below this many elapsed days a straight-line projection is noise.
MIN_DAYS_FOR_PROJECTION = 3


def period_bounds(period_type: str, start: date, end: Optional[date] = None) -> tuple[date, date]:
    """The inclusive [start, end] of a budget period. For `month` and `quarter`
    `start` is any day inside the period and `end` is ignored; `custom` takes
    both as given."""
    if period_type == "month":
        first = start.replace(day=1)
        return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])
    if period_type == "quarter":
        month0 = 3 * ((start.month - 1) // 3) + 1
        first = date(start.year, month0, 1)
        last_month = month0 + 2
        return first, date(start.year, last_month, calendar.monthrange(start.year, last_month)[1])
    if period_type == "custom":
        if end is None:
            raise ValueError("custom period needs an end date")
        if end < start:
            raise ValueError("period ends before it starts")
        if (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise ValueError("period too long")
        return start, end
    raise ValueError(f"unknown period type {period_type!r}")


def days_in(start: date, end: date) -> int:
    return (end - start).days + 1


def elapsed(start: date, end: date, today: date) -> dict[str, Any]:
    total = days_in(start, end)
    if today < start:
        done = 0
    elif today > end:
        done = total
    else:
        done = (today - start).days + 1
    return {"total_days": total, "elapsed_days": done,
            "elapsed_fraction": round(done / total, 6) if total else 1.0,
            "days_left": total - done,
            "state": "upcoming" if today < start else ("closed" if today > end else "running")}


def _num(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def burn(amount: float, used: float, start: date, end: date, today: date) -> dict[str, Any]:
    """Money used against the amount, next to time used, and where the straight
    line ends. `projected_overrun` is None when no honest projection exists
    (too early in the period, or the period has not started)."""
    amount = max(0.0, _num(amount))
    used = max(0.0, _num(used))
    t = elapsed(start, end, today)
    used_fraction = (used / amount) if amount > 0 else None  # a zero amount has no fraction
    projected_total: Optional[float] = None
    reliable = False
    if t["state"] == "closed":
        projected_total, reliable = used, True
    elif t["state"] == "running" and t["elapsed_days"] >= MIN_DAYS_FOR_PROJECTION:
        projected_total = used * t["total_days"] / t["elapsed_days"]
        reliable = True
    overrun = None
    if projected_total is not None:
        overrun = round(max(0.0, projected_total - amount), 2)
    if used > amount + 1e-9:
        pace = PACE_OVER
    elif t["state"] == "running" and overrun:  # the straight line ends past the amount
        pace = PACE_AHEAD
    else:
        pace = PACE_ON_TRACK
    return {
        **t,
        "used_fraction": None if used_fraction is None else round(used_fraction, 6),
        "projected_total": None if projected_total is None else round(projected_total, 2),
        "projected_overrun": overrun,
        "projection_reliable": reliable,
        "pace": pace,
    }


def remaining(amount: float, spent: float, committed: float) -> float:
    """Money still free. May be negative (overspent); callers decide what to show."""
    return round(_num(amount) - _num(spent) - _num(committed), 2)


def effective_remaining(own: float, parent: Optional[float]) -> tuple[float, str]:
    """What a budget may still use: its own remainder, but never more than the
    budget it sits inside. Returns (amount floored at 0, 'self' | 'parent')."""
    if parent is not None and parent < own:
        return max(0.0, parent), "parent"
    return max(0.0, own), "self"


def _fold(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def scope_matcher(scope_type: str, scope_names: dict[str, Any]):
    """A predicate over a row dict. `scope_names` carries what the budget's
    stored value resolves to: `warehouse` (a name), `supplier_id` + `supplier`
    (id and name) or `category` (text). Row keys: `warehouse`, `supplier_id`,
    `supplier`, `category`. A company budget matches everything."""
    if scope_type == "company":
        return lambda row: True
    if scope_type == "warehouse":
        want = _fold(scope_names.get("warehouse"))
        return lambda row: bool(want) and _fold(row.get("warehouse")) == want
    if scope_type == "supplier":
        sid = scope_names.get("supplier_id")
        name = _fold(scope_names.get("supplier"))

        def _sup(row: dict) -> bool:
            if sid and row.get("supplier_id") == sid:
                return True
            return bool(name) and _fold(row.get("supplier")) == name
        return _sup
    if scope_type == "category":
        want = _fold(scope_names.get("category"))
        return lambda row: bool(want) and _fold(row.get("category")) == want
    raise ValueError(f"unknown scope type {scope_type!r}")


def ordered_value(rows: Iterable[dict], matches) -> dict[str, Any]:
    """Sum the ordered value of purchase-order item rows that match a scope.

    Each row: `value` (qty x unit cost, or None when the line has no cost),
    `open` (bool: the order is not fully received yet), plus whatever the
    matcher reads. Lines with no cost are COUNTED, never treated as zero: the
    caller shows the count next to the figure."""
    spent = committed = 0.0
    unknown = 0
    lines = 0
    for row in rows:
        if not matches(row):
            continue
        lines += 1
        value = row.get("value")
        if value is None:
            unknown += 1
            continue
        if row.get("open"):
            committed += float(value)
        else:
            spent += float(value)
    return {"spent": round(spent, 2), "committed": round(committed, 2),
            "unknown_cost_lines": unknown, "lines": lines}


def order_check(order_value: float, free: float, unknown_cost_lines: int = 0) -> dict[str, Any]:
    """Would an order of `order_value` fit in `free`? `over_by` is 0 when it fits."""
    free = max(0.0, _num(free))
    over = max(0.0, round(_num(order_value) - free, 2))
    return {"order_value": round(_num(order_value), 2), "remaining": round(free, 2),
            "over_by": over, "exceeds": over > 0, "unknown_cost_lines": int(unknown_cost_lines)}


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)

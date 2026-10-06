"""Purchase budget allocation: what to fund first when the recommended orders
cost more than the money left to spend.

Pure arithmetic, no I/O, no pandas, no randomness. It never changes a
recommendation: every line comes back with the quantity the engine recommended
(or, for a partial line, a smaller quantity that is only a PROPOSAL for the
caller to show). It decides ORDER and AFFORDABILITY, nothing else.

The rule
--------
1. Candidate lines are PEDIR_YA first, then PEDIR_PRONTO. Any other signal is
   not a candidate and is reported as ignored.
2. Inside a tier lines are visited by RISK DENSITY, money at risk per unit of
   money to spend (money_at_risk / line cost), highest first. Ties break on
   larger money at risk, then ABC class (A before B before C), then
   (sku, warehouse, supplier), so the result never depends on input order.
   A line with no money-at-risk figure sorts AFTER every valued line.
3. A visited line is funded WHOLE when its full cost fits in what is left.
   Otherwise it is funded PARTIALLY only if the affordable quantity is at least
   its MOQ and at least `min_partial_fraction` of the recommendation (a smaller
   order is a useless order). Otherwise it is deferred, with the reason. The
   walk continues past a deferred line: a cheaper line further down may still
   fit.
4. A line whose unit cost is unknown is NEVER treated as free. It is listed as
   `cost_unknown`, left out of the cap arithmetic, and the result carries a
   warning naming how many there are.

Guarantees (each one has a test)
--------------------------------
* the funded cost never exceeds the budget handed in;
* the same lines in any input order give the identical result;
* a funded or partial quantity is never below the line's MOQ (unless the engine
  itself recommended less than its MOQ, in which case the whole line is the only
  funded amount);
* with `remaining=None`, or a budget at least the cost of every costed line,
  every costed candidate is funded in full: the same as having no budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

TIER_ORDER = {"PEDIR_YA": 0, "PEDIR_PRONTO": 1}

STATUS_FUNDED = "funded"
STATUS_PARTIAL = "partial"
STATUS_DEFERRED = "deferred"
STATUS_COST_UNKNOWN = "cost_unknown"
STATUS_IGNORED = "ignored"

# Why a line is not (fully) funded. Stable codes, rendered by the frontend.
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_MOQ_EXCEEDS_REMAINDER = "moq_exceeds_remainder"
REASON_REMAINDER_TOO_SMALL = "remainder_too_small"
REASON_BUDGET_PARTIAL = "budget_partial"
REASON_NO_COST = "no_cost"
REASON_NO_QUANTITY = "no_quantity"
REASON_NOT_ACTIONABLE = "not_actionable"

DEFAULT_MIN_PARTIAL_FRACTION = 0.25
_EPS = 1e-9
_ABC_RANK = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True)
class BudgetLine:
    """One candidate order line."""
    key: str
    sku: str
    quantity: float
    unit_cost: Optional[float]
    signal: str
    money_at_risk: Optional[float] = None
    abc: Optional[str] = None
    moq: Optional[float] = None
    supplier: Optional[str] = None
    warehouse: Optional[str] = None


@dataclass
class AllocatedLine:
    key: str
    sku: str
    supplier: Optional[str]
    warehouse: Optional[str]
    signal: str
    abc: Optional[str]
    status: str
    reason: Optional[str]
    recommended_qty: float
    funded_qty: float
    unit_cost: Optional[float]
    full_cost: Optional[float]
    funded_cost: float
    money_at_risk: Optional[float]
    risk_density: Optional[float]
    uncovered_risk: Optional[float]
    moq: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class AllocationResult:
    lines: list[AllocatedLine]
    summary: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"lines": [ln.as_dict() for ln in self.lines],
                "summary": self.summary, "warnings": self.warnings}


def _finite_positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _finite_nonneg(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0:
        return None
    return v


def line_from_mapping(row: Mapping[str, Any], key: Optional[str] = None) -> BudgetLine:
    """Build a line from a status row (the shape `/inventory/status` returns)."""
    sku = str(row.get("sku") or "")
    wh = row.get("warehouse")
    sup = row.get("supplier")
    qty = _finite_nonneg(row.get("recommended_qty", row.get("quantity"))) or 0.0
    return BudgetLine(
        key=key or "|".join((sku, str(wh or ""), str(sup or ""))),
        sku=sku,
        quantity=qty,
        unit_cost=_finite_positive(row.get("unit_cost")),
        signal=str(row.get("signal") or ""),
        money_at_risk=_finite_nonneg(row.get("money_at_risk")),
        abc=(str(row.get("abc")).upper() if row.get("abc") else None),
        moq=_finite_positive(row.get("moq")),
        supplier=sup,
        warehouse=wh,
    )


def _step(moq: Optional[float]) -> float:
    """The smallest quantity increment: one unit, or the MOQ itself when the
    SKU is sold by weight/volume with a fractional minimum."""
    return moq if moq is not None and 0 < moq < 1 else 1.0


def _sort_key(line: BudgetLine, cost: float):
    risk = line.money_at_risk
    density = (risk / cost) if (risk is not None and cost > 0) else None
    return (
        TIER_ORDER[line.signal],
        density is None,
        -(density if density is not None else 0.0),
        -(risk if risk is not None else 0.0),
        _ABC_RANK.get((line.abc or "").upper(), 3),
        line.sku, line.warehouse or "", line.supplier or "", line.key,
    )


def _result_line(line: BudgetLine, status: str, reason: Optional[str], funded_qty: float,
                 full_cost: Optional[float], funded_cost: float,
                 density: Optional[float]) -> AllocatedLine:
    risk = line.money_at_risk
    uncovered = None
    if risk is not None:
        if line.quantity > 0 and status in (STATUS_FUNDED, STATUS_PARTIAL):
            frac = min(1.0, funded_qty / line.quantity)
            uncovered = round(risk * (1.0 - frac), 2)
        elif status in (STATUS_DEFERRED, STATUS_COST_UNKNOWN):
            uncovered = round(risk, 2)
    return AllocatedLine(
        key=line.key, sku=line.sku, supplier=line.supplier, warehouse=line.warehouse,
        signal=line.signal, abc=line.abc, status=status, reason=reason,
        recommended_qty=line.quantity, funded_qty=funded_qty, unit_cost=line.unit_cost,
        full_cost=None if full_cost is None else round(full_cost, 2),
        funded_cost=round(funded_cost, 2), money_at_risk=risk,
        risk_density=None if density is None else round(density, 6),
        uncovered_risk=uncovered, moq=line.moq,
    )


def allocate_budget(
    lines: Iterable[BudgetLine],
    remaining: Optional[float],
    *,
    min_partial_fraction: float = DEFAULT_MIN_PARTIAL_FRACTION,
) -> AllocationResult:
    """Choose what to fund. `remaining=None` means no cap (everything costed is
    funded in full); a number is the money left, floored at zero."""
    all_lines = list(lines)
    keys = [ln.key for ln in all_lines]
    if len(set(keys)) != len(keys):
        raise ValueError("budget lines need unique keys")
    cap: Optional[float] = None
    if remaining is not None:
        try:
            cap = float(remaining)
        except (TypeError, ValueError):
            cap = 0.0
        if not math.isfinite(cap):
            cap = None if cap > 0 else 0.0
        else:
            cap = max(0.0, cap)
    frac_min = min(1.0, max(0.0, float(min_partial_fraction)))

    out: list[AllocatedLine] = []
    costed: list[tuple[BudgetLine, float]] = []
    unknown_cost = 0
    for ln in all_lines:
        if ln.signal not in TIER_ORDER:
            out.append(_result_line(ln, STATUS_IGNORED, REASON_NOT_ACTIONABLE, 0.0, None, 0.0, None))
        elif not (ln.quantity > 0):
            out.append(_result_line(ln, STATUS_IGNORED, REASON_NO_QUANTITY, 0.0, None, 0.0, None))
        elif _finite_positive(ln.unit_cost) is None:
            unknown_cost += 1
            out.append(_result_line(ln, STATUS_COST_UNKNOWN, REASON_NO_COST, 0.0, None, 0.0, None))
        else:
            costed.append((ln, float(ln.unit_cost) * ln.quantity))

    costed.sort(key=lambda p: _sort_key(p[0], p[1]))

    left = cap  # None = unlimited
    funded_total = 0.0
    for ln, cost in costed:
        risk = ln.money_at_risk
        density = (risk / cost) if (risk is not None and cost > 0) else None
        unit = float(ln.unit_cost)
        if left is None or cost <= left + _EPS:
            funded_total += cost
            if left is not None:
                left = max(0.0, left - cost)
            out.append(_result_line(ln, STATUS_FUNDED, None, ln.quantity, cost, cost, density))
            continue

        step = _step(ln.moq)
        floor_qty = ln.moq if ln.moq is not None else step
        afford_units = math.floor(left / unit / step + _EPS) * step
        if afford_units < step - _EPS:
            reason = REASON_BUDGET_EXHAUSTED
            out.append(_result_line(ln, STATUS_DEFERRED, reason, 0.0, cost, 0.0, density))
        elif afford_units < floor_qty - _EPS:
            out.append(_result_line(ln, STATUS_DEFERRED, REASON_MOQ_EXCEEDS_REMAINDER,
                                    0.0, cost, 0.0, density))
        elif afford_units < frac_min * ln.quantity - _EPS:
            out.append(_result_line(ln, STATUS_DEFERRED, REASON_REMAINDER_TOO_SMALL,
                                    0.0, cost, 0.0, density))
        else:
            qty = min(afford_units, ln.quantity)
            part_cost = qty * unit
            # Rounding guard: never let float noise push the total over the cap.
            while part_cost > left + _EPS and qty - step >= floor_qty - _EPS:
                qty -= step
                part_cost = qty * unit
            if part_cost > left + _EPS:
                out.append(_result_line(ln, STATUS_DEFERRED, REASON_MOQ_EXCEEDS_REMAINDER,
                                        0.0, cost, 0.0, density))
            else:
                funded_total += part_cost
                left = max(0.0, left - part_cost)
                out.append(_result_line(ln, STATUS_PARTIAL, REASON_BUDGET_PARTIAL,
                                        qty, cost, part_cost, density))

    # Stable presentation order: tier, then the walk order the funded lines were
    # visited in is kept for costed lines; the rest follow, sorted by key.
    walk = {ln.key: i for i, (ln, _) in enumerate(costed)}
    out.sort(key=lambda r: (r.key not in walk, walk.get(r.key, 0), r.sku, r.warehouse or "",
                            r.supplier or "", r.key))

    def _sum(rows: Iterable[AllocatedLine], attr: str) -> float:
        return round(sum((getattr(r, attr) or 0.0) for r in rows), 2)

    candidates = [r for r in out if r.status != STATUS_IGNORED]
    valued = [r for r in candidates if r.money_at_risk is not None and r.status != STATUS_COST_UNKNOWN]
    unvalued_costed = sum(1 for r in candidates
                          if r.money_at_risk is None and r.status != STATUS_COST_UNKNOWN)
    summary = {
        "capped": cap is not None,
        "budget_remaining_in": None if cap is None else round(cap, 2),
        "budget_remaining_after": None if left is None else round(left, 2),
        "funded_cost": round(funded_total, 2),
        "full_cost": _sum(candidates, "full_cost"),
        "funded_lines": sum(1 for r in out if r.status == STATUS_FUNDED),
        "partial_lines": sum(1 for r in out if r.status == STATUS_PARTIAL),
        "deferred_lines": sum(1 for r in out if r.status == STATUS_DEFERRED),
        "cost_unknown_lines": unknown_cost,
        "ignored_lines": sum(1 for r in out if r.status == STATUS_IGNORED),
        "money_at_risk_total": round(sum(r.money_at_risk for r in valued), 2),
        "money_at_risk_uncovered": round(
            sum((r.uncovered_risk or 0.0) for r in valued), 2),
        # Lines whose risk is unknown cannot add to either figure; said out loud.
        "risk_unknown_lines": unvalued_costed,
        "cost_unknown_risk": round(sum(
            (r.money_at_risk or 0.0) for r in candidates if r.status == STATUS_COST_UNKNOWN), 2),
    }
    warnings: list[dict[str, Any]] = []
    if unknown_cost:
        warnings.append({"code": "cost_unknown_lines", "params": {"count": unknown_cost}})
    if unvalued_costed:
        warnings.append({"code": "risk_unknown_lines", "params": {"count": unvalued_costed}})
    return AllocationResult(lines=out, summary=summary, warnings=warnings)

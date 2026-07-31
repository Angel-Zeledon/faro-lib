"""
Policy backtest — score the DECISION, not the forecast.

"WAPE 8%" tells a distributor nothing. What the product is for is a purchase
order, and a purchase order is judged by whether the shelf stayed full and how
much cash sat in the warehouse doing nothing. Those two are in tension, which is
exactly why a single accuracy number cannot stand in for them: a forecast that
is uniformly 20% high can post a respectable WAPE while quietly doubling the
inventory a tenant carries.

This module replays the ordering policy over a window of ACTUAL demand, driving
it with the forecast the engine would have had at the time, and reports what the
buyer would have lived through: fill rate, stockout buckets, average inventory,
days of cover, capital tied up.

Two properties make the output honest:

  * The simulation consumes REAL demand, not the forecast. A model cannot score
    well by being self-consistent.
  * The same simulation runs against a naive baseline forecast, so the headline
    is a difference and not an absolute number floating free of any reference.
    A policy that stocks out 3% of the time might be excellent or terrible; a
    policy that stocks out 3% where the naive one stocks out 11% is an
    unambiguous improvement.

Example:
    outcome = simulate_policy(
        demand=actuals, forecast=fc, lead_time=7,
        safety_stock=40.0, initial_stock=120.0, unit_cost=3.5,
    )
    outcome.fill_rate, outcome.capital_tied_up
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np


@dataclass
class PolicyOutcome:
    """What the buyer would have experienced over the simulated window."""
    n_buckets: int = 0
    total_demand: float = 0.0
    units_short: float = 0.0
    units_ordered: float = 0.0
    stockout_buckets: int = 0
    avg_inventory: float = 0.0
    peak_inventory: float = 0.0
    unit_cost: Optional[float] = None

    @property
    def fill_rate(self) -> float:
        """Share of demand served from stock. The number a buyer feels."""
        if self.total_demand <= 0:
            return 1.0
        return max(0.0, 1.0 - self.units_short / self.total_demand)

    @property
    def stockout_rate(self) -> float:
        if self.n_buckets <= 0:
            return 0.0
        return self.stockout_buckets / self.n_buckets

    @property
    def days_of_cover(self) -> float:
        daily = self.total_demand / self.n_buckets if self.n_buckets else 0.0
        return self.avg_inventory / daily if daily > 0 else 0.0

    @property
    def capital_tied_up(self) -> Optional[float]:
        if self.unit_cost is None:
            return None
        return self.avg_inventory * float(self.unit_cost)

    def to_dict(self) -> dict:
        return {
            "n_buckets": self.n_buckets,
            "total_demand": round(self.total_demand, 2),
            "units_short": round(self.units_short, 2),
            "units_ordered": round(self.units_ordered, 2),
            "stockout_buckets": self.stockout_buckets,
            "fill_rate": round(self.fill_rate, 4),
            "stockout_rate": round(self.stockout_rate, 4),
            "avg_inventory": round(self.avg_inventory, 2),
            "peak_inventory": round(self.peak_inventory, 2),
            "days_of_cover": round(self.days_of_cover, 2),
            "capital_tied_up": (round(self.capital_tied_up, 2)
                                if self.capital_tied_up is not None else None),
        }


def _forecast_window(forecast: Sequence[float], start: int, length: int) -> np.ndarray:
    """
    The next `length` buckets of forecast as seen from bucket `start`.

    Past the end of the forecast the last value is carried forward. That is a
    deliberate, stated degradation: it keeps the simulation running to the end
    of the demand window instead of silently ordering zero, which would
    manufacture stockouts that the policy never actually caused.
    """
    arr = np.asarray(forecast, dtype=float)
    if arr.size == 0:
        return np.zeros(length)
    window = arr[start:start + length]
    if window.size < length:
        pad = np.full(length - window.size, arr[-1] if arr.size else 0.0)
        window = np.concatenate([window, pad])
    return window


def simulate_policy(
    demand: Sequence[float],
    forecast: Sequence[float],
    lead_time: int,
    safety_stock: float,
    initial_stock: float = 0.0,
    unit_cost: Optional[float] = None,
    moq: float = 0.0,
    review_period: int = 1,
) -> PolicyOutcome:
    """
    Replay a continuous-review reorder-point policy over actual demand.

    At each review the inventory POSITION (on hand plus everything already
    ordered and in transit) is compared against a reorder point built from the
    forecast of the next `lead_time` buckets plus the safety stock. Counting
    in-transit stock is what stops the policy from re-ordering the same
    shortfall on every bucket of the lead time — a simulator that omits it
    reports enormous inventory and flatters any forecast, because the excess is
    an artefact of the simulation rather than of the decision being tested.

    Unmet demand is LOST, not backordered: a distributor's customer buys from
    somebody else that day.
    """
    actual = np.asarray(demand, dtype=float)
    n = actual.size
    if n == 0:
        return PolicyOutcome(unit_cost=unit_cost)

    lead = max(0, int(lead_time))
    on_hand = float(initial_stock)
    arrivals: Dict[int, float] = {}
    on_order = 0.0

    outcome = PolicyOutcome(n_buckets=n, unit_cost=unit_cost)
    inventory_trace: List[float] = []

    for t in range(n):
        # Receive whatever was ordered `lead` buckets ago.
        arriving = arrivals.pop(t, 0.0)
        on_hand += arriving
        on_order -= arriving

        review = max(1, int(review_period))
        if t % review == 0:
            # The PROTECTION INTERVAL is the lead time plus the review period,
            # not the lead time alone. An order placed now does not protect
            # anything until it lands, and the next chance to react is one
            # review later — so the position has to cover both. Sizing the
            # reorder point on L alone leaves the policy exactly one review
            # period short, which shows up as a permanent low-level stockout
            # rate that looks like a forecasting problem and is not one.
            expected = float(np.sum(_forecast_window(forecast, t, lead + review)))
            reorder_point = expected + float(safety_stock)
            position = on_hand + on_order
            if position < reorder_point:
                qty = reorder_point - position
                if moq and moq > 0:
                    qty = math.ceil(qty / moq) * moq
                if qty > 0:
                    arrivals[t + lead] = arrivals.get(t + lead, 0.0) + qty
                    on_order += qty
                    outcome.units_ordered += qty

        want = float(actual[t])
        sold = min(on_hand, want)
        short = want - sold
        on_hand -= sold

        outcome.total_demand += want
        if short > 1e-9:
            outcome.units_short += short
            outcome.stockout_buckets += 1

        inventory_trace.append(on_hand)

    outcome.avg_inventory = float(np.mean(inventory_trace)) if inventory_trace else 0.0
    outcome.peak_inventory = float(np.max(inventory_trace)) if inventory_trace else 0.0
    return outcome


def naive_forecast(history: Sequence[float], horizon: int) -> np.ndarray:
    """
    The reference every result is stated against: repeat the last observation.

    This is the policy a distributor runs without the product — "order about
    what we sold last time" — so it is the only baseline whose comparison means
    anything commercially.
    """
    arr = np.asarray(history, dtype=float)
    last = float(arr[-1]) if arr.size else 0.0
    return np.full(max(0, int(horizon)), max(0.0, last))


@dataclass
class PolicyComparison:
    """The engine's policy against the do-nothing policy, on identical demand."""
    model: PolicyOutcome
    baseline: PolicyOutcome
    model_name: str = ""

    def to_dict(self) -> dict:
        model, baseline = self.model.to_dict(), self.baseline.to_dict()
        capital_delta = None
        if (self.model.capital_tied_up is not None
                and self.baseline.capital_tied_up is not None):
            capital_delta = round(
                self.baseline.capital_tied_up - self.model.capital_tied_up, 2)
        return {
            "model": self.model_name,
            "policy": model,
            "baseline": baseline,
            "fill_rate_gain": round(model["fill_rate"] - baseline["fill_rate"], 4),
            "stockouts_avoided": baseline["stockout_buckets"] - model["stockout_buckets"],
            "inventory_delta": round(model["avg_inventory"] - baseline["avg_inventory"], 2),
            "capital_freed": capital_delta,
        }


def backtest_policy(
    demand: Sequence[float],
    forecast: Sequence[float],
    history: Sequence[float],
    lead_time: int,
    safety_stock: float,
    initial_stock: Optional[float] = None,
    unit_cost: Optional[float] = None,
    moq: float = 0.0,
    model_name: str = "",
) -> PolicyComparison:
    """
    Run the same policy twice — once on the model's forecast, once on naive.

    Both runs start from the same stock and face the same demand, so every
    difference in the outcome is attributable to the forecast and nothing else.
    """
    actual = np.asarray(demand, dtype=float)
    start = (float(initial_stock) if initial_stock is not None
             else float(np.mean(actual) * max(1, lead_time)) if actual.size else 0.0)

    return PolicyComparison(
        model_name=model_name,
        model=simulate_policy(
            demand=actual, forecast=forecast, lead_time=lead_time,
            safety_stock=safety_stock, initial_stock=start,
            unit_cost=unit_cost, moq=moq,
        ),
        baseline=simulate_policy(
            demand=actual, forecast=naive_forecast(history, len(actual)),
            lead_time=lead_time, safety_stock=safety_stock, initial_stock=start,
            unit_cost=unit_cost, moq=moq,
        ),
    )


def aggregate(comparisons: Sequence[PolicyComparison]) -> dict:
    """
    Catalogue-level headline.

    Fill rate is weighted by demand rather than averaged across SKUs: a stockout
    on the SKU that carries the business is not the same event as one on a SKU
    that sells twice a month, and an unweighted mean says it is.
    """
    if not comparisons:
        return {}

    def _fill(select) -> float:
        served = sum(select(c).total_demand - select(c).units_short for c in comparisons)
        total = sum(select(c).total_demand for c in comparisons)
        return round(served / total, 4) if total > 0 else 1.0

    model_capital = [c.model.capital_tied_up for c in comparisons
                     if c.model.capital_tied_up is not None]
    base_capital = [c.baseline.capital_tied_up for c in comparisons
                    if c.baseline.capital_tied_up is not None]

    return {
        "n_series": len(comparisons),
        "fill_rate": _fill(lambda c: c.model),
        "baseline_fill_rate": _fill(lambda c: c.baseline),
        "stockout_buckets": sum(c.model.stockout_buckets for c in comparisons),
        "baseline_stockout_buckets": sum(c.baseline.stockout_buckets for c in comparisons),
        "stockouts_avoided": sum(
            c.baseline.stockout_buckets - c.model.stockout_buckets for c in comparisons),
        "avg_inventory": round(
            float(np.mean([c.model.avg_inventory for c in comparisons])), 2),
        "baseline_avg_inventory": round(
            float(np.mean([c.baseline.avg_inventory for c in comparisons])), 2),
        "capital_tied_up": round(sum(model_capital), 2) if model_capital else None,
        "baseline_capital_tied_up": round(sum(base_capital), 2) if base_capital else None,
    }

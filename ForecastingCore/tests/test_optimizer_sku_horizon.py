"""
Per-SKU planning horizon in the MILP (math audit 2026-10-01, O1).

An order arrives at bucket t only for t > lead time, so a SKU whose lead time
reached the horizon had every arrival gated and was planned at 0. The API now
extends such a SKU's horizon; this file pins what the engine does with it:

- the reproduction from the audit (demand 10/bucket, lead 15, horizon 14)
  orders nothing on the old horizon and orders once the horizon reaches past
  the lead time;
- a SKU that is NOT extended gets exactly the decisions it got before, even
  when it shares the problem with one that is (the golden case);
- the size gate counts only the variables the per-SKU horizons leave free, and
  is unchanged when no SKU has its own horizon;
- the greedy fallback honours the same horizons.
"""

import pytest

from forecasting_core.business.optimizer import (
    OptimizationInput, VariableIndex, _lanes_with_fixed_cost, live_var_count, optimize,
)


def _inp(skus, horizon, *, lead, demand=10.0, stock=0.0, own=None, warehouses=("w",)):
    """`lead`, `demand`, `stock`: per-SKU dicts or a scalar for every SKU."""
    def per(v, s):
        return v[s] if isinstance(v, dict) else v
    whs = list(warehouses)
    return OptimizationInput(
        skus=list(skus), warehouses=whs, horizon=horizon,
        demand={(s, w): [per(demand, s) if w == whs[0] else 0.0] * horizon
                for s in skus for w in whs},
        stock0={(s, w): (per(stock, s) if w == whs[0] else 0.0) for s in skus for w in whs},
        lead_time_buckets={s: per(lead, s) for s in skus},
        holding_cost={s: 0.0055 for s in skus},
        stockout_cost={s: 3.0 for s in skus},
        order_cost={s: 1.0 for s in skus},
        horizon_by_sku=dict(own or {}),
    )


def _ordered(result, sku):
    return sum(q for (s, _w, _t), q in result.orders.items() if s == sku)


class TestTheAuditReproduction:

    def test_lead_at_or_past_the_horizon_ordered_nothing(self):
        """The defect, kept as a pin of what the old horizon does."""
        r = optimize(_inp(["A"], 14, lead=15))
        assert r.status == "optimal"
        assert _ordered(r, "A") == 0

    def test_reaching_past_the_lead_time_orders_the_post_arrival_demand(self):
        """Horizon 16 = lead 15 + one bucket: the order arriving at 16 is
        planned. 10 units, net of nothing on hand."""
        r = optimize(_inp(["A"], 16, lead=15))
        assert r.status == "optimal"
        assert _ordered(r, "A") == pytest.approx(10.0)

    def test_stock_still_on_hand_at_arrival_is_netted(self):
        """180 on hand covers buckets 1..18; arrivals 16..22 need 70; 30 are
        left at bucket 15, so 40 are bought."""
        r = optimize(_inp(["A"], 22, lead=15, stock=180.0))
        assert _ordered(r, "A") == pytest.approx(40.0)


class TestAnUnextendedSkuIsUntouched:

    def test_golden_decisions_survive_a_neighbour_with_a_longer_horizon(self):
        alone = optimize(_inp(["A"], 14, lead=10))
        assert _ordered(alone, "A") == pytest.approx(40.0)   # the audit's own number

        together = optimize(_inp(
            ["A", "B"], 30, lead={"A": 10, "B": 25}, own={"A": 14}))
        assert together.status == "optimal"
        for t in range(1, 15):
            assert together.orders[("A", "w", t)] == alone.orders[("A", "w", t)]
            assert together.shortages[("A", "w", t)] == alone.shortages[("A", "w", t)]
        for t in range(15, 31):
            assert together.orders[("A", "w", t)] == 0
            assert together.shortages[("A", "w", t)] == 0
        assert _ordered(together, "B") > 0

    def test_carrying_stock_past_its_own_horizon_costs_nothing(self):
        """A's leftover stock rides through buckets 15..30 at no cost, or
        the neighbour's longer horizon would change A's total."""
        alone = optimize(_inp(["A"], 14, lead=10, stock=500.0))
        together = optimize(_inp(["A"], 30, lead=10, stock=500.0, own={"A": 14}))
        assert together.total_cost == pytest.approx(alone.total_cost)

    def test_demand_past_its_own_horizon_is_ignored_not_infeasible(self):
        inp = _inp(["A"], 30, lead=10, own={"A": 14})
        r = optimize(inp)
        assert r.status == "optimal"
        assert _ordered(r, "A") == pytest.approx(40.0)


class TestTheSizeGate:

    def test_unchanged_without_per_sku_horizons(self):
        inp = _inp(["A", "B"], 14, lead=5, warehouses=("w", "v"))
        idx = VariableIndex(inp.skus, inp.warehouses, inp.horizon, _lanes_with_fixed_cost(inp))
        assert live_var_count(inp, idx) == idx.n_vars

    def test_counts_only_the_free_variables(self):
        inp = _inp(["A", "B"], 30, lead=5, own={"A": 14}, warehouses=("w", "v"))
        idx = VariableIndex(inp.skus, inp.warehouses, inp.horizon, _lanes_with_fixed_cost(inp))
        per_bucket = 3 * 2 + 2           # order/inv/short x 2 warehouses + 2 lanes
        assert live_var_count(inp, idx) == per_bucket * (14 + 30)
        assert live_var_count(inp, idx) < idx.n_vars

    def test_one_long_lead_time_does_not_push_the_tenant_into_the_fallback(self):
        """40 SKUs x 14 buckets x 1 warehouse fit under 5000; stretching the
        whole matrix to 46 buckets for ONE SKU would not have."""
        skus = [f"S{i}" for i in range(40)]
        lead = {s: 5 for s in skus}
        lead["S0"] = 45
        own = {s: 14 for s in skus[1:]}
        inp = _inp(skus, 46, lead=lead, own=own)
        assert optimize(inp, max_vars_before_fallback=5000).status == "optimal"


class TestTheFallbackHonoursTheHorizons:

    def test_greedy_plan_stops_at_each_skus_horizon(self):
        inp = _inp(["A", "B"], 30, lead={"A": 10, "B": 25}, own={"A": 14})
        r = optimize(inp, max_vars_before_fallback=0)
        assert r.status == "fallback"
        assert _ordered(r, "A") == pytest.approx(40.0)
        assert _ordered(r, "B") == pytest.approx(50.0)
        assert all(r.orders[("A", "w", t)] == 0 for t in range(15, 31))

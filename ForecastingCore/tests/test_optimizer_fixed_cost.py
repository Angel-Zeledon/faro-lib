"""
Per-lane FIXED cost of a transfer: the charge you pay once per shipment, no
matter how many units (or SKUs) ride along. Modeled with a binary per
(lane, bucket) linked to the continuous transfer variables by a big-M row.
"""

import numpy as np
import pytest

from forecasting_core.business.optimizer import (
    OptimizationInput,
    VariableIndex,
    build_problem,
    optimize,
)


def _split_vs_consolidate_input(fixed_cost: float | None) -> OptimizationInput:
    """
    W1 holds exactly the 30 units W2 will consume over 3 buckets (10/bucket);
    W2 starts empty. Holding is charged at the same rate in both warehouses,
    so shipping all 30 up front and shipping 10 per bucket cost exactly the
    same in units-held and in per-unit transfer cost — the ONLY thing that can
    separate the two plans is how many shipments they use.
    """
    fixed = {} if fixed_cost is None else {("W1", "W2"): fixed_cost}
    return OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=3,
        demand={("A", "W1"): [0.0, 0.0, 0.0], ("A", "W2"): [10.0, 10.0, 10.0]},
        stock0={("A", "W1"): 30.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 0.1},
        stockout_cost={"A": 100.0},
        order_cost={"A": 10.0},
        transfer_cost=1.0,
        transfer_cost_by_lane={("W1", "W2"): 0.1},
        transfer_fixed_cost_by_lane=fixed,
    )


class TestFixedCostDrivesThePlan:
    def test_fixed_cost_consolidates_three_shipments_into_one(self):
        """
        Splitting would pay the 50 fixed cost three times; consolidating pays
        it once and costs nothing extra in holding. The chosen plan must be a
        single 30-unit shipment in bucket 1 (it cannot be later — bucket 1
        already needs 10 units and W2 is empty).
        """
        result = optimize(_split_vs_consolidate_input(50.0))
        assert result.status == "optimal"

        moved = {t: result.transfers[("A", "W1", "W2", t)] for t in (1, 2, 3)}
        assert moved == {1: 30.0, 2: 0.0, 3: 0.0}
        assert sum(result.orders.values()) == 0.0
        assert sum(result.shortages.values()) == 0.0
        # 30 units * 0.1 per unit = 3, holding at W2 (20 + 10 + 0) * 0.1 = 3,
        # and the fixed cost charged EXACTLY once = 50.
        assert result.total_cost == pytest.approx(3.0 + 3.0 + 50.0)

    def test_prohibitive_fixed_cost_makes_buying_the_cheaper_plan(self):
        """
        Same network, fixed cost 1000: any shipment at all now costs more than
        buying W2's whole horizon (30 units * 10). The plan must stop
        transferring and start purchasing.
        """
        result = optimize(_split_vs_consolidate_input(1000.0))
        assert result.status == "optimal"

        assert all(qty == 0.0 for qty in result.transfers.values())
        assert sum(result.orders[("A", "W2", t)] for t in (1, 2, 3)) == 30.0
        assert sum(result.shortages.values()) == 0.0
        # 30 units bought at 10 = 300, plus W1's untouched 30 units held for
        # 3 buckets at 0.1 = 9. No fixed cost is charged at all.
        assert result.total_cost == pytest.approx(309.0)

    def test_one_shipment_carries_every_sku_and_is_charged_once(self):
        """
        The binary is per (lane, bucket), shared across SKUs: one truck moving
        two SKUs in the same bucket pays the fixed cost once, not per SKU.
        """
        inp = OptimizationInput(
            skus=["A", "B"],
            warehouses=["W1", "W2"],
            horizon=1,
            demand={("A", "W1"): [0.0], ("A", "W2"): [10.0],
                    ("B", "W1"): [0.0], ("B", "W2"): [10.0]},
            stock0={("A", "W1"): 10.0, ("A", "W2"): 0.0,
                    ("B", "W1"): 10.0, ("B", "W2"): 0.0},
            lead_time_buckets={"A": 0, "B": 0},
            holding_cost={"A": 0.1, "B": 0.1},
            stockout_cost={"A": 100.0, "B": 100.0},
            order_cost={"A": 10.0, "B": 10.0},
            transfer_cost=1.0,
            transfer_cost_by_lane={("W1", "W2"): 0.1},
            transfer_fixed_cost_by_lane={("W1", "W2"): 50.0},
        )
        result = optimize(inp)
        assert result.status == "optimal"
        assert result.transfers[("A", "W1", "W2", 1)] == 10.0
        assert result.transfers[("B", "W1", "W2", 1)] == 10.0
        # 20 units * 0.1 = 2, plus ONE fixed cost of 50 (two would be 102).
        assert result.total_cost == pytest.approx(52.0)


class TestBinaryIsGenuinelyLinked:
    def test_transfer_without_its_indicator_violates_the_linking_row(self):
        """A plan that moves units while its shipment binary is 0 must be
        infeasible — otherwise the fixed cost could be dodged entirely."""
        problem = build_problem(_split_vs_consolidate_input(50.0))
        idx = problem.index

        x = np.zeros(idx.n_vars)
        x[idx.transfer_idx("A", "W1", "W2", 1)] = 5.0  # moving, indicator off
        assert (problem.A_ub @ x > problem.b_ub).any()

        x[idx.ship_idx("W1", "W2", 1)] = 1.0  # indicator on -> feasible again
        assert (problem.A_ub @ x <= problem.b_ub + 1e-9).all()

    def test_indicator_row_covers_every_sku_on_that_lane_and_bucket(self):
        """The linking row sums ALL SKUs moving on that lane in that bucket,
        so no SKU can slip across on a switched-off binary."""
        inp = _split_vs_consolidate_input(50.0)
        inp.skus = ["A", "B"]
        inp.demand[("B", "W1")] = [0.0, 0.0, 0.0]
        inp.demand[("B", "W2")] = [10.0, 10.0, 10.0]
        inp.stock0[("B", "W1")] = 30.0
        inp.stock0[("B", "W2")] = 0.0
        inp.lead_time_buckets["B"] = 0
        inp.holding_cost["B"] = 0.1
        inp.stockout_cost["B"] = 100.0
        inp.order_cost["B"] = 10.0

        problem = build_problem(inp)
        idx = problem.index
        row = _linking_row_for(problem, idx.ship_idx("W1", "W2", 2))
        assert problem.A_ub[row, idx.transfer_idx("A", "W1", "W2", 2)] == 1.0
        assert problem.A_ub[row, idx.transfer_idx("B", "W1", "W2", 2)] == 1.0
        # ...and only that bucket.
        assert problem.A_ub[row, idx.transfer_idx("A", "W1", "W2", 1)] == 0.0

    def test_big_m_comes_from_the_instance_not_a_constant(self):
        """M is stock0 + horizon demand (30 + 30 here); doubling the demand
        must move it, which a hardcoded constant could not do."""
        problem = build_problem(_split_vs_consolidate_input(50.0))
        idx = problem.index
        row = _linking_row_for(problem, idx.ship_idx("W1", "W2", 1))
        assert problem.A_ub[row, idx.ship_idx("W1", "W2", 1)] == -60.0

        inp = _split_vs_consolidate_input(50.0)
        inp.demand[("A", "W2")] = [20.0, 20.0, 20.0]
        bigger = build_problem(inp)
        bigger_row = _linking_row_for(bigger, bigger.index.ship_idx("W1", "W2", 1))
        assert bigger.A_ub[bigger_row, bigger.index.ship_idx("W1", "W2", 1)] == -90.0


class TestZeroFixedCostChangesNothing:
    def test_problem_is_bit_for_bit_identical_to_the_pre_feature_build(self):
        """Regression guard: a tenant with no fixed cost configured (absent OR
        explicitly 0) must get exactly the old problem — no binaries, no
        inequality rows, same objective and same constraint matrix."""
        absent = build_problem(_split_vs_consolidate_input(None))
        explicit_zero = build_problem(_split_vs_consolidate_input(0.0))

        # 1 SKU * 2 warehouses * 3 buckets * (order+inv+short) = 18,
        # plus 1 SKU * 2 ordered pairs * 3 buckets of transfer = 6.
        assert absent.index.n_vars == 24
        assert explicit_zero.index.n_vars == 24
        assert absent.index.fixed_cost_lanes() == []
        assert explicit_zero.index.fixed_cost_lanes() == []
        assert absent.A_ub is None and absent.b_ub is None
        assert explicit_zero.A_ub is None and explicit_zero.b_ub is None

        np.testing.assert_array_equal(absent.c, explicit_zero.c)
        np.testing.assert_array_equal(absent.A_eq, explicit_zero.A_eq)
        np.testing.assert_array_equal(absent.b_eq, explicit_zero.b_eq)
        np.testing.assert_array_equal(absent.integrality, explicit_zero.integrality)
        np.testing.assert_array_equal(absent.bounds.ub, explicit_zero.bounds.ub)

    def test_solved_plan_is_identical_with_and_without_the_zero_lane_entry(self):
        absent = optimize(_split_vs_consolidate_input(None))
        explicit_zero = optimize(_split_vs_consolidate_input(0.0))

        assert absent.orders == explicit_zero.orders
        assert absent.transfers == explicit_zero.transfers
        assert absent.inventory == explicit_zero.inventory
        assert absent.shortages == explicit_zero.shortages
        assert absent.total_cost == pytest.approx(explicit_zero.total_cost)

    def test_only_lanes_that_charge_a_fixed_cost_get_a_binary(self):
        inp = _split_vs_consolidate_input(50.0)
        inp.transfer_fixed_cost_by_lane[("W2", "W1")] = 0.0
        idx = build_problem(inp).index

        assert idx.fixed_cost_lanes() == [("W1", "W2")]
        with pytest.raises(ValueError):
            idx.ship_idx("W2", "W1", 1)

    def test_unknown_lane_key_never_mints_a_variable(self):
        """A fixed cost keyed on a warehouse pair that isn't in this problem
        must be ignored rather than allocating an unreachable binary."""
        idx = VariableIndex(
            skus=["A"], warehouses=["W1", "W2"], horizon=2,
            fixed_cost_lanes=[("W1", "W9"), ("W1", "W1")],
        )
        assert idx.fixed_cost_lanes() == []
        assert idx.n_vars == VariableIndex(["A"], ["W1", "W2"], 2).n_vars


def _linking_row_for(problem, ship_var_idx: int) -> int:
    """The shipment-linking row for a (lane, bucket) is the only A_ub row with
    a negative (big-M) coefficient on that binary's column."""
    rows = np.where(problem.A_ub[:, ship_var_idx] < 0)[0]
    assert len(rows) == 1, f"expected exactly one linking row for ship col {ship_var_idx}"
    return rows[0]

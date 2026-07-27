"""
Per-lane FIXED cost of a transfer: the charge you pay once per shipment, no
matter how many units (or SKUs) ride along. Modeled with a binary per
(lane, bucket) linked to the transfer variables by one big-M row per SKU.
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

    def test_every_sku_on_that_lane_and_bucket_gets_a_row_on_the_same_binary(self):
        """Each SKU is linked to the (lane, bucket) binary by its own row — all
        pointing at the SAME binary — so no SKU can slip across on a
        switched-off binary and the truck is still charged once."""
        problem = build_problem(_two_sku_consolidate_input(50.0))
        idx = problem.index

        rows = _linking_rows_for(problem, idx.ship_idx("W1", "W2", 2))
        assert len(rows) == 2
        moved = {
            int(np.flatnonzero(problem.A_ub[r] > 0)[0]) for r in rows
        }
        assert moved == {
            idx.transfer_idx("A", "W1", "W2", 2),
            idx.transfer_idx("B", "W1", "W2", 2),
        }
        # ...and only that bucket.
        for r in rows:
            assert problem.A_ub[r, idx.transfer_idx("A", "W1", "W2", 1)] == 0.0

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

    def test_each_rows_big_m_is_scaled_to_its_own_sku(self):
        """A row's M must bound the units THAT SKU can carry, not the whole
        catalogue's: sharing one catalogue-wide M is what let a big SKU shrink
        every other SKU's indicator below the solver's integrality tolerance."""
        inp = _two_sku_consolidate_input(50.0)
        inp.stock0[("B", "W1")] = 30_000.0     # B is now a huge SKU
        problem = build_problem(inp)
        idx = problem.index

        by_sku = {}
        for r in _linking_rows_for(problem, idx.ship_idx("W1", "W2", 1)):
            sku = "A" if problem.A_ub[r, idx.transfer_idx("A", "W1", "W2", 1)] else "B"
            by_sku[sku] = problem.A_ub[r, idx.ship_idx("W1", "W2", 1)]

        assert by_sku["A"] == -60.0        # A: 30 stock + 30 demand
        assert by_sku["B"] == -30_030.0    # B: 30000 stock + 30 demand — B's size
                                           # must not leak into A's row.


class TestBigMDoesNotLeakAcrossSkus:
    """Regression: found by running the solver on realistic magnitudes rather
    than toy ones. With a single catalogue-wide M, one large SKU pushed the
    ship indicator a shipment needs (units / M) below HiGHS's ~1e-6 integrality
    tolerance, so the binary read as 0 and the lane's fixed cost was charged as
    ~0 — every other SKU shipped for free."""

    @staticmethod
    def _small_sku_beside_a_giant(big_stock: float) -> OptimizationInput:
        """SMALL wants 2 units moved A->B over the horizon; the lane charges
        1000 per shipment and no order can arrive in time (lead 5 > horizon 2).
        Shipping costs 1000, leaving the 2 units unmet costs 2 * 100 = 200, so
        the honest optimum NEVER ships. BIG only exists to inflate the bound.
        """
        return OptimizationInput(
            skus=["SMALL", "BIG"],
            warehouses=["A", "B"],
            horizon=2,
            demand={("SMALL", "A"): [0.0, 0.0], ("SMALL", "B"): [1.0, 1.0],
                    ("BIG", "A"): [0.0, 0.0], ("BIG", "B"): [0.0, 0.0]},
            stock0={("SMALL", "A"): 10.0, ("SMALL", "B"): 0.0,
                    ("BIG", "A"): big_stock, ("BIG", "B"): 0.0},
            lead_time_buckets={"SMALL": 5, "BIG": 5},
            holding_cost={"SMALL": 0.0, "BIG": 0.0},
            stockout_cost={"SMALL": 100.0, "BIG": 0.0},
            order_cost={"SMALL": 1.0, "BIG": 1.0},
            transfer_cost=0.0,
            transfer_cost_by_lane={("A", "B"): 0.0, ("B", "A"): 0.0},
            transfer_lead_buckets={("A", "B"): 0, ("B", "A"): 0},
            transfer_fixed_cost_by_lane={("A", "B"): 1000.0, ("B", "A"): 1000.0},
        )

    @pytest.mark.parametrize("big_stock", [0.0, 1e6, 5e7, 1e9])
    def test_a_giant_sku_never_makes_another_skus_shipment_free(self, big_stock):
        result = optimize(self._small_sku_beside_a_giant(big_stock))

        assert result.status == "optimal"
        assert all(qty == 0.0 for qty in result.transfers.values()), (
            "shipped despite a fixed cost that costs more than the shortage — "
            "the indicator was satisfied below the solver's integrality tolerance"
        )
        # 2 units of unmet demand at 100 each; no fixed cost, no orders.
        assert result.total_cost == pytest.approx(200.0)

    def test_the_bound_ignores_other_skus_units(self):
        from forecasting_core.business.optimizer import _shipment_big_m

        inp = self._small_sku_beside_a_giant(5e7)
        assert _shipment_big_m(inp, "SMALL") == 12.0   # 10 on hand + 2 demanded
        assert _shipment_big_m(inp, "BIG") == 5e7


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


def _two_sku_consolidate_input(fixed_cost: float) -> OptimizationInput:
    """_split_vs_consolidate_input with a second, identical SKU riding along."""
    inp = _split_vs_consolidate_input(fixed_cost)
    inp.skus = ["A", "B"]
    inp.demand[("B", "W1")] = [0.0, 0.0, 0.0]
    inp.demand[("B", "W2")] = [10.0, 10.0, 10.0]
    inp.stock0[("B", "W1")] = 30.0
    inp.stock0[("B", "W2")] = 0.0
    inp.lead_time_buckets["B"] = 0
    inp.holding_cost["B"] = 0.1
    inp.stockout_cost["B"] = 100.0
    inp.order_cost["B"] = 10.0
    return inp


def _linking_rows_for(problem, ship_var_idx: int) -> list[int]:
    """The shipment-linking rows for a (lane, bucket) are the A_ub rows with a
    negative (big-M) coefficient on that binary's column — one per SKU."""
    rows = np.flatnonzero(problem.A_ub[:, ship_var_idx] < 0).tolist()
    assert rows, f"expected linking rows for ship col {ship_var_idx}"
    return rows


def _linking_row_for(problem, ship_var_idx: int) -> int:
    """The single linking row of a one-SKU problem."""
    rows = _linking_rows_for(problem, ship_var_idx)
    assert len(rows) == 1, f"expected exactly one linking row for ship col {ship_var_idx}"
    return rows[0]

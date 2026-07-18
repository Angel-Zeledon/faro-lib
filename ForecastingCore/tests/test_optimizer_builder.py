import numpy as np
from forecasting_core.business.optimizer import OptimizationInput, VariableIndex, build_problem


def _tiny_input() -> OptimizationInput:
    """1 SKU, 2 warehouses, 2 buckets, lead time 0 (orders can arrive from t=1)."""
    return OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=2,
        demand={("A", "W1"): [5.0, 5.0], ("A", "W2"): [3.0, 3.0]},
        stock0={("A", "W1"): 10.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )


class TestBuildProblem:
    def test_objective_vector_matches_costs_at_each_index(self):
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        assert problem.c[idx.order_idx("A", "W1", 1)] == inp.order_cost["A"]
        assert problem.c[idx.inv_idx("A", "W1", 1)] == inp.holding_cost["A"]
        assert problem.c[idx.short_idx("A", "W1", 1)] == inp.stockout_cost["A"]
        assert problem.c[idx.transfer_idx("A", "W1", "W2", 1)] == inp.transfer_cost

    def test_balance_row_t1_matches_hand_computed_equation(self):
        """
        Row for (sku=A, warehouse=W1, t=1):
          inv[A,W1,1] - order[A,W1,1] - transfer[A,W2,W1,1] + transfer[A,W1,W2,1]
            - short[A,W1,1] = stock0[A,W1] - demand[A,W1,1] = 10 - 5 = 5
        """
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        row = _find_row_for(problem, idx.inv_idx("A", "W1", 1))
        expected = np.zeros(idx.n_vars)
        expected[idx.inv_idx("A", "W1", 1)] = 1.0
        expected[idx.order_idx("A", "W1", 1)] = -1.0
        expected[idx.transfer_idx("A", "W2", "W1", 1)] = -1.0  # inbound to W1
        expected[idx.transfer_idx("A", "W1", "W2", 1)] = 1.0   # outbound from W1
        expected[idx.short_idx("A", "W1", 1)] = -1.0

        np.testing.assert_array_equal(problem.A_eq[row], expected)
        assert problem.b_eq[row] == 5.0  # stock0(10) - demand(5)

    def test_balance_row_t2_references_prior_bucket_inventory(self):
        """
        Row for (sku=A, warehouse=W2, t=2):
          inv[A,W2,2] - inv[A,W2,1] - order[A,W2,2]
            - transfer[A,W1,W2,2] + transfer[A,W2,W1,2] - short[A,W2,2]
            = -demand[A,W2,2] = -3
        """
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        row = _find_row_for(problem, idx.inv_idx("A", "W2", 2))
        expected = np.zeros(idx.n_vars)
        expected[idx.inv_idx("A", "W2", 2)] = 1.0
        expected[idx.inv_idx("A", "W2", 1)] = -1.0
        expected[idx.order_idx("A", "W2", 2)] = -1.0
        expected[idx.transfer_idx("A", "W1", "W2", 2)] = -1.0
        expected[idx.transfer_idx("A", "W2", "W1", 2)] = 1.0
        expected[idx.short_idx("A", "W2", 2)] = -1.0

        np.testing.assert_array_equal(problem.A_eq[row], expected)
        assert problem.b_eq[row] == -3.0

    def test_lead_time_fixes_early_orders_to_zero_bound(self):
        inp = _tiny_input()
        inp.lead_time_buckets = {"A": 1}  # order can't arrive before t=2
        problem = build_problem(inp)
        idx = problem.index
        o1 = idx.order_idx("A", "W1", 1)
        o2 = idx.order_idx("A", "W1", 2)
        assert problem.bounds.lb[o1] == 0 and problem.bounds.ub[o1] == 0
        assert problem.bounds.ub[o2] > 0  # not fixed to zero

    def test_integrality_marks_order_and_transfer_as_integer_only(self):
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index
        assert problem.integrality[idx.order_idx("A", "W1", 1)] == 1
        assert problem.integrality[idx.transfer_idx("A", "W1", "W2", 1)] == 1
        assert problem.integrality[idx.inv_idx("A", "W1", 1)] == 0
        assert problem.integrality[idx.short_idx("A", "W1", 1)] == 0


def _find_row_for(problem, inv_var_idx: int) -> int:
    """The balance-equation row for a given (sku,warehouse,t) is the one
    whose A_eq row has a +1.0 exactly at that inv[...] column (each inv
    variable appears with coefficient 1.0 in EXACTLY one row: its own
    balance equation)."""
    rows = np.where(problem.A_eq[:, inv_var_idx] == 1.0)[0]
    assert len(rows) == 1, f"expected exactly one balance row for inv col {inv_var_idx}, found {len(rows)}"
    return rows[0]

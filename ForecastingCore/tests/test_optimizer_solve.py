from forecasting_core.business.optimizer import OptimizationInput, optimize


def test_transfer_chosen_over_purchase_when_surplus_exists_elsewhere():
    """
    W1 has a large surplus and no demand; W2 has a deficit. Transfer is much
    cheaper than ordering+holding. The optimizer must move stock from W1 to
    W2 rather than placing a new purchase order.
    """
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=1,
        demand={("A", "W1"): [0.0], ("A", "W2"): [20.0]},
        stock0={("A", "W1"): 100.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 100.0},
        order_cost={"A": 10.0},
        transfer_cost=0.1,
    )
    result = optimize(inp)
    assert result.status == "optimal"
    assert result.transfers[("A", "W1", "W2", 1)] >= 20.0
    assert result.orders[("A", "W2", 1)] == 0.0
    assert result.shortages[("A", "W2", 1)] == 0.0


def test_purchase_chosen_when_no_surplus_anywhere():
    """Both warehouses are short — nothing to transfer from, must purchase."""
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=1,
        demand={("A", "W1"): [10.0], ("A", "W2"): [10.0]},
        stock0={("A", "W1"): 0.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 100.0},
        order_cost={"A": 5.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    assert result.status == "optimal"
    assert result.orders[("A", "W1", 1)] >= 10.0
    assert result.orders[("A", "W2", 1)] >= 10.0
    total_transferred = sum(v for k, v in result.transfers.items() if k[3] == 1)
    assert total_transferred == 0.0


def test_balance_conservation_holds_for_solved_result():
    """
    Structural invariant, independent of solver optimality: for every
    (sku, warehouse, t), the returned orders/transfers/inventory/shortages
    must satisfy the balance equation exactly.
    """
    inp = OptimizationInput(
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
    result = optimize(inp)
    for w in inp.warehouses:
        prev_inv = inp.stock0[("A", w)]
        for t in [1, 2]:
            inbound = sum(
                result.transfers.get(("A", a, w, t), 0.0)
                for a in inp.warehouses if a != w
            )
            outbound = sum(
                result.transfers.get(("A", w, b, t), 0.0)
                for b in inp.warehouses if b != w
            )
            expected_inv = (
                prev_inv + result.orders[("A", w, t)] + inbound - outbound
                - inp.demand[("A", w)][t - 1] + result.shortages[("A", w, t)]
            )
            assert abs(result.inventory[("A", w, t)] - expected_inv) < 1e-6
            prev_inv = result.inventory[("A", w, t)]


def test_fallback_used_when_solver_fails(monkeypatch):
    import forecasting_core.business.optimizer as opt_mod

    def _broken_milp(*args, **kwargs):
        class FakeResult:
            success = False
        return FakeResult()

    monkeypatch.setattr(opt_mod, "milp", _broken_milp)

    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1"],
        horizon=1,
        demand={("A", "W1"): [10.0]},
        stock0={("A", "W1"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    assert result.status == "fallback"
    assert result.orders[("A", "W1", 1)] > 0  # still a usable recommendation


def test_fallback_degrades_gracefully_on_malformed_input():
    """
    A missing demand entry makes build_problem's `inp.demand[(i, w)][t - 1]`
    raise KeyError. optimize() must still return a usable fallback result
    instead of propagating the exception.
    """
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=1,
        demand={("A", "W1"): [10.0]},  # ("A", "W2") missing on purpose
        stock0={("A", "W1"): 0.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    assert result.status == "fallback"
    assert result.orders[("A", "W1", 1)] == 10.0
    assert result.orders[("A", "W2", 1)] == 0.0  # missing demand defaults to 0


def test_fallback_keeps_on_hand_stock_available_during_lead_time():
    """
    stock0=100 comfortably covers demand=[10, 10] even though lead_time=1
    means no NEW order can arrive at t=1. The fallback must serve demand
    from on-hand stock during the lead-time window rather than discarding
    it, so no order should be placed at all.
    """
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1"],
        horizon=2,
        demand={("A", "W1"): [10.0, 10.0]},
        stock0={("A", "W1"): 100.0},
        lead_time_buckets={"A": 1},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )
    import forecasting_core.business.optimizer as opt_mod
    idx = opt_mod.VariableIndex(inp.skus, inp.warehouses, inp.horizon)
    result = opt_mod._fallback_recommend(inp, idx)

    assert result.orders[("A", "W1", 1)] == 0.0
    assert result.orders[("A", "W1", 2)] == 0.0
    assert result.shortages[("A", "W1", 1)] == 0.0
    assert result.shortages[("A", "W1", 2)] == 0.0
    assert result.inventory[("A", "W1", 1)] == 90.0
    assert result.inventory[("A", "W1", 2)] == 80.0

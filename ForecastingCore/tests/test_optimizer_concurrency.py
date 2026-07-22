"""
Concurrency safety of the MILP core.

The optimizer is built on scipy.optimize.milp (HiGHS), a pure function: every
optimize() call constructs its own VariableIndex, MilpProblem and solver run,
holding no module-level solver or model state. These tests pin that property
down so a future refactor that (re)introduces shared mutable state — a cached
problem, a reused solver handle — fails loudly instead of surfacing as a rare,
load-only 500 under concurrent requests.

Kept deliberately small (few threads, tiny horizon): the goal is to prove the
absence of shared state, not to benchmark the solver, and many concurrent
HiGHS solves oversubscribe CPU cores for no extra coverage.
"""

import threading

from forecasting_core.business.optimizer import OptimizationInput, optimize


def _make_input(skus, warehouses, horizon=6):
    demand, stock0, lead, holding, stockout, order = {}, {}, {}, {}, {}, {}
    for s in skus:
        lead[s] = 2
        holding[s] = 0.01
        stockout[s] = 30.0
        order[s] = 10.0
        for w in warehouses:
            demand[(s, w)] = [5.0] * horizon
            stock0[(s, w)] = 2.0
    return OptimizationInput(
        skus=skus, warehouses=warehouses, horizon=horizon,
        demand=demand, stock0=stock0, lead_time_buckets=lead,
        holding_cost=holding, stockout_cost=stockout, order_cost=order,
        transfer_cost=0.5,
    )


def test_concurrent_solves_of_same_input_are_deterministic_and_error_free():
    """
    Threads solving the SAME input concurrently must all succeed and return the
    identical objective — any shared mutable solver/model state would manifest
    as an exception or a diverging total_cost here.
    """
    inp = _make_input(["A", "B"], ["north", "south"])
    baseline = optimize(inp)
    assert baseline.status == "optimal"

    objectives: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def work():
        try:
            cost = round(optimize(inp).total_cost, 4)
            with lock:
                objectives.append(cost)
        except Exception as exc:  # noqa: BLE001 - record, don't swallow silently
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent solves raised: {errors}"
    assert len(objectives) == 8
    assert set(objectives) == {round(baseline.total_cost, 4)}


def test_concurrent_solves_of_independent_inputs_do_not_cross_contaminate():
    """
    Threads solving DIFFERENT inputs concurrently must each get the answer for
    their own input — proving no state leaks from one solve into another. Each
    per-thread result is compared against the same input solved in isolation.
    """
    inputs = {k: _make_input([f"S{k}"], ["north", "south"]) for k in range(8)}
    expected = {k: round(optimize(inp).total_cost, 4) for k, inp in inputs.items()}

    got: dict[int, float] = {}
    errors: list[str] = []
    lock = threading.Lock()

    def work(k):
        try:
            cost = round(optimize(inputs[k]).total_cost, 4)
            with lock:
                got[k] = cost
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(k,)) for k in inputs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent solves raised: {errors}"
    assert got == expected

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
import time

import pytest

from forecasting_core.business import optimizer as optimizer_mod
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


# ── The deadlock these two tests used to trigger ──────────────────────────────
#
# Both tests above WEDGED the process before 2026-08-06 — reliably enough that
# the whole engine suite never finished (measured: each test alone passed in
# ~1.5s, the two together were killed at 3m20). A hang is a terrible guard: it
# never turns red, it just stops. So the tests below pin the INVARIANT that
# fixes it — always the same solver thread — which fails loudly and instantly
# if anyone drops the executor and calls milp() inline again.


def test_every_solve_runs_on_one_dedicated_thread_never_the_caller(monkeypatch):
    """The property that closed the deadlock.

    HiGHS does not survive being entered from different OS threads over a
    process's life; a mutex serialises the calls but cannot say "always the same
    thread", which is why serialising alone did not fix it. Callers must
    therefore never run milp() themselves, no matter which thread they are on.
    """
    solver_threads: set[str] = set()
    caller_threads: set[str] = set()
    real_milp = optimizer_mod.milp
    lock = threading.Lock()

    def spy(*args, **kwargs):
        with lock:
            solver_threads.add(threading.current_thread().name)
        return real_milp(*args, **kwargs)

    monkeypatch.setattr(optimizer_mod, "milp", spy)

    inp = _make_input(["A", "B"], ["north", "south"])
    optimize(inp)                                    # from the main thread
    caller_threads.add(threading.current_thread().name)

    def work():
        with lock:
            caller_threads.add(threading.current_thread().name)
        optimize(inp)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "a solve wedged its caller"

    assert len(solver_threads) == 1, (
        f"milp() ran on {len(solver_threads)} different threads {solver_threads} — "
        "the single-thread guarantee is gone and the deadlock is reachable again")
    assert len(caller_threads) == 7, "the callers were not actually on 7 threads"
    assert solver_threads.isdisjoint(caller_threads), (
        f"milp() ran on a CALLER thread {solver_threads & caller_threads}")


def test_a_solver_that_stops_answering_degrades_instead_of_holding_the_caller():
    """The promise `time_limit` alone could not keep.

    The solver time limit lives inside HiGHS, so it does nothing when HiGHS
    itself stops answering — which is exactly the failure that made the backend
    look like it had disappeared. The outer wait is what bounds it: the caller
    gives up and returns the greedy fallback, so the product answers worse
    rather than not at all.
    """
    release = threading.Event()

    def hanging_milp(*args, **kwargs):
        release.wait(timeout=30)                      # released in the finally
        raise AssertionError("should not be awaited by the caller")

    original = optimizer_mod.milp
    optimizer_mod.milp = hanging_milp
    try:
        inp = _make_input(["A"], ["north"])
        t0 = time.time()
        result = optimize(inp, time_limit_s=0.1)
        elapsed = time.time() - t0

        assert result.status == "fallback", (
            "a solver that never answers must degrade, not succeed")
        assert elapsed < optimizer_mod._SOLVE_WAIT_GRACE_S + 3, (
            f"the caller waited {elapsed:.1f}s on a solver that never answered")
    finally:
        optimizer_mod.milp = original
        release.set()
        # Let the wedged worker drain so it cannot slow a later test down.
        time.sleep(0.2)


@pytest.mark.parametrize("round_", range(3))
def test_repeated_batches_of_concurrent_solves_do_not_wedge(round_):
    """`8 threads x 2 rounds` was a reliable wedge; 3 batches is cheap insurance.

    Parametrised so a wedge names the batch it died on instead of failing an
    opaque loop.
    """
    inp = _make_input(["A", "B"], ["north", "south"])
    errors: list[str] = []
    lock = threading.Lock()

    def work():
        try:
            optimize(inp)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in threads), "solves wedged"
    assert errors == [], f"concurrent solves raised: {errors}"

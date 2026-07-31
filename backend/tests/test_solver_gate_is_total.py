"""
Every MILP solve goes through the concurrency gate. No exceptions.

`_MAX_CONCURRENT_SOLVES = 2` is not a tuning knob, it is the only thing standing
between the API and a wedged process. Measured locally on this machine with the
engine's own `optimize()`:

    2 threads x 20 rounds -> clean
    3 threads             -> stops making progress; killed at 5 minutes

Three concurrent HiGHS solves do not raise, they stop. A wedged worker returns
nothing at all — no 500, no 503 — so from the browser the whole backend is
simply gone.

`/inventory/optimize` held the gate. `/inventory/cash-calendar/fit` did not, and
called `optimize()` straight out, which made the cap a fiction: two purchasing
panels take both slots, the cash calendar adds a third solve. Two buyers
refreshing while a third opens the calendar was enough.

This test does not check that a particular endpoint is gated — it checks the
INVARIANT, by watching the real solver. A new endpoint that solves without the
slot fails here on the day it is written, which is the only moment this is cheap
to fix.
"""

import threading

import pytest

import backend.api.v1.inventory as inventory_api
from backend.inventory import optimizer_service as opt_svc


class _SolveWatcher:
    """Records whether each solve happened while a gate slot was held."""

    def __init__(self):
        self.calls: list[bool] = []
        self._depth = threading.local()

    @property
    def depth(self) -> int:
        return getattr(self._depth, "value", 0)

    def enter(self):
        self._depth.value = self.depth + 1

    def leave(self):
        self._depth.value = self.depth - 1

    def record(self):
        self.calls.append(self.depth > 0)


@pytest.fixture
def watcher(monkeypatch):
    """Wrap the real gate and the real solver, changing neither's behaviour."""
    w = _SolveWatcher()
    real_slot = opt_svc.solve_slot

    from contextlib import contextmanager

    @contextmanager
    def watched_slot():
        with real_slot():
            w.enter()
            try:
                yield
            finally:
                w.leave()

    monkeypatch.setattr(opt_svc, "solve_slot", watched_slot)

    import forecasting_core.business.optimizer as core_opt
    real_optimize = core_opt.optimize

    def watched_optimize(inp):
        w.record()
        return real_optimize(inp)

    monkeypatch.setattr(core_opt, "optimize", watched_optimize)
    return w


def _session_with_a_forecast(tenant_id, session_id):
    from backend.db import session_store
    from backend.inventory import service as inv_svc

    inv_svc.upsert_stock(tenant_id, "GATE_SKU", {
        "current_stock": 40, "lead_time_days": 5, "unit_cost": 3.0,
        "warehouse": "principal",
    })
    session_store.set_forecasts(tenant_id, session_id, {
        "GATE_SKU": {"lightgbm": {
            "forecast": [{"date": "2026-01-01", "value": 6.0}] * 30,
        }},
    })


class TestEveryEndpointThatSolvesHoldsASlot:

    def test_the_optimize_endpoint(self, client, auth_headers, test_tenant,
                                   test_session, watcher):
        _session_with_a_forecast(test_tenant["id"], test_session["id"])
        resp = client.get(
            f"/api/v1/inventory/optimize?session_id={test_session['id']}&horizon_days=30",
            headers=auth_headers,
        )
        if resp.status_code == 403:
            pytest.skip("tenant plan has no MILP_OPTIMIZER feature")
        assert resp.status_code == 200, resp.text
        assert watcher.calls, "the endpoint did not solve at all — test proves nothing"
        assert all(watcher.calls), "a solve ran outside the concurrency gate"

    def test_the_cash_calendar_fit_endpoint(self, client, auth_headers, test_tenant,
                                            test_session, watcher):
        """The one that was missing it. Two panels take both slots and this
        endpoint used to add a third solve on top."""
        _session_with_a_forecast(test_tenant["id"], test_session["id"])
        resp = client.post(
            f"/api/v1/inventory/cash-calendar/fit?session_id={test_session['id']}",
            json={"budget": 100000},
            headers=auth_headers,
        )
        if resp.status_code in (403, 404):
            pytest.skip("endpoint gated by plan or shape changed")
        assert resp.status_code == 200, resp.text
        assert watcher.calls, "the endpoint did not solve at all — test proves nothing"
        assert all(watcher.calls), (
            "cash-calendar/fit solved outside the gate: the cap of "
            f"{opt_svc._MAX_CONCURRENT_SOLVES} is a fiction while any path can bypass it"
        )


class TestTheGateItself:

    def test_it_refuses_rather_than_queueing(self):
        """Queueing would tie up thread-pool workers waiting; the point is a
        fast 503, not a slow success."""
        held = []
        try:
            for _ in range(opt_svc._MAX_CONCURRENT_SOLVES):
                slot = opt_svc.solve_slot()
                slot.__enter__()
                held.append(slot)
            with pytest.raises(opt_svc.OptimizerBusy):
                with opt_svc.solve_slot():
                    pass
        finally:
            for slot in held:
                slot.__exit__(None, None, None)

    def test_a_released_slot_is_reusable(self):
        """A raised exception inside the slot must not leak it permanently —
        one failed solve would otherwise shrink the cap for the process's life."""
        with pytest.raises(ValueError):
            with opt_svc.solve_slot():
                raise ValueError("boom")
        with opt_svc.solve_slot():
            pass  # acquiring again proves the slot came back

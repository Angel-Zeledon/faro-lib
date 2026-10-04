"""
The row says when its cushion cannot keep the configured service level.

docs/stability.md 17(b): on intermittent demand the lead-time cushion was
measured delivering ~50% against a nominal 95%, and four modelling attempts to
fix it were refuted. The owner's direction (2026-09-30) is to degrade out loud:
the SKU row carries `service_level_caveat = "intermittent_demand"` so the
screen can say so, instead of printing a percentage the product does not keep.

The classification comes from the engine's routing plan
(`result["routing"][sku]["flags"]`), which every training result stores.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.inventory.service import _service_level_caveats
from backend.sessions.service import create_session


class TestCaveatFromRoutingFlags:

    def test_intermittent_flag_yields_the_caveat(self):
        result = {"routing": {"A": {"models": ["croston"], "flags": ["intermittent"]}}}
        assert _service_level_caveats(result) == {"A": "intermittent_demand"}

    def test_intermittent_among_several_flags_still_yields_it(self):
        result = {"routing": {"A": {"flags": ["seasonal", "intermittent", "volatile"]}}}
        assert _service_level_caveats(result) == {"A": "intermittent_demand"}

    def test_dense_classes_get_no_caveat(self):
        result = {"routing": {
            "A": {"flags": ["stable"]},
            "B": {"flags": ["seasonal", "volatile"]},
        }}
        assert _service_level_caveats(result) == {}

    def test_a_result_without_routing_invents_nothing(self):
        assert _service_level_caveats({}) == {}
        assert _service_level_caveats({"routing": None}) == {}
        assert _service_level_caveats({"routing": {"A": "garbage"}}) == {}


def _sku() -> str:
    return f"SKU-{uuid4().hex[:8].upper()}"


def _flat_forecast(value: float, n_points: int = 40) -> dict:
    start = date.today()
    return {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": value}
        for i in range(n_points)
    ]}}


def _session_with(test_tenant, flags_by_sku: dict) -> str:
    session_id = create_session(
        test_tenant["id"], "usr_test", f"caveat-{uuid4().hex[:6]}",
    )["id"]
    session_store.set_forecasts(test_tenant["id"], session_id, {
        sku: _flat_forecast(2.0) for sku in flags_by_sku
    })
    session_store.set_training_result(test_tenant["id"], session_id, {
        "routing": {sku: {"models": ["lightgbm"], "flags": flags}
                    for sku, flags in flags_by_sku.items()},
    })
    return session_id


class TestCaveatReachesEveryRow:

    def test_aggregate_rows_carry_the_caveat_only_on_intermittent_skus(self, test_tenant):
        slow, steady = _sku(), _sku()
        for sku in (slow, steady):
            inv_svc.upsert_stock(test_tenant["id"], sku, {
                "current_stock": 5.0, "lead_time_days": 15, "moq": 1.0,
            })
        session_id = _session_with(test_tenant, {
            slow: ["intermittent"], steady: ["stable"],
        })

        rows = {r["sku"]: r for r in
                inv_svc.get_inventory_status(test_tenant["id"], session_id)}
        assert rows[slow]["service_level_caveat"] == "intermittent_demand"
        assert rows[steady]["service_level_caveat"] is None

    def test_warehouse_rows_carry_the_same_caveat(self, test_tenant):
        slow = _sku()
        inv_svc.upsert_stock(test_tenant["id"], slow, {
            "current_stock": 5.0, "lead_time_days": 15, "moq": 1.0,
        })
        session_id = _session_with(test_tenant, {slow: ["intermittent"]})

        rows = [r for r in
                inv_svc.get_inventory_status_by_warehouse(test_tenant["id"], session_id)
                if r["sku"] == slow]
        assert rows, "the SKU must appear in the per-warehouse view"
        assert all(r["service_level_caveat"] == "intermittent_demand" for r in rows)

    def test_the_caveat_does_not_change_the_numbers(self, test_tenant):
        """Saying so is the whole change: the recommendation for the same SKU
        is identical with and without the flag."""
        a, b = _sku(), _sku()
        for sku in (a, b):
            inv_svc.upsert_stock(test_tenant["id"], sku, {
                "current_stock": 5.0, "lead_time_days": 15, "moq": 1.0,
            })
        session_id = _session_with(test_tenant, {a: ["intermittent"], b: ["stable"]})

        rows = {r["sku"]: r for r in
                inv_svc.get_inventory_status(test_tenant["id"], session_id)}
        assert rows[a]["recommended_qty"] == pytest.approx(rows[b]["recommended_qty"])
        assert rows[a]["reorder_point"] == pytest.approx(rows[b]["reorder_point"])

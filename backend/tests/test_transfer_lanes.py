"""
Transfer lanes: time + money for inter-warehouse moves (PENDIENTES #2).

Covers the schema, the lane endpoints (permission pairs + DB state), the ETA
stamped on a transfer at send time, and the transfer-vs-buy decision itself.
"""

import pytest

from backend.db import session_store
from backend.db.connection import query, query_one
from backend.inventory import service as inv_svc
from backend.inventory import transfer_lane_service as lane_svc
from backend.inventory import transfer_service as tr_svc
from backend.inventory import warehouse_service as wh_svc
from backend.inventory.series import SERIES_SEPARATOR


def _columns(table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    return {r["column_name"] for r in rows}


def _lane_row(tid, from_wh, to_wh):
    return query_one(
        """SELECT * FROM transfer_lanes
           WHERE tenant_id = %s AND from_warehouse = %s AND to_warehouse = %s""",
        (tid, from_wh, to_wh),
    )


@pytest.fixture()
def two_warehouses(client, test_tenant):
    tid = test_tenant["id"]
    wh_svc.create_warehouse(tid, "principal", is_default=True)
    wh_svc.create_warehouse(tid, "Norte")
    inv_svc.upsert_stock(tid, "A", {"current_stock": 100, "warehouse": "principal"})
    inv_svc.upsert_stock(tid, "A", {"current_stock": 10, "warehouse": "Norte"})
    return tid


@pytest.fixture()
def user_id(registered_user):
    return registered_user["user"]["id"]


class TestLaneSchema:
    def test_transfer_lanes_table_exists(self, client):
        assert {"id", "tenant_id", "from_warehouse", "to_warehouse",
                "lead_time_days", "cost_per_unit", "fixed_cost",
                "created_at"} <= _columns("transfer_lanes")

    def test_transfer_log_carries_lead_time_and_eta(self, client):
        assert {"lead_time_days", "expected_arrival"} <= _columns("inventory_transfer_log")


class TestLaneService:
    def test_missing_lane_falls_back_to_documented_default(self, two_warehouses):
        lane = lane_svc.resolve_lane(two_warehouses, "principal", "Norte")
        assert lane["lead_time_days"] == lane_svc.DEFAULT_LANE_LEAD_TIME_DAYS == 1
        assert lane["cost_per_unit"] == 0.0
        assert lane["fixed_cost"] == 0.0
        assert lane["is_default"] is True

    def test_upsert_updates_in_place(self, two_warehouses):
        tid = two_warehouses
        lane_svc.upsert_lane(tid, "principal", "Norte", 3, 0.5, 20.0)
        lane_svc.upsert_lane(tid, "principal", "Norte", 7, 1.5, 30.0)
        rows = query(
            """SELECT lead_time_days, cost_per_unit, fixed_cost FROM transfer_lanes
               WHERE tenant_id = %s AND from_warehouse = 'principal'
                 AND to_warehouse = 'Norte'""",
            (tid,))
        assert len(rows) == 1
        assert rows[0]["lead_time_days"] == 7
        assert float(rows[0]["cost_per_unit"]) == 1.5
        assert float(rows[0]["fixed_cost"]) == 30.0

    def test_lane_direction_matters(self, two_warehouses):
        tid = two_warehouses
        lane_svc.upsert_lane(tid, "principal", "Norte", 3)
        assert lane_svc.resolve_lane(tid, "principal", "Norte")["lead_time_days"] == 3
        # The reverse direction was never configured -> default, not 3.
        assert lane_svc.resolve_lane(tid, "Norte", "principal")["lead_time_days"] == 1

    def test_same_warehouse_and_unknown_warehouse_rejected(self, two_warehouses):
        tid = two_warehouses
        with pytest.raises(ValueError):
            lane_svc.upsert_lane(tid, "principal", "principal", 1)
        with pytest.raises(ValueError):
            lane_svc.upsert_lane(tid, "principal", "Nowhere", 1)


class TestLaneApi:
    def test_put_viewer_denied_no_row_written(self, client, viewer_headers, two_warehouses):
        tid = two_warehouses
        r = client.put("/api/v1/inventory/warehouses/lanes", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "lead_time_days": 3, "cost_per_unit": 0.4, "fixed_cost": 12.0,
        }, headers=viewer_headers)
        assert r.status_code == 403
        assert _lane_row(tid, "principal", "Norte") is None

    def test_put_analyst_writes_row(self, client, analyst_headers, two_warehouses):
        tid = two_warehouses
        r = client.put("/api/v1/inventory/warehouses/lanes", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "lead_time_days": 3, "cost_per_unit": 0.4, "fixed_cost": 12.0,
        }, headers=analyst_headers)
        assert r.status_code == 200, r.text
        row = _lane_row(tid, "principal", "Norte")
        assert row is not None
        assert row["lead_time_days"] == 3
        assert float(row["cost_per_unit"]) == 0.4
        assert float(row["fixed_cost"]) == 12.0

    def test_list_returns_configured_lanes(self, client, analyst_headers, two_warehouses):
        client.put("/api/v1/inventory/warehouses/lanes", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "lead_time_days": 2, "cost_per_unit": 0.1, "fixed_cost": 0,
        }, headers=analyst_headers)
        r = client.get("/api/v1/inventory/warehouses/lanes", headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert [(l["from_warehouse"], l["to_warehouse"], l["lead_time_days"])
                for l in data] == [("principal", "Norte", 2)]

    def test_delete_viewer_denied_then_analyst_removes(
        self, client, viewer_headers, analyst_headers, two_warehouses,
    ):
        tid = two_warehouses
        lane_svc.upsert_lane(tid, "principal", "Norte", 3, 0.4, 12.0)

        r = client.delete(
            "/api/v1/inventory/warehouses/lanes"
            "?from_warehouse=principal&to_warehouse=Norte",
            headers=viewer_headers)
        assert r.status_code == 403
        assert _lane_row(tid, "principal", "Norte") is not None

        r = client.delete(
            "/api/v1/inventory/warehouses/lanes"
            "?from_warehouse=principal&to_warehouse=Norte",
            headers=analyst_headers)
        assert r.status_code == 204, r.text
        assert _lane_row(tid, "principal", "Norte") is None

    def test_delete_unknown_lane_is_404(self, client, analyst_headers, two_warehouses):
        r = client.delete(
            "/api/v1/inventory/warehouses/lanes"
            "?from_warehouse=principal&to_warehouse=Norte",
            headers=analyst_headers)
        assert r.status_code == 404

    def test_cross_tenant_lane_invisible(self, client, analyst_headers, two_warehouses,
                                         make_tenant_user_headers):
        client.put("/api/v1/inventory/warehouses/lanes", json={
            "from_warehouse": "principal", "to_warehouse": "Norte",
            "lead_time_days": 3, "cost_per_unit": 0.4, "fixed_cost": 12.0,
        }, headers=analyst_headers)
        other = make_tenant_user_headers(role="analyst")
        r = client.get("/api/v1/inventory/warehouses/lanes", headers=other)
        assert r.status_code == 200
        assert r.json()["data"] == []


class TestTransferStampsEta:
    def test_configured_lane_stamps_lead_time_and_expected_arrival(
        self, two_warehouses, user_id,
    ):
        tid = two_warehouses
        lane_svc.upsert_lane(tid, "principal", "Norte", 4, 0.2, 5.0)
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 10}])
        row = query_one(
            """SELECT lead_time_days, expected_arrival, created_at
               FROM inventory_transfer_log WHERE id = %s AND tenant_id = %s""",
            (t["id"], tid))
        assert row["lead_time_days"] == 4
        assert row["expected_arrival"] is not None
        delta_days = (row["expected_arrival"] - row["created_at"]).total_seconds() / 86400
        assert delta_days == pytest.approx(4.0, abs=0.01)

    def test_unconfigured_lane_stamps_the_default_one_day(self, two_warehouses, user_id):
        tid = two_warehouses
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 10}])
        row = query_one(
            """SELECT lead_time_days, expected_arrival, created_at
               FROM inventory_transfer_log WHERE id = %s""", (t["id"],))
        assert row["lead_time_days"] == lane_svc.DEFAULT_LANE_LEAD_TIME_DAYS
        delta_days = (row["expected_arrival"] - row["created_at"]).total_seconds() / 86400
        assert delta_days == pytest.approx(1.0, abs=0.01)

    def test_editing_the_lane_does_not_rewrite_a_sent_transfer(
        self, two_warehouses, user_id,
    ):
        """The ETA is frozen at send time: a lane edited afterwards must not
        retroactively change what was promised for goods already on the road."""
        tid = two_warehouses
        lane_svc.upsert_lane(tid, "principal", "Norte", 2)
        t = tr_svc.create_transfer(tid, user_id, "principal", "Norte",
                                   [{"sku": "A", "qty": 5}])
        lane_svc.upsert_lane(tid, "principal", "Norte", 9)
        row = query_one(
            "SELECT lead_time_days FROM inventory_transfer_log WHERE id = %s", (t["id"],))
        assert row["lead_time_days"] == 2


# ── The decision itself: transfer vs buy ─────────────────────────────────────

def _forecast_entry(daily, days=30):
    return {"lightgbm": {
        "historical": [],
        "forecast": [
            {"date": f"2026-08-{i+1:02d}", "value": daily, "lower": None, "upper": None}
            for i in range(days)
        ],
    }}


PURCHASE_LEAD_TIME_DAYS = 5


def _seed_network(tid, sid, unit_cost=10.0):
    """Norte needs stock (5 units at 10/day); principal can donate (600 units).
    Purchase lead time is 5 days, so a lane must beat that to win."""
    session_store.set_forecasts(tid, sid, {
        f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
        f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
    })
    for warehouse, stock in (("Norte", 5), ("principal", 600)):
        inv_svc.upsert_stock(tid, "A", {
            "current_stock": stock, "lead_time_days": PURCHASE_LEAD_TIME_DAYS,
            "warehouse": warehouse, "moq": 1, "unit_cost": unit_cost,
        })


def _needy_row(tid, sid):
    items = inv_svc.get_inventory_status_by_warehouse(tid, sid)
    return next(i for i in items if i["warehouse"] == "Norte" and i["sku"] == "A")


class TestTransferVsBuyDecision:
    def test_fast_and_cheap_lane_is_recommended(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid)
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=2, cost_per_unit=0.1, fixed_cost=1.0)

        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "transfer"
        ts = needy["transfer_suggestion"]
        assert ts["reason_code"] == "transfer_faster_and_cheaper"
        assert ts["lane_days"] == 2
        params = ts["params"]
        assert params["from_warehouse"] == "principal"
        assert params["lane_days"] == 2
        assert params["purchase_days"] == PURCHASE_LEAD_TIME_DAYS
        # qty * (10 - 0.1) - 1 fixed, comfortably positive.
        assert params["saving"] > 0
        assert needy["transfer_rejected_reason"] is None

    def test_lane_slower_than_purchase_stays_an_order(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid)
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=9, cost_per_unit=0.1, fixed_cost=0.0)

        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "order"
        assert needy["transfer_suggestion"] is None
        rejected = needy["transfer_rejected_reason"]
        assert rejected["reason_code"] == "transfer_too_slow"
        assert rejected["params"]["lane_days"] == 9
        assert rejected["params"]["purchase_days"] == PURCHASE_LEAD_TIME_DAYS
        assert rejected["params"]["from_warehouse"] == "principal"

    def test_lane_as_slow_as_purchase_stays_an_order(self, test_tenant, completed_session):
        """Equal lead times mean the move buys no time at all — it must lose."""
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid)
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=PURCHASE_LEAD_TIME_DAYS)
        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "order"
        assert needy["transfer_rejected_reason"]["reason_code"] == "transfer_too_slow"

    def test_lane_costlier_than_buying_stays_an_order(self, test_tenant, completed_session):
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid, unit_cost=10.0)
        # Fast (2 < 5) but 50/unit against a 10/unit purchase.
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=2, cost_per_unit=50.0, fixed_cost=0.0)

        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "order"
        assert needy["transfer_suggestion"] is None
        rejected = needy["transfer_rejected_reason"]
        assert rejected["reason_code"] == "transfer_more_expensive"
        assert rejected["params"]["transfer_cost"] > rejected["params"]["purchase_cost"]

    def test_fixed_cost_alone_can_lose_the_comparison(self, test_tenant, completed_session):
        """A free-per-unit lane with a huge dispatch fee is still more
        expensive than buying — the fixed cost must be part of the math."""
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid, unit_cost=10.0)
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=2, cost_per_unit=0.0, fixed_cost=1_000_000.0)
        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "order"
        assert needy["transfer_rejected_reason"]["reason_code"] == "transfer_more_expensive"

    def test_no_lane_configured_preserves_previous_behavior(
        self, test_tenant, completed_session,
    ):
        """Regression guard: a tenant that never configured a lane must get
        exactly the pre-feature recommendation (transfer, same qty and donor
        coverage) — the default lane is 1 day and free."""
        tid, sid = test_tenant["id"], completed_session["id"]
        _seed_network(tid, sid)
        assert lane_svc.list_lanes(tid) == []

        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "transfer"
        ts = needy["transfer_suggestion"]
        assert ts["from_warehouse"] == "principal"
        assert ts["donor_coverage_days_after"] == 55.5  # the pre-feature value
        assert ts["lane_days"] == lane_svc.DEFAULT_LANE_LEAD_TIME_DAYS
        assert needy["transfer_rejected_reason"] is None

    def test_no_unit_cost_skips_the_money_test_but_not_the_time_test(
        self, test_tenant, completed_session,
    ):
        """With no cost on file the money comparison is unknowable, so the
        decision rests on time alone and `saving` must stay null (the UI must
        not claim a figure that doesn't exist)."""
        tid, sid = test_tenant["id"], completed_session["id"]
        session_store.set_forecasts(tid, sid, {
            f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
            f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        })
        for warehouse, stock in (("Norte", 5), ("principal", 600)):
            inv_svc.upsert_stock(tid, "A", {
                "current_stock": stock, "lead_time_days": PURCHASE_LEAD_TIME_DAYS,
                "warehouse": warehouse, "moq": 1,
            })
        lane_svc.upsert_lane(tid, "principal", "Norte",
                             lead_time_days=2, cost_per_unit=999.0, fixed_cost=0.0)

        needy = _needy_row(tid, sid)
        assert needy["recommended_action"] == "transfer"
        assert needy["transfer_suggestion"]["params"]["saving"] is None


class TestOptimizerLaneWiring:
    def test_lane_cost_and_transit_reach_the_milp_input(self, test_tenant, test_session):
        from backend.inventory.optimizer_service import build_optimization_input

        tid, sid = test_tenant["id"], test_session["id"]
        inv_svc.upsert_stock(tid, "OPT1", {
            "current_stock": 100, "lead_time_days": 10, "unit_cost": 20.0,
            "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, "OPT1", {
            "current_stock": 0, "lead_time_days": 10, "unit_cost": 20.0,
            "warehouse": "Sur"})
        session_store.set_forecasts(tid, sid, {
            "OPT1": {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })
        lane_svc.upsert_lane(tid, "Norte", "Sur", lead_time_days=3, cost_per_unit=2.5)

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.transfer_cost_by_lane[("Norte", "Sur")] == 2.5
        assert inp.transfer_lead_buckets[("Norte", "Sur")] == 3
        # The unconfigured reverse direction keeps the documented default.
        assert inp.transfer_cost_by_lane[("Sur", "Norte")] == 0.0
        assert inp.transfer_lead_buckets[("Sur", "Norte")] == 1

    def test_slow_lane_cannot_deliver_before_its_transit_time(self):
        """Pure-engine check: a lane with N buckets of transit is bounded to
        zero for buckets 1..N, so the solver cannot teleport stock."""
        from forecasting_core.business.optimizer import OptimizationInput, build_problem

        inp = OptimizationInput(
            skus=["A"], warehouses=["W1", "W2"], horizon=4,
            demand={("A", "W1"): [0.0] * 4, ("A", "W2"): [5.0] * 4},
            stock0={("A", "W1"): 100.0, ("A", "W2"): 0.0},
            lead_time_buckets={"A": 1},
            holding_cost={"A": 0.01}, stockout_cost={"A": 30.0}, order_cost={"A": 10.0},
            transfer_cost=1.0,
            transfer_cost_by_lane={("W1", "W2"): 0.2},
            transfer_lead_buckets={("W1", "W2"): 2},
        )
        problem = build_problem(inp)
        idx = problem.index
        for t in (1, 2):
            assert problem.bounds.ub[idx.transfer_idx("A", "W1", "W2", t)] == 0
        for t in (3, 4):
            assert problem.bounds.ub[idx.transfer_idx("A", "W1", "W2", t)] > 0
            assert problem.c[idx.transfer_idx("A", "W1", "W2", t)] == 0.2
        # The unconfigured reverse lane keeps the global cost and no transit gate.
        assert problem.c[idx.transfer_idx("A", "W2", "W1", 1)] == 1.0
        assert problem.bounds.ub[idx.transfer_idx("A", "W2", "W1", 1)] > 0

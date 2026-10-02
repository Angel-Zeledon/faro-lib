"""
The plan's horizon reaches the next order's arrival (math audit 2026-10-01, O1).

Owner's decision: each SKU is planned over max(configured horizon, lead time +
review period), with "no declared review period" meaning the next order can go
out one bucket later. Before it, a SKU whose lead time reached the horizon —
with the defaults (15-day lead time, 14-day horizon), every SKU nobody had
configured — was planned at 0, under a heading that said the plan covers the
next 14 days.

What the extension buys, exactly: the MILP is a lost-sales model, so the
quantity it plans for an extended SKU is the demand from the order's arrival
until the next order can arrive, net of whatever stock and on-order units are
still left at arrival. Demand during the lead time is served by what is on hand
or not at all — no order placed today can reach it.
"""

from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import service as inv_svc


def _sku():
    return f"O1-{uuid4().hex[:8]}"


def _forecast(tid, sid, sku, *, per_day=10.0, days=30):
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": f"2026-01-{(i % 28) + 1:02d}", "value": per_day,
         "lower": per_day, "upper": per_day}
        for i in range(days)]}}})


def _solve(tid, sid, horizon_days=14, period="daily", stock_rows=None):
    from forecasting_core.business.optimizer import optimize
    stock_rows = stock_rows if stock_rows is not None else inv_svc.list_stock(tid)
    planning = opt_svc.resolve_planning_inputs(tid, stock_rows)
    inp = opt_svc.build_optimization_input(
        tid, sid, horizon_days, stock_rows=stock_rows, period=period, planning=planning)
    result = optimize(inp)
    out = opt_svc.serialize_optimization_result(
        inp, result, stock_rows, horizon_days=horizon_days, planning=planning)
    return inp, result, out


def _qty(out, sku):
    return sum(o["qty"] for o in out["orders"] if o["sku"] == sku)


class TestEffectiveHorizonRule:

    @pytest.mark.parametrize("horizon,lead,review,expected", [
        (14, 15, 0, 16),   # the audit's case: one bucket past the lead time
        (14, 14, 0, 15),   # lead == horizon also admitted no arrival
        (14, 13, 0, 14),   # horizon > lead: unchanged
        (14, 10, 0, 14),   # horizon > lead: unchanged
        (14, 10, 7, 17),   # a declared cadence the horizon does not reach
        (14, 15, 7, 22),
        (30, 10, 7, 30),   # the horizon already reaches it
    ])
    def test_rule(self, horizon, lead, review, expected):
        assert opt_svc.effective_horizon_buckets(horizon, lead, review) == expected


class TestTheAuditReproductionThroughTheService:

    def test_lead_15_horizon_14_is_no_longer_planned_at_zero(self, test_tenant, test_session):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        _forecast(tid, sid, sku)

        inp, result, out = _solve(tid, sid, 14)
        assert inp.horizon == 16
        assert result.status == "optimal"
        assert _qty(out, sku) == 10, "the post-arrival demand, net of nothing on hand"
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["effective_horizon_days"] == 16
        assert line["horizon_extended"] is True
        assert line["demand_extrapolated"] is False
        assert out["horizon_days"] == 14, "the configured horizon is still reported as such"
        assert out["extended_lines"] == 1

    def test_stock_and_on_order_are_netted_over_the_review_window(
        self, test_tenant, test_session, monkeypatch,
    ):
        """Supplier reviewed weekly: horizon 15 + 7 = 22. 100 on hand + 80 on
        order cover 18 days; arrivals 16..22 need 70; 30 are left at day 15,
        so 40 are bought."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        supplier = f"Weekly-{uuid4().hex[:6]}"
        execute("INSERT INTO suppliers (tenant_id, name, review_period_days) "
                "VALUES (%s, %s, 7)", (tid, supplier))
        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "supplier": supplier, "warehouse": "principal"})
        _forecast(tid, sid, sku)
        monkeypatch.setattr(inv_svc, "get_incoming_qty",
                            lambda _tid: {(sku, "principal"): 80.0})

        inp, _result, out = _solve(tid, sid, 14)
        assert inp.horizon == 22
        assert inp.stock0[(sku, "principal")] == pytest.approx(180.0)
        assert _qty(out, sku) == 40

    def test_a_forecast_shorter_than_the_extension_is_extended_at_its_mean(
        self, test_tenant, test_session,
    ):
        """A 45-day supplier on a 30-day forecast would otherwise be extended
        into zeros and planned at 0 again."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 45,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        _forecast(tid, sid, sku, days=30)

        inp, _result, out = _solve(tid, sid, 14)
        assert inp.horizon == 46
        assert inp.demand[(sku, "principal")][45] == pytest.approx(10.0)
        assert _qty(out, sku) == 10
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["demand_extrapolated"] is True

    def test_weekly_plan_extends_in_weeks(self, test_tenant, test_session):
        """Weekly: horizon 14 days = 2 buckets, lead 15 days = 3 buckets ->
        4 buckets = 28 days; one week of 70 is planned."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        _forecast(tid, sid, sku, per_day=70.0, days=8)

        inp, _result, out = _solve(tid, sid, 14, period="weekly")
        assert inp.horizon == 4
        assert _qty(out, sku) == 70
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["effective_horizon_days"] == 28


class TestHorizonPastTheLeadTimeIsUnchanged:

    def test_golden_lead_10_horizon_14(self, test_tenant, test_session):
        """The audit's own control: lead 10, horizon 14 ordered 40 (buckets
        11-14). Same input, same plan, same problem."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 10,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        _forecast(tid, sid, sku)

        inp, result, out = _solve(tid, sid, 14)
        assert inp.horizon == 14
        assert inp.horizon_by_sku == {}
        assert _qty(out, sku) == 40
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["horizon_extended"] is False
        assert line["effective_horizon_days"] == 14

    def test_an_unextended_sku_is_planned_the_same_beside_an_extended_one(
        self, test_tenant, test_session,
    ):
        tid, sid = test_tenant["id"], test_session["id"]
        short, long_ = _sku(), _sku()
        inv_svc.upsert_stock(tid, short, {"current_stock": 0, "lead_time_days": 10,
                                          "unit_cost": 10.0, "moq": 1,
                                          "warehouse": "principal"})
        _forecast(tid, sid, short)
        _inp, _r, alone = _solve(tid, sid, 14)

        inv_svc.upsert_stock(tid, long_, {"current_stock": 0, "lead_time_days": 20,
                                          "unit_cost": 10.0, "moq": 1,
                                          "warehouse": "principal"})
        forecasts = session_store.get_forecasts(tid, sid)
        forecasts[long_] = forecasts[short]
        session_store.set_forecasts(tid, sid, forecasts)
        inp, _r, together = _solve(tid, sid, 14)

        assert inp.horizon == 21
        assert inp.horizon_by_sku[short] == 14
        assert _qty(together, short) == _qty(alone, short) == 40
        assert _qty(together, long_) == 10


class TestTransfersFollowTheSameHorizon:

    def test_a_sister_warehouse_covers_the_extension_before_anything_is_bought(
        self, test_tenant, test_session,
    ):
        """Two warehouses, demand at principal, 400 idle units at Norte, lead
        time 15 on a 14-day horizon. The extension must not turn into a
        purchase while a free one-day transfer can cover it — and the
        transfers must reach the extended buckets, not stop at day 14."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 400, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "Norte"})
        _forecast(tid, sid, sku)

        inp, result, out = _solve(tid, sid, 14)
        assert inp.horizon == 16
        assert inp.demand[(sku, "principal")][0] == pytest.approx(10.0)
        assert inp.demand[(sku, "Norte")][0] == pytest.approx(0.0)
        assert _qty(out, sku) == 0
        moved = sum(t["qty"] for t in out["transfers"]
                    if t["sku"] == sku and t["to_warehouse"] == "principal")
        # Day 1 cannot be reached by a one-day lane; days 2..16 can.
        assert moved == 150
        assert result.status == "optimal"


class TestTheEndpointSaysWhatEachLineCovers:

    def test_optimize_reports_the_effective_horizon(
        self, client, auth_headers, test_tenant, test_session,
    ):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 15,
                                        "unit_cost": 10.0, "moq": 1,
                                        "warehouse": "principal"})
        _forecast(tid, sid, sku)
        resp = client.get("/api/v1/inventory/optimize",
                          params={"session_id": sid, "horizon_days": 14},
                          headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["horizon_days"] == 14
        line = next(o for o in data["orders"] if o["sku"] == sku)
        assert line["qty"] == 10
        assert line["effective_horizon_days"] == 16
        assert line["horizon_extended"] is True

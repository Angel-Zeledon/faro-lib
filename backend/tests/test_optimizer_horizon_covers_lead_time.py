"""
A SKU whose next order lands past the optimizer's horizon gets the Panel's
quantity (math audit 2026-10-01, O1 — owner's decision "igual que el Panel").

Before: with the defaults (15-day lead time, 14-day horizon) every SKU nobody
had configured was planned at 0 by the MILP — every arrival bucket was gated —
while /compras showed a real number for the same SKU from the semáforo.

Now such a SKU leaves the MILP and the optimizer reports exactly what
`get_inventory_status` (the Panel / morning briefing) recommends: demand ×
(lead time + review period) + safety stock − (on hand + on order), with its
MOQ floor and signal gate. Every SKU whose horizon reaches its next order is
optimized exactly as before.

Each "equals the Panel" test calls BOTH paths and compares them, so a change
to either side that makes them disagree goes red here.
"""

from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import service as inv_svc


def _sku():
    return f"O1-{uuid4().hex[:8]}"


def _forecast(tid, sid, skus, *, per_period=10.0, steps=30):
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": f"2026-01-{(i % 28) + 1:02d}", "value": per_period,
         "lower": per_period, "upper": per_period}
        for i in range(steps)]}} for sku in skus})


def _plan(tid, sid, horizon_days=14, period="daily"):
    stock_rows = inv_svc.list_stock(tid)
    planning = opt_svc.resolve_planning_inputs(tid, stock_rows)
    inp = opt_svc.build_optimization_input(
        tid, sid, horizon_days, stock_rows=stock_rows, period=period, planning=planning)
    result = opt_svc.solve(inp)
    return inp, result, opt_svc.serialize_optimization_result(
        inp, result, stock_rows, horizon_days=horizon_days, planning=planning)


def _panel_qty(tid, sid, sku, period="daily"):
    rows = inv_svc.get_inventory_status(tid, sid, 0.95, period)
    return next(r for r in rows if r["sku"] == sku)["recommended_qty"]


def _qty(out, sku):
    return sum(o["qty"] for o in out["orders"] if o["sku"] == sku)


def _stock(tid, sku, **fields):
    inv_svc.upsert_stock(tid, sku, {"unit_cost": 10.0, "moq": 1,
                                    "warehouse": "principal", **fields})


class TestEffectiveHorizonRule:

    @pytest.mark.parametrize("horizon,lead,review,expected", [
        (14, 15, 0, 16),   # the audit's case: past the horizon
        (14, 14, 0, 15),   # lead == horizon also admitted no arrival
        (14, 13, 0, 14),   # horizon reaches it: MILP as before
        (14, 10, 0, 14),
        (14, 10, 7, 17),   # a declared cadence the horizon does not reach
        (30, 10, 7, 30),
    ])
    def test_rule(self, horizon, lead, review, expected):
        assert opt_svc.effective_horizon_buckets(horizon, lead, review) == expected


class TestEqualsThePanel:

    def test_the_audit_case(self, test_tenant, test_session):
        """10/day, lead time 15, horizon 14, nothing on hand: the MILP planned 0."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=0, lead_time_days=15)
        _forecast(tid, sid, [sku])

        inp, result, out = _plan(tid, sid, 14)
        panel = _panel_qty(tid, sid, sku)
        assert panel >= 150, "the Panel covers at least the 15 days of lead time"
        assert _qty(out, sku) == panel
        assert sku not in inp.skus, "the MILP must not plan it a second time"
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["sized_like_panel"] is True
        assert line["effective_horizon_days"] == 15
        assert out["extended_lines"] == 1
        assert result.status == "optimal", "no SKU left for the solver is not a fallback"

    def test_with_stock_on_order_and_a_review_period(
        self, test_tenant, test_session, monkeypatch,
    ):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        supplier = f"Weekly-{uuid4().hex[:6]}"
        execute("INSERT INTO suppliers (tenant_id, name, review_period_days) "
                "VALUES (%s, %s, 7)", (tid, supplier))
        _stock(tid, sku, current_stock=60, lead_time_days=15, supplier=supplier)
        _forecast(tid, sid, [sku])
        monkeypatch.setattr(inv_svc, "get_incoming_qty",
                            lambda _tid: {(sku, "principal"): 40.0})
        monkeypatch.setattr(inv_svc, "get_incoming_detail", lambda _tid: [
            {"sku": sku, "warehouse": "principal", "qty": 40.0, "kind": "po",
             "reference": "OC-TEST", "source_id": "x"}])

        _inp, _r, out = _plan(tid, sid, 14)
        panel = _panel_qty(tid, sid, sku)
        assert panel > 0
        assert _qty(out, sku) == panel
        line = next(o for o in out["orders"] if o["sku"] == sku)
        assert line["effective_horizon_days"] == 22   # 15 + 7

        # The on-order units are netted on both sides: without them both grow
        # by exactly the same 40.
        monkeypatch.setattr(inv_svc, "get_incoming_qty", lambda _tid: {})
        monkeypatch.setattr(inv_svc, "get_incoming_detail", lambda _tid: [])
        _inp, _r, out2 = _plan(tid, sid, 14)
        assert _qty(out2, sku) == _panel_qty(tid, sid, sku) == panel + 40

    def test_a_sku_the_panel_says_not_to_order_gets_no_line(self, test_tenant, test_session):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=5000, lead_time_days=15)
        _forecast(tid, sid, [sku])
        _inp, _r, out = _plan(tid, sid, 14)
        assert _panel_qty(tid, sid, sku) == 0
        assert _qty(out, sku) == 0
        assert all(o["sku"] != sku for o in out["orders"])

    def test_weekly_tenant(self, test_tenant, test_session):
        """Weekly: horizon 14 days = 2 buckets, lead 15 days = 3 buckets."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=0, lead_time_days=15)
        _forecast(tid, sid, [sku], per_period=70.0, steps=8)

        _inp, _r, out = _plan(tid, sid, 14, period="weekly")
        panel = _panel_qty(tid, sid, sku, period="weekly")
        assert panel > 0
        assert _qty(out, sku) == panel

    def test_the_cash_check_prices_the_same_line(
        self, client, auth_headers, test_tenant, test_session,
    ):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=0, lead_time_days=15)
        _forecast(tid, sid, [sku])
        resp = client.post("/api/v1/inventory/cash-calendar/fit",
                           params={"session_id": sid, "horizon_days": 14},
                           json={"budget": 1_000_000}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        line = next(x for x in resp.json()["data"]["lines"] if x["sku"] == sku)
        assert line["amount"] == pytest.approx(_panel_qty(tid, sid, sku) * 10.0)


class TestWhatTheHorizonReachesIsUnchanged:

    def test_golden_lead_10_horizon_14(self, test_tenant, test_session):
        """The audit's own control: lead 10, horizon 14 ordered 40 (buckets
        11-14). Same plan, by the MILP."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=0, lead_time_days=10)
        _forecast(tid, sid, [sku])

        inp, _r, out = _plan(tid, sid, 14)
        assert inp.skus == [sku]
        assert inp.horizon == 14
        assert _qty(out, sku) == 40
        assert out["orders"][0]["sized_like_panel"] is False
        assert out["extended_lines"] == 0

    def test_a_milp_sku_is_planned_the_same_beside_a_panel_sku(
        self, test_tenant, test_session,
    ):
        tid, sid = test_tenant["id"], test_session["id"]
        short, long_ = _sku(), _sku()
        _stock(tid, short, current_stock=0, lead_time_days=10)
        _forecast(tid, sid, [short])
        _inp, _r, alone = _plan(tid, sid, 14)

        _stock(tid, long_, current_stock=0, lead_time_days=20)
        _forecast(tid, sid, [short, long_])
        inp, _r, together = _plan(tid, sid, 14)

        assert inp.skus == [short]
        assert _qty(together, short) == _qty(alone, short) == 40
        assert _qty(together, long_) == _panel_qty(tid, sid, long_)


class TestTheEndpointSaysWhichLinesAreThePanels:

    def test_optimize(self, client, auth_headers, test_tenant, test_session):
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        _stock(tid, sku, current_stock=0, lead_time_days=15)
        _forecast(tid, sid, [sku])
        resp = client.get("/api/v1/inventory/optimize",
                          params={"session_id": sid, "horizon_days": 14},
                          headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "optimal"
        line = next(o for o in data["orders"] if o["sku"] == sku)
        assert line["qty"] == _panel_qty(tid, sid, sku)
        assert line["sized_like_panel"] is True
        assert data["extended_lines"] == 1

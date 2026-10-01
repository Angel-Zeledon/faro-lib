"""
Mathematical audit of 2026-10-01 — docs/stability.md, "Mathematical audit".

Every test here failed on the code as it stood before that day and states the
arithmetic it pins: the inputs, what was shown, and what is true. Most are the
same defect class as stability §1.6 and §3.1 — a per-PERIOD forecast (per week
on a weekly tenant) multiplied by, or compared with, a count of DAYS.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import service as inv_svc


def _ts(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _snapshot(tid: str, sku: str, level: float, warehouse, days_ago: float) -> None:
    execute(
        "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tid, sku, level, warehouse, _ts(days_ago)),
    )


# ── Tenant-wide stock history: two warehouses written on different days ─────

class TestTenantWideLevelCarriesEveryWarehouseForward:
    """The 11.15 fix summed, per day, only the warehouses WRITTEN that day.
    principal written Monday (500) and Norte Wednesday (20) gave the series
    500, 20 — a 480-unit fall in a tenant whose stock never moved."""

    def test_pure_helper(self):
        rows = [
            {"warehouse": "principal", "current_stock": 500, "recorded_at": _ts(5)},
            {"warehouse": "Norte", "current_stock": 20, "recorded_at": _ts(3)},
            {"warehouse": "Norte", "current_stock": 15, "recorded_at": _ts(1)},
        ]
        levels = [lvl for _at, lvl in inv_svc.tenant_wide_daily_levels(rows)]
        assert levels == [500, 520, 515]

    def test_get_stock_history_does_not_invent_a_fall(self, client, test_tenant):
        tid, sku = test_tenant["id"], "MA-HIST-1"
        _snapshot(tid, sku, 500, "principal", 5)
        _snapshot(tid, sku, 20, "Norte", 3)
        series = [h["stock"] for h in inv_svc.get_stock_history(tid, sku, days=10)]
        assert series == [500, 520], series

    def test_a_level_from_before_the_window_still_counts(self, client, test_tenant):
        """principal last written 20 days ago still holds its 500 today."""
        tid, sku = test_tenant["id"], "MA-HIST-2"
        _snapshot(tid, sku, 500, "principal", 20)
        _snapshot(tid, sku, 20, "Norte", 3)
        series = [h["stock"] for h in inv_svc.get_stock_history(tid, sku, days=10)]
        assert series == [520], series


# ── Demand trend ("real demand is running X% above the forecast") ───────────

class TestDemandTrend:

    def _history(self, monkeypatch, points):
        hist = [{"stock": lvl, "date": _ts(d).isoformat()} for d, lvl in points]
        monkeypatch.setattr(inv_svc, "get_stock_history", lambda *a, **k: hist)

    def test_snapshot_count_is_not_elapsed_days(self, monkeypatch):
        """4 writes spread over 13 days, selling exactly the forecast 10/day:
        expected used to be 10 x 4 snapshots = 40 against 130 sold -> +225%."""
        self._history(monkeypatch, [(13, 200), (9, 160), (4, 110), (0, 70)])
        assert inv_svc._calc_demand_trend("t", "s", 10.0, days=14) is None

    def test_a_weekly_forecast_is_converted_to_days(self, monkeypatch):
        """70/week is 10/day. Read as 70/day it reported -86% on every SKU of
        every weekly tenant."""
        self._history(monkeypatch, [(13, 200), (9, 160), (4, 110), (0, 70)])
        assert inv_svc._calc_demand_trend("t", "s", 70.0, days=14, period="weekly") is None

    def test_a_reception_does_not_cancel_the_sales(self, monkeypatch):
        """Sold 10/day for 12 days with a 300-unit reception in between:
        first - last = 120 - 300 < 0 used to return None (no alert)."""
        self._history(monkeypatch, [(12, 120), (8, 80), (7, 380), (0, 300)])
        # 40 + 80 = 120 sold over 12 days = exactly the forecast.
        assert inv_svc._calc_demand_trend("t", "s", 10.0, days=14) is None
        # And a real doubling is still caught.
        assert inv_svc._calc_demand_trend("t", "s", 5.0, days=14) == pytest.approx(100.0)


# ── "Pausing the next order would free ₡X" ─────────────────────────────────

class TestOverstockFreesOnlyTheExcess:

    def test_one_day_past_the_ceiling_frees_one_day_of_stock(self):
        """46 days of cover, 15-day lead time, ceiling 45 days, ₡46,000 on the
        shelf: the excess is 1 day = ₡1,000. It used to say ₡46,000."""
        item = {"sku": "OV", "display_name": "OV", "signal": "SOBRESTOCK",
                "coverage_days": 46.0, "lead_time_days": 15, "abc": "A",
                "inventory_value": 46000.0, "recommended_qty": 0}
        rec = next(r for r in inv_svc.generate_recommendations([item])
                   if r["rec_type"] == "OVERSTOCK")
        assert rec["text_params"]["amount"] == "₡1,000"


# ── Price breaks ────────────────────────────────────────────────────────────

class TestPriceBreakSpeaksDays:

    def _breaks(self):
        return [{"min_qty": 500, "unit_price": 7.8, "supplier_id": "sup",
                 "supplier_name": "Acme", "sku": "PB"}]

    def test_a_weekly_forecast_is_converted_before_judging_coverage(self, monkeypatch):
        """70/week = 10/day, 50 on hand, stepping 100 -> 500 units: 55 days of
        cover against a 45-day limit must be refused. Read as 70/DAY it was
        7.9 'days' and recommended."""
        from backend.inventory import price_break_service as pb
        monkeypatch.setattr(pb, "list_price_breaks", lambda tid: self._breaks())
        status = [{"sku": "PB", "daily_demand": 70.0, "current_stock": 50.0,
                   "lead_time_days": 15, "unit_cost": 8.5, "supplier": "Acme"}]
        out = pb.evaluate_cart("t", [{"sku": "PB", "quantity": 100}], status,
                               0.20, period="weekly")
        assert out[0]["total_coverage_days"] == pytest.approx(55.0)
        assert out[0]["worth_it"] is False
        assert out[0]["reason_code"] == "would_overstock"

    def test_holding_counts_the_wait_behind_the_shelf(self):
        """S=300 on the shelf at arrival, q0=100, q1=500, d=10/day:
        extra unit-days = 400 * (2*300 + 500) / 20 = 22,000 — the old
        400^2 / 20 = 8,000 ignored that the extra units cannot sell before
        the 300 + 100 ahead of them."""
        from backend.inventory import price_break_service as pb
        opp = pb.evaluate_step_up(
            sku="PB", supplier_name="Acme", current_quantity=100, base_cost=10.0,
            breaks=[{"min_qty": 500, "unit_price": 9.0}], daily_demand=10.0,
            current_stock=300, lead_time_days=0, holding_cost_pct=0.365,
        )
        # 22,000 unit-days x 9.0 x 0.365 / 365 = 198.0
        assert opp["holding_cost"] == pytest.approx(198.0)


# ── BOM explosion ───────────────────────────────────────────────────────────

class TestBomSpeaksDays:

    def test_a_weekly_finished_good_is_not_multiplied_by_days(self, monkeypatch):
        """70/week over a 28-day horizon is 280 units, not 70 x 28 = 1,960."""
        from backend.inventory import bom_service
        monkeypatch.setattr(inv_svc, "get_inventory_status", lambda *a, **k: [
            {"sku": "FG", "daily_demand": 70.0, "current_stock": 0.0,
             "product_type": "finished_good"},
        ])
        monkeypatch.setattr(bom_service, "list_all_bom", lambda tid: [
            {"parent_sku": "FG", "child_sku": "RM", "quantity": 2.0, "unit": "u"},
        ])
        out = bom_service.explode_requirements("t", "s", 28, period="weekly")
        fg = out["finished_goods"][0]
        assert fg["forecast_demand"] == pytest.approx(280.0)


# ── Recommendation log and "cost of ignoring" ───────────────────────────────

class TestRecommendationLogStoresADailyRate:

    def test_weekly_demand_is_stored_per_day(self, client, test_tenant):
        from backend.inventory import recommendation_log
        tid = test_tenant["id"]
        recommendation_log.record_recommendations(
            tid, "sess", [{"sku": "RL-1", "signal": "PEDIR_YA", "daily_demand": 70.0,
                           "recommended_qty": 10, "current_stock": 0,
                           "lead_time_days": 15}],
            period="weekly",
        )
        row = query_one(
            "SELECT avg_daily_demand FROM inventory_recommendation_log "
            "WHERE tenant_id = %s AND sku = 'RL-1'", (tid,))
        assert row["avg_daily_demand"] == pytest.approx(10.0)


class TestCostOfIgnoringReadsTheTenantWideLevel:

    def test_one_empty_warehouse_is_not_a_stockout(self, client, test_tenant):
        """Norte at 0 while principal holds 500 is not lost sales."""
        from backend.inventory import recommendation_reports as rr
        tid, sku = test_tenant["id"], "CI-1"
        _snapshot(tid, sku, 500, "principal", 6)
        _snapshot(tid, sku, 0, "Norte", 4)
        snaps = rr._stock_snapshots(tid, sku, _ts(10).date())
        assert all(s["current_stock"] > 0 for s in snaps), snaps


# ── Dead capital's signal column ────────────────────────────────────────────

class TestDeadCapitalSignalUsesTheTenantsGrain:

    def test_the_status_is_computed_at_the_planning_period(self, monkeypatch, client, test_tenant):
        from backend.inventory import dead_capital
        from backend.sessions import planning_service
        seen = {}

        def _status(tenant_id, session_id, *a, **k):
            seen["period"] = k.get("period", a[1] if len(a) > 1 else "daily")
            return []

        monkeypatch.setattr(planning_service, "get_planning",
                            lambda tid: {"period": "weekly", "horizon": 8})
        monkeypatch.setattr(inv_svc, "get_inventory_status", _status)
        dead_capital.get_dead_capital(test_tenant["id"], session_id="any")
        assert seen["period"] == "weekly"


# ── "You sell X a day, so it lasts you N days" ──────────────────────────────

class TestExplanationSpeaksDays:

    def test_a_weekly_tenant_reads_per_day_figures(self):
        """70/week and 3 weeks of cover used to read "you sell 70 a day, it
        lasts you 3 days" — both numbers in the wrong unit for their words."""
        exp = inv_svc.build_explanation(
            current_stock=210, daily_demand=70.0, coverage_days=3.0, lead_time=14,
            lead_time_source="user", reorder_point=180.0, signal="OK", period="weekly",
        )
        assert exp["params"]["daily_demand"] == pytest.approx(10.0)
        assert exp["params"]["coverage_days"] == pytest.approx(21.0)
        assert "sell 10.0 per day" in exp["text"]
        assert "lasts you 21 days" in exp["text"]

    def test_daily_is_unchanged(self):
        exp = inv_svc.build_explanation(
            current_stock=30, daily_demand=10.0, coverage_days=3.0, lead_time=14,
            lead_time_source="user", reorder_point=180.0, signal="PEDIR_YA",
        )
        assert exp["params"]["daily_demand"] == pytest.approx(10.0)
        assert exp["params"]["coverage_days"] == pytest.approx(3.0)


class TestBreakdownAddsUp:
    """`antes_moq` is the last step of a sum shown to the buyer. It left out
    the units already on their way, so "before rounding 150" was followed by
    an order of 50."""

    def test_incoming_is_a_step_of_the_sum(self, client, auth_headers, test_tenant, monkeypatch):
        from backend.db import session_store
        from backend.sessions.service import create_session
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ma-breakdown")["id"]
        r = client.put("/api/v1/inventory/stock/MA-BRK",
                       json={"current_stock": 0, "lead_time_days": 10, "moq": 1},
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        session_store.set_forecasts(tid, sid, {"MA-BRK": {"lightgbm": {"forecast": [
            {"date": f"2026-01-{i + 1:02d}", "value": 10.0, "lower": 10.0, "upper": 10.0}
            for i in range(14)]}}})
        monkeypatch.setattr(inv_svc, "get_incoming_qty",
                            lambda tid: {("MA-BRK", "principal"): 60.0})
        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == "MA-BRK")
        calc = item["calc_explanation"]
        assert calc["incoming"] == pytest.approx(60.0)
        # 10/day x 10 days = 100, no spread, 0 on hand, 60 on the way -> 40.
        assert calc["antes_moq"] == pytest.approx(40.0)
        assert item["recommended_qty"] == pytest.approx(40.0)

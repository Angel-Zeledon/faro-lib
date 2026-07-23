"""Per-period coverage/semáforo + horizon windowing (multi-period Phase C)."""

import math

from backend.inventory.service import (
    _DAYS_PER_PERIOD,
    _days_per_period,
    _lead_time_in_periods,
    _steps_for_lead_time,
)


class TestPeriodMathHelpers:
    def test_days_per_period_map(self):
        assert _DAYS_PER_PERIOD == {"daily": 1, "weekly": 7, "monthly": 30}
        assert _days_per_period("daily") == 1
        assert _days_per_period("weekly") == 7
        assert _days_per_period("monthly") == 30
        # Unknown/legacy period degrades to daily (1) — never raises.
        assert _days_per_period("fortnightly") == 1
        assert _days_per_period(None) == 1

    def test_lead_time_in_periods(self):
        assert _lead_time_in_periods(14, "weekly") == 2.0
        assert _lead_time_in_periods(15, "weekly") == 15 / 7
        assert _lead_time_in_periods(30, "monthly") == 1.0
        assert _lead_time_in_periods(15, "daily") == 15.0

    def test_steps_for_lead_time_rounds_up_min_one(self):
        assert _steps_for_lead_time(15, "weekly") == 3     # ceil(15/7)
        assert _steps_for_lead_time(14, "weekly") == 2     # ceil(14/7)
        assert _steps_for_lead_time(5, "weekly") == 1      # ceil(5/7) -> 1 floor
        assert _steps_for_lead_time(45, "monthly") == 2    # ceil(45/30)
        assert _steps_for_lead_time(15, "daily") == 15


from backend.db import session_store
from backend.inventory import service as svc
from backend.sessions.service import create_session


def _forecast(per_bucket_demand: float, spread: float, n: int = 30) -> dict:
    """One model, n buckets of constant demand. In a period-trained session each
    bucket is one PERIOD, so per_bucket_demand is units/period for that grain."""
    pts = [
        {
            "date": f"2026-01-{i + 1:02d}",
            "value": per_bucket_demand,
            "lower": max(0.0, per_bucket_demand - spread),
            "upper": per_bucket_demand + spread,
        }
        for i in range(n)
    ]
    return {"lightgbm": {"forecast": pts}}


def _put_stock(client, headers, sku, **fields):
    r = client.put(f"/api/v1/inventory/stock/{sku}", json=fields, headers=headers)
    assert r.status_code == 200, r.text


class TestWeeklyCoverage:
    def test_weekly_coverage_reads_in_weeks_and_signal_matches_hand_calc(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "weekly-cov")["id"]
        sku = "WK_COV"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        items = svc.get_inventory_status(tid, sid, period="weekly")
        it = next(i for i in items if i["sku"] == sku)
        assert it["coverage_days"] == 4.0          # value is in WEEKS now
        assert it["daily_demand"] == 10.0          # per-period (weekly) demand
        assert it["signal"] == "OK"

    def test_switching_period_flips_the_same_sku_coverage(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "flip")["id"]
        sku = "FLIP"
        _put_stock(client, auth_headers, sku, current_stock=20, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        daily = next(i for i in svc.get_inventory_status(tid, sid, period="daily")
                     if i["sku"] == sku)
        weekly = next(i for i in svc.get_inventory_status(tid, sid, period="weekly")
                      if i["sku"] == sku)
        assert daily["signal"] == "PEDIR_YA"
        assert weekly["signal"] == "PEDIR_PRONTO"
        assert daily["coverage_days"] == 2.0 and weekly["coverage_days"] == 2.0


class TestDailyRegression:
    def test_period_daily_is_byte_identical_to_default(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-regression")["id"]
        _put_stock(client, auth_headers, "R_SHORT", current_stock=1, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_OK", current_stock=130, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_PILE", current_stock=9999, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {
            "R_SHORT": _forecast(100.0, 10.0, n=14),
            "R_OK":    _forecast(10.0, 6.0, n=14),
            "R_PILE":  _forecast(1.0, 0.1, n=14),
        })

        default_items = svc.get_inventory_status(tid, sid)
        daily_items = svc.get_inventory_status(tid, sid, period="daily")
        assert default_items == daily_items

    def test_hand_computed_daily_values_unchanged(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-hand")["id"]
        _put_stock(client, auth_headers, "HAND", current_stock=40, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"HAND": _forecast(10.0, 0.0, n=14)})
        it = next(i for i in svc.get_inventory_status(tid, sid) if i["sku"] == "HAND")
        assert it["coverage_days"] == 4.0
        assert it["daily_demand"] == 10.0
        assert it["signal"] == "PEDIR_YA"


class TestByWarehousePeriod:
    def test_weekly_by_warehouse_coverage_in_weeks(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-weekly")["id"]
        sku = "WHWK"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        rows = svc.get_inventory_status_by_warehouse(tid, sid, period="weekly")
        row = next(r for r in rows if r["sku"] == sku)
        assert row["coverage_days"] == 4.0     # weeks
        assert row["signal"] == "OK"

    def test_by_warehouse_daily_default_unchanged(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-daily")["id"]
        sku = "WHDL"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=10,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0, n=14)})
        default = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid)
                       if r["sku"] == sku)
        explicit = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid, period="daily")
                        if r["sku"] == sku)
        assert default == explicit
        assert default["signal"] == "PEDIR_YA"


class TestStatusEndpointPeriod:
    def test_status_envelope_carries_active_period(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-weekly")["id"]
        _put_stock(client, auth_headers, "EP", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"EP": _forecast(10.0, 0.0)})

        monkeypatch.setattr(
            inv_api.planning_service, "get_planning",
            lambda t: {"period": "weekly", "horizon": 4,
                       "available_periods": ["daily", "weekly"], "max_horizon": 26})

        r = client.get(f"/api/v1/inventory/status?session_id={sid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["period"] == "weekly"
        assert data["coverage_unit"] == "week"
        it = next(i for i in data["items"] if i["sku"] == "EP")
        assert it["coverage_days"] == 4.0   # 4 weeks
        assert it["signal"] == "OK"

    def test_status_defaults_session_to_active_resolver(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-default")["id"]
        _put_stock(client, auth_headers, "EPD", current_stock=1, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"EPD": _forecast(100.0, 0.0, n=14)})
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: sid)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "daily", "horizon": 14,
                                       "available_periods": ["daily"], "max_horizon": 90})
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 200, r.text
        skus = {i["sku"] for i in r.json()["data"]["items"]}
        assert "EPD" in skus

    def test_status_no_session_and_no_active_returns_400(
        self, client, auth_headers, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: None)
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 400


class TestOptimizerHorizonConversion:
    def test_endpoint_converts_active_horizon_to_days(
        self, client, auth_headers, test_tenant, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        captured = {}

        def _fake_build(tenant_id, session_id, horizon_days, stock_rows=None, period="daily"):
            captured["horizon_days"] = horizon_days
            captured["period"] = period
            return None

        monkeypatch.setattr(inv_api.opt_svc, "build_optimization_input", _fake_build)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 4,
                                       "available_periods": ["daily", "monthly"],
                                       "max_horizon": 12})
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        assert captured["horizon_days"] == 120
        assert captured["period"] == "monthly"

    def test_endpoint_accepts_horizon_beyond_old_cap(
        self, client, auth_headers, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 12,
                                       "available_periods": ["monthly"], "max_horizon": 12})
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text


class TestOptimizerLeadTimeBuckets:
    def test_lead_time_periodized(self, client, auth_headers, test_tenant):
        from backend.inventory import optimizer_service as opt
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "opt-lead")["id"]
        _put_stock(client, auth_headers, "OPTL", current_stock=5, lead_time_days=30,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {"OPTL": _forecast(10.0, 0.0)})
        inp = opt.build_optimization_input(tid, sid, horizon_days=4, period="monthly")
        assert inp is not None
        assert inp.lead_time_buckets["OPTL"] == 1
        inp_d = opt.build_optimization_input(tid, sid, horizon_days=30)
        assert inp_d.lead_time_buckets["OPTL"] == 30


class TestBriefingPeriodAware:
    def test_briefing_uses_active_period_not_daily(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        """QA Bug 2: /hoy morning-briefing must read the session in the active
        period. A weekly session's per-period demand read as daily flags
        everything PEDIR_YA; period-aware, it agrees with /inventory."""
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "brief-weekly")["id"]
        # 10 units/week, 40 stock, lead 14d -> 4 weeks coverage vs 2-week lead
        # -> OK weekly. Read as DAILY it would be 4 "days" vs 14 -> PEDIR_YA.
        _put_stock(client, auth_headers, "BRF", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"BRF": _forecast(10.0, 0.0)})
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: sid)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "weekly", "horizon": 4,
                                       "available_periods": ["daily", "weekly"],
                                       "max_horizon": 26})
        r = client.get("/api/v1/inventory/morning-briefing", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # Weekly-aware: BRF is OK, not a risk -> risks empty of BRF.
        risk_skus = {i["sku"] for i in data["risks"]}
        assert "BRF" not in risk_skus

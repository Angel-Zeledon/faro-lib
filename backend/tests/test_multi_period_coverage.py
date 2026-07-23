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

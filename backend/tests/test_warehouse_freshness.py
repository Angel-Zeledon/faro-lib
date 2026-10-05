"""Per-warehouse freshness: when did each warehouse last report?

The company-wide clocks hide the expensive case: three warehouses current and
one gone quiet. These tests pin what a warehouse's silence is made of (its
newest stock update and its newest sales date), when it is called out, and that
the daily reminder names it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import _json, execute, query_one
from backend.notifications import freshness_service as fs
from tests.test_data_freshness import (
    NOW, _activity, _capture_emails, _completed_session, _only_this_tenant,
)


@pytest.fixture(autouse=True)
def _db_pool(client):
    return client


def _stock(tenant_id, warehouse, age_days, sku=None, now=NOW):
    execute(
        """INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, updated_at)
           VALUES (%s, %s, %s, 10, %s)""",
        (tenant_id, sku or f"{warehouse}-sku", warehouse, now - timedelta(days=age_days)),
    )


def _store_sales(session_id, tenant_id, through: dict):
    """What the runner stores on a finished training: newest sales date per store."""
    execute(
        """INSERT INTO session_results (session_id, tenant_id, training_result)
           VALUES (%s, %s, %s)
           ON CONFLICT (session_id) DO UPDATE SET training_result = EXCLUDED.training_result""",
        (session_id, tenant_id, _json({"store_data_through": through})),
    )


def _by_name(out):
    return {w["name"]: w for w in out["items"]}


class TestPerWarehouse:
    def test_a_quiet_warehouse_is_lagging_while_the_others_are_current(self, test_tenant):
        tid = test_tenant["id"]
        _stock(tid, "Norte", 1)
        _stock(tid, "Sur", 12)
        out = fs.get_warehouse_freshness(tid, NOW)

        assert out["multi"] is True and out["lagging"] == ["Sur"]
        w = _by_name(out)
        assert w["Norte"]["stock_age_days"] == 1 and w["Norte"]["state"] == "fresh"
        assert w["Sur"]["stock_age_days"] == 12 and w["Sur"]["silent_days"] == 12
        assert w["Sur"]["lagging"] is True and w["Norte"]["lagging"] is False

    def test_a_sales_date_counts_as_reporting(self, test_tenant):
        """Stock untouched for 12 days, but its sales reach 2 days ago: the
        warehouse is reporting, just not through the stock screen."""
        tid = test_tenant["id"]
        sid = _completed_session(tid, trained_days_ago=1, data_through_days_ago=1)
        _stock(tid, "Norte", 1)
        _stock(tid, "Sur", 12)
        _store_sales(sid, tid, {
            "sur": (NOW - timedelta(days=2)).date().isoformat(),     # case differs on purpose
            "Norte": (NOW - timedelta(days=1)).date().isoformat(),
        })
        out = fs.get_warehouse_freshness(tid, NOW)
        sur = _by_name(out)["Sur"]
        assert sur["sales_age_days"] == 2 and sur["silent_days"] == 2
        assert sur["lagging"] is False and out["lagging"] == []

    def test_everything_late_is_left_to_the_company_wide_warning(self, test_tenant):
        tid = test_tenant["id"]
        _stock(tid, "Norte", 15)
        _stock(tid, "Sur", 20)
        out = fs.get_warehouse_freshness(tid, NOW)
        assert out["lagging"] == []
        assert all(w["state"] == "stale" for w in out["items"])

    def test_a_single_warehouse_has_no_per_warehouse_story(self, test_tenant):
        tid = test_tenant["id"]
        _stock(tid, "principal", 30)
        out = fs.get_warehouse_freshness(tid, NOW)
        assert out["multi"] is False and out["lagging"] == []

    def test_a_registered_warehouse_that_never_reported_is_unknown_not_fresh(self, test_tenant):
        tid = test_tenant["id"]
        _stock(tid, "Norte", 1)
        execute("INSERT INTO warehouses (tenant_id, name) VALUES (%s, 'Este')", (tid,))
        out = fs.get_warehouse_freshness(tid, NOW)
        este = _by_name(out)["Este"]
        assert este["state"] == "unknown" and este["silent_days"] is None
        assert este["lagging"] is False and out["lagging"] == []

    def test_the_threshold_is_the_existing_stock_window(self, test_tenant):
        tid = test_tenant["id"]
        _stock(tid, "Norte", 0)
        _stock(tid, "Sur", fs.STOCK_STALE_DAYS - 1)
        assert fs.get_warehouse_freshness(tid, NOW)["lagging"] == []
        execute("UPDATE inventory_stock SET updated_at = %s WHERE tenant_id = %s AND warehouse = 'Sur'",
                (NOW - timedelta(days=fs.STOCK_STALE_DAYS), tid))
        assert fs.get_warehouse_freshness(tid, NOW)["lagging"] == ["Sur"]


class TestEndpoint:
    def test_the_company_view_carries_the_warehouses_and_warns(
        self, client, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        real_now = datetime.now(timezone.utc)
        _stock(tid, "Norte", 1, now=real_now)
        _stock(tid, "Sur", 12, now=real_now)
        r = client.get("/api/v1/data-freshness", headers=viewer_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["warehouses"]["lagging"] == ["Sur"]
        assert data["warn"] is True
        assert {w["name"]: w["silent_days"] for w in data["warehouses"]["items"]} == {
            "Norte": 1, "Sur": 12}

    def test_another_tenants_warehouses_are_not_visible(
        self, client, test_tenant, make_tenant_user_headers,
    ):
        _stock(test_tenant["id"], "Mine", 1, now=datetime.now(timezone.utc))
        other = make_tenant_user_headers(role="admin")
        data = client.get("/api/v1/data-freshness", headers=other).json()["data"]
        assert data["warehouses"]["items"] == []


class TestTheReminderNamesThem:
    def test_a_quiet_warehouse_alone_triggers_the_reminder_and_is_named(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=2, data_through_days_ago=2)   # sales fine
        _stock(tid, "Norte", 1)
        _stock(tid, "Sur", 12)          # past the stale window, short of the blind one
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        assert fs.run_daily_freshness_reminders(NOW) == 1

        assert len(sent) == 1
        assert "Sur (12 d)" in sent[0]["html"] and "Norte" not in sent[0]["html"]
        assert "bodegas" in sent[0]["subject"].lower()
        rows = _activity(tid, fs.REMINDER_EMAIL_ACTION)
        assert rows[0]["context"]["silent_warehouses"] == 1

    def test_no_quiet_warehouse_means_no_reminder(self, monkeypatch, registered_user, test_tenant):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=2, data_through_days_ago=2)
        _stock(tid, "Norte", 1)
        _stock(tid, "Sur", 2)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)
        assert fs.run_daily_freshness_reminders(NOW) == 0 and sent == []

    def test_a_name_that_is_tenant_data_is_escaped_in_the_email(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=2, data_through_days_ago=2)
        _stock(tid, "Norte", 1)
        _stock(tid, "<b>Sur</b>", 12)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)
        fs.run_daily_freshness_reminders(NOW)
        assert "&lt;b&gt;Sur&lt;/b&gt; (12 d)" in sent[0]["html"]
        assert "<b>Sur</b>" not in sent[0]["html"]


class TestTheRunnerRecordsStoreDates:
    def test_store_data_through_is_the_newest_row_per_store(self):
        import pandas as pd
        from backend.workers.runner import _store_data_through

        df = pd.DataFrame({
            "d": pd.to_datetime(["2026-01-01", "2026-01-09", "2026-01-05", "2026-01-02"]),
            "sku": ["A", "A", "A", "B"],
            "store": ["Norte", "Norte", "Sur", "Sur"],
        })
        cfg = {"date": "d", "group_keys": ["sku", "store"]}
        assert _store_data_through(df, cfg) == {"Norte": "2026-01-09", "Sur": "2026-01-05"}
        # No store column mapped: nothing to report, and no failure.
        assert _store_data_through(df, {"date": "d", "group_keys": ["sku"]}) == {}
        assert _store_data_through(None, cfg) == {}

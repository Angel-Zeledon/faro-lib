"""Recurring delivery schedules: the Python-owned halves.

The routes and the materialiser are Rust (`backend-rs/src/recurring/`, cases in
`tests/contract/contract_test.py`). Python owns the schema and must itself
honour the feature wherever it still serves a path:

* the reference date rules the Rust port is differentially tested against;
* the table, its CHECK constraints, and the unique index that makes
  materialisation idempotent (shared with blanket contracts);
* reopening a commitment a schedule made, which follows the schedule's status;
* whole-tenant export and erasure cover the table;
* /health reports the loop the Rust service records;
* the two event vocabularies carry the three recurring-delivery events.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

import psycopg2
import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory import recurring_delivery_dates as rdd


def _spec(freq="weekly", **over):
    s = {"frequency": freq, "weekday": 2 if freq in ("weekly", "fortnightly") else None,
         "day_of_month": 15 if freq == "monthly" else None,
         "start_date": date(2026, 10, 1), "end_date": date(2027, 3, 31),
         "holiday_dates": [], "avoid_weekends": False, "shift_rule": "after"}
    s.update(over)
    return s


# ── The reference date rules (pure) ──────────────────────────────────────────

class TestReferenceDates:

    def test_weekly_starts_on_the_first_matching_weekday(self):
        s = _spec("weekly", weekday=3, start_date=date(2026, 10, 6), end_date=date(2026, 11, 5))
        got = [n for n, _ in rdd.occurrences(s, date(2026, 1, 1), date(2027, 1, 1))]
        assert got == [date(2026, 10, 8), date(2026, 10, 15), date(2026, 10, 22),
                       date(2026, 10, 29), date(2026, 11, 5)]

    def test_fortnightly_keeps_its_cadence_whatever_the_window(self):
        s = _spec("fortnightly", weekday=0, start_date=date(2026, 10, 5), end_date=date(2027, 1, 31))
        full = [n for n, _ in rdd.occurrences(s, date(2026, 1, 1), date(2027, 12, 31))]
        late = [n for n, _ in rdd.occurrences(s, date(2026, 11, 10), date(2027, 12, 31))]
        assert full[:3] == [date(2026, 10, 5), date(2026, 10, 19), date(2026, 11, 2)]
        assert late[0] == date(2026, 11, 16) and full[-len(late):] == late

    def test_monthly_clamps_to_the_last_day_leap_year_included(self):
        s = _spec("monthly", day_of_month=31, start_date=date(2027, 1, 1), end_date=date(2027, 5, 31))
        assert [n for n, _ in rdd.occurrences(s, date(2027, 1, 1), date(2027, 12, 31))] == [
            date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)]
        leap = _spec("monthly", day_of_month=30, start_date=date(2028, 2, 1), end_date=date(2028, 2, 29))
        assert [n for n, _ in rdd.occurrences(leap, date(2028, 1, 1), date(2028, 12, 31))] == [date(2028, 2, 29)]

    def test_semimonthly_is_the_fifteenth_and_the_last_day(self):
        s = _spec("semimonthly", start_date=date(2026, 10, 16), end_date=date(2026, 12, 15))
        assert [n for n, _ in rdd.occurrences(s, date(2026, 1, 1), date(2027, 1, 1))] == [
            date(2026, 10, 31), date(2026, 11, 15), date(2026, 11, 30), date(2026, 12, 15)]

    def test_holiday_skip_before_and_after(self):
        base = dict(day_of_month=15, start_date=date(2026, 10, 1), end_date=date(2026, 12, 31),
                    holiday_dates={date(2026, 10, 15), date(2026, 10, 16)})
        after = rdd.occurrences(_spec("monthly", shift_rule="after", **base), date(2026, 10, 1), date(2026, 12, 31))
        assert after[0] == (date(2026, 10, 15), date(2026, 10, 17))
        before = rdd.occurrences(_spec("monthly", shift_rule="before", **base), date(2026, 10, 1), date(2026, 12, 31))
        assert before[0][1] == date(2026, 10, 14)
        skip = rdd.occurrences(_spec("monthly", shift_rule="skip", **base), date(2026, 10, 1), date(2026, 12, 31))
        assert [n for n, _ in skip] == [date(2026, 11, 15), date(2026, 12, 15)]

    def test_weekend_avoidance_moves_to_the_adjacent_working_day(self):
        s = _spec("monthly", day_of_month=15, start_date=date(2026, 11, 1), end_date=date(2026, 11, 30),
                  avoid_weekends=True)                       # 2026-11-15 is a Sunday
        assert rdd.occurrences(s, date(2026, 11, 1), date(2026, 11, 30))[0][1] == date(2026, 11, 16)
        s["shift_rule"] = "before"
        assert rdd.occurrences(s, date(2026, 11, 1), date(2026, 11, 30))[0][1] == date(2026, 11, 13)

    def test_the_window_is_in_delivery_dates(self):
        s = _spec("monthly", day_of_month=15, start_date=date(2026, 11, 1), end_date=date(2026, 11, 30),
                  avoid_weekends=True)
        assert rdd.occurrences(s, date(2026, 11, 1), date(2026, 11, 15)) == []
        assert len(rdd.occurrences(s, date(2026, 11, 16), date(2026, 11, 30))) == 1


# ── The schema ───────────────────────────────────────────────────────────────

def _insert_schedule(tenant_id, user_id, **over):
    row = {
        "id": f"rds_{uuid4().hex[:12]}", "tenant_id": tenant_id, "customer": "ACME", "sku": "SKU-R",
        "quantity": 10, "frequency": "weekly", "weekday": 1,
        "start_date": date.today(), "end_date": date.today() + timedelta(days=90),
        "created_by": user_id,
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    execute(f"INSERT INTO recurring_delivery_schedules ({cols}) VALUES ({marks})", tuple(row.values()))
    return row["id"]


class TestSchema:

    def test_defaults_and_columns(self, registered_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        sid = _insert_schedule(tid, uid)
        row = query_one("SELECT * FROM recurring_delivery_schedules WHERE id = %s", (sid,))
        assert row["status"] == "active" and row["revision"] == 1
        assert row["shift_rule"] == "after" and row["horizon_days"] == 180
        assert row["holiday_dates"] == [] and row["avoid_weekends"] is False
        assert row["on_top_of_base"] is True and row["last_materialised_at"] is None

    @pytest.mark.parametrize("over", [
        {"frequency": "daily"}, {"weekday": 7}, {"day_of_month": 0}, {"day_of_month": 32},
        {"status": "ended"}, {"shift_rule": "never"}, {"quantity": 0}, {"horizon_days": 0},
        {"horizon_days": 731}, {"revision": 0},
        {"end_date": date.today() - timedelta(days=1)},
    ])
    def test_the_table_refuses_nonsense(self, registered_user, over):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_schedule(tid, uid, **over)
        assert query("SELECT id FROM recurring_delivery_schedules WHERE tenant_id = %s", (tid,)) == []

    def test_one_live_commitment_per_schedule_sku_and_nominal_date(self, registered_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        sid = _insert_schedule(tid, uid)
        nominal = date.today() + timedelta(days=10)
        sql = """INSERT INTO committed_demand
                     (tenant_id, sku, delivery_date, quantity, created_by, source,
                      contract_id, contract_root_id, contract_release_date)
                 VALUES (%s, 'SKU-R', %s, 10, %s, 'contract', %s, %s, %s)
                 ON CONFLICT (tenant_id, contract_root_id, sku, contract_release_date)
                     WHERE contract_root_id IS NOT NULL AND contract_withdrawn_at IS NULL
                 DO NOTHING RETURNING id"""
        args = (tid, nominal, uid, sid, sid, nominal)
        assert query_one(sql, args) is not None
        assert query_one(sql, args) is None                    # the rerun adds nothing
        assert len(query("SELECT 1 FROM committed_demand WHERE contract_root_id = %s", (sid,))) == 1
        # A withdrawn row frees its slot: the resumed schedule may make it again.
        execute("UPDATE committed_demand SET contract_withdrawn_at = NOW() WHERE contract_root_id = %s", (sid,))
        assert query_one(sql, args) is not None


# ── Reopening a commitment a schedule made ───────────────────────────────────

def _schedule_row(tid, uid, sid, status="fulfilled"):
    nominal = date.today() + timedelta(days=5)
    return query_one(
        """INSERT INTO committed_demand
               (tenant_id, sku, delivery_date, quantity, created_by, source, status,
                contract_id, contract_root_id, contract_release_date)
           VALUES (%s, 'SKU-R', %s, 10, %s, 'contract', %s, %s, %s, %s) RETURNING id""",
        (tid, nominal, uid, status, sid, sid, nominal))["id"]


class TestReopen:

    def test_reopen_follows_the_schedule_status(self, client, viewer_headers, analyst_headers,
                                                registered_user, analyst_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        sid = _insert_schedule(tid, uid)
        cid = _schedule_row(tid, uid, sid)
        url = f"/api/v1/committed-demand/{cid}/status"
        # Permission pair: a viewer cannot reopen, and the row is unchanged.
        denied = client.post(url, json={"status": "open"}, headers=viewer_headers)
        assert denied.status_code == 403
        assert query_one("SELECT status FROM committed_demand WHERE id = %s", (cid,))["status"] == "fulfilled"
        # Paused schedule: refused with the contract-inactive code, row unchanged.
        execute("UPDATE recurring_delivery_schedules SET status = 'paused' WHERE id = %s", (sid,))
        refused = client.post(url, json={"status": "open"}, headers=analyst_headers)
        assert refused.status_code == 409
        assert refused.json()["error_code"] == "committed_demand_contract_inactive"
        assert query_one("SELECT status FROM committed_demand WHERE id = %s", (cid,))["status"] == "fulfilled"
        # Active schedule: an analyst reopens it.
        execute("UPDATE recurring_delivery_schedules SET status = 'active' WHERE id = %s", (sid,))
        done = client.post(url, json={"status": "open"}, headers=analyst_headers)
        assert done.status_code == 200, done.text
        assert query_one("SELECT status FROM committed_demand WHERE id = %s", (cid,))["status"] == "open"

    def test_a_row_of_no_known_schedule_cannot_be_reopened(self, client, analyst_headers,
                                                           registered_user, analyst_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        cid = _schedule_row(tid, uid, "rds_doesnotexist")
        r = client.post(f"/api/v1/committed-demand/{cid}/status", json={"status": "open"},
                        headers=analyst_headers)
        assert r.status_code == 409
        assert query_one("SELECT status FROM committed_demand WHERE id = %s", (cid,))["status"] == "fulfilled"

    def test_another_tenants_schedule_does_not_count(self, client, analyst_headers, registered_user,
                                                     analyst_user):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
            sid = _insert_schedule(other["id"], uid)
            cid = _schedule_row(tid, uid, sid)
            r = client.post(f"/api/v1/committed-demand/{cid}/status", json={"status": "open"},
                            headers=analyst_headers)
            assert r.status_code == 409
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


# ── Export, erasure, health, vocabularies ────────────────────────────────────

class TestPlumbing:

    def test_the_tenant_export_and_erasure_cover_the_table(self):
        from backend.tenants import data_export
        text = open(data_export.__file__, encoding="utf-8").read()
        assert text.count('"recurring_delivery_schedules"') >= 2     # export spec and erase list

    def test_deleting_the_tenant_removes_its_schedules(self, registered_user):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        sid = _insert_schedule(other["id"], registered_user["user"]["id"])
        execute("DELETE FROM tenants WHERE id = %s", (other["id"],))
        assert query("SELECT 1 FROM recurring_delivery_schedules WHERE id = %s", (sid,)) == []

    def test_health_reports_the_loop(self, client):
        body = client.get("/health").json()
        loops = {entry["loop"]: entry for entry in body["loops"]}
        assert "recurring_deliveries" in loops

    def test_events_and_audit_vocabularies(self):
        from backend.activity.events import EVENTS
        from backend.audit import catalog
        for action in ("created", "revised", "status_changed"):
            name = f"recurring_delivery.{action}"
            assert EVENTS[name].kind == "purchase"
            assert catalog.LEGACY[name] == ("recurring_delivery", name)
        assert "recurring_delivery" in catalog.TARGET_TYPES

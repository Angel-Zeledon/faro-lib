"""The published S&OP consensus reaching the purchase recommendation (Python side).

The consensus routes are Rust (`backend-rs/src/routes/consensus.rs`) and write the
rows these tests insert by hand with the same shapes. What Python owns, and what is
pinned here:

* with nothing published, every number is exactly what it was;
* ONLY an approved version moves a recommendation, through the one place demand
  is decided, and the row names it (who approved, which functions said what);
* a consensus stands in for a manual adjustment on the dates it covers, never on
  the dates it does not (applying both would count one judgement twice; dropping a
  whole longer adjustment would silently lose the rest of it);
* the schema keeps the ledgers honest: submissions are append-only, a version is
  frozen, there is at most one published version per forecast.
"""

import json
from datetime import date, timedelta
from uuid import uuid4

import psycopg2.errors
import pytest

from backend.db import session_store
from backend.db.connection import execute, query_one
from backend.inventory import forecast_adjustment_service as adj_svc
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session


def _flat_session(test_tenant, sku, per_day=10.0, lead_time=20, stock=50.0):
    tid = test_tenant["id"]
    sid = create_session(tid, "usr_test", f"cons-{uuid4().hex[:6]}")["id"]
    inv_svc.upsert_stock(tid, sku, {"current_stock": stock, "lead_time_days": lead_time, "moq": 1.0})
    start = date.today()
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day} for i in range(60)]}}})
    return sid


def _row(test_tenant, sid, sku):
    return {i["sku"]: i for i in inv_svc.get_inventory_status(test_tenant["id"], sid)}[sku]


def _line(sku, first, last, pct_bp, inputs=None):
    today = date.today()
    return {"sku": sku, "start_date": (today + timedelta(days=first)).isoformat(),
            "end_date": (today + timedelta(days=last)).isoformat(), "pct_bp": pct_bp, "source": None,
            "inputs": inputs or [{"function": "sales", "pct_bp": pct_bp,
                                  "submission_id": "s1", "capped": False}]}


def _publish(test_tenant, sid, user_id, lines, status="approved", name="Q4 plan"):
    row = query_one(
        """INSERT INTO consensus_versions
               (tenant_id, session_id, name, rule, lines, line_count, sku_count, status, created_by,
                decided_by, decided_at)
           VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, NOW()) RETURNING id""",
        (test_tenant["id"], sid, name, json.dumps({"rule": "priority"}), json.dumps(lines), len(lines),
         len({ln["sku"] for ln in lines}), status, user_id, user_id))
    return row["id"]


def _manual(test_tenant, sid, sku, first, last, pct, user):
    today = date.today()
    return adj_svc.create(test_tenant["id"], sid, user, sku=sku,
                          start_date=today + timedelta(days=first), end_date=today + timedelta(days=last),
                          mode="percent", value=pct, reason_code="promotion")


class TestWhatPlanningReads:

    def test_nothing_published_changes_nothing(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        before = _row(test_tenant, sid, sku)
        assert before["recommended_qty"] == 150.0 and before["adjustments_applied"] == []
        # Proposals, rejections, withdrawals and replaced versions are records, not inputs.
        uid = registered_user["user"]["id"]
        for status in ("proposed", "rejected", "withdrawn", "superseded"):
            _publish(test_tenant, sid, uid, [_line(sku, 0, 30, 5000)], status=status)
        after = _row(test_tenant, sid, sku)
        assert after["recommended_qty"] == 150.0
        assert after["adjustments_applied"] == []
        assert adj_svc.active_by_sku(test_tenant["id"], sid) == {}

    def test_an_approved_consensus_moves_the_order_and_is_named(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        uid = registered_user["user"]["id"]
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0     # primes the cached status
        inputs = [{"function": "sales", "pct_bp": 6000, "submission_id": "a", "capped": False},
                  {"function": "finance", "pct_bp": 4000, "submission_id": "b", "capped": False}]
        _publish(test_tenant, sid, uid, [_line(sku, 0, 30, 5000, inputs)], name="Q4 plan")
        row = _row(test_tenant, sid, sku)
        assert row["recommended_qty"] == pytest.approx(250.0)               # 10*1.5*20 - 50
        applied = row["adjustments_applied"]
        assert len(applied) == 1
        a = applied[0]
        assert a["reason_code"] == "consensus" and a["mode"] == "consensus"
        assert a["pct"] == 50.0
        assert a["created_by"] == uid and a["created_by_name"] == "Test Admin"   # the approver
        assert "Q4 plan" in a["reason_note"] and "sales +60.00%" in a["reason_note"]
        assert "finance +40.00%" in a["reason_note"]
        assert row["daily_demand"] == 10.0, "the model's own number stays visible"

    def test_withdrawing_it_hands_planning_back(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        uid = registered_user["user"]["id"]
        vid = _publish(test_tenant, sid, uid, [_line(sku, 0, 30, 5000)])
        assert _row(test_tenant, sid, sku)["recommended_qty"] == pytest.approx(250.0)
        execute("UPDATE consensus_versions SET status = 'withdrawn' WHERE id = %s", (vid,))
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0
        assert _row(test_tenant, sid, sku)["adjustments_applied"] == []

    def test_a_consensus_of_another_forecast_does_not_leak(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        other = _flat_session(test_tenant, sku)
        _publish(test_tenant, other, registered_user["user"]["id"], [_line(sku, 0, 30, 5000)])
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0

    def test_a_line_that_ended_is_ignored(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        _publish(test_tenant, sid, registered_user["user"]["id"], [_line(sku, -30, -5, 5000)])
        assert adj_svc.active_by_sku(test_tenant["id"], sid) == {}
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0

    def test_an_unreadable_line_is_skipped_and_never_breaks_the_recommendation(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        _publish(test_tenant, sid, registered_user["user"]["id"],
                 [{"sku": sku, "start_date": "not-a-date", "end_date": "2026-01-01", "pct_bp": "x"},
                  _line(sku, 0, 30, 5000)])
        assert _row(test_tenant, sid, sku)["recommended_qty"] == pytest.approx(250.0)


class TestStandingInForManualAdjustments:

    def test_the_consensus_replaces_a_manual_adjustment_on_the_dates_it_covers(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        uid = registered_user["user"]["id"]
        manual = _manual(test_tenant, sid, sku, 0, 30, 100, uid)               # +100%
        assert _row(test_tenant, sid, sku)["recommended_qty"] == pytest.approx(350.0)
        _publish(test_tenant, sid, uid, [_line(sku, 0, 30, 5000)])              # the agreed number: +50%
        row = _row(test_tenant, sid, sku)
        assert row["recommended_qty"] == pytest.approx(250.0), "one judgement, counted once"
        assert [a["reason_code"] for a in row["adjustments_applied"]] == ["consensus"]
        assert row["adjustments_applied"][0]["replaces"] == [manual["id"]]

    def test_a_longer_manual_adjustment_keeps_applying_where_the_consensus_is_silent(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        uid = registered_user["user"]["id"]
        _manual(test_tenant, sid, sku, 0, 29, 100, uid)                         # +100% for 30 days
        _publish(test_tenant, sid, uid, [_line(sku, 0, 9, 5000)])               # consensus: days 0-9 only
        row = _row(test_tenant, sid, sku)
        # lead-time window = days 0-19. Consensus covers 10 of them (+50%): x1.25.
        # The manual adjustment keeps days 10-29, of which 10 are in the window (+100%): x1.5.
        assert row["recommended_qty"] == pytest.approx(10 * 1.25 * 1.5 * 20 - 50)
        assert sorted(a["reason_code"] for a in row["adjustments_applied"]) == ["consensus", "promotion"]

    def test_an_agreed_no_change_beats_a_manual_adjustment_on_its_dates(self, test_tenant, registered_user):
        sku = f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        uid = registered_user["user"]["id"]
        _manual(test_tenant, sid, sku, 0, 30, 100, uid)
        _publish(test_tenant, sid, uid, [_line(sku, 0, 30, 0)])                 # the functions agreed: no change
        row = _row(test_tenant, sid, sku)
        assert row["recommended_qty"] == 150.0
        assert row["adjustments_applied"] == []

    def test_another_product_keeps_its_manual_adjustment(self, test_tenant, registered_user):
        a, b = f"CNS-{uuid4().hex[:6]}", f"CNS-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, a)
        inv_svc.upsert_stock(test_tenant["id"], b, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        series = {"lightgbm": {"forecast": [{"date": (date.today() + timedelta(days=i)).isoformat(), "value": 10.0}
                                            for i in range(60)]}}
        session_store.set_forecasts(test_tenant["id"], sid, {
            a: series, b: series})
        uid = registered_user["user"]["id"]
        _manual(test_tenant, sid, b, 0, 30, 100, uid)
        _publish(test_tenant, sid, uid, [_line(a, 0, 30, 5000)])
        assert _row(test_tenant, sid, b)["recommended_qty"] == pytest.approx(350.0)
        assert _row(test_tenant, sid, a)["recommended_qty"] == pytest.approx(250.0)


class TestTheSchemaKeepsTheLedgersHonest:

    def _submission(self, test_tenant, user, sid, **over):
        row = {"sku": "S", "function": "sales", "start": date.today(), "end": date.today() + timedelta(days=5),
               "pct_bp": 1000, "reason": "promotion", **over}
        return query_one(
            """INSERT INTO consensus_submissions
                   (tenant_id, session_id, sku, function, start_date, end_date, pct_bp, reason_code, revision,
                    created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s) RETURNING id""",
            (test_tenant["id"], sid, row["sku"], row["function"], row["start"], row["end"], row["pct_bp"],
             row["reason"], user))["id"]

    def test_a_submission_can_only_be_marked_superseded(self, test_tenant, registered_user):
        uid = registered_user["user"]["id"]
        first = self._submission(test_tenant, uid, "s1")
        second = self._submission(test_tenant, uid, "s1", pct_bp=1500)
        with pytest.raises(Exception, match="append-only"):
            execute("UPDATE consensus_submissions SET pct_bp = 0 WHERE id = %s", (first,))
        with pytest.raises(Exception, match="append-only"):
            execute("UPDATE consensus_submissions SET created_by = 'x', superseded_by = %s WHERE id = %s",
                    (second, first))
        execute("UPDATE consensus_submissions SET superseded_by = %s, superseded_at = NOW() WHERE id = %s",
                (second, first))
        with pytest.raises(Exception, match="append-only"):          # and only once
            execute("UPDATE consensus_submissions SET superseded_by = 'other' WHERE id = %s", (first,))
        row = query_one("SELECT pct_bp, superseded_by FROM consensus_submissions WHERE id = %s", (first,))
        assert row == {"pct_bp": 1000, "superseded_by": second}

    def test_a_submission_outside_the_possible_range_is_refused(self, test_tenant, registered_user):
        uid = registered_user["user"]["id"]
        for bad in (-10001, 100001):
            with pytest.raises(Exception, match="check"):
                self._submission(test_tenant, uid, "s1", pct_bp=bad)

    def test_a_frozen_version_only_changes_status(self, test_tenant, registered_user):
        uid = registered_user["user"]["id"]
        vid = _publish(test_tenant, "s1", uid, [_line("S", 0, 5, 1000)], status="proposed")
        with pytest.raises(Exception, match="frozen"):
            execute("UPDATE consensus_versions SET lines = '[]'::jsonb WHERE id = %s", (vid,))
        with pytest.raises(Exception, match="frozen"):
            execute("UPDATE consensus_versions SET name = 'renamed' WHERE id = %s", (vid,))
        execute("UPDATE consensus_versions SET status = 'rejected', decided_by = %s WHERE id = %s", (uid, vid))
        assert query_one("SELECT status FROM consensus_versions WHERE id = %s", (vid,))["status"] == "rejected"

    def test_only_one_version_per_forecast_can_be_published(self, test_tenant, registered_user):
        uid = registered_user["user"]["id"]
        _publish(test_tenant, "s1", uid, [_line("S", 0, 5, 1000)])
        with pytest.raises(psycopg2.errors.UniqueViolation):
            _publish(test_tenant, "s1", uid, [_line("S", 0, 5, 2000)])
        _publish(test_tenant, "s2", uid, [_line("S", 0, 5, 2000)])    # another forecast is fine

    def test_version_events_are_immutable(self, test_tenant, registered_user):
        uid = registered_user["user"]["id"]
        vid = _publish(test_tenant, "s1", uid, [_line("S", 0, 5, 1000)], status="proposed")
        execute("INSERT INTO consensus_version_events (tenant_id, version_id, to_status, actor_id) "
                "VALUES (%s, %s, 'proposed', %s)", (test_tenant["id"], vid, uid))
        with pytest.raises(Exception, match="immutable"):
            execute("UPDATE consensus_version_events SET comment = 'x' WHERE version_id = %s", (vid,))

    def test_whole_tenant_erasure_covers_every_consensus_table_children_first(self):
        from backend.tenants import data_export
        order = data_export._DELETE_ORDER
        tables = ("consensus_version_events", "consensus_versions", "consensus_submissions",
                  "consensus_members", "consensus_settings", "consensus_evidence")
        for table in tables:
            assert table in order, table
        assert order.index("consensus_version_events") < order.index("consensus_versions")
        exported = {stem for stem, _t, _c in data_export._EXPORT_SPECS}
        assert set(tables) <= exported, "the export must carry the consensus ledgers too"

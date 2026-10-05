"""Spike exclusions: the ledger, its audit trail and its application by the runner.

Pins: a mark is stored append-only beside the data (who/when/why) and audited;
undo stamps the row instead of deleting it; viewers cannot write; another
tenant cannot see or undo a mark; and the runner applies the active marks to the
engine's copy of the data, records per mark what it did, and says so in the
run's findings, while with no marks the data is exactly what it was.
"""

import pandas as pd
import pytest

from backend.db.connection import query, query_one
from backend.inventory import spike_edit_service as svc
from backend.sessions.service import get_session
from backend.workers import runner


def _dataset_id(tid, sid):
    return get_session(tid, sid)["dataset_id"]


def _body(**kw):
    base = {"sku": "SKU-A", "start_date": "2025-03-01", "end_date": "2025-03-05",
            "reason_code": "one_off_order", "reason_note": "Municipal tender"}
    base.update(kw)
    return base


class TestSpikeEditApi:

    def test_permission_pair_and_the_stored_row(self, client, viewer_headers, analyst_headers,
                                                analyst_user, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        url = f"/api/v1/sessions/{sid}/spike-edits"
        assert client.post(url, json=_body(), headers=viewer_headers).status_code == 403
        assert query("SELECT 1 FROM spike_edits WHERE tenant_id = %s", (tid,)) == []

        ok = client.post(url, json=_body(), headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        row = query_one("SELECT * FROM spike_edits WHERE tenant_id = %s", (tid,))
        assert (row["sku"], row["reason_code"], row["reason_note"]) == (
            "SKU-A", "one_off_order", "Municipal tender")
        assert str(row["start_date"]) == "2025-03-01" and str(row["end_date"]) == "2025-03-05"
        assert row["dataset_id"] == _dataset_id(tid, sid)
        assert row["created_by"] == analyst_user["user"]["id"] and row["reverted_at"] is None
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'forecast.spike_excluded' AND resource = %s", (tid, row["id"]))

        listed = client.get(url, headers=viewer_headers).json()["data"]
        assert [i["id"] for i in listed["items"]] == [row["id"]]
        assert listed["items"][0]["created_by_name"] and "one_off_order" in listed["reasons"]
        assert listed["items"][0]["applied"] is None      # no run has used it yet

    def test_undo_permission_pair_keeps_the_row_and_audits(
            self, client, viewer_headers, analyst_headers, analyst_user, completed_session,
            registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        url = f"/api/v1/sessions/{sid}/spike-edits"
        created = client.post(url, json=_body(), headers=analyst_headers).json()["data"]
        revert = f"/api/v1/spike-edits/{created['id']}/revert"

        assert client.post(revert, headers=viewer_headers).status_code == 403
        assert query_one("SELECT reverted_at FROM spike_edits WHERE id = %s",
                         (created["id"],))["reverted_at"] is None

        assert client.post(revert, headers=analyst_headers).status_code == 200
        row = query_one("SELECT * FROM spike_edits WHERE id = %s", (created["id"],))
        assert row is not None and row["reverted_at"] is not None
        assert row["reverted_by"] == analyst_user["user"]["id"]
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'forecast.spike_restored' AND resource = %s", (tid, created["id"]))
        # Not listed any more, but still on file; and undoing twice is refused.
        assert client.get(url, headers=viewer_headers).json()["data"]["items"] == []
        assert len(client.get(url + "?include_reverted=true",
                              headers=viewer_headers).json()["data"]["items"]) == 1
        again = client.post(revert, headers=analyst_headers)
        assert again.status_code == 409
        # ...and the period can be marked again as a NEW row.
        assert client.post(url, json=_body(), headers=analyst_headers).status_code == 201
        assert query_one("SELECT COUNT(*) AS n FROM spike_edits WHERE tenant_id = %s",
                         (tid,))["n"] == 2

    def test_refusals_change_nothing(self, client, analyst_headers, completed_session,
                                     registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        url = f"/api/v1/sessions/{sid}/spike-edits"
        for bad in (
            _body(reason_code="because"),
            _body(reason_code="other", reason_note=""),
            _body(start_date="2025-03-09", end_date="2025-03-01"),
            _body(start_date="2020-01-01", end_date="2025-03-01"),   # longer than a year
            _body(start_date="2999-01-01", end_date="2999-01-02"),   # not history yet
            _body(start_date="not-a-date"),
            _body(sku="   "),
        ):
            assert client.post(url, json=bad, headers=analyst_headers).status_code in (400, 422), bad
        assert query("SELECT 1 FROM spike_edits WHERE tenant_id = %s", (tid,)) == []

    def test_an_overlapping_mark_is_refused_until_the_first_is_undone(
            self, client, analyst_headers, completed_session):
        url = f"/api/v1/sessions/{completed_session['id']}/spike-edits"
        assert client.post(url, json=_body(), headers=analyst_headers).status_code == 201
        clash = client.post(url, json=_body(start_date="2025-03-05", end_date="2025-03-09"),
                            headers=analyst_headers)
        assert clash.status_code == 409
        # Another product is independent.
        assert client.post(url, json=_body(sku="SKU-B"), headers=analyst_headers).status_code == 201

    def test_another_tenant_cannot_see_or_undo_a_mark(
            self, client, analyst_headers, completed_session, make_tenant_user_headers):
        url = f"/api/v1/sessions/{completed_session['id']}/spike-edits"
        created = client.post(url, json=_body(), headers=analyst_headers).json()["data"]
        other = make_tenant_user_headers(role="admin")
        assert client.get(url, headers=other).status_code == 404
        assert client.post(f"/api/v1/spike-edits/{created['id']}/revert",
                           headers=other).status_code == 404
        assert query_one("SELECT reverted_at FROM spike_edits WHERE id = %s",
                         (created["id"],))["reverted_at"] is None


class TestRunnerApplication:

    def _df(self):
        return pd.DataFrame({
            "date": pd.date_range("2025-03-01", periods=9, freq="D"),
            "sku": "SKU-A",
            "qty": [10.0, 10.0, 10.0, 10.0, 900.0, 10.0, 10.0, 10.0, 10.0],
        })

    def test_no_marks_returns_the_same_frame_and_writes_nothing(
            self, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        df, notes = self._df(), []
        out = runner._apply_spike_edits(df, "date", "qty", ["sku"], tid, sid,
                                        _dataset_id(tid, sid), notes)
        assert out is df and notes == []
        assert query("SELECT 1 FROM spike_edit_applications WHERE tenant_id = %s", (tid,)) == []

    def test_an_active_mark_is_applied_recorded_and_reported(
            self, analyst_headers, client, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        mark = client.post(f"/api/v1/sessions/{sid}/spike-edits", headers=analyst_headers,
                           json=_body(start_date="2025-03-05", end_date="2025-03-05")).json()["data"]
        undone = client.post(f"/api/v1/sessions/{sid}/spike-edits", headers=analyst_headers,
                             json=_body(sku="SKU-Z", start_date="2025-03-05",
                                        end_date="2025-03-05")).json()["data"]
        client.post(f"/api/v1/spike-edits/{undone['id']}/revert", headers=analyst_headers)

        df, notes = self._df(), []
        original = df.copy(deep=True)
        out = runner._apply_spike_edits(df, "date", "qty", ["sku"], tid, sid,
                                        _dataset_id(tid, sid), notes)
        assert out["qty"].tolist() == [10.0] * 9
        pd.testing.assert_frame_equal(df, original)          # the input is untouched

        rows = query("SELECT * FROM spike_edit_applications WHERE tenant_id = %s", (tid,))
        assert len(rows) == 1                                  # the undone mark is ignored
        assert (rows[0]["spike_edit_id"], rows[0]["status"], rows[0]["points_treated"]) == (
            mark["id"], "applied", 1)
        assert rows[0]["original_total"] == 900.0 and rows[0]["replacement_total"] == 10.0
        assert [(n["error_id"], n["severity"], n["context"]["n_points"]) for n in notes] == [
            ("PREP_SPIKE_EDITS_APPLIED", "info", 1)]

        # The session's own list now says what the run did with the mark.
        listed = client.get(f"/api/v1/sessions/{sid}/spike-edits",
                            headers=analyst_headers).json()["data"]["items"]
        assert listed[0]["applied"]["points_treated"] == 1

    def test_a_mark_with_no_data_is_reported_not_silent(
            self, analyst_headers, client, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        client.post(f"/api/v1/sessions/{sid}/spike-edits", headers=analyst_headers,
                    json=_body(start_date="2024-01-01", end_date="2024-01-03"))
        df, notes = self._df(), []
        out = runner._apply_spike_edits(df, "date", "qty", ["sku"], tid, sid,
                                        _dataset_id(tid, sid), notes)
        assert out["qty"].tolist() == df["qty"].tolist()
        assert [n["error_id"] for n in notes] == ["PREP_SPIKE_EDITS_UNMATCHED"]
        assert query_one("SELECT points_treated, status FROM spike_edit_applications "
                         "WHERE tenant_id = %s", (tid,)) == {
            "points_treated": 0, "status": "no_match"}

    def test_a_failure_is_noted_and_the_data_is_left_alone(
            self, monkeypatch, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]

        def boom(*a, **k):
            raise RuntimeError("ledger unreadable")
        monkeypatch.setattr(svc, "active_for_dataset", boom)
        df, notes = self._df(), []
        out = runner._apply_spike_edits(df, "date", "qty", ["sku"], tid, sid,
                                        _dataset_id(tid, sid), notes)
        assert out is df
        assert [n["error_id"] for n in notes] == ["PREP_SPIKE_EDITS_FAILED"]

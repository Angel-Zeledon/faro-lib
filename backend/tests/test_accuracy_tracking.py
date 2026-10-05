"""Automatic realised-accuracy tracking.

When a sales file lands, the live forecast is graded against it, the LATEST
reading is stored per session, and ONE bell alert is raised when the forecast
has degraded past the threshold. Notification only: nothing retrains.

Every assertion reads `session_accuracy_tracking` / `activity_logs` directly.
"""

import csv
import io
from datetime import date, timedelta

import pytest

from backend.config import settings
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.forecast_check import tracking
from backend.forecast_check.service import _champion_forecasts

URL = "/api/v1/sessions/{sid}/accuracy-tracking"
ALERT = "forecast.accuracy_degraded"


def _upload(client, headers, rows, name="later.csv"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    w.writerows(rows)
    r = client.post("/api/v1/datasets",
                    files={"file": (name, buf.getvalue().encode(), "text/csv")},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _rows(tid, sid, factor, first_n=None):
    rows = []
    for sku, series in _champion_forecasts(tid, sid).items():
        dates = sorted(series)[:first_n] if first_n else sorted(series)
        rows += [(d, sku, round(series[d] * factor, 4)) for d in dates]
    return rows


@pytest.fixture
def tracked(completed_session, registered_user):
    """A completed session whose training accuracy is exactly 10% WAPE for the
    model every SKU is bought from, so the baseline is a known number."""
    tid, sid = registered_user["tenant"]["id"], completed_session["id"]
    result = session_store.get_training_result(tid, sid)
    skus = sorted(_champion_forecasts(tid, sid))
    result["metrics"]["rows"] = [
        {"sku": s, "model": "lightgbm", "type": "ml", "mae": 2.0, "wape": 0.10}
        for s in skus
    ]
    session_store.set_training_result(tid, sid, result)
    return tid, sid


def _reading(sid):
    return query_one("SELECT * FROM session_accuracy_tracking WHERE session_id = %s", (sid,))


def _alerts(tid, sid):
    return query(
        "SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s "
        "AND resource = %s", (tid, ALERT, sid))


def _dataset(tid, ds_id):
    from backend.datasets.service import get_dataset
    return get_dataset(tid, ds_id)


class TestReading:
    def test_a_late_file_is_graded_against_the_training_baseline(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        rows = _rows(tid, sid, 1.5)
        ds = _upload(client, auth_headers, rows)
        out = tracking.track_dataset(tid, ds["id"])
        assert [r["session_id"] for r in out] == [sid]

        row = _reading(sid)
        assert row["tenant_id"] == tid and row["dataset_id"] == ds["id"]
        assert row["status"] == "degraded"
        assert row["baseline_wape"] == pytest.approx(0.10)
        assert row["realised_wape"] == pytest.approx(1 / 3, abs=1e-3)   # |f-1.5f| / 1.5f
        assert row["degradation_pct"] == pytest.approx((1 / 3 - 0.10) / 0.10 * 100, abs=0.2)
        assert row["n_points"] == len(rows) and row["n_skus"] == 3
        assert str(row["compared_from"]) == min(r[0] for r in rows)
        assert str(row["compared_to"]) == max(r[0] for r in rows)

    def test_a_forecast_that_holds_up_is_stable_and_silent(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.05))
        tracking.track_dataset(tid, ds["id"])
        row = _reading(sid)
        assert row["status"] == "stable"
        assert row["realised_wape"] < row["baseline_wape"]
        assert _alerts(tid, sid) == [] and row["alerted_at"] is None

    def test_too_few_points_is_not_judged_and_does_not_alert(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        ds = _upload(client, auth_headers, _rows(tid, sid, 3.0, first_n=3))   # 9 points
        tracking.track_dataset(tid, ds["id"])
        assert _reading(sid)["status"] == "too_little"
        assert _alerts(tid, sid) == []

    def test_a_file_that_does_not_reach_the_forecast_stores_nothing(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        ds = _upload(client, auth_headers,
                     [((date(2031, 1, 1) + timedelta(days=i)).isoformat(), "SKU_001", 5)
                      for i in range(20)])
        assert tracking.track_dataset(tid, ds["id"]) == []
        assert _reading(sid) is None

    def test_an_older_file_does_not_overwrite_a_reading_of_newer_sales(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        full = _upload(client, auth_headers, _rows(tid, sid, 1.5), "full.csv")
        tracking.track_dataset(tid, full["id"])
        before = _reading(sid)
        early = _upload(client, auth_headers, _rows(tid, sid, 1.0, first_n=5), "early.csv")
        assert tracking.track_dataset(tid, early["id"]) == []
        after = _reading(sid)
        assert after["dataset_id"] == full["id"]
        assert after["realised_wape"] == before["realised_wape"]

    def test_the_threshold_comes_from_settings(
        self, client, auth_headers, tracked, monkeypatch,
    ):
        tid, sid = tracked
        monkeypatch.setattr(settings, "accuracy_degradation_threshold_pct", 1000.0)
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        row = _reading(sid)
        assert row["status"] == "stable" and row["threshold_pct"] == 1000.0
        assert _alerts(tid, sid) == []

    def test_work_per_upload_is_bounded(
        self, client, auth_headers, tracked, monkeypatch,
    ):
        tid, sid = tracked
        owner = query_one("SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (tid,))
        for i in range(8):   # a tenant with many completed sessions
            execute(
                "INSERT INTO sessions (id, tenant_id, name, status, created_by) "
                "VALUES (%s, %s, %s, 'COMPLETED', %s)",
                (f"sess-bulk-{i}", tid, f"bulk {i}", owner["id"]))
        graded = []
        monkeypatch.setattr(tracking, "track_session",
                            lambda t, s, d: graded.append(s["id"]))
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        assert len(graded) == tracking.MAX_SESSIONS_PER_UPLOAD == 5


class TestTheOneAlert:
    def test_degradation_raises_exactly_one_bell_alert_however_often_it_is_rerun(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        for _ in range(3):
            tracking.track_dataset(tid, ds["id"])

        alerts = _alerts(tid, sid)
        assert len(alerts) == 1
        ctx = alerts[0]["context"]
        assert ctx["severity"] == "warning" and ctx["kind"] == "training"
        assert ctx["reason"] == "realised_accuracy_below_training"
        assert ctx["reason_params"] == {"baseline": 10, "realised": 33}
        assert ctx["degradation_pct"] == round(_reading(sid)["degradation_pct"])
        assert _reading(sid)["alerted_at"] is not None

        # ... and it is on the bell the user reads.
        from backend.notifications.alert_history import list_alerts
        user = query_one("SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (tid,))
        items = list_alerts(tid, user["id"])["items"]
        entry = next(i for i in items if i["action"] == ALERT)
        assert entry["severity"] == "warning" and entry["source"] == "system"

    def test_recovery_re_arms_the_alert_for_a_later_relapse(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        bad = _upload(client, auth_headers, _rows(tid, sid, 1.5), "bad.csv")
        tracking.track_dataset(tid, bad["id"])
        good = _upload(client, auth_headers, _rows(tid, sid, 1.05), "good.csv")
        tracking.track_dataset(tid, good["id"])
        assert _reading(sid)["status"] == "stable" and _reading(sid)["alerted_at"] is None
        worse = _upload(client, auth_headers, _rows(tid, sid, 2.0), "worse.csv")
        tracking.track_dataset(tid, worse["id"])
        assert len(_alerts(tid, sid)) == 2

    def test_a_second_degraded_upload_while_still_degraded_does_not_alert_again(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        bad = _upload(client, auth_headers, _rows(tid, sid, 1.5), "bad.csv")
        tracking.track_dataset(tid, bad["id"])
        again = _upload(client, auth_headers, _rows(tid, sid, 1.6), "bad-again.csv")
        tracking.track_dataset(tid, again["id"])
        assert _reading(sid)["dataset_id"] == again["id"]      # the reading moved on
        assert len(_alerts(tid, sid)) == 1                     # the episode did not

    def test_nothing_is_retrained_or_replaced(self, client, auth_headers, tracked):
        tid, sid = tracked
        before = query_one("SELECT status, archived_at FROM sessions WHERE id = %s", (sid,))
        jobs_before = query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s", (tid,))["n"]
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        after = query_one("SELECT status, archived_at FROM sessions WHERE id = %s", (sid,))
        assert after == before
        assert query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s",
                         (tid,))["n"] == jobs_before


class TestScope:
    def test_another_tenants_session_is_never_graded(
        self, client, auth_headers, tracked,
    ):
        tid, sid = tracked
        from backend.tenants.service import create_tenant
        other = create_tenant("pytest-other-tracking")
        try:
            ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
            # The other tenant cannot even resolve this tenant's dataset.
            assert tracking.track_dataset(other["id"], ds["id"]) == []
            assert query_one(
                "SELECT COUNT(*) AS n FROM session_accuracy_tracking WHERE tenant_id = %s",
                (other["id"],))["n"] == 0
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_erasing_a_tenant_removes_its_readings(self, client, auth_headers, tracked):
        tid, sid = tracked
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        assert _reading(sid) is not None
        # The tenant fixture's teardown deletes the tenant row; do it here and
        # check that nothing of the reading survives it.
        execute("DELETE FROM tenants WHERE id = %s", (tid,))
        assert _reading(sid) is None


class TestUploadHook:
    def test_uploading_a_sales_file_triggers_the_reading(
        self, client, auth_headers, tracked, monkeypatch,
    ):
        tid, sid = tracked
        monkeypatch.setattr(tracking, "_spawn", lambda fn: fn())   # run inline
        _upload(client, auth_headers, _rows(tid, sid, 1.5))
        assert _reading(sid)["status"] == "degraded"
        assert len(_alerts(tid, sid)) == 1

    def test_a_failing_reading_never_fails_the_upload(
        self, client, auth_headers, tracked, monkeypatch,
    ):
        tid, sid = tracked
        monkeypatch.setattr(tracking, "_spawn", lambda fn: fn())
        monkeypatch.setattr(tracking, "track_dataset",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))     # still 201
        assert query_one("SELECT id FROM datasets WHERE id = %s", (ds["id"],)) is not None


class TestEndpoint:
    def test_it_returns_the_stored_row_to_every_role(
        self, client, auth_headers, viewer_headers, tracked,
    ):
        tid, sid = tracked
        assert client.get(URL.format(sid=sid), headers=viewer_headers).json()["data"] is None
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        r = client.get(URL.format(sid=sid), headers=viewer_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        row = _reading(sid)
        assert d["status"] == row["status"] == "degraded"
        assert d["realised_wape"] == pytest.approx(row["realised_wape"])
        assert d["baseline_wape"] == pytest.approx(0.10)
        assert d["alerted_at"] is not None

    def test_another_tenants_session_is_not_found(
        self, client, auth_headers, tracked, make_tenant_user_headers,
    ):
        tid, sid = tracked
        ds = _upload(client, auth_headers, _rows(tid, sid, 1.5))
        tracking.track_dataset(tid, ds["id"])
        stranger = make_tenant_user_headers(role="admin")
        r = client.get(URL.format(sid=sid), headers=stranger)
        assert r.status_code == 404
        assert r.json().get("data") is None

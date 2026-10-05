"""Sessions are permanent.

A real tenant's session — its configuration, results, forecasts, artifacts and
the dataset it trained on — is never erased by the product on its own and never
by a user action that cannot be undone. What a user calls "delete" is an
ARCHIVE (kept, hidden from the working list, restorable, audited); a plan
ceiling only ever refuses to CREATE; a dataset a session reads cannot be
deleted; and the one erasing loop (the trial reaper) can only reach demo
tenants.
"""
import csv
import io
import re
from pathlib import Path
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one

BACKEND = Path(__file__).resolve().parents[1]


def _make_session(client, headers, name=None):
    r = client.post("/api/v1/sessions", headers=headers,
                    json={"name": name or f"perm-{uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _csv(rows, header=("date", "sku", "sales")):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


# ── 1. "Delete" archives: nothing is erased ──────────────────────────────────

class TestArchiveInsteadOfDelete:

    def test_delete_keeps_results_forecasts_and_configs(
        self, client, auth_headers, completed_session, registered_user,
    ):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        forecasts_before = session_store.get_forecasts(tid, sid)
        assert forecasts_before

        r = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert r.status_code == 204

        row = query_one("SELECT archived_at, archived_by FROM sessions WHERE id = %s", (sid,))
        assert row["archived_at"] is not None
        assert row["archived_by"] == registered_user["user"]["id"]
        assert session_store.get_forecasts(tid, sid) == forecasts_before
        assert session_store.get_training_result(tid, sid) is not None
        assert query_one("SELECT 1 AS x FROM session_configs WHERE session_id = %s", (sid,))
        assert query_one("SELECT 1 AS x FROM session_results WHERE session_id = %s", (sid,))

    def test_archive_is_audited_and_restore_brings_it_back(
        self, client, auth_headers, completed_session, registered_user,
    ):
        sid = completed_session["id"]
        client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        logged = query_one(
            "SELECT context FROM activity_logs WHERE tenant_id = %s AND action = 'session.archive' "
            "AND resource = %s", (registered_user["tenant"]["id"], sid))
        assert logged and logged["context"]["name"]

        r = client.post(f"/api/v1/sessions/{sid}/restore", headers=auth_headers)
        assert r.status_code == 200 and r.json()["data"]["archived_at"] is None
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"] is None
        assert query_one(
            "SELECT 1 AS x FROM activity_logs WHERE action = 'session.restore' AND resource = %s",
            (sid,))

    def test_viewer_cannot_archive_or_restore(
        self, client, auth_headers, viewer_headers, completed_session,
    ):
        sid = completed_session["id"]
        assert client.delete(f"/api/v1/sessions/{sid}", headers=viewer_headers).status_code == 403
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"] is None
        client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert client.post(f"/api/v1/sessions/{sid}/restore", headers=viewer_headers).status_code == 403
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"] is not None

    def test_a_session_with_a_job_in_flight_cannot_be_archived(
        self, client, auth_headers, test_session, registered_user,
    ):
        from backend.training.job_service import create_job
        sid = test_session["id"]
        create_job(registered_user["tenant"]["id"], sid, registered_user["user"]["id"])
        r = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert r.status_code == 409
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"] is None

    def test_archiving_twice_is_harmless_and_logs_once(self, client, auth_headers, test_session, registered_user):
        sid = test_session["id"]
        assert client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers).status_code == 204
        first = query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"]
        assert client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers).status_code == 204
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s", (sid,))["archived_at"] == first
        n = query_one("SELECT COUNT(*) AS n FROM activity_logs WHERE action = 'session.archive' "
                      "AND resource = %s", (sid,))["n"]
        assert n == 1

    def test_foreign_tenants_session_cannot_be_archived(self, client, test_session, make_tenant_user_headers):
        other = make_tenant_user_headers(role="admin")
        assert client.delete(f"/api/v1/sessions/{test_session['id']}", headers=other).status_code == 404
        assert query_one("SELECT archived_at FROM sessions WHERE id = %s",
                         (test_session["id"],))["archived_at"] is None


# ── 2. The plan ceiling blocks creating, and deletes nothing ─────────────────

class TestCeilingNeverDeletes:

    @pytest.fixture
    def free_tenant(self, monkeypatch, registered_user):
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)
        execute("UPDATE tenants SET tier = 'free', quota = '{}' WHERE id = %s",
                (registered_user["tenant"]["id"],))
        return registered_user["tenant"]["id"]

    def _ids(self, tid):
        return {r["id"] for r in query("SELECT id FROM sessions WHERE tenant_id = %s", (tid,))}

    def test_hitting_the_ceiling_refuses_the_fourth_and_keeps_the_three(
        self, client, auth_headers, free_tenant,
    ):
        made = {_make_session(client, auth_headers) for _ in range(3)}
        r = client.post("/api/v1/sessions", headers=auth_headers, json={"name": "fourth"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
        assert r.json()["detail"]["limit"] == "max_sessions"
        assert self._ids(free_tenant) == made
        assert all(query_one("SELECT archived_at FROM sessions WHERE id = %s", (s,))["archived_at"] is None
                   for s in made)

    def test_archiving_frees_a_slot_and_restoring_at_the_ceiling_is_refused(
        self, client, auth_headers, free_tenant,
    ):
        made = [_make_session(client, auth_headers) for _ in range(3)]
        client.delete(f"/api/v1/sessions/{made[0]}", headers=auth_headers)
        fourth = _make_session(client, auth_headers)           # the slot was freed
        r = client.post(f"/api/v1/sessions/{made[0]}/restore", headers=auth_headers)
        assert r.status_code == 403                             # full again: refused, not forced
        row = query_one("SELECT archived_at FROM sessions WHERE id = %s", (made[0],))
        assert row["archived_at"] is not None                   # still archived, still intact
        assert self._ids(free_tenant) == {*made, fourth}        # nothing was deleted anywhere

    def test_a_backtest_at_the_ceiling_is_refused_without_deleting(
        self, client, auth_headers, free_tenant, completed_session,
    ):
        # completed_session + 2 more fill the three slots.
        _make_session(client, auth_headers)
        _make_session(client, auth_headers)
        before = self._ids(free_tenant)
        r = client.post(f"/api/v1/sessions/{completed_session['id']}/backtest",
                        headers=auth_headers, json={"holdout_periods": 7})
        assert r.status_code == 403
        assert self._ids(free_tenant) == before


# ── 3. A dataset a session reads cannot be erased ────────────────────────────

class TestDatasetProtection:

    def test_dataset_used_by_a_session_is_refused_and_its_file_survives(
        self, client, auth_headers, configured_session, uploaded_dataset,
    ):
        row = query_one("SELECT file_path FROM datasets WHERE id = %s", (uploaded_dataset["id"],))
        assert Path(row["file_path"]).exists()
        r = client.delete(f"/api/v1/data-sources/{uploaded_dataset['id']}", headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "data_source_in_use"
        assert query_one("SELECT 1 AS x FROM datasets WHERE id = %s", (uploaded_dataset["id"],))
        assert Path(row["file_path"]).exists(), "refused, yet the file was already gone from disk"

    def test_an_archived_session_still_protects_its_dataset(
        self, client, auth_headers, configured_session, uploaded_dataset,
    ):
        client.delete(f"/api/v1/sessions/{configured_session['id']}", headers=auth_headers)
        r = client.delete(f"/api/v1/data-sources/{uploaded_dataset['id']}", headers=auth_headers)
        assert r.status_code == 409

    def test_unused_dataset_deletes_and_is_audited(
        self, client, auth_headers, csv_bytes, registered_user,
    ):
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("loose.csv", csv_bytes, "text/csv")}).json()["data"]
        path = Path(query_one("SELECT file_path FROM datasets WHERE id = %s", (up["id"],))["file_path"])
        assert client.delete(f"/api/v1/data-sources/{up['id']}", headers=auth_headers).status_code == 200
        assert query_one("SELECT 1 AS x FROM datasets WHERE id = %s", (up["id"],)) is None
        assert not path.exists()
        assert query_one("SELECT 1 AS x FROM activity_logs WHERE action = 'dataset.delete' AND resource = %s",
                         (up["id"],))

    def test_replacing_a_file_keeps_the_previous_one(self, client, auth_headers, csv_bytes):
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("v1.csv", csv_bytes, "text/csv")}).json()["data"]
        old = Path(query_one("SELECT file_path FROM datasets WHERE id = %s", (up["id"],))["file_path"])
        original = old.read_bytes()
        r = client.post(f"/api/v1/data-sources/{up['id']}/file", headers=auth_headers,
                        files={"file": ("v2.csv", _csv([("2024-01-01", "SKU_001", 1)]), "text/csv")})
        assert r.status_code == 200, r.text
        kept = list((old.parent / "previous").glob("*/data.csv"))
        assert len(kept) == 1 and kept[0].read_bytes() == original


# ── 4. No cleanup path erases a real tenant's session ────────────────────────

class TestNoCleanupPathErasesRealSessions:

    def test_source_has_no_session_delete_outside_tenant_erasure(self):
        """The only code allowed to remove session rows is whole-tenant erasure
        (right to erasure, typed confirmation) and the trial reaper through it."""
        offenders = []
        for p in BACKEND.rglob("*.py"):
            rel = p.relative_to(BACKEND).as_posix()
            if rel.startswith(("tests/", ".venv/")) or "/tests/" in rel:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"DELETE\s+FROM\s+sessions\b", text, re.I) and rel not in (
                    "tenants/data_export.py", "db/migrations.py"):
                offenders.append(rel)
            if re.search(r"\bsession_svc\.delete_session\b|\bdelete_session\(tenant", text):
                offenders.append(rel)
        assert not offenders, f"these modules can erase a session: {offenders}"

    def test_trial_reaper_never_touches_a_real_tenant_with_a_past_trial_date(
        self, registered_user, completed_session,
    ):
        from backend.trial import service as trial_svc
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'free', trial_ends_at = NOW() - INTERVAL '3 days' WHERE id = %s",
                (tid,))
        trial_svc.reap_expired_trials()
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (tid,))
        assert query_one("SELECT 1 AS x FROM sessions WHERE id = %s", (completed_session["id"],))
        assert session_store.get_forecasts(tid, completed_session["id"])

    def test_trial_reaper_rechecks_the_tier_right_before_erasing(
        self, registered_user, completed_session, monkeypatch,
    ):
        """A tenant listed as an expired trial that became a customer between the
        SELECT and the erase must survive."""
        from backend.trial import service as trial_svc
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'demo', trial_ends_at = NOW() - INTERVAL '1 day' WHERE id = %s",
                (tid,))
        real_query = trial_svc.query

        def _upgraded_after_listing(sql, params=None, *a, **k):
            rows = real_query(sql, params, *a, **k)
            execute("UPDATE tenants SET tier = 'paid' WHERE id = %s", (tid,))
            return rows

        monkeypatch.setattr(trial_svc, "query", _upgraded_after_listing)
        trial_svc.reap_expired_trials()
        assert query_one("SELECT 1 AS x FROM sessions WHERE id = %s", (completed_session["id"],))
        assert query_one("SELECT tier FROM tenants WHERE id = %s", (tid,))["tier"] == "paid"

    def test_trial_reaper_still_erases_an_expired_demo_tenant(self, client):
        from backend.tenants.data_export import delete_tenant
        from backend.trial import service as trial_svc
        data = client.post("/api/v1/trial").json()["data"]
        tid = query_one("SELECT tenant_id FROM users WHERE email = %s", (data["email"],))["tenant_id"]
        try:
            execute("UPDATE tenants SET trial_ends_at = NOW() - INTERVAL '1 hour' WHERE id = %s", (tid,))
            trial_svc.reap_expired_trials()
            assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (tid,)) is None
        finally:
            if query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (tid,)):
                delete_tenant(tid)

    def test_scheduled_retrain_prune_archives_and_never_deletes(self, registered_user):
        from backend.sessions import retrain_service
        tid = registered_user["tenant"]["id"]
        sched = f"sched-{uuid4().hex[:8]}"
        ids = []
        for i, status in enumerate(("COMPLETED", "COMPLETED", "FAILED")):
            sid = f"sess_{uuid4().hex[:8]}"
            execute(
                "INSERT INTO sessions (id, tenant_id, name, status, created_by, scheduled_job_id, updated_at) "
                "VALUES (%s, %s, %s, %s, 'u', %s, NOW() - make_interval(hours => %s))",
                (sid, tid, f"run{i}", status, sched, i))
            execute("INSERT INTO session_configs (session_id, tenant_id) VALUES (%s, %s)", (sid, tid))
            ids.append(sid)
        n = retrain_service.prune_previous_runs(tid, sched)
        assert n == 2
        rows = {r["id"]: r for r in query(
            "SELECT id, archived_at FROM sessions WHERE id = ANY(%s)", (ids,))}
        assert set(rows) == set(ids), "a pruned run was erased"
        assert rows[ids[0]]["archived_at"] is None            # the one now serving
        assert rows[ids[1]]["archived_at"] is not None
        assert rows[ids[2]]["archived_at"] is not None
        # Idempotent: a second pass finds nothing left to archive.
        assert retrain_service.prune_previous_runs(tid, sched) == 0

    def test_archived_and_backtest_sessions_never_drive_planning(self, registered_user, completed_session):
        from backend.inventory.service import get_latest_completed_session
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        assert get_latest_completed_session(tid)["session_id"] == sid
        execute("UPDATE sessions SET archived_at = NOW() WHERE id = %s", (sid,))
        assert get_latest_completed_session(tid) is None
        execute("UPDATE sessions SET archived_at = NULL, is_backtest = TRUE WHERE id = %s", (sid,))
        assert get_latest_completed_session(tid) is None


# ── 5. The library: search, filter, sort, paginate ───────────────────────────

class TestSessionLibrary:

    def _seed(self, tid, n=30):
        ids = []
        for i in range(n):
            sid = f"sess_{uuid4().hex[:10]}"
            execute(
                "INSERT INTO sessions (id, tenant_id, name, status, created_by, created_at) "
                "VALUES (%s, %s, %s, %s, 'u', NOW() - make_interval(days => %s))",
                (sid, tid, f"Run {i:02d}{' promo' if i % 10 == 0 else ''}",
                 "COMPLETED" if i % 3 else "FAILED", i))
            execute("INSERT INTO session_configs (session_id, tenant_id) VALUES (%s, %s)", (sid, tid))
            ids.append(sid)
        return ids

    def _get(self, client, headers, **params):
        r = client.get("/api/v1/sessions/summary", headers=headers, params=params)
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_pagination_total_and_stable_pages(self, client, auth_headers, registered_user):
        self._seed(registered_user["tenant"]["id"], 30)
        p1 = self._get(client, auth_headers, limit=10, skip=0)
        p2 = self._get(client, auth_headers, limit=10, skip=10)
        p4 = self._get(client, auth_headers, limit=10, skip=30)
        assert p1["total"] == 30 and len(p1["items"]) == 10 and len(p2["items"]) == 10
        assert not ({s["id"] for s in p1["items"]} & {s["id"] for s in p2["items"]})
        assert p4["items"] == [] and p4["total"] == 30
        # newest first by default
        dates = [s["created_at"] for s in p1["items"]]
        assert dates == sorted(dates, reverse=True)

    def test_search_status_and_sort(self, client, auth_headers, registered_user):
        self._seed(registered_user["tenant"]["id"], 30)
        promo = self._get(client, auth_headers, q="promo", limit=50)
        assert promo["total"] == 3 and all("promo" in s["name"] for s in promo["items"])
        failed = self._get(client, auth_headers, status="FAILED", limit=50)
        assert failed["total"] == 10 and {s["status"] for s in failed["items"]} == {"FAILED"}
        both = self._get(client, auth_headers, status=["FAILED", "COMPLETED"], limit=50)
        assert both["total"] == 30
        by_name = self._get(client, auth_headers, sort="name", order="asc", limit=5)
        names = [s["name"] for s in by_name["items"]]
        assert names == sorted(names, key=str.lower)

    def test_search_treats_wildcards_literally(self, client, auth_headers, registered_user):
        self._seed(registered_user["tenant"]["id"], 5)
        assert self._get(client, auth_headers, q="%")["total"] == 0
        assert self._get(client, auth_headers, q="_")["total"] == 0

    def test_archived_scope_and_other_tenants_are_isolated(
        self, client, auth_headers, registered_user, make_tenant_user_headers,
    ):
        ids = self._seed(registered_user["tenant"]["id"], 4)
        client.delete(f"/api/v1/sessions/{ids[0]}", headers=auth_headers)
        assert self._get(client, auth_headers)["total"] == 3
        arch = self._get(client, auth_headers, archived="archived")
        assert arch["total"] == 1 and arch["items"][0]["id"] == ids[0]
        assert arch["items"][0]["archived_at"] is not None
        assert self._get(client, auth_headers, archived="all")["total"] == 4
        other = make_tenant_user_headers(role="admin")
        assert self._get(client, other, archived="all")["total"] == 0

    def test_headline_accuracy_and_models_come_from_the_results(
        self, client, auth_headers, completed_session,
    ):
        data = self._get(client, auth_headers, status="COMPLETED")
        item = next(s for s in data["items"] if s["id"] == completed_session["id"])
        assert item["models"] == ["lightgbm", "prophet"]
        rows = session_store.get_training_result(
            query_one("SELECT tenant_id FROM sessions WHERE id = %s", (item["id"],))["tenant_id"],
            item["id"])["metrics"]["rows"]
        per_sku_best = {}
        for r in rows:
            per_sku_best[r["sku"]] = min(per_sku_best.get(r["sku"], 9), r["wape"])
        assert item["accuracy"] == pytest.approx(1 - sum(per_sku_best.values()) / len(per_sku_best), abs=1e-6)
        by_acc = self._get(client, auth_headers, sort="accuracy", order="desc")
        assert by_acc["items"][0]["id"] == completed_session["id"]

    def test_bad_sort_is_rejected_not_interpolated(self, client, auth_headers):
        r = client.get("/api/v1/sessions/summary", headers=auth_headers,
                       params={"sort": "name; DROP TABLE sessions"})
        assert r.status_code == 422


# ── 6. Compare with ANY dataset, and say why when nothing overlaps ───────────

class TestCompareWithAnyDataset:

    URL = "/api/v1/sessions/{sid}/forecast-vs-actual"

    def test_the_training_file_itself_is_offered_with_its_dates_and_the_gap(
        self, client, auth_headers, completed_session, uploaded_dataset,
    ):
        d = client.get(self.URL.format(sid=completed_session["id"]), headers=auth_headers).json()["data"]
        mine = next(c for c in d["candidates"] if c["dataset_id"] == uploaded_dataset["id"])
        assert mine["is_training_dataset"] is True
        assert mine["first_date"] == "2023-01-01"
        assert mine["overlap"]["relation"] == "ends_before_forecast"
        assert mine["overlap"]["gap_days"] > 0
        assert d["status"] == "no_later_upload"

    def test_an_older_file_that_covers_the_window_can_be_chosen(
        self, client, auth_headers, completed_session, registered_user,
    ):
        """The file is uploaded BEFORE the session is created in this timeline
        only by name; what matters is that it is selectable regardless of upload
        order and graded exactly."""
        from backend.forecast_check.service import _champion_forecasts
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        fc = _champion_forecasts(tid, sid)
        rows = [(d, sku, round(v, 4)) for sku, s in fc.items() for d, v in s.items()]
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("history.csv", _csv(rows), "text/csv")}).json()["data"]
        # Make it OLDER than the session.
        execute("UPDATE datasets SET uploaded_at = NOW() - INTERVAL '400 days' WHERE id = %s", (up["id"],))
        d = client.get(self.URL.format(sid=sid) + f"?dataset_id={up['id']}", headers=auth_headers).json()["data"]
        assert d["status"] == "ok"
        assert d["result"]["aggregate"]["wape"] == pytest.approx(0.0, abs=1e-6)
        assert d["overlap"]["relation"] == "covers"
        assert d["overlap"]["compared_periods"] == d["overlap"]["forecast_periods"]
        listed = next(c for c in d["candidates"] if c["dataset_id"] == up["id"])
        assert listed["uploaded_after_session"] is False
        assert listed["overlap"]["relation"] == "covers"

    def test_a_chosen_file_that_misses_the_window_explains_by_how_much(
        self, client, auth_headers, completed_session,
    ):
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("old.csv", _csv([("2020-01-0%d" % i, "SKU_001", 3) for i in range(1, 8)]),
                                         "text/csv")}).json()["data"]
        d = client.get(self.URL.format(sid=completed_session["id"]) + f"?dataset_id={up['id']}",
                       headers=auth_headers).json()["data"]
        assert d["status"] == "no_overlap" and d["result"] is None
        assert d["overlap"]["relation"] == "ends_before_forecast"
        assert d["source"]["first_date"] == "2020-01-01" and d["source"]["last_date"] == "2020-01-07"

    def test_dates_overlap_but_products_do_not(self, client, auth_headers, completed_session, registered_user):
        from backend.forecast_check.service import _champion_forecasts
        fc = _champion_forecasts(registered_user["tenant"]["id"], completed_session["id"])
        dates = sorted({d for s in fc.values() for d in s})
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("other.csv", _csv([(d, "NOT_A_SKU", 4) for d in dates]),
                                         "text/csv")}).json()["data"]
        d = client.get(self.URL.format(sid=completed_session["id"]) + f"?dataset_id={up['id']}",
                       headers=auth_headers).json()["data"]
        assert d["status"] == "no_matching_series"
        assert d["overlap"]["relation"] == "covers"

    def test_a_file_without_the_columns_is_a_reason_not_an_error(self, client, auth_headers, completed_session):
        up = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("odd.csv", _csv([("a", "b")], header=("foo", "bar")), "text/csv")}
                         ).json()["data"]
        d = client.get(self.URL.format(sid=completed_session["id"]) + f"?dataset_id={up['id']}",
                       headers=auth_headers).json()["data"]
        assert d["status"] == "columns_missing"
        listed = next(c for c in d["candidates"] if c["dataset_id"] == up["id"])
        assert listed["range_error"] == "columns_missing"


# ── 7. Back-test: hold out the last periods and retrain ──────────────────────

class TestBacktest:

    def test_holdout_copy_drops_the_last_periods_and_keeps_the_original(self, tmp_path):
        from backend.dataframes.actuals import write_holdout_copy
        src = tmp_path / "s.csv"
        src.write_bytes(_csv([(f"2023-01-{d:02d}", "A", d) for d in range(1, 29)]))
        before = src.read_bytes()
        dst = tmp_path / "copy.csv"
        out = write_holdout_copy(str(src), str(dst), "date", 7, None)
        assert out["cutoff"] == "2023-01-21" and out["last_date"] == "2023-01-28"
        kept = dst.read_text().strip().splitlines()
        assert len(kept) - 1 == 21 and kept[-1].startswith("2023-01-21")
        assert src.read_bytes() == before
        too_much = write_holdout_copy(str(src), str(tmp_path / "x.csv"), "date", 40, None)
        assert too_much == {"error": "holdout_too_large"}

    def test_weekly_cut_lands_on_a_whole_week(self, tmp_path):
        from datetime import date, timedelta
        from backend.dataframes.actuals import write_holdout_copy
        src = tmp_path / "s.csv"
        start = date(2023, 1, 2)   # a Monday
        src.write_bytes(_csv([((start + timedelta(days=i)).isoformat(), "A", 1) for i in range(120)]))
        out = write_holdout_copy(str(src), str(tmp_path / "w.csv"), "date", 4, "W-MON")
        assert date.fromisoformat(out["cutoff"]).weekday() == 0     # a closing Monday

    def test_backtest_launches_a_flagged_session_on_a_shorter_copy(
        self, client, auth_headers, configured_session, uploaded_dataset, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        # The backtest source must be COMPLETED; the fixture's data is real.
        from tests.fixtures.synthetic_data import seed_completed_session
        seed_completed_session(tid, sid, n_skus=3)
        r = client.post(f"/api/v1/sessions/{sid}/backtest", headers=auth_headers,
                        json={"holdout_periods": 7})
        assert r.status_code == 202, r.text
        out = r.json()["data"]
        new = query_one("SELECT * FROM sessions WHERE id = %s", (out["session_id"],))
        assert new["is_backtest"] is True and new["status"] == "QUEUED"
        assert new["backtest_source_dataset_id"] == uploaded_dataset["id"]
        assert new["backtest_holdout_periods"] == 7
        assert new["dataset_id"] == out["dataset_id"] != uploaded_dataset["id"]
        assert new["archived_at"] is None
        copy_rows = query_one("SELECT file_path FROM datasets WHERE id = %s", (out["dataset_id"],))
        orig = query_one("SELECT file_path FROM datasets WHERE id = %s", (uploaded_dataset["id"],))
        n_copy = len(Path(copy_rows["file_path"]).read_text().strip().splitlines())
        n_orig = len(Path(orig["file_path"]).read_text().strip().splitlines())
        assert n_copy < n_orig                                      # the original is untouched
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] == "COMPLETED"
        # It is audited and the config travelled.
        assert query_one("SELECT 1 AS x FROM activity_logs WHERE action = 'session.backtest' AND resource = %s",
                         (out["session_id"],))
        assert session_store.get_field(tid, out["session_id"], "models_cfg")

    def test_backtest_validation_and_permissions(
        self, client, auth_headers, viewer_headers, completed_session,
    ):
        sid = completed_session["id"]
        before = query_one("SELECT COUNT(*) AS n FROM sessions")["n"]
        assert client.post(f"/api/v1/sessions/{sid}/backtest", headers=viewer_headers,
                           json={"holdout_periods": 7}).status_code == 403
        assert client.post(f"/api/v1/sessions/{sid}/backtest", headers=auth_headers,
                           json={"holdout_periods": 0}).status_code == 422
        assert client.post(f"/api/v1/sessions/{sid}/backtest", headers=auth_headers,
                           json={"holdout_periods": 500}).status_code == 422
        assert query_one("SELECT COUNT(*) AS n FROM sessions")["n"] == before

    def test_backtest_of_a_session_still_training_is_refused(self, client, auth_headers, test_session):
        r = client.post(f"/api/v1/sessions/{test_session['id']}/backtest", headers=auth_headers,
                        json={"holdout_periods": 7})
        assert r.status_code == 409

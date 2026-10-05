"""`GET /jobs/active` — what the app-wide training indicator and the resumed
training screen read — and the worker's progress reporter."""

import pytest

from backend.db.connection import execute, query_one
from backend.sessions import service as session_svc
from backend.training import job_service
from backend.training.job_service import create_job, mark_running, update_progress


def _session_with_job(tid, uid, name, family_id=None, granularity=None, status="RUNNING"):
    s = session_svc.create_session(tid, uid, name)
    execute(
        "UPDATE sessions SET family_id = %s, granularity = %s WHERE id = %s",
        (family_id, granularity, s["id"]),
    )
    job = create_job(tid, s["id"], uid)
    if status != "QUEUED":
        execute("UPDATE jobs SET status = %s WHERE id = %s", (status, job["id"]))
    return s, job


class TestActiveEndpoint:

    def test_nothing_running_returns_empty_list(self, client, auth_headers):
        r = client.get("/api/v1/jobs/active", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["families"] == []

    def test_running_family_is_reported_with_averaged_percent(
        self, client, auth_headers, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        base, base_job = _session_with_job(tid, uid, "plan", None, "daily")
        execute("UPDATE sessions SET family_id = id WHERE id = %s", (base["id"],))
        sib, sib_job = _session_with_job(tid, uid, "plan · weekly", base["id"], "weekly")
        mark_running(tid, base_job["id"], "w1")
        update_progress(tid, base_job["id"], {"percent": 40, "step": "ml_training", "message": "Training ML models"})
        # The sibling finished: counts as 100.
        execute("UPDATE jobs SET status = 'COMPLETED' WHERE id = %s", (sib_job["id"],))

        r = client.get("/api/v1/jobs/active", headers=auth_headers)
        assert r.status_code == 200
        fams = r.json()["data"]["families"]
        assert len(fams) == 1
        f = fams[0]
        assert f["base_session_id"] == base["id"]
        assert f["base_job_id"] == base_job["id"]
        assert f["status"] == "RUNNING"
        assert f["step"] == "ml_training"
        assert f["percent"] == 70            # (40 + 100) / 2
        assert {m["job_id"] for m in f["members"]} == {base_job["id"], sib_job["id"]}

    def test_finished_job_is_not_active(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _session_with_job(tid, uid, "done", None, None, status="COMPLETED")
        _session_with_job(tid, uid, "bad", None, None, status="FAILED")
        assert client.get("/api/v1/jobs/active", headers=auth_headers).json()["data"]["families"] == []

    def test_orphaned_job_older_than_the_window_is_ignored(
        self, client, auth_headers, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _, job = _session_with_job(tid, uid, "orphan", None, None)
        execute("UPDATE jobs SET created_at = NOW() - INTERVAL '30 hours' WHERE id = %s", (job["id"],))
        assert client.get("/api/v1/jobs/active", headers=auth_headers).json()["data"]["families"] == []

    def test_viewer_can_read_it(self, client, viewer_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _, job = _session_with_job(tid, uid, "visible", None, None, status="QUEUED")
        r = client.get("/api/v1/jobs/active", headers=viewer_headers)
        assert r.status_code == 200
        assert [f["base_job_id"] for f in r.json()["data"]["families"]] == [job["id"]]

    def test_other_tenants_runs_are_invisible(
        self, client, make_tenant_user_headers, test_tenant, registered_user, auth_headers,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _session_with_job(tid, uid, "mine", None, None)
        other = make_tenant_user_headers(role="analyst")
        assert client.get("/api/v1/jobs/active", headers=other).json()["data"]["families"] == []
        assert len(client.get("/api/v1/jobs/active", headers=auth_headers).json()["data"]["families"]) == 1

    def test_requires_authentication(self, client):
        assert client.get("/api/v1/jobs/active").status_code in (401, 403)

    def test_active_is_not_read_as_a_job_id(self, client, auth_headers):
        r = client.get("/api/v1/jobs/active", headers=auth_headers)
        assert r.json().get("error") is None and "families" in r.json()["data"]


class TestJobProgressReporter:

    def _job(self, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "p")
        return tid, s["id"], create_job(tid, s["id"], uid)["id"]

    def test_persisted_percent_is_monotonic_across_stages_and_units(
        self, test_tenant, registered_user, monkeypatch,
    ):
        from backend.workers import runner
        monkeypatch.setattr(runner, "_PROGRESS_MIN_INTERVAL_S", 0.0)
        tid, sid, jid = self._job(test_tenant, registered_user)
        seen = []
        real = runner.update_progress

        def spy(t, j, p):
            real(t, j, p)
            seen.append(query_one("SELECT progress FROM jobs WHERE id = %s", (j,))["progress"])
        monkeypatch.setattr(runner, "update_progress", spy)

        prog = runner.JobProgress(tid, sid, jid)
        prog.begin("init", "x")
        prog.begin("load", "x")
        prog.drop("gap_fill")
        prog.begin("ml_training", "Training ML models")
        for d in range(1, 21):
            prog.on_engine_event({"stage": "ml_training", "done": d, "total": 20,
                                  "message": "Training ML models"})
        prog.on_engine_event({"stage": "ml_training", "finished": True})
        prog.begin("saving", "Saving results...")

        pcts = [p["percent"] for p in seen]
        assert pcts == sorted(pcts) and pcts[-1] > pcts[0]
        # Many distinct values inside the training stage, not a plateau and a jump.
        assert len({p["percent"] for p in seen if p["step"] == "ml_training"}) >= 5
        row = query_one("SELECT progress FROM jobs WHERE id = %s", (jid,))["progress"]
        assert row["step"] == "saving" and row["percent"] == pcts[-1]

    def test_unit_updates_inside_a_stage_are_throttled_but_stage_changes_are_not(
        self, test_tenant, registered_user, monkeypatch,
    ):
        from backend.workers import runner
        monkeypatch.setattr(runner, "_PROGRESS_MIN_INTERVAL_S", 3600.0)
        tid, sid, jid = self._job(test_tenant, registered_user)
        writes = []
        monkeypatch.setattr(runner, "update_progress", lambda t, j, p: writes.append(p))
        prog = runner.JobProgress(tid, sid, jid)
        prog.begin("ml_training", "Training ML models")
        for d in range(1, 50):
            prog.on_engine_event({"stage": "ml_training", "done": d, "total": 50,
                                  "message": "Training ML models"})
        assert len(writes) == 1
        prog.begin("stat_training", "Training statistical models")
        assert len(writes) == 2

    def test_worker_pickup_does_not_claim_progress(self, test_tenant, registered_user):
        tid, sid, jid = self._job(test_tenant, registered_user)
        job = mark_running(tid, jid, "w1")
        assert job["progress"]["percent"] == 0

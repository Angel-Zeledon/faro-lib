"""While a run is training, the session has to say so.

`runner.py` only ever wrote COMPLETED or FAILED back onto the session, so a
session being trained right now read QUEUED for the whole run. Everything
downstream was already built for the truth and never saw it:

  * `/historial` has a RUNNING badge and the copy "Calculando" — unreachable.
  * The public `/train` status endpoint documents RUNNING as one of its four
    values, and a customer polling it never got one.
  * `DELETE /sessions/{id}` refused when the session was RUNNING, which meant
    it refused never: a live training run could be deleted out from under its
    own worker, cascading the job row away while the worker kept writing.

The two tests that covered that last guard wrote `status='RUNNING'` onto the
session by hand — a state no worker produced — so they passed against the open
door. Hence this file: it asserts the status the *runner* writes, observed from
inside the run.
"""

import pytest

from backend.db.connection import query_one
from backend.sessions import service as session_svc
from backend.training.job_service import create_job


class TestSessionIsMarkedRunning:
    def test_the_session_reads_RUNNING_while_the_engine_is_working(
        self, test_tenant, registered_user, monkeypatch,
    ):
        """Observed from inside, not inferred from the end state.

        `build_engine_config` is the runner's first real step. Reading the
        session row from there is reading it mid-run, which is exactly the
        moment the screen and the public API were lying about.
        """
        from backend.workers import runner

        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "labels-itself")
        job = create_job(tid, s["id"], uid)

        seen: dict = {}

        def _spy(tenant_id, session_id):
            row = query_one(
                "SELECT status, pipeline_step FROM sessions WHERE id = %s", (session_id,)
            )
            seen.update(row or {})
            raise RuntimeError("stop here — the status is what this test is about")

        monkeypatch.setattr(runner, "build_engine_config", _spy)
        runner.run_training_job(tid, s["id"], job["id"])

        assert seen.get("status") == "RUNNING", (
            f"the session said {seen.get('status')!r} while the engine was running"
        )
        assert seen.get("pipeline_step") == "train"

    def test_a_failure_still_lands_on_FAILED(
        self, test_tenant, registered_user, monkeypatch,
    ):
        """Marking RUNNING first must not leave a crashed run stuck there."""
        from backend.workers import runner

        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "crashes")
        job = create_job(tid, s["id"], uid)

        def _boom(tenant_id, session_id):
            raise RuntimeError("engine config blew up")

        monkeypatch.setattr(runner, "build_engine_config", _boom)
        runner.run_training_job(tid, s["id"], job["id"])

        assert query_one(
            "SELECT status FROM sessions WHERE id = %s", (s["id"],)
        )["status"] == "FAILED"
        assert query_one(
            "SELECT status FROM jobs WHERE id = %s", (job["id"],)
        )["status"] == "FAILED"

    def test_labelling_the_session_cannot_stop_the_run(
        self, test_tenant, registered_user, monkeypatch,
    ):
        """The label is best-effort: if it fails, the training still happens.

        A run that refused to start because it could not update a status column
        would be a worse product than one that trains under a stale label. The
        run is then let to fail on the spy below; the failure path's own
        `force_status` is broken too by this patch, and the assertion is that
        the engine was reached at all.
        """
        from backend.workers import runner

        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "label-fails")
        job = create_job(tid, s["id"], uid)

        def _no_status(*a, **k):
            raise RuntimeError("status column unavailable")

        reached = {"yes": False}

        def _spy(tenant_id, session_id):
            reached["yes"] = True
            raise RuntimeError("stop here")

        monkeypatch.setattr(runner, "force_status", _no_status)
        monkeypatch.setattr(runner, "build_engine_config", _spy)
        runner.run_training_job(tid, s["id"], job["id"])

        assert reached["yes"], "the run never started because the label could not be written"


class TestDeletingALiveRun:
    """The guard the dead status left open, exercised through the API."""

    @pytest.mark.parametrize("job_status", ["QUEUED", "RUNNING"])
    def test_delete_is_refused_while_a_job_is_in_flight(
        self, job_status, client, auth_headers, test_tenant, registered_user,
    ):
        from backend.training import job_service

        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, f"live-{job_status}")
        job = create_job(tid, s["id"], uid)
        if job_status == "RUNNING":
            job_service.mark_running(tid, job["id"], "worker-test")

        resp = client.delete(f"/api/v1/sessions/{s['id']}", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "session_running_cannot_delete"
        assert query_one("SELECT id FROM sessions WHERE id = %s", (s["id"],)) is not None
        assert query_one("SELECT id FROM jobs WHERE id = %s", (job["id"],)) is not None

    def test_viewer_is_refused_too_and_nothing_moves(
        self, client, viewer_headers, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "viewer-delete")
        create_job(tid, s["id"], uid)

        resp = client.delete(f"/api/v1/sessions/{s['id']}", headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one("SELECT id FROM sessions WHERE id = %s", (s["id"],)) is not None

"""Split-deployment topology: which loops an instance runs, and whose
orphaned jobs it may recover.

Two invariants protect a deployment with a separate worker container:
  1. WORKER_ENABLED / SCHEDULER_ENABLED decide which components start() launches,
     so the API can run with none and a scaled-out extra worker can run the
     claim loop without duplicating the daily-email cron loops.
  2. Orphan recovery is scoped to the recovering instance's worker_id (plus
     legacy NULL rows): a restart of worker A must never fail a job worker B
     is actively training — and an API redeploy must not fail either's.
"""

from uuid import uuid4

from backend.config import settings
from backend.db.connection import execute, query_one
from backend.sessions import service as session_svc
from backend.training.job_service import create_job
from backend.workers import worker


def _running_job(tid: str, uid: str, worker_id, name: str) -> tuple[str, str]:
    """A job in RUNNING state claimed by `worker_id` (None = legacy row)."""
    s = session_svc.create_session(tid, uid, name)
    job_id = create_job(tid, s["id"], uid)["id"]
    execute(
        "UPDATE jobs SET status = 'RUNNING', started_at = NOW(), worker_id = %s WHERE id = %s",
        (worker_id, job_id),
    )
    return job_id, s["id"]


class TestEnabledComponents:

    def test_both_enabled_runs_everything(self, monkeypatch):
        monkeypatch.setattr(settings, "worker_enabled", True)
        monkeypatch.setattr(settings, "scheduler_enabled", True)
        assert worker.enabled_components() == [
            "job-worker", "job-scheduler", "inventory-alerts",
            "overstock-snapshot", "integration-sync",
        ]

    def test_api_only_instance_runs_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "worker_enabled", False)
        monkeypatch.setattr(settings, "scheduler_enabled", False)
        assert worker.enabled_components() == []

    def test_scaled_out_extra_worker_claims_but_never_schedules(self, monkeypatch):
        # The config that prevents duplicate daily emails on a second worker.
        monkeypatch.setattr(settings, "worker_enabled", True)
        monkeypatch.setattr(settings, "scheduler_enabled", False)
        components = worker.enabled_components()
        assert components == ["job-worker"]

    def test_scheduler_only_instance_has_no_claim_loop(self, monkeypatch):
        monkeypatch.setattr(settings, "worker_enabled", False)
        monkeypatch.setattr(settings, "scheduler_enabled", True)
        components = worker.enabled_components()
        assert "job-worker" not in components
        assert "inventory-alerts" in components


class TestWorkerId:

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setattr(settings, "worker_id", "worker-7")
        assert worker.worker_id() == "worker-7"

    def test_blank_falls_back_to_hostname(self, monkeypatch):
        import socket
        monkeypatch.setattr(settings, "worker_id", "   ")
        assert worker.worker_id() == socket.gethostname()


class TestOrphanRecoveryScope:

    def test_recovers_own_and_legacy_jobs_but_never_a_siblings(
        self, monkeypatch, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        mine_wid = f"w-mine-{uuid4().hex[:8]}"
        sibling_wid = f"w-sibling-{uuid4().hex[:8]}"

        mine_job, mine_session = _running_job(tid, uid, mine_wid, "orphan-mine")
        sibling_job, sibling_session = _running_job(tid, uid, sibling_wid, "orphan-sibling")
        legacy_job, _ = _running_job(tid, uid, None, "orphan-legacy")

        monkeypatch.setattr(settings, "worker_id", mine_wid)
        recovered = worker.recover_orphaned_jobs()
        assert recovered >= 2  # mine + legacy (parallel tests may add NULL rows)

        mine = query_one("SELECT status, error, completed_at FROM jobs WHERE id = %s", (mine_job,))
        assert mine["status"] == "FAILED"
        assert "aborted" in mine["error"]
        assert mine["completed_at"] is not None

        legacy = query_one("SELECT status FROM jobs WHERE id = %s", (legacy_job,))
        assert legacy["status"] == "FAILED"

        # The invariant that makes multi-worker safe: the sibling's live job
        # is untouched — still RUNNING, no error, still owned by the sibling.
        sibling = query_one(
            "SELECT status, error, worker_id FROM jobs WHERE id = %s", (sibling_job,))
        assert sibling["status"] == "RUNNING"
        assert sibling["error"] is None
        assert sibling["worker_id"] == sibling_wid

        # Session state follows its job's fate.
        mine_sess = query_one("SELECT status FROM sessions WHERE id = %s", (mine_session,))
        assert mine_sess["status"] == "FAILED"
        sibling_sess = query_one("SELECT status FROM sessions WHERE id = %s", (sibling_session,))
        assert sibling_sess["status"] != "FAILED"


class TestWorkerOpensItsOwnPool:
    """The dedicated worker container is a SECOND process: it reaches the
    database only if it opens a pool itself.

    This is not theory. Running the production image with
    `python -m backend.workers` against a healthy database logged
    "Waiting for database (n/60): DB pool not initialized" sixty times and
    exited 1 — a restart loop in which training never runs and the 08:00
    alerts never fire, next to an API that answers /health perfectly.
    """

    def test_wait_for_db_opens_a_pool_when_the_process_has_none(self, monkeypatch):
        from backend.db import connection
        from backend.workers.__main__ import _wait_for_db

        original = connection._pool
        # Exactly the state a freshly-exec'd worker process starts in.
        monkeypatch.setattr(connection, "_pool", None)
        assert not connection.pool_is_initialized()

        try:
            _wait_for_db()

            assert connection.pool_is_initialized(), (
                "_wait_for_db returned without opening a pool; the worker "
                "process would burn its 60 attempts and exit(1)"
            )
            # And the pool it opened actually reaches the database.
            assert connection.query_one("SELECT 1 AS ok")["ok"] == 1
        finally:
            opened = connection._pool
            if opened is not None and opened is not original:
                opened.closeall()
            connection._pool = original

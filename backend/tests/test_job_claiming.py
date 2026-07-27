"""Taking a job off the queue has to be one atomic step.

The worker used to `dequeue()` (SELECT the oldest QUEUED row), then `get_job()`
to re-check it was still QUEUED, then `mark_running()`. Two workers polling at
the same moment both saw the same row, both found it QUEUED, and both
dispatched it: the same session trained twice, each run overwriting the other's
results. A single dev worker hid this completely.

`claim()` does it in one UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP
LOCKED), so a second worker takes a *different* job or nothing at all.
"""

import threading

import pytest

from backend.db.connection import query_one
from backend.sessions import service as session_svc
from backend.training import queue as job_queue
from backend.training.job_service import create_job


def _queued_job(tid, uid, name="claim-test"):
    s = session_svc.create_session(tid, uid, name)
    return create_job(tid, s["id"], uid)["id"], s["id"]


class TestClaimIsAtomic:
    def test_one_job_is_claimed_exactly_once_under_concurrency(
        self, client, test_tenant, registered_user,
    ):
        """The defect this file exists for: N workers, one job, one winner."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        job_id, _ = _queued_job(tid, uid)

        claims: list[dict] = []
        lock = threading.Lock()

        def worker(n):
            got = job_queue.claim(f"worker-{n}")
            if got and got["job_id"] == job_id:
                with lock:
                    claims.append(got)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert len(claims) == 1, f"job claimed {len(claims)} times: {claims}"

    def test_claiming_marks_it_running_with_the_claimant(
        self, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        job_id, session_id = _queued_job(tid, uid)

        got = job_queue.claim("worker-alpha")
        assert got["job_id"] == job_id
        assert got["session_id"] == session_id

        row = query_one("SELECT status, worker_id, started_at FROM jobs WHERE id = %s", (job_id,))
        assert row["status"] == "RUNNING"
        assert row["worker_id"] == "worker-alpha"
        assert row["started_at"] is not None

    def test_an_already_running_job_is_not_claimed_again(
        self, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        job_id, _ = _queued_job(tid, uid)
        job_queue.claim("worker-first")

        again = job_queue.claim("worker-second")
        assert again is None or again["job_id"] != job_id

    def test_empty_queue_returns_none(self, client, test_tenant):
        # Drain anything this tenant (or a neighbour) left behind.
        while job_queue.claim("worker-drain"):
            pass
        assert job_queue.claim("worker-drain") is None


class TestBlockedTenantsStayQueued:
    def test_a_blocked_tenant_job_is_left_for_the_next_poll(
        self, client, test_tenant, registered_user,
    ):
        """At its concurrent-job limit a tenant is skipped, NOT failed: the row
        must still be QUEUED afterwards so a later poll can pick it up."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        while job_queue.claim("worker-drain"):
            pass
        job_id, _ = _queued_job(tid, uid)

        got = job_queue.claim("worker-1", is_tenant_blocked=lambda t: t == tid)
        assert got is None

        row = query_one("SELECT status, worker_id FROM jobs WHERE id = %s", (job_id,))
        assert row["status"] == "QUEUED"
        assert row["worker_id"] is None

    def test_an_unblocked_tenant_still_flows(
        self, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        while job_queue.claim("worker-drain"):
            pass
        job_id, _ = _queued_job(tid, uid)

        got = job_queue.claim("worker-1", is_tenant_blocked=lambda t: False)
        assert got is not None and got["job_id"] == job_id

    def test_the_predicate_is_asked_once_per_tenant(
        self, client, test_tenant, registered_user,
    ):
        """It hits the entitlements service and the DB; one call per tenant per
        poll, not one per queued job."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        while job_queue.claim("worker-drain"):
            pass
        for i in range(3):
            _queued_job(tid, uid, name=f"claim-test-{i}")

        asked: list[str] = []

        def spy(t):
            asked.append(t)
            return False

        job_queue.claim("worker-1", is_tenant_blocked=spy)
        assert asked.count(tid) == 1

"""
Plan-limit enforcement regressions:

1. max_dataset_size_mb — uploads used to validate ONLY against the global
   settings.max_upload_size_mb (200 MB), so professional (500) / enterprise
   (2000) tenants were silently capped at 200 MB and the per-plan limit was
   never enforced at all. The plan (or per-tenant quota override) value is now
   authoritative; the global setting is only an infra ceiling when the plan
   defines no limit (None).

2. max_concurrent_jobs — the worker only honored the process-wide
   settings.max_concurrent_jobs; a tenant's per-plan concurrent-job limit was
   never enforced. Dequeue now skips (postpones) jobs of tenants already at
   their RUNNING-job limit while other tenants' jobs keep flowing.
"""

from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one, _json


def _dataset_count(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS c FROM datasets WHERE tenant_id = %s", (tenant_id,)
    )["c"]


def _set_quota(tenant_id: str, quota: dict) -> None:
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json(quota), tenant_id))


def _csv_of_mb(mb: float) -> bytes:
    header = b"sku,date,sales\n"
    return header + b"a" * (int(mb * 1024 * 1024) - len(header))


# ── 1. Dataset size: plan/quota limit enforced on upload ────────────────────

def test_upload_blocked_beyond_plan_dataset_size(
    monkeypatch, make_tenant_user_headers, client,
):
    """A file over the tenant's max_dataset_size_mb (quota override = 1 MB,
    cheap to exercise) must be rejected with the canonical PLAN_LIMIT_REACHED
    error and create NO dataset row."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="analyst", return_tenant_id=True
    )
    _set_quota(tenant_id, {"max_dataset_size_mb": 1})

    before = _dataset_count(tenant_id)
    resp = client.post(
        "/api/v1/datasets",
        files={"file": ("big.csv", _csv_of_mb(2), "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "PLAN_LIMIT_REACHED"
    assert detail["limit"] == "max_dataset_size_mb"
    assert detail["max"] == 1
    assert _dataset_count(tenant_id) == before  # no dataset row leaked through


def test_plan_dataset_size_wins_over_global_infra_ceiling(
    monkeypatch, make_tenant_user_headers, client,
):
    """THE original bug: the global settings.max_upload_size_mb silently capped
    paid plans below their entitlement. With the global ceiling shrunk to 1 MB
    and the tenant's plan limit at 5 MB, a 2 MB upload must SUCCEED — the plan
    limit is authoritative, not the global setting."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    monkeypatch.setattr("backend.config.settings.max_upload_size_mb", 1)

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="analyst", return_tenant_id=True
    )
    _set_quota(tenant_id, {"max_dataset_size_mb": 5})

    content = _csv_of_mb(2)
    resp = client.post(
        "/api/v1/datasets",
        files={"file": ("mid.csv", content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    dataset_id = resp.json()["data"]["id"]

    row = query_one(
        "SELECT size_bytes FROM datasets WHERE id = %s AND tenant_id = %s",
        (dataset_id, tenant_id),
    )
    assert row is not None
    assert row["size_bytes"] == len(content)


def test_global_ceiling_applies_when_plan_limit_is_none(
    monkeypatch, make_tenant_user_headers, client,
):
    """When the resolved plan/quota limit is None (unlimited), the global
    settings.max_upload_size_mb still acts as the hard infra ceiling."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    monkeypatch.setattr("backend.config.settings.max_upload_size_mb", 1)

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="analyst", return_tenant_id=True
    )
    _set_quota(tenant_id, {"max_dataset_size_mb": None})

    before = _dataset_count(tenant_id)
    resp = client.post(
        "/api/v1/datasets",
        files={"file": ("big.csv", _csv_of_mb(2), "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 400  # infra ValueError path, not a plan error
    assert "exceeds" in resp.json()["detail"]
    assert _dataset_count(tenant_id) == before


def test_session_upload_shortcut_enforces_plan_dataset_size(
    monkeypatch, make_tenant_user_headers, client,
):
    """POST /sessions/{id}/upload routes through the same service-level
    enforcement: over-quota upload is blocked, no dataset row is created, and
    the session keeps dataset_id NULL (nothing attached)."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="analyst", return_tenant_id=True
    )
    r = client.post("/api/v1/sessions", json={"name": "upload-cap"}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["data"]["id"]

    _set_quota(tenant_id, {"max_dataset_size_mb": 1})

    before = _dataset_count(tenant_id)
    resp = client.post(
        f"/api/v1/sessions/{session_id}/upload",
        files={"file": ("big.csv", _csv_of_mb(2), "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "PLAN_LIMIT_REACHED"
    assert detail["limit"] == "max_dataset_size_mb"

    assert _dataset_count(tenant_id) == before
    sess = query_one(
        "SELECT dataset_id, status FROM sessions WHERE id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )
    assert sess["dataset_id"] is None       # nothing attached
    assert sess["status"] == "DRAFT"        # no bogus DATASET_LOADED transition


# ── Permission pair for the touched upload endpoint ─────────────────────────

def test_upload_permission_pair(make_tenant_user_headers, client):
    """Viewer is denied (403) with no dataset row created; analyst succeeds
    and the row lands in the DB."""
    viewer_headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="viewer", return_tenant_id=True
    )
    before = _dataset_count(tenant_id)
    r = client.post(
        "/api/v1/datasets",
        files={"file": ("data.csv", _csv_of_mb(0.01), "text/csv")},
        headers=viewer_headers,
    )
    assert r.status_code == 403
    assert _dataset_count(tenant_id) == before  # denied request wrote nothing

    analyst_headers, analyst_tid = make_tenant_user_headers(
        plan="starter", role="analyst", return_tenant_id=True
    )
    r2 = client.post(
        "/api/v1/datasets",
        files={"file": ("data.csv", _csv_of_mb(0.01), "text/csv")},
        headers=analyst_headers,
    )
    assert r2.status_code == 201, r2.text
    dataset_id = r2.json()["data"]["id"]
    row = query_one(
        "SELECT id FROM datasets WHERE id = %s AND tenant_id = %s",
        (dataset_id, analyst_tid),
    )
    assert row is not None


# ── 2. Concurrent jobs: per-tenant limit at dequeue time ────────────────────

def _mk_session(tenant_id: str) -> str:
    session_id = f"sess_{uuid4().hex[:12]}"
    execute(
        "INSERT INTO sessions (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
        (session_id, tenant_id, "plan-limit-test", "pytest"),
    )
    return session_id


def _mk_job(tenant_id: str, session_id: str, status: str, age_seconds: int) -> str:
    """Insert a job aged far into the past so these rows are strictly older
    than any QUEUED job another test (or agent) may have left in the shared DB
    — dequeue order over our rows is then deterministic."""
    job_id = f"job_{uuid4().hex[:12]}"
    execute(
        """INSERT INTO jobs (id, tenant_id, session_id, created_by, status, created_at)
           VALUES (%s, %s, %s, %s, %s, NOW() - (%s * INTERVAL '1 second'))""",
        (job_id, tenant_id, session_id, "pytest", status, age_seconds),
    )
    return job_id


_VERY_OLD = 10 * 365 * 24 * 3600  # ~10 years


def _job_status(job_id: str) -> str:
    return query_one("SELECT status FROM jobs WHERE id = %s", (job_id,))["status"]


def test_dequeue_postpones_tenant_at_concurrent_job_limit(
    monkeypatch, make_tenant_user_headers, client,
):
    """Tenant A (max_concurrent_jobs=1 via quota override) already has one
    RUNNING job, and the OLDEST queued job. Dequeue must skip A's job — which
    stays QUEUED — and hand out tenant B's newer job instead. Once A's running
    job completes, A's queued job is picked up again."""
    from backend.training import queue as job_queue
    from backend.workers.worker import _tenant_at_concurrent_job_limit

    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    _, tenant_a = make_tenant_user_headers(plan="starter", return_tenant_id=True)
    _, tenant_b = make_tenant_user_headers(plan="starter", return_tenant_id=True)
    _set_quota(tenant_a, {"max_concurrent_jobs": 1})

    sess_a = _mk_session(tenant_a)
    sess_b = _mk_session(tenant_b)
    running_a = _mk_job(tenant_a, sess_a, "RUNNING", _VERY_OLD + 300)
    queued_a = _mk_job(tenant_a, sess_a, "QUEUED", _VERY_OLD + 200)  # oldest QUEUED
    queued_b = _mk_job(tenant_b, sess_b, "QUEUED", _VERY_OLD + 100)

    item = job_queue.dequeue(is_tenant_blocked=_tenant_at_concurrent_job_limit)
    assert item is not None
    assert item["job_id"] == queued_b       # A skipped despite being older
    assert item["tenant_id"] == tenant_b
    assert _job_status(queued_a) == "QUEUED"  # postponed, NOT lost/cancelled

    # A's running job finishes -> its queued job becomes eligible again.
    execute("UPDATE jobs SET status = 'COMPLETED' WHERE id = %s", (running_a,))
    # B's job would have been marked RUNNING by the worker after dequeue.
    execute("UPDATE jobs SET status = 'RUNNING' WHERE id = %s", (queued_b,))

    item2 = job_queue.dequeue(is_tenant_blocked=_tenant_at_concurrent_job_limit)
    assert item2 is not None
    assert item2["job_id"] == queued_a


def test_tenant_at_concurrent_job_limit_predicate(
    monkeypatch, make_tenant_user_headers,
):
    """Direct predicate checks: blocked exactly when RUNNING count reaches the
    plan/quota limit; None means unlimited; testing mode disables the check."""
    from backend.workers.worker import _tenant_at_concurrent_job_limit

    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    _, tenant_id = make_tenant_user_headers(plan="starter", return_tenant_id=True)
    sess = _mk_session(tenant_id)

    # Starter plan default is max_concurrent_jobs=2; 1 RUNNING -> below limit.
    _mk_job(tenant_id, sess, "RUNNING", _VERY_OLD)
    assert _tenant_at_concurrent_job_limit(tenant_id) is False

    # 2 RUNNING -> at the limit.
    _mk_job(tenant_id, sess, "RUNNING", _VERY_OLD)
    assert _tenant_at_concurrent_job_limit(tenant_id) is True

    # QUEUED jobs do not count toward the concurrency limit.
    _mk_job(tenant_id, sess, "QUEUED", _VERY_OLD)
    assert _tenant_at_concurrent_job_limit(tenant_id) is True  # still 2 RUNNING

    # Quota override None -> unlimited, never blocked.
    _set_quota(tenant_id, {"max_concurrent_jobs": None})
    assert _tenant_at_concurrent_job_limit(tenant_id) is False

    # Back at the limit, but testing mode bypasses enforcement entirely.
    _set_quota(tenant_id, {"max_concurrent_jobs": 1})
    assert _tenant_at_concurrent_job_limit(tenant_id) is True
    monkeypatch.setattr("backend.config.settings.testing_mode", True)
    assert _tenant_at_concurrent_job_limit(tenant_id) is False

"""
PHASE 2 — Stress and concurrency tests.
Tests system behavior under concurrent load, bulk operations, and sustained traffic.
Run with: pytest tests/test_stress.py -m stress -v

These tests are slower — they use real DB writes under concurrency.
"""

import threading
import time
from contextlib import contextmanager

import psycopg2
import pytest
from uuid import uuid4


@contextmanager
def _pooled_connection_wrapper(wrap):
    """Hand every pooled connection to `wrap` before the caller sees it.

    Lets a test inject a fault into the exact psycopg2 call it cares about
    (`cursor()`, `commit()`, …) without touching backend.db internals that only
    exist in one version of the code. `putconn` is unwrapped again because the
    pool keys checked-out connections by `id(conn)`.
    """
    import backend.db.connection as db_conn

    pool = db_conn._pool
    real_getconn, real_putconn = pool.getconn, pool.putconn

    def getconn(*args, **kwargs):
        return wrap(real_getconn(*args, **kwargs))

    def putconn(conn=None, *args, **kwargs):
        return real_putconn(getattr(conn, "_inner", conn), *args, **kwargs)

    pool.getconn, pool.putconn = getconn, putconn
    try:
        yield
    finally:
        pool.getconn, pool.putconn = real_getconn, real_putconn


class _WrappedConn:
    """Base for fault-injecting connection wrappers — forwards everything."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _log_rows(job_id: str) -> list[dict]:
    from backend.db.connection import query
    return query(
        "SELECT message FROM training_logs WHERE job_id = %s ORDER BY id",
        (job_id,),
    )


@pytest.fixture
def db_pool():
    """Guarantees an initialized connection pool.

    The tests below drive backend.db directly instead of going through the API,
    so they must not depend on some earlier test having booted the FastAPI app
    (whose lifespan is what normally calls init_pool). Same sizing as
    backend/main.py, so pool behavior under concurrency is the real one.
    """
    import backend.db.connection as db_conn
    from backend.config import settings

    if db_conn._pool is None:
        db_conn.init_pool(settings.database_url, min_conn=5, max_conn=20)
    return db_conn._pool


def _new_job(tenant_id: str, name: str) -> tuple[str, str]:
    """A session + a job that no worker may claim.

    `training_logs.job_id` has an FK to `jobs`, so a log test needs a real job —
    but a job left in QUEUED is claimable by EVERY worker pointed at this
    database, not just the one in this process. conftest patches
    `backend.workers.worker.start`, which silences the in-process worker and
    nothing else: a locally running dev server (`uvicorn backend.main:app`,
    same DATABASE_URL) polls `jobs WHERE status = 'QUEUED'` across all tenants,
    marks this job RUNNING and lets `workers/runner.py::_emit` append its own
    lines under this exact job_id. That is measured, not hypothetical — it is
    why `test_concurrent_log_appends` read 22 lines after writing 20: the two
    extra lines were the runner's "[init] Building engine config..." and
    "[load] Loading dataset..." before the job failed for want of a dataset.

    Withdrawing the job from the queue up front makes the test own its log
    exclusively, whatever else is running against this database.
    """
    from backend.sessions.service import create_session
    from backend.training import queue as job_queue
    from backend.training.job_service import create_job, get_job

    session = create_session(tenant_id, "usr_test", name)
    job = create_job(tenant_id, session["id"], "usr_test")
    job_queue.remove(job["id"])
    claimable = get_job(tenant_id, job["id"])["status"]
    assert claimable == "CANCELLED", (
        f"job left in status {claimable!r} — a foreign worker can still run it "
        f"and write to this test's log"
    )
    return session["id"], job["id"]


@pytest.mark.stress
@pytest.mark.slow
class TestConcurrentSessionCreation:
    def test_10_concurrent_session_creates(self, client, auth_headers):
        """10 threads creating sessions simultaneously — no data races."""
        results = []
        errors = []

        def create():
            try:
                resp = client.post(
                    "/api/v1/sessions",
                    json={"name": f"concurrent-{uuid4().hex[:6]}"},
                    headers=auth_headers,
                )
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Errors during concurrent create: {errors}"
        successes = [r for r in results if r == 201]
        assert len(successes) == 10, f"Expected 10 successes, got {successes}"

    def test_concurrent_logins_same_user(self, client, registered_user):
        """Multiple simultaneous logins for the same user."""
        tokens = []
        errors = []
        lock = threading.Lock()

        def login():
            try:
                resp = client.post("/api/v1/auth/login", json={
                    "email": registered_user["email"],
                    "password": registered_user["password"],
                })
                if resp.status_code == 200:
                    with lock:
                        tokens.append(resp.json()["data"]["access_token"])
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=login) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        assert len(tokens) == 5
        # All tokens should be distinct
        assert len(set(tokens)) == 5, "Each login should produce a unique access token"

    def test_concurrent_reads_on_same_session(self, client, auth_headers, completed_session):
        """Many concurrent GET requests for the same session should all succeed."""
        sid = completed_session["id"]
        results = []
        errors = []

        def read():
            try:
                resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        assert all(r == 200 for r in results), f"Some reads failed: {results}"


@pytest.mark.stress
@pytest.mark.slow
class TestBulkOperations:
    def test_create_and_list_many_sessions(self, client, auth_headers):
        """Create 15 sessions, then verify list pagination works correctly."""
        session_ids = []
        for i in range(15):
            resp = client.post(
                "/api/v1/sessions",
                json={"name": f"bulk-{i:03d}-{uuid4().hex[:4]}"},
                headers=auth_headers,
            )
            assert resp.status_code == 201
            session_ids.append(resp.json()["data"]["id"])

        # First page
        page1 = client.get("/api/v1/sessions?skip=0&limit=10", headers=auth_headers)
        assert page1.status_code == 200
        body1 = page1.json()["data"]
        assert len(body1["items"]) == 10
        assert body1["total"] >= 15

        # Second page
        page2 = client.get("/api/v1/sessions?skip=10&limit=10", headers=auth_headers)
        assert page2.status_code == 200
        body2 = page2.json()["data"]
        assert len(body2["items"]) >= 5

        # No overlap between pages
        ids1 = {s["id"] for s in body1["items"]}
        ids2 = {s["id"] for s in body2["items"]}
        assert len(ids1 & ids2) == 0

    def test_upload_multiple_datasets(self, client, auth_headers):
        """Upload 5 CSV files and list them all."""
        from tests.fixtures.synthetic_data import generate_csv_bytes

        ds_ids = []
        for i in range(5):
            data = generate_csv_bytes(n_skus=2, n_days=10, seed=i)
            resp = client.post(
                "/api/v1/datasets",
                files={"file": (f"bulk_{i}.csv", data, "text/csv")},
                headers=auth_headers,
            )
            assert resp.status_code == 201
            ds_ids.append(resp.json()["data"]["id"])

        list_resp = client.get("/api/v1/datasets", headers=auth_headers)
        assert list_resp.status_code == 200
        listed_ids = {d["id"] for d in list_resp.json()["data"]["items"]}
        assert all(did in listed_ids for did in ds_ids)

    def test_bulk_session_config_writes(self, client, auth_headers, test_session, uploaded_dataset):
        """Write and read all 6 config steps multiple times (simulate rapid wizard navigation)."""
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )

        for _ in range(5):
            client.post(
                f"/api/v1/sessions/{sid}/configure/columns",
                json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
                headers=auth_headers,
            )

        resp = client.get(f"/api/v1/sessions/{sid}/configure/columns", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["date_column"] == "date"

    def test_large_bulk_import_does_not_freeze_event_loop(self, client, auth_headers):
        """
        Regression test for commit 72a8ec4: bulk_import used to do one synchronous
        psycopg2 round-trip per CSV row directly inside an async handler, which froze
        the asyncio event loop — and with it /health — for every tenant, for the whole
        duration of the import (confirmed: a 10k-row file blocked /health). The fix
        offloads the blocking work via asyncio.to_thread (api/v1/inventory.py:166).

        This starts a large import on a background thread and, while it's in flight,
        repeatedly hits /health on the same shared TestClient (which serves all
        requests off a single background event loop) — if the import were still
        blocking that loop, /health calls would queue up behind it and spike in
        latency. With the fix, /health latency should stay low throughout.
        """
        import time
        from uuid import uuid4 as _uuid4

        n_rows = 3000
        lines = ["sku,current_stock,lead_time_days"]
        for i in range(n_rows):
            lines.append(f"FREEZE-TEST-{_uuid4().hex[:10]}-{i},10,5")
        csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")

        # Baseline BEFORE the import, on this machine, in this state. The
        # assertion below is relative to it: an absolute ceiling measures how
        # busy the machine is, and this test is about whether the event loop is
        # blocked. Whatever slows the baseline slows the probes equally, while a
        # blocked loop shows up as a spike no ambient load can explain.
        idle_latencies = []
        for _ in range(10):
            start = time.monotonic()
            client.get("/health")
            idle_latencies.append(time.monotonic() - start)
        idle = sorted(idle_latencies)[len(idle_latencies) // 2]

        import_done = threading.Event()
        import_result = {}

        def do_import():
            started = time.monotonic()
            try:
                resp = client.post(
                    "/api/v1/inventory/bulk",
                    files={"file": ("large.csv", csv_bytes, "text/csv")},
                    headers=auth_headers,
                )
                import_result["status_code"] = resp.status_code
                import_result["body"] = resp.text[:400]
            except Exception as exc:  # noqa: BLE001 - reported, see the assert below
                import_result["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                import_result["seconds"] = time.monotonic() - started
                import_done.set()

        import_thread = threading.Thread(target=do_import)
        import_thread.start()

        health_latencies = []
        # Poll /health while the import is in flight. The sleep matters: without
        # it this loop is a busy spin that fights the import thread for the GIL,
        # which is exactly the stall the test claims to be measuring.
        while not import_done.is_set():
            start = time.monotonic()
            client.get("/health")
            health_latencies.append(time.monotonic() - start)
            time.sleep(0.05)
            if len(health_latencies) > 2000:  # safety cap in case import_done is missed
                break

        import_thread.join(timeout=120)
        # Distinguish the three ways this can go wrong. Asserting only on
        # status_code reported all of them as the same opaque `None != 200`.
        assert not import_result.get("error"), (
            f"Bulk import raised instead of responding: {import_result['error']}"
        )
        assert import_result.get("status_code") is not None, (
            "Bulk import never returned — still running after the 120 s join timeout"
        )
        assert import_result["status_code"] == 200, (
            f"Bulk import returned {import_result['status_code']}: {import_result.get('body')}"
        )

        assert health_latencies, "Import finished before any /health probe ran — increase n_rows"

        # 2 s of headroom OVER the idle baseline: the same budget the absolute
        # version used, minus the machine. A blocked loop parks /health for the
        # whole import (seconds), so it cannot slip under this.
        budget = idle + 2.0
        assert max(health_latencies) < budget, (
            f"/health latency spiked to {max(health_latencies):.2f}s while bulk import "
            f"was in flight, against an idle baseline of {idle:.2f}s — the event loop "
            f"appears to be blocked again (regression of 72a8ec4). The import itself "
            f"took {import_result['seconds']:.1f}s over {len(health_latencies)} probes"
        )


@pytest.mark.stress
@pytest.mark.slow
class TestConcurrentDBWrites:
    def test_concurrent_session_store_writes(self, db_pool, test_tenant):
        """Multiple threads writing different fields to the same session_configs row."""
        from backend.db import session_store
        from backend.sessions.service import create_session
        errors = []

        s = create_session(test_tenant["id"], "usr_test", "concurrent-writes")
        tid = test_tenant["id"]
        sid = s["id"]

        def write_field(field: str, value: dict):
            try:
                session_store.set_field(tid, sid, field, value)
            except Exception as e:
                errors.append(f"{field}: {e}")

        threads = [
            threading.Thread(target=write_field, args=(f, v))
            for f, v in [
                ("columns_cfg", {"date_column": "date"}),
                ("features_cfg", {"lags": [1, 7]}),
                ("models_cfg", {"selected_models": ["lightgbm"]}),
                ("validation_cfg", {"train_ratio": 0.8}),
                ("business_cfg", {"service_level": 0.95}),
            ]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent write errors: {errors}"
        cfg = session_store.get_all_configs(tid, sid)
        assert cfg["columns_cfg"]["date_column"] == "date"
        assert cfg["features_cfg"]["lags"] == [1, 7]

    def test_concurrent_log_appends(self, db_pool, test_tenant):
        """Multiple threads appending logs to the same job — no duplicates or losses."""
        from backend.db import session_store

        tid = test_tenant["id"]
        sid, jid = _new_job(tid, "concurrent-logs")
        errors = []

        def append(msg: str):
            try:
                session_store.append_log(tid, sid, jid, msg)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=append, args=(f"log line {i}",))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent log errors: {errors}"
        lines = session_store.get_logs(tid, sid, jid)
        # Exact multiset, not just the count: a duplicated write and a lost one
        # are opposite bugs, and `len(...) == 20` alone reports neither of them
        # legibly (this test was seen returning 22).
        assert sorted(lines) == sorted(f"log line {i}" for i in range(20)), (
            f"Expected exactly the 20 written lines, got {len(lines)}: {sorted(lines)}"
        )

    def test_log_fixture_job_is_never_left_in_the_shared_queue(self, db_pool, test_tenant):
        """A job a test creates must not be runnable by anybody else.

        The job queue is the `jobs` table, so it is shared by every process
        pointed at this database — not just this pytest process. conftest's
        `mock.patch("backend.workers.worker.start")` only silences the worker
        living inside pytest; a dev server started with the same DATABASE_URL
        keeps polling `jobs WHERE status = 'QUEUED'` for all tenants and will
        happily run a job a test just created, appending `runner._emit` lines
        under that job_id while the test is still writing its own.

        This asserts the exact precondition that made `test_concurrent_log_appends`
        read 22 lines after writing 20: the job must be out of the queue before
        the test uses its log.
        """
        from backend.training import queue as job_queue

        tid = test_tenant["id"]
        _sid, jid = _new_job(tid, "queue-isolation")

        queued = {row["job_id"] for row in job_queue.peek()}
        assert jid not in queued, (
            "test job is sitting in the shared QUEUED queue — any worker "
            "process on this database may run it and write to its training log"
        )
        claimed = job_queue.dequeue()
        assert claimed is None or claimed["job_id"] != jid, (
            "the worker's own dequeue() hands out this test's job"
        )

    def test_write_is_not_duplicated_when_the_commit_ack_is_lost(self, db_pool, test_tenant):
        """A statement whose COMMIT reached the server must never be re-run.

        db/connection.py retries a statement once when the pooled connection
        turns out to be dead — Supabase's pooler drops them silently. That
        retry used to fire on ANY OperationalError, including one raised by
        `conn.commit()` itself, where the server may already have applied the
        write and only the acknowledgement was lost. One `append_log` call then
        wrote two rows; the same retry sits under every INSERT and every
        non-idempotent UPDATE in the backend.

        The fault injected here is exactly that: the commit lands, the
        acknowledgement does not.
        """
        from backend.db import session_store

        tid = test_tenant["id"]
        sid, jid = _new_job(tid, "commit-ack-lost")
        state = {"armed": True}

        class AckLossConn(_WrappedConn):
            def commit(self):
                self._inner.commit()          # the server has applied it
                if state["armed"]:
                    state["armed"] = False
                    raise psycopg2.OperationalError(
                        "server closed the connection unexpectedly"
                    )

        raised = None
        with _pooled_connection_wrapper(lambda c: AckLossConn(c) if state["armed"] else c):
            try:
                session_store.append_log(tid, sid, jid, "written once")
            except psycopg2.OperationalError as exc:
                raised = exc

        assert not state["armed"], "fault was never triggered — test proves nothing"
        rows = _log_rows(jid)
        assert [r["message"] for r in rows] == ["written once"], (
            f"one append_log call wrote {len(rows)} rows: "
            f"{[r['message'] for r in rows]}"
        )
        assert raised is not None, (
            "a write whose commit outcome is unknown was reported as successful"
        )

    def test_dead_pooled_connection_is_still_retried(self, db_pool, test_tenant):
        """The narrow, safe half of the retry must survive the fix.

        When the connection dies while the statement is in flight, no COMMIT
        was ever sent, so nothing can have been applied — retrying once on a
        fresh connection is correct and must still happen, exactly once.
        """
        from backend.db import session_store

        tid = test_tenant["id"]
        sid, jid = _new_job(tid, "dead-pooled-conn")
        state = {"armed": True}

        class DeadOnFirstUseConn(_WrappedConn):
            def cursor(self, *args, **kwargs):
                if state["armed"]:
                    state["armed"] = False
                    raise psycopg2.OperationalError(
                        "server closed the connection unexpectedly"
                    )
                return self._inner.cursor(*args, **kwargs)

        with _pooled_connection_wrapper(DeadOnFirstUseConn):
            session_store.append_log(tid, sid, jid, "survived the retry")

        assert not state["armed"], "fault was never triggered — test proves nothing"
        rows = _log_rows(jid)
        assert [r["message"] for r in rows] == ["survived the retry"], (
            f"expected exactly one row after a safe retry, got {[r['message'] for r in rows]}"
        )

    def test_writes_beyond_pool_size_are_not_dropped(self, db_pool, test_tenant):
        """More concurrent writers than pooled connections must not lose writes.

        psycopg2's ThreadedConnectionPool refuses instead of queueing: the
        moment every connection is checked out it raises
        PoolError("connection pool exhausted"). With max_conn=20 in main.py,
        a burst of concurrent writes lost rows outright even though each write
        holds its connection for a millisecond.
        """
        import backend.db.connection as db_conn
        from backend.config import settings
        from backend.db import session_store

        tid = test_tenant["id"]
        sid, jid = _new_job(tid, "pool-exhaustion")
        n_writers = 8
        errors = []

        def append(msg: str):
            try:
                session_store.append_log(tid, sid, jid, msg)
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")

        original_pool = db_conn._pool
        db_conn.init_pool(settings.database_url, min_conn=1, max_conn=2)
        small_pool = db_conn._pool
        try:
            threads = [
                threading.Thread(target=append, args=(f"burst {i}",))
                for i in range(n_writers)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert all(not t.is_alive() for t in threads), "a writer never finished"
        finally:
            db_conn._pool = original_pool
            small_pool.closeall()

        assert not errors, f"writes failed while the pool was saturated: {errors}"
        rows = _log_rows(jid)
        assert sorted(r["message"] for r in rows) == sorted(
            f"burst {i}" for i in range(n_writers)
        ), f"expected {n_writers} rows, got {sorted(r['message'] for r in rows)}"


@pytest.mark.stress
@pytest.mark.slow
class TestResponseTimes:
    def test_session_list_responds_under_2s(self, client, auth_headers):
        start = time.time()
        resp = client.get("/api/v1/sessions", headers=auth_headers)
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 2.0, f"Session list took {elapsed:.2f}s — too slow"

    def test_health_endpoint_responds_under_500ms(self, client):
        start = time.time()
        resp = client.get("/health")
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 0.5, f"Health check took {elapsed:.2f}s"

    def test_login_is_not_doing_anything_expensive(self, client, registered_user):
        """Login's cost measured AGAINST the harness floor, not the wall clock.

        The old assertion was `elapsed < 2.0`. Login really takes ~350 ms
        (measured against a live server), but inside a 25-minute suite on a
        loaded machine the same call reads 2.9 s — so this failed for machine
        load, not for anything about login, and it did so on most full runs.
        Raising the ceiling would only have moved the lie.

        Comparing against `/health` under the same conditions cancels the
        ambient load out: whatever slows one slows the other. What survives is
        the work login actually does — one bcrypt verify plus a couple of
        queries — so it still fails for the reason it exists: an N+1 creeping
        into the login path, a second hash, a synchronous remote call.

        Best-of-3 on both sides; the minimum is the sample least contaminated
        by a scheduler hiccup, and using it for both keeps it fair.
        """
        def best_of_3(call):
            times = []
            for _ in range(3):
                start = time.time()
                resp = call()
                times.append(time.time() - start)
                assert resp.status_code == 200, resp.text
            return min(times)

        floor = best_of_3(lambda: client.get("/health"))
        login = best_of_3(lambda: client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        }))

        # bcrypt at the default cost is a few hundred ms and is the bulk of it.
        # Verified this can fail: with the budget set to 0 it does.
        assert login < floor + 2.0, (
            f"login {login:.2f}s vs harness floor {floor:.2f}s — "
            f"login is doing {login - floor:.2f}s of its own work"
        )

    def test_forecast_series_responds_under_1s(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        start = time.time()
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 1.0, f"Forecast series took {elapsed:.2f}s"

"""
PHASE 2 — Stress and concurrency tests.
Tests system behavior under concurrent load, bulk operations, and sustained traffic.
Run with: pytest tests/test_stress.py -m stress -v

These tests are slower — they use real DB writes under concurrency.
"""

import threading
import time
import pytest
from uuid import uuid4


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
        lines = ["sku,stock_actual,lead_time_dias"]
        for i in range(n_rows):
            lines.append(f"FREEZE-TEST-{_uuid4().hex[:10]}-{i},10,5")
        csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")

        import_done = threading.Event()
        import_result = {}

        def do_import():
            resp = client.post(
                "/api/v1/inventory/bulk",
                files={"file": ("large.csv", csv_bytes, "text/csv")},
                headers=auth_headers,
            )
            import_result["status_code"] = resp.status_code
            import_done.set()

        import_thread = threading.Thread(target=do_import)
        import_thread.start()

        health_latencies = []
        # Poll /health repeatedly while the import is in flight.
        while not import_done.is_set():
            start = time.monotonic()
            client.get("/health")
            health_latencies.append(time.monotonic() - start)
            if len(health_latencies) > 200:  # safety cap in case import_done is missed
                break

        import_thread.join(timeout=120)
        assert import_result.get("status_code") == 200

        assert health_latencies, "Import finished before any /health probe ran — increase n_rows"
        assert max(health_latencies) < 2.0, (
            f"/health latency spiked to {max(health_latencies):.2f}s while bulk import "
            f"was in flight — the event loop appears to be blocked again (regression of 72a8ec4)"
        )


@pytest.mark.stress
@pytest.mark.slow
class TestConcurrentDBWrites:
    def test_concurrent_session_store_writes(self, test_tenant):
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

    def test_concurrent_log_appends(self, test_tenant):
        """Multiple threads appending logs to the same job — no duplicates or losses."""
        from backend.db import session_store
        from backend.sessions.service import create_session
        from backend.training.job_service import create_job

        s = create_session(test_tenant["id"], "usr_test", "concurrent-logs")
        j = create_job(test_tenant["id"], s["id"], "usr_test")
        tid = test_tenant["id"]
        sid = s["id"]
        jid = j["id"]
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
        assert len(lines) == 20


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

    def test_login_responds_under_2s(self, client, registered_user):
        start = time.time()
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 2.0, f"Login took {elapsed:.2f}s"

    def test_forecast_series_responds_under_1s(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        start = time.time()
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 1.0, f"Forecast series took {elapsed:.2f}s"

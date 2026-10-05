"""`GET /training/run-durations`: median / p95 training time per catalogue size and
per granularity, read from the lineage manifests of the tenant's last N runs."""

from uuid import uuid4

import pytest

from backend.db.connection import _json, execute

URL = "/api/v1/training/run-durations"


@pytest.fixture(autouse=True)
def _db_pool(client):
    return client


def _manifest(tenant_id, seconds, series, granularity="weekly", outcome="COMPLETED",
              age_minutes=0):
    manifest = {
        "outcome": outcome,
        "session": {"granularity": granularity},
        "timing": {"duration_seconds": seconds},
        "counts": {"skus_forecast": series},
    }
    execute(
        """INSERT INTO session_manifests (id, session_id, tenant_id, outcome, manifest, created_at)
           VALUES (%s, %s, %s, %s, %s, NOW() - (%s || ' minutes')::interval)""",
        (f"mf_{uuid4().hex[:10]}", f"s_{uuid4().hex[:6]}", tenant_id, outcome,
         _json(manifest), age_minutes),
    )


class TestAggregate:
    def test_median_and_p95_by_size_bucket_and_granularity(
        self, client, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        for i, s in enumerate([10, 20, 30, 40, 100]):          # small catalogue, weekly
            _manifest(tid, s, 30, "weekly", age_minutes=i)
        for i, s in enumerate([200, 400]):                      # mid catalogue, daily
            _manifest(tid, s, 300, "daily", age_minutes=10 + i)

        r = client.get(URL, headers=viewer_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["completed_runs"] == 7 and d["failed_runs"] == 0
        small = next(b for b in d["by_size"] if b["min_series"] == 1)
        assert (small["max_series"], small["runs"]) == (50, 5)
        assert small["median_seconds"] == 30 and small["p95_seconds"] == 100
        mid = next(b for b in d["by_size"] if b["min_series"] == 201)
        assert mid["runs"] == 2 and mid["median_seconds"] == 300 and mid["max_seconds"] == 400
        gran = {g["granularity"]: g for g in d["by_granularity"]}
        assert gran["weekly"]["runs"] == 5 and gran["daily"]["runs"] == 2
        assert d["overall"]["runs"] == 7 and d["overall"]["max_seconds"] == 400

    def test_failed_runs_are_counted_not_averaged(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        _manifest(tid, 50, 30)
        _manifest(tid, 3, 30, outcome="FAILED")
        d = client.get(URL, headers=viewer_headers).json()["data"]
        assert d["completed_runs"] == 1 and d["failed_runs"] == 1
        assert d["overall"]["median_seconds"] == 50

    def test_only_the_last_n_runs_are_read(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        _manifest(tid, 1000, 30, age_minutes=500)               # old and slow
        for i in range(3):
            _manifest(tid, 10, 30, age_minutes=i)
        d = client.get(URL + "?limit=3", headers=viewer_headers).json()["data"]
        assert d["window_runs"] == 3 and d["overall"]["max_seconds"] == 10

    def test_a_run_without_a_duration_is_untimed_never_zero(
        self, client, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        _manifest(tid, None, 30)
        _manifest(tid, 20, 30)
        d = client.get(URL, headers=viewer_headers).json()["data"]
        assert d["untimed_runs"] == 1 and d["completed_runs"] == 1
        assert d["overall"]["median_seconds"] == 20

    def test_no_runs_is_an_empty_answer(self, client, viewer_headers, test_tenant):
        d = client.get(URL, headers=viewer_headers).json()["data"]
        assert d["overall"] is None and d["by_size"] == [] and d["by_granularity"] == []

    def test_another_tenants_runs_are_never_counted(
        self, client, test_tenant, make_tenant_user_headers,
    ):
        _manifest(test_tenant["id"], 99, 30)
        other = make_tenant_user_headers(role="viewer")
        d = client.get(URL, headers=other).json()["data"]
        assert d["completed_runs"] == 0 and d["overall"] is None

    def test_it_requires_authentication_and_bounds_the_limit(self, client, viewer_headers):
        assert client.get(URL).status_code in (401, 403)
        assert client.get(URL + "?limit=0", headers=viewer_headers).status_code == 422
        assert client.get(URL + "?limit=100000", headers=viewer_headers).status_code == 422

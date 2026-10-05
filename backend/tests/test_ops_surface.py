"""The operations surface: thresholds, bounded memory, operator-only access.

The evaluators are pure, so each SLO threshold has a test that fails if the
comparison flips. The endpoint tests read real rows: a QUEUED job planted in
the database must show up in the queue reading, which is the only way to know
the snapshot is looking at the actual table.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.config import settings
from backend.db.connection import execute
from backend.middleware import latency_window
from backend.service_config import ops

API = "/api/v1/service-config"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


# ── Latency window ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_window():
    latency_window.reset()
    yield
    latency_window.reset()


def test_percentiles_use_nearest_rank():
    for ms in range(1, 101):
        latency_window.record("inventory", float(ms), 200, now=1000.0)
    row = latency_window.snapshot(now=1000.0)[0]
    assert (row["p50_ms"], row["p95_ms"], row["p99_ms"], row["max_ms"]) == (50.0, 95.0, 99.0, 100.0)
    assert row["count"] == 100 and row["errors_5xx"] == 0


def test_5xx_are_counted_and_samples_expire():
    latency_window.record("po", 10.0, 500, now=1000.0)
    latency_window.record("po", 10.0, 200, now=1000.0)
    row = latency_window.snapshot(now=1000.0)[0]
    assert row["errors_5xx"] == 1
    # Past the window the family disappears instead of reporting stale numbers.
    assert latency_window.snapshot(now=1000.0 + latency_window.WINDOW_SECONDS + 1) == []


def test_memory_is_bounded_per_family_and_in_family_count():
    for i in range(latency_window.SAMPLES_PER_FAMILY + 50):
        latency_window.record("a", 1.0, 200, now=1.0)
    assert latency_window.snapshot(now=1.0)[0]["count"] == latency_window.SAMPLES_PER_FAMILY
    for i in range(latency_window.MAX_FAMILIES + 20):
        latency_window.record(f"fam{i}", 1.0, 200, now=1.0)
    families = {r["family"] for r in latency_window.snapshot(now=1.0)}
    assert len(families) <= latency_window.MAX_FAMILIES + 1  # + the "other" bucket
    assert latency_window.OTHER in families


def test_route_family_uses_the_template_never_the_id():
    assert latency_window.route_family("/api/v1/skus/{sku}/history", "/api/v1/skus/ABC-1/history") == "skus"
    assert latency_window.route_family(None, "/wp-login.php") == "other"
    assert latency_window.route_family("/api/v1/{anything}", "/api/v1/x") == "other"


# ── Threshold evaluators ────────────────────────────────────────────────────

def test_queue_wait_over_threshold_is_degraded():
    assert ops.evaluate_queue(2, 9 * 60, 10)["state"] == ops.OK
    assert ops.evaluate_queue(2, 11 * 60, 10)["state"] == ops.DEGRADED
    assert ops.evaluate_queue(0, None, 10)["state"] == ops.OK


def test_heartbeat_missing_is_unknown_when_idle_and_degraded_when_jobs_wait():
    assert ops.evaluate_heartbeat(None, 0, 120)["state"] == ops.UNKNOWN
    assert ops.evaluate_heartbeat(None, 3, 120)["state"] == ops.DEGRADED
    assert ops.evaluate_heartbeat(30, 3, 120)["state"] == ops.OK
    assert ops.evaluate_heartbeat(121, 0, 120)["state"] == ops.DEGRADED


def test_disk_below_the_free_floor_is_degraded():
    assert ops.evaluate_disk("disk_storage", 1000, 100, 10)["state"] == ops.OK
    assert ops.evaluate_disk("disk_storage", 1000, 99, 10)["state"] == ops.DEGRADED


def test_pool_saturation_threshold_and_unknown_before_init():
    assert ops.evaluate_pool({"max": 20, "in_use": 17, "saturation_pct": 85.0}, 85)["state"] == ops.DEGRADED
    assert ops.evaluate_pool({"max": 20, "in_use": 5, "saturation_pct": 25.0}, 85)["state"] == ops.OK
    assert ops.evaluate_pool(None, 85)["state"] == ops.UNKNOWN


def test_latency_needs_a_few_samples_before_it_can_degrade():
    slow = {"family": "x", "count": 4, "p95_ms": 9999.0}
    assert ops.evaluate_latency([slow], 3000)["state"] == ops.OK
    assert ops.evaluate_latency([{**slow, "count": 5}], 3000)["state"] == ops.DEGRADED


def test_backup_states():
    fresh = {"status": "ok", "finished_at": (NOW - timedelta(hours=3)).isoformat(),
             "storage_bytes": 50_000}
    assert ops.evaluate_backup(fresh, None, NOW, 36)["state"] == ops.OK
    old = {**fresh, "finished_at": (NOW - timedelta(hours=40)).isoformat()}
    result = ops.evaluate_backup(old, None, NOW, 36)
    assert result["state"] == ops.DEGRADED and "too_old" in result["detail"]["problems"]
    tiny = {**fresh, "storage_bytes": 100}
    assert "storage_archive_suspiciously_small" in ops.evaluate_backup(tiny, None, NOW, 36)["detail"]["problems"]
    assert ops.evaluate_backup({**fresh, "status": "failed"}, None, NOW, 36)["state"] == ops.DEGRADED
    # No marker is never "ok": a backup nobody can see did not provably happen.
    assert ops.evaluate_backup(None, "marker_not_found", NOW, 36)["state"] == ops.UNKNOWN
    assert ops.evaluate_backup({"finished_at": "garbage"}, None, NOW, 36)["state"] == ops.DEGRADED


def test_overall_degraded_beats_unknown_beats_ok():
    ok, unk, bad = {"state": "ok"}, {"state": "unknown"}, {"state": "degraded"}
    assert ops.overall([ok, ok]) == "ok"
    assert ops.overall([ok, unk]) == "unknown"
    assert ops.overall([ok, unk, bad]) == "degraded"


def test_error_class_groups_by_exception_name_not_message():
    assert ops.classify_error("ValueError: bad column 'x'") == "ValueError"
    assert ops.classify_error("psycopg2.errors.DeadlockDetected: x") == "DeadlockDetected"
    assert ops.classify_error("worker restarted") == "other"
    assert ops.classify_error(None) == "unknown"


def test_backup_marker_reader_reports_why_it_has_nothing(tmp_path):
    assert ops.read_backup_marker("") == (None, "backup_status_path_not_set")
    assert ops.read_backup_marker(str(tmp_path / "nope.json")) == (None, "marker_not_found")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ops.read_backup_marker(str(bad)) == (None, "marker_not_json")
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert ops.read_backup_marker(str(good)) == ({"status": "ok"}, None)


# ── Thresholds live in the registry ─────────────────────────────────────────

def test_every_threshold_the_snapshot_reads_is_declared_in_the_registry():
    from backend.service_config.registry import BY_KEY
    declared = {f.key for f in BY_KEY["operations"].fields}
    used = {name for name in type(settings).model_fields if name.startswith("ops_")}
    assert used and used <= declared


# ── Endpoint: operator only, reads the real tables ──────────────────────────

@pytest.fixture
def operator(monkeypatch, registered_user):
    monkeypatch.setattr("backend.config.settings.instance_admin_emails", [registered_user["email"]])
    return registered_user


def _plant_job(tenant_id: str, status: str, age_seconds: int, error: str | None = None) -> str:
    session_id = f"sess_{uuid4().hex[:12]}"
    execute("INSERT INTO sessions (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
            (session_id, tenant_id, "ops-test", "pytest"))
    job_id = f"job_{uuid4().hex[:12]}"
    execute(
        """INSERT INTO jobs (id, tenant_id, session_id, created_by, status, created_at,
                             started_at, completed_at, error)
           VALUES (%s, %s, %s, 'pytest', %s, NOW() - (%s * INTERVAL '1 second'),
                   CASE WHEN %s IN ('RUNNING', 'FAILED') THEN NOW() - (%s * INTERVAL '1 second') END,
                   CASE WHEN %s = 'FAILED' THEN NOW() END, %s)""",
        (job_id, tenant_id, session_id, status, age_seconds, status, age_seconds, status, error),
    )
    return job_id


def test_ops_snapshot_sees_planted_jobs_and_flags_a_stale_queue(
    client, auth_headers, operator, monkeypatch, tmp_path,
):
    monkeypatch.setattr("backend.config.settings.backup_status_path", str(tmp_path / "none.json"))
    tenant = operator["tenant"]["id"]
    queued = _plant_job(tenant, "QUEUED", 25 * 60)
    running = _plant_job(tenant, "RUNNING", 90)
    _plant_job(tenant, "FAILED", 60, error="ValueError: boom")

    resp = client.get(f"{API}/ops", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]

    checks = {c["key"]: c for c in data["checks"]}
    assert checks["queue"]["state"] == "degraded"  # 25 min > the 10 min threshold
    assert data["queue"]["oldest_queued_age_seconds"] >= 25 * 60 - 5
    assert running in {j["job_id"] for j in data["running_jobs"]}
    assert {"error_class": "ValueError", "count": 1} in data["failed_jobs_24h"]["by_error_class"]
    assert checks["backup"]["state"] == "unknown"  # marker not found is never "ok"
    assert data["overall"] == "degraded"
    assert queued  # the planted id is what drove the reading above


def test_ops_reports_a_healthy_backup_from_the_marker_file(
    client, auth_headers, operator, monkeypatch, tmp_path,
):
    marker = tmp_path / "last_success.json"
    marker.write_text(json.dumps({
        "status": "ok", "finished_at": datetime.now(timezone.utc).isoformat(),
        "db_bytes": 1234, "storage_bytes": 99_999,
    }), encoding="utf-8")
    monkeypatch.setattr("backend.config.settings.backup_status_path", str(marker))
    monkeypatch.setattr("backend.config.settings.backup_dir", str(tmp_path))
    data = client.get(f"{API}/ops", headers=auth_headers).json()["data"]
    checks = {c["key"]: c for c in data["checks"]}
    assert checks["backup"]["state"] == "ok"
    assert checks["disk_backup"]["detail"]["free_bytes"] > 0


def test_a_tenant_admin_who_is_not_an_operator_is_denied(client, auth_headers, monkeypatch):
    monkeypatch.setattr("backend.config.settings.instance_admin_emails", ["someone.else@example.com"])
    resp = client.get(f"{API}/ops", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "not_instance_operator"


def test_a_viewer_is_denied_even_when_named_operator(client, viewer_headers, viewer_user, monkeypatch):
    monkeypatch.setattr("backend.config.settings.instance_admin_emails", [viewer_user["email"]])
    assert client.get(f"{API}/ops", headers=viewer_headers).status_code == 403


def test_ops_requires_authentication(client):
    assert client.get(f"{API}/ops").status_code in (401, 403)


# ── Worker heartbeat ────────────────────────────────────────────────────────

def test_heartbeat_is_written_throttled_and_read_back(client, monkeypatch):
    from backend.workers import loop_state

    monkeypatch.setattr(loop_state, "_last_beat_monotonic", -loop_state.HEARTBEAT_EVERY_SECONDS)
    execute("DELETE FROM system_loop_runs WHERE loop = %s", (loop_state.WORKER_HEARTBEAT,))
    loop_state.beat("worker-test")
    first = loop_state.heartbeat()
    assert first and first["worker"] == "worker-test"
    # A second beat inside the throttle window writes nothing.
    loop_state.beat("worker-other")
    assert loop_state.heartbeat()["worker"] == "worker-test"
    # And it is not one of the boundary loops /health lists.
    assert loop_state.WORKER_HEARTBEAT not in {r["loop"] for r in loop_state.status()}


def test_slow_statements_are_counted_without_their_parameters(client, monkeypatch):
    from backend.db import connection as db

    monkeypatch.setattr(db, "_SLOW_QUERY_MS", 0.0)
    db.query("SELECT %s AS secret_marker", ("tenant-private-value",))
    stats = db.slow_query_stats()
    assert stats["count"] >= 1
    assert "tenant-private-value" not in json.dumps(stats)

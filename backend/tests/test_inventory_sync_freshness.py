"""Freshness: on-demand refresh, coalescing, cooldown, scoped refresh, the chip.

Uses the same "customer database" fixtures as test_inventory_sync.py; every
claim about stock is a direct query.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.inventory_sync import engine, profiles, scheduler
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    MAPPING, _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)

URL = "/api/v1/inventory-sync"


def _ready(tid, src_db, src_table, rows, **kw):
    _put(src_db, src_table, rows)
    p = _profile(tid, _source(tid), src_table, **kw)
    profiles.mark_dry_run(tid, p["id"], p["config_hash"])
    execute("UPDATE inventory_sync_profiles SET enabled = TRUE WHERE id = %s", (p["id"],))
    return profiles.get(tid, p["id"])


class TestRefresh:

    def test_refresh_makes_stock_current_and_reports_done(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        out = scheduler.refresh(tid, "usr_test", wait_s=20)
        assert out["profiles"][0]["status"] == "done"
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10
        assert out["freshness"]["state"] == "fresh"
        assert query_one("SELECT trigger_kind FROM inventory_sync_runs WHERE profile_id = %s",
                         (p["id"],))["trigger_kind"] == "refresh"

    def test_many_presses_at_once_make_one_run(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [(f"S{i}", "Norte", i) for i in range(200)])
        results: list = []
        barrier = threading.Barrier(12)

        def press():
            barrier.wait()
            results.append(scheduler.refresh(tid, "usr_test", wait_s=20)["profiles"][0])

        threads = [threading.Thread(target=press) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        runs = query("SELECT id FROM inventory_sync_runs WHERE profile_id = %s", (p["id"],))
        assert len(runs) == 1, [r["status"] for r in results]
        assert all(r["status"] in ("done", "running", "cooldown") for r in results)
        assert sum(1 for r in results if r.get("joined")) >= 1

    def test_cooldown_answers_with_retry_after(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        scheduler.refresh(tid, "usr_test", wait_s=20)
        again = scheduler.refresh(tid, "usr_test", wait_s=0)["profiles"][0]
        assert again["status"] == "cooldown" and again["retry_after_s"] >= 1
        assert len(query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s", (p["id"],))) == 1

    def test_scoped_refresh_touches_only_those_skus(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _ready(tid, src_db, src_table, [("A1", "Norte", 10), ("B2", "Norte", 5)])
        scheduler.refresh(tid, "usr_test", wait_s=20)
        _put(src_db, src_table, [("A1", "Norte", 11), ("B2", "Norte", 6)])
        out = scheduler.refresh(tid, "usr_test", skus=["A1"], wait_s=20)["profiles"][0]
        assert out["status"] == "done"
        stock = _stock(tid)
        assert float(stock[("A1", "Norte")]["current_stock"]) == 11
        assert float(stock[("B2", "Norte")]["current_stock"]) == 5   # untouched
        run = query_one("SELECT run_kind, rows_read, scope FROM inventory_sync_runs WHERE id = %s",
                        (out["run_id"],))
        assert run["run_kind"] == "scoped" and run["rows_read"] == 1 and run["scope"]["skus"] == ["A1"]

    def test_a_scoped_refresh_never_moves_the_watermark_past_unread_changes(self, test_tenant, src_db,
                                                                           src_table):
        """B2 changed at 10:00, A1 at 10:05. A refresh of A1 alone sees 10:05;
        had it stored that as the watermark, the next incremental read
        (>= 10:05 - overlap) would never see B2's change."""
        from datetime import datetime
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10, None, 1, datetime(2026, 1, 1)),
                                            ("B2", "Norte", 5, None, 1, datetime(2026, 1, 1))],
                   mode="incremental", watermark_column="updated_at", overlap_minutes=0)
        scheduler.refresh(tid, "usr_test", wait_s=20)
        before = profiles.get(tid, p["id"])["watermark_value"]
        _put(src_db, src_table, [("A1", "Norte", 11, None, 1, datetime(2026, 3, 1, 10, 5)),
                                 ("B2", "Norte", 6, None, 1, datetime(2026, 3, 1, 10, 0))])
        execute("UPDATE inventory_sync_profiles SET last_refresh_at = NULL WHERE id = %s", (p["id"],))
        scheduler.refresh(tid, "usr_test", skus=["A1"], wait_s=20)
        assert profiles.get(tid, p["id"])["watermark_value"] == before
        execute("UPDATE inventory_sync_profiles SET last_refresh_at = NULL WHERE id = %s", (p["id"],))
        scheduler.refresh(tid, "usr_test", wait_s=20)
        assert float(_stock(tid)[("B2", "Norte")]["current_stock"]) == 6

    def test_a_full_run_records_the_watermark(self, test_tenant, src_db, src_table):
        """Switching a profile to incremental (or the first continuous poll)
        starts where the last full read stopped instead of re-reading all."""
        from datetime import datetime
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10, None, 1, datetime(2026, 2, 1))],
                   watermark_column="updated_at")
        scheduler.refresh(tid, "usr_test", wait_s=20)
        wm = profiles.get(tid, p["id"])["watermark_value"]
        assert wm and wm["value"].startswith("2026-02-01")

    def test_scoped_refresh_never_applies_missing_policy(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _ready(tid, src_db, src_table, [("A1", "Norte", 10), ("B2", "Norte", 5)], missing_policy="zero")
        scheduler.refresh(tid, "usr_test", wait_s=20)
        scheduler.refresh(tid, "usr_test", skus=["A1"], wait_s=20)
        assert float(_stock(tid)[("B2", "Norte")]["current_stock"]) == 5

    def test_a_down_source_keeps_last_data_and_turns_the_chip(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        scheduler.refresh(tid, "usr_test", wait_s=20)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME TO {src_table}_down")
        try:
            execute("UPDATE inventory_sync_profiles SET last_refresh_at = NULL WHERE id = %s", (p["id"],))
            out = scheduler.refresh(tid, "usr_test", wait_s=20)["profiles"][0]
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"ALTER TABLE {src_table}_down RENAME TO {src_table}")
        assert out["status"] == "failed" and out["error_code"] == "inventory_sync_object_missing"
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10    # never blanked
        fresh = scheduler.freshness(tid)
        assert fresh["state"] == "blocked"       # schema drift pauses the profile
        assert fresh["profiles"][0]["reason"] == "inventory_sync_object_missing"
        again = scheduler.refresh(tid, "usr_test", wait_s=0)["profiles"][0]
        assert again["status"] == "paused"       # refuses to run, says why

    def test_freshness_states(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        assert scheduler.freshness(tid)["state"] == "none"
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)], interval_minutes=10)
        assert scheduler.freshness(tid)["state"] == "stale"      # never succeeded
        _run(tid, p["id"])
        assert scheduler.freshness(tid)["state"] == "fresh"
        execute("UPDATE inventory_sync_profiles SET data_as_of = NOW() - INTERVAL '3 hours', "
                "last_success_at = NOW() - INTERVAL '3 hours' WHERE id = %s", (p["id"],))
        assert scheduler.freshness(tid)["state"] == "stale"

    def test_api_permission_pair_and_viewer_reads_freshness(self, client, viewer_headers, analyst_headers,
                                                            test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        r = client.post(f"{URL}/refresh", json={"wait_seconds": 10}, headers=viewer_headers)
        assert r.status_code == 403
        assert query("SELECT 1 FROM inventory_sync_runs WHERE tenant_id = %s", (tid,)) == []
        assert client.get(f"{URL}/freshness", headers=viewer_headers).json()["data"]["state"] == "stale"
        r = client.post(f"{URL}/refresh", json={"wait_seconds": 15}, headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["profiles"][0]["status"] == "done"
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10


class TestScheduling:

    def test_continuous_profile_needs_an_incremental_reader(self, test_tenant, src_db, src_table):
        from backend.errors import AppError
        tid = test_tenant["id"]
        with pytest.raises(AppError) as exc:
            _profile(tid, _source(tid), src_table, freshness="continuous")
        assert exc.value.code == "inventory_sync_continuous_needs_incremental"
        p = _profile(tid, _source(tid), src_table, freshness="continuous", mode="incremental",
                     watermark_column="updated_at", poll_seconds=20)
        assert p["freshness"] == "continuous" and p["poll_seconds"] == 20

    def test_continuous_alternates_incremental_and_reconcile(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)], freshness="continuous",
                   mode="incremental", watermark_column="updated_at", poll_seconds=20)
        assert scheduler._kind_for(p, "continuous") == "reconcile"       # first: full reconcile
        execute("UPDATE inventory_sync_profiles SET next_reconcile_at = NOW() + INTERVAL '1 hour' "
                "WHERE id = %s", (p["id"],))
        assert scheduler._kind_for(profiles.get(tid, p["id"]), "continuous") == "incremental"

    def test_paused_and_blocked_profiles_are_not_claimed(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        execute("UPDATE inventory_sync_profiles SET next_run_at = NOW() - INTERVAL '1 minute', "
                "health = 'blocked', health_changed_at = NOW() WHERE id = %s", (p["id"],))
        assert scheduler.claim_due(owner="w", profile_id=p["id"]) is None
        # Editing (re-testing) the data source lifts the block on the next pass.
        execute("UPDATE datasets SET updated_at = NOW() + INTERVAL '1 second' WHERE id = %s",
                (p["data_source_id"],))
        claimed = scheduler.claim_due(owner="w2", profile_id=p["id"])
        assert claimed and claimed["id"] == p["id"]

    def test_next_run_has_jitter(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)], interval_minutes=60)
        waits = {round(scheduler._next_wait_seconds(profiles.get(tid, p["id"]))) for _ in range(20)}
        assert len(waits) > 5 and all(2880 <= w <= 4320 for w in waits)

    def test_prune_keeps_recent_runs(self, test_tenant, src_db, src_table, monkeypatch):
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [("A1", "Norte", 10)])
        for _ in range(25):
            _run(tid, p["id"])
        execute("UPDATE inventory_sync_runs SET started_at = NOW() - INTERVAL '400 days' WHERE profile_id = %s",
                (p["id"],))
        scheduler.prune()
        assert len(query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s", (p["id"],))) == 20


class TestCrashResume:

    def test_a_worker_killed_mid_apply_is_resumed_from_its_checkpoint(self, test_tenant, src_db, src_table,
                                                                     monkeypatch):
        from backend.inventory_sync import apply as applier
        tid = test_tenant["id"]
        p = _ready(tid, src_db, src_table, [(f"S{i:03d}", "Norte", i + 1) for i in range(30)])
        monkeypatch.setattr(applier, "BATCH", 10)
        original = applier.apply_batch
        calls = {"n": 0}

        class Killed(BaseException):
            """Stands in for the process dying (not an Exception: nothing catches it)."""

        def dying(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise Killed()
            return original(*a, **kw)

        monkeypatch.setattr(applier, "apply_batch", dying)
        owner = "worker-a"
        scheduler.claim_one(tid, p["id"], owner)
        run_id = engine.start_run(tid, p["id"], "schedule", worker=owner)
        with pytest.raises(Killed):
            engine.execute_run(tid, p["id"], run_id, lease_owner=owner)
        half = _stock(tid)
        assert len(half) == 10                                   # first batch committed, no half batch
        run = query_one("SELECT status, checkpoint, stage_complete, brake_passed FROM inventory_sync_runs "
                        "WHERE id = %s", (run_id,))
        assert run["status"] == "running" and run["stage_complete"] and run["brake_passed"]
        assert run["checkpoint"]["after"] == ["S009", "Norte"]
        # The lease expires; another worker takes the profile over and RESUMES.
        monkeypatch.setattr(applier, "apply_batch", original)
        execute("UPDATE inventory_sync_profiles SET lease_until = NOW() - INTERVAL '1 second', "
                "next_run_at = NOW() - INTERVAL '1 second' WHERE id = %s", (p["id"],))
        claimed = scheduler.claim_due(owner="worker-b", profile_id=p["id"])
        assert claimed and claimed["id"] == p["id"]
        scheduler.run_claimed(claimed, "worker-b", "schedule")
        final = query_one("SELECT status, created FROM inventory_sync_runs WHERE id = %s", (run_id,))
        assert final["status"] == "success" and final["created"] == 30
        assert len(_stock(tid)) == 30
        assert len(query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s", (p["id"],))) == 1

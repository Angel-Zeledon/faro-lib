"""
PHASE 2 — Database layer integration tests.
Tests the session_store, connection pool, CASCADE deletes, and DB constraints directly.
Bypasses the HTTP layer to verify data integrity at the persistence level.
"""

import pytest
from uuid import uuid4


@pytest.mark.integration
class TestMigrations:
    """
    Regression guard for the schema-drift finding: jobs/chats/chat_messages
    were used throughout the app but had no CREATE TABLE in migrations.py,
    so a fresh deployment would 500 on first use of Forecast Studio training
    or the AI Analyst chat.
    """

    def test_run_all_does_not_raise(self, client):
        # `client` boots the app, which initialises the connection pool. Without
        # it every migration failed on "DB pool not initialized" and the old
        # run_all() swallowed all 60 as warnings — the test passed while applying
        # nothing at all.
        from backend.db.migrations import run_all
        run_all()  # idempotent — must succeed even when tables already exist

    def test_a_broken_migration_raises_instead_of_being_swallowed(self, client, monkeypatch):
        """
        Regression: run_all() used to log EVERY exception as a warning, so a typo,
        a missing FK target or a bad backfill looked exactly like an idempotent
        re-run and the server booted on a half-built schema.
        """
        from backend.db import migrations

        monkeypatch.setattr(
            migrations, "_MIGRATIONS",
            [("deliberately_broken", "CREATE TABLE nope (x INT) REFERENCES ghost_table(id)")],
        )
        with pytest.raises(migrations.MigrationError) as exc_info:
            migrations.run_all()
        assert "deliberately_broken" in str(exc_info.value)

    def test_already_applied_objects_are_not_treated_as_failures(self, client, monkeypatch):
        """Re-running a CREATE without IF NOT EXISTS must stay silent, not explode."""
        from backend.db import migrations
        from backend.db.connection import execute

        table = f"dup_probe_{uuid4().hex[:8]}"
        execute(f"CREATE TABLE {table} (x INT)")
        try:
            monkeypatch.setattr(
                migrations, "_MIGRATIONS",
                [("recreate_existing", f"CREATE TABLE {table} (x INT)")],
            )
            migrations.run_all()  # duplicate_table (42P07) is tolerated
        finally:
            execute(f"DROP TABLE IF EXISTS {table}")

    def test_jobs_chats_chat_messages_tables_exist(self):
        from backend.db.connection import query
        rows = query(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('jobs', 'chats', 'chat_messages')
            """
        )
        found = {r["table_name"] for r in rows}
        assert found == {"jobs", "chats", "chat_messages"}


@pytest.mark.integration
class TestConnectionPool:
    def test_query_returns_list(self):
        from backend.db.connection import query
        result = query("SELECT 1 AS val")
        assert isinstance(result, list)
        assert result[0]["val"] == 1

    def test_query_one_returns_dict_or_none(self):
        from backend.db.connection import query_one
        result = query_one("SELECT 1 AS val")
        assert result is not None
        assert result["val"] == 1

    def test_query_one_returns_none_for_no_rows(self):
        from backend.db.connection import query_one
        result = query_one(
            "SELECT 1 WHERE 1 = 0"
        )
        assert result is None

    def test_execute_runs_without_error(self):
        from backend.db.connection import execute
        # Safe no-op: update where nothing matches
        execute(
            "UPDATE sessions SET updated_at = NOW() WHERE id = %s",
            ("nonexistent_id",),
        )


@pytest.mark.integration
class TestSessionStore:
    def test_set_and_get_field_roundtrip(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "store-test")
        sid = s["id"]
        tid = test_tenant["id"]

        payload = {"key": "value", "nested": {"x": 1}, "list": [1, 2, 3]}
        session_store.set_field(tid, sid, "columns_cfg", payload)
        result = session_store.get_field(tid, sid, "columns_cfg")
        assert result == payload

    def test_overwrite_field(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "overwrite-test")
        tid = test_tenant["id"]
        session_store.set_field(tid, s["id"], "features_cfg", {"lags": [1, 7]})
        session_store.set_field(tid, s["id"], "features_cfg", {"lags": [1, 7, 14, 28]})
        result = session_store.get_field(tid, s["id"], "features_cfg")
        assert result["lags"] == [1, 7, 14, 28]

    def test_get_nonexistent_field_returns_none(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "null-field-test")
        result = session_store.get_field(test_tenant["id"], s["id"], "models_cfg")
        assert result is None

    def test_clear_field(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "clear-test")
        tid = test_tenant["id"]
        session_store.set_field(tid, s["id"], "validation_cfg", {"train_ratio": 0.8})
        session_store.clear_field(tid, s["id"], "validation_cfg")
        assert session_store.get_field(tid, s["id"], "validation_cfg") is None

    def test_set_and_get_training_result(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "result-test")
        tid = test_tenant["id"]
        payload = {
            "job_id": "job_xyz",
            "metrics": {"rows": [{"sku": "A", "mae": 2.5}]},
            "config": {},
        }
        session_store.set_training_result(tid, s["id"], payload)
        result = session_store.get_training_result(tid, s["id"])
        assert result["job_id"] == "job_xyz"
        assert result["metrics"]["rows"][0]["mae"] == 2.5

    def test_set_and_get_forecasts(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "forecasts-test")
        tid = test_tenant["id"]
        forecasts = {
            "SKU_001": {
                "lightgbm": {
                    "historical": [{"date": "2023-01-01", "value": 10.0}],
                    "forecast": [{"date": "2023-04-01", "value": 12.0}],
                }
            }
        }
        session_store.set_forecasts(tid, s["id"], forecasts)
        result = session_store.get_forecasts(tid, s["id"])
        assert "SKU_001" in result
        assert "lightgbm" in result["SKU_001"]

    def test_append_and_get_logs(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session
        from backend.training.job_service import create_job

        s = create_session(test_tenant["id"], "usr_test", "logs-test")
        tid = test_tenant["id"]
        # training_logs.job_id has a FK to jobs — logs only ever exist for a
        # real job, so the test must create one instead of inventing an id.
        job_id = create_job(tid, s["id"], "usr_test")["id"]
        session_store.append_log(tid, s["id"], job_id, "line 1")
        session_store.append_log(tid, s["id"], job_id, "line 2")
        session_store.append_log(tid, s["id"], job_id, "line 3")
        lines = session_store.get_logs(tid, s["id"], job_id)
        assert len(lines) == 3
        assert "line 1" in lines[0]
        assert "line 3" in lines[2]

    def test_get_logs_tail_limit(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        from backend.training.job_service import create_job

        s = create_session(test_tenant["id"], "usr_test", "logs-tail-test")
        tid = test_tenant["id"]
        job_id = create_job(tid, s["id"], "usr_test")["id"]
        for i in range(10):
            session_store.append_log(tid, s["id"], job_id, f"line {i}")
        lines = session_store.get_logs(tid, s["id"], job_id, tail=3)
        assert len(lines) == 3

    def test_get_all_configs_returns_dict(self, test_tenant):
        from backend.db import session_store
        from backend.sessions.service import create_session

        s = create_session(test_tenant["id"], "usr_test", "all-configs-test")
        tid = test_tenant["id"]
        session_store.set_field(tid, s["id"], "columns_cfg", {"date_column": "date"})
        session_store.set_field(tid, s["id"], "models_cfg", {"selected_models": ["lightgbm"]})
        cfg = session_store.get_all_configs(tid, s["id"])
        assert cfg["columns_cfg"]["date_column"] == "date"
        assert cfg["models_cfg"]["selected_models"] == ["lightgbm"]


@pytest.mark.integration
class TestCascadeDeletes:
    def test_delete_tenant_cascades_to_users(self):
        from backend.tenants.service import create_tenant
        from backend.users.service import create_user
        from backend.db.connection import execute, query_one

        tenant = create_tenant(f"cascade-test-{uuid4().hex[:8]}")
        user = create_user(
            tenant_id=tenant["id"],
            email=f"cascade-{uuid4().hex[:8]}@test.com",
            password="TestPass123!",
            role="analyst",
        )
        user_id = user["id"]

        execute("DELETE FROM tenants WHERE id = %s", (tenant["id"],))
        row = query_one("SELECT id FROM users WHERE id = %s", (user_id,))
        assert row is None, "User should be CASCADE-deleted with tenant"

    def test_delete_session_cascades_to_jobs(self, test_tenant):
        from backend.sessions.service import create_session, delete_session
        from backend.training.job_service import create_job
        from backend.db.connection import query_one

        s = create_session(test_tenant["id"], "usr_test", "cascade-jobs-test")
        job = create_job(test_tenant["id"], s["id"], "usr_test")
        job_id = job["id"]

        delete_session(test_tenant["id"], s["id"])
        row = query_one("SELECT id FROM jobs WHERE id = %s", (job_id,))
        assert row is None, "Job should be CASCADE-deleted with session"

    def test_delete_session_cascades_to_session_configs(self, test_tenant):
        from backend.sessions.service import create_session, delete_session
        from backend.db import session_store
        from backend.db.connection import query_one

        s = create_session(test_tenant["id"], "usr_test", "cascade-cfg-test")
        session_store.set_field(test_tenant["id"], s["id"], "columns_cfg", {"test": True})

        delete_session(test_tenant["id"], s["id"])
        row = query_one(
            "SELECT session_id FROM session_configs WHERE session_id = %s",
            (s["id"],),
        )
        assert row is None, "session_configs row should be CASCADE-deleted"


@pytest.mark.integration
class TestUniqueConstraints:
    def test_duplicate_email_raises(self, test_tenant):
        from backend.users.service import create_user
        email = f"dup-{uuid4().hex[:8]}@test.com"
        create_user(test_tenant["id"], email, "TestPass123!", "analyst")
        with pytest.raises(Exception):
            create_user(test_tenant["id"], email, "AnotherPass123!", "viewer")

    def test_duplicate_tenant_slug_raises(self):
        from backend.db.connection import execute
        # Create tenant with specific slug via raw SQL to force constraint check
        from backend.utils.ids import generate_id
        from backend.db.connection import execute as _exec, _json
        tid1 = generate_id("ten")
        tid2 = generate_id("ten")
        slug = f"dupe-slug-{uuid4().hex[:8]}"
        quota = _json({"max_sessions": 20})
        _exec(
            "INSERT INTO tenants (id, name, slug, plan, status, quota, settings, created_at) "
            "VALUES (%s, %s, %s, 'free', 'active', %s, '{}', NOW())",
            (tid1, "T1", slug, quota),
        )
        with pytest.raises(Exception):
            _exec(
                "INSERT INTO tenants (id, name, slug, plan, status, quota, settings, created_at) "
                "VALUES (%s, %s, %s, 'free', 'active', %s, '{}', NOW())",
                (tid2, "T2", slug, quota),
            )
        execute("DELETE FROM tenants WHERE id = %s", (tid1,))


@pytest.mark.integration
class TestSessionStateMachine:
    def test_create_session_starts_as_draft(self, test_tenant):
        from backend.sessions.service import create_session
        s = create_session(test_tenant["id"], "usr_test", "sm-test")
        assert s["status"] == "DRAFT"

    def test_valid_transition_succeeds(self, test_tenant):
        from backend.sessions.service import create_session, transition
        s = create_session(test_tenant["id"], "usr_test", "transition-test")
        updated = transition(test_tenant["id"], s["id"], "DATASET_LOADED", "inspect")
        assert updated["status"] == "DATASET_LOADED"

    def test_invalid_transition_raises_value_error(self, test_tenant):
        from backend.sessions.service import create_session, transition
        s = create_session(test_tenant["id"], "usr_test", "invalid-trans-test")
        with pytest.raises(ValueError):
            transition(test_tenant["id"], s["id"], "COMPLETED")  # DRAFT → COMPLETED invalid

    def test_force_status_bypasses_state_machine(self, test_tenant):
        from backend.sessions.service import create_session, force_status
        s = create_session(test_tenant["id"], "usr_test", "force-status-test")
        updated = force_status(test_tenant["id"], s["id"], "COMPLETED", "results")
        assert updated["status"] == "COMPLETED"

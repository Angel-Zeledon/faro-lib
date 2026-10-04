"""A scheduled retrain must look at the data before it trains.

Two defects, both verified in docs/retraining-and-enterprise.md:

1. The schedule never asked whether anything had changed, so a nightly preset
   over a file nobody touched retrained identical data every night.
2. A schedule on a SQL source trained on the snapshot the wizard once took,
   forever: each materialize creates a NEW dataset and nothing pointed the
   schedule at it.

Every assertion reads the database, not the return value.
"""
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one


@pytest.fixture
def fake_launch(monkeypatch):
    from backend.sessions import family_service
    calls = []

    def _launch(tenant_id, session_id, user_id, **kw):
        calls.append(session_id)
        return {"base_job_id": "job_x"}

    monkeypatch.setattr(family_service, "launch_training_family", _launch)
    return calls


def _dataset(tid, path, *, parent_id=None, name="sales.csv", source_type="file"):
    ds_id = f"ds_{uuid4().hex[:8]}"
    execute(
        """INSERT INTO datasets (id, tenant_id, name, original_filename, file_path,
                                 file_type, row_count, uploaded_by, parent_id, source_type)
           VALUES (%s,%s,%s,%s,%s,'csv',3,'usr_test',%s,%s)""",
        (ds_id, tid, name, name, str(path), parent_id, source_type),
    )
    return ds_id


def _template(tid, dataset_id):
    session_id = f"sess_{uuid4().hex[:8]}"
    execute(
        """INSERT INTO sessions (id, tenant_id, name, status, created_by, dataset_id)
           VALUES (%s,%s,'Weekly','COMPLETED','usr_test',%s)""",
        (session_id, tid, dataset_id),
    )
    execute("INSERT INTO session_configs (session_id, tenant_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING", (session_id, tid))
    session_store.set_field(tid, session_id, "columns_cfg", {"sku": "sku"})
    session_store.set_field(tid, session_id, "models_cfg", {"models": ["prophet"]})
    return session_id


def _schedule(tid, session_id):
    sched = f"sched-{uuid4().hex[:8]}"
    execute(
        "INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run) "
        "VALUES (%s,%s,%s,'0 6 * * 1',NOW())", (sched, tid, session_id))
    return sched


def _runs(sched):
    return query("SELECT outcome, reason, session_id, dataset_id, content_hash "
                 "FROM schedule_runs WHERE schedule_id=%s ORDER BY ran_at, id", (sched,))


def _complete_scheduled_sessions(sched):
    execute("UPDATE sessions SET status='COMPLETED' WHERE scheduled_job_id=%s", (sched,))


class TestNoNewDataNoRetrain:
    def test_an_unchanged_file_is_skipped_and_the_skip_is_recorded(
        self, client, test_tenant, tmp_path, fake_launch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        f = tmp_path / "data.csv"
        f.write_text("sku,date,qty\na,2026-01-01,1\n")
        template = _template(tid, _dataset(tid, f))
        sched = _schedule(tid, template)

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        _complete_scheduled_sessions(sched)
        assert len(fake_launch) == 1

        # Same bytes, due again.
        assert retrain_service.launch_scheduled_retrain(tid, sched, template) is None

        assert len(fake_launch) == 1, "trained again on identical data"
        assert query_one("SELECT COUNT(*) AS n FROM sessions WHERE scheduled_job_id=%s",
                         (sched,))["n"] == 1
        rows = _runs(sched)
        assert [r["outcome"] for r in rows] == ["launched", "skipped"]
        assert rows[1]["reason"] == "no_new_data"
        assert rows[1]["content_hash"] == rows[0]["content_hash"]

    def test_a_changed_file_trains_again(
        self, client, test_tenant, tmp_path, fake_launch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        f = tmp_path / "data.csv"
        f.write_text("sku,date,qty\na,2026-01-01,1\n")
        template = _template(tid, _dataset(tid, f))
        sched = _schedule(tid, template)

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        _complete_scheduled_sessions(sched)
        f.write_text("sku,date,qty\na,2026-01-01,1\na,2026-01-02,4\n")

        assert retrain_service.launch_scheduled_retrain(tid, sched, template) is not None
        assert len(fake_launch) == 2
        rows = _runs(sched)
        assert [r["outcome"] for r in rows] == ["launched", "launched"]
        assert rows[0]["content_hash"] != rows[1]["content_hash"]

    def test_a_previous_run_that_failed_does_not_count_as_trained(
        self, client, test_tenant, tmp_path, fake_launch,
    ):
        """Nothing is serving that data, so identical bytes must still train."""
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        f = tmp_path / "data.csv"
        f.write_text("sku,date,qty\na,2026-01-01,1\n")
        template = _template(tid, _dataset(tid, f))
        sched = _schedule(tid, template)

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        execute("UPDATE sessions SET status='FAILED' WHERE scheduled_job_id=%s", (sched,))

        assert retrain_service.launch_scheduled_retrain(tid, sched, template) is not None
        assert len(fake_launch) == 2

    def test_the_skip_is_visible_in_the_schedule_history_to_a_viewer(
        self, client, test_tenant, viewer_headers, tmp_path, fake_launch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        f = tmp_path / "data.csv"
        f.write_text("sku,date,qty\na,2026-01-01,1\n")
        template = _template(tid, _dataset(tid, f))
        sched = _schedule(tid, template)
        retrain_service.launch_scheduled_retrain(tid, sched, template)
        _complete_scheduled_sessions(sched)
        retrain_service.launch_scheduled_retrain(tid, sched, template)

        r = client.get("/api/v1/schedules/history", headers=viewer_headers)
        assert r.status_code == 200
        skipped = [e for e in r.json()["data"] if e["status"] == "SKIPPED"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_new_data"
        assert skipped[0]["session_name"] == "Weekly"


class TestSqlScheduleTrainsOnFreshData:
    def _sql_world(self, tid, tmp_path):
        source_id = f"ds_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO datasets (id, tenant_id, name, file_type, source_type,
                                     connection_status, uploaded_by, saved_query)
               VALUES (%s,%s,'ERP','sql','sql','connected','usr_test','select 1')""",
            (source_id, tid),
        )
        old = tmp_path / "old.csv"
        old.write_text("sku,date,qty\na,2026-01-01,1\n")
        old_ds = _dataset(tid, old, parent_id=source_id, name="ERP (SQL)")
        template = _template(tid, old_ds)
        return source_id, old_ds, template, _schedule(tid, template)

    def _fake_materialize(self, monkeypatch, tmp_path, tid, content_by_call):
        from backend.datasources import service as ds_svc
        made = []

        def _fake(tenant_id, user_id, source_id, sql=None, name=None):
            body = content_by_call[min(len(made), len(content_by_call) - 1)]
            path = tmp_path / f"snap{len(made)}.csv"
            path.write_text(body)
            ds_id = _dataset(tenant_id, path, parent_id=source_id,
                             name=name or "snap")
            made.append(ds_id)
            return {"id": ds_id}

        monkeypatch.setattr(ds_svc, "materialize_sql_source", _fake)
        return made

    def test_the_run_trains_on_a_fresh_snapshot_not_the_old_dataset(
        self, client, test_tenant, tmp_path, fake_launch, monkeypatch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        source_id, old_ds, template, sched = self._sql_world(tid, tmp_path)
        made = self._fake_materialize(
            monkeypatch, tmp_path, tid, ["sku,date,qty\na,2026-01-01,1\na,2026-01-09,7\n"])

        retrain_service.launch_scheduled_retrain(tid, sched, template)

        assert len(made) == 1
        run = query_one("SELECT dataset_id FROM sessions WHERE scheduled_job_id=%s", (sched,))
        assert run["dataset_id"] == made[0] != old_ds
        assert query_one("SELECT created_by_schedule_id FROM datasets WHERE id=%s",
                         (made[0],))["created_by_schedule_id"] == sched
        assert _runs(sched)[0]["dataset_id"] == made[0]

    def test_a_snapshot_identical_to_the_last_trained_one_is_skipped_and_dropped(
        self, client, test_tenant, tmp_path, fake_launch, monkeypatch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        source_id, old_ds, template, sched = self._sql_world(tid, tmp_path)
        same = "sku,date,qty\na,2026-01-01,1\na,2026-01-09,7\n"
        made = self._fake_materialize(monkeypatch, tmp_path, tid, [same, same])

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        _complete_scheduled_sessions(sched)
        assert retrain_service.launch_scheduled_retrain(tid, sched, template) is None

        assert len(fake_launch) == 1
        assert [r["outcome"] for r in _runs(sched)] == ["launched", "skipped"]
        # The second snapshot added nothing, so it was not kept.
        assert query_one("SELECT id FROM datasets WHERE id=%s", (made[1],)) is None
        assert query_one("SELECT id FROM datasets WHERE id=%s", (made[0],)) is not None

    def test_a_source_that_cannot_refresh_fails_loudly_instead_of_training_stale_data(
        self, client, test_tenant, tmp_path, fake_launch, monkeypatch,
    ):
        from backend.datasources import service as ds_svc
        from backend.errors import AppError
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        source_id, old_ds, template, sched = self._sql_world(tid, tmp_path)

        def _boom(*a, **k):
            raise AppError("sql_query_failed", "Query failed: db down", status_code=422)

        monkeypatch.setattr(ds_svc, "materialize_sql_source", _boom)

        with pytest.raises(AppError):
            retrain_service.launch_scheduled_retrain(tid, sched, template)

        assert fake_launch == []
        assert query_one("SELECT COUNT(*) AS n FROM sessions WHERE scheduled_job_id=%s",
                         (sched,))["n"] == 0
        rows = _runs(sched)
        assert [(r["outcome"], r["reason"]) for r in rows] == [
            ("failed", "source_refresh_failed")]

    def test_earlier_snapshots_nothing_uses_are_freed(
        self, client, test_tenant, tmp_path, fake_launch, monkeypatch,
    ):
        from backend.sessions import retrain_service

        tid = test_tenant["id"]
        source_id, old_ds, template, sched = self._sql_world(tid, tmp_path)
        made = self._fake_materialize(monkeypatch, tmp_path, tid, [
            "sku,date,qty\na,2026-01-01,1\nb,2026-01-02,1\n",
            "sku,date,qty\na,2026-01-01,1\nb,2026-01-02,1\nc,2026-01-03,1\n",
            "sku,date,qty\na,2026-01-01,1\nb,2026-01-02,1\nc,2026-01-03,1\nd,2026-01-04,1\n",
        ])
        for _ in range(3):
            retrain_service.launch_scheduled_retrain(tid, sched, template)
            _complete_scheduled_sessions(sched)

        alive = {r["id"] for r in query(
            "SELECT id FROM datasets WHERE created_by_schedule_id=%s", (sched,))}
        # Run 3 pruned run 1's session (not serving), so only snapshots still
        # referenced by a surviving session plus the newest remain; the first
        # one is gone, and the dataset a person made is untouched.
        assert made[0] not in alive
        assert made[2] in alive
        assert query_one("SELECT id FROM datasets WHERE id=%s", (old_ds,)) is not None

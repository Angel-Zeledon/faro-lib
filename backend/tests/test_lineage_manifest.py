"""The lineage manifest: how a forecast was produced, recorded once, unchanged.

Every assertion reads `session_manifests` (or the hash of the real file on disk)
rather than trusting a response, and the runner is driven end to end with the
engine mocked the same way `test_integration_forecasting.py` does.
"""
import hashlib
from unittest import mock
from uuid import uuid4

import psycopg2
import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.lineage.hashing import json_sha256
from tests.test_integration_forecasting import _make_mock_engine


def _run(tenant_id, session_id, engine):
    from backend.training.job_service import list_jobs_for_session
    from backend.workers.runner import run_training_job

    job_id = list_jobs_for_session(tenant_id, session_id)[0]["id"]
    with mock.patch("forecasting_core.engine.ForecastEngine") as fe:
        fe.from_dict.return_value = engine
        run_training_job(tenant_id, session_id, job_id)
    return job_id


def _manifest_rows(tenant_id, session_id):
    return query(
        "SELECT * FROM session_manifests WHERE tenant_id=%s AND session_id=%s "
        "ORDER BY created_at", (tenant_id, session_id))


@pytest.mark.integration
class TestAManifestIsWrittenForEveryRun:
    def test_a_completed_run_records_who_what_with_which_data_and_the_forecast_hash(
        self, client, auth_headers, configured_session, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = _run(tid, sid, _make_mock_engine(n_skus=2))

        rows = _manifest_rows(tid, sid)
        assert len(rows) == 1
        row = rows[0]
        m = row["manifest"]
        assert row["job_id"] == job_id and row["outcome"] == "COMPLETED"

        # who
        assert m["trigger"]["kind"] == "user"
        assert m["trigger"]["actor_id"] == registered_user["user"]["id"]
        assert m["trigger"]["label"] == registered_user["email"]

        # on which data: the hash of the file as it sits on disk
        ds = query_one(
            "SELECT d.file_path FROM datasets d JOIN sessions s ON s.dataset_id=d.id "
            "WHERE s.id=%s", (sid,))
        with open(ds["file_path"], "rb") as fh:
            assert m["dataset"]["content_hash"] == hashlib.sha256(fh.read()).hexdigest()

        # under which configuration
        stored_cols = session_store.get_field(tid, sid, "columns_cfg")
        assert m["config"]["columns_cfg"] == stored_cols
        assert m["models"]["selected"] == ["lightgbm"]
        assert m["models"]["outcomes"]["lightgbm"]["series"] == 2
        assert "data" not in m["config"]["effective_engine_config"] or \
            "path" not in m["config"]["effective_engine_config"]["data"]

        # with which code
        assert m["versions"]["engine"]
        assert m["versions"]["python"]

        # how long each stage took
        assert m["stage_timings_seconds"], "no stage timings were captured"
        assert all(v >= 0 for v in m["stage_timings_seconds"].values())

        # what it produced
        forecasts = session_store.get_forecasts(tid, sid)
        assert m["forecast"]["series_count"] == len(forecasts) == 2
        assert m["forecast"]["hash"] == json_sha256(forecasts)
        # The runner is called directly here, so no worker claimed the job and
        # `started_at` is legitimately empty; the end of the run is always set.
        assert m["timing"]["queued_at"] and m["timing"]["finished_at"]

    def test_a_failed_run_records_the_error_and_no_forecast(
        self, client, auth_headers, configured_session, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        engine = _make_mock_engine(n_skus=2)
        engine.train.side_effect = RuntimeError("engine exploded")
        _run(tid, sid, engine)

        rows = _manifest_rows(tid, sid)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "FAILED"
        m = rows[0]["manifest"]
        assert "engine exploded" in m["error"]
        assert m["forecast"] == {"hash": None, "series_count": 0}
        assert m["trigger"]["actor_id"] == registered_user["user"]["id"]

    def test_the_trigger_names_a_schedule_and_an_api_key_not_just_people(
        self, client, auth_headers, configured_session, registered_user,
    ):
        from backend.lineage.manifest import build_manifest

        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = _run(tid, sid, _make_mock_engine(n_skus=1))

        execute("UPDATE jobs SET created_by='scheduler' WHERE id=%s", (job_id,))
        execute("UPDATE sessions SET scheduled_job_id='sched_x' WHERE id=%s", (sid,))
        scheduled = build_manifest(tid, sid, job_id, outcome="COMPLETED")
        assert scheduled["trigger"]["kind"] == "schedule"
        assert scheduled["trigger"]["schedule_id"] == "sched_x"

        execute("UPDATE sessions SET scheduled_job_id=NULL WHERE id=%s", (sid,))
        key_id = f"key_{uuid4().hex[:8]}"
        execute("INSERT INTO api_keys (id, tenant_id, name, key_hash, role) "
                "VALUES (%s, %s, 'ERP nightly', %s, 'analyst')",
                (key_id, tid, f"hash-{uuid4().hex}"))
        execute("UPDATE jobs SET created_by=%s WHERE id=%s", (f"api_key:{key_id}", job_id))
        keyed = build_manifest(tid, sid, job_id, outcome="COMPLETED")
        assert keyed["trigger"] == {
            "kind": "api_key", "actor_id": f"api_key:{key_id}",
            "schedule_id": None, "label": "ERP nightly"}


@pytest.mark.integration
class TestTheManifestCannotBeChanged:
    def test_the_database_refuses_an_update(
        self, client, auth_headers, configured_session, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        _run(tid, sid, _make_mock_engine(n_skus=1))

        before = _manifest_rows(tid, sid)[0]["manifest"]
        with pytest.raises(psycopg2.Error):
            execute("UPDATE session_manifests SET outcome='FAILED' WHERE session_id=%s", (sid,))
        with pytest.raises(psycopg2.Error):
            execute("UPDATE session_manifests SET manifest='{}'::jsonb WHERE session_id=%s", (sid,))
        assert _manifest_rows(tid, sid)[0]["manifest"] == before
        assert _manifest_rows(tid, sid)[0]["outcome"] == "COMPLETED"

    def test_deleting_the_forecast_keeps_how_it_was_made_until_the_tenant_is_erased(
        self, client, auth_headers, configured_session, registered_user,
    ):
        from backend.tenants.data_export import delete_tenant

        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        _run(tid, sid, _make_mock_engine(n_skus=1))

        execute("DELETE FROM sessions WHERE id=%s", (sid,))
        assert len(_manifest_rows(tid, sid)) == 1

        delete_tenant(tid)
        assert _manifest_rows(tid, sid) == []


@pytest.mark.integration
class TestManifestEndpoint:
    def test_any_role_reads_it_and_it_matches_the_stored_row(
        self, client, auth_headers, viewer_headers, configured_session, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        _run(tid, sid, _make_mock_engine(n_skus=1))

        r = client.get(f"/api/v1/sessions/{sid}/manifest", headers=viewer_headers)
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["manifest"] == _manifest_rows(tid, sid)[0]["manifest"]
        assert body["outcome"] == "COMPLETED"

    def test_a_session_that_never_trained_says_so_with_a_code(
        self, client, auth_headers, test_session,
    ):
        r = client.get(f"/api/v1/sessions/{test_session['id']}/manifest", headers=auth_headers)
        assert r.status_code == 404
        assert r.json()["error_code"] == "manifest_not_available"

    def test_another_tenant_cannot_read_it(
        self, client, auth_headers, configured_session, registered_user,
        make_tenant_user_headers,
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        _run(tid, sid, _make_mock_engine(n_skus=1))

        outsider = make_tenant_user_headers(role="admin")
        r = client.get(f"/api/v1/sessions/{sid}/manifest", headers=outsider)
        assert r.status_code == 404
        assert r.json()["error_code"] == "session_not_found"

"""Persisted models and re-forecasting, through the real engine and the real queue.

One tenant trains one session for real (module fixture), and every test reads
back from the database and the disk rather than trusting a response:

  * training writes versioned, content-hashed artifacts, and the same digests are
    in the immutable lineage manifest;
  * a re-forecast is a NEW session; the parent is untouched;
  * unchanged data gives the parent's forecast, new data gives a different one,
    and neither refits anything;
  * a tampered artifact, a schema the models were not trained on and a window
    that moved too far are refused with a stable code, and the parent keeps
    serving;
  * permissions, tenant isolation, plan ceilings and the scheduled flow.
"""
import csv
import hashlib
import io
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.sessions import service as session_svc

# `lightgbm` carries the per-SKU recursive path and `ets` the carried statistical
# state; 25 trees keep the one real training short.
MODELS = {"mode": "selected", "selected_models": ["lightgbm", "ets"],
          "hyperparameters": {"lightgbm": {"n_estimators": 25}}}


def _csv_text(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    w.writerows(rows)
    return buf.getvalue().encode()


def _read_rows(data: bytes):
    return [r for r in csv.reader(io.StringIO(data.decode()))][1:]


def _extend(data: bytes, days: int, factor: float = 1.0) -> bytes:
    """`days` more daily rows per SKU after the file's last date, level scaled."""
    from datetime import date, timedelta
    rows = _read_rows(data)
    last: dict = {}
    level: dict = {}
    for d, sku, v in rows:
        last[sku] = max(last.get(sku, d), d)
        level.setdefault(sku, []).append(float(v))
    out = list(rows)
    for sku, d in last.items():
        base = sum(level[sku][-14:]) / 14.0
        start = date.fromisoformat(d)
        for i in range(1, days + 1):
            out.append([(start + timedelta(days=i)).isoformat(), sku,
                        round(base * factor * (1 + 0.03 * ((i % 5) - 2)), 2)])
    return _csv_text(out)


def _login(client, tenant_id, role):
    from backend.users import service as user_svc
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tenant_id, email=email, password="TestPass123!",
                                role=role, full_name=f"Test {role}")
    user_svc.mark_verified(tenant_id, user["id"])
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _upload(client, headers, data: bytes, name="sales.csv") -> dict:
    r = client.post("/api/v1/datasets", files={"file": (name, data, "text/csv")}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _dataset_path(tenant_id, dataset_id) -> Path:
    return Path(query_one("SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
                          (dataset_id, tenant_id))["file_path"])


def _run_job(tenant_id, session_id):
    """Run the session's newest job on the real engine, as the worker would."""
    from backend.training.job_service import list_jobs_for_session
    from backend.workers.runner import run_training_job
    job = list_jobs_for_session(tenant_id, session_id)[0]
    run_training_job(tenant_id, session_id, job["id"])
    return job["id"]


def _session(tenant_id, session_id):
    return session_svc.get_session(tenant_id, session_id)


def _forecast_by_series(tenant_id, session_id):
    out = {}
    for sku, models in (session_store.get_forecasts(tenant_id, session_id) or {}).items():
        for model, body in models.items():
            out[(sku, model)] = [(p["date"], p["value"]) for p in body["forecast"]]
    return out


def _manifest(tenant_id, session_id):
    return query_one(
        "SELECT * FROM session_manifests WHERE tenant_id=%s AND session_id=%s "
        "ORDER BY created_at DESC LIMIT 1", (tenant_id, session_id))


def _sessions_of(tenant_id):
    return {r["id"] for r in query("SELECT id FROM sessions WHERE tenant_id=%s", (tenant_id,))}


@pytest.fixture(scope="module")
def world(client):
    """A tenant with a COMPLETED session trained for real, plus the datasets the
    tests re-forecast against, a second tenant, and one user of each role."""
    from backend.tenants.service import create_tenant
    from tests.fixtures.synthetic_data import generate_csv_bytes

    tenant = create_tenant(f"pytest-{uuid4().hex[:10]}")
    other = create_tenant(f"pytest-{uuid4().hex[:10]}")
    tid, oid = tenant["id"], other["id"]
    admin = _login(client, tid, "admin")
    analyst = _login(client, tid, "analyst")
    viewer = _login(client, tid, "viewer")
    other_analyst = _login(client, oid, "analyst")

    base_csv = generate_csv_bytes(n_skus=4, n_days=90)
    dataset = _upload(client, admin, base_csv)

    sid = client.post("/api/v1/sessions", json={"name": "reforecast-parent"},
                      headers=admin).json()["data"]["id"]
    client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset["id"]}, headers=admin)
    client.get(f"/api/v1/sessions/{sid}/inspect", headers=admin)
    client.post(f"/api/v1/sessions/{sid}/configure/columns",
                json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
                headers=admin)
    client.post(f"/api/v1/sessions/{sid}/configure/features",
                json={"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True},
                headers=admin)
    r = client.post(f"/api/v1/sessions/{sid}/configure/models", json=MODELS, headers=admin)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/sessions/{sid}/train", headers=admin)
    assert r.status_code == 202, r.text
    _run_job(tid, sid)
    assert _session(tid, sid)["status"] == "COMPLETED"

    # The same content under a new dataset id: "nothing changed" for the models.
    same = _upload(client, admin, base_csv, "same.csv")
    world_ = {
        "tid": tid, "oid": oid, "sid": sid, "dataset": dataset, "same": same,
        "csv": base_csv, "admin": admin, "analyst": analyst, "viewer": viewer,
        "other_analyst": other_analyst,
    }
    yield world_
    for t in (tid, oid):
        execute("DELETE FROM tenants WHERE id = %s", (t,))
        from backend.storage import paths
        for d in (paths.artifacts_dir(t, "x").parent, paths.dataset_dir(t, "x").parent,
                  paths.session_dir(t, "x").parent):
            shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def no_fit(monkeypatch):
    """Any attempt to fit a model fails the run, which fails the test."""
    import lightgbm
    from statsmodels.tsa.holtwinters import ExponentialSmoothing  # noqa: F401

    from forecasting_core.training.trainer import Trainer

    def boom(*_a, **_k):
        raise AssertionError("a model was fitted during a re-forecast")

    monkeypatch.setattr(lightgbm.LGBMRegressor, "fit", boom)
    monkeypatch.setattr(Trainer, "train", boom)


# ---------------------------------------------------------------------------

class TestTrainingStoresVerifiedModels:

    def test_every_family_is_registered_with_the_hash_of_its_bytes(self, world):
        rows = query("SELECT * FROM model_artifacts WHERE tenant_id=%s AND session_id=%s",
                     (world["tid"], world["sid"]))
        families = {r["family"] for r in rows}
        assert {"context", "lightgbm"} <= families
        for r in rows:
            data = Path(r["storage_path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == r["content_hash"]
            assert r["size_bytes"] == len(data) and r["inherited_from_session_id"] is None
            assert r["metadata"]["model_family"] == r["family"]
            assert len(r["metadata"]["input_schema_hash"]) == 64
            assert r["metadata"]["engine_version"]
            assert r["metadata"]["feature_spec"]["lags"] == [1, 7]

    def test_files_live_under_the_tenants_storage_and_nowhere_else(self, world):
        from backend.storage import paths
        root = paths.artifacts_dir(world["tid"], "x").parent.resolve()
        for r in query("SELECT storage_path FROM model_artifacts WHERE tenant_id=%s",
                       (world["tid"],)):
            assert root in Path(r["storage_path"]).resolve().parents

    def test_the_same_digests_are_in_the_immutable_manifest(self, world):
        m = _manifest(world["tid"], world["sid"])["manifest"]
        recorded = {a["family"]: a["sha256"] for a in m["model_artifacts"]}
        stored = {r["family"]: r["content_hash"] for r in
                  query("SELECT family, content_hash FROM model_artifacts "
                        "WHERE tenant_id=%s AND session_id=%s", (world["tid"], world["sid"]))}
        assert recorded == stored

    def test_the_session_records_when_its_models_were_fitted(self, world):
        s = _session(world["tid"], world["sid"])
        assert s["last_full_refit_at"] is not None
        assert s["is_reforecast"] is False and s["parent_session_id"] is None


class TestTamperedArtifactsAreRefused:

    def _lightgbm_file(self, world):
        return Path(query_one(
            "SELECT storage_path FROM model_artifacts WHERE tenant_id=%s AND session_id=%s "
            "AND family='lightgbm'", (world["tid"], world["sid"]))["storage_path"])

    def test_a_modified_file_is_refused_before_it_is_parsed(self, world):
        from backend.errors import AppError
        from backend.model_registry import service as registry
        path = self._lightgbm_file(world)
        original = path.read_bytes()
        try:
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
            with pytest.raises(AppError) as info:
                registry.load_verified(world["tid"], world["sid"])
            assert info.value.code == "artifact_integrity_failed"
            assert info.value.params["family"] == "lightgbm"
        finally:
            path.write_bytes(original)
        assert registry.load_verified(world["tid"], world["sid"])   # restored: loads again

    def test_a_row_edited_to_match_a_swapped_file_still_fails_the_manifest(self, world):
        """Changing the file AND its registry digest is not enough: the manifest
        written by the training is immutable and records the original."""
        from backend.errors import AppError
        from backend.model_registry import service as registry
        path = self._lightgbm_file(world)
        original = path.read_bytes()
        row = query_one("SELECT content_hash FROM model_artifacts WHERE tenant_id=%s "
                        "AND session_id=%s AND family='lightgbm'", (world["tid"], world["sid"]))
        forged = original[:-1] + bytes([original[-1] ^ 0x01])
        try:
            path.write_bytes(forged)
            execute("UPDATE model_artifacts SET content_hash=%s WHERE tenant_id=%s "
                    "AND session_id=%s AND family='lightgbm'",
                    (hashlib.sha256(forged).hexdigest(), world["tid"], world["sid"]))
            with pytest.raises(AppError) as info:
                registry.load_verified(world["tid"], world["sid"])
            assert info.value.code == "artifact_integrity_failed"
        finally:
            path.write_bytes(original)
            execute("UPDATE model_artifacts SET content_hash=%s WHERE tenant_id=%s "
                    "AND session_id=%s AND family='lightgbm'",
                    (row["content_hash"], world["tid"], world["sid"]))

    def test_a_re_forecast_on_a_tampered_artifact_fails_the_job_and_spares_the_parent(
        self, client, world,
    ):
        path = self._lightgbm_file(world)
        original = path.read_bytes()
        before = _forecast_by_series(world["tid"], world["sid"])
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast", headers=world["analyst"],
                        json={"dataset_id": world["same"]["id"]})
        assert r.status_code == 202, r.text
        child = r.json()["data"]["session_id"]
        try:
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
            _run_job(world["tid"], child)
        finally:
            path.write_bytes(original)
        job = query_one("SELECT status, error FROM jobs WHERE session_id=%s AND tenant_id=%s",
                        (child, world["tid"]))
        assert job["status"] == "FAILED"
        assert job["error"] == "reforecast_artifact_integrity_failed"
        assert _session(world["tid"], child)["status"] == "FAILED"
        assert _session(world["tid"], world["sid"])["status"] == "COMPLETED"
        assert _forecast_by_series(world["tid"], world["sid"]) == before


class TestAReforecastIsANewSessionAndNeverTouchesItsParent:

    def test_unchanged_data_reproduces_the_parents_forecast_without_fitting(
        self, client, world, no_fit,
    ):
        tid, sid = world["tid"], world["sid"]
        parent_before = {
            "session": dict(_session(tid, sid)),
            "forecasts": _forecast_by_series(tid, sid),
            "result": session_store.get_training_result(tid, sid),
            "artifacts": query("SELECT id, content_hash FROM model_artifacts "
                               "WHERE tenant_id=%s AND session_id=%s ORDER BY id", (tid, sid)),
        }
        r = client.post(f"/api/v1/sessions/{sid}/reforecast", headers=world["analyst"],
                        json={"dataset_id": world["same"]["id"]})
        assert r.status_code == 202, r.text
        data = r.json()["data"]
        child = data["session_id"]
        assert child != sid and data["parent_session_id"] == sid and data["status"] == "QUEUED"

        row = _session(tid, child)
        assert row["is_reforecast"] is True and row["parent_session_id"] == sid
        assert row["family_id"] == parent_before["session"]["family_id"]
        assert row["granularity"] == parent_before["session"]["granularity"]
        assert row["dataset_id"] == world["same"]["id"]
        assert row["last_full_refit_at"] == parent_before["session"]["last_full_refit_at"]

        _run_job(tid, child)
        assert _session(tid, child)["status"] == "COMPLETED"

        got = _forecast_by_series(tid, child)
        want = parent_before["forecasts"]
        assert got.keys() == want.keys() and got
        for key, points in want.items():
            assert [d for d, _ in got[key]] == [d for d, _ in points], key
            for (_, a), (_, b) in zip(got[key], points):
                assert a == pytest.approx(b, abs=1e-6), key

        # The parent: the same row, the same results, the same models on disk.
        after = _session(tid, sid)
        for col in ("status", "updated_at", "dataset_id", "last_full_refit_at",
                    "is_reforecast", "archived_at"):
            assert after[col] == parent_before["session"][col], col
        assert session_store.get_training_result(tid, sid) == parent_before["result"]
        assert _forecast_by_series(tid, sid) == want
        assert query("SELECT id, content_hash FROM model_artifacts WHERE tenant_id=%s "
                     "AND session_id=%s ORDER BY id", (tid, sid)) == parent_before["artifacts"]

    def test_the_child_shares_the_parents_models_instead_of_copying_them(self, world):
        tid, sid = world["tid"], world["sid"]
        child = query_one("SELECT id FROM sessions WHERE tenant_id=%s AND parent_session_id=%s "
                          "AND status='COMPLETED' ORDER BY created_at LIMIT 1", (tid, sid))["id"]
        parent = {r["family"]: r for r in query(
            "SELECT * FROM model_artifacts WHERE tenant_id=%s AND session_id=%s", (tid, sid))}
        mine = {r["family"]: r for r in query(
            "SELECT * FROM model_artifacts WHERE tenant_id=%s AND session_id=%s", (tid, child))}
        assert mine.keys() == parent.keys()
        for family, row in mine.items():
            assert row["content_hash"] == parent[family]["content_hash"]
            assert row["storage_path"] == parent[family]["storage_path"]
            assert row["inherited_from_session_id"] == sid

    def test_the_lineage_manifest_names_the_parent_the_trigger_the_data_and_the_artifacts(
        self, world,
    ):
        tid, sid = world["tid"], world["sid"]
        child = query_one("SELECT id FROM sessions WHERE tenant_id=%s AND parent_session_id=%s "
                          "AND status='COMPLETED' ORDER BY created_at LIMIT 1", (tid, sid))["id"]
        m = _manifest(tid, child)["manifest"]
        parent_m = _manifest(tid, sid)["manifest"]
        assert m["trigger"]["kind"] == "user"
        lineage = m["reforecast"]
        assert lineage["parent_session_id"] == sid
        assert lineage["parent_dataset_hash"] == parent_m["dataset"]["content_hash"]
        assert m["dataset"]["content_hash"] == hashlib.sha256(world["csv"]).hexdigest()
        used = {a["family"]: a for a in lineage["artifacts_used"]}
        assert used["lightgbm"]["sha256"] == {
            a["family"]: a["sha256"] for a in parent_m["model_artifacts"]}["lightgbm"]
        assert used["lightgbm"]["origin_session_id"] == sid
        assert lineage["summary"]["by_mode"].get("updated", 0) > 0
        assert not lineage["summary"]["refit_needed"]
        assert lineage["last_full_refit_at"]

    def test_the_re_forecast_is_not_recorded_as_a_new_fit(self, world):
        tid, sid = world["tid"], world["sid"]
        child = query_one("SELECT id FROM sessions WHERE tenant_id=%s AND parent_session_id=%s "
                          "AND status='COMPLETED' ORDER BY created_at LIMIT 1", (tid, sid))["id"]
        rows = query("SELECT 1 FROM training_run_metrics WHERE tenant_id=%s AND session_id=%s",
                     (tid, child))
        assert rows == []
        payload = session_store.get_training_result(tid, child)["reforecast"]
        assert payload["parent_session_id"] == sid and payload["summary"]["by_mode"]

    def test_new_actuals_move_the_forecast_and_the_parent_keeps_its_own(
        self, client, world, no_fit,
    ):
        tid, sid = world["tid"], world["sid"]
        path = _dataset_path(tid, world["dataset"]["id"])
        original = path.read_bytes()
        parent_forecast = _forecast_by_series(tid, sid)
        parent_dates = {k: v[0][0] for k, v in parent_forecast.items()}
        try:
            # The file under the session's own dataset id is replaced by a newer one.
            path.write_bytes(_extend(original, days=5, factor=2.0))
            st = client.get(f"/api/v1/sessions/{sid}/reforecast/status",
                            headers=world["viewer"]).json()["data"]
            assert st["eligible"] is True and st["new_data"] is True and st["reason"] is None
            r = client.post(f"/api/v1/sessions/{sid}/reforecast", headers=world["analyst"])
            assert r.status_code == 202, r.text
            child = r.json()["data"]["session_id"]
            assert _session(tid, child)["dataset_id"] == world["dataset"]["id"]
            _run_job(tid, child)
            assert _session(tid, child)["status"] == "COMPLETED"
            m = _manifest(tid, child)["manifest"]
            assert m["dataset"]["content_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
            assert m["dataset"]["content_hash"] != m["reforecast"]["parent_dataset_hash"]
        finally:
            path.write_bytes(original)

        got = _forecast_by_series(tid, child)
        assert got.keys() == parent_forecast.keys()
        for key, points in got.items():
            assert points[0][0] > parent_dates[key], f"{key}: the origin did not advance"
        moved = [k for k in got if max(abs(a - b) for (_, a), (_, b) in
                                       zip(got[k], parent_forecast[k])) > 1e-6]
        assert moved, "the new actuals changed no forecast"
        assert _forecast_by_series(tid, sid) == parent_forecast
        # Once the parent's own file is back, the new data is no longer "new".
        st = client.get(f"/api/v1/sessions/{sid}/reforecast/status", headers=world["viewer"])
        assert st.json()["data"]["new_data"] is False


class TestWhatIsRefusedAndWhy:

    def test_a_window_that_moved_too_far_fails_with_its_code_and_the_parent_keeps_serving(
        self, client, world,
    ):
        tid, sid = world["tid"], world["sid"]
        far = _upload(client, world["admin"], _extend(world["csv"], days=140), "far.csv")
        r = client.post(f"/api/v1/sessions/{sid}/reforecast", headers=world["analyst"],
                        json={"dataset_id": far["id"]})
        assert r.status_code == 202, r.text
        child = r.json()["data"]["session_id"]
        job_id = _run_job(tid, child)
        job = query_one("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
        assert job["status"] == "FAILED" and job["error"] == "reforecast_window_shifted_too_far"
        assert _session(tid, child)["status"] == "FAILED"
        assert _session(tid, sid)["status"] == "COMPLETED"
        log = " ".join(session_store.get_logs(tid, child, job_id, tail=50))
        assert "window_shifted_too_far" in log and "shift_buckets" in log

    def test_a_schema_the_models_were_not_trained_on_fails_with_its_code(self, client, world):
        tid, sid = world["tid"], world["sid"]
        r = client.post(f"/api/v1/sessions/{sid}/reforecast", headers=world["analyst"],
                        json={"dataset_id": world["same"]["id"]})
        child = r.json()["data"]["session_id"]
        cfg = session_store.get_field(tid, child, "features_cfg")
        session_store.set_field(tid, child, "features_cfg", {**cfg, "lags": [1, 7, 14]})
        job_id = _run_job(tid, child)
        job = query_one("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
        assert job["status"] == "FAILED" and job["error"] == "reforecast_schema_incompatible"
        assert _session(tid, sid)["status"] == "COMPLETED"

    def test_nothing_new_is_refused_and_no_session_is_made(self, client, world):
        tid, sid = world["tid"], world["sid"]
        before = _sessions_of(tid)
        r = client.post(f"/api/v1/sessions/{sid}/reforecast", headers=world["analyst"])
        assert r.status_code == 409
        assert r.json()["error_code"] == "reforecast_no_new_data"
        assert _sessions_of(tid) == before

    def test_a_session_with_no_stored_models_is_refused(self, client, world):
        tid = world["tid"]
        legacy = session_svc.create_session(tid, "x", "trained-before-artifacts")["id"]
        execute("UPDATE sessions SET status='COMPLETED' WHERE id=%s", (legacy,))
        before = _sessions_of(tid)
        r = client.post(f"/api/v1/sessions/{legacy}/reforecast", headers=world["analyst"])
        assert r.status_code == 409 and r.json()["error_code"] == "reforecast_no_artifacts"
        st = client.get(f"/api/v1/sessions/{legacy}/reforecast/status", headers=world["viewer"])
        assert st.json()["data"]["eligible"] is False
        assert st.json()["data"]["reason"] == "no_artifacts"
        assert _sessions_of(tid) == before

    def test_a_session_that_is_not_completed_is_refused(self, client, world):
        tid = world["tid"]
        draft = session_svc.create_session(tid, "x", "draft")["id"]
        r = client.post(f"/api/v1/sessions/{draft}/reforecast", headers=world["analyst"])
        assert r.status_code == 409 and r.json()["error_code"] == "reforecast_parent_not_completed"


class TestWhoMayAskAndWhoseDataItIs:

    def test_a_viewer_is_denied_and_nothing_is_created(self, client, world):
        before = _sessions_of(world["tid"])
        jobs_before = query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id=%s",
                                (world["tid"],))["c"]
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast", headers=world["viewer"],
                        json={"dataset_id": world["same"]["id"]})
        assert r.status_code == 403
        assert _sessions_of(world["tid"]) == before
        assert query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id=%s",
                         (world["tid"],))["c"] == jobs_before

    def test_a_viewer_may_read_the_status(self, client, world):
        r = client.get(f"/api/v1/sessions/{world['sid']}/reforecast/status",
                       headers=world["viewer"])
        assert r.status_code == 200
        assert r.json()["data"]["has_artifacts"] is True

    def test_another_tenant_cannot_see_or_start_it(self, client, world):
        oid = world["oid"]
        before = _sessions_of(world["tid"]) | _sessions_of(oid)
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast",
                        headers=world["other_analyst"])
        assert r.status_code == 404
        r = client.get(f"/api/v1/sessions/{world['sid']}/reforecast/status",
                       headers=world["other_analyst"])
        assert r.status_code == 404
        assert _sessions_of(world["tid"]) | _sessions_of(oid) == before
        from backend.errors import AppError
        from backend.model_registry import service as registry
        assert registry.list_artifacts(oid, world["sid"]) == []
        with pytest.raises(AppError) as info:
            registry.load_verified(oid, world["sid"])
        assert info.value.code == "artifacts_not_found"

    def test_a_dataset_of_another_tenant_is_not_a_valid_target(self, client, world):
        theirs = _upload(client, world["other_analyst"], world["csv"], "theirs.csv")
        before = _sessions_of(world["tid"])
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast", headers=world["analyst"],
                        json={"dataset_id": theirs["id"]})
        assert r.status_code == 404
        assert _sessions_of(world["tid"]) == before


class TestPlanCeilings:

    def test_a_re_forecast_counts_as_a_saved_forecast_and_stops_at_the_ceiling(
        self, client, world, monkeypatch,
    ):
        tid = world["tid"]
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        # Free tier: 3 saved forecasts. Archive what earlier tests made, then fill
        # the working list to the ceiling with the parent and two others.
        for sid in _sessions_of(tid) - {world["sid"]}:
            session_svc.archive_session(tid, sid, "test")
        assert session_svc.count_sessions(tid) == 1
        for i in range(2):
            session_svc.create_session(tid, "x", f"filler-{i}")
        assert session_svc.count_sessions(tid) == 3

        before = _sessions_of(tid)
        jobs_before = query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id=%s", (tid,))["c"]
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast", headers=world["analyst"],
                        json={"dataset_id": world["same"]["id"]})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
        assert _sessions_of(tid) == before
        assert query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id=%s",
                         (tid,))["c"] == jobs_before

        # Archiving one frees the slot: the ceiling only ever refuses to create.
        filler = query_one("SELECT id FROM sessions WHERE tenant_id=%s AND name='filler-0'",
                           (tid,))["id"]
        session_svc.archive_session(tid, filler, "test")
        r = client.post(f"/api/v1/sessions/{world['sid']}/reforecast", headers=world["analyst"],
                        json={"dataset_id": world["same"]["id"]})
        assert r.status_code == 202, r.text
        assert r.json()["data"]["session_id"] in _sessions_of(tid) - before
        assert session_svc.count_sessions(tid) == 3


class TestTheScheduledChoice:
    """'Re-forecast daily, refit periodically'."""

    def _schedule(self, client, world, mode="reforecast"):
        r = client.post(f"/api/v1/sessions/{world['sid']}/schedule", headers=world["analyst"],
                        json={"cron_expr": "0 6 * * *", "retrain_mode": mode})
        assert r.status_code == 200, r.text
        return r.json()["data"]["id"]

    def _launch(self, world, schedule_id):
        from backend.sessions import retrain_service
        return retrain_service.launch_scheduled_retrain(
            world["tid"], schedule_id, world["sid"])

    @pytest.fixture(autouse=True)
    def _room(self, world):
        # Whatever an earlier test left queued is cancelled, so it neither runs nor
        # counts as a re-forecast in flight.
        execute("UPDATE jobs SET status='CANCELLED' WHERE tenant_id=%s "
                "AND status IN ('QUEUED','RUNNING')", (world["tid"],))
        execute("UPDATE sessions SET status='CANCELLED' WHERE tenant_id=%s AND id<>%s "
                "AND status IN ('QUEUED','RUNNING','MODELS_CONFIGURED')",
                (world["tid"], world["sid"]))
        for sid in _sessions_of(world["tid"]) - {world["sid"]}:
            session_svc.archive_session(world["tid"], sid, "test")
        execute("DELETE FROM scheduled_jobs WHERE tenant_id=%s", (world["tid"],))
        execute("DELETE FROM schedule_runs WHERE tenant_id=%s", (world["tid"],))
        execute("UPDATE sessions SET last_full_refit_at = NOW() WHERE id=%s", (world["sid"],))

    def test_the_mode_is_stored_and_a_resave_that_omits_it_keeps_it(self, client, world):
        sched = self._schedule(client, world)
        assert query_one("SELECT retrain_mode FROM scheduled_jobs WHERE id=%s",
                         (sched,))["retrain_mode"] == "reforecast"
        r = client.post(f"/api/v1/sessions/{world['sid']}/schedule", headers=world["analyst"],
                        json={"cron_expr": "0 7 * * *"})
        assert r.status_code == 200
        assert query_one("SELECT retrain_mode FROM scheduled_jobs WHERE id=%s",
                         (sched,))["retrain_mode"] == "reforecast"
        got = client.get(f"/api/v1/sessions/{world['sid']}/schedule", headers=world["viewer"])
        assert got.json()["data"]["retrain_mode"] == "reforecast"

    def test_an_unknown_mode_is_rejected_and_viewers_cannot_set_one(self, client, world):
        r = client.post(f"/api/v1/sessions/{world['sid']}/schedule", headers=world["analyst"],
                        json={"cron_expr": "0 6 * * *", "retrain_mode": "whenever"})
        assert r.status_code == 422
        r = client.post(f"/api/v1/sessions/{world['sid']}/schedule", headers=world["viewer"],
                        json={"cron_expr": "0 6 * * *", "retrain_mode": "reforecast"})
        assert r.status_code == 403
        assert query("SELECT 1 FROM scheduled_jobs WHERE tenant_id=%s", (world["tid"],)) == []

    def test_young_models_are_re_forecast_not_refitted(self, client, world, no_fit):
        tid = world["tid"]
        sched = self._schedule(client, world)
        before = _sessions_of(tid)
        launched = self._launch(world, sched)
        assert launched["mode"] == "reforecast" and launched["sessions"]
        made = _sessions_of(tid) - before
        assert len(made) == len(launched["sessions"]) >= 1
        for sid in made:
            row = _session(tid, sid)
            assert row["is_reforecast"] is True and row["parent_session_id"] == world["sid"]
            assert row["scheduled_job_id"] == sched and row["status"] == "QUEUED"
        child = launched["sessions"][0]["session_id"]
        _run_job(tid, child)
        assert _session(tid, child)["status"] == "COMPLETED"
        assert _manifest(tid, child)["manifest"]["trigger"]["kind"] == "schedule"
        run = query_one("SELECT * FROM schedule_runs WHERE tenant_id=%s AND schedule_id=%s",
                        (tid, sched))
        assert run["outcome"] == "launched" and run["session_id"] == child
        assert run["reason_params"]["mode"] == "reforecast"

    def test_old_models_are_refitted_in_full(self, client, world):
        tid = world["tid"]
        sched = self._schedule(client, world)
        # 10 days since the last full fit, against the default 7.
        execute("UPDATE sessions SET last_full_refit_at = NOW() - interval '10 days' "
                "WHERE id=%s", (world["sid"],))
        before = _sessions_of(tid)
        self._launch(world, sched)
        made = _sessions_of(tid) - before
        assert made
        rows = [_session(tid, s) for s in made]
        assert all(r["is_reforecast"] is False for r in rows), "an old model was only re-forecast"
        assert any(r["scheduled_job_id"] == sched for r in rows)

    def test_the_age_limit_is_the_registry_setting(self, client, world, monkeypatch):
        tid = world["tid"]
        sched = self._schedule(client, world)
        execute("UPDATE sessions SET last_full_refit_at = NOW() - interval '10 days' "
                "WHERE id=%s", (world["sid"],))
        monkeypatch.setattr("backend.config.settings.reforecast_full_refit_days", 30)
        before = _sessions_of(tid)
        launched = self._launch(world, sched)
        assert launched["mode"] == "reforecast"
        assert all(_session(tid, s)["is_reforecast"] for s in _sessions_of(tid) - before)

    def test_a_schedule_left_on_refit_never_re_forecasts(self, client, world):
        tid = world["tid"]
        sched = self._schedule(client, world, mode="refit")
        before = _sessions_of(tid)
        self._launch(world, sched)
        assert all(not _session(tid, s)["is_reforecast"] for s in _sessions_of(tid) - before)

    def test_a_failed_re_forecast_makes_the_next_run_refit(self, client, world):
        tid = world["tid"]
        sched = self._schedule(client, world)
        launched = self._launch(world, sched)
        child = launched["sessions"][0]["session_id"]
        execute("UPDATE sessions SET status='FAILED' WHERE id=%s", (child,))
        execute("DELETE FROM schedule_runs WHERE tenant_id=%s", (tid,))
        for sid in [s["session_id"] for s in launched["sessions"]]:
            execute("UPDATE sessions SET status='FAILED' WHERE id=%s", (sid,))
        before = _sessions_of(tid)
        self._launch(world, sched)
        made = _sessions_of(tid) - before
        assert made and all(not _session(tid, s)["is_reforecast"] for s in made)

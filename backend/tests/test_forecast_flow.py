"""
PHASE 2 — Forecast pipeline flow tests.
Tests the complete wizard → train → results → forecast series flow.
Training is NOT executed — results are seeded directly via seed_completed_session().
"""

from unittest import mock


class TestTrainingEndpoints:
    def test_start_training_queues_job(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert "job_id" in data
        assert data["status"] == "QUEUED"

    def test_start_training_before_wizard_complete_is_rejected(self, client, auth_headers, test_session, uploaded_dataset):
        sid = test_session["id"]
        # Attach dataset and configure only columns — no models config
        client.post(f"/api/v1/sessions/{sid}/dataset",
                    json={"dataset_id": uploaded_dataset["id"]}, headers=auth_headers)
        client.post(f"/api/v1/sessions/{sid}/configure/columns",
                    json={"date_column": "date", "target_column": "sales"},
                    headers=auth_headers)
        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        # The wizard state machine rejects training before MODELS_CONFIGURED
        # with a 409 (invalid state); a 422 (validation) is also acceptable.
        assert resp.status_code in (409, 422)

    def test_start_training_on_nonexistent_session_returns_404(self, client, auth_headers):
        resp = client.post("/api/v1/sessions/sess_ghost/train", headers=auth_headers)
        assert resp.status_code == 404

    def test_viewer_cannot_start_training(self, client, viewer_headers, configured_session):
        sid = configured_session["id"]
        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=viewer_headers)
        assert resp.status_code == 403

    def test_list_jobs_for_session(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        resp = client.get(f"/api/v1/sessions/{sid}/jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_get_job_status(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        train = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = train.json()["data"]["job_id"]
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == job_id

    def test_get_train_status_endpoint(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        resp = client.get(f"/api/v1/sessions/{sid}/train/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["session_id"] == sid

    def test_cancel_queued_job(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        train = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = train.json()["data"]["job_id"]
        resp = client.delete(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "CANCELLED"

    def test_training_failure_transitions_job_and_session_to_failed(
        self, client, auth_headers, configured_session, registered_user,
    ):
        """
        Forces a real failure inside engine.train() (not a hand-set DB status) by
        running the worker's run_training_job synchronously with ForecastEngine.train
        mocked to raise. Everything before that — load_data, data quality, routing —
        runs for real against the configured_session's actual uploaded CSV. Verifies
        the FAILED path end-to-end: job row, session row, and that /results correctly
        409s afterward. This was previously completely untested — every other
        training test only exercises the COMPLETED path via seed_completed_session.
        """
        sid = configured_session["id"]
        tenant_id = registered_user["tenant"]["id"]

        train = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert train.status_code == 202
        job_id = train.json()["data"]["job_id"]

        from backend.workers.runner import run_training_job
        from backend.training import job_service
        from backend.sessions import service as session_svc
        from forecasting_core.engine import ForecastEngine

        with mock.patch.object(
            ForecastEngine, "train", side_effect=RuntimeError("synthetic training failure")
        ):
            run_training_job(tenant_id, sid, job_id)

        job = job_service.get_job(tenant_id, job_id)
        assert job["status"] == "FAILED"
        assert "synthetic training failure" in job["error"]

        session = session_svc.get_session(tenant_id, sid)
        assert session["status"] == "FAILED"

        results = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert results.status_code == 409

        status_resp = client.get(f"/api/v1/sessions/{sid}/train/status", headers=auth_headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["data"]["status"] == "FAILED"
        assert status_resp.json()["data"]["job"]["status"] == "FAILED"


class TestResultsEndpoints:
    def test_get_training_results(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "metrics" in data
        assert data["metrics"]["n_skus"] == 3

    def test_get_metrics(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/metrics", headers=auth_headers)
        assert resp.status_code == 200
        metrics = resp.json()["data"]
        assert "rows" in metrics
        assert len(metrics["rows"]) > 0

    def test_metrics_rows_have_required_fields(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/metrics", headers=auth_headers)
        for row in resp.json()["data"]["rows"]:
            for field in ("sku", "model", "mae", "rmse", "wape"):
                assert field in row, f"Missing field '{field}' in metrics row"

    def test_get_inventory(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/inventory", headers=auth_headers)
        assert resp.status_code == 200

    def test_get_routing(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/routing", headers=auth_headers)
        assert resp.status_code == 200

    def test_get_data_quality(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/quality", headers=auth_headers)
        assert resp.status_code == 200

    def test_results_on_non_completed_session_returns_409(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert resp.status_code == 409

    def test_report_alias_endpoint(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/report", headers=auth_headers)
        assert resp.status_code == 200

    def test_routing_plan_alias_endpoint(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/routing-plan", headers=auth_headers)
        assert resp.status_code == 200

    def test_export_config_endpoint(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/export-config", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        assert "columns" in cfg
        assert "models" in cfg


class TestForecastSeriesEndpoints:
    def test_get_forecast_series_returns_historical_and_forecast(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sku"] == "SKU_001"
        assert isinstance(data["historical"], list)
        assert isinstance(data["forecast"], list)
        assert len(data["historical"]) > 0
        assert len(data["forecast"]) > 0

    def test_forecast_series_has_correct_structure(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        data = resp.json()["data"]
        for pt in data["forecast"]:
            assert "date" in pt
            assert "value" in pt

    def test_forecast_series_with_model_filter(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(
            f"/api/v1/sessions/{sid}/forecast-series/SKU_001?model=lightgbm",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["model"] == "lightgbm"

    def test_forecast_series_available_models(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        data = resp.json()["data"]
        assert "available_models" in data
        assert len(data["available_models"]) >= 1

    def test_forecast_series_nonexistent_sku_returns_empty(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/GHOST_SKU", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sku"] == "GHOST_SKU"
        assert data["forecast"] == []

    def test_forecast_series_on_non_completed_session_returns_409(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        assert resp.status_code == 409

    def test_predict_endpoint(self, client, auth_headers, completed_session):
        sid = completed_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/predict",
            json={"sku": "SKU_001", "horizon": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sku"] == "SKU_001"
        assert "result" in data


class TestJobLogs:
    def test_get_job_logs(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        train = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = train.json()["data"]["job_id"]
        resp = client.get(f"/api/v1/jobs/{job_id}/logs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "lines" in data
        assert "job_id" in data

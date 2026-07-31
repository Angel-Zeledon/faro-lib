"""
PHASE 2 — End-to-end integration: full wizard → mock training → results.
Uses mock.patch to replace forecasting_core.engine.ForecastEngine with a stub
that completes instantly with deterministic results.
Tests that the runner stores results correctly and endpoints serve them.
"""

import pytest
from unittest import mock
from datetime import date, timedelta


def _make_mock_engine(n_skus: int = 3, horizon: int = 14):
    """
    Returns a MagicMock that mimics ForecastEngine's interface.
    Produces deterministic synthetic outputs.
    """
    import random
    rng = random.Random(42)
    skus = [f"SKU_{i + 1:03d}" for i in range(n_skus)]
    hist_start = date(2023, 1, 1)
    fc_start = date(2023, 4, 1)

    # Minimal historical DataFrame stub
    mock_df = mock.MagicMock()
    mock_df.empty = False
    mock_df.__bool__ = lambda self: True

    fc_rows = []
    for sku in skus:
        for step in range(horizon):
            fc_rows.append({
                "sku": sku,
                "model": "lightgbm",
                "step": step,
                "date": (fc_start + timedelta(days=step)).isoformat(),
                "forecast": round(rng.uniform(10, 100), 2),
                "p90_lo": round(rng.uniform(5, 90), 2),
                "p90_hi": round(rng.uniform(15, 110), 2),
            })

    metrics_rows = [
        {"sku": sku, "model": "lightgbm", "mae": 3.5, "rmse": 5.1, "wape": 0.12}
        for sku in skus
    ]
    inventory_rows = [
        {"sku": sku, "reorder_point": 100.0, "safety_stock": 20.0}
        for sku in skus
    ]

    engine = mock.MagicMock()
    engine._df = mock_df
    engine._run_id = "mock_run_001"
    engine.get_forecast.return_value = {"rows": fc_rows, "n_skus": n_skus, "horizon": horizon}
    engine.get_metrics.return_value = {"rows": metrics_rows, "n_models": 1, "n_skus": n_skus}
    engine.get_inventory_report.return_value = {"rows": inventory_rows}
    engine.generate_report.return_value = {}
    engine.get_routing_plan.return_value = {"rows": [{"sku": s, "assigned_model": "lightgbm"} for s in skus]}
    engine.get_data_quality_report.return_value = {
        "n_series": n_skus, "issues": [],
        "rows": [{"sku": s, "completeness": 1.0, "n_rows": 90} for s in skus],
    }
    # Everything the runner puts into the stored training result has to be
    # JSON-serialisable. A MagicMock's auto-attributes are not, so any engine
    # method the runner persists must be stubbed here explicitly — otherwise the
    # failure surfaces as "MagicMock is not JSON serializable" from psycopg2,
    # far away from the method that was actually missing.
    engine.get_demand_risk.return_value = {}
    engine.get_policy_backtest.return_value = {}
    engine.get_run_warnings.return_value = {"validation": [], "corrections": []}
    engine.export_config.return_value = None
    return engine


@pytest.mark.integration
class TestMockedTrainingE2E:
    """
    Full E2E: configure session → trigger training → worker calls runner
    → mock engine completes → results available via API.
    """

    def _run_training_synchronously(self, tenant_id: str, session_id: str, mock_engine):
        """
        Simulates what the worker does: calls run_training_job synchronously
        with the engine mocked out.
        ForecastEngine is imported inside run_training_job, so we patch the
        source module (forecasting_core.engine.ForecastEngine).
        """
        from backend.workers.runner import run_training_job
        from backend.training.job_service import list_jobs_for_session
        jobs = list_jobs_for_session(tenant_id, session_id)
        assert jobs, "No jobs created for session"
        job_id = jobs[0]["id"]

        with mock.patch("forecasting_core.engine.ForecastEngine") as MockFE:
            MockFE.from_dict.return_value = mock_engine
            run_training_job(tenant_id, session_id, job_id)

        return job_id

    def test_e2e_training_completes_and_stores_results(
        self, client, auth_headers, configured_session, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        mock_engine = _make_mock_engine(n_skus=2)

        # Trigger training
        train_resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert train_resp.status_code == 202

        # Simulate worker
        self._run_training_synchronously(tid, sid, mock_engine)

        # Assert results available
        results_resp = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert results_resp.status_code == 200
        data = results_resp.json()["data"]
        assert "metrics" in data
        assert data["metrics"]["n_skus"] == 2

    def test_e2e_forecast_series_available_after_training(
        self, client, auth_headers, configured_session, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        mock_engine = _make_mock_engine(n_skus=2)

        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        self._run_training_synchronously(tid, sid, mock_engine)

        resp = client.get(f"/api/v1/sessions/{sid}/forecast-series/SKU_001", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["forecast"]) == 14
        assert data["model"] == "lightgbm"

    def test_e2e_session_status_is_completed_after_training(
        self, client, auth_headers, configured_session, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        mock_engine = _make_mock_engine(n_skus=2)

        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        self._run_training_synchronously(tid, sid, mock_engine)

        resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.json()["data"]["status"] == "COMPLETED"

    def test_e2e_job_status_is_completed_after_training(
        self, client, auth_headers, configured_session, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        mock_engine = _make_mock_engine(n_skus=2)

        train_resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        job_id = train_resp.json()["data"]["job_id"]
        self._run_training_synchronously(tid, sid, mock_engine)

        job_resp = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert job_resp.json()["data"]["status"] == "COMPLETED"

    def test_e2e_metrics_have_correct_sku_count(
        self, client, auth_headers, configured_session, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        mock_engine = _make_mock_engine(n_skus=3)

        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        self._run_training_synchronously(tid, sid, mock_engine)

        resp = client.get(f"/api/v1/sessions/{sid}/metrics", headers=auth_headers)
        rows = resp.json()["data"]["rows"]
        skus_in_metrics = {r["sku"] for r in rows}
        assert len(skus_in_metrics) == 3


@pytest.mark.integration
class TestRunnerConfigBuilder:
    def test_build_engine_config_with_full_wizard(self, configured_session, registered_user):
        from backend.workers.runner import build_engine_config
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        config = build_engine_config(tid, sid)

        assert config["columns"]["date"] == "date"
        assert config["columns"]["target"] == "sales"
        # Phase 1 migrated the single `group` key to a `group_keys` list.
        assert config["columns"]["group_keys"] == ["sku"]

    def test_build_engine_config_missing_dataset_returns_empty_path(self, test_session, registered_user):
        from backend.workers.runner import build_engine_config
        tid = registered_user["tenant"]["id"]
        sid = test_session["id"]
        config = build_engine_config(tid, sid)
        assert config["data"]["path"] == ""

    def test_build_engine_config_includes_model_dict(self, configured_session, registered_user):
        from backend.workers.runner import build_engine_config
        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        config = build_engine_config(tid, sid)
        assert "lightgbm" in config["models"]

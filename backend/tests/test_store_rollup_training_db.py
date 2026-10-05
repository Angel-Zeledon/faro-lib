"""A two-store sales file, trained end to end on the real engine and queue.

Before the fix the stored forecast for a SKU selling 100 + 10 = 110/week across
two stores was ~10/week (one store's number), with an empty history, and the
run completed without a word. Every assertion here reads the database or the
file on disk, not a response.
"""
import csv
import hashlib
import io
from datetime import date, timedelta

import pytest

from backend.db import session_store
from backend.db.connection import query_one


def _two_store_csv(days: int = 140) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha", "producto", "tienda", "ventas"])
    start = date(2026, 1, 1)
    for sku in ("A", "B"):
        for store, weekly in (("Norte", 100.0), ("Sur", 10.0)):
            for i in range(days):
                # A small deterministic wiggle, so no model sees a flat line.
                v = weekly / 7.0 * (1 + 0.05 * ((i % 5) - 2))
                w.writerow([(start + timedelta(days=i)).isoformat(), sku, store, round(v, 3)])
    return buf.getvalue().encode()


def _run_job(tenant_id, session_id):
    from backend.training.job_service import list_jobs_for_session
    from backend.workers.runner import run_training_job
    job = list_jobs_for_session(tenant_id, session_id)[0]
    run_training_job(tenant_id, session_id, job["id"])
    return job["id"]


@pytest.mark.integration
class TestTwoStoreFileTrainsOnTheSkuTotal:
    def test_stored_forecast_is_the_sku_total_and_the_run_says_stores_were_summed(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        data = _two_store_csv()
        r = client.post("/api/v1/datasets",
                        files={"file": ("tiendas.csv", data, "text/csv")},
                        headers=auth_headers)
        assert r.status_code == 201, r.text
        dataset_id = r.json()["data"]["id"]

        sid = client.post("/api/v1/sessions", json={"name": "two-stores"},
                          headers=auth_headers).json()["data"]["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id},
                    headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"canonical_mapping": {"sku": "producto", "date": "fecha",
                                        "demand": "ventas", "store": "tienda"}},
            headers=auth_headers)
        assert r.status_code == 200, r.text
        client.post(f"/api/v1/sessions/{sid}/configure/features",
                    json={"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True},
                    headers=auth_headers)
        r = client.post(f"/api/v1/sessions/{sid}/configure/models",
                        json={"mode": "selected", "selected_models": ["lightgbm"],
                              "hyperparameters": {"lightgbm": {"n_estimators": 50}}},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert r.status_code == 202, r.text
        job_id = _run_job(tid, sid)

        status = query_one("SELECT status FROM sessions WHERE id=%s AND tenant_id=%s",
                           (sid, tid))["status"]
        assert status == "COMPLETED"

        # The stored forecast: one series per SKU, at the SKU-total scale.
        forecasts = session_store.get_forecasts(tid, sid)
        assert set(forecasts) == {"A", "B"}, sorted(forecasts)
        for sku in ("A", "B"):
            series = forecasts[sku]["lightgbm"]
            horizon = len(series["forecast"])
            assert horizon > 0
            daily = sum(p["value"] for p in series["forecast"]) / horizon
            # 110/week = 15.7/day. One store alone would be 14.3 or 1.4.
            assert daily * 7 == pytest.approx(110.0, rel=0.15), (sku, daily * 7)
            assert series["historical"], "history must be stored next to the forecast"

        # The run says what it did, where the results screen reads it...
        result = session_store.get_training_result(tid, sid)
        rollup = result["store_rollup"]
        assert rollup["applied"] is True
        assert rollup["n_stores"] == 2 and rollup["n_skus"] == 2
        assert result["config"]["columns"]["group_keys"] == ["producto"]
        codes = [g["code"] for g in result["warnings"]["validation"]]
        assert "PREP_STORES_SUMMED" in codes
        # ...per-store freshness still knows both stores...
        assert set(result["store_data_through"]) == {"Norte", "Sur"}

        # ...and so does the immutable lineage record of this job.
        manifest = query_one(
            "SELECT manifest FROM session_manifests WHERE tenant_id=%s AND job_id=%s",
            (tid, job_id))["manifest"]
        assert manifest["store_rollup"]["n_stores"] == 2

        # The upload itself is untouched.
        path = query_one("SELECT file_path FROM datasets WHERE id=%s AND tenant_id=%s",
                         (dataset_id, tid))["file_path"]
        with open(path, "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == hashlib.sha256(data).hexdigest()

"""
One-off E2E verification that sample_data/quickstart_demo.csv works through the
real backend pipeline: upload -> attach -> inspect -> configure columns ->
configure features/models/validation/forecast/business -> train -> inventory.

NOT a pytest file. Run with `python -m tests._verify_demo_data` from backend/.
"""
import json
import os
import sys
from unittest import mock
from uuid import uuid4

_worker_patch = mock.patch("backend.workers.worker.start")
_worker_patch.start()  # must stay active through TestClient(...).__enter__() below,
# since that's what actually triggers FastAPI's lifespan (which calls worker.start()) —
# closing the patch earlier lets a real background worker thread spin up and race with
# this script's own direct run_training_job() calls.

from backend.main import app

from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=True).__enter__()

from backend.tenants.service import create_tenant
from backend.users import service as user_svc
from backend.db.connection import execute as _exec, _json as _to_json

TENANT = create_tenant(f"verify-{uuid4().hex[:8]}")
_exec(
    "UPDATE tenants SET quota = %s WHERE id = %s",
    (_to_json({"max_sessions": 100000, "max_skus_per_session": 1000000,
               "max_concurrent_jobs": 100, "max_dataset_size_mb": 500}), TENANT["id"]),
)
EMAIL = f"verify-{uuid4().hex[:8]}@example.com"
PASSWORD = "TestPass123!"
_user = user_svc.create_user(TENANT["id"], EMAIL, PASSWORD, "admin", "Verify Bot")
user_svc.mark_verified(TENANT["id"], _user["id"])

_resp = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
assert _resp.status_code == 200, _resp.text
TOKEN = _resp.json()["data"]["access_token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "quickstart_demo.csv")
CSV_PATH = os.path.abspath(CSV_PATH)

out = {}

def fail(stage, body):
    out["failed_stage"] = stage
    out["body"] = body
    print(json.dumps(out, indent=2, default=str))
    sys.exit(1)

r = client.post("/api/v1/sessions", json={"name": f"verify-demo-{uuid4().hex[:6]}"}, headers=HEADERS)
if r.status_code != 201:
    fail("create_session", r.text)
sid = r.json()["data"]["id"]
out["session_id"] = sid

with open(CSV_PATH, "rb") as f:
    file_bytes = f.read()
out["csv_size_bytes"] = len(file_bytes)

r = client.post("/api/v1/datasets", files={"file": ("quickstart_demo.csv", file_bytes, "text/csv")}, headers=HEADERS)
out["upload_status"] = r.status_code
if r.status_code != 201:
    fail("upload", r.text)
ds_id = r.json()["data"]["id"]

r = client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": ds_id}, headers=HEADERS)
out["attach_status"] = r.status_code
if r.status_code != 200:
    fail("attach", r.text)

r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=HEADERS)
out["inspect_status"] = r.status_code
if r.status_code != 200:
    fail("inspect", r.text)

data = r.json()["data"]
profile = data.get("profile", {})
opts = data.get("column_options", {})
out["warnings"] = profile.get("warnings")
out["data_quality_issues"] = [i.get("type") for i in profile.get("data_quality", {}).get("issues", [])]
out["recommended"] = profile.get("recommended")
out["date_candidates"] = opts.get("date_candidates")
out["target_candidates"] = opts.get("target_candidates")
out["group_candidates"] = opts.get("group_candidates")
out["n_rows"] = profile.get("stats", {}).get("n_rows")

assert "date" in (opts.get("date_candidates") or []), f"expected 'date' detected, got {opts.get('date_candidates')}"
assert "cantidad_vendida" in (opts.get("target_candidates") or []), f"expected 'cantidad_vendida' detected, got {opts.get('target_candidates')}"
assert "sku" in (opts.get("group_candidates") or []), f"expected 'sku' detected, got {opts.get('group_candidates')}"

dc = "date"
tc = "cantidad_vendida"
sc = "sku"

body = {
    "date_column": dc, "target_column": tc, "sku_column": sc,
    "gap_fill": "forward",
    "outlier_config": {"strategy": "leave"},
}
r = client.post(f"/api/v1/sessions/{sid}/configure/columns", json=body, headers=HEADERS)
out["configure_columns_status"] = r.status_code
if r.status_code != 200:
    fail("configure_columns", r.text)

r = client.post(f"/api/v1/sessions/{sid}/configure/features",
                json={"lags": [1, 7], "rolling": [7], "diffs": [], "calendar": True},
                headers=HEADERS)
out["configure_features_status"] = r.status_code
if r.status_code != 200:
    fail("configure_features", r.text)

r = client.post(f"/api/v1/sessions/{sid}/configure/models",
                json={"mode": "selected", "selected_models": ["lightgbm", "xgboost"]},
                headers=HEADERS)
out["configure_models_status"] = r.status_code
if r.status_code != 200:
    fail("configure_models", r.text)

r = client.post(f"/api/v1/sessions/{sid}/configure/validation",
                json={"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3,
                      "min_history": 14, "seasonal_period": 7},
                headers=HEADERS)
out["configure_validation_status"] = r.status_code
if r.status_code != 200:
    fail("configure_validation", r.text)

r = client.post(f"/api/v1/sessions/{sid}/config/forecast", json={"horizon": 30}, headers=HEADERS)
out["config_forecast_status"] = r.status_code
if r.status_code != 200:
    fail("config_forecast", r.text)

r = client.post(f"/api/v1/sessions/{sid}/config/business",
                json={"service_level": 0.95, "lead_time_days": 7}, headers=HEADERS)
out["config_business_status"] = r.status_code
if r.status_code != 200:
    fail("config_business", r.text)

r = client.post(f"/api/v1/sessions/{sid}/train", headers=HEADERS)
out["start_training_status"] = r.status_code
if r.status_code != 202:
    fail("start_training", r.text)
job_id = r.json()["data"]["job_id"]

from backend.workers.runner import run_training_job
try:
    run_training_job(TENANT["id"], sid, job_id)
except Exception as e:
    out["run_training_job_exception"] = f"{type(e).__name__}: {e}"

from backend.training.job_service import get_job
job = get_job(TENANT["id"], job_id)
out["job_status"] = job.get("status") if job else None
out["job_error"] = job.get("error") if job else None

if not job or job.get("status") != "COMPLETED":
    fail("training", out.get("job_error") or out.get("run_training_job_exception"))

from backend.inventory.service import get_inventory_status
items = get_inventory_status(TENANT["id"], sid)
out["inventory_sku_count"] = len(items)
out["inventory_skus"] = sorted(i["sku"] for i in items)
out["inventory_signals"] = {i["sku"]: i["signal"] for i in items}

expected_skus = {"SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005", "SKU-006"}
found_skus = set(out["inventory_skus"])
out["missing_skus"] = sorted(expected_skus - found_skus)
out["unexpected_skus"] = sorted(found_skus - expected_skus)

from backend.db import session_store
training_result = session_store.get_training_result(TENANT["id"], sid)
metric_rows = (training_result or {}).get("metrics", {}).get("rows", [])
out["n_training_results"] = len(metric_rows)
out["training_results_per_sku"] = {}
for row in metric_rows:
    out["training_results_per_sku"].setdefault(str(row.get("sku")), []).append(row.get("model"))

print(json.dumps(out, indent=2, default=str))

if out["missing_skus"]:
    print(f"\nFAIL: missing SKUs in inventory output: {out['missing_skus']}")
    sys.exit(1)

print("\nOK: full pipeline completed for all 6 SKUs.")

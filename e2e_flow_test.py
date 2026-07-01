"""
End-to-end flow driver: reproduces the real UI flow against the running backend
to catch the SKU-count / __all__ bug that unit tests miss.

Usage: python e2e_flow_test.py <csv_path> <date_col> <target_col> <sku_col>
"""
import sys, time, re, uuid, json
import requests
import psycopg2

BASE = "http://localhost:8002/api/v1"
CSV   = sys.argv[1] if len(sys.argv) > 1 else "demo_ventas.csv"
DATE  = sys.argv[2] if len(sys.argv) > 2 else "fecha"
TGT   = sys.argv[3] if len(sys.argv) > 3 else "cantidad"
SKU   = sys.argv[4] if len(sys.argv) > 4 else "sku"


def unwrap(resp):
    j = resp.json()
    return j.get("data", j) if isinstance(j, dict) else j


def step(name, resp, show=False):
    ok = resp.status_code < 400
    print(f"[{'OK ' if ok else 'ERR'}] {name}: {resp.status_code}")
    if not ok or show:
        print("     ", resp.text[:300])
    resp.raise_for_status()
    return unwrap(resp)


# uploaded SKU truth
import csv as _csv
rows = list(_csv.DictReader(open(CSV, encoding="utf-8-sig")))
uploaded_skus = sorted(set(r[SKU] for r in rows)) if SKU in (rows[0] if rows else {}) else ["<single-series>"]
print(f"\n### Uploaded file: {CSV} — {len(rows)} rows, {len(uploaded_skus)} SKU(s): {uploaded_skus}\n")

# 1. signup
email = f"e2e-{uuid.uuid4().hex[:8]}@faro-e2e.io"
pw = "TestPass123!"
sg = step("signup", requests.post(f"{BASE}/auth/signup",
          json={"email": email, "password": pw, "full_name": "E2E", "tenant_name": f"e2e-{uuid.uuid4().hex[:6]}"}))
uid = sg["user"]["id"]; tid = sg["user"].get("tenant_id") or sg["tenant"]["id"]

# mark verified directly (login requires it) via service
url = re.search(r'DATABASE_URL=(\S+)', open('backend/.env').read()).group(1)
from backend.db.connection import init_pool
init_pool(url)
from backend.users import service as user_svc
user_svc.mark_verified(tid, uid)
print("     marked verified")

# 2. login
lg = step("login", requests.post(f"{BASE}/auth/login", json={"email": email, "password": pw}))
H = {"Authorization": f"Bearer {lg['access_token']}"}

# 3. create session + upload + attach
sess = step("create session", requests.post(f"{BASE}/sessions", json={"name": "e2e"}, headers=H))
sid = sess["id"] if "id" in sess else sess["session_id"]
with open(CSV, "rb") as f:
    ds = step("upload dataset", requests.post(f"{BASE}/datasets",
              files={"file": (CSV.split('/')[-1], f, "text/csv")}, headers=H))
did = ds["id"]
step("attach dataset", requests.post(f"{BASE}/sessions/{sid}/dataset", json={"dataset_id": did}, headers=H))

# 4. inspect — KEY diagnostic: did it detect the SKU column as a group candidate?
insp = step("inspect", requests.get(f"{BASE}/sessions/{sid}/inspect", headers=H))
opts = insp["column_options"]
print("     date_candidates :", opts["date_candidates"])
print("     target_candidates:", opts["target_candidates"])
print("     group_candidates :", opts["group_candidates"])
sku_to_use = SKU if SKU in opts["group_candidates"] else None
print(f"     >> sku_column we will send: {sku_to_use!r}  (None => trains as __all__)")

# 5. configure
step("columns", requests.post(f"{BASE}/sessions/{sid}/configure/columns", headers=H, json={
    "date_column": DATE, "target_column": TGT, "sku_column": sku_to_use,
    "gap_fill": "forward",
    "outlier_config": {"strategy": "winsorize_sigma", "n_sigma": 3, "percentile": 0.05, "iqr_k": 1.5,
                       "per_sku_overrides": {}, "per_sku_n_sigma": {}, "per_sku_percentile": {}, "per_sku_iqr_k": {}},
}))
step("features", requests.post(f"{BASE}/sessions/{sid}/configure/features", headers=H,
     json={"lags": [1, 7, 14], "rolling": [7, 14], "diffs": [1], "calendar": True, "ewm_spans": [7]}))
step("models", requests.post(f"{BASE}/sessions/{sid}/configure/models", headers=H,
     json={"mode": "selected", "selected_models": ["lightgbm"], "hyperparameters": {},
           "auto_select_best": True, "selection_metric": "wape"}))
step("validation", requests.post(f"{BASE}/sessions/{sid}/configure/validation", headers=H,
     json={"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3, "min_history": 20, "seasonal_period": 7}))
step("forecast cfg", requests.post(f"{BASE}/sessions/{sid}/config/forecast", headers=H, json={"horizon": 30}))
step("business cfg", requests.post(f"{BASE}/sessions/{sid}/config/business", headers=H,
     json={"service_level": 0.95, "lead_time_days": 15, "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0}))

# 6. train + poll
tr = step("train", requests.post(f"{BASE}/sessions/{sid}/train", headers=H))
job_id = tr["job_id"]
print("     job:", job_id)
status = None
for i in range(160):  # up to ~8 min
    j = unwrap(requests.get(f"{BASE}/jobs/{job_id}", headers=H))
    status = j["status"]; pct = (j.get("progress") or {}).get("percent")
    if status in ("COMPLETED", "FAILED", "CANCELLED"):
        break
    if i % 5 == 0:
        print(f"     ...{status} {pct}%")
    time.sleep(3)
print(f"     FINAL job status: {status}")
if status != "COMPLETED":
    print("     job error:", j.get("error"))
    sys.exit(1)

# 7. compare counts across stages
from backend.db.connection import query
fc = query("SELECT jsonb_object_keys(forecasts) k FROM session_results WHERE session_id=%s", (sid,))
forecast_skus = sorted(r["k"] for r in fc)

inv = unwrap(requests.get(f"{BASE}/inventory/status?session_id={sid}", headers=H))
inv_skus = sorted(i["sku"] for i in inv["items"])
excluded = inv.get("excluded_skus") or []
print("  EXCLUDED_SKUS:", [(e["sku"], e["reason"], e["n_rows"]) for e in excluded])

print("\n" + "=" * 60)
print("RESULT — SKU count across the pipeline")
print("=" * 60)
print(f"  Uploaded   : {len(uploaded_skus):>2}  {uploaded_skus}")
print(f"  Forecast   : {len(forecast_skus):>2}  {forecast_skus}")
print(f"  Inventory  : {len(inv_skus):>2}  {inv_skus}")
print(f"  Signals    : {{ {', '.join(i['sku']+':'+i['signal'] for i in inv['items'])} }}")
match = (len(uploaded_skus) == len(forecast_skus) == len(inv_skus)) and uploaded_skus[0] != "<single-series>"
print(f"\n  >>> {'PASS — counts match' if match else 'MISMATCH / __all__ — BUG REPRODUCED'}")
print(f"  session_id={sid} tenant_id={tid}")

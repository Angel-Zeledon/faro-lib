"""
Exploratory adversarial QA script for Quick Start.

NOT a pytest file — run directly with `python -m tests._qa_explore` from
backend/. Creates ONE tenant/user and reuses it across all scenarios to
avoid paying auth setup cost (bcrypt hashing + DB round trips) per case.

Prints a structured JSON report to stdout (and qa_report.json on disk).
"""
import csv
import io
import json
import sys
from datetime import date, timedelta
from unittest import mock
from uuid import uuid4

_worker_patch = mock.patch("backend.workers.worker.start")
_worker_patch.start()  # left active for the process lifetime: must still be active when
# TestClient(...).__enter__() below runs FastAPI's lifespan, which is what actually calls
# worker.start() — closing the patch before then (e.g. via `with:`) lets a real background
# worker thread spin up and race with this script's own direct run_training_job() calls.

from backend.main import app

from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=True).__enter__()

from backend.tenants.service import create_tenant
from backend.users import service as user_svc

TENANT = create_tenant(f"qa-{uuid4().hex[:8]}")
from backend.db.connection import execute as _exec, _json as _to_json
_exec(
    "UPDATE tenants SET quota = %s WHERE id = %s",
    (_to_json({"max_sessions": 100000, "max_skus_per_session": 1000000,
               "max_concurrent_jobs": 100, "max_dataset_size_mb": 500}), TENANT["id"]),
)
EMAIL = f"qa-{uuid4().hex[:8]}@example.com"
PASSWORD = "TestPass123!"
_user = user_svc.create_user(TENANT["id"], EMAIL, PASSWORD, "admin", "QA Bot")
user_svc.mark_verified(TENANT["id"], _user["id"])

_resp = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
assert _resp.status_code == 200, _resp.text
TOKEN = _resp.json()["data"]["access_token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

RESULTS = []


def csv_rows(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def good_rows(n_skus=2, n_days=40, sku_prefix="SKU", date_col_ok=True,
              stock=True, lead=True):
    rows = []
    start = date(2024, 1, 1)
    for i in range(n_skus):
        sku = f"{sku_prefix}_{i+1:03d}"
        for d in range(n_days):
            dt = (start + timedelta(days=d)).isoformat()
            val = round(10 + 5 * (d % 7) + i * 2, 2)
            row = [dt, sku, val]
            if stock:
                row.append(100 - d)
            if lead:
                row.append(5 + i)
            rows.append(row)
    header = ["date", "sku", "target"]
    if stock:
        header.append("current_stock")
    if lead:
        header.append("lead_time_days")
    return header, rows


def run_case(name, file_bytes, filename="data.csv", date_col=None, target_col=None,
             sku_col="__auto__", run_training=False, models=None, notes=""):
    out = {"name": name, "notes": notes}
    try:
        r = client.post("/api/v1/sessions", json={"name": f"qa-{name}-{uuid4().hex[:6]}"}, headers=HEADERS)
        if r.status_code != 201:
            out["error_stage"] = "create_session"
            out["status"] = r.status_code
            out["body"] = _safe_body(r)
            RESULTS.append(out)
            return out
        sid = r.json()["data"]["id"]
        out["session_id"] = sid

        r = client.post("/api/v1/datasets", files={"file": (filename, file_bytes, "text/csv")}, headers=HEADERS)
        out["upload_status"] = r.status_code
        out["upload_body"] = _safe_body(r)
        if r.status_code != 201:
            RESULTS.append(out)
            return out
        ds_id = r.json()["data"]["id"]

        r = client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": ds_id}, headers=HEADERS)
        out["attach_status"] = r.status_code

        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=HEADERS)
        out["inspect_status"] = r.status_code
        if r.status_code != 200:
            out["inspect_body"] = _safe_body(r)
            RESULTS.append(out)
            return out

        data = r.json()["data"]
        profile = data.get("profile", {})
        out["warnings"] = profile.get("warnings")
        out["data_quality_issues"] = [i.get("type") for i in profile.get("data_quality", {}).get("issues", [])]
        out["recommended"] = profile.get("recommended")
        opts = data.get("column_options", {})
        out["date_candidates"] = opts.get("date_candidates")
        out["target_candidates"] = opts.get("target_candidates")
        out["group_candidates"] = opts.get("group_candidates")
        out["n_rows"] = profile.get("stats", {}).get("n_rows")

        # Resolve columns to submit
        dc = date_col if date_col is not None else (opts.get("date_candidates") or [""])[0]
        tc = target_col if target_col is not None else (opts.get("target_candidates") or [""])[0]
        if sku_col == "__auto__":
            sc = (opts.get("group_candidates") or [None])[0]
        else:
            sc = sku_col

        body = {
            "date_column": dc, "target_column": tc, "sku_column": sc,
            "gap_fill": "forward",
            "outlier_config": {"strategy": "leave"},
        }
        r = client.post(f"/api/v1/sessions/{sid}/configure/columns", json=body, headers=HEADERS)
        out["configure_columns_status"] = r.status_code
        out["configure_columns_body"] = _safe_body(r) if r.status_code != 200 else None
        out["submitted_columns"] = {"date": dc, "target": tc, "sku": sc}

        if run_training and r.status_code == 200:
            client.post(f"/api/v1/sessions/{sid}/configure/features",
                        json={"lags": [1], "rolling": [], "diffs": [], "calendar": False},
                        headers=HEADERS)
            client.post(f"/api/v1/sessions/{sid}/configure/models",
                        json={"mode": "selected", "selected_models": models or ["lightgbm"]},
                        headers=HEADERS)
            client.post(f"/api/v1/sessions/{sid}/configure/validation",
                        json={"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 2,
                              "min_history": 5, "seasonal_period": 7},
                        headers=HEADERS)
            client.post(f"/api/v1/sessions/{sid}/config/forecast",
                        json={"horizon": 5}, headers=HEADERS)
            client.post(f"/api/v1/sessions/{sid}/config/business",
                        json={"service_level": 0.95, "lead_time_days": 7}, headers=HEADERS)

            r = client.post(f"/api/v1/sessions/{sid}/train", headers=HEADERS)
            out["start_training_status"] = r.status_code
            out["start_training_body"] = _safe_body(r)
            if r.status_code == 202:
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

                if job and job.get("status") == "COMPLETED":
                    from backend.inventory.service import get_inventory_status
                    items = get_inventory_status(TENANT["id"], sid)
                    out["inventory_skus"] = [i["sku"] for i in items]
                    out["inventory_signals"] = {i["sku"]: i["signal"] for i in items}

    except Exception as e:
        out["uncaught_exception"] = f"{type(e).__name__}: {e}"
    RESULTS.append(out)
    return out


def _safe_body(r):
    try:
        return r.json()
    except Exception:
        return r.text[:300]


# ═══════════════════════════════════════════════════════════════════════
# A. Column validation
# ═══════════════════════════════════════════════════════════════════════

h, rows = good_rows()
run_case("A1_missing_target_column", csv_rows(["date", "sku"], [[r[0], r[1]] for r in rows]),
         notes="No numeric column at all — nothing to forecast")

run_case("A2_missing_date_column", csv_rows(["sku", "target"], [[r[1], r[2]] for r in rows]),
         notes="No date-like column at all")

run_case("A3_duplicate_column_names",
         (lambda: (lambda buf: (buf.write("date,date,target\n2024-01-01,2024-01-01,10\n2024-01-02,2024-01-02,11\n"), buf.getvalue().encode())[1])(io.StringIO()))(),
         notes="Two columns both named 'date'")

run_case("A4_garbage_header_names", csv_rows(["xx", "yy", "zz"], [[r[0], r[1], r[2]] for r in rows]),
         notes="Header names carry no semantic hint at all")

run_case("A5_blank_header_cell",
         b"date,,target\n2024-01-01,SKU_001,10\n2024-01-02,SKU_001,12\n2024-01-03,SKU_001,9\n",
         notes="One header cell is empty string")

run_case("A6_column_names_with_spaces",
         csv_rows([" date ", " sku ", " target "], [[r[0], r[1], r[2]] for r in rows]),
         notes="Header names with leading/trailing whitespace")

run_case("A7_column_names_uppercase",
         csv_rows(["DATE", "SKU", "TARGET"], [[r[0], r[1], r[2]] for r in rows]),
         notes="Header names in all caps — does detection still match hints?")

run_case("A8_column_names_special_chars",
         csv_rows(["fecha (dd/mm/yyyy)", "código#producto", "cantidad$"], [[r[0], r[1], r[2]] for r in rows]),
         notes="Special characters embedded in header names")

run_case("A9_completely_empty_file", b"", notes="Zero-byte file")

run_case("A10_header_only_no_rows", b"date,sku,target\n", notes="Header row, zero data rows")

run_case("A11_single_column_file", b"target\n10\n12\n9\n14\n", notes="Only one column total")

# ═══════════════════════════════════════════════════════════════════════
# B. Type validation
# ═══════════════════════════════════════════════════════════════════════

run_case("B1_sku_numeric", csv_rows(["date", "sku", "target"],
         [[(date(2024,1,1)+timedelta(days=d)).isoformat(), 1001+i, 10+d] for i in range(2) for d in range(25)]),
         notes="SKU column is purely numeric (1001, 1002)")

run_case("B2_sku_alphanumeric_mixed", csv_rows(["date", "sku", "target"],
         [[(date(2024,1,1)+timedelta(days=d)).isoformat(), f"SKU-{i}-A", 10+d] for i in range(2) for d in range(25)]),
         notes="SKU with mixed alphanumeric + dashes")

run_case("B3_sku_empty_blank",
         csv_rows(["date", "sku", "target"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "" if d % 5 == 0 else "SKU_001", 10+d] for d in range(30)]),
         notes="Some rows have blank SKU value")

run_case("B4_date_invalid_strings",
         b"date,sku,target\nnot-a-date,SKU_001,10\nalso bad,SKU_001,11\n2024-01-03,SKU_001,9\n2024-01-04,SKU_001,14\n",
         notes="Date column contains unparseable strings")

run_case("B5_date_future_extreme",
         csv_rows(["date", "sku", "target"],
                  [["2099-01-01", "SKU_001", 10], ["2099-01-02", "SKU_001", 12], ["2099-01-03", "SKU_001", 9]]),
         notes="All dates far in the future")

run_case("B6_date_mixed_formats",
         b"date,sku,target\n2024-01-01,SKU_001,10\n01/02/2024,SKU_001,11\nJan 3 2024,SKU_001,9\n2024-01-04,SKU_001,14\n2024-01-05,SKU_001,8\n",
         notes="Date column mixes ISO, slash, and textual formats")

run_case("B7_target_textual",
         csv_rows(["date", "sku", "target"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", "alto" if d % 2 else "bajo"] for d in range(25)]),
         notes="Target column contains text labels instead of numbers")

# current_stock / lead_time_days go through the new sync_stock_from_dataset path
run_case("B8_lead_time_negative",
         csv_rows(["date", "sku", "target", "lead_time_days"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, -5] for d in range(25)]),
         notes="lead_time_days is negative")

run_case("B9_lead_time_decimal",
         csv_rows(["date", "sku", "target", "lead_time_days"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, 5.7] for d in range(25)]),
         notes="lead_time_days is a decimal value")

run_case("B10_lead_time_blank",
         csv_rows(["date", "sku", "target", "lead_time_days"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, ""] for d in range(25)]),
         notes="lead_time_days is blank/empty for every row")

run_case("B11_stock_negative",
         csv_rows(["date", "sku", "target", "current_stock"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, -50] for d in range(25)]),
         notes="current_stock is negative")

run_case("B12_stock_blank",
         csv_rows(["date", "sku", "target", "current_stock"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, ""] for d in range(25)]),
         notes="current_stock is blank for every row")

run_case("B13_stock_textual",
         csv_rows(["date", "sku", "target", "current_stock"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, "mucho"] for d in range(25)]),
         notes="current_stock contains text instead of a number")

# ═══════════════════════════════════════════════════════════════════════
# C. Data quality
# ═══════════════════════════════════════════════════════════════════════

_h, _r = good_rows(n_skus=1, n_days=30)
run_case("C1_exact_duplicate_rows", csv_rows(_h, _r + _r), notes="Entire dataset duplicated row-for-row")

run_case("C2_contradictory_duplicate_date_sku",
         csv_rows(["date", "sku", "target"],
                  [["2024-01-01", "SKU_001", 10], ["2024-01-01", "SKU_001", 999],
                   ["2024-01-02", "SKU_001", 11], ["2024-01-03", "SKU_001", 9]]),
         notes="Same date+SKU appears twice with wildly different target values")

run_case("C3_temporal_gaps",
         csv_rows(["date", "sku", "target"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d]
                   for d in range(40) if d % 4 != 0]),
         notes="25% of expected days missing — irregular gaps")

run_case("C4_very_short_series",
         csv_rows(["date", "sku", "target"],
                  [["2024-01-01", "SKU_001", 10], ["2024-01-02", "SKU_001", 12], ["2024-01-03", "SKU_001", 9]]),
         notes="Only 3 rows total")

run_case("C5_single_data_point", csv_rows(["date", "sku", "target"], [["2024-01-01", "SKU_001", 10]]),
         notes="Exactly 1 row")

run_case("C6_constant_series",
         csv_rows(["date", "sku", "target"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 42] for d in range(30)]),
         notes="Target is the same value every single day")

run_case("C7_extreme_outlier",
         csv_rows(["date", "sku", "target"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001",
                    999999999 if d == 15 else 10 + (d % 5)] for d in range(30)]),
         notes="One row has a target 8 orders of magnitude above the rest")

# ═══════════════════════════════════════════════════════════════════════
# D. Scale / boundary cases
# ═══════════════════════════════════════════════════════════════════════

run_case("D1_one_sku", csv_rows(*good_rows(n_skus=1, n_days=40)), notes="Single SKU")
run_case("D2_two_skus", csv_rows(*good_rows(n_skus=2, n_days=40)), notes="Two SKUs")

_h, _r = good_rows(n_skus=1500, n_days=10, stock=False, lead=False)
run_case("D3_thousands_of_skus", csv_rows(_h, _r), notes="1500 SKUs x 10 days = 15000 rows (inspect-only, no training)")

run_case("D4_single_day_multi_sku",
         csv_rows(["date", "sku", "target"], [["2024-06-01", f"SKU_{i:03d}", 10+i] for i in range(20)]),
         notes="All rows share the exact same date")

_start = date(1990, 1, 1)
_decades_rows = [[(_start + timedelta(days=d)).isoformat(), "SKU_001", 10 + (d % 30)] for d in range(0, 365*34, 7)]
run_case("D5_decades_of_data", csv_rows(["date", "sku", "target"], _decades_rows),
         notes="Weekly data spanning 1990-2024 (~34 years)")

run_case("D6_inventory_zero",
         csv_rows(["date", "sku", "target", "current_stock"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, 0] for d in range(25)]),
         notes="current_stock is 0 for every row", run_training=True)

run_case("D7_inventory_extreme_high",
         csv_rows(["date", "sku", "target", "current_stock"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, 1_000_000_000] for d in range(25)]),
         notes="current_stock is 1 billion", run_training=True)

run_case("D8_lead_time_zero",
         csv_rows(["date", "sku", "target", "lead_time_days"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, 0] for d in range(25)]),
         notes="lead_time_days is 0", run_training=True)

run_case("D9_lead_time_huge",
         csv_rows(["date", "sku", "target", "lead_time_days"],
                  [[(date(2024,1,1)+timedelta(days=d)).isoformat(), "SKU_001", 10+d, 3650] for d in range(25)]),
         notes="lead_time_days is 3650 (10 years)", run_training=True)

# ═══════════════════════════════════════════════════════════════════════
# E. Full E2E training with WRONG column name (bypassing frontend defaults)
# ═══════════════════════════════════════════════════════════════════════

run_case("E1_training_with_nonexistent_date_column",
         csv_rows(*good_rows(n_skus=1, n_days=30)),
         date_col="date_que_no_existe", target_col="target",
         run_training=True, notes="Force a date_column name that is NOT in the file")

run_case("E2_training_with_nonexistent_target_column",
         csv_rows(*good_rows(n_skus=1, n_days=30)),
         date_col="date", target_col="columna_inventada",
         run_training=True, notes="Force a target_column name that is NOT in the file")

run_case("E3_training_baseline_good_data",
         csv_rows(*good_rows(n_skus=2, n_days=40)),
         run_training=True, notes="Control case: clean 2-SKU dataset, should succeed end-to-end")

# ═══════════════════════════════════════════════════════════════════════

with open("qa_report.json", "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, indent=2, default=str)

print(f"\n\n=== Ran {len(RESULTS)} scenarios. Report written to qa_report.json ===")
for r in RESULTS:
    print(json.dumps(r, default=str)[:500])

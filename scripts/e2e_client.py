#!/usr/bin/env python3
"""
E2E Integration Client
======================
Simulates the complete frontend → backend flow via real HTTP calls.
Exercises every wizard step: signup → upload → configure → train → results.

Requirements:
    pip install requests psycopg2-binary

    (Both are already installed in backend/.venv — activate it first, or install globally.)

Usage:
    # From the forecasting/ root directory:
    python scripts/e2e_client.py

    # Keep the test tenant in DB after the run (useful for manual inspection):
    python scripts/e2e_client.py --no-cleanup

    # Run only statistical models (faster, no ML deps needed):
    python scripts/e2e_client.py --models prophet arima

    # Custom backend URL:
    python scripts/e2e_client.py --base-url http://localhost:8001/api/v1

Options:
    --base-url    Backend base URL (default: http://localhost:8001/api/v1)
    --db-url      PostgreSQL URL override (defaults to reading Backend/.env)
    --no-cleanup  Keep the test tenant in DB after the run
    --skus        Number of SKUs in synthetic dataset (default: 5)
    --days        Days of history per SKU (default: 90)
    --models      Models to train, space-separated (default: lightgbm)
    --timeout     Max seconds to wait for training (default: 300)

What this script tests (in order):
    0.  Health check — is the backend reachable?
    1.  POST /auth/signup
    2.  Email verification bypass via direct psycopg2 DB update
    3.  POST /auth/login → access token
    4.  POST /datasets (file upload)
    5.  POST /sessions (create)
    6.  POST /sessions/{id}/dataset (attach)
    7.  GET  /sessions/{id}/inspect (dataset profiling)
    8.  POST /sessions/{id}/configure/columns
    9.  POST /sessions/{id}/configure/features
    10. POST /sessions/{id}/configure/models
    11. POST /sessions/{id}/configure/validation
    12. POST /sessions/{id}/config/forecast
    13. POST /sessions/{id}/config/business
    14. GET  /sessions/{id}/config-summary (verify before training)
    15. POST /sessions/{id}/train → job_id
    16. Poll GET /jobs/{job_id} until COMPLETED/FAILED
    17. GET  /jobs/{job_id}/logs (on failure)
    18. GET  /sessions/{id}/results
    19. GET  /sessions/{id}/metrics
    20. GET  /sessions/{id}/inventory
    21. GET  /sessions/{id}/routing
    22. GET  /sessions/{id}/forecast-series/{sku}
    99. DELETE tenant (cleanup)
"""

import argparse
import csv
import io
import json
import math
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(
        "Missing: pip install requests\n"
        "Or activate the backend venv first: .\\backend\\.venv\\Scripts\\activate"
    )

try:
    import psycopg2
except ImportError:
    sys.exit(
        "Missing: pip install psycopg2-binary\n"
        "Or activate the backend venv first: .\\backend\\.venv\\Scripts\\activate"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="E2E integration client for ForecastPlatform backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-url", default="http://localhost:8001/api/v1")
    p.add_argument("--db-url", default=None)
    p.add_argument("--no-cleanup", action="store_true")
    p.add_argument("--skus", type=int, default=5)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--models", nargs="+", default=["lightgbm"])
    p.add_argument("--timeout", type=int, default=300, help="Max seconds to wait for training")
    return p.parse_args()


# ── Pretty output ─────────────────────────────────────────────────────────────

W = 68

def banner(title: str) -> None:
    print(f"\n{'━' * W}")
    print(f"  {title}")
    print(f"{'━' * W}")

def step(n: int | str, title: str) -> None:
    print(f"\n{'─' * W}")
    print(f"  Step {n}: {title}")
    print(f"{'─' * W}")

def ok(label: str, extra: str = "") -> None:
    line = f"  ✓  {label}"
    if extra:
        line += f"   →  {extra}"
    print(line)

def warn(msg: str) -> None:
    print(f"  ⚠  {msg}")

def fail_exit(label: str, resp: "requests.Response | None" = None, msg: str = "") -> None:
    print(f"\n  ✗  FAILED: {label}")
    if resp is not None:
        print(f"     HTTP {resp.status_code}")
        try:
            body = resp.json()
            print(f"     {json.dumps(body, indent=4)[:600]}")
        except Exception:
            print(f"     {resp.text[:600]}")
    if msg:
        print(f"     {msg}")
    sys.exit(1)


# ── Dataset generator ─────────────────────────────────────────────────────────

def generate_csv_bytes(n_skus: int = 5, n_days: int = 90, seed: int = 42) -> tuple[bytes, list[str]]:
    """Multi-SKU time-series with trend + weekly seasonality + noise. Returns (bytes, sku_list)."""
    rng = random.Random(seed)
    start = date(2024, 1, 1)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    skus: list[str] = []
    for i in range(n_skus):
        sku = f"SKU_{i + 1:03d}"
        skus.append(sku)
        base = rng.uniform(30, 150)
        trend = rng.uniform(-0.01, 0.12)
        for d in range(n_days):
            dt = start + timedelta(days=d)
            season = base * 0.15 * math.sin(2 * math.pi * d / 7)
            noise = rng.gauss(0, base * 0.08)
            val = max(0.0, base + trend * d + season + noise)
            w.writerow([dt.isoformat(), sku, round(val, 2)])
    return buf.getvalue().encode("utf-8"), skus


# ── .env reader ───────────────────────────────────────────────────────────────

def read_database_url(db_url_arg: str | None) -> str:
    if db_url_arg:
        return db_url_arg

    # Try both Backend/ and backend/ casing
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "Backend" / ".env", root / "backend" / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "DATABASE_URL":
                    return val.strip().strip('"').strip("'")

    fail_exit(
        "DATABASE_URL not found",
        msg=(
            "Could not read Backend/.env — pass --db-url explicitly:\n"
            "  python scripts/e2e_client.py --db-url 'postgresql://...'"
        ),
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_connect(db_url: str):
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        conn.autocommit = True
        return conn
    except Exception as e:
        fail_exit("Cannot connect to database", msg=str(e))


def verify_email_in_db(db_url: str, email: str) -> None:
    conn = _db_connect(db_url)
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET email_verified = TRUE WHERE email = %s", (email.lower(),))
        if cur.rowcount == 0:
            # User insert might still be in-flight; wait and retry once
            time.sleep(1)
            cur.execute("UPDATE users SET email_verified = TRUE WHERE email = %s", (email.lower(),))
            if cur.rowcount == 0:
                conn.close()
                fail_exit(f"User {email} not found in DB — signup may have failed")
    conn.close()
    ok("Email verified via direct DB update (bypassing SMTP)")


def delete_tenant(db_url: str, tenant_id: str) -> None:
    conn = _db_connect(db_url)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
    conn.close()
    ok(f"Test tenant deleted (CASCADE cleaned all rows)", f"tenant_id={tenant_id}")


# ── HTTP client ───────────────────────────────────────────────────────────────

class Client:
    """Thin wrapper around requests.Session that manages auth headers and error reporting."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self._s = requests.Session()
        self.token: str | None = None

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def post(self, path: str, body: dict | None = None, files: dict | None = None, label: str = "") -> dict:
        url = f"{self.base}{path}"
        if files:
            resp = self._s.post(url, files=files, headers=self._auth())
        else:
            resp = self._s.post(url, json=body, headers={**self._auth(), "Content-Type": "application/json"})
        return self._unwrap(resp, label or f"POST {path}")

    def get(self, path: str, label: str = "") -> dict:
        resp = self._s.get(f"{self.base}{path}", headers=self._auth())
        return self._unwrap(resp, label or f"GET {path}")

    def get_raw(self, path: str) -> requests.Response:
        return self._s.get(f"{self.base}{path}", headers=self._auth())

    @staticmethod
    def _unwrap(resp: requests.Response, label: str) -> dict:
        if resp.status_code not in (200, 201, 202):
            fail_exit(label, resp)
        body = resp.json()
        return body.get("data", body)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    base_url: str = args.base_url
    db_url: str = read_database_url(args.db_url)

    unique = f"{int(time.time() * 1000) % 1_000_000:06d}"
    email = f"e2e_{unique}@test.local"
    password = "E2eTestPass123!"
    tenant_name = f"E2E Corp {unique}"

    # ── Banner ────────────────────────────────────────────────────────────────
    banner(f"ForecastPlatform  E2E Integration Test  [{unique}]")
    print(f"  Backend  : {base_url}")
    print(f"  Email    : {email}")
    print(f"  Dataset  : {args.skus} SKUs × {args.days} days")
    print(f"  Models   : {args.models}")
    print(f"  Timeout  : {args.timeout}s")

    client = Client(base_url)
    server_root = base_url.replace("/api/v1", "")

    # ── Step 0: Health ────────────────────────────────────────────────────────
    step(0, "Health check")
    try:
        resp = requests.get(f"{server_root}/health", timeout=5)
    except requests.exceptions.ConnectionError:
        fail_exit(
            "Cannot connect to backend",
            msg=(
                f"Start it first:\n"
                f"  cd backend && uvicorn backend.main:app --reload --port 8001\n"
                f"  (or: cd Backend && ...)"
            ),
        )
    if resp.status_code != 200:
        fail_exit("Health endpoint returned non-200", resp)
    health = resp.json()
    ok(f"Backend is up", f"version={health.get('version')}  queued_jobs={health.get('queued_jobs')}")

    # ── Step 1: Signup ────────────────────────────────────────────────────────
    step(1, "Signup — create tenant + admin user")
    data = client.post("/auth/signup", {
        "email": email,
        "password": password,
        "tenant_name": tenant_name,
        "full_name": "E2E Tester",
    }, label="POST /auth/signup")
    tenant_id: str = data["tenant"]["id"]
    user_id: str = data["user"]["id"]
    ok(f"Tenant created: {tenant_name}", f"id={tenant_id}")
    ok(f"User created: {email}", f"id={user_id}")

    # ── Step 2: Email verification ────────────────────────────────────────────
    step(2, "Email verification bypass (direct DB update)")
    verify_email_in_db(db_url, email)

    # ── Step 3: Login ─────────────────────────────────────────────────────────
    step(3, "Login")
    data = client.post("/auth/login", {
        "email": email,
        "password": password,
    }, label="POST /auth/login")
    client.token = data["access_token"]
    ok("Access token acquired", f"expires_in={data['expires_in']}s")

    # ── Step 4: Generate + upload dataset ─────────────────────────────────────
    step(4, "Generate synthetic dataset + upload")
    csv_bytes, skus = generate_csv_bytes(n_skus=args.skus, n_days=args.days)

    # Save to disk so the user can also upload it manually via the frontend
    scripts_dir = Path(__file__).parent
    csv_path = scripts_dir / "sample_sales.csv"
    csv_path.write_bytes(csv_bytes)
    ok(f"CSV saved to disk", f"{csv_path}  ({len(csv_bytes):,} bytes)")

    data = client.post(
        "/datasets",
        files={"file": ("sample_sales.csv", csv_bytes, "text/csv")},
        label="POST /datasets",
    )
    dataset_id: str = data["id"]
    ok(f"Dataset uploaded", f"id={dataset_id}  filename={data.get('filename')}")

    # ── Step 5: Create session ────────────────────────────────────────────────
    step(5, "Create forecast session")
    data = client.post("/sessions", {
        "name": f"E2E Session {unique}",
        "description": "Automated end-to-end integration test",
        "tags": ["e2e", "test"],
    }, label="POST /sessions")
    session_id: str = data["id"]
    ok(f"Session created: {data['name']}", f"id={session_id}  status={data['status']}")

    # ── Step 6: Attach dataset ────────────────────────────────────────────────
    step(6, "Attach dataset to session")
    data = client.post(f"/sessions/{session_id}/dataset", {
        "dataset_id": dataset_id,
    }, label="POST /sessions/{id}/dataset")
    ok("Dataset attached", f"status={data.get('status')}")

    # ── Step 7: Inspect dataset ───────────────────────────────────────────────
    step(7, "Inspect dataset (profile + column detection)")
    data = client.get(f"/sessions/{session_id}/inspect", "GET /sessions/{id}/inspect")
    profile = data.get("profile", {})
    col_opts = data.get("column_options", {})
    n_rows = profile.get("n_rows", "?")
    n_cols = len(profile.get("columns", {}))
    ok(f"Profile: {n_rows} rows × {n_cols} columns")
    if col_opts:
        ok(f"Column options: {col_opts}")

    # ── Step 8: Configure columns ─────────────────────────────────────────────
    step(8, "Configure columns (wizard step 2)")
    client.post(f"/sessions/{session_id}/configure/columns", {
        "date_column": "date",
        "target_column": "sales",
        "sku_column": "sku",
        "exogenous": [],
    }, label="POST /sessions/{id}/configure/columns")
    ok("date=date, target=sales, sku=sku")

    # ── Step 9: Configure feature engineering ─────────────────────────────────
    step(9, "Configure feature engineering (wizard step 3)")
    client.post(f"/sessions/{session_id}/configure/features", {
        "lags": [1, 7, 14],
        "rolling": [7, 14],
        "diffs": [1],
        "calendar": True,
        "ewm_spans": [],
    }, label="POST /sessions/{id}/configure/features")
    ok("lags=[1,7,14]  rolling=[7,14]  calendar=True")

    # ── Step 10: Configure models ─────────────────────────────────────────────
    step(10, "Configure models (wizard step 4)")
    client.post(f"/sessions/{session_id}/configure/models", {
        "mode": "selected",
        "selected_models": args.models,
        "hyperparameters": {},
        "auto_select_best": True,
        "selection_metric": "wape",
    }, label="POST /sessions/{id}/configure/models")
    ok(f"Models: {args.models}")

    # ── Step 11: Configure validation strategy ────────────────────────────────
    step(11, "Configure training strategy (wizard step 5)")
    client.post(f"/sessions/{session_id}/configure/validation", {
        "train_ratio": 0.8,
        "walk_forward": True,
        "wfv_splits": 3,
        "min_history": 20,
        "seasonal_period": 7,
        "horizon": 14,
    }, label="POST /sessions/{id}/configure/validation")
    ok("train_ratio=0.8  walk_forward=True  horizon=14")

    # ── Step 12: Configure forecast output ────────────────────────────────────
    step(12, "Configure forecast output (wizard step 6)")
    client.post(f"/sessions/{session_id}/config/forecast", {
        "horizon": 14,
        "quantiles": [0.1, 0.9],
    }, label="POST /sessions/{id}/config/forecast")
    ok("horizon=14  quantiles=[0.1, 0.9]")

    # ── Step 13: Configure business parameters ────────────────────────────────
    step(13, "Configure business parameters (wizard step 7)")
    client.post(f"/sessions/{session_id}/config/business", {
        "service_level": 0.95,
        "lead_time_days": 7,
        "holding_cost_pct": 0.20,
        "stockout_cost_multiplier": 3.0,
    }, label="POST /sessions/{id}/config/business")
    ok("service_level=0.95  lead_time=7d  holding_cost=20%")

    # ── Step 14: Verify config summary ────────────────────────────────────────
    step(14, "Verify config summary before training")
    data = client.get(f"/sessions/{session_id}/config-summary", "GET config-summary")
    has_cols = bool(data.get("columns"))
    has_models = bool(data.get("models"))
    ok(f"columns_cfg present: {has_cols}   models_cfg present: {has_models}")
    if not has_cols or not has_models:
        fail_exit(
            "Config summary missing required fields",
            msg="Training will be rejected — check wizard steps above",
        )

    # ── Step 15: Start training ───────────────────────────────────────────────
    step(15, "Start training job")
    data = client.post(f"/sessions/{session_id}/train", label="POST /sessions/{id}/train")
    job_id: str = data["job_id"]
    ok(f"Job queued", f"job_id={job_id}")

    # ── Step 16: Poll for completion ──────────────────────────────────────────
    step(16, "Polling training job until completion...")
    terminal_states = {"COMPLETED", "FAILED", "CANCELLED"}
    poll_interval = 3
    elapsed = 0
    status = "QUEUED"
    job: dict = {}

    while elapsed < args.timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval

        resp = client.get_raw(f"/jobs/{job_id}")
        if resp.status_code != 200:
            warn(f"Poll HTTP {resp.status_code} — retrying...")
            continue

        job = resp.json().get("data", {})
        status = job.get("status", "UNKNOWN")
        progress = job.get("progress_pct") or 0
        error = job.get("error") or ""
        print(
            f"    [{elapsed:3d}s]  status={status:<14s}  progress={progress:5.1f}%  {error[:40]}",
            end="\r", flush=True,
        )

        if status in terminal_states:
            break
    else:
        print()
        fail_exit(f"Timed out after {args.timeout}s", msg="Increase --timeout or check server logs")

    print()  # clear the \r line

    if status in ("FAILED", "CANCELLED"):
        step("16b", f"Job {status} — fetching logs")
        logs_resp = client.get_raw(f"/jobs/{job_id}/logs")
        if logs_resp.status_code == 200:
            lines = logs_resp.json().get("data", {}).get("lines", [])
            print(f"  Last {min(20, len(lines))} log lines:")
            for line in lines[-20:]:
                print(f"    [{line.get('level','INFO'):5s}] {line.get('message','')}")
        fail_exit(f"Training {status}")

    ok(f"Training COMPLETED", f"elapsed={elapsed}s")

    # ── Step 17: Training results ─────────────────────────────────────────────
    step(17, "Fetch training results")
    data = client.get(f"/sessions/{session_id}/results", "GET /results")
    metrics = data.get("metrics", {})
    rows = metrics.get("rows", [])
    n_models_trained = metrics.get("n_models", "?")
    ok(f"Metrics: {len(rows)} rows  ({metrics.get('n_skus','?')} SKUs × {n_models_trained} models)")
    if rows:
        print("  Top results (MAE / WAPE):")
        for r in rows[:8]:
            print(
                f"    {r.get('sku','?'):12s}  {r.get('model','?'):15s}"
                f"  MAE={r.get('mae', 0):.3f}  WAPE={r.get('wape', 0):.3f}"
            )
        if len(rows) > 8:
            print(f"    … and {len(rows) - 8} more rows")

    # ── Step 18: Metrics ──────────────────────────────────────────────────────
    step(18, "Fetch /metrics endpoint")
    data = client.get(f"/sessions/{session_id}/metrics", "GET /metrics")
    ok(f"Metrics payload keys: {list(data.keys()) if data else '(empty)'}")

    # ── Step 19: Inventory recommendations ───────────────────────────────────
    step(19, "Fetch inventory recommendations")
    data = client.get(f"/sessions/{session_id}/inventory", "GET /inventory")
    inv_rows = data.get("rows", []) if isinstance(data, dict) else []
    ok(f"Inventory: {len(inv_rows)} SKU recommendations")
    if inv_rows:
        print("  Sample (first 3):")
        for r in inv_rows[:3]:
            print(
                f"    {r.get('sku','?'):12s}  "
                f"reorder_point={r.get('reorder_point','?')}  "
                f"safety_stock={r.get('safety_stock','?')}"
            )

    # ── Step 20: Routing plan ─────────────────────────────────────────────────
    step(20, "Fetch model routing plan")
    data = client.get(f"/sessions/{session_id}/routing", "GET /routing")
    routing_rows = data.get("rows", []) if isinstance(data, dict) else []
    ok(f"Routing: {len(routing_rows)} SKUs assigned")
    if routing_rows:
        for r in routing_rows[:3]:
            print(
                f"    {r.get('sku','?'):12s}  model={r.get('assigned_model','?')}"
                f"  reason={r.get('reason','?')}"
            )

    # ── Step 21: Forecast series ──────────────────────────────────────────────
    step(21, f"Fetch forecast series for {skus[0]}")
    data = client.get(
        f"/sessions/{session_id}/forecast-series/{skus[0]}",
        f"GET /forecast-series/{skus[0]}",
    )
    hist = data.get("historical", [])
    fc = data.get("forecast", [])
    model_used = data.get("model", "?")
    available = data.get("available_models", [])
    ok(f"Historical: {len(hist)} pts   Forecast: {len(fc)} pts   Model: {model_used}")
    ok(f"Available models for this SKU: {available}")
    if fc:
        print("  Forecast (first 7 days):")
        for pt in fc[:7]:
            lo = pt.get("lower", "?")
            hi = pt.get("upper", "?")
            val = pt.get("value", 0)
            lo_str = f"{lo:.1f}" if isinstance(lo, float) else str(lo)
            hi_str = f"{hi:.1f}" if isinstance(hi, float) else str(hi)
            print(f"    {pt.get('date')}  {val:7.2f}  [{lo_str}, {hi_str}]")

    # ── Summary ───────────────────────────────────────────────────────────────
    banner("ALL STEPS PASSED ✓")
    print(f"  Session ID  : {session_id}")
    print(f"  Job ID      : {job_id}")
    print(f"  Training    : {elapsed}s")
    print(f"  CSV file    : {csv_path}")
    print()
    print(f"  Swagger UI  : {server_root}/docs")
    print(f"  Session API : GET {base_url}/sessions/{session_id}")
    print(f"  Results     : GET {base_url}/sessions/{session_id}/results")
    print()

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if args.no_cleanup:
        warn("Skipping cleanup (--no-cleanup). Test tenant remains in DB.")
        warn(f"To delete manually: DELETE FROM tenants WHERE id = '{tenant_id}';")
    else:
        step("99", "Cleanup — deleting test tenant")
        delete_tenant(db_url, tenant_id)


if __name__ == "__main__":
    main()

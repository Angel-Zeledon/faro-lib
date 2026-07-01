"""
Batch end-to-end runner: generates several CSVs with different data shapes and
runs the FULL quick-start flow (same config the wizard uses) against each, then
reports a matrix of SKU counts at every stage (uploaded -> forecast -> inventory)
to surface where SKUs get lost.

Submits all trainings first (worker runs 2 concurrently), then polls them all.
"""
import sys, time, re, uuid, csv, io, math, random
from datetime import date, timedelta
import requests

BASE = "http://localhost:8002/api/v1"
RNG = random.Random(42)


# ── synthetic data generators ────────────────────────────────────────────────
def _series(n_days, base, start, sku, name, date_col, sku_col, name_col, tgt_col, zero_prob=0.0):
    rows = []
    for d in range(n_days):
        dt = start + timedelta(days=d)
        season = base * 0.20 * math.sin(2 * math.pi * d / 7)          # weekly
        trend  = base * 0.001 * d
        noise  = RNG.gauss(0, base * 0.08)
        val = max(0.0, base + trend + season + noise)
        if zero_prob and RNG.random() < zero_prob:
            val = 0.0
        row = {date_col: dt.isoformat(), sku_col: sku, tgt_col: int(round(val))}
        if name_col:
            row[name_col] = name
        rows.append(row)
    return rows


def gen(spec):
    """spec: dict describing the dataset. Returns (csv_bytes, fieldnames, truth_skus)."""
    dcol, scol, ncol, tcol = spec["date"], spec["sku"], spec.get("name"), spec["target"]
    start = date(2024, 1, 1)
    rows = []
    for (sku, name, n_days, base, zp) in spec["skus"]:
        rows.extend(_series(n_days, base, start, sku, name, dcol, scol, ncol, tcol, zp))
    # extra constant column (to test that constants don't hijack grouping)
    if spec.get("constant_col"):
        for r in rows:
            r[spec["constant_col"]] = "Centro"
    fields = [dcol, scol] + ([ncol] if ncol else []) + ([spec["constant_col"]] if spec.get("constant_col") else []) + [tcol]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader(); w.writerows(rows)
    # truth = SKUs with ENOUGH history (>= min_history 20) vs all uploaded
    all_skus = sorted({s[0] for s in spec["skus"]})
    enough   = sorted({s[0] for s in spec["skus"] if s[2] >= 20})
    return buf.getvalue().encode("utf-8"), fields, all_skus, enough


# ── dataset battery ───────────────────────────────────────────────────────────
DATASETS = [
    {"label": "A_clean_5sku", "date": "fecha", "sku": "sku", "name": "nombre", "target": "cantidad",
     "skus": [(f"SKU-00{i}", f"Prod {i}", 180, 40 + i * 10, 0.0) for i in range(1, 6)]},
    {"label": "B_single_sku", "date": "fecha", "sku": "sku", "name": "nombre", "target": "cantidad",
     "skus": [("SKU-001", "Aceite", 180, 60, 0.0)]},
    {"label": "C_short_history_mix", "date": "fecha", "sku": "sku", "name": "nombre", "target": "cantidad",
     "skus": [("SKU-001", "A", 180, 50, 0.0), ("SKU-002", "B", 180, 70, 0.0), ("SKU-003", "C", 180, 30, 0.0),
              ("SKU-004", "D-corto", 12, 40, 0.0), ("SKU-005", "E-corto", 8, 25, 0.0)]},
    {"label": "D_many_12sku", "date": "fecha", "sku": "sku", "name": "nombre", "target": "cantidad",
     "skus": [(f"SKU-{i:03d}", f"Prod {i}", 100, 20 + i * 5, 0.0) for i in range(1, 13)]},
    {"label": "E_intermittent", "date": "fecha", "sku": "sku", "name": "nombre", "target": "cantidad",
     "skus": [("SKU-001", "Normal1", 150, 50, 0.0), ("SKU-002", "Normal2", 150, 40, 0.0),
              ("SKU-003", "Lento", 150, 12, 0.55), ("SKU-004", "MuyLento", 150, 8, 0.82)]},
    {"label": "F_alt_schema", "date": "fecha", "sku": "producto", "name": None, "target": "unidades",
     "constant_col": "categoria",
     "skus": [(f"P{i}", None, 150, 35 + i * 8, 0.0) for i in range(1, 5)]},
]


def unwrap(r):
    j = r.json(); return j.get("data", j) if isinstance(j, dict) else j


def setup_and_submit(ds):
    """Signup, upload, configure, start training. Returns context dict."""
    csv_bytes, fields, all_skus, enough_skus = gen(ds)
    email = f"e2e-{uuid.uuid4().hex[:8]}@faro-e2e.io"
    sg = unwrap(requests.post(f"{BASE}/auth/signup", json={
        "email": email, "password": "TestPass123!", "full_name": "E2E", "tenant_name": f"e2e-{uuid.uuid4().hex[:6]}"}))
    uid = sg["user"]["id"]; tid = sg["user"].get("tenant_id") or sg["tenant"]["id"]
    user_svc.mark_verified(tid, uid)
    lg = unwrap(requests.post(f"{BASE}/auth/login", json={"email": email, "password": "TestPass123!"}))
    H = {"Authorization": f"Bearer {lg['access_token']}"}

    sid = unwrap(requests.post(f"{BASE}/sessions", json={"name": ds["label"]}, headers=H))["id"]
    did = unwrap(requests.post(f"{BASE}/datasets", files={"file": (ds["label"] + ".csv", csv_bytes, "text/csv")}, headers=H))["id"]
    requests.post(f"{BASE}/sessions/{sid}/dataset", json={"dataset_id": did}, headers=H)

    opts = unwrap(requests.get(f"{BASE}/sessions/{sid}/inspect", headers=H))["column_options"]
    sku_to_use = ds["sku"] if ds["sku"] in opts["group_candidates"] else None

    requests.post(f"{BASE}/sessions/{sid}/configure/columns", headers=H, json={
        "date_column": ds["date"], "target_column": ds["target"], "sku_column": sku_to_use,
        "gap_fill": "forward",
        "outlier_config": {"strategy": "winsorize_sigma", "n_sigma": 3, "percentile": 0.05, "iqr_k": 1.5,
                           "per_sku_overrides": {}, "per_sku_n_sigma": {}, "per_sku_percentile": {}, "per_sku_iqr_k": {}}})
    requests.post(f"{BASE}/sessions/{sid}/configure/features", headers=H,
                  json={"lags": [1, 7, 14, 28], "rolling": [7, 14, 28], "diffs": [1], "calendar": True, "ewm_spans": [7, 14]})
    requests.post(f"{BASE}/sessions/{sid}/configure/models", headers=H,
                  json={"mode": "selected", "selected_models": ["lightgbm", "prophet", "croston", "xgboost"],
                        "hyperparameters": {}, "auto_select_best": True, "selection_metric": "wape"})
    requests.post(f"{BASE}/sessions/{sid}/configure/validation", headers=H,
                  json={"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3, "min_history": 20, "seasonal_period": 7})
    requests.post(f"{BASE}/sessions/{sid}/config/forecast", headers=H, json={"horizon": 30})
    requests.post(f"{BASE}/sessions/{sid}/config/business", headers=H,
                  json={"service_level": 0.95, "lead_time_days": 15, "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0})
    job = unwrap(requests.post(f"{BASE}/sessions/{sid}/train", headers=H))
    return {"label": ds["label"], "tid": tid, "sid": sid, "H": H, "job_id": job["job_id"],
            "all_skus": all_skus, "enough_skus": enough_skus, "group_candidates": opts["group_candidates"],
            "sku_sent": sku_to_use, "status": None, "error": None}


def main():
    print("Submitting all trainings...\n")
    ctxs = []
    for ds in DATASETS:
        try:
            c = setup_and_submit(ds)
            print(f"  submitted {c['label']:22} job={c['job_id']} group_cands={c['group_candidates']} sku_sent={c['sku_sent']!r}")
            ctxs.append(c)
        except Exception as e:
            print(f"  FAILED submit {ds['label']}: {e}")

    print("\nPolling jobs (worker runs 2 concurrent)...")
    pending = {c["job_id"]: c for c in ctxs}
    deadline = time.time() + 60 * 25
    while pending and time.time() < deadline:
        for jid, c in list(pending.items()):
            j = unwrap(requests.get(f"{BASE}/jobs/{jid}", headers=c["H"]))
            st = j["status"]
            if st in ("COMPLETED", "FAILED", "CANCELLED"):
                c["status"] = st; c["error"] = j.get("error")
                print(f"  {c['label']:22} -> {st}" + (f"  ERR: {str(j.get('error'))[:80]}" if st != "COMPLETED" else ""))
                pending.pop(jid)
        if pending:
            time.sleep(5)

    from backend.db.connection import query
    print("\n" + "=" * 92)
    print(f"{'DATASET':22} {'uploaded':>8} {'expect>=20d':>11} {'forecast':>8} {'inventory':>9}  verdict")
    print("=" * 92)
    for c in ctxs:
        if c["status"] != "COMPLETED":
            print(f"{c['label']:22} {'-':>8} {'-':>11} {'-':>8} {'-':>9}  TRAIN {c['status']}")
            continue
        fc = query("SELECT jsonb_object_keys(forecasts) k FROM session_results WHERE session_id=%s", (c["sid"],))
        fcs = sorted(r["k"] for r in fc)
        inv = unwrap(requests.get(f"{BASE}/inventory/status?session_id={c['sid']}", headers=c["H"]))
        invs = sorted(i["sku"] for i in inv["items"])
        all_n, enough_n = len(c["all_skus"]), len(c["enough_skus"])
        has_all = "__all__" in fcs
        ok = (not has_all) and len(fcs) == enough_n and len(invs) == enough_n
        verdict = "OK" if ok else ("__all__!" if has_all else "MISMATCH")
        print(f"{c['label']:22} {all_n:>8} {enough_n:>11} {len(fcs):>8} {len(invs):>9}  {verdict}")
        if not ok:
            lost = sorted(set(c['enough_skus']) - set(fcs))
            print(f"{'':22}   forecast SKUs: {fcs}")
            if lost:
                print(f"{'':22}   LOST (had >=20d but missing): {lost}")

    # cleanup
    print("\nCleanup test tenants...")
    from backend.db.connection import execute
    for c in ctxs:
        try: execute("DELETE FROM tenants WHERE id=%s", (c["tid"],))
        except Exception: pass
    print("done.")


if __name__ == "__main__":
    url = re.search(r'DATABASE_URL=(\S+)', open('backend/.env').read()).group(1)
    from backend.db.connection import init_pool
    init_pool(url)
    from backend.users import service as user_svc
    globals()["user_svc"] = user_svc
    main()

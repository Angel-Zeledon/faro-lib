"""
Pure-Python synthetic dataset generators for testing.
No pandas/numpy dependency — generates CSV bytes directly.
"""

import csv
import io
import math
import random
from datetime import date, timedelta


def generate_csv_bytes(
    n_skus: int = 10,
    n_days: int = 90,
    start_date: str = "2023-01-01",
    seed: int = 42,
    date_col: str = "date",
    sku_col: str = "sku",
    target_col: str = "sales",
) -> bytes:
    """
    Multi-SKU time series with trend + weekly seasonality + noise.
    Returns UTF-8 encoded CSV bytes ready for multipart upload.
    """
    rng = random.Random(seed)
    start = date.fromisoformat(start_date)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([date_col, sku_col, target_col])

    for sku_idx in range(n_skus):
        sku = f"SKU_{sku_idx + 1:03d}"
        base_level = rng.uniform(20, 150)
        trend = rng.uniform(-0.02, 0.15)
        for day_idx in range(n_days):
            dt = start + timedelta(days=day_idx)
            seasonality = base_level * 0.15 * math.sin(2 * math.pi * day_idx / 7)
            noise = rng.gauss(0, base_level * 0.08)
            value = max(0.0, base_level + trend * day_idx + seasonality + noise)
            writer.writerow([dt.isoformat(), sku, round(value, 2)])

    return buf.getvalue().encode("utf-8")


def generate_minimal_csv_bytes() -> bytes:
    """2 SKUs × 30 days — minimal valid dataset for fast tests."""
    return generate_csv_bytes(n_skus=2, n_days=30, seed=99)


def generate_large_csv_bytes(n_skus: int = 50, n_days: int = 365) -> bytes:
    """Larger dataset for stress / performance tests."""
    return generate_csv_bytes(n_skus=n_skus, n_days=n_days, seed=7)


def generate_sparse_csv_bytes() -> bytes:
    """30% zeros — tests zero-demand handling."""
    rng = random.Random(11)
    start = date(2023, 1, 1)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "sku", "sales"])
    for sku_idx in range(3):
        sku = f"SPARSE_{sku_idx + 1}"
        for day_idx in range(50):
            dt = start + timedelta(days=day_idx)
            value = 0.0 if rng.random() < 0.3 else round(rng.uniform(1, 80), 2)
            writer.writerow([dt.isoformat(), sku, value])
    return buf.getvalue().encode("utf-8")


def generate_single_sku_csv_bytes() -> bytes:
    """Single SKU — edge case for group-less sessions."""
    return generate_csv_bytes(n_skus=1, n_days=60, seed=55, sku_col="product")


def generate_wrong_schema_csv_bytes() -> bytes:
    """CSV with columns that don't match any expected schema."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["product_id", "quantity", "timestamp", "region"])
    writer.writerow(["P001", 10, "2023-01-01", "NORTH"])
    writer.writerow(["P001", 15, "2023-01-02", "NORTH"])
    return buf.getvalue().encode("utf-8")


def generate_malformed_csv_bytes() -> bytes:
    """Inconsistent row lengths — malformed CSV."""
    return b"date,sku,sales\n2023-01-01,SKU_001,10,extra_col\n2023-01-02\n"


def seed_completed_session(tenant_id: str, session_id: str, n_skus: int = 3) -> dict:
    """
    Directly seeds the DB with a synthetic training result and forecast series.
    Bypasses the ML pipeline — use to test results endpoints without running training.
    Also force-transitions the session to COMPLETED.
    """
    from datetime import datetime as _dt
    from backend.db import session_store
    from backend.sessions.service import force_status

    rng = random.Random(42)
    skus = [f"SKU_{i + 1:03d}" for i in range(n_skus)]
    models = ["lightgbm", "prophet"]

    # ── metrics ────────────────────────────────────────────────────────────
    metrics_rows = [
        {
            "sku": sku,
            "model": model,
            "type": "ml",
            "mae": round(rng.uniform(1, 15), 3),
            "rmse": round(rng.uniform(2, 20), 3),
            "wape": round(rng.uniform(0.05, 0.25), 3),
        }
        for sku in skus
        for model in models
    ]

    result_payload = {
        "job_id": "job_test_seed",
        "run_id": "run_test_seed",
        "completed_at": _dt.utcnow().isoformat(),
        "metrics": {"rows": metrics_rows, "n_models": len(models), "n_skus": n_skus},
        "inventory": {
            "rows": [
                {"sku": sku, "reorder_point": round(rng.uniform(50, 200), 1),
                 "safety_stock": round(rng.uniform(10, 50), 1)}
                for sku in skus
            ]
        },
        "routing": {
            "rows": [
                {"sku": sku, "assigned_model": models[0], "reason": "best_wape"}
                for sku in skus
            ]
        },
        "data_quality": {
            "n_series": n_skus,
            "issues": [],
            "rows": [{"sku": sku, "completeness": 1.0, "n_rows": 90} for sku in skus],
        },
        "config": {},
    }
    session_store.set_training_result(tenant_id, session_id, result_payload)

    # ── forecasts ──────────────────────────────────────────────────────────
    hist_start = date(2023, 1, 1)
    fc_start = date(2023, 4, 1)
    forecasts: dict = {}
    for sku in skus:
        forecasts[sku] = {}
        for model in models:
            historical = [
                {"date": (hist_start + timedelta(days=i)).isoformat(),
                 "value": round(rng.uniform(10, 100), 2)}
                for i in range(90)
            ]
            forecast_pts = [
                {
                    "date": (fc_start + timedelta(days=i)).isoformat(),
                    "value": round(rng.uniform(10, 100), 2),
                    "lower": round(rng.uniform(5, 90), 2),
                    "upper": round(rng.uniform(20, 120), 2),
                }
                for i in range(14)
            ]
            forecasts[sku][model] = {"historical": historical, "forecast": forecast_pts}
    session_store.set_forecasts(tenant_id, session_id, forecasts)

    force_status(tenant_id, session_id, "COMPLETED", "results")
    return result_payload

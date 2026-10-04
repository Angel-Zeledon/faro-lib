"""Forecast vs. what actually sold.

Orchestration only: it finds the sales uploaded after a session's forecast was
made, hands both series to ForecastingCore (`evaluation.realized`) for the
metrics and the verdict, and returns the result. Nothing new is persisted: the
forecast already is (`session_results.forecasts`, one row per session, never
overwritten by a later training), and the actuals are the tenant's own later
dataset files.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.dataframes.actuals import load_actual_series
from backend.datasets.service import get_dataset, list_datasets
from backend.db import session_store
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

# How many of the newest candidate uploads are opened when no dataset is named.
MAX_CANDIDATES_TRIED = 5
# Per-SKU rows returned (worst first); the aggregate always covers every SKU.
MAX_SKUS_RETURNED = 300


def _champion_forecasts(tenant_id: str, session_id: str) -> dict[str, dict[str, float]]:
    """{sku: {iso_date: forecast}} from the model each SKU is bought from."""
    from backend.inventory.service import best_model_by_sku

    forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    result = session_store.get_training_result(tenant_id, session_id) or {}
    rows = (result.get("metrics") or {}).get("rows") or []
    champion = best_model_by_sku(rows) if rows else {}

    out: dict[str, dict[str, float]] = {}
    for sku, by_model in forecasts.items():
        if not isinstance(by_model, dict) or not by_model:
            continue
        model = champion.get(sku)
        raw = by_model[model] if model in by_model else next(iter(by_model.values()))
        points = raw.get("forecast", []) if isinstance(raw, dict) else raw
        series = {}
        for p in points or []:
            d, v = str(p.get("date", ""))[:10], p.get("value")
            if d and v is not None:
                series[d] = float(v)
        if series:
            out[str(sku)] = series
    return out


def _candidate_datasets(tenant_id: str, session: dict) -> list[dict]:
    """Datasets the tenant uploaded AFTER this session was created, newest first."""
    created = session.get("created_at")
    items = list_datasets(tenant_id, 0, 100)
    return [
        d for d in items
        if d["id"] != session.get("dataset_id")
        and (created is None or d["uploaded_at"] > created)
    ]


def forecast_vs_actual(
    tenant_id: str, session: dict, dataset_id: Optional[str] = None,
) -> dict:
    from forecasting_core.evaluation.realized import compare_forecast_to_actuals

    session_id = session["id"]
    forecasts = _champion_forecasts(tenant_id, session_id)
    cfg = build_engine_config(tenant_id, session_id)
    cols = cfg["columns"]
    target_freq = (cfg.get("granularity") or {}).get("target_freq")
    forecast_dates = {d for s in forecasts.values() for d in s}

    candidates = _candidate_datasets(tenant_id, session)
    base = {
        "session_id": session_id,
        "target_freq": target_freq,
        "forecast_from": min(forecast_dates) if forecast_dates else None,
        "forecast_to": max(forecast_dates) if forecast_dates else None,
        "candidates": [
            {"dataset_id": d["id"], "name": d["name"], "uploaded_at": d["uploaded_at"].isoformat()}
            for d in candidates
        ],
    }

    if dataset_id:
        chosen = get_dataset(tenant_id, dataset_id)   # tenant-scoped: a foreign id resolves to None
        to_try = [chosen] if chosen else []
    else:
        to_try = candidates[:MAX_CANDIDATES_TRIED]
    if not to_try or not forecasts:
        return {**base, "status": "no_later_upload", "source": None, "result": None}

    best = None
    last_reason = "no_overlap"
    for ds in to_try:
        try:
            loaded = load_actual_series(
                ds["file_path"], cols["date"], cols["target"],
                list(cols["group_keys"]), target_freq)
        except Exception as e:  # an unreadable file is a reason, not a 500
            log.warning("forecast_vs_actual: could not read dataset %s: %s", ds["id"], e)
            last_reason = "unreadable"
            continue
        if "error" in loaded:
            last_reason = loaded["error"]
            continue
        overlap = sum(
            1 for sku, fc in forecasts.items()
            for d in fc if d in (loaded["series"].get(sku) or {})
        )
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, ds, loaded)

    if best is None:
        return {**base, "status": last_reason, "source": None, "result": None}

    _, ds, loaded = best
    compared = compare_forecast_to_actuals(forecasts, loaded["series"])
    total_skus = len(compared["skus"])
    compared["skus"] = compared["skus"][:MAX_SKUS_RETURNED]
    return {
        **base,
        "status": "ok",
        "source": {
            "dataset_id": ds["id"], "name": ds["name"],
            "uploaded_at": ds["uploaded_at"].isoformat(),
            "last_date": loaded["last_date"],
        },
        "n_skus_total": total_skus,
        "result": compared,
    }

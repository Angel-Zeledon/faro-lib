"""Forecast vs. what actually sold.

Orchestration only: it lines a session's stored forecast up against ANY dataset
the tenant owns (a later upload, an older file, the full history), hands both
series to ForecastingCore (`evaluation.realized`) for the metrics, the verdict
and the date-overlap reading, and returns the result. Nothing new is persisted:
the forecast already is (`session_results.forecasts`, one row per session, never
overwritten by a later training), and the actuals are the tenant's own dataset
files.

Real future data usually does not exist yet, so the picker is deliberately wide:
the user chooses the session and the dataset, sees which date ranges overlap,
and when they do not, gets the reason (`status` + `overlap`) rather than a
blank screen. A pure back-test (`backend/forecast_check/backtest.py`) makes the
overlap exist by training on a copy of the data with its last periods held out.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from backend.dataframes.actuals import dataset_date_range, load_actual_series
from backend.datasets.service import get_dataset, list_datasets
from backend.db import session_store
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

# How many of the best-overlapping datasets are fully opened when none is named.
MAX_CANDIDATES_TRIED = 5
# Datasets listed in the picker (every dataset of the tenant, newest first).
MAX_CANDIDATES_LISTED = 500
# Seconds spent reading date ranges of not-yet-cached files per request; files
# past the budget are listed without a range and probed on a later request.
RANGE_BUDGET_SECONDS = 6.0
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


def _all_datasets(tenant_id: str) -> list[dict]:
    """Every dataset the tenant has, newest first (paged so none is missed)."""
    out: list[dict] = []
    while len(out) < MAX_CANDIDATES_LISTED:
        page = list_datasets(tenant_id, len(out), 100)
        out += page
        if len(page) < 100:
            break
    return out


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _candidates(
    tenant_id: str, session: dict, date_col: str,
    forecast_from: Optional[str], forecast_to: Optional[str],
) -> list[dict]:
    """Every dataset as a comparison source: what it is, how it relates to the
    session, which dates it covers and how those dates sit against the forecast
    window."""
    from forecasting_core.evaluation.realized import describe_overlap

    created = session.get("created_at")
    deadline = time.monotonic() + RANGE_BUDGET_SECONDS
    out = []
    for d in _all_datasets(tenant_id):
        entry = {
            "dataset_id": d["id"], "name": d["name"],
            "filename": d.get("original_filename"),
            "uploaded_at": _iso(d["uploaded_at"]),
            "is_training_dataset": d["id"] == session.get("dataset_id"),
            "uploaded_after_session": bool(created is not None and d["uploaded_at"] > created),
            "first_date": None, "last_date": None, "range_error": None,
            "overlap": None,
        }
        path = d.get("file_path")
        if path and time.monotonic() < deadline:
            rng = dataset_date_range(path, date_col)
            if "error" in rng:
                entry["range_error"] = rng["error"]
            else:
                entry["first_date"], entry["last_date"] = rng["first_date"], rng["last_date"]
                entry["overlap"] = describe_overlap(
                    forecast_from, forecast_to, rng["first_date"], rng["last_date"])
        else:
            entry["range_error"] = "not_probed"
        out.append(entry)
    return out


def _rank_for_auto_pick(candidates: list[dict], preferred: Optional[str]) -> list[dict]:
    """Datasets whose dates intersect the forecast window, best guess first: the
    one a back-test names, then those that cover the whole window, then the
    most recently uploaded."""
    hits = [c for c in candidates
            if c["overlap"] and c["overlap"]["relation"] in ("covers", "partial")]
    hits.sort(key=lambda c: c["uploaded_at"] or "", reverse=True)
    hits.sort(key=lambda c: (c["dataset_id"] != preferred,
                             c["overlap"]["relation"] != "covers"))
    return hits


def forecast_vs_actual(
    tenant_id: str, session: dict, dataset_id: Optional[str] = None,
) -> dict:
    """Grade a session's stored forecast against one dataset of the tenant.

    ``status``:
      ok               compared; ``result`` carries the metrics
      no_later_upload  nothing was named and the tenant has no file besides
                       the one the session trained on
      no_overlap       no dataset (the named one, or none of the tenant's when
                       nothing was named) has dates inside the forecast window;
                       ``overlap`` / ``candidates`` say by how much it misses
      no_matching_series  the dates line up but no (SKU, period) pair matches
      columns_missing / no_rows / unreadable   the named file cannot be read
      dataset_not_found                        the id is not this tenant's
      no_forecast      the session has no stored forecast to compare
    """
    from forecasting_core.evaluation.realized import compare_forecast_to_actuals, describe_overlap

    session_id = session["id"]
    forecasts = _champion_forecasts(tenant_id, session_id)
    cfg = build_engine_config(tenant_id, session_id)
    cols = cfg["columns"]
    target_freq = (cfg.get("granularity") or {}).get("target_freq")
    forecast_dates = {d for s in forecasts.values() for d in s}
    f_from = min(forecast_dates) if forecast_dates else None
    f_to = max(forecast_dates) if forecast_dates else None

    candidates = _candidates(tenant_id, session, cols["date"], f_from, f_to)
    base = {
        "session_id": session_id,
        "target_freq": target_freq,
        "forecast_from": f_from,
        "forecast_to": f_to,
        "forecast_periods": len(forecast_dates),
        "candidates": candidates,
        "is_backtest": bool(session.get("is_backtest")),
        "backtest_source_dataset_id": session.get("backtest_source_dataset_id"),
        "backtest_holdout_periods": session.get("backtest_holdout_periods"),
    }
    if not forecasts:
        return {**base, "status": "no_forecast", "source": None, "overlap": None, "result": None}

    by_id = {c["dataset_id"]: c for c in candidates}
    if dataset_id:
        chosen = get_dataset(tenant_id, dataset_id)   # tenant-scoped: a foreign id resolves to None
        if not chosen:
            return {**base, "status": "dataset_not_found", "source": None, "overlap": None, "result": None}
        to_try = [chosen]
    else:
        ranked = _rank_for_auto_pick(candidates, session.get("backtest_source_dataset_id"))
        to_try = [d for d in (get_dataset(tenant_id, c["dataset_id"])
                              for c in ranked[:MAX_CANDIDATES_TRIED]) if d]
        if not to_try:
            # Nothing to line up. With no file besides the one the session was
            # trained on there is simply nothing to compare against yet; with
            # other files around, none of their dates reach the forecast window.
            others = [c for c in candidates if not c["is_training_dataset"]]
            return {**base, "status": "no_overlap" if others else "no_later_upload",
                    "source": None, "overlap": None, "result": None}

    best = None
    last_reason = "no_overlap"
    last_ds = None
    for ds in to_try:
        last_ds = ds
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
        elif not overlap:
            by_id.setdefault(ds["id"], {}).update(
                first_date=loaded["first_date"], last_date=loaded["last_date"])

    def _source(ds: dict, loaded: Optional[dict]) -> dict:
        meta = by_id.get(ds["id"], {})
        return {
            "dataset_id": ds["id"], "name": ds["name"],
            "uploaded_at": _iso(ds["uploaded_at"]),
            "first_date": (loaded or {}).get("first_date") or meta.get("first_date"),
            "last_date": (loaded or {}).get("last_date") or meta.get("last_date"),
            "is_training_dataset": ds["id"] == session.get("dataset_id"),
        }

    if best is None:
        src = _source(last_ds, None) if last_ds else None
        ov = (describe_overlap(f_from, f_to, src["first_date"], src["last_date"])
              if src else None)
        if last_reason == "no_overlap" and ov and ov["relation"] in ("covers", "partial"):
            # The dates line up but not one (SKU, period) pair does: the file is
            # about other products, or they are spelled differently.
            last_reason = "no_matching_series"
        return {**base, "status": last_reason, "source": src, "overlap": ov, "result": None}

    _, ds, loaded = best
    compared = compare_forecast_to_actuals(forecasts, loaded["series"])
    total_skus = len(compared["skus"])
    compared["skus"] = compared["skus"][:MAX_SKUS_RETURNED]
    src = _source(ds, loaded)
    compared_dates = {p["date"] for p in compared["aggregate"]["series"]}
    return {
        **base,
        "status": "ok",
        "source": src,
        "overlap": {
            **describe_overlap(f_from, f_to, src["first_date"], src["last_date"]),
            "compared_periods": len(compared_dates),
            "forecast_periods": len(forecast_dates),
        },
        "n_skus_total": total_skus,
        "result": compared,
    }

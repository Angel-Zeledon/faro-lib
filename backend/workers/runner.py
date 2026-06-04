"""
Runner — bridges the backend job system to the forecasting_core ML library.

This is the ONLY place the backend imports from forecasting_core.
Everything else in the backend is pure orchestration.
"""

import logging
from datetime import datetime

from backend.db import session_store
from backend.datasets.service import get_dataset
from backend.sessions.service import get_session
from backend.training.job_service import mark_completed, mark_failed, update_progress
from backend.training.progress_broadcaster import broadcaster
from backend.sessions.service import force_status
from backend.api.v1.webhooks import fire_webhooks

log = logging.getLogger(__name__)


# ── Config assembly ────────────────────────────────────────────────────────

def build_engine_config(tenant_id: str, session_id: str) -> dict:
    """
    Assembles a SessionConfig-compatible dict from the session's DB config blobs.
    Maps backend schema → forecasting_core schema.
    """
    s = get_session(tenant_id, session_id)
    columns_cfg = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    features_cfg = session_store.get_field(tenant_id, session_id, "features_cfg") or {}
    models_cfg = session_store.get_field(tenant_id, session_id, "models_cfg") or {}
    validation_cfg = session_store.get_field(tenant_id, session_id, "validation_cfg") or {}
    business_cfg = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    forecast_cfg = session_store.get_field(tenant_id, session_id, "forecast_cfg") or {}

    # Resolve dataset file path from DB
    dataset_path = ""
    dataset_id = s.get("dataset_id") if s else None
    if dataset_id:
        ds_meta = get_dataset(tenant_id, dataset_id)
        if ds_meta:
            dataset_path = ds_meta.get("file_path", "")

    # Build models dict: {model_name: {hyperparams}}
    selected = models_cfg.get("selected_models", [])
    hyperparams = models_cfg.get("hyperparameters", {})
    if models_cfg.get("mode") == "all":
        from forecasting_core.models.factory import ModelFactory
        selected = ModelFactory.available_models()
    models_dict = {m: hyperparams.get(m, {}) for m in selected}

    return {
        "name": f"session_{session_id[:8]}",
        "data": {
            "path": dataset_path,
            "date_freq": None,
        },
        "columns": {
            "target": columns_cfg.get("target_column", ""),
            "date": columns_cfg.get("date_column", ""),
            "group": columns_cfg.get("sku_column"),
            "exogenous": columns_cfg.get("exogenous", []),
        },
        "_gap_fill": columns_cfg.get("gap_fill", "leave"),
        "_outlier_config": columns_cfg.get("outlier_config", {}),
        "features": {
            "lags": features_cfg.get("lags", [1, 7, 14]),
            "rolling": features_cfg.get("rolling", [7, 14, 28]),
            "diffs": features_cfg.get("diffs", [1]),
            "calendar": features_cfg.get("calendar", True),
            "ewm_spans": features_cfg.get("ewm_spans", []),
            "fourier_periods": features_cfg.get("fourier_periods", []),
            "fourier_K": features_cfg.get("fourier_K", 2),
        },
        "models": models_dict,
        "training": {
            "train_ratio": validation_cfg.get("train_ratio", 0.8),
            "walk_forward": validation_cfg.get("walk_forward", True),
            "wfv_splits": validation_cfg.get("wfv_splits", 3),
            "min_history": validation_cfg.get("min_history", 20),
            "seasonal_period": validation_cfg.get("seasonal_period", 7),
        },
        "forecast": {
            "horizon": forecast_cfg.get("horizon", validation_cfg.get("horizon", 14)),
        },
        "business": {
            "service_level": business_cfg.get("service_level", 0.95),
            "lead_time_days": business_cfg.get("lead_time_days", 7),
            "holding_cost_pct": business_cfg.get("holding_cost_pct", 0.20),
            "stockout_cost_multiplier": business_cfg.get("stockout_cost_multiplier", 3.0),
        },
    }


# ── Gap fill helper ───────────────────────────────────────────────────────

def _apply_gap_fill(df: "pd.DataFrame", date_col: str, target_col: str,
                    group_col: str | None, strategy: str) -> "pd.DataFrame":
    """
    Reindex a time series to fill missing dates using the chosen strategy.
    strategy: "zero" | "mean" | "forward" | "interpolate" | "leave"
    """
    import pandas as pd

    if strategy == "leave" or not strategy:
        return df

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # Detect native frequency (median gap in days)
    try:
        ref = df if not group_col else df.groupby(group_col).first().reset_index()
        sample_dates = df[date_col].dropna().sort_values().drop_duplicates()
        freq_days = int(sample_dates.diff().dropna().dt.days.median())
        if freq_days < 1:
            freq_days = 1
    except Exception:
        return df

    freq_str = f"{freq_days}D"
    groups = [None] if not group_col else df[group_col].unique().tolist()
    parts = []

    for g in groups:
        sub = df if g is None else df[df[group_col] == g].copy()
        sub = sub.drop_duplicates(subset=[date_col]).sort_values(date_col)
        if len(sub) < 2:
            parts.append(sub)
            continue

        full_idx = pd.date_range(sub[date_col].min(), sub[date_col].max(), freq=freq_str)
        sub = sub.set_index(date_col).reindex(full_idx)
        sub.index.name = date_col

        if group_col:
            sub[group_col] = g

        if strategy == "zero":
            sub[target_col] = sub[target_col].fillna(0)
        elif strategy == "mean":
            sub[target_col] = sub[target_col].fillna(sub[target_col].mean())
        elif strategy == "forward":
            sub[target_col] = sub[target_col].ffill().bfill()
        elif strategy == "interpolate":
            sub[target_col] = sub[target_col].interpolate(method="linear").bfill().ffill()

        parts.append(sub.reset_index())

    if not parts:
        return df

    result = pd.concat(parts, ignore_index=True)
    # Restore other columns (non-numeric cols in original)
    other_cols = [c for c in df.columns if c not in (date_col, target_col, group_col or "")]
    if other_cols and group_col:
        fill_map = df.groupby(group_col)[other_cols].first()
        result = result.merge(fill_map, on=group_col, how="left", suffixes=("", "_orig"))
    elif other_cols and not group_col:
        for c in other_cols:
            if c not in result.columns:
                result[c] = df[c].iloc[0]

    return result[[c for c in df.columns if c in result.columns]]


# ── Outlier treatment ─────────────────────────────────────────────────────

def _apply_outlier_treatment(
    df: "pd.DataFrame",
    date_col: str,
    target_col: str,
    group_col: str | None,
    outlier_cfg: dict,
) -> "pd.DataFrame":
    """
    Apply outlier treatment per SKU before training.

    Strategies:
      leave           — no change
      winsorize_sigma — clip to mean ± n_sigma × std
      winsorize_pct   — clip to [percentile%, (100-percentile)%]
      iqr_fence       — clip to Q1 - iqr_k×IQR .. Q3 + iqr_k×IQR
      remove          — replace outliers with NaN then ffill/bfill
      log1p           — log1p transform of the entire series (stabilises variance)

    Per-SKU overrides in outlier_cfg["per_sku_overrides"] take precedence.
    """
    import pandas as pd
    import numpy as np

    global_strategy = outlier_cfg.get("strategy", "leave") if isinstance(outlier_cfg, dict) else "leave"
    per_sku_overrides = outlier_cfg.get("per_sku_overrides", {}) if isinstance(outlier_cfg, dict) else {}
    if global_strategy == "leave" and not per_sku_overrides:
        return df

    df = df.copy()
    groups = [None] if not group_col else df[group_col].unique().tolist()

    def _get(key, default, sku_key=None):
        per_sku_map = outlier_cfg.get(f"per_sku_{key}", {}) if isinstance(outlier_cfg, dict) else {}
        if sku_key and str(sku_key) in per_sku_map:
            return per_sku_map[str(sku_key)]
        return outlier_cfg.get(key, default) if isinstance(outlier_cfg, dict) else default

    for g in groups:
        mask = pd.Series(True, index=df.index) if g is None else (df[group_col] == g)
        sku_key = g

        overrides = outlier_cfg.get("per_sku_overrides", {}) if isinstance(outlier_cfg, dict) else {}
        strategy = overrides.get(str(sku_key), global_strategy) if sku_key is not None else global_strategy

        if strategy == "leave":
            continue

        vals = pd.to_numeric(df.loc[mask, target_col], errors="coerce")
        clean = vals.dropna()
        if len(clean) < 4:
            continue

        try:
            if strategy == "winsorize_sigma":
                n_sigma = _get("n_sigma", 3.0, sku_key)
                mu, sigma = clean.mean(), clean.std()
                if sigma > 0:
                    df.loc[mask, target_col] = vals.clip(mu - n_sigma * sigma, mu + n_sigma * sigma)

            elif strategy == "winsorize_pct":
                pct = _get("percentile", 1.0, sku_key)
                lo = clean.quantile(pct / 100)
                hi = clean.quantile(1 - pct / 100)
                df.loc[mask, target_col] = vals.clip(lo, hi)

            elif strategy == "iqr_fence":
                iqr_k = _get("iqr_k", 1.5, sku_key)
                Q1, Q3 = clean.quantile(0.25), clean.quantile(0.75)
                IQR = Q3 - Q1
                if IQR > 0:
                    df.loc[mask, target_col] = vals.clip(Q1 - iqr_k * IQR, Q3 + iqr_k * IQR)

            elif strategy == "remove":
                # Use 3×IQR to identify, then interpolate
                Q1, Q3 = clean.quantile(0.25), clean.quantile(0.75)
                IQR = Q3 - Q1
                if IQR > 0:
                    lo, hi = Q1 - 3 * IQR, Q3 + 3 * IQR
                    outlier_mask = mask & ((pd.to_numeric(df[target_col], errors="coerce") < lo) |
                                          (pd.to_numeric(df[target_col], errors="coerce") > hi))
                    df.loc[outlier_mask, target_col] = np.nan
                    df.loc[mask, target_col] = (
                        df.loc[mask, target_col]
                          .interpolate(method="linear")
                          .ffill()
                          .bfill()
                    )

            elif strategy == "log1p":
                clipped = vals.clip(lower=0)
                df.loc[mask, target_col] = np.log1p(clipped)

        except Exception as e:
            log.warning(f"Outlier treatment failed for SKU={g}: {e}")

    return df


# ── Forecast series generation ─────────────────────────────────────────────

def _generate_forecast_series(engine, config: dict) -> dict:
    """
    Build {sku: {model: {"historical": [...], "forecast": [...]}}} from engine results.
    Uses the ML/stat forecasts produced by train(); returns {} if no forecast data.
    """
    import pandas as pd
    from collections import defaultdict

    fc_raw = engine.get_forecast()   # {"rows": [...], "n_skus": N, "horizon": H}
    rows = fc_raw.get("rows", [])
    if not rows:
        return {}

    col_cfg    = config["columns"]
    dt_col     = col_cfg["date"]
    target_col = col_cfg["target"]
    group_col  = col_cfg.get("group")

    df = engine._df.copy() if engine._df is not None else pd.DataFrame()

    # Historical series per SKU
    historical_by_sku: dict = {}
    if not df.empty and dt_col in df.columns and target_col in df.columns:
        df[dt_col] = pd.to_datetime(df[dt_col])
        src = (
            df.groupby(group_col)
            if group_col and group_col in df.columns
            else [("__all__", df)]
        )
        for sku, g in src:
            historical_by_sku[str(sku)] = [
                {"date": str(row[dt_col])[:10], "value": round(float(row[target_col]), 4)}
                for _, row in g.sort_values(dt_col).iterrows()
            ]

    # Group forecast rows by (sku, model)
    by_sku_model: dict = defaultdict(list)
    for r in rows:
        by_sku_model[(str(r["sku"]), str(r["model"]))].append(r)

    # Detect quantile column names from any point in the data
    q_keys: list = []
    for pts_list in by_sku_model.values():
        if pts_list:
            q_keys = sorted(
                [k for k in pts_list[0] if k.startswith("q") and k[1:].isdigit()],
                key=lambda k: int(k[1:]),
            )
            break

    result: dict = {}
    for (sku_key, model_name), pts in by_sku_model.items():
        pts_sorted = sorted(pts, key=lambda x: x.get("step", 0))
        forecast_pts = []
        for p in pts_sorted:
            # Resolve lower/upper: prefer explicit lower/upper, fall back to p90 bands
            lo = p.get("lower") if p.get("lower") is not None else p.get("p90_lo")
            hi = p.get("upper") if p.get("upper") is not None else p.get("p90_hi")
            raw_val = float(p["forecast"])
            raw_lo  = float(lo) if lo is not None else None
            raw_hi  = float(hi) if hi is not None else None
            pt: dict = {
                "date":  str(p["date"])[:10],
                "value": round(max(0.0, raw_val), 4),
                "lower": round(max(0.0, raw_lo), 4) if raw_lo is not None else None,
                "upper": round(max(0.0, raw_hi), 4) if raw_hi is not None else None,
            }
            for q_key in q_keys:
                v = p.get(q_key)
                pt[q_key] = round(float(v), 4) if v is not None else None
            forecast_pts.append(pt)
        if sku_key not in result:
            result[sku_key] = {}
        result[sku_key][model_name] = {
            "historical": historical_by_sku.get(sku_key, []),
            "forecast":   forecast_pts,
        }

    return result


# ── Progress helpers ───────────────────────────────────────────────────────

def _emit(tenant_id: str, session_id: str, job_id: str, percent: int, step: str, message: str):
    progress = {"percent": percent, "step": step, "message": message}
    update_progress(tenant_id, job_id, progress)
    broadcaster.broadcast_sync(job_id, {"type": "progress", "job_id": job_id, **progress})
    session_store.append_log(
        tenant_id, session_id, job_id,
        f"[{datetime.utcnow().isoformat()}] [{step}] {message}",
    )


# ── Main training entry point ──────────────────────────────────────────────

def run_training_job(tenant_id: str, session_id: str, job_id: str) -> None:
    log.info(f"Runner starting job={job_id} session={session_id} tenant={tenant_id}")
    try:
        _emit(tenant_id, session_id, job_id, 5, "init", "Building engine config...")
        config = build_engine_config(tenant_id, session_id)

        _emit(tenant_id, session_id, job_id, 10, "load", "Loading dataset...")
        from forecasting_core.engine import ForecastEngine
        engine = ForecastEngine.from_dict(config)
        engine.load_data(config["data"]["path"])

        gap_fill = config.pop("_gap_fill", "leave")
        outlier_cfg = config.pop("_outlier_config", {})

        col_cfg = config["columns"]

        if gap_fill and gap_fill != "leave" and engine._df is not None:
            _emit(tenant_id, session_id, job_id, 14, "gap_fill",
                  f"Filling missing dates (strategy: {gap_fill})...")
            engine._df = _apply_gap_fill(
                engine._df,
                date_col=col_cfg["date"],
                target_col=col_cfg["target"],
                group_col=col_cfg.get("group"),
                strategy=gap_fill,
            )

        strategy = outlier_cfg.get("strategy", "leave") if isinstance(outlier_cfg, dict) else "leave"
        if strategy and strategy != "leave" and engine._df is not None:
            _emit(tenant_id, session_id, job_id, 16, "outliers",
                  f"Applying outlier treatment (strategy: {strategy})...")
            engine._df = _apply_outlier_treatment(
                engine._df,
                date_col=col_cfg["date"],
                target_col=col_cfg["target"],
                group_col=col_cfg.get("group"),
                outlier_cfg=outlier_cfg,
            )

        _emit(tenant_id, session_id, job_id, 20, "inspect", "Running data quality check...")
        dq_report = engine.get_data_quality_report()

        _emit(tenant_id, session_id, job_id, 30, "routing", "Computing model routing...")
        routing = engine.get_routing_plan()

        _emit(tenant_id, session_id, job_id, 40, "training", "Training models (this may take a while)...")
        engine.train()

        _emit(tenant_id, session_id, job_id, 85, "results", "Collecting metrics...")
        metrics = engine.get_metrics()
        inventory = engine.get_inventory_report()
        report = engine.generate_report()

        _emit(tenant_id, session_id, job_id, 90, "saving", "Saving results...")
        result_payload = {
            "job_id": job_id,
            "run_id": engine._run_id,
            "completed_at": datetime.utcnow().isoformat(),
            "metrics": metrics,
            "inventory": inventory,
            "routing": routing,
            "data_quality": dq_report,
            "config": config,
        }
        session_store.set_training_result(tenant_id, session_id, result_payload)

        _emit(tenant_id, session_id, job_id, 93, "forecast", "Generating forecast series...")
        forecasts: dict = {}
        try:
            forecasts = _generate_forecast_series(engine, config)
            if forecasts:
                session_store.set_forecasts(tenant_id, session_id, forecasts)
                log.info(f"Saved forecast series for {len(forecasts)} SKUs")
            else:
                log.info("No forecast data available to save")
        except Exception as e:
            log.warning(f"Forecast series generation failed (non-fatal): {e}")

        _emit(tenant_id, session_id, job_id, 96, "indexing", "Indexing session for AI analyst...")
        try:
            from backend.ai import rag as _rag
            from backend.training.job_service import get_job as _get_job
            _job       = _get_job(tenant_id, job_id) or {}
            _user_id   = _job.get("created_by") or "system"
            _sess      = get_session(tenant_id, session_id) or {}
            _sess_name = _sess.get("name") or session_id
            inspection = session_store.get_field(tenant_id, session_id, "inspection") or {}
            n = _rag.index_session(
                tenant_id=tenant_id,
                session_id=session_id,
                result=result_payload,
                inspection=inspection,
                forecasts=forecasts,
                user_id=_user_id,
                session_name=_sess_name,
            )
            log.info(f"RAG: indexed {n} vectors for session {session_id} user {_user_id}")
        except Exception as e:
            log.warning(f"RAG indexing failed (non-fatal): {e}")

        # Save binary artifacts to disk
        from backend.storage import paths
        artifact_path = paths.artifacts_dir(tenant_id, session_id)
        artifact_path.mkdir(parents=True, exist_ok=True)
        try:
            engine.export_config(str(artifact_path / "session_config.json"))
        except Exception as e:
            log.warning(f"Config export failed: {e}")

        mark_completed(tenant_id, job_id)
        force_status(tenant_id, session_id, "COMPLETED", "results")
        fire_webhooks(tenant_id, "job.completed", {"job_id": job_id, "session_id": session_id})
        broadcaster.broadcast_sync(job_id, {"type": "completed", "job_id": job_id})
        log.info(f"Job {job_id} completed successfully")

    except Exception as exc:
        error_msg = str(exc)
        log.error(f"Job {job_id} failed: {error_msg}", exc_info=True)
        mark_failed(tenant_id, job_id, error_msg)
        force_status(tenant_id, session_id, "FAILED")
        fire_webhooks(tenant_id, "job.failed", {"job_id": job_id, "session_id": session_id, "error": error_msg})
        broadcaster.broadcast_sync(job_id, {
            "type": "failed",
            "job_id": job_id,
            "error": error_msg,
        })

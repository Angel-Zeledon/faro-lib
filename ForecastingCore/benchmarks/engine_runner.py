"""Runs the product's own engine on one training frame.

This mirrors what ``backend/workers/runner.py`` does for a training job:
``ForecastEngine.from_dict(config)`` -> ``load_data`` -> ``train`` ->
``get_metrics`` / ``get_forecast``. No pipeline internals are touched; the
champion per series is chosen exactly the way the product serves it (lowest
``cost_horizon`` among non-baseline models, falling back down
``CHAMPION_METRIC_ORDER``).
"""

from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd

DEFAULT_MODELS = ("lightgbm", "xgboost", "ets", "arima", "croston")


def build_config(horizon: int, season: int, models: List[str]) -> dict:
    """Same shape as ``build_engine_config`` in backend/workers/runner.py, with
    the wizard's defaults (walk-forward, 3 splits, min_history 20)."""
    return {
        "name": "benchmark",
        "data": {"path": "", "date_freq": None},
        "columns": {"target": "demand", "date": "date",
                    "group_keys": ["sku"], "exogenous": [], "inventory": ""},
        "features": {"lags": [1, 2, 3, 4], "rolling": [4, 8], "diffs": [1],
                     "calendar": True, "ewm_spans": [], "fourier_periods": [],
                     "fourier_K": 2},
        "models": {m: {} for m in models},
        "training": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3,
                     "min_history": 20, "seasonal_period": season, "max_workers": 1},
        "forecast": {"horizon": horizon},
        "granularity": {"strategy": "native", "target_freq": None},
        "hierarchy": {"levels": [], "reconciliation": "bottom_up"},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    }


def _val_scores(metrics_rows: list) -> Dict[str, Dict[str, float]]:
    """{sku: {model: validation cost}} on the champion metric, baselines out."""
    from forecasting_core.evaluation.metrics import CHAMPION_METRIC_ORDER
    if not metrics_rows:
        return {}
    m = pd.DataFrame(metrics_rows)
    if "model" not in m.columns:
        return {}
    metric = next((c for c in CHAMPION_METRIC_ORDER if c in m.columns), None)
    if metric is None:
        return {}
    if "type" in m.columns:
        m = m[m["type"] != "baseline"]
    m = m.dropna(subset=[metric])
    out: Dict[str, Dict[str, float]] = {}
    for sku, model, v in zip(m["sku"], m["model"], m[metric]):
        out.setdefault(str(sku), {})[str(model)] = float(v)
    return out


def _champions(metrics_rows: list, noise_aware: bool = True) -> Dict[str, str]:
    """Same call the engine makes (Pipeline._select_champions)."""
    scores = _val_scores(metrics_rows)
    if noise_aware:
        from forecasting_core.evaluation.champion import select_champions
        return select_champions(scores)
    return {s: min(v, key=v.get) for s, v in scores.items()}


def run_engine(train_df: pd.DataFrame, horizon: int, season: int,
               models: List[str]) -> dict:
    """Train on ``train_df`` (sku, date, demand) and return
    ``{"forecasts": {sku: {model: {"point": arr, "q": {tau: arr}}}},
       "champion": {sku: model}, "failed": [sku, ...], "error": str|None}``.
    """
    from forecasting_core.engine import ForecastEngine

    logging.getLogger("forecasting_core").setLevel(logging.ERROR)
    cfg = build_config(horizon, season, models)
    try:
        engine = ForecastEngine.from_dict(cfg)
        engine.load_data(train_df.copy())
        engine.train()
        metrics = engine.get_metrics()
        fc = engine.get_forecast()
    except Exception as exc:  # an engine failure is a result, not a crash
        return {"forecasts": {}, "champion": {}, "failed": sorted(train_df["sku"].astype(str).unique()),
                "error": f"{type(exc).__name__}: {exc}"}

    rows = pd.DataFrame(fc.get("rows") or [])
    forecasts: Dict[str, dict] = {}
    if not rows.empty:
        qcols = sorted((c for c in rows.columns if c.startswith("q") and c[1:].isdigit()),
                       key=lambda c: int(c[1:]))
        for (sku, model), g in rows.groupby(["sku", "model"]):
            g = g.sort_values("step").head(horizon)
            if len(g) < horizon:
                continue
            q = {int(c[1:]) / 100.0: np.maximum(0.0, g[c].to_numpy(dtype=float))
                 for c in qcols if g[c].notna().all()}
            forecasts.setdefault(str(sku), {})[str(model)] = {
                "point": np.maximum(0.0, g["forecast"].to_numpy(dtype=float)),
                "q": q,
            }
    rows_m = metrics.get("rows") or []
    champion = _champions(rows_m)
    champion = {s: m for s, m in champion.items() if s in forecasts and m in forecasts[s]}
    champion_plain = _champions(rows_m, noise_aware=False)
    champion_plain = {s: m for s, m in champion_plain.items() if s in forecasts and m in forecasts[s]}
    produced = set(forecasts)
    failed = sorted(set(train_df["sku"].astype(str).unique()) - produced)
    return {"forecasts": forecasts, "champion": champion,
            "champion_plain": champion_plain, "val_scores": _val_scores(rows_m),
            "failed": failed, "error": None}

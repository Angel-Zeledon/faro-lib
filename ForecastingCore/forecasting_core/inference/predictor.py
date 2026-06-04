"""
Inference module — generates future forecasts from trained models.

Two strategies:
  - ML models (LightGBM/XGBoost): recursive multi-step forecasting.
    Each future step generates features from actual + predicted history,
    then uses the fitted model to predict the next value.
  - Statistical models (ARIMA/Prophet/ETS/Croston): the training step
    already computes future forecast arrays when called with horizon > 0.
    Here we just attach dates and confidence intervals.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _compute_quantile_bounds(value: float, residual_std: float, quantiles: List[float]) -> dict:
    """
    Return per-quantile forecast bounds + backward-compat lower/upper keys.

    Each key is named q{int(q*100)} (e.g. q10, q50, q90, q95).
    lower = smallest configured quantile bound (or symmetric if none < 0.5).
    upper = largest configured quantile bound.
    """
    from scipy.stats import norm as _snorm

    bounds: dict = {}
    for q in quantiles:
        raw = value + float(_snorm.ppf(q)) * residual_std
        bounds[f"q{int(round(q * 100))}"] = round(max(0.0, raw), 4)

    sorted_qs = sorted(quantiles)
    lo_qs = [q for q in sorted_qs if q < 0.5]
    hi_qs = [q for q in sorted_qs if q >= 0.5]

    if lo_qs:
        bounds["lower"] = bounds[f"q{int(round(lo_qs[0] * 100))}"]
    else:
        hi_key = f"q{int(round(hi_qs[-1] * 100))}" if hi_qs else None
        bounds["lower"] = round(max(0.0, 2.0 * value - bounds.get(hi_key, value)), 4)

    bounds["upper"] = bounds[f"q{int(round(hi_qs[-1] * 100))}"] if hi_qs else round(value, 4)

    # p10/p50/p90 aliases for frontend consumption
    bounds["p10"] = bounds.get("q10", bounds["lower"])
    bounds["p50"] = bounds.get("q50", round(max(0.0, value), 4))
    bounds["p90"] = bounds.get("q90", bounds["upper"])
    return bounds


# ── Calendar helpers ───────────────────────────────────────────────────────

def _easter(year: int) -> pd.Timestamp:
    """Anonymous Gregorian Easter algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return pd.Timestamp(year=year, month=month, day=day)


def _calendar_row(dt: pd.Timestamp) -> dict:
    """
    Build the same calendar features that FeatureEngineer._calendar() produces
    for a single future date.  Holiday lookup uses precomputed Easter; Colombia
    national holidays beyond Easter/Christmas are approximated as zero for future
    dates (they cannot be known without full holiday library for future years, but
    Easter and Christmas are computed exactly).
    """
    easter_dt = _easter(dt.year)
    xmas_dt = pd.Timestamp(year=dt.year, month=12, day=25)
    days_to_easter = abs((dt - easter_dt).days)
    days_to_xmas = abs((dt - xmas_dt).days)
    is_christmas = int(dt.month == 12 and dt.day == 25)
    is_easter = int(dt.normalize() == easter_dt)

    return {
        "year":              dt.year,
        "month":             dt.month,
        "day":               dt.day,
        "dow":               dt.dayofweek,
        "week":              int(dt.isocalendar()[1]),
        "quarter":           dt.quarter,
        "is_weekend":        int(dt.dayofweek >= 5),
        "sin_month":         float(np.sin(2 * np.pi * dt.month / 12)),
        "cos_month":         float(np.cos(2 * np.pi * dt.month / 12)),
        "sin_dow":           float(np.sin(2 * np.pi * dt.dayofweek / 7)),
        "cos_dow":           float(np.cos(2 * np.pi * dt.dayofweek / 7)),
        "is_holiday":        0,
        "is_easter":         is_easter,
        "is_christmas":      is_christmas,
        "days_to_holiday":   0,
        "days_to_easter":    days_to_easter,
        "days_to_xmas":      days_to_xmas,
        "holiday_intensity": is_easter * 5 + is_christmas * 5,
        "christmas_season":  int(dt.month in [11, 12]),
    }


# ── ML recursive forecasting ───────────────────────────────────────────────

def recursive_ml_predict(
    fitted_model: Any,
    feature_names: List[str],
    residuals: np.ndarray,
    history: List[float],
    features_cfg: Any,
    horizon: int,
    future_dates: List[pd.Timestamp],
    quantiles: Optional[List[float]] = None,
    fitted_model_p10: Any = None,
    fitted_model_p50: Any = None,
    fitted_model_p90: Any = None,
) -> List[dict]:
    """
    Multi-step ahead recursive forecasting for an ML model.

    For each future step:
      1. Build calendar, lag, diff, rolling, and EWM features
         using the known + already-predicted history.
      2. Align to the model's expected feature_names.
      3. Predict → append to history → repeat.

    Prediction intervals are derived from training residuals at the requested
    quantiles (defaults to [0.1, 0.9] when not specified).

    Args:
        fitted_model:  Fitted sklearn-compatible model.
        feature_names: Ordered feature names the model was trained on.
        residuals:     In-sample residuals (training set).
        history:       List of historical target values (newest last).
                       Should be at least max(lags + rolling) long.
        features_cfg:  FeaturesConfig (lags, rolling, diffs, ewm_spans, calendar).
        horizon:       Number of steps ahead.
        future_dates:  List of pd.Timestamps, one per future step.
        quantiles:     Quantile levels for prediction intervals, e.g. [0.1, 0.5, 0.9].
                       Defaults to [0.1, 0.9].

    Returns:
        List of dicts with keys: date, value, lower, upper, q{N} per quantile.
    """
    if quantiles is None:
        quantiles = [0.1, 0.9]
    buf = list(history)
    residual_std = float(np.std(residuals)) if len(residuals) > 1 else 0.0

    results = []
    for future_dt in future_dates:
        row: dict = {}

        if features_cfg.calendar:
            row.update(_calendar_row(future_dt))

        # Lag features
        for l in features_cfg.lags:
            row[f"lag_{l}"] = float(buf[-l]) if len(buf) >= l else 0.0

        # Diff features
        for d in features_cfg.diffs:
            if len(buf) > d:
                row[f"diff_{d}"] = float(buf[-1] - buf[-1 - d])
                row[f"pct_change_{d}"] = float(
                    (buf[-1] - buf[-1 - d]) / (abs(buf[-1 - d]) + 1e-9)
                )
            else:
                row[f"diff_{d}"] = 0.0
                row[f"pct_change_{d}"] = 0.0

        # Rolling features (shift(1) then window — mirrors FeatureEngineer._rolling)
        for w in features_cfg.rolling:
            window_vals = buf[-(w + 1):-1] if len(buf) >= w + 1 else buf[:-1]
            if window_vals:
                arr = np.array(window_vals[-w:], dtype=float)
                mean_v = float(np.mean(arr))
                std_v = float(np.std(arr)) if len(arr) > 1 else 0.0
                row[f"roll_mean_{w}"] = mean_v
                row[f"roll_std_{w}"] = std_v
                row[f"roll_min_{w}"] = float(np.min(arr))
                row[f"roll_max_{w}"] = float(np.max(arr))
                row[f"cv_{w}"] = std_v / (abs(mean_v) + 1e-9)
            else:
                for col in (f"roll_mean_{w}", f"roll_std_{w}", f"roll_min_{w}",
                            f"roll_max_{w}", f"cv_{w}"):
                    row[col] = 0.0

        # EWM features
        for span in features_cfg.ewm_spans:
            if len(buf) >= 2:
                ewm_val = float(
                    pd.Series(buf[:-1], dtype=float).ewm(span=span).mean().iloc[-1]
                )
            else:
                ewm_val = float(buf[-1]) if buf else 0.0
            row[f"ewm_{span}"] = ewm_val

        # Align to model's expected feature order; fill any missing column with 0
        X = pd.DataFrame([{f: row.get(f, 0.0) for f in feature_names}])

        try:
            y_pred = max(0.0, float(fitted_model.predict(X)[0]))
        except Exception as e:
            log.warning(f"Prediction step failed ({future_dt}): {e}; using last known")
            y_pred = max(0.0, float(buf[-1]))

        buf.append(y_pred)

        point = {"date": str(future_dt)[:10], "value": round(y_pred, 4)}

        if fitted_model_p10 is not None and fitted_model_p50 is not None and fitted_model_p90 is not None:
            try:
                p10_val = max(0.0, float(fitted_model_p10.predict(X)[0]))
                p50_val = max(0.0, float(fitted_model_p50.predict(X)[0]))
                p90_val = max(0.0, float(fitted_model_p90.predict(X)[0]))
                point.update(_compute_quantile_bounds(y_pred, residual_std, quantiles))
                point["p10"] = round(p10_val, 4)
                point["p50"] = round(p50_val, 4)
                point["p90"] = round(p90_val, 4)
                point["lower"] = point["p10"]
                point["upper"] = point["p90"]
            except Exception:
                point.update(_compute_quantile_bounds(y_pred, residual_std, quantiles))
        else:
            point.update(_compute_quantile_bounds(y_pred, residual_std, quantiles))

        results.append(point)

    return results


# ── Top-level dispatcher ───────────────────────────────────────────────────

def predict_all_skus(
    fitted_models: dict,
    stat_forecasts: dict,
    raw_df: pd.DataFrame,
    config: Any,
) -> Dict[str, Dict[str, List[dict]]]:
    """
    Generate forecast series for every SKU from every trained model.

    Args:
        fitted_models:  {f"{model}_{sku}": {fitted_model, feature_names,
                                             residuals, model, sku, ...}}
                        — comes from Trainer results (ML models only).
        stat_forecasts: {model_name: {sku: {forecast: np.ndarray,
                                            residuals: np.ndarray}}}
                        — comes from stat model run_*_core calls with horizon > 0.
        raw_df:         Raw historical DataFrame (not feature-engineered).
        config:         SessionConfig instance.

    Returns:
        {sku: {model_name: [{date, value, lower, upper}]}}
    """
    c = config.columns
    horizon = config.forecast.horizon
    quantiles: List[float] = list(config.forecast.quantiles) if config.forecast.quantiles else [0.1, 0.9]
    result: Dict[str, Dict[str, List[dict]]] = {}

    # ── ML models ─────────────────────────────────────────────────────────
    for key, entry in fitted_models.items():
        fitted_model = entry.get("fitted_model")
        feature_names = entry.get("feature_names", [])
        residuals = entry.get("residuals", np.array([]))
        model_name = entry.get("model", "unknown")
        sku = str(entry.get("sku", "__all__"))

        if fitted_model is None or not feature_names:
            continue

        # SKU-specific historical data
        if c.group and raw_df is not None:
            sub = raw_df[raw_df[c.group].astype(str) == sku].sort_values(c.date)
        elif raw_df is not None:
            sub = raw_df.sort_values(c.date)
        else:
            continue

        sub = sub.dropna(subset=[c.target])
        if len(sub) < 2:
            continue

        # Buffer size: enough for all lag and rolling lookbacks
        lags = config.features.lags or [1]
        rolling = config.features.rolling or [1]
        max_lookback = max(max(lags), max(rolling)) + 2
        history = list(sub[c.target].astype(float).values[-max_lookback:])

        # Future dates
        dates = pd.to_datetime(sub[c.date])
        last_date = dates.iloc[-1]
        delta = dates.iloc[-1] - dates.iloc[-2] if len(dates) >= 2 else pd.Timedelta(days=1)
        future_dates = [last_date + delta * i for i in range(1, horizon + 1)]

        try:
            pts = recursive_ml_predict(
                fitted_model=fitted_model,
                feature_names=feature_names,
                residuals=residuals if isinstance(residuals, np.ndarray) else np.array([]),
                history=history,
                features_cfg=config.features,
                horizon=horizon,
                future_dates=future_dates,
                quantiles=quantiles,
                fitted_model_p10=entry.get("fitted_model_p10"),
                fitted_model_p50=entry.get("fitted_model_p50"),
                fitted_model_p90=entry.get("fitted_model_p90"),
            )
            result.setdefault(sku, {})[model_name] = pts
        except Exception as e:
            log.warning(f"ML predict failed {model_name}/{sku}: {e}")

    # ── Statistical models ─────────────────────────────────────────────────
    for model_name, sku_dict in stat_forecasts.items():
        for sku, entry in sku_dict.items():
            forecast_arr = entry.get("forecast")
            residuals = entry.get("residuals", np.array([]))

            if forecast_arr is None or len(forecast_arr) == 0:
                continue

            # Future dates for this SKU
            if c.group and raw_df is not None:
                sub = raw_df[raw_df[c.group].astype(str) == str(sku)].sort_values(c.date)
            elif raw_df is not None:
                sub = raw_df.sort_values(c.date)
            else:
                continue

            sub = sub.dropna(subset=[c.target])
            if len(sub) < 2:
                continue

            dates = pd.to_datetime(sub[c.date])
            last_date = dates.iloc[-1]
            delta = (
                dates.iloc[-1] - dates.iloc[-2]
                if len(dates) >= 2
                else pd.Timedelta(days=1)
            )
            residual_std = float(np.std(residuals)) if len(residuals) > 1 else 0.0

            p10_arr = entry.get("p10")
            p90_arr = entry.get("p90")
            p50_arr = entry.get("p50")

            pts = []
            for i, val in enumerate(forecast_arr[:horizon]):
                next_date = last_date + delta * (i + 1)
                v = max(0.0, float(val))
                point = {"date": str(next_date)[:10], "value": round(v, 4)}
                point.update(_compute_quantile_bounds(v, residual_std, quantiles))
                if p10_arr is not None and i < len(p10_arr):
                    point["p10"] = round(float(p10_arr[i]), 4)
                    point["lower"] = point["p10"]
                if p50_arr is not None and i < len(p50_arr):
                    point["p50"] = round(float(p50_arr[i]), 4)
                if p90_arr is not None and i < len(p90_arr):
                    point["p90"] = round(float(p90_arr[i]), 4)
                    point["upper"] = point["p90"]
                pts.append(point)

            if pts:
                result.setdefault(str(sku), {})[model_name] = pts

    return result

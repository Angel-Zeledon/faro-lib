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


def _primary_group(c) -> Optional[str]:
    """Return the first group key, or None if group_keys is empty."""
    return c.group_keys[0] if c.group_keys else None


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

def _calendar_rows(dates: List[pd.Timestamp], features_cfg: Any) -> List[dict]:
    """
    Calendar features for future dates, from the SAME builder training used.

    This function used to hardcode `is_holiday = 0` and `days_to_holiday = 0`
    for every future date, on the theory that future holidays are unknowable.
    They are not — the `holidays` library derives them from each country's
    rules — and the cost of the assumption was severe: the model was trained on
    a feature that carried real signal and then served a constant, so every
    holiday-driven demand spike was invisible to the forecast. Now both sides
    call `calendar_frame`.
    """
    from forecasting_core.features.calendar import (
        CALENDAR_COLUMNS, HolidayCalendar, calendar_frame,
    )
    cal = HolidayCalendar(getattr(features_cfg, "holiday_country", None))
    frame = calendar_frame(dates, cal)
    return [
        {col: (float(v) if isinstance(v, float) else int(v))
         for col, v in zip(CALENDAR_COLUMNS, row)}
        for row in frame.itertuples(index=False, name=None)
    ]


def _calendar_row(dt: pd.Timestamp, features_cfg: Any = None) -> dict:
    """Single future date's calendar features (see _calendar_rows)."""
    return _calendar_rows([pd.Timestamp(dt)], features_cfg)[0]


def _fourier_rows(dates: List[pd.Timestamp], features_cfg: Any) -> List[dict]:
    """Fourier terms for future dates — same fixed epoch training used."""
    from forecasting_core.features.calendar import fourier_frame

    periods = list(getattr(features_cfg, "fourier_periods", []) or [])
    if not periods:
        return [{} for _ in dates]
    K = int(getattr(features_cfg, "fourier_K", 2) or 2)
    frame = fourier_frame(dates, periods, K)
    cols = list(frame.columns)
    return [dict(zip(cols, row)) for row in frame.itertuples(index=False, name=None)]


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

    calendar_rows = (
        _calendar_rows(list(future_dates), features_cfg)
        if features_cfg.calendar else [{} for _ in future_dates]
    )
    seasonal_rows = _fourier_rows(list(future_dates), features_cfg)

    results = []
    for step_i, future_dt in enumerate(future_dates):
        row: dict = {}
        row.update(calendar_rows[step_i])
        row.update(seasonal_rows[step_i])

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

        # Rolling features — mirrors FeatureEngineer._rolling: shift(1).rolling(w)
        # at the step being predicted means the window is the w most recent
        # values in buf, INCLUDING the newest one. std uses ddof=1 like pandas.
        for w in features_cfg.rolling:
            window_vals = buf[-w:]
            if window_vals:
                arr = np.array(window_vals, dtype=float)
                mean_v = float(np.mean(arr))
                std_v = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
                row[f"roll_mean_{w}"] = mean_v
                row[f"roll_std_{w}"] = std_v
                row[f"roll_min_{w}"] = float(np.min(arr))
                row[f"roll_max_{w}"] = float(np.max(arr))
                row[f"cv_{w}"] = std_v / (abs(mean_v) + 1e-9)
            else:
                for col in (f"roll_mean_{w}", f"roll_std_{w}", f"roll_min_{w}",
                            f"roll_max_{w}", f"cv_{w}"):
                    row[col] = 0.0

        # EWM features — shift(1).ewm at the step being predicted covers every
        # value in buf, including the newest one.
        for span in features_cfg.ewm_spans:
            if buf:
                ewm_val = float(
                    pd.Series(buf, dtype=float).ewm(span=span).mean().iloc[-1]
                )
            else:
                ewm_val = 0.0
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


# ── Direct multi-horizon forecasting ───────────────────────────────────────

def _bucket_delta(dates: pd.Series) -> pd.Timedelta:
    """
    The series' cadence, taken as the MEDIAN gap rather than the last one.

    Reading it off the final two observations makes a single mistyped year
    define the whole forecast calendar. Measured end to end: a file whose 60
    daily rows carried one 1900 typo and one 2099 typo produced forecast dates
    of 2173-11-01, 2247-09-03 and 2321-07-05 — the run completed, reported no
    error, and presented a forecast for the twenty-fourth century as an ordinary
    result. The median is what `data/profiler.py` already uses to detect
    frequency, and one bad row cannot move it.
    """
    ordered = pd.to_datetime(pd.Series(dates)).sort_values()
    if len(ordered) < 2:
        return pd.Timedelta(days=1)
    gaps = ordered.diff().dropna()
    gaps = gaps[gaps > pd.Timedelta(0)]
    if gaps.empty:
        return pd.Timedelta(days=1)
    return pd.Timedelta(gaps.median())


def _future_dates(sub: pd.DataFrame, date_col: str, horizon: int) -> List[pd.Timestamp]:
    dates = pd.to_datetime(sub[date_col])
    last_date = dates.max()
    delta = _bucket_delta(dates)
    return [last_date + delta * i for i in range(1, horizon + 1)]


def _direct_forecast(entry: dict, raw_df, config, horizon: int,
                     quantiles: List[float]) -> List[dict]:
    """
    Forecast from a direct multi-horizon model, with conformal intervals.

    Two things differ from the recursive path, and both are the point of it:
    every step is predicted independently from the same origin, so no step is
    built on a guess; and the band around step h comes from the residuals
    measured AT step h in the rolling-origin backtest, so it widens with the
    horizon the way the real uncertainty does.
    """
    from forecasting_core.evaluation.conformal import (
        apply_bands, enforce_monotonic, horizon_bands,
    )

    forecaster = entry.get("direct_forecaster")
    if forecaster is None:
        return []

    c = config.columns
    primary = _primary_group(c)
    sku = str(entry.get("sku", "__all__"))
    if primary and raw_df is not None:
        sub = raw_df[raw_df[primary].astype(str) == sku].sort_values(c.date)
    elif raw_df is not None:
        sub = raw_df.sort_values(c.date)
    else:
        return []
    sub = sub.dropna(subset=[c.target])
    if len(sub) < 2:
        return []

    try:
        values = forecaster.point(horizon)
    except Exception as e:
        log.warning(f"Direct predict failed for {sku}: {e}")
        return []
    if values.size == 0:
        return []

    dates = _future_dates(sub, c.date, horizon)
    scale = float(getattr(forecaster.profile, "scale", 1.0))
    bands = horizon_bands(
        forecaster.residuals_by_horizon, quantiles,
        horizons=range(1, horizon + 1),
    )

    points: List[dict] = []
    for i, value in enumerate(values[:horizon]):
        step = i + 1
        point = {"date": str(dates[i])[:10], "value": round(float(value), 4)}
        band = bands.get(step)
        if band:
            point.update(apply_bands(float(value), enforce_monotonic(band), scale=scale))
        else:
            # No calibration data at all (a run with no viable folds): report
            # the point forecast with a degenerate band rather than inventing a
            # width. A band that is honestly absent beats one that is fabricated.
            point.update({"lower": point["value"], "upper": point["value"],
                          "p10": point["value"], "p50": point["value"],
                          "p90": point["value"]})
        points.append(point)
    return points


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

        # Direct multi-horizon models carry their own origin and predict every
        # step in one shot; they must not be fed to the recursive path, which
        # would treat step 1's prediction as an observation for step 2.
        if entry.get("forecast_strategy") == "direct":
            pts = _direct_forecast(entry, raw_df, config, horizon, quantiles)
            if pts:
                result.setdefault(sku, {})[model_name] = pts
            continue

        if fitted_model is None or not feature_names:
            continue

        # SKU-specific historical data
        if _primary_group(c) and raw_df is not None:
            sub = raw_df[raw_df[_primary_group(c)].astype(str) == sku].sort_values(c.date)
        elif raw_df is not None:
            sub = raw_df.sort_values(c.date)
        else:
            continue

        sub = sub.dropna(subset=[c.target])
        if len(sub) < 2:
            continue

        # Buffer size: enough for all lag, diff and rolling lookbacks
        lags = config.features.lags or [1]
        rolling = config.features.rolling or [1]
        diffs = config.features.diffs or [1]
        max_lookback = max(max(lags), max(rolling), max(diffs)) + 2
        history = list(sub[c.target].astype(float).values[-max_lookback:])

        # Future dates — cadence from the median gap, so one mistyped year
        # cannot set the forecast calendar (see _bucket_delta).
        dates = pd.to_datetime(sub[c.date])
        last_date = dates.max()
        delta = _bucket_delta(dates)
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
            if _primary_group(c) and raw_df is not None:
                sub = raw_df[raw_df[_primary_group(c)].astype(str) == str(sku)].sort_values(c.date)
            elif raw_df is not None:
                sub = raw_df.sort_values(c.date)
            else:
                continue

            sub = sub.dropna(subset=[c.target])
            if len(sub) < 2:
                continue

            dates = pd.to_datetime(sub[c.date])
            last_date = dates.max()
            delta = _bucket_delta(dates)
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

"""
Re-forecast: a fresh forecast from persisted models and NEWER history, without
re-estimating anything that was fitted.

What each family does with the new actuals
------------------------------------------
``lightgbm`` / ``xgboost``  recursive: the model is restored from its native
    format and the lag / rolling / EWM features are rebuilt from the new
    history. Nothing is refitted.
``global_lgbm``  direct: the model and the per-series scale stay fixed; the
    origin feature row is recomputed from the new history.
``arima``  the fitted parameters are applied to the new history (``filter``).
``ets``  the smoothing parameters and initial states are applied to the new
    history. Needs the series to start where it started at training time.
``croston`` / ``tsb`` / ``adida`` / ``imapa``  no fitted state beyond their
    constants: re-run on the new history.
``prophet`` (and anything else with no carried state)  CANNOT be updated: it is
    refitted for that series, and the result says so (``mode == "refit"``).

What it refuses, with a structured reason, instead of answering wrongly:
the input schema differs from the one the models were trained on, the cadence
changed, the new history ends BEFORE the models' data did, or it ends so far
after that the lag state the models would forecast from is a stale guess. See
``ReforecastRefused.code``.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from forecasting_core.reforecast import bundle as _bundle
from forecasting_core.reforecast.bundle import (
    KIND_ML_GLOBAL, KIND_ML_RECURSIVE, KIND_STAT_STATE, LoadedArtifacts,
)
from forecasting_core.reforecast.prepare import (
    bucket_seconds, diff_schema, input_schema, numeric_view, prepare_history,
)

log = logging.getLogger(__name__)

MODE_UPDATED = "updated"
MODE_REFIT = "refit"
MODE_SKIPPED = "skipped"

# Cadence may wobble by this much (a 30-day and a 31-day month) before it counts
# as a different cadence.
_CADENCE_TOLERANCE = 0.15


class ReforecastRefused(Exception):
    """The artifacts cannot answer for this data. ``code`` is stable and
    machine-readable; ``params`` carries what a person needs to act on it."""

    def __init__(self, code: str, message: str, params: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params or {}


@dataclass
class UnitStatus:
    sku: str
    model: str
    mode: str
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"sku": self.sku, "model": self.model, "mode": self.mode,
                "reason": self.reason}


@dataclass
class ReforecastResult:
    forecast_df: pd.DataFrame
    inventory_df: Optional[pd.DataFrame]
    metrics_df: pd.DataFrame
    demand_risk: dict
    policy_backtest: dict
    run_metadata: dict
    statuses: List[UnitStatus]
    horizon: int
    parent_run_id: str
    new_series: List[str] = field(default_factory=list)
    shift_buckets: float = 0.0
    fitted_models: dict = field(default_factory=dict)

    @property
    def summary(self) -> dict:
        by_mode: Dict[str, int] = {}
        by_model: Dict[str, Dict[str, int]] = {}
        reasons: Dict[str, int] = {}
        for s in self.statuses:
            by_mode[s.mode] = by_mode.get(s.mode, 0) + 1
            by_model.setdefault(s.model, {})
            by_model[s.model][s.mode] = by_model[s.model].get(s.mode, 0) + 1
            if s.reason:
                reasons[s.reason] = reasons.get(s.reason, 0) + 1
        return {
            "by_mode": by_mode, "by_model": by_model, "skip_reasons": reasons,
            "new_series": len(self.new_series),
            "shift_buckets": round(self.shift_buckets, 2),
            "refit_needed": by_mode.get(MODE_REFIT, 0) > 0,
        }


# -- compatibility ----------------------------------------------------------

def _primary(cfg) -> Optional[str]:
    return cfg.columns.group_keys[0] if cfg.columns.group_keys else None


def _check_supported(cfg) -> None:
    if (cfg.transforms.columns or {}):
        raise ReforecastRefused(
            "unsupported_transforms",
            "The session scales or encodes columns; those transforms are fitted "
            "on the data and are not carried over, so a full refit is required.")
    if getattr(cfg.hierarchy, "levels", None):
        raise ReforecastRefused(
            "unsupported_hierarchy",
            "The session reconciles a hierarchy; reconciliation is not applied "
            "on a re-forecast, so a full refit is required.")


def _check_columns(cfg, df: pd.DataFrame) -> None:
    c = cfg.columns
    needed = [c.date, c.target] + ([_primary(cfg)] if _primary(cfg) else [])
    missing = [col for col in needed if col not in df.columns]
    if missing:
        raise ReforecastRefused(
            "schema_incompatible",
            f"The new history lacks required column(s): {missing}.",
            {"missing_columns": ", ".join(missing)})


def _check_schema(ctx: dict, cfg, df: pd.DataFrame) -> None:
    changed = diff_schema(ctx.get("input_schema") or {}, input_schema(cfg))
    if set(changed) == {"group_keys"}:
        trained_keys = list(changed["group_keys"]["trained"] or [])
        current_keys = list(changed["group_keys"]["current"] or [])
        if len(trained_keys) >= 2 and current_keys == trained_keys[:1]:
            # Models fitted per (SKU, store) before stores were summed into one
            # series per SKU (data/store_rollup.py). Those models forecast one
            # store's demand as the SKU's, so they must not be reused; the user
            # changed nothing, so "your columns changed" would be a false reason.
            raise ReforecastRefused(
                "stores_summed_since_training",
                "The models were trained per (SKU, store); the session now "
                "forecasts each SKU on the sum of its stores, so a full refit is "
                "required.",
                {"trained_group_keys": ", ".join(map(str, trained_keys))})
    if changed:
        raise ReforecastRefused(
            "schema_incompatible",
            "The input schema differs from the one the models were trained on: "
            f"{sorted(changed)}.",
            {"fields": ", ".join(sorted(changed))})


def _check_window(ctx: dict, df: pd.DataFrame, cfg, max_shift: Optional[float]) -> float:
    c = cfg.columns
    trained_bucket = float(ctx.get("bucket_seconds") or 86400.0)
    current_bucket = bucket_seconds(df, c.date)
    if abs(current_bucket - trained_bucket) / trained_bucket > _CADENCE_TOLERANCE:
        raise ReforecastRefused(
            "cadence_changed",
            "The new history has a different cadence than the data the models "
            "were trained on.",
            {"trained_seconds": int(trained_bucket), "current_seconds": int(current_bucket)})

    trained_last = pd.Timestamp((ctx.get("data_window") or {}).get("last"))
    new_last = pd.to_datetime(df[c.date]).max()
    shift = (new_last - trained_last).total_seconds() / trained_bucket
    if shift < -0.5:
        raise ReforecastRefused(
            "history_older_than_models",
            "The new history ends before the data the models were trained on.",
            {"trained_last": str(trained_last)[:10], "new_last": str(new_last)[:10]})
    limit = float(max_shift if max_shift is not None else ctx.get("trained_horizon") or 1)
    if shift > limit + 1e-9:
        raise ReforecastRefused(
            "window_shifted_too_far",
            "The new history runs too far past the data the models were trained "
            "on for their lag state to be updated reliably; refit instead.",
            {"shift_buckets": round(shift, 2), "limit_buckets": round(limit, 2),
             "trained_last": str(trained_last)[:10], "new_last": str(new_last)[:10]})
    return float(max(shift, 0.0))


# -- stat families ----------------------------------------------------------

def _stat_from_state(model: str, state: dict, series: np.ndarray, h: int,
                     same_start: bool) -> Optional[dict]:
    """Forecast ``h`` steps by running the carried state over ``series``.
    Returns None when the state cannot be applied to this series."""
    if model == "arima" and state.get("kind") == "arima":
        from statsmodels.tsa.arima.model import ARIMA
        res = ARIMA(pd.Series(series), order=tuple(state["order"])).filter(
            np.asarray(state["params"], dtype=float))
        fc = res.get_forecast(steps=h)
        mean = np.asarray(fc.predicted_mean, dtype=float)
        ci = fc.conf_int(alpha=0.2)
        return {"forecast": mean, "p50": mean,
                "p10": np.maximum(0.0, np.asarray(ci.iloc[:, 0], dtype=float)),
                "p90": np.maximum(0.0, np.asarray(ci.iloc[:, 1], dtype=float))}

    if model == "ets" and state.get("kind") == "ets":
        if not same_start:
            return None          # initial states are tied to where the series began
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        p = state["params"]
        seasonal = bool(state["use_seasonal"])
        init_seasonal = p.get("initial_seasons") if seasonal else None
        fit_kw = {"smoothing_level": p.get("smoothing_level"),
                  "smoothing_trend": p.get("smoothing_trend"),
                  "optimized": False}
        if seasonal:
            fit_kw["smoothing_seasonal"] = p.get("smoothing_seasonal")
        if p.get("damping_trend") is not None:
            fit_kw["damping_trend"] = p.get("damping_trend")
        res = ExponentialSmoothing(
            series, trend="add",
            seasonal="add" if seasonal else None,
            seasonal_periods=int(state["seasonal_period"]) if seasonal else None,
            initialization_method="known",
            initial_level=p.get("initial_level"),
            initial_trend=p.get("initial_trend"),
            initial_seasonal=init_seasonal,
        ).fit(**fit_kw)
        return {"forecast": np.asarray(res.forecast(h), dtype=float)}

    if model == "croston" and state.get("kind") == "croston":
        from forecasting_core.models.croston import croston_forecast
        return {"forecast": croston_forecast(series, alpha=float(state["alpha"]), n_ahead=h)}

    if model in ("tsb", "adida", "imapa"):
        # Same as Croston: fixed constants, no fitted state — re-run them.
        from forecasting_core.models.intermittent import forecast_from_state
        fc = forecast_from_state(model, state, series, h)
        return None if fc is None else {"forecast": fc}
    return None


def _refit_stat(model: str, sku: str, df: pd.DataFrame, cfg, h: int) -> Optional[dict]:
    """Run the family's own training function on this one series. The slow path,
    taken only for what cannot be updated; callers flag it."""
    c, t = cfg.columns, cfg.training
    primary = _primary(cfg)
    sub = df[df[primary].astype(str) == sku] if primary else df
    if model == "arima":
        from forecasting_core.models.arima import run_arima_core as fn
        extra: dict = {}
    elif model == "ets":
        from forecasting_core.models.ets import run_ets_core as fn
        extra = {}
    elif model == "croston":
        from forecasting_core.models.croston import run_croston_core as fn
        extra = {}
    elif model == "tsb":
        from forecasting_core.models.intermittent import run_tsb_core as fn
        extra = {}
    elif model == "adida":
        from forecasting_core.models.intermittent import run_adida_core as fn
        extra = {}
    elif model == "imapa":
        from forecasting_core.models.intermittent import run_imapa_core as fn
        extra = {}
    elif model == "prophet":
        from forecasting_core.models.prophet import run_prophet_core as fn
        extra = {"holiday_country": getattr(cfg.features, "holiday_country", "") or ""}
    else:
        return None
    out = fn(sub, c.date, c.target, primary, t.train_ratio, t.min_history,
             t.seasonal_period, horizon=h, **extra)
    res = out.get(sku if primary else "__all__")
    if not isinstance(res, dict) or res.get("forecast") is None:
        return None
    return res


# -- ML families ------------------------------------------------------------

def _restore_ml(family: str, payload: dict, series_last: Dict[str, str],
                statuses: List[UnitStatus]) -> dict:
    out: Dict[str, dict] = {}
    for key, unit in payload["units"].items():
        sku = unit["sku"]
        model = _bundle.restore_native(unit.get("native"))
        if model is None:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, "model_not_persisted"))
            continue
        reason = _series_problem(unit, series_last)
        if reason:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, reason))
            continue
        out[key] = {
            "sku": sku, "store": unit.get("store"), "model": family,
            "fitted_model": model,
            "feature_names": list(unit.get("feature_names") or []),
            "residuals": np.asarray(unit.get("residuals", []), dtype=float),
        }
        statuses.append(UnitStatus(sku, family, MODE_UPDATED))
    return out


def _series_problem(unit: dict, series_last: Dict[str, str]) -> Optional[str]:
    sku = unit["sku"]
    now = series_last.get(sku)
    if now is None:
        return "series_missing"
    trained = unit.get("trained_last_date")
    if trained and pd.Timestamp(now) < pd.Timestamp(trained):
        return "series_history_shorter"
    return None


def _restore_global(family: str, payload: dict, df: pd.DataFrame, cfg, h: int,
                    series_last: Dict[str, str], statuses: List[UnitStatus]) -> dict:
    from forecasting_core.data.canonical import SERIES_SEPARATOR
    from forecasting_core.features.engineer import FeatureEngineer
    from forecasting_core.training.global_trainer import (
        GlobalDirectForecaster, SeriesProfile,
    )

    units = payload["units"]
    shared = payload["shared"]
    model = _bundle.restore_native(shared.get("native"))

    def _skip_all(reason: str) -> dict:
        for u in units.values():
            statuses.append(UnitStatus(u["sku"], family, MODE_SKIPPED, reason))
        return {}

    if model is None:
        return _skip_all("model_not_persisted")
    if h > int(payload.get("trained_horizon") or 0):
        return _skip_all("horizon_exceeds_training")

    c = cfg.columns
    group_cols = [k for k in c.group_keys if k in df.columns]
    engineer = FeatureEngineer(cfg.features, dt_col=c.date, target=c.target,
                               group_cols=group_cols)
    df_ml = numeric_view(engineer.transform(df, drop_warmup=False))
    if group_cols:
        df_ml = df_ml.sort_values(group_cols + [c.date]).reset_index(drop=True)
        keys = df_ml[group_cols].astype(str).agg(SERIES_SEPARATOR.join, axis=1)
    else:
        df_ml = df_ml.sort_values(c.date).reset_index(drop=True)
        keys = pd.Series(["__all__"] * len(df_ml))
    last_idx = df_ml.groupby(keys, sort=False).tail(1)
    rows_by_key = {keys.loc[i]: df_ml.loc[i] for i in last_idx.index}
    counts = keys.value_counts()

    residuals = {int(h_): np.asarray(v, dtype=float)
                 for h_, v in shared.get("residuals_by_horizon", [])}
    cumulative = {int(h_): np.asarray(v, dtype=float)
                  for h_, v in shared.get("cumulative_residuals_by_horizon", [])}
    scaled = list(shared.get("scaled_features") or [])

    out: Dict[str, dict] = {}
    for key, unit in units.items():
        sku = unit["sku"]
        reason = _series_problem(unit, series_last)
        prof = unit["profile"]
        row = rows_by_key.get(prof["key"])
        if reason is None and (row is None or int(counts.get(prof["key"], 0)) < 2):
            reason = "series_missing"
        feature_cols = list(unit.get("feature_names") or [])
        if reason is None and any(col not in df_ml.columns for col in feature_cols):
            reason = "feature_columns_missing"
        if reason:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, reason))
            continue

        scale = float(prof["scale"])
        origin: Dict[str, float] = {}
        for col in feature_cols:
            v = float(row[col])
            # The trainer stores the origin in raw units after dividing by the
            # scale; repeating the round trip keeps unchanged history bit-for-bit.
            origin[col] = (v / scale) * scale if col in scaled else v
        profile = SeriesProfile(
            key=prof["key"], sku=prof["sku"], store=prof["store"],
            codes=tuple(int(x) for x in prof["codes"]), scale=scale,
            cv=float(prof["cv"]), n_rows=int(prof["n_rows"]))
        forecaster = GlobalDirectForecaster(
            model=model, profile=profile,
            model_features=list(shared["model_features"]), scaled_features=scaled,
            origin_row=origin, origin_lag=1,
            residuals_by_horizon=residuals, cumulative_residuals_by_horizon=cumulative)
        out[key] = {
            "sku": sku, "store": unit.get("store"), "model": family,
            "forecast_strategy": "direct", "direct_forecaster": forecaster,
            "fitted_model": None, "feature_names": feature_cols,
            "residuals": np.array([]),
        }
        statuses.append(UnitStatus(sku, family, MODE_UPDATED))
    return out


def _restore_stat(family: str, payload: dict, df: pd.DataFrame, cfg, h: int,
                  series_last: Dict[str, str], allow_refit: bool,
                  statuses: List[UnitStatus]) -> dict:
    c = cfg.columns
    primary = _primary(cfg)
    out: Dict[str, dict] = {}
    for sku, unit in payload["units"].items():
        reason = _series_problem(unit, series_last)
        if reason:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, reason))
            continue
        sub = df[df[primary].astype(str) == sku] if primary else df
        sub = sub.sort_values(c.date)
        series = sub[c.target].astype(float).to_numpy()
        first = str(pd.to_datetime(sub[c.date]).min())[:10]
        same_start = unit.get("trained_first_date") == first

        result = None
        state = unit.get("state")
        if state:
            try:
                result = _stat_from_state(family, state, series, h, same_start)
            except Exception as exc:  # noqa: BLE001
                log.warning("re-forecast: %s state failed for %s: %s", family, sku, exc)
        if result is not None:
            result["residuals"] = np.asarray(unit.get("residuals", []), dtype=float)
            out[sku] = result
            statuses.append(UnitStatus(sku, family, MODE_UPDATED))
            continue

        # Not updatable. Refit this series, flagged, or say why not.
        if not allow_refit:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, "refit_required"))
            continue
        try:
            result = _refit_stat(family, sku, df, cfg, h)
        except Exception as exc:  # noqa: BLE001
            log.warning("re-forecast: refit of %s failed for %s: %s", family, sku, exc)
            result = None
        if result is None:
            statuses.append(UnitStatus(sku, family, MODE_SKIPPED, "refit_not_supported"))
            continue
        out[sku] = result
        statuses.append(UnitStatus(sku, family, MODE_REFIT, "state_not_carried"
                                   if not state else "state_not_applicable"))
    return out


# -- entry point ------------------------------------------------------------

def reforecast(
    artifacts: LoadedArtifacts,
    new_history: pd.DataFrame,
    config,
    horizon: Optional[int] = None,
    *,
    max_shift_buckets: Optional[float] = None,
    allow_refit: bool = True,
) -> ReforecastResult:
    """A fresh forecast from ``artifacts`` over ``new_history``.

    ``config`` is the ``SessionConfig`` to forecast under (its business rules
    drive the inventory recommendations); its input schema must equal the one the
    artifacts were trained with. ``horizon`` defaults to ``config.forecast.horizon``.
    Raises ``ReforecastRefused``."""
    from forecasting_core.ensemble.ensemble import WeightedEnsemble
    from forecasting_core.pipelines.pipeline import Pipeline

    ctx = artifacts.context
    cfg = copy.deepcopy(config)
    _check_supported(cfg)
    h = int(horizon or cfg.forecast.horizon or ctx.get("trained_horizon") or 1)
    cfg.forecast.horizon = h

    _check_columns(cfg, new_history)
    df = prepare_history(cfg, new_history)
    if df.empty:
        raise ReforecastRefused("schema_incompatible", "The new history has no usable rows.",
                                {"missing_columns": ""})
    _check_schema(ctx, cfg, df)
    shift = _check_window(ctx, df, cfg, max_shift_buckets)

    c = cfg.columns
    primary = _primary(cfg)
    if primary:
        series_last = {str(k): str(pd.to_datetime(g[c.date]).max())[:10]
                       for k, g in df.groupby(df[primary].astype(str))}
    else:
        series_last = {"__all__": str(pd.to_datetime(df[c.date]).max())[:10]}

    statuses: List[UnitStatus] = []
    results_ml: Dict[str, dict] = {}
    results_stat: Dict[str, dict] = {}
    for family, payload in artifacts.families.items():
        kind = payload.get("kind")
        if kind == KIND_ML_RECURSIVE:
            results_ml.update(_restore_ml(family, payload, series_last, statuses))
        elif kind == KIND_ML_GLOBAL:
            results_ml.update(_restore_global(family, payload, df, cfg, h,
                                              series_last, statuses))
        else:
            restored = _restore_stat(family, payload, df, cfg, h, series_last,
                                     allow_refit, statuses)
            if restored:
                results_stat[family] = restored

    # Explanations belong to the models, which did not change: carry them over so
    # the metrics read of a re-forecast is as complete as the parent's.
    shap = ctx.get("shap") or {}
    for entry in results_ml.values():
        imp = (shap.get(str(entry.get("sku"))) or {}).get(str(entry.get("model")))
        if imp:
            entry["shap_importance"] = imp

    metrics_df = pd.DataFrame(ctx.get("metrics_rows") or [])
    ensemble = WeightedEnsemble()
    scores = Pipeline._ensemble_scores(metrics_df)
    if scores:
        ensemble.fit(scores)

    pipe = Pipeline(cfg)
    forecast_df = pipe._generate_forecast_df(results_ml, results_stat, df, ensemble=ensemble)
    if forecast_df is None or forecast_df.empty:
        raise ReforecastRefused(
            "nothing_to_forecast",
            "No persisted model could produce a forecast for this history.",
            {"skipped": len([s for s in statuses if s.mode == MODE_SKIPPED])})

    inventory_df = pipe._inventory(df, c, cfg.business, h, forecast_df=forecast_df,
                                   metrics_df=metrics_df)

    known = set((ctx.get("series_anchors") or {}).keys())
    new_series = sorted(set(series_last) - known)[:200]
    meta = dict(ctx.get("run_metadata") or {})
    meta["skipped_no_forecast"] = list(pipe._skipped_no_forecast)

    return ReforecastResult(
        forecast_df=forecast_df, inventory_df=inventory_df, metrics_df=metrics_df,
        demand_risk=ctx.get("demand_risk") or {},
        policy_backtest=ctx.get("policy_backtest") or {},
        run_metadata=meta, statuses=statuses, horizon=h,
        parent_run_id=str(ctx.get("run_id") or ""), new_series=new_series,
        shift_buckets=shift, fitted_models=results_ml,
    )

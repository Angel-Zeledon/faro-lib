"""
Pipeline — orchestrates the full forecasting workflow.

Steps:
  validate → load → profile → clean → feature_eng
  → route → train → evaluate → forecast → inventory → register

Each step can be run individually or as a full pipeline.run().

Example:
    pipeline = Pipeline(config)
    results = pipeline.run()
    # results.metrics_df, results.forecast_df, results.inventory_df
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store

log = logging.getLogger(__name__)


class PipelineStatus(Enum):
    """
    Lifecycle states of a Pipeline.run() execution.

    Broadcast via the on_progress callback as {"pct": int, "message": str, "status": str}.
    """
    IDLE        = "idle"
    LOADING     = "loading"
    VALIDATING  = "validating"
    QUALITY     = "quality"
    ROUTING     = "routing"
    TRAINING    = "training"
    FORECASTING = "forecasting"
    INVENTORY   = "inventory"
    DONE        = "done"
    FAILED      = "failed"


@dataclass
class PipelineResults:
    """All outputs from a full pipeline run."""
    metrics_df:           Optional[pd.DataFrame] = None
    forecast_df:          Optional[pd.DataFrame] = None
    forecast_by_sku_df:   Optional[pd.DataFrame] = None   # rollup: sum by SKU across stores
    forecast_by_store_df: Optional[pd.DataFrame] = None   # rollup: sum by store across SKUs
    inventory_df:         Optional[pd.DataFrame] = None
    quality_df:           Optional[pd.DataFrame] = None
    run_id:               str = ""
    config_hash:          str = ""
    metadata:             dict = field(default_factory=dict)
    fitted_models:        dict = field(default_factory=dict)   # {key: ML trainer result}
    stat_forecasts:       dict = field(default_factory=dict)   # {model: {sku: {forecast, residuals}}}


# ---------------------------------------------------------------------------
# ML recursive multi-step forecaster
# ---------------------------------------------------------------------------

def _ml_recursive_forecast(
    model,
    target_history: np.ndarray,
    last_feature_row: np.ndarray,
    feature_names: List[str],
    horizon: int,
) -> np.ndarray:
    """
    Iterative horizon-step forecast for an sklearn-compatible ML model.

    Updates lag features after each step; rolling/calendar features are held
    constant (valid approximation for short horizons).
    """
    # Map lag_N column names → their index in feature_names
    lag_map: Dict[int, int] = {}
    for i, col in enumerate(feature_names):
        if col.startswith("lag_"):
            try:
                lag_map[int(col.split("_")[1])] = i
            except (ValueError, IndexError):
                pass

    max_lag = max(lag_map.keys()) if lag_map else 1
    buffer = list(target_history[-max(max_lag, 1):]) if len(target_history) > 0 else [0.0]

    preds = []
    feat = last_feature_row.copy().astype(float)

    for _ in range(horizon):
        try:
            pred = float(model.predict(feat.reshape(1, -1))[0])
            pred = max(0.0, pred)
        except Exception:
            pred = float(buffer[-1]) if buffer else 0.0
        preds.append(pred)
        buffer.append(pred)

        # Update lag features: lag_n = value n steps back from end of buffer
        for n, idx in lag_map.items():
            if len(buffer) >= n:
                feat[idx] = buffer[-n]

    return np.array(preds)


# ---------------------------------------------------------------------------
# Config → flat dict for validators
# ---------------------------------------------------------------------------

def _config_as_validation_dict(cfg) -> dict:
    """Flatten SessionConfig into the dict format expected by validation modules."""
    return {
        "dt":                 cfg.columns.date,
        "target":             cfg.columns.target,
        "group_id":           cfg.columns.group_keys[0] if cfg.columns.group_keys else None,
        "data":               cfg.data.path,
        "models":             cfg.models,
        "train_ratio":        cfg.training.train_ratio,
        "prediction_horizon": cfg.forecast.horizon,
        "min_history":        cfg.training.min_history,
        "seasonal_period":    cfg.training.seasonal_period,
        "wfv_splits":         cfg.training.wfv_splits,
        "features": {
            "lags":    cfg.features.lags,
            "rolling": cfg.features.rolling,
            "diffs":   cfg.features.diffs,
        },
    }


class Pipeline:
    """
    Full forecasting pipeline driven by a SessionConfig.

    Args:
        config: A SessionConfig instance with all parameters.

    Usage:
        pipeline = Pipeline(config)
        results = pipeline.run()
    """

    def __init__(self, config, df: Optional[pd.DataFrame] = None):
        from forecasting_core.config.config import SessionConfig
        self.config = config if isinstance(config, object) else SessionConfig.from_dict(config)
        self._df = df  # Optional pre-loaded DataFrame; if None, loads from cfg.data.path

    def run(self, on_progress: Optional[Callable[[dict], None]] = None) -> PipelineResults:
        """
        Execute the full pipeline end-to-end.

        Parameters
        ----------
        on_progress : callable, optional
            Called at each major step with a dict:
            {"pct": int 0-100, "message": str, "status": PipelineStatus.value}

            Example usage (backend worker):
                def update(evt):
                    redis.publish("session:progress", json.dumps(evt))
                Pipeline(config).run(on_progress=update)
        """

        def _progress(pct: int, message: str, status: PipelineStatus = PipelineStatus.TRAINING):
            if on_progress:
                try:
                    on_progress({"pct": pct, "message": message, "status": status.value})
                except Exception:
                    pass

        """Execute the full pipeline end-to-end."""
        from forecasting_core.data.loader import DataLoader
        from forecasting_core.data.quality import DataQualityChecker
        from forecasting_core.features.engineer import FeatureEngineer
        from forecasting_core.models.factory import ModelFactory
        from forecasting_core.training.trainer import Trainer
        from forecasting_core.training.router import ModelRouter
        from forecasting_core.models.arima import run_arima_core
        from forecasting_core.models.prophet import run_prophet_core
        from forecasting_core.models.ets import run_ets_core
        from forecasting_core.models.croston import run_croston_core
        from forecasting_core.models.sarimax import run_sarimax_core
        from forecasting_core.models.lstm import run_lstm_core
        from forecasting_core.evaluation.baselines import BaselineEvaluator
        from forecasting_core.ensemble.ensemble import WeightedEnsemble
        from forecasting_core.registry.registry import ModelRegistry
        from forecasting_core.validation.auto_correct import auto_correct_data
        from forecasting_core.validation.pipeline_resilience import PartialResultCollector
        from forecasting_core.validation import (
            validate_semantic, validate_data, detect_leakage,
            check_model_compatibility, ValidationMode,
        )

        cfg = self.config
        c   = cfg.columns
        t   = cfg.training
        b   = cfg.business
        h   = cfg.forecast.horizon
        val_cfg = _config_as_validation_dict(cfg)

        _progress(0, "Loading data", PipelineStatus.LOADING)

        # 1. Load — use injected DataFrame if available, otherwise read from disk
        if self._df is not None:
            log.info("Pipeline: using pre-loaded DataFrame...")
            df = self._df.copy()
        else:
            log.info("Pipeline: loading data from disk...")
            df = DataLoader().load(cfg.data.path, cfg.data.sql_engine)

        df[c.date] = pd.to_datetime(df[c.date])
        df = df.dropna(subset=[c.target]).sort_values(
            [c.group, c.date] if c.group else [c.date]
        ).reset_index(drop=True)

        # Resolve group_cols: use configured group_keys filtered to columns
        # actually present in the DataFrame so that multi-key configs (e.g.
        # ["sku", "store"]) degrade gracefully on single-key datasets.
        group_cols: List[str] = [k for k in c.group_keys if k in df.columns]

        _progress(10, "Validating data", PipelineStatus.VALIDATING)

        # 2. Validation layers (WARNING mode — issues logged, never abort)
        log.info("Pipeline: running validation layers...")
        self._run_validation(df, val_cfg)

        # 2b. Auto-correct: fill NaN gaps, clip outliers per SKU
        df, correction_log = auto_correct_data(df, val_cfg, clip_outliers=True)
        for entry in correction_log.to_list():
            log.info(f"[auto_correct] {entry['action']}: {entry['description']}")

        _progress(20, "Checking data quality", PipelineStatus.QUALITY)

        # 3. Data Quality
        log.info("Pipeline: data quality check...")
        rt = cfg.routing.thresholds
        checker = DataQualityChecker(
            dt_col=c.date, target_col=c.target, group_col=c.group_keys[0] if c.group_keys else None,
            min_history=t.min_history, seasonal_period=t.seasonal_period,
            freq=cfg.data.date_freq,
            intermittency_threshold=rt.intermittency,
            cv_volatile=rt.cv_volatile,
            seasonal_strength_threshold=rt.seasonal_strength,
        )
        dq_reports = checker.check(df)
        quality_df = checker.summary(dq_reports)
        df = checker.filter_valid_skus(df, dq_reports)
        n_valid = df[c.group].nunique() if c.group else 1
        log.info(f"  Valid SKUs: {n_valid}")

        _progress(30, f"Routing models for {n_valid} SKUs", PipelineStatus.ROUTING)

        # 4. Model Routing
        log.info("Pipeline: routing models...")
        router = ModelRouter(cfg.models, enabled=cfg.routing.enabled)
        routing = router.route(dq_reports)
        log.info(router.summary(routing))

        _progress(40, "Engineering features", PipelineStatus.TRAINING)

        # 5. Feature Engineering
        log.info("Pipeline: feature engineering...")
        engineer = FeatureEngineer(
            cfg.features, dt_col=c.date, target=c.target,
            group_cols=group_cols,
        )
        df_ml = engineer.transform(df)

        # 6. Baselines
        baselines = self._compute_baselines(df, c, t)

        _progress(50, "Training ML models", PipelineStatus.TRAINING)
        
        def sanitize_ml_dataframe(df):
            df = df.copy()

            for col in df.columns:
                if df[col].dtype == "object":
                    # intentar convertir a numérico
                    converted = pd.to_numeric(df[col], errors="coerce")

                    # si casi todo se pudo convertir → usar numérico
                    if converted.notna().mean() > 0.8:
                        df[col] = converted
                    else:
                        # si es categórica real → category (solo si usas LightGBM correctamente)
                        df[col] = df[col].astype("category")

            return df

        # 7. Train ML — derive ML model names from factory (not hardcoded)
        log.info("Pipeline: training ML models...")
        factory = ModelFactory(cfg.models)
        ml_models = factory.build_ml()
        ml_skus: set = set()
        for mn in factory.ml_names():
            ml_skus.update(router.skus_for_model(routing, mn))
        df_ml_f = df_ml[df_ml[c.group].astype(str).isin(ml_skus)] if c.group and ml_skus else df_ml

        # 🔧 FIX CRÍTICO: sanitizar features antes de ML
        df_ml_f = sanitize_ml_dataframe(df_ml_f)
        trainer = Trainer(
            t.train_ratio, t.walk_forward, t.wfv_splits,
            tuning=t.tuning, tuning_trials=t.tuning_trials,
        )
        results_ml = trainer.train(
            df_ml_f, ml_models,
            group_cols=group_cols,
            target=c.target, dt=c.date,
        ) if ml_models else {}

        # 7b. Quantile ML models — train p10/p50/p90 regressors and attach to results
        if ml_models:
            log.info("Pipeline: training quantile ML models (p10/p50/p90)...")
            for q_level, key_suffix in [(0.1, "p10"), (0.5, "p50"), (0.9, "p90")]:
                try:
                    q_models = factory.build_quantile_ml(q_level)
                    q_results = trainer.train(
                        df_ml_f, q_models,
                        group_cols=group_cols,
                        target=c.target, dt=c.date,
                    )
                    for res_key, q_res in q_results.items():
                        if res_key in results_ml:
                            results_ml[res_key][f"fitted_model_{key_suffix}"] = q_res.get("fitted_model")
                except Exception as e:
                    log.warning(f"Pipeline: quantile {key_suffix} training failed: {e}")

        _progress(60, "Training statistical models", PipelineStatus.TRAINING)

        # 8. Statistical models — tracked via PartialResultCollector
        results_stat: dict = {}
        collector = PartialResultCollector(fail_fast_threshold=1.0)

        for model_name, run_fn in [
            ("arima",   run_arima_core),
            ("prophet", run_prophet_core),
            ("ets",     run_ets_core),
            ("croston", run_croston_core),
            ("lstm",    run_lstm_core),
        ]:
            skus = router.skus_for_model(routing, model_name)
            if not skus:
                continue
            sub = df[df[c.group].astype(str).isin(skus)] if c.group else df
            log.info(f"Pipeline: running {model_name} on {len(skus)} SKUs...")
            try:
                result = run_fn(
                    sub, c.date, c.target, c.group,
                    t.train_ratio, t.min_history, t.seasonal_period,
                    horizon=h,
                )
                results_stat[model_name] = result
                for sku in skus:
                    if str(sku) in result:
                        r = result[str(sku)]
                        collector.record_success(str(sku), model_name,
                                                 r if isinstance(r, dict) else {"mae": r})
                    else:
                        collector.record_failure(str(sku), model_name,
                                                 RuntimeError("skipped by model"))
            except Exception as e:
                log.warning(f"Pipeline: {model_name} failed entirely: {e}")
                for sku in skus:
                    collector.record_failure(str(sku), model_name, e)

        # SARIMAX — only when exogenous columns are configured
        sarimax_skus = router.skus_for_model(routing, "sarimax")
        if sarimax_skus and "sarimax" in cfg.models:
            sub = df[df[c.group].astype(str).isin(sarimax_skus)] if c.group else df
            exog_cols = [col for col in c.exogenous if col in df.columns]
            sarimax_hp = cfg.models.get("sarimax", {})
            log.info(f"Pipeline: running sarimax on {len(sarimax_skus)} SKUs "
                     f"(exog={exog_cols})...")
            try:
                results_stat["sarimax"] = run_sarimax_core(
                    sub, c.date, c.target, c.group,
                    t.train_ratio, t.min_history, t.seasonal_period,
                    exog_cols=exog_cols,
                    order=sarimax_hp.get("order", (1, 1, 1)),
                    seasonal_order=sarimax_hp.get("seasonal_order", None),
                    horizon=h,
                )
            except Exception as e:
                log.warning(f"Pipeline: sarimax failed entirely: {e}")

        stat_summary = collector.summary()
        log.info(
            f"Pipeline: stat models — {stat_summary['succeeded']} SKU-model pairs succeeded, "
            f"{stat_summary['failed']} failed"
        )
        if stat_summary["failed_skus"]:
            log.warning(f"  Failed SKUs: {stat_summary['failed_skus'][:10]}")

        _progress(75, "Building ensemble", PipelineStatus.TRAINING)

        # 9. Ensemble
        sku_model_mae: dict = {}
        for key, res in results_ml.items():
            sku, model = res.get("sku"), res.get("model")
            if sku and model:
                sku_model_mae.setdefault(sku, {})[model] = res.get("mae", float("inf"))
        ensemble = WeightedEnsemble()
        if sku_model_mae:
            ensemble.fit(sku_model_mae)

        # 10. Flatten evaluation metrics
        metrics_df = self._flatten(results_ml, results_stat, baselines)

        _progress(85, "Generating forecasts", PipelineStatus.FORECASTING)

        # 11. Generate future forecasts via inference module (+ ensemble row per SKU)
        forecast_df = self._generate_forecast_df(results_ml, results_stat, df, ensemble=ensemble)

        _progress(92, "Computing inventory recommendations", PipelineStatus.INVENTORY)

        # 12. Inventory recommendations — use real forecast arrays, not historical mean
        inventory_df = self._inventory(df, c, b, h, forecast_df=forecast_df, metrics_df=metrics_df)

        # 13. Registry
        registry = ModelRegistry(path=cfg.data.registry_path)
        run_id = registry.log_run(
            session_name=cfg.name,
            config_hash=cfg.hash,
            results={k: {m: v for m, v in r.items() if isinstance(v, (int, float))}
                     for k, r in results_ml.items()},
            metadata={"n_skus": df[c.group].nunique() if c.group else 1, "n_rows": len(df)},
        )
        log.info(f"Pipeline: run logged → {run_id}")
        _progress(100, f"Done — run {run_id}", PipelineStatus.DONE)

        results = PipelineResults(
            metrics_df=metrics_df,
            forecast_df=forecast_df,
            inventory_df=inventory_df,
            quality_df=quality_df,
            run_id=run_id,
            config_hash=cfg.hash,
            metadata={"n_skus": df[c.group].nunique() if c.group else 1},
            fitted_models=results_ml,
            stat_forecasts=results_stat,
        )

        if forecast_df is not None and "store" in forecast_df.columns:
            results.forecast_by_sku_df   = aggregate_by_sku(forecast_df)
            results.forecast_by_store_df = aggregate_by_store(forecast_df)

        return results

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _run_validation(self, df: pd.DataFrame, val_cfg: dict):
        """Run validation layers 2-4 in WARNING mode; log issues, never abort."""
        from forecasting_core.validation import (
            validate_semantic, validate_data, detect_leakage,
            check_model_compatibility, ValidationMode,
        )
        mode = ValidationMode.WARNING

        sem = validate_semantic(val_cfg, df, mode)
        for w in sem.warnings:
            log.warning(f"[semantic] {w}")
        for e in sem.errors:
            log.warning(f"[semantic] {e}")

        dv = validate_data(df, val_cfg, mode)
        for w in dv.warnings:
            log.warning(f"[data] {w}")
        for e in dv.errors:
            log.warning(f"[data] {e}")

        leak = detect_leakage(df, val_cfg, mode=mode)
        for w in leak.warnings:
            log.warning(f"[leakage] {w}")
        for e in leak.errors:
            log.warning(f"[leakage] {e}")

        compat = check_model_compatibility(df, val_cfg, mode)
        for w in compat.warnings:
            log.warning(f"[compat] {w}")

    # ------------------------------------------------------------------
    # Forecast generation
    # ------------------------------------------------------------------

    def _generate_forecast_df(
        self,
        results_ml: dict,
        results_stat: dict,
        df: pd.DataFrame,
        ensemble=None,
    ) -> Optional[pd.DataFrame]:
        """
        Delegate forecast generation to the inference module.

        Produces one row per (sku, model, step) plus an "ensemble" row per SKU
        when a fitted WeightedEnsemble is provided and multiple models competed.
        Each row carries quantile columns (q10, q50, q90, …) derived from
        config.forecast.quantiles, plus backward-compat lower/upper columns.
        """
        from forecasting_core.inference.predictor import predict_all_skus

        fc_dict = predict_all_skus(
            fitted_models=results_ml,
            stat_forecasts=results_stat,
            raw_df=df,
            config=self.config,
        )

        if not fc_dict:
            return None

        # Detect quantile column names from the first available point
        q_keys: List[str] = []
        for model_dict in fc_dict.values():
            for pts in model_dict.values():
                if pts:
                    q_keys = sorted(
                        [k for k in pts[0] if k.startswith("q") and k[1:].isdigit()],
                        key=lambda k: int(k[1:]),
                    )
                    break
            if q_keys:
                break

        rows = []
        for sku, model_dict in fc_dict.items():
            for model_name, pts in model_dict.items():
                for i, pt in enumerate(pts):
                    row = {
                        "sku":      sku,
                        "model":    model_name,
                        "date":     pd.Timestamp(pt["date"]),
                        "forecast": pt["value"],
                        "lower":    pt.get("lower"),
                        "upper":    pt.get("upper"),
                        "p10":      pt.get("p10"),
                        "p50":      pt.get("p50"),
                        "p90":      pt.get("p90"),
                        "step":     i + 1,
                    }
                    for q_key in q_keys:
                        row[q_key] = pt.get(q_key)
                    rows.append(row)

        # Ensemble rows — weighted combination of per-model forecasts
        if ensemble is not None and getattr(ensemble, "_weights", {}):
            for sku, model_dict in fc_dict.items():
                if len(model_dict) < 2:
                    continue  # ensemble only meaningful with multiple models
                ref_pts = next(iter(model_dict.values()))
                n_steps = min(len(pts) for pts in model_dict.values())

                # Value arrays per model (shape: n_steps each)
                val_arrays = {
                    mn: np.array([pts[i]["value"] for i in range(n_steps)])
                    for mn, pts in model_dict.items()
                }
                try:
                    ens_values = ensemble.predict(sku, val_arrays)
                except Exception as e:
                    log.warning(f"Ensemble predict failed for {sku}: {e}")
                    continue

                # Quantile arrays per model per q_key
                ens_q: Dict[str, np.ndarray] = {}
                for q_key in q_keys:
                    q_arrays = {
                        mn: np.array([pts[i].get(q_key, pts[i]["value"]) for i in range(n_steps)])
                        for mn, pts in model_dict.items()
                    }
                    try:
                        ens_q[q_key] = ensemble.predict(sku, q_arrays)
                    except Exception:
                        pass

                for i in range(n_steps):
                    val = round(max(0.0, float(ens_values[i])), 4)
                    row = {
                        "sku":      sku,
                        "model":    "ensemble",
                        "date":     pd.Timestamp(ref_pts[i]["date"]),
                        "forecast": val,
                        "step":     i + 1,
                    }
                    for q_key in q_keys:
                        if q_key in ens_q:
                            row[q_key] = round(max(0.0, float(ens_q[q_key][i])), 4)
                        else:
                            row[q_key] = None
                    row["lower"] = row.get(q_keys[0]) if q_keys else None
                    row["upper"] = row.get(q_keys[-1]) if q_keys else None
                    rows.append(row)

        return pd.DataFrame(rows) if rows else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_baselines(self, df, c, t):
        from forecasting_core.evaluation.baselines import BaselineEvaluator
        results = {}
        src = df.groupby(c.group) if c.group else [(None, df)]
        for sku, g in src:
            g = g.sort_values(c.date)
            series = g[c.target].astype(float).values
            cut = int(len(series) * t.train_ratio)
            if cut < 2 or cut >= len(series):
                continue
            results[str(sku) if sku else "__all__"] = BaselineEvaluator.evaluate_baselines(
                series[:cut], series[cut:], period=t.seasonal_period
            )
        return results

    def _flatten(self, results_ml, results_stat, baselines) -> pd.DataFrame:
        rows = []
        for key, res in results_ml.items():
            rows.append({
                "model": res.get("model", key), "type": "ml",
                "sku": res.get("sku", key), "mae": res.get("mae"),
                "rmse": res.get("rmse"), "wape": res.get("wape"),
                "bias": res.get("bias"), "mape": res.get("mape"),
                "smape": res.get("smape"), "n_folds": res.get("n_folds"),
                "validation": res.get("validation"),
            })
        for model_name, res_dict in results_stat.items():
            model_type = "dl" if model_name == "lstm" else "stat"
            for sku, res in res_dict.items():
                if isinstance(res, dict):
                    rows.append({
                        "model": model_name, "type": model_type, "sku": sku,
                        "mae": res.get("mae"), "rmse": res.get("rmse"),
                        "wape": res.get("wape"), "bias": res.get("bias"),
                        "mape": res.get("mape"), "smape": res.get("smape"),
                    })
                else:
                    rows.append({"model": model_name, "type": model_type, "sku": sku, "mae": float(res)})
        for sku, blines in baselines.items():
            for bname, bm in blines.items():
                rows.append({"model": bname, "type": "baseline", "sku": sku, **bm})
        return pd.DataFrame(rows)

    def _inventory(
        self,
        df: pd.DataFrame,
        c,
        b,
        horizon: int,
        forecast_df: Optional[pd.DataFrame] = None,
        metrics_df: Optional[pd.DataFrame] = None,
    ) -> Optional[pd.DataFrame]:
        from forecasting_core.business.inventory import InventoryAdvisor

        advisor = InventoryAdvisor(
            b.service_level,
            b.lead_time_days,
            b.holding_cost_pct,
            b.stockout_cost_multiplier,
        )

        fc_arrays: Dict[str, np.ndarray] = {}

        # -----------------------------
        # 🔧 NORMALIZE SKU FUNCTION
        # -----------------------------
        def norm_sku(x):
            if pd.isna(x):
                return None
            return str(x).strip()

        # -----------------------------
        # USE MODEL FORECASTS
        # -----------------------------
        if forecast_df is not None and not forecast_df.empty and "forecast" in forecast_df.columns:

            best_model_per_sku: Dict[str, str] = {}

            if metrics_df is not None and not metrics_df.empty and "mae" in metrics_df.columns:
                for sku_val, grp in metrics_df.groupby("sku"):
                    valid = grp.dropna(subset=["mae"])
                    if not valid.empty:
                        best_model_per_sku[norm_sku(sku_val)] = valid.loc[valid["mae"].idxmin(), "model"]

            for sku_val, sku_fc in forecast_df.groupby("sku"):
                sku = norm_sku(sku_val)
                if sku is None:
                    continue

                best = best_model_per_sku.get(sku)

                if best:
                    rows = sku_fc[sku_fc["model"] == best]
                    # ❗ NO fallback silencioso global
                    if rows.empty:
                        continue
                else:
                    rows = sku_fc

                arr = (
                    rows.sort_values("step")["forecast"]
                    .astype(float)
                    .to_numpy()
                )

                if len(arr) == 0:
                    continue

                if len(arr) < horizon:
                    arr = np.pad(arr, (0, horizon - len(arr)), constant_values=arr[-1])

                fc_arrays[sku] = np.clip(arr, 0.0, None)

        # -----------------------------
        # FALLBACK SAFE (PER SKU ONLY)
        # -----------------------------
        if not fc_arrays:
            if c.group and c.group in df.columns:

                for sku_val, g in df.groupby(c.group):
                    sku = norm_sku(sku_val)

                    g_clean = pd.to_numeric(g[c.target], errors="coerce")
                    if g_clean.dropna().empty:
                        continue

                    mean_demand = float(g_clean.mean())

                    fc_arrays[sku] = np.full(
                        horizon,
                        max(mean_demand, 0.0)
                    )

            else:
                g_clean = pd.to_numeric(df[c.target], errors="coerce")
                if g_clean.dropna().empty:
                    return None

                mean_demand = float(g_clean.mean())
                fc_arrays["__global__"] = np.full(horizon, max(mean_demand, 0.0))

        # -----------------------------
        # FINAL VALIDATION
        # -----------------------------
        if not fc_arrays:
            return None

        recs = advisor.batch_recommend(fc_arrays)
        return advisor.summary_df(recs)

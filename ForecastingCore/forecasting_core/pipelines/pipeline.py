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
from forecasting_core.evaluation.metrics import CHAMPION_METRIC_ORDER

log = logging.getLogger(__name__)

# norm.ppf(0.9). The engine writes q90 = value + 1.2816 * residual_std (see
# inference/predictor.py), so dividing the q90 spread by this recovers the
# residual sigma exactly — the same constant backend/inventory/service.py
# ::_point_sigma uses, so both layers agree on what "sigma" means.
_Q90_Z = 1.2816


def _primary_group(c) -> "Optional[str]":
    """Return the first group key, or None if group_keys is empty."""
    return c.group_keys[0] if c.group_keys else None


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
    # {sku: cumulative demand-uncertainty bands} — see Pipeline._demand_risk.
    demand_risk:          dict = field(default_factory=dict)
    # Purchasing outcome the forecast would have produced — see _policy_backtest.
    policy_backtest:      dict = field(default_factory=dict)
    run_id:               str = ""
    config_hash:          str = ""
    metadata:             dict = field(default_factory=dict)
    fitted_models:        dict = field(default_factory=dict)   # {key: ML trainer result}
    stat_forecasts:       dict = field(default_factory=dict)   # {model: {sku: {forecast, residuals}}}


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
        # SKUs where a naive baseline scored better than every real model. Set
        # by _select_champions; carried out in the run metadata rather than left
        # in a log line nobody reads.
        self._outperformed_by_baseline: List[dict] = []
        # SKUs that finished the run with no inventory recommendation. See
        # _inventory: on screen this is indistinguishable from "well stocked".
        self._skipped_no_forecast: List[dict] = []
        # {sku: champion model}, set by _select_champions. _demand_risk needs it
        # so the ONE band it publishes per SKU belongs to the model the purchase
        # is actually computed from.
        self._champion_by_sku: Dict[str, str] = {}

    def _maybe_resample(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate to a common frequency when the session chose Estrategia B.
        No-op (returns df unchanged) for the default "native" strategy.
        """
        g = self.config.granularity
        if g.strategy != "aggregate" or not g.target_freq:
            return df
        from forecasting_core.data.resampler import resample_to_frequency
        c = self.config.columns
        # NOTE: only the PRIMARY group key is used here. For a multi-key config
        # (e.g. group_keys=["sku", "store"] or, once multi-warehouse lands,
        # ["sku", "bodega"]), this sums demand ACROSS the secondary keys —
        # correct only while every group_keys config in this codebase is
        # single-key. Extend resample_to_frequency to group by all of
        # group_keys before wiring multi-warehouse data through Estrategia B.
        group_col = _primary_group(c)
        # Inventory has to survive the resample or censored-demand recovery
        # silently switches off for every Estrategia B session. `min` is the
        # right aggregation: a week in which stock touched zero on any day is a
        # week in which demand was truncated, and `last` would miss it whenever
        # the shelf was restocked before the week closed.
        inventory_col = getattr(c, "inventory", "") or ""
        return resample_to_frequency(
            df, c.date, group_col, c.target, g.target_freq,
            extra_cols={inventory_col: "min"} if inventory_col else None,
        )

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
        from forecasting_core.ensemble.ensemble import WeightedEnsemble
        from forecasting_core.registry.registry import ModelRegistry
        from forecasting_core.validation.auto_correct import auto_correct_data
        from forecasting_core.validation.pipeline_resilience import PartialResultCollector

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
            [_primary_group(c), c.date] if _primary_group(c) else [c.date]
        ).reset_index(drop=True)

        df = self._maybe_resample(df)

        # Resolve group_cols: use configured group_keys filtered to columns
        # actually present in the DataFrame so that multi-key configs (e.g.
        # ["sku", "store"]) degrade gracefully on single-key datasets.
        group_cols: List[str] = [k for k in c.group_keys if k in df.columns]

        _progress(10, "Validating data", PipelineStatus.VALIDATING)

        # 2. Validation layers (WARNING mode — never abort, but the findings
        # travel with the results so the UI can show them.)
        log.info("Pipeline: running validation layers...")
        validation_findings = self._run_validation(df, val_cfg)

        # 2b. Auto-correct: fill NaN gaps, clip outliers per SKU
        df, correction_log = auto_correct_data(df, val_cfg, clip_outliers=True)
        for entry in correction_log.to_list():
            log.info(f"[auto_correct] {entry['action']}: {entry['description']}")

        # 2c. Censored demand: a stockout day records what could be sold, not
        # what was wanted. Runs BEFORE quality checks and feature engineering so
        # every later step sees demand, not availability. No-op unless the user
        # mapped an inventory column (see data/censoring.py).
        from forecasting_core.data.censoring import recover_censored_demand
        df, censoring_report = recover_censored_demand(
            df, date_col=c.date, target_col=c.target,
            group_col=_primary_group(c),
            inventory_col=getattr(c, "inventory", "") or "",
        )
        if censoring_report.changed_anything:
            log.info(
                f"[censoring] recovered {censoring_report.n_recovered} stockout "
                f"buckets (+{censoring_report.units_recovered:.0f} units)"
            )

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
        n_valid = df[_primary_group(c)].nunique() if _primary_group(c) else 1
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
        df_ml, unservable = self._drop_unservable_features(df_ml, df, c)
        if unservable:
            validation_findings.append({
                "error_id": "FEATURE_NOT_AVAILABLE_AT_FORECAST_TIME",
                "severity": "warning",
                "layer": "features",
                "message": (
                    "Dropped column(s) that exist in the history but cannot be "
                    f"known for a future date: {', '.join(unservable)}."
                ),
                "context": {"columns": unservable},
                "suggestions": [],
            })

        # 6. Baselines
        baselines = self._compute_baselines(df, c, t)

        _progress(50, "Training ML models", PipelineStatus.TRAINING)
        
        def sanitize_ml_dataframe(df):
            df = df.copy()

            for col in df.columns:
                if df[col].dtype == "object":
                    # try converting to numeric
                    converted = pd.to_numeric(df[col], errors="coerce")

                    # almost everything converted cleanly -> treat as numeric
                    if converted.notna().mean() > 0.8:
                        df[col] = converted
                    else:
                        # genuinely categorical -> category dtype (only correct with LightGBM)
                        df[col] = df[col].astype("category")

            return df

        # 7. Train ML — derive ML model names from factory (not hardcoded)
        log.info("Pipeline: training ML models...")
        factory = ModelFactory(cfg.models)
        ml_models = factory.build_ml()
        ml_skus: set = set()
        for mn in factory.ml_names():
            ml_skus.update(router.skus_for_model(routing, mn))
        df_ml_f = df_ml[df_ml[_primary_group(c)].astype(str).isin(ml_skus)] if _primary_group(c) and ml_skus else df_ml

        # CRITICAL FIX: sanitize features before ML
        df_ml_f = sanitize_ml_dataframe(df_ml_f)
        trainer = Trainer(
            t.train_ratio, t.walk_forward, t.wfv_splits,
            tuning=t.tuning, tuning_trials=t.tuning_trials,
            max_workers=t.max_workers,
            # Score the model on the problem the product actually solves: a
            # forecast for h buckets out cannot have seen the h buckets before
            # it. See WalkForwardSplitter.
            gap=h,
            # …and grade the final model over the whole h-step forecast, on the
            # same protocol the statistical and global models are graded on, so
            # champion selection compares like with like. See
            # Trainer._horizon_metrics.
            horizon=h,
            features_cfg=cfg.features,
        )
        results_ml = trainer.train(
            df_ml_f, ml_models,
            group_cols=group_cols,
            target=c.target, dt=c.date,
        ) if ml_models else {}

        # 7b. There is no separate p10/p50/p90 pass, and that is deliberate.
        #
        # This step used to fit three extra quantile regressors per ML model per
        # SKU. Measured on the demo catalogue: the point models took 15.7s and
        # the three quantile passes 21.5s — 58% of all ML training time, the
        # single most expensive thing the trainer did.
        #
        # Nothing that decides anything used them. Their metrics were discarded
        # by design, and the layer that turns a band into a purchase quantity
        # refused their band: `backend/inventory/service.py::_point_sigma`
        # prefers `q90` and records why — "xgboost's upper spread is 43%
        # narrower than its own honest q90", because a quantile model fitted on
        # the training set and then run recursively is graded on nothing and
        # tightens on its own residuals. So the product was paying the majority
        # of its training budget for a band drawn on a chart while the money was
        # computed from a different one.
        #
        # One band now, the one the decision already used: the empirical
        # quantiles of the out-of-fold residual bank (`_compute_quantile_bounds`
        # in the predictor), with p10/p50/p90 as aliases of q10/q50/q90 so the
        # chart and the reorder point cannot disagree again.

        # 7c. Global cross-learning model — ONE fit over every series at once.
        # Trained on the full feature frame (not `df_ml_f`, which is narrowed to
        # the SKUs routed to the per-SKU ML models): a global model's whole
        # value is the series the routing table would have excluded.
        if factory.global_names():
            _progress(58, "Training the global cross-learning model",
                      PipelineStatus.TRAINING)
            log.info("Pipeline: training global model over all series...")
            try:
                from forecasting_core.training.global_trainer import GlobalTrainer

                # Built with the warm-up rows KEPT. A series shorter than the
                # widest configured lag loses every row to the standard warm-up
                # drop — a 24-bucket SKU against a lag_28 config produces an
                # empty frame — so it never reaches the model that exists to
                # serve it. Measured on a real run: the one newly-launched SKU
                # in a 13-series catalogue was excluded as "no_forecast" while
                # the global model trained happily on the other twelve, which is
                # the exact opposite of the point. LightGBM splits on missing
                # values natively, so the warm-up NaNs cost nothing.
                df_ml_global = engineer.transform(df, drop_warmup=False)
                global_skus = set(router.skus_for_model(routing, "global_lgbm"))
                df_global = (
                    df_ml_global[df_ml_global[_primary_group(c)].astype(str).isin(global_skus)]
                    if _primary_group(c) and global_skus else df_ml_global
                )
                global_results = GlobalTrainer(
                    horizon=h,
                    train_ratio=t.train_ratio,
                    walk_forward=t.walk_forward,
                    wfv_splits=t.wfv_splits,
                    max_workers=t.max_workers,
                    params=cfg.models.get("global_lgbm") or {},
                ).train(
                    sanitize_ml_dataframe(df_global),
                    group_cols=group_cols, target=c.target, dt=c.date,
                )
                log.info(f"Pipeline: global model produced {len(global_results)} series")
                results_ml.update(global_results)
            except Exception as e:
                # A global-model failure must not take the whole run down: every
                # per-SKU model has already trained and is still usable.
                log.warning(f"Pipeline: global model failed: {e}", exc_info=True)

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
            sub = df[df[_primary_group(c)].astype(str).isin(skus)] if _primary_group(c) else df
            log.info(f"Pipeline: running {model_name} on {len(skus)} SKUs...")
            # Prophet is the one runner that takes the holiday calendar. The ML
            # path has had it all along; giving it to Prophet too means both
            # families see the same holidays for the same tenant.
            extra = ({"holiday_country": getattr(cfg.features, "holiday_country", "") or ""}
                     if model_name == "prophet" else {})
            try:
                result = run_fn(
                    sub, c.date, c.target, _primary_group(c),
                    t.train_ratio, t.min_history, t.seasonal_period,
                    horizon=h, **extra,
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
            sub = df[df[_primary_group(c)].astype(str).isin(sarimax_skus)] if _primary_group(c) else df
            exog_cols = [col for col in c.exogenous if col in df.columns]
            sarimax_hp = cfg.models.get("sarimax", {})
            log.info(f"Pipeline: running sarimax on {len(sarimax_skus)} SKUs "
                     f"(exog={exog_cols})...")
            try:
                results_stat["sarimax"] = run_sarimax_core(
                    sub, c.date, c.target, _primary_group(c),
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

        # 9. Flatten evaluation metrics
        metrics_df = self._flatten(results_ml, results_stat, baselines)

        # 10. Ensemble — fitted AFTER the metrics table, from the metrics table.
        # It used to be fitted before it, from `results_ml` alone and on the
        # 1-step `mae`: the statistical models were handed a weight of zero
        # while still being fed into the average, and the weighting metric was
        # one no other layer in the product uses.
        ensemble = WeightedEnsemble()
        ensemble_scores = self._ensemble_scores(metrics_df)
        if ensemble_scores:
            ensemble.fit(ensemble_scores)

        _progress(85, "Generating forecasts", PipelineStatus.FORECASTING)

        # 11. Generate future forecasts via inference module (+ ensemble row per SKU)
        forecast_df = self._generate_forecast_df(results_ml, results_stat, df, ensemble=ensemble)

        _progress(92, "Computing inventory recommendations", PipelineStatus.INVENTORY)

        # 12. Inventory recommendations — use real forecast arrays, not historical mean
        inventory_df = self._inventory(df, c, b, h, forecast_df=forecast_df, metrics_df=metrics_df)

        # 12b. Two outcomes of step 12 that the user has to be told about. They
        # are emitted as ordinary validation findings so they travel the channel
        # that already exists and already has a panel — inventing a second
        # reporting path for them would just be a second thing to forget.
        validation_findings.extend(self._inventory_findings())

        # 13. Registry
        registry = ModelRegistry(path=cfg.data.registry_path)
        run_id = registry.log_run(
            session_name=cfg.name,
            config_hash=cfg.hash,
            results={k: {m: v for m, v in r.items() if isinstance(v, (int, float))}
                     for k, r in results_ml.items()},
            metadata={"n_skus": df[_primary_group(c)].nunique() if _primary_group(c) else 1, "n_rows": len(df)},
        )
        log.info(f"Pipeline: run logged → {run_id}")
        _progress(100, f"Done — run {run_id}", PipelineStatus.DONE)

        results = PipelineResults(
            metrics_df=metrics_df,
            forecast_df=forecast_df,
            inventory_df=inventory_df,
            quality_df=quality_df,
            demand_risk=self._demand_risk(results_ml, cfg.forecast.quantiles),
            policy_backtest=self._policy_backtest(results_ml, b),
            run_id=run_id,
            config_hash=cfg.hash,
            metadata={
                "n_skus": df[_primary_group(c)].nunique() if _primary_group(c) else 1,
                # Carried out of the pipeline so the caller can surface them;
                # see _run_validation for why logging alone was not enough.
                "validation_findings": validation_findings,
                # The censoring report travels with the corrections so the user
                # is told which observations are estimates rather than
                # measurements. Silently rewriting someone's sales figures and
                # only logging it would be the worst version of this feature.
                "corrections": correction_log.to_list() + (
                    [censoring_report.to_dict()]
                    if censoring_report.changed_anything else []
                ),
                "censoring": censoring_report.to_dict(),
                # SKUs no trained model could beat a naive forecast on. Worth
                # telling a distributor about: it is the honest signal that the
                # history for that product carries no pattern worth modelling.
                "outperformed_by_baseline": list(self._outperformed_by_baseline),
                "skipped_no_forecast": list(self._skipped_no_forecast),
            },
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

    def _run_validation(self, df: pd.DataFrame, val_cfg: dict) -> list[dict]:
        """Run validation layers 2-4 in WARNING mode; never abort.

        Returns the findings as plain dicts so the caller can SHOW them. They
        used to be written to the server log only, which meant the user never
        learned that their upload had duplicate rows, an unsorted date column,
        or — worst of all — target leakage, the very thing that produces a
        model with impossibly good accuracy and a nonsense forecast.
        """
        from forecasting_core.validation import (
            validate_semantic, validate_data, detect_leakage,
            check_model_compatibility, ValidationMode,
        )
        mode = ValidationMode.WARNING
        findings: list[dict] = []

        def collect(layer: str, result) -> None:
            for severity, items in (("error", result.errors), ("warning", result.warnings)):
                for item in items:
                    log.warning(f"[{layer}] {item}")
                    # Validation objects expose to_dict(); anything else is
                    # stringified rather than dropped.
                    payload = item.to_dict() if hasattr(item, "to_dict") else {"message": str(item)}
                    findings.append({**payload, "layer": layer, "severity": severity})

        collect("semantic", validate_semantic(val_cfg, df, mode))
        collect("data", validate_data(df, val_cfg, mode))
        collect("leakage", detect_leakage(df, val_cfg, mode=mode))
        collect("compatibility", check_model_compatibility(df, val_cfg, mode))
        return findings

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

        # Build sku→store lookup from ML training results.
        # Trainer already stores "store" in each result dict (set to "Tienda única"
        # for single-group datasets, or the actual store value for multi-key datasets).
        sku_to_store: Dict[str, Optional[str]] = {}
        for res in results_ml.values():
            sku_key = str(res.get("sku", ""))
            store_val = res.get("store")
            if sku_key and store_val is not None:
                sku_to_store[sku_key] = str(store_val)

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
            store = sku_to_store.get(sku)
            for model_name, pts in model_dict.items():
                for i, pt in enumerate(pts):
                    row = {
                        "sku":      sku,
                        "store":    store,
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
                store = sku_to_store.get(sku)
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
                        "store":    store,
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

    # Defined in evaluation/metrics.py because the backend's inventory layer
    # walks the SAME list to answer the same question, and the two drifting
    # apart means the engine plans from one model while the purchase order
    # comes from another. See CHAMPION_METRIC_ORDER for the measurement.
    CHAMPION_METRICS = CHAMPION_METRIC_ORDER

    @staticmethod
    def _ensemble_scores(metrics_df) -> Dict[str, Dict[str, float]]:
        """
        {sku: {model: error}} for the ensemble weights, on the champion metric.

        Same column and same exclusion as `_select_champions`: baselines are a
        floor to beat, not members of an average, and a naive forecast dragging
        on the ensemble would be the same defect as crowning one.
        """
        if metrics_df is None or metrics_df.empty or "sku" not in metrics_df.columns:
            return {}
        metric = next((m for m in Pipeline.CHAMPION_METRICS if m in metrics_df.columns), None)
        if metric is None:
            return {}
        rows = metrics_df[metrics_df["type"] != "baseline"] if "type" in metrics_df.columns else metrics_df
        rows = rows.dropna(subset=[metric, "model"])
        scores: Dict[str, Dict[str, float]] = {}
        for sku_val, model, value in zip(rows["sku"], rows["model"], rows[metric]):
            scores.setdefault(str(sku_val), {})[str(model)] = float(value)
        return scores

    def _select_champions(self, metrics_df, norm_sku) -> Dict[str, str]:
        """
        Pick the model that will drive each SKU's purchase recommendation.

        This used to be `mae.idxmin()`. MAE is symmetric: it scores a forecast
        that is 10 units low exactly as well as one that is 10 units high, so it
        crowned models that split the difference on a business where the two
        mistakes cost different amounts. Ordering by the asymmetric cost picks
        the model that is wrong in the cheaper direction.

        Ranking is by `cost_horizon`: the same asymmetric cost, but measured
        over the whole h-step forecast for EVERY family. The per-SKU ML models
        used to be ranked on a 1-step score — their validation rows carry their
        own true lag features, so each one was a fresh one-step problem — while
        the statistical models forecast their entire test window from the end of
        train and the global model runs a rolling-origin backtest. Mixing those
        in one column was comparing an easy question with a hard one, and it
        handed the ML models a systematic advantage on every SKU. They are now
        all asked the same question (see Trainer._horizon_metrics).

        A second, quieter version of the same bug was in the LENGTH of the
        statistical models' window, not just its origin, and it survived the
        fix above (docs/stability.md #17(d)). `models/ets.py`, `arima.py`,
        `prophet.py`, `croston.py`, `sarimax.py` and `lstm.py` each forecast
        their WHOLE held-out tail — `len(test)` steps — and this method used to
        take that whole-tail `cost` and put it straight into `cost_horizon`
        unchanged. On a 450-row daily series at `train_ratio=0.8` that tail is
        ~90 buckets against the ML models' 30: a strictly harder question, so
        the statistical families were handicapped by construction and lost the
        champion race for a reason that had nothing to do with being worse.
        Each statistical runner now computes its OWN `cost_horizon`, windowed
        to `min(horizon, len(test))`, before its result ever reaches this
        table — the length gap is closed at the source, not patched here. Their
        other metrics (`mae`, `rmse`, `wape`, `bias`, `mape`, `smape`, `cost`)
        deliberately still cover the WHOLE held-out tail: "how wrong is this
        model over everything it was asked to forecast" is a real question and
        nothing else in the product reads those columns as if they were
        windowed — only `cost_horizon` is the comparable one.

        What is still not identical, and is worth stating plainly rather than
        burying:

        * The h-step forecasts are produced at different origins. The ML and
          statistical models are scored from the single final train cutoff; the
          global model averages several rolling origins, so its number rests on
          more evidence and is less at the mercy of one unusual window.
        * A series with less held-out data than the horizon is scored over
          fewer steps than a longer one. The shortfall is visible — in
          `horizon_metrics["by_horizon"]` (and the flattened `horizon_steps`
          column) for the ML/global rows, in each statistical runner's own
          `horizon_steps` for the rest — not hidden, but two SKUs' figures can
          still cover different numbers of steps.
        * `cost` uses the product's standard 3:1 shortfall-to-surplus ratio for
          every tenant, not the tenant's configured stockout multiplier. The
          configured value drives the actual order quantity; here it would only
          have to be threaded through six model runners to change a ranking it
          rarely reorders.
        """
        champions: Dict[str, str] = {}
        self._outperformed_by_baseline = []
        self._champion_by_sku = {}
        if metrics_df is None or metrics_df.empty or "sku" not in metrics_df.columns:
            return champions

        metric = next((m for m in self.CHAMPION_METRICS if m in metrics_df.columns), None)
        if metric is None:
            return champions

        has_type = "type" in metrics_df.columns
        for sku_val, grp in metrics_df.groupby("sku"):
            valid = grp.dropna(subset=[metric])
            if valid.empty:
                continue

            # Baselines are scored so the real models have something to beat;
            # they are not candidates. Buying from a naive forecast because it
            # happened to win a fold is a defect, not a fallback — the same rule
            # `backend/inventory/service.py::best_model_by_sku` already applies,
            # and the two layers disagreeing was itself the bug: a baseline
            # crowned here produced no forecast rows downstream, so `_inventory`
            # dropped the SKU with no recommendation and no trace.
            #
            # The information is not discarded, only kept out of the race: a SKU
            # no real model can beat a naive forecast on is something the
            # business should hear about, and it is recorded for the caller.
            candidates = valid[valid["type"] != "baseline"] if has_type else valid
            if candidates.empty:
                continue

            champion = candidates.loc[candidates[metric].idxmin(), "model"]
            champions[norm_sku(sku_val)] = champion
            # Kept for `_demand_risk`: only the champion's band may survive
            # into the payload, because the champion is the model the purchase
            # is computed from. See that method.
            self._champion_by_sku[str(sku_val)] = str(champion)

            if has_type:
                baselines = valid[valid["type"] == "baseline"]
                if not baselines.empty:
                    best_baseline = baselines[metric].min()
                    if best_baseline < candidates[metric].min():
                        self._outperformed_by_baseline.append({
                            "sku": norm_sku(sku_val),
                            "model": champion,
                            "baseline": baselines.loc[baselines[metric].idxmin(), "model"],
                        })

        if self._outperformed_by_baseline:
            log.warning(
                "%d SKU(s) had no model beat a naive baseline on %s — e.g. %s",
                len(self._outperformed_by_baseline), metric,
                [e["sku"] for e in self._outperformed_by_baseline[:5]],
            )
        return champions

    @staticmethod
    def _drop_unservable_features(df_ml, raw_df, c) -> tuple:
        """
        Remove features the inference path cannot reproduce for a future date.

        A model may only use inputs that exist on both sides of the forecast
        boundary. The engineered features qualify — calendar, Fourier, lags and
        rolling statistics are all computed for future dates by the predictor.
        Columns that merely came along with the upload do not: `inventory`,
        `price`, `cost`, the censoring flag. `recursive_ml_predict` builds each
        future row from the feature NAMES and fills anything it cannot compute
        with `row.get(f, 0.0)`, so every one of them is a real number during
        fitting and a hard zero at serve time.

        Measured before removing them: on a series whose demand is genuinely
        truncated by stock, `inventory` came back with the highest feature
        importance of all — above the lag — while shifting the served forecast
        by only ~2%, because a tree cannot extrapolate below its lowest split
        and the zero simply lands in an average leaf. So the forecast damage is
        mild. The reporting damage is not: that column tops the SHAP list the
        product shows the user as the explanation for a number it never
        influenced.

        Returns (frame, dropped_column_names).
        """
        reserved = {c.date, c.target, *(c.group_keys or [])}
        generated = [col for col in df_ml.columns if col not in raw_df.columns]
        passthrough = [
            col for col in df_ml.columns
            if col in raw_df.columns
            and col not in reserved
            and col not in generated
            and pd.api.types.is_numeric_dtype(df_ml[col])
        ]
        if not passthrough:
            return df_ml, []

        log.warning(
            "Dropping %d feature column(s) the forecast cannot supply for a "
            "future date: %s. They are constant-filled at inference, so keeping "
            "them only adds a feature the model leans on and the forecast never "
            "receives.",
            len(passthrough), passthrough,
        )

        # Everything above is dropped. Only some of it is worth TELLING the user
        # about, and getting that wrong is its own defect: the first version of
        # this warning fired on every single session and named `inventory`,
        # `lead_time`, `promo` and `discount` — columns the canonical schema
        # broadcasts as constants into files that never contained them. "We
        # dropped your inventory column" is a confusing thing to read when you
        # never uploaded one.
        #
        # A column is worth naming only if it actually varies (a broadcast
        # default does not) and is not the target under another name (the
        # canonical alias, which the Trainer's leakage guard would have removed
        # anyway).
        #
        # The alias is skipped by NAME as well as by value, because the two stop
        # matching. `apply_canonical_defaults` copies the mapped column into
        # `demand` before the backend collapses same-day rows, and the collapse
        # aggregates the target only — so a file with two rows for one day left
        # `demand` holding one of them and the target holding their sum. On
        # measured data that produced the user-visible warning "Dropped
        # column(s) ... : demand", naming a column nobody uploaded, about a
        # divergence the pipeline created itself.
        target_alias = "demand" if c.target != "demand" else None
        target_values = df_ml[c.target] if c.target in df_ml.columns else None
        reportable = []
        for col in passthrough:
            series = df_ml[col]
            if col == target_alias:
                continue
            if series.nunique(dropna=True) <= 1:
                continue
            if target_values is not None and series.astype(float).equals(
                    target_values.astype(float)):
                continue
            reportable.append(col)

        return df_ml.drop(columns=passthrough), reportable

    def _inventory_findings(self) -> List[dict]:
        """
        Turn the two silent outcomes of inventory generation into findings.

        `NO_MODEL_BEAT_BASELINE` — every model trained for this SKU scored worse
        than simply repeating the last value. That is not a crash and the SKU
        still gets a recommendation, but it says the history carries no pattern
        worth modelling, and a distributor deciding how much to trust a number
        deserves to know which numbers those are.

        `SKU_WITHOUT_RECOMMENDATION` — the SKU finished the run with nothing.
        This is the one that must never be quiet: on the semáforo, a product
        with no recommendation looks exactly like a product that is well
        stocked, so the failure mode is the user not buying something they
        needed to buy.
        """
        findings: List[dict] = []
        for entry in self._outperformed_by_baseline:
            findings.append({
                "error_id": "NO_MODEL_BEAT_BASELINE",
                "severity": "warning",
                "layer": "inventory",
                "message": (
                    f"No trained model beat the {entry['baseline']} baseline for "
                    f"SKU {entry['sku']}; planning uses {entry['model']}."
                ),
                "context": entry,
                "suggestions": [],
            })
        for entry in self._skipped_no_forecast:
            findings.append({
                "error_id": "SKU_WITHOUT_RECOMMENDATION",
                "severity": "error",
                "layer": "inventory",
                "message": (
                    f"SKU {entry['sku']} got no purchase recommendation: its "
                    f"selected model ({entry['model']}) produced no forecast."
                ),
                "context": entry,
                "suggestions": [],
            })
        return findings

    def _policy_backtest(self, results_ml: dict, business) -> dict:
        """
        Replay the purchasing decision the forecast would have driven.

        This is the number a distributor can actually check against their own
        experience: how much of demand was served, how many stockouts, how much
        stock sat in the warehouse. Accuracy metrics cannot express it — a
        forecast biased 25% high and one biased 25% low post the same WAPE and
        produce opposite businesses.

        Scoped to the series whose model ran a rolling-origin backtest, because
        those are the only ones for which a past forecast and the actuals that
        followed it both exist. Series without one are absent from the result
        rather than guessed at, and `n_series` in the payload says how many were
        covered so the headline can never be read as catalogue-wide when it is
        not.
        """
        from forecasting_core.business.policy_backtest import (
            PolicyComparison, aggregate, backtest_policy,
        )

        lead_time = max(1, int(getattr(business, "lead_time_days", 7) or 7))
        service_level = float(getattr(business, "service_level", 0.95) or 0.95)

        comparisons: List[PolicyComparison] = []
        per_sku: Dict[str, dict] = {}

        for entry in results_ml.values():
            records = entry.get("backtest_records") or []
            if not records:
                continue
            # Origins are appended fold by fold; the last is the most recent and
            # the most representative of what the model would do now.
            record = records[-1]
            actual = np.asarray(record.get("actual", []), dtype=float)
            predicted = np.asarray(record.get("pred", []), dtype=float)
            if actual.size < 2 or predicted.size < 2:
                continue

            history = np.concatenate([r["actual"] for r in records[:-1]]) \
                if len(records) > 1 else actual[:1]
            # The cushion the policy would really have used, from the same
            # measured bands the reorder point uses in production.
            forecaster = entry.get("direct_forecaster")
            safety = 0.0
            if forecaster is not None:
                from forecasting_core.evaluation.conformal import horizon_bands
                bands = horizon_bands(
                    forecaster.cumulative_residuals_by_horizon, [service_level],
                )
                key = min(len(actual), max(bands) if bands else 1)
                band = bands.get(key, {})
                if band:
                    safety = max(0.0, float(list(band.values())[0])
                                 * float(forecaster.profile.scale))

            try:
                comparison = backtest_policy(
                    demand=actual, forecast=predicted, history=history,
                    lead_time=min(lead_time, max(1, len(actual) - 1)),
                    safety_stock=safety, model_name=str(entry.get("model", "")),
                )
            except Exception as exc:
                log.warning(f"Policy backtest failed for {entry.get('sku')}: {exc}")
                continue

            comparisons.append(comparison)
            per_sku[str(entry.get("sku"))] = comparison.to_dict()

        if not comparisons:
            return {}
        return {"summary": aggregate(comparisons), "by_sku": per_sku}

    def _demand_risk(self, results_ml: dict, quantiles) -> dict:
        """
        Per-SKU uncertainty of CUMULATIVE demand, which is what a reorder point
        is actually exposed to.

        A safety stock covers the demand that accumulates while the order is in
        transit, so the quantity to bound is the SUM over the lead time, not any
        single bucket. The textbook `z * sigma_daily * sqrt(L)` is one way to
        approximate that sum's quantile, and it assumes the per-bucket errors
        are normal and independent. They are neither — a forecast that is high
        today is usually high tomorrow — so `sqrt(L)` understates the risk on
        exactly the SKUs with the most persistent bias.

        The rolling-origin backtest measured the cumulative error directly, so
        the honest number is available and gets published here as an offset in
        UNITS, per lead time L and per quantile:

            demand over L buckets at quantile q
                = sum(point forecast over L) + offsets[L][q]

        Two sources can supply this, and both produce the same shape so the
        consumer (`backend/inventory/service.py::_measured_safety_stock`)
        needs no new vocabulary to read either:

          * the global model's own rolling-origin backtest
            (`GlobalDirectForecaster.cumulative_residuals_by_horizon`);
          * the per-SKU `Trainer`'s pooled bank — built from the SAME
            walk-forward folds every per-SKU champion is already scored on
            (see `Trainer._bank_fold_cumulative_residuals`), pooled across the
            whole catalogue in scaled units, the same way GlobalTrainer pools
            across every series it fits.

        A SKU whose champion has neither — no folds ran, or the run's pooled
        bank could not fund `MIN_RESIDUALS_PER_HORIZON` at any horizon — is
        absent from the result and the consumer keeps its classical formula
        rather than being handed a quantile estimated from a handful of
        points.
        """
        from forecasting_core.evaluation.conformal import (
            MIN_RESIDUALS_PER_HORIZON, enforce_horizon_monotonic,
            enforce_monotonic, horizon_bands,
        )

        levels = [float(q) for q in (quantiles or [0.5, 0.9, 0.95])]
        risk: dict = {}
        for entry in results_ml.values():
            forecaster = entry.get("direct_forecaster")
            if forecaster is not None:
                cumulative = getattr(forecaster, "cumulative_residuals_by_horizon", None)
                scale = float(getattr(forecaster.profile, "scale", 1.0))
            else:
                # The per-SKU Trainer's pooled bank. Unlike the global model's
                # own backtest it has no per-series fallback to borrow from
                # when a horizon is thin (cumulative residuals must not pool
                # across HORIZONS — see conformal.py), so a horizon under the
                # floor is dropped here instead of being quantile-d from a
                # handful of points into a confident-looking number.
                cumulative = entry.get("cumulative_residuals_by_horizon")
                scale = entry.get("series_scale")
                if cumulative:
                    cumulative = {
                        h: v for h, v in cumulative.items()
                        if len(v) >= MIN_RESIDUALS_PER_HORIZON
                    }
            if not cumulative or scale is None:
                continue
            scale = float(scale)
            # Cumulative residuals must NOT be pooled across horizons — their
            # scale grows with the horizon by construction. The structure is
            # restored afterwards instead, which is where it belongs.
            bands = enforce_horizon_monotonic(
                horizon_bands(cumulative, levels, pool_across_horizons=False)
            )
            offsets = {
                str(h): {
                    str(q): round(float(offset) * scale, 4)
                    for q, offset in enforce_monotonic(band).items()
                }
                for h, band in sorted(bands.items())
            }
            if not offsets:
                continue

            # ONE band per SKU, and it must be the CHAMPION's.
            #
            # This dict is keyed by SKU while `results_ml` holds one entry per
            # (model, SKU), so several entries compete to write the same key
            # and, before this, the last writer won. The global model is added
            # with `results_ml.update(global_results)` AFTER the per-SKU ones,
            # so it always won — measured on the demo catalogue, all ten SKUs
            # came out labelled `global_lgbm` even where the champion was
            # xgboost.
            #
            # That is not a cosmetic mislabel. `backend/inventory/service.py`
            # drops a band whose model is not the SKU's champion, so every
            # per-SKU champion silently fell back to `z*sigma*sqrt(L)` — the
            # classical cushion this whole exercise exists to replace (see
            # stability.md 17b: it delivers 69-84% against a promised 95%).
            sku_key = str(entry.get("sku"))
            champion = self._champion_by_sku.get(sku_key)
            if champion is not None and str(entry.get("model")) != champion:
                continue
            # No champion known yet (a caller that reaches here before
            # `_select_champions` has run) keeps the old last-writer-wins
            # behaviour rather than emitting nothing.
            risk[sku_key] = {
                "model": entry.get("model"),
                "quantiles": levels,
                "cumulative_offsets": offsets,
            }
        return risk

    def _compute_baselines(self, df, c, t):
        from forecasting_core.evaluation.baselines import BaselineEvaluator
        results = {}
        src = df.groupby(_primary_group(c)) if _primary_group(c) else [(None, df)]
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

    @staticmethod
    def _horizon_cost(res: dict):
        """
        The `cost` measured over the whole h-step forecast, or None.

        `mae`/`rmse`/`wape`/`cost` on an ML or global row are 1-step numbers, by
        construction (see Trainer._horizon_metrics). The h-step figure lives
        under `horizon_metrics` and is the only one that answers the same
        question the statistical models were asked.
        """
        hm = res.get("horizon_metrics") or {}
        return (hm.get("all_horizons") or {}).get("cost")

    @staticmethod
    def _horizon_steps(res: dict):
        """
        How many steps `_horizon_cost` was actually computed over, or None.

        `by_horizon` already carries exactly one entry per step the trainer
        could fund (see `Trainer._horizon_metrics`'s docstring on short
        series), so this is its length, not a re-derivation — the visible
        counterpart to `cost_horizon` for ML/global rows, mirroring the
        `horizon_steps` each statistical runner now reports for itself.
        """
        hm = res.get("horizon_metrics") or {}
        by_h = hm.get("by_horizon")
        return len(by_h) if isinstance(by_h, dict) else None

    def _flatten(self, results_ml, results_stat, baselines) -> pd.DataFrame:
        rows = []
        for key, res in results_ml.items():
            model_name = res.get("model", key)
            rows.append({
                "model": model_name,
                "type": "global" if model_name == "global_lgbm" else "ml",
                "sku": res.get("sku", key), "mae": res.get("mae"),
                "rmse": res.get("rmse"), "wape": res.get("wape"),
                "bias": res.get("bias"), "mape": res.get("mape"),
                "smape": res.get("smape"), "cost": res.get("cost"),
                "cost_horizon": self._horizon_cost(res),
                "horizon_steps": self._horizon_steps(res),
                "n_folds": res.get("n_folds"),
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
                        "cost": res.get("cost"),
                        # Each statistical runner (models/ets.py, arima.py,
                        # prophet.py, croston.py, sarimax.py, lstm.py) now
                        # computes its OWN `cost_horizon`, windowed to
                        # `min(horizon, len(test))` so it answers the same
                        # h-step question as the ML/global rows above rather
                        # than the whole held-out tail `cost` does
                        # (docs/stability.md #17(d)). A result produced before
                        # that existed — a legacy persisted session, or a raw
                        # dict handed to this method by a test — carries no
                        # `cost_horizon` key, and falling back to `cost` there
                        # reproduces the OLD (whole-tail, and on a long series
                        # too easy for the h-step race) behaviour for that
                        # data rather than inventing a number that was never
                        # computed under the new protocol.
                        "cost_horizon": res.get("cost_horizon", res.get("cost")),
                        "horizon_steps": res.get("horizon_steps"),
                    })
                else:
                    rows.append({"model": model_name, "type": model_type, "sku": sku, "mae": float(res)})
        for sku, blines in baselines.items():
            for bname, bm in blines.items():
                # Baselines are still evaluated over the entire test window in
                # one shot (BaselineEvaluator.evaluate_baselines is not
                # windowed) and are not part of this fix's scope: a baseline
                # is never a champion candidate (`_select_champions` excludes
                # `type == "baseline"`), it is only compared against the
                # candidates' minimum cost to flag when none of them beat it.
                # That comparison is still a length mismatch after this
                # change — noted here rather than silently left implied.
                rows.append({"model": bname, "type": "baseline", "sku": sku,
                             **bm, "cost_horizon": bm.get("cost")})
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
        # {sku: sigma}, sigma = one standard deviation of the forecast ERROR,
        # recovered from the q90 band the same way backend/inventory/service.py
        # ::_point_sigma does, so the engine and the backend agree on what
        # "sigma" means. A SKU with no band is left out of this dict, which
        # sends InventoryAdvisor.recommend down its own (poorer) fallback —
        # see the comment there.
        std_by_sku: Dict[str, float] = {}
        # SKUs whose champion produced no forecast rows, so they end the run with
        # no recommendation. Carried out of here because on screen "no
        # recommendation" and "well stocked" look identical.
        skipped_no_forecast: List[dict] = []

        def norm_sku(x):
            if pd.isna(x):
                return None
            return str(x).strip()

        # -----------------------------
        # USE MODEL FORECASTS
        # -----------------------------
        if forecast_df is not None and not forecast_df.empty and "forecast" in forecast_df.columns:

            best_model_per_sku = self._select_champions(metrics_df, norm_sku)

            for sku_val, sku_fc in forecast_df.groupby("sku"):
                sku = norm_sku(sku_val)
                if sku is None:
                    continue

                best = best_model_per_sku.get(sku)

                if best:
                    rows = sku_fc[sku_fc["model"] == best]
                    # Deliberately NO silent global fallback: pooling every
                    # model's forecast here would answer with a number that
                    # belongs to no model. But dropping the SKU must not be
                    # silent either — it leaves the product with no
                    # recommendation, which on screen is indistinguishable from
                    # "well stocked". Say so.
                    if rows.empty:
                        log.warning(
                            "SKU %s: champion %r produced no forecast rows — "
                            "no inventory recommendation will be generated",
                            sku, best,
                        )
                        skipped_no_forecast.append({"sku": sku, "model": best})
                        continue
                else:
                    rows = sku_fc

                rows_sorted = rows.sort_values("step")
                arr = rows_sorted["forecast"].astype(float).to_numpy()

                if len(arr) == 0:
                    continue

                if len(arr) < horizon:
                    arr = np.pad(arr, (0, horizon - len(arr)), constant_values=arr[-1])

                fc_arrays[sku] = np.clip(arr, 0.0, None)

                # sigma per step, averaged over the horizon — NOT np.std(arr),
                # which is the spread of the forecast path (near-zero for a
                # flat, stable-SKU forecast, exactly what a good model
                # produces). Only computed where q90 is present: legacy
                # sessions and non-quantile models leave this SKU out of
                # std_by_sku entirely.
                if "q90" in rows_sorted.columns:
                    q90_vals = rows_sorted["q90"].astype(float).to_numpy()
                    fc_vals  = rows_sorted["forecast"].astype(float).to_numpy()
                    valid = ~np.isnan(q90_vals)
                    if valid.any():
                        sigmas = np.clip((q90_vals[valid] - fc_vals[valid]) / _Q90_Z, 0.0, None)
                        std_by_sku[sku] = float(np.mean(sigmas))

        # -----------------------------
        # FALLBACK SAFE (PER SKU ONLY)
        # -----------------------------
        if not fc_arrays:
            if _primary_group(c) and _primary_group(c) in df.columns:

                for sku_val, g in df.groupby(_primary_group(c)):
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

        self._skipped_no_forecast = skipped_no_forecast
        recs = advisor.batch_recommend(fc_arrays, std_by_sku=std_by_sku)
        return advisor.summary_df(recs)

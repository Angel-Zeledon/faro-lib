"""
ForecastEngine — main entry point for forecasting_core.

Fluent API design: each method returns `self`, enabling method chaining.
Every method the engine exposes has a corresponding API endpoint and frontend UI.

Example (programmatic):
    engine = ForecastEngine()
    engine.load_data("sales.csv")
    engine.choose_columns(target="sales", date="date", sku="sku")
    engine.configure_features(lags=[1, 7, 14], rolling=[7, 14])
    engine.configure_training(walk_forward=True, wfv_splits=3)
    engine.select_models(["lightgbm", "prophet"])
    engine.train()
    report = engine.generate_report()

Example (from config):
    engine = ForecastEngine.from_config("session_config.json")
    engine.train()

Example (API usage — what the FastAPI layer calls):
    engine = ForecastEngine()
    engine.load_data(path)
    profile  = engine.get_profile()           # → sent to frontend for column picker
    options  = engine.get_column_options()    # → sent to frontend as dropdowns
    schema   = engine.get_config_schema()     # → sent to frontend for form builder
    engine.choose_columns(target, date, sku)
    engine.train()
    metrics  = engine.get_metrics()
    forecast = engine.get_forecast()
    inventory = engine.get_inventory_report()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)


def _primary_group(c) -> Optional[str]:
    """Return the first group key, or None if group_keys is empty."""
    return c.group_keys[0] if c.group_keys else None


def _seasonal_labels(period: int, dates: "pd.Series") -> List[str]:
    """Generate human-readable labels for each position in a seasonal cycle."""
    if period == 7:
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if period == 12:
        return ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if period == 4:
        return ["Q1", "Q2", "Q3", "Q4"]
    if period == 52:
        return [f"W{i+1}" for i in range(52)]
    return [f"P{i+1}" for i in range(period)]


class EngineError(Exception):
    pass


class ForecastEngine:
    """
    Main forecasting engine. Stateful, fluent interface.

    State machine:
        INIT → loaded → configured → trained → forecasted

    Attributes:
        _df:       Raw loaded DataFrame.
        _df_ml:    Feature-engineered DataFrame (ML-ready).
        _config:   SessionConfig with all parameters.
        _results:  Dict with training results per model/SKU.
        _profile:  DataProfiler output (column metadata).
        _dq:       DataQualityChecker reports.
        _routing:  ModelRouter assignments.
    """

    def __init__(self):
        self._df:       Optional[pd.DataFrame] = None
        self._df_ml:    Optional[pd.DataFrame] = None
        self._config    = None
        self._results:  Dict[str, Any] = {}
        self._profile:  Optional[dict] = None
        self._dq:       Optional[dict] = None
        self._routing:  Optional[dict] = None
        self._metrics_df:    Optional[pd.DataFrame] = None
        self._forecast_df:   Optional[pd.DataFrame] = None
        self._inventory_df:  Optional[pd.DataFrame] = None
        self._run_id:    str = ""
        self._fitted_models:  dict = {}   # ML training results — kept for re-forecast
        self._stat_forecasts: dict = {}   # Stat forecast arrays — kept for re-forecast
        self._transformer = None          # DataTransformer fitted at train() time

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config_path: str) -> "ForecastEngine":
        """Build engine from a session_config.json file."""
        from forecasting_core.config.config import SessionConfig
        engine = cls()
        engine._config = SessionConfig.from_json(config_path)
        return engine

    @classmethod
    def from_dict(cls, config_dict: dict) -> "ForecastEngine":
        """Build engine from a config dict (used by the API layer)."""
        from forecasting_core.config.config import SessionConfig
        engine = cls()
        engine._config = SessionConfig.from_dict(config_dict)
        return engine

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> "ForecastEngine":
        """
        Persist fitted models, stat forecasts, and config to disk.

        Uses joblib serialization (handles sklearn models and numpy arrays).
        The caller is responsible for choosing a stable path; the backend
        worker typically calls this after train() completes.

        Args:
            path: File path to write (e.g. "models/session_1.joblib").

        Returns:
            self (for chaining)
        """
        import joblib
        from pathlib import Path
        if not self._fitted_models and not self._stat_forecasts:
            raise EngineError("Nothing to save — call train() first.")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config":         self._config.to_dict() if self._config else {},
            "fitted_models":  self._fitted_models,
            "stat_forecasts": self._stat_forecasts,
            "transformer":    self._transformer,
        }
        joblib.dump(payload, path, compress=3)
        log.info(f"ForecastEngine saved → {path}")
        return self

    @classmethod
    def load(cls, path: str) -> "ForecastEngine":
        """
        Restore a ForecastEngine from a previously saved joblib file.

        Restores config, fitted ML models, and stat forecast arrays so that
        predict() works without retraining.

        Args:
            path: Path written by a previous save() call.

        Returns:
            A new ForecastEngine instance ready for predict().
        """
        import joblib
        from forecasting_core.config.config import SessionConfig
        payload = joblib.load(path)
        cfg_dict = payload.get("config", {})
        engine = cls()
        if cfg_dict:
            try:
                engine._config = SessionConfig.from_dict(cfg_dict)
            except Exception as e:
                log.warning(f"Could not restore config from saved file: {e}")
        engine._fitted_models  = payload.get("fitted_models", {})
        engine._stat_forecasts = payload.get("stat_forecasts", {})
        engine._transformer    = payload.get("transformer")
        log.info(f"ForecastEngine loaded ← {path} "
                 f"({len(engine._fitted_models)} ML entries, "
                 f"{len(engine._stat_forecasts)} stat models)")
        return engine

    # ------------------------------------------------------------------
    # Step 1: Load data
    # ------------------------------------------------------------------

    def load_data(self, source) -> "ForecastEngine":
        """
        Load dataset from file path or DataFrame.

        Args:
            source: Path string (CSV/Excel/Parquet/SQL) or pd.DataFrame.

        Returns:
            self (for chaining)

        Example:
            engine.load_data("sales.csv")
            engine.load_data(my_dataframe)
        """
        from forecasting_core.data.loader import DataLoader
        loader = DataLoader()
        if isinstance(source, pd.DataFrame):
            self._df = loader.load_df(source)
        else:
            self._ensure_config()
            sql_engine = self._config.data.sql_engine
            self._df = loader.load(str(source), sql_engine)
            self._config.data.path = str(source)
        log.info(f"Loaded {len(self._df):,} rows × {len(self._df.columns)} cols")
        return self

    # ------------------------------------------------------------------
    # Step 2: Inspect — methods used by API/frontend for dynamic UI
    # ------------------------------------------------------------------

    def get_profile(self) -> dict:
        """
        Full dataset profile: column metadata, recommendations, stats.

        Returns:
            {columns: [...], recommended: {date, target, group, freq}, stats: {...}}

        Used by:
            - Frontend: column picker UI
            - API: GET /datasets/{id}/profile
        """
        self._require_data()
        from forecasting_core.data.profiler import DataProfiler
        profiler = DataProfiler()
        self._profile = profiler.profile(self._df)
        return self._profile

    def get_column_options(self) -> dict:
        """
        Candidate columns for each role (date / target / group / exogenous).

        Returns:
            {date_candidates, target_candidates, group_candidates, exog_candidates}

        Used by:
            - Frontend: dropdowns for column selection
            - API: GET /datasets/{id}/columns
        """
        self._require_data()
        from forecasting_core.data.profiler import DataProfiler
        return DataProfiler().get_column_options(self._df)

    def get_data_quality_report(self) -> dict:
        """
        Per-SKU data quality report.

        Returns:
            {sku: {quality_score, series_type, warnings, ...}}

        Used by:
            - Frontend: quality dashboard before training
            - API: GET /datasets/{id}/quality
        """
        self._require_configured()
        from forecasting_core.data.quality import DataQualityChecker
        c = self._config.columns
        rt = self._config.routing.thresholds
        checker = DataQualityChecker(
            dt_col=c.date, target_col=c.target, group_col=_primary_group(c),
            min_history=self._config.training.min_history,
            seasonal_period=self._config.training.seasonal_period,
            intermittency_threshold=rt.intermittency,
            cv_volatile=rt.cv_volatile,
            seasonal_strength_threshold=rt.seasonal_strength,
        )
        self._dq = checker.check(self._df_prepared())
        return {sku: r.to_dict() for sku, r in self._dq.items()}

    def get_config_schema(self) -> dict:
        """
        Returns the full schema of configurable parameters.
        Used by the frontend to build dynamic configuration forms.

        Returns:
            {training: {...}, features: {...}, forecast: {...}, business: {...}, available_models: [...]}

        Used by:
            - Frontend: config form builder
            - API: GET /config/schema
        """
        from forecasting_core.config.config import SessionConfig
        return SessionConfig.schema()

    def get_available_models(self) -> list:
        """
        Returns all models available in forecasting_core.

        Used by:
            - Frontend: model selection checkboxes
        """
        from forecasting_core.models.factory import ModelFactory
        return ModelFactory.available_models()

    def get_routing_plan(self) -> dict:
        """
        Returns the model routing plan (which models run on which SKUs).
        Requires data to be loaded and columns configured.

        Used by:
            - Frontend: routing preview before training
            - API: GET /sessions/{id}/routing
        """
        self._require_configured()
        from forecasting_core.data.quality import DataQualityChecker
        from forecasting_core.training.router import ModelRouter
        c = self._config.columns
        rt = self._config.routing.thresholds
        checker = DataQualityChecker(
            c.date, c.target, _primary_group(c),
            min_history=self._config.training.min_history,
            seasonal_period=self._config.training.seasonal_period,
            intermittency_threshold=rt.intermittency,
            cv_volatile=rt.cv_volatile,
            seasonal_strength_threshold=rt.seasonal_strength,
        )
        reports = checker.check(self._df_prepared())
        router = ModelRouter(self._config.models, self._config.routing.enabled)
        routing = router.route(reports)
        return {
            sku: {
                "models":  sorted(models),
                "flags":   sorted(getattr(reports[sku], "series_flags", {reports[sku].series_type})),
                "reasons": getattr(reports[sku], "series_reasons", {}),
            }
            for sku, models in routing.items()
        }

    def configure_transforms(
        self,
        specs: Dict[str, dict],
        auto_apply: bool = False,
    ) -> "ForecastEngine":
        """
        Configure per-column data transformations applied before feature engineering.

        Transformations are applied in order: impute → encode → scale.
        If the target column is scaled, the engine automatically inverts the scaling
        on all forecast output so the user always receives values in the original scale.

        Args:
            specs:      {column_name: {impute, encode, scale, log_offset, log_base}}

                        impute  : none | mean | median | mode | forward
                                  | interpolate | zero | smart
                        encode  : none | label | one_hot | ordinal | binary | auto
                        scale   : none | standard | minmax | robust | log | power
                        log_offset : float, default 1.0  (used when scale="log")
                        log_base   : float | None        (None = natural log)

            auto_apply: Store flag so the engine knows suggestions were auto-applied.

        Returns:
            self

        Example:
            engine.configure_transforms({
                "sales":  {"impute": "median", "scale": "log"},
                "price":  {"scale": "minmax"},
                "region": {"encode": "label"},
            })
        """
        self._ensure_config()
        from forecasting_core.config.config import TransformConfig
        from forecasting_core.data.transform import VALID_IMPUTE, VALID_ENCODE, VALID_SCALE
        for col, spec in specs.items():
            imp = spec.get("impute", "none")
            enc = spec.get("encode", "none")
            scl = spec.get("scale",  "none")
            if imp not in VALID_IMPUTE:
                raise EngineError(f"configure_transforms: invalid impute '{imp}' for '{col}'. "
                                  f"Valid: {sorted(VALID_IMPUTE)}")
            if enc not in VALID_ENCODE:
                raise EngineError(f"configure_transforms: invalid encode '{enc}' for '{col}'. "
                                  f"Valid: {sorted(VALID_ENCODE)}")
            if scl not in VALID_SCALE:
                raise EngineError(f"configure_transforms: invalid scale '{scl}' for '{col}'. "
                                  f"Valid: {sorted(VALID_SCALE)}")
        self._config.transforms = TransformConfig(columns=specs, auto_apply=auto_apply)
        return self

    def get_transform_suggestions(self) -> List[dict]:
        """
        Suggest per-column transforms based on the loaded data's characteristics.

        Analyzes dtype, null rate, cardinality, and skewness to produce a ranked
        list of suggestions. Returns suggestions sorted by column role
        (features first, target last).

        Returns:
            list of dicts:
            [
                {
                    column       : str,
                    is_target    : bool,
                    suggested_spec: {impute, encode, scale},
                    reasons      : [str, ...],
                    auto_apply   : bool  (False for target — needs explicit opt-in),
                }
            ]

        Used by:
            - Frontend: "Configure transforms" step with suggestions highlighted
            - API: GET /sessions/{id}/transform-suggestions
        """
        self._require_data()
        self._ensure_config()
        from scipy.stats import skew as _skew

        c = self._config.columns
        df = self._df_prepared() if self._config.columns.target else self._df.copy()

        skip_cols: set = set()
        if c.date:
            skip_cols.add(c.date)
        if _primary_group(c):
            skip_cols.add(_primary_group(c))

        suggestions = []
        for col in df.columns:
            if col in skip_cols:
                continue

            s = df[col]
            is_target   = (col == c.target)
            is_numeric  = pd.api.types.is_numeric_dtype(s)
            is_datetime = pd.api.types.is_datetime64_any_dtype(s)
            null_pct    = float(s.isna().mean() * 100)
            n_unique    = int(s.nunique())

            spec:    dict = {}
            reasons: list = []

            # ── Imputation ───────────────────────────────────────────
            if null_pct > 0:
                if is_numeric:
                    spec["impute"] = "median"
                    reasons.append(f"{null_pct:.1f}% missing → median imputation")
                elif is_datetime:
                    spec["impute"] = "forward"
                    reasons.append(f"{null_pct:.1f}% missing → forward fill")
                else:
                    spec["impute"] = "mode"
                    reasons.append(f"{null_pct:.1f}% missing → mode imputation")

            # ── Encoding ─────────────────────────────────────────────
            if not is_numeric and not is_datetime:
                if n_unique <= 15:
                    spec["encode"] = "one_hot"
                    reasons.append(f"{n_unique} categories → one-hot encoding")
                elif n_unique <= 200:
                    spec["encode"] = "label"
                    reasons.append(f"{n_unique} categories → label encoding")
                else:
                    spec["encode"] = "binary"
                    reasons.append(f"{n_unique} categories → binary encoding (high cardinality)")

            # ── Scaling ──────────────────────────────────────────────
            if is_numeric:
                clean = s.dropna().values
                try:
                    sk = abs(float(_skew(clean))) if len(clean) > 3 else 0.0
                except Exception:
                    sk = 0.0

                if is_target:
                    if sk > 2.0:
                        spec["scale"] = "log"
                        reasons.append(
                            f"target skewness={sk:.2f} > 2 → log transform improves "
                            f"model fit; output will be auto-inverted to original scale"
                        )
                else:
                    if sk > 2.0:
                        spec["scale"] = "log"
                        reasons.append(f"skewness={sk:.2f} → log transform reduces skew")
                    else:
                        std_val = float(np.std(clean)) if len(clean) > 1 else 0.0
                        mean_val = abs(float(np.mean(clean))) if len(clean) > 0 else 1.0
                        if std_val > 10 * (mean_val or 1.0):
                            spec["scale"] = "robust"
                            reasons.append("high variance relative to mean → robust scaling")
                        else:
                            spec["scale"] = "standard"
                            reasons.append("numeric feature → standard scaling for ML models")

            if spec:
                suggestions.append({
                    "column":        col,
                    "is_target":     is_target,
                    "suggested_spec": spec,
                    "reasons":       reasons,
                    "auto_apply":    not is_target,
                })

        # Sort: non-target features first, target last
        suggestions.sort(key=lambda x: (x["is_target"], x["column"]))
        return suggestions

    def analyze(
        self,
        sku: Optional[str] = None,
        seasonal_period: Optional[int] = None,
    ) -> dict:
        """
        Run full statistical and time-series analysis for one or all SKUs.

        Covers: descriptive stats, normality, outliers, intermittency (ADI/CV²),
        distribution fitting, stationarity (ADF + KPSS), STL decomposition,
        seasonality (FFT + ACF), trend (Mann-Kendall + Sen's slope + change points),
        and autocorrelation (ACF/PACF + Ljung-Box).

        Args:
            sku:             If given, returns analysis for that SKU only.
                             If None, returns {sku: analysis_dict} for all SKUs.
            seasonal_period: Override the seasonal period used for STL decomposition
                             and seasonality detection. Defaults to
                             config.training.seasonal_period.

        Returns:
            dict (single SKU) or dict[str, dict] (all SKUs)

        Requires data to be loaded and columns configured via choose_columns().

        Used by:
            - API: GET /sessions/{id}/analyze/{sku}
            - API: GET /sessions/{id}/analyze
        """
        self._require_configured()
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer
        c = self._config.columns
        period = seasonal_period or self._config.training.seasonal_period
        analyzer = TimeSeriesAnalyzer(
            df=self._df_prepared(),
            date_col=c.date,
            target_col=c.target,
            group_col=_primary_group(c),
            seasonal_period=period,
        )
        return analyzer.analyze(sku)

    def get_analysis_summary(
        self,
        seasonal_period: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Flat DataFrame with one row per SKU containing all key analysis metrics.

        Useful for comparing SKUs side-by-side, sorting by seasonality strength,
        identifying intermittent-demand items, or selecting model families.

        Columns include: sku, n, mean, std, cv, skewness, kurtosis,
        zero_pct, outlier_pct, best_distribution, croston_class, adi, cv2,
        stationarity, diff_order, dominant_period, seasonal_strength,
        seasonality_class, trend_direction, trend_pvalue, sens_slope,
        n_change_points, suggested_ar_order, suggested_ma_order, is_white_noise.

        Args:
            seasonal_period: Override seasonal period (defaults to config value).

        Returns:
            pd.DataFrame — one row per SKU

        Used by:
            - API: GET /sessions/{id}/analysis-summary
        """
        self._require_configured()
        from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer
        c = self._config.columns
        period = seasonal_period or self._config.training.seasonal_period
        analyzer = TimeSeriesAnalyzer(
            df=self._df_prepared(),
            date_col=c.date,
            target_col=c.target,
            group_col=_primary_group(c),
            seasonal_period=period,
        )
        return analyzer.summary()

    # ------------------------------------------------------------------
    # Step 3: Configure — mirrors each configurable parameter
    # ------------------------------------------------------------------

    def choose_columns(
        self,
        target: str,
        date: str,
        sku: Optional[str] = None,
        exogenous: Optional[List[str]] = None,
    ) -> "ForecastEngine":
        """
        Set column mapping for target, date, and optional group/SKU.

        Args:
            target:     Column to forecast.
            date:       Date/timestamp column.
            sku:        Group/SKU column (None = single series).
            exogenous:  Additional regressor columns (for Prophet/SARIMAX).

        Returns:
            self
        """
        self._require_data()
        self._ensure_config()
        self._config.columns.target = target
        self._config.columns.date = date
        self._config.columns.group_keys = [sku] if sku else []
        self._config.columns.exogenous = exogenous or []
        self._config.validate()
        return self

    def configure_features(
        self,
        lags: Optional[List[int]] = None,
        rolling: Optional[List[int]] = None,
        diffs: Optional[List[int]] = None,
        calendar: bool = True,
        ewm_spans: Optional[List[int]] = None,
    ) -> "ForecastEngine":
        """
        Configure feature engineering parameters.

        Args:
            lags:      Lag periods (e.g. [1, 7, 14]).
            rolling:   Rolling window sizes (e.g. [7, 14, 28]).
            diffs:     Differencing periods.
            calendar:  Include calendar features (month, DOW, holidays).
            ewm_spans: Exponential weighted mean spans.

        Returns:
            self
        """
        self._ensure_config()
        f = self._config.features
        if lags      is not None: f.lags = lags
        if rolling   is not None: f.rolling = rolling
        if diffs     is not None: f.diffs = diffs
        if ewm_spans is not None: f.ewm_spans = ewm_spans
        f.calendar = calendar
        return self

    def configure_training(
        self,
        train_ratio: float = 0.8,
        walk_forward: bool = True,
        wfv_splits: int = 3,
        min_history: int = 20,
        seasonal_period: int = 7,
    ) -> "ForecastEngine":
        """Configure training strategy."""
        self._ensure_config()
        t = self._config.training
        t.train_ratio = train_ratio
        t.walk_forward = walk_forward
        t.wfv_splits = wfv_splits
        t.min_history = min_history
        t.seasonal_period = seasonal_period
        return self

    def configure_forecast(
        self, horizon: int = 14, quantiles: Optional[List[float]] = None
    ) -> "ForecastEngine":
        """Configure forecast horizon and quantile levels."""
        self._ensure_config()
        self._config.forecast.horizon = horizon
        if quantiles: self._config.forecast.quantiles = quantiles
        return self

    def configure_business(
        self,
        service_level: float = 0.95,
        lead_time_days: int = 7,
        holding_cost_pct: float = 0.20,
        stockout_cost_multiplier: float = 3.0,
    ) -> "ForecastEngine":
        """Configure business rules for inventory recommendations."""
        self._ensure_config()
        b = self._config.business
        b.service_level = service_level
        b.lead_time_days = lead_time_days
        b.holding_cost_pct = holding_cost_pct
        b.stockout_cost_multiplier = stockout_cost_multiplier
        return self

    def select_models(self, models: List[str], hyperparams: Optional[Dict] = None) -> "ForecastEngine":
        """
        Select which models to include.

        Args:
            models:      List of model names (e.g. ["lightgbm", "prophet"]).
            hyperparams: {model_name: {param: value}} overrides.

        Returns:
            self
        """
        self._ensure_config()
        hp = hyperparams or {}
        self._config.models = {
            m: {**self._config.models.get(m, {}), **hp.get(m, {})}
            for m in models
        }
        return self

    def set_config(self, config_dict: dict) -> "ForecastEngine":
        """Replace full config from dict. Used by the API layer."""
        from forecasting_core.config.config import SessionConfig
        self._config = SessionConfig.from_dict(config_dict)
        return self

    # ------------------------------------------------------------------
    # Step 4: Train
    # ------------------------------------------------------------------

    def train(self, on_progress=None) -> "ForecastEngine":
        """
        Run the full training pipeline.

        Internally calls:
          DataTransformer (if configured) → DataQualityChecker → ModelRouter
          → FeatureEngineer → Trainer (WFV) → ARIMA/Prophet/ETS/Croston
          → WeightedEnsemble → Registry

        Args:
            on_progress: Optional callable receiving dicts:
                {"pct": 0-100, "message": str, "status": str}
                Useful for streaming progress to a frontend or job queue.

        Returns:
            self
        """
        self._require_configured()
        df_train = self._df_for_training()
        from forecasting_core.pipelines.pipeline import Pipeline
        results = Pipeline(self._config, df=df_train).run(on_progress=on_progress)
        self._metrics_df     = results.metrics_df
        self._forecast_df    = results.forecast_df
        self._inventory_df   = results.inventory_df
        self._run_id         = results.run_id
        self._fitted_models  = results.fitted_models
        self._stat_forecasts = results.stat_forecasts

        # Apply hierarchical reconciliation if configured
        hierarchy_cfg = self._config.to_dict().get("hierarchy", {})
        if hierarchy_cfg and hierarchy_cfg.get("levels") and self._forecast_df is not None:
            try:
                from forecasting_core.hierarchy import HierarchicalReconciler
                reconciler = HierarchicalReconciler(levels=hierarchy_cfg["levels"])
                method = hierarchy_cfg.get("reconciliation", "bottom_up")
                if method == "top_down" and self._df is not None:
                    self._forecast_df = reconciler.top_down(
                        self._forecast_df, self._df,
                        date_col="date", value_col="forecast",
                        target_col=self._config.columns.target,
                    )
                else:
                    self._forecast_df = reconciler.bottom_up(
                        self._forecast_df, date_col="date", value_col="forecast"
                    )
                log.info(f"Hierarchical reconciliation ({method}) applied.")
            except Exception as e:
                log.warning(f"Hierarchical reconciliation failed: {e}")

        log.info(f"Training complete. Run ID: {self._run_id}")
        return self

    # ------------------------------------------------------------------
    # Step 5: Read results
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict:
        """
        Return training metrics as a list of records.

        Returns:
            {"rows": [...], "by_model": {model: {avg_mae, avg_rmse, avg_wape, avg_bias, avg_mape, avg_smape}}, "shap": {...}}

        Used by:
            - Frontend: Results page — table + bar chart
            - API: GET /sessions/{id}/metrics
        """
        self._require_trained()
        df = self._metrics_df
        rows = df.to_dict(orient="records")
        by_model = (df.groupby("model")
                    .agg(avg_mae=("mae", "mean"), avg_rmse=("rmse", "mean"),
                         avg_wape=("wape", "mean"), avg_bias=("bias", "mean"),
                         avg_mape=("mape", "mean"), avg_smape=("smape", "mean"))
                    .round(4).to_dict(orient="index"))

        # Extract SHAP importance per SKU per model from fitted ML models
        shap: dict = {}
        for key, entry in self._fitted_models.items():
            shap_imp = entry.get("shap_importance")
            if not shap_imp:
                continue
            sku = str(entry.get("sku", key))
            model_name = str(entry.get("model", key))
            shap.setdefault(sku, {})[model_name] = shap_imp

        return {"rows": rows, "by_model": by_model, "shap": shap}

    def get_forecast(self) -> dict:
        """
        Return point forecasts per SKU as JSON-serializable records.

        Returns:
            {"rows": [...], "n_skus": int, "horizon": int}

        Used by:
            - Frontend: forecast chart
            - API: GET /sessions/{id}/forecasts
        """
        self._require_trained()
        if self._forecast_df is None or self._forecast_df.empty:
            return {"rows": [], "n_skus": 0, "horizon": 0}
        df = self._inverse_transform_forecast(self._forecast_df)
        # Convert Timestamps to ISO strings for JSON serialisation
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)
        return {
            "rows":   df.to_dict(orient="records"),
            "n_skus": int(df["sku"].nunique()) if "sku" in df.columns else 0,
            "horizon": int(df["step"].max()) if "step" in df.columns else 0,
        }

    def predict(self, horizon: Optional[int] = None) -> pd.DataFrame:
        """
        Return future forecasts as a DataFrame.

        Strategy:
          1. Return cached forecasts if they cover the requested horizon.
          2. Re-generate from cached fitted models (no retraining).
          3. Fallback: re-run the full pipeline with the loaded DataFrame.

        Args:
            horizon: Steps ahead (defaults to config.forecast.horizon).

        Returns:
            DataFrame[sku, model, date, forecast, p90_lo, p90_hi, step]
        """
        self._require_trained()
        h = horizon or self._config.forecast.horizon

        # 1. Cached forecasts cover the requested horizon
        if self._forecast_df is not None and not self._forecast_df.empty:
            max_step = int(self._forecast_df["step"].max()) if "step" in self._forecast_df.columns else 0
            if max_step >= h:
                mask = self._forecast_df["step"] <= h
                return self._forecast_df[mask].reset_index(drop=True)

        # 2. Re-generate from cached fitted models (no retraining)
        if self._fitted_models or self._stat_forecasts:
            log.info(f"predict(): re-generating {h}-step forecast from cached models (no retraining)...")
            self._config.forecast.horizon = h
            from forecasting_core.inference.predictor import predict_all_skus
            fc_dict = predict_all_skus(
                self._fitted_models, self._stat_forecasts,
                self._df, self._config,
            )
            rows = []
            for sku, model_dict in fc_dict.items():
                for model_name, pts in model_dict.items():
                    for i, pt in enumerate(pts):
                        rows.append({
                            "sku": sku, "model": model_name,
                            "date": pd.Timestamp(pt["date"]),
                            "forecast": pt["value"],
                            "p90_lo": pt.get("lower"),
                            "p90_hi": pt.get("upper"),
                            "step": i + 1,
                        })
            if rows:
                self._forecast_df = pd.DataFrame(rows)
                return self._inverse_transform_forecast(
                    self._forecast_df
                ).reset_index(drop=True)

        # 3. Fallback: full pipeline re-run (passes self._df to avoid FileNotFoundError)
        log.info(f"predict(): no cached models — re-running full pipeline for {h} steps...")
        self._config.forecast.horizon = h
        from forecasting_core.pipelines.pipeline import Pipeline
        results = Pipeline(self._config, df=self._df).run()
        self._forecast_df    = results.forecast_df
        self._metrics_df     = results.metrics_df
        self._inventory_df   = results.inventory_df
        self._fitted_models  = results.fitted_models
        self._stat_forecasts = results.stat_forecasts

        if self._forecast_df is None:
            return pd.DataFrame(columns=["sku", "model", "date", "forecast", "p90_lo", "p90_hi", "step"])
        return self._forecast_df.reset_index(drop=True)

    def get_inventory_report(self) -> dict:
        """
        Return inventory recommendations (reorder point, safety stock, stockout risk).

        Returns:
            {"recommendations": [...]}

        Used by:
            - Frontend: Decision Intelligence dashboard
            - API: GET /sessions/{id}/inventory
        """
        self._require_trained()
        if self._inventory_df is None or self._inventory_df.empty:
            return {"recommendations": []}
        return {"recommendations": self._inventory_df.to_dict(orient="records")}

    # ------------------------------------------------------------------
    # Report / export
    # ------------------------------------------------------------------

    def generate_report(self) -> dict:
        """
        Full report: metrics + inventory + config summary.

        Returns:
            {metrics, inventory, config_hash, run_id, n_skus, ...}
        """
        self._require_trained()
        return {
            "run_id":      self._run_id,
            "config_hash": self._config.hash,
            "metrics":     self.get_metrics(),
            "inventory":   self.get_inventory_report(),
            "config":      self._config.to_dict(),
        }

    def get_forecast_dict(self) -> dict:
        """
        Return forecasts as {sku: {model: [{date, value, lower, upper}]}}.

        This is the exact format written to session_forecasts_file.
        Values come from the pipeline's recursive ML and stat model forecasters;
        confidence intervals are ±1σ-based (90% CI from p90_lo/p90_hi).

        Returns empty dict if no forecast was generated (e.g., insufficient data).
        """
        self._require_trained()
        if self._forecast_df is None or self._forecast_df.empty:
            return {}

        df = self._forecast_df.copy()
        if "date" in df.columns:
            df["date"] = df["date"].astype(str).str[:10]

        result: dict = {}
        for (sku, model), grp in df.groupby(["sku", "model"]):
            pts = []
            sort_col = "step" if "step" in grp.columns else "date"
            for _, row in grp.sort_values(sort_col).iterrows():
                lo = row.get("p90_lo")
                hi = row.get("p90_hi")
                pts.append({
                    "date":  str(row["date"])[:10],
                    "value": round(float(row["forecast"]), 4),
                    "lower": round(float(lo), 4) if lo is not None and not pd.isna(lo) else 0.0,
                    "upper": round(float(hi), 4) if hi is not None and not pd.isna(hi) else 0.0,
                })
            result.setdefault(str(sku), {})[str(model)] = pts

        return result

    def predict_by_sku(self, sku: str, horizon: Optional[int] = None) -> pd.DataFrame:
        """
        Return forecasts for a single SKU.

        Args:
            sku:     SKU identifier to filter.
            horizon: Steps ahead (defaults to config.forecast.horizon).

        Returns:
            DataFrame[sku, model, date, forecast, p90_lo, p90_hi, step]
            filtered to the requested SKU.
        """
        df = self.predict(horizon)
        if "sku" not in df.columns or df.empty:
            return df
        return df[df["sku"].astype(str) == str(sku)].reset_index(drop=True)

    def detect_drift(
        self,
        new_data,
        feature_columns: Optional[List[str]] = None,
    ) -> dict:
        """
        Detect statistical drift between training data and new incoming data.

        Compares feature distributions using PSI and the KS test.
        Requires data to have been loaded and training completed.

        Args:
            new_data:        Path string or DataFrame of new observations.
            feature_columns: Feature columns to check (defaults to all numeric).

        Returns:
            {
                "feature_drift": {col: {psi, psi_level, ks_p_value, drift}},
                "alerts":        [str, ...],
                "has_drift":     bool,
                "n_drifted_features": int,
            }
        """
        self._require_trained()
        self._require_data()
        from forecasting_core.monitoring.drift import DriftDetector

        c = self._config.columns
        if isinstance(new_data, str):
            from forecasting_core.data.loader import DataLoader
            new_df = DataLoader().load(new_data)
        else:
            new_df = new_data if isinstance(new_data, pd.DataFrame) else pd.DataFrame(new_data)

        detector = DriftDetector(
            reference_df=self._df,
            target_col=c.target,
            group_col=_primary_group(c) or "",
        )
        feature_report = detector.detect_feature_drift(new_df)
        alerts = detector.alerts(feature_report)
        return {
            "feature_drift":        feature_report,
            "alerts":               alerts,
            "has_drift":            len(alerts) > 0,
            "n_drifted_features":   sum(1 for v in feature_report.values() if v["drift"]),
        }

    # ------------------------------------------------------------------
    # Chart-ready outputs
    # ------------------------------------------------------------------

    def get_decomposition_chart(
        self,
        sku: Optional[str] = None,
        seasonal_period: Optional[int] = None,
    ) -> dict:
        """
        Return STL decomposition in chart-ready format with real dates.

        Maps trend/seasonal/residual arrays to their actual dates so the
        frontend can render them directly without any post-processing.

        Args:
            sku:             SKU to analyse (None = full dataset).
            seasonal_period: Override period (defaults to config value).

        Returns:
            {
                sku, period,
                dates:    ["YYYY-MM-DD", ...],
                original: [float, ...],
                trend:    [float | null, ...],
                seasonal: [float | null, ...],
                residual: [float | null, ...],
                trend_strength:    float,
                seasonal_strength: float,
            }

        Used by:
            - Frontend: STL decomposition chart tab
            - API: GET /sessions/{id}/decomposition/{sku}
        """
        self._require_configured()
        from forecasting_core.analysis.decomposition import decompose

        c = self._config.columns
        period = seasonal_period or self._config.training.seasonal_period
        df = self._df_prepared()

        if sku and _primary_group(c):
            sub = df[df[_primary_group(c)].astype(str) == str(sku)]
        else:
            sub = df
        sub = sub.sort_values(c.date)

        series = sub[c.target].astype(float).values
        dates  = pd.to_datetime(sub[c.date]).dt.strftime("%Y-%m-%d").tolist()

        if len(series) < 2 * period:
            return {
                "sku": str(sku) if sku else "__all__",
                "error": f"Insufficient data: need {2 * period} points, got {len(series)}",
            }

        result = decompose(series, period)

        def _fmt(arr):
            return [round(float(v), 4) if not np.isnan(v) else None for v in arr]

        return {
            "sku":               str(sku) if sku else "__all__",
            "period":            period,
            "dates":             dates,
            "original":          [round(float(v), 4) for v in series],
            "trend":             _fmt(result["trend"]),
            "seasonal":          _fmt(result["seasonal"]),
            "residual":          _fmt(result["residual"]),
            "trend_strength":    result.get("trend_strength"),
            "seasonal_strength": result.get("seasonal_strength"),
        }

    def get_seasonality_chart(
        self,
        sku: Optional[str] = None,
        seasonal_period: Optional[int] = None,
    ) -> dict:
        """
        Return seasonal indices per period position in chart-ready format.

        Each index represents the average demand relative to the grand mean
        at that position in the seasonal cycle:
            index > 1.0  → above-average demand at that position
            index < 1.0  → below-average demand at that position

        Args:
            sku:             SKU to analyse (None = full dataset).
            seasonal_period: Override period (defaults to config value).

        Returns:
            {
                sku, period,
                indices: [float, ...],          # length == period
                labels:  ["Mon", "Tue", ...],   # human-readable position labels
                grand_mean: float,
            }

        Used by:
            - Frontend: seasonality bar/radar chart
            - API: GET /sessions/{id}/seasonality/{sku}
        """
        self._require_configured()
        c = self._config.columns
        period = seasonal_period or self._config.training.seasonal_period
        df = self._df_prepared()

        if sku and _primary_group(c):
            sub = df[df[_primary_group(c)].astype(str) == str(sku)]
        else:
            sub = df
        sub = sub.sort_values(c.date)

        series = sub[c.target].astype(float).values
        dates  = pd.to_datetime(sub[c.date])
        grand_mean = float(np.mean(series)) if len(series) > 0 else 1.0
        safe_mean  = grand_mean if abs(grand_mean) > 1e-9 else 1.0

        indices = []
        for i in range(period):
            positions = series[i::period]
            idx = round(float(np.mean(positions)) / safe_mean, 4) if len(positions) > 0 else 1.0
            indices.append(idx)

        return {
            "sku":        str(sku) if sku else "__all__",
            "period":     period,
            "indices":    indices,
            "labels":     _seasonal_labels(period, dates),
            "grand_mean": round(grand_mean, 4),
        }

    # ------------------------------------------------------------------
    # Scenario / What-If
    # ------------------------------------------------------------------

    def apply_scenario(
        self,
        rules: list,
        inplace: bool = False,
    ) -> dict:
        """
        Apply what-if scenario rules to the current forecast and return the result.

        Rules can filter by SKU, model, and date range, then apply a multiplier,
        offset, floor, or ceiling to the forecast values and confidence bands.

        Args:
            rules:   list of ScenarioRule instances or equivalent dicts.
                     Dict keys: sku, model, multiplier, offset, date_start,
                     date_end, floor, ceiling, label.
            inplace: If True, replace self._forecast_df with the adjusted version.
                     Useful for persisting the scenario as the active forecast.

        Returns:
            {
                rows: [...],       # adjusted forecast records
                n_skus: int,
                rules_applied: int,
            }

        Example:
            # +10% across all SKUs, never negative
            engine.apply_scenario([
                {"multiplier": 1.10},
                {"floor": 0.0},
            ])

            # +25% for SKU_A only in June
            engine.apply_scenario([
                {"sku": "SKU_A", "date_start": "2025-06-01",
                 "date_end": "2025-06-30", "multiplier": 1.25},
            ])

        Used by:
            - Frontend: "What-if" scenario panel
            - API: POST /sessions/{id}/scenario
        """
        self._require_trained()
        from forecasting_core.scenarios.scenario import ScenarioEngine, ScenarioRule

        parsed = [ScenarioRule.from_dict(r) if isinstance(r, dict) else r for r in rules]
        result_df = ScenarioEngine.apply(self._forecast_df, parsed)

        if inplace:
            self._forecast_df = result_df

        df_out = result_df.copy()
        if "date" in df_out.columns:
            df_out["date"] = df_out["date"].astype(str).str[:10]

        return {
            "rows":          df_out.to_dict(orient="records"),
            "n_skus":        int(df_out["sku"].nunique()) if "sku" in df_out.columns else 0,
            "rules_applied": len(parsed),
        }

    def get_scenario_dict(self, rules: list) -> dict:
        """
        Apply scenario rules and return {sku: {model: [{date, value, lower, upper}]}}.

        Same format as get_forecast_dict() so the frontend can swap them transparently.

        Args:
            rules: see apply_scenario()

        Returns:
            {sku: {model: [{date, value, lower, upper}]}}
        """
        self._require_trained()
        from forecasting_core.scenarios.scenario import ScenarioEngine, ScenarioRule

        parsed = [ScenarioRule.from_dict(r) if isinstance(r, dict) else r for r in rules]
        return ScenarioEngine.apply_to_dict(self.get_forecast_dict(), parsed)

    def export_config(self, path: str):
        """Save current config to JSON for reproducibility."""
        self._ensure_config()
        self._config.to_json(path)
        log.info(f"Config exported → {path}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_data(self):
        if self._df is None:
            raise EngineError("No data loaded. Call load_data() first.")

    def _require_configured(self):
        self._require_data()
        if self._config is None:
            raise EngineError("Not configured. Call choose_columns() and select_models() first.")
        self._config.validate()

    def _require_trained(self):
        # Also pass if models were restored via load() — fitted_models is sufficient proof.
        if self._metrics_df is None and not self._fitted_models and not self._stat_forecasts:
            raise EngineError("Not trained yet. Call train() first.")

    def _ensure_config(self):
        if self._config is None:
            from forecasting_core.config.config import SessionConfig
            self._config = SessionConfig()

    def _df_prepared(self) -> pd.DataFrame:
        c = self._config.columns
        df = self._df.copy()
        df[c.date] = pd.to_datetime(df[c.date])
        df = df.dropna(subset=[c.target])
        sort_cols = [_primary_group(c), c.date] if _primary_group(c) else [c.date]
        return df.sort_values(sort_cols).reset_index(drop=True)

    def _df_for_training(self) -> pd.DataFrame:
        """Return the DataFrame to pass to Pipeline.run().
        Applies configured transforms (impute/encode/scale) and stores the
        fitted DataTransformer on self._transformer for forecast inversion."""
        df = self._df_prepared()
        specs = getattr(getattr(self._config, "transforms", None), "columns", {})
        if not specs:
            self._transformer = None
            return df
        from forecasting_core.data.transform import DataTransformer
        self._transformer = DataTransformer(specs)
        return self._transformer.fit_transform(df)

    def _inverse_transform_forecast(self, df: pd.DataFrame) -> pd.DataFrame:
        """Invert target scaling on the forecast column if a transformer is set."""
        if self._transformer is None:
            return df
        c = self._config.columns
        if not self._transformer.has_target_scaling(c.target):
            return df
        df = df.copy()
        df["forecast"] = self._transformer.inverse_transform_target(
            df["forecast"].values, c.target
        )
        if "p90_lo" in df.columns and df["p90_lo"].notna().any():
            df["p90_lo"] = self._transformer.inverse_transform_target(
                df["p90_lo"].fillna(df["forecast"]).values, c.target
            )
        if "p90_hi" in df.columns and df["p90_hi"].notna().any():
            df["p90_hi"] = self._transformer.inverse_transform_target(
                df["p90_hi"].fillna(df["forecast"]).values, c.target
            )
        return df

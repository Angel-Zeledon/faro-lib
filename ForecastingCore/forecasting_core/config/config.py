"""
SessionConfig — validated, serializable, reproducible session configuration.

Every run in forecasting_core is driven by a SessionConfig.
Configs are hashed for reproducibility and can be serialized to JSON.

Example:
    cfg = SessionConfig.from_dict({
        "data": {"path": "sales.csv"},
        "columns": {"target": "sales", "date": "date", "group_keys": ["sku", "store"]},
        "features": {"lags": [1, 7, 14], "rolling": [7, 14]},
        "models": {"lightgbm": {"n_estimators": 300}},
        "training": {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3},
        "forecast": {"horizon": 14},
    })
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# Sub-configs (nested dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class TransformConfig:
    """
    Per-column transform specifications applied before feature engineering.

    columns : dict of {column_name: spec_dict}
        Each spec_dict may contain:
            impute  : none | mean | median | mode | forward | interpolate | zero | smart
            encode  : none | label | one_hot | ordinal | binary | auto
            scale   : none | standard | minmax | robust | log | power
            log_offset : float  (default 1.0, used when scale="log")
            log_base   : float | None  (None = natural log)

    auto_apply : bool
        If True, auto-suggested transforms are applied without manual review.
        Suggested transforms are computed by ForecastEngine.get_transform_suggestions().

    Example:
        TransformConfig(columns={
            "sales":  {"impute": "median", "scale": "log"},
            "price":  {"scale": "minmax"},
            "region": {"encode": "label"},
        })
    """
    columns:    Dict[str, dict] = field(default_factory=dict)
    auto_apply: bool            = False


@dataclass
class DataConfig:
    path: str = ""
    sql_engine: str = ""
    encoding: str = "utf-8"
    date_freq: Optional[str] = None          # "D", "W", "MS", None=auto
    registry_path: str = "registry.json"


@dataclass
class ColumnsConfig:
    target: str = ""
    date: str = ""
    group_keys: List[str] = field(default_factory=lambda: ["sku", "store"])
    exogenous: List[str] = field(default_factory=list)



@dataclass
class FeaturesConfig:
    lags: List[int] = field(default_factory=lambda: [1, 7, 14])
    diffs: List[int] = field(default_factory=lambda: [1, 7])
    rolling: List[int] = field(default_factory=lambda: [7, 14, 28])
    calendar: bool = True
    ewm_spans: List[int] = field(default_factory=list)
    fourier_periods: List[int] = field(default_factory=list)  # e.g. [7, 30, 365]
    fourier_K: int = 2  # harmonics per period


@dataclass
class TrainingConfig:
    train_ratio: float = 0.8
    walk_forward: bool = True
    wfv_splits: int = 3
    min_history: int = 20
    seasonal_period: int = 7
    tuning: bool = False
    tuning_trials: int = 30


@dataclass
class ForecastConfig:
    horizon: int = 14
    quantiles: List[float] = field(default_factory=lambda: [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95])


@dataclass
class GranularityConfig:
    strategy: str = "native"            # "native" | "aggregate"
    target_freq: Optional[str] = None   # only meaningful when strategy == "aggregate"


@dataclass
class BusinessConfig:
    service_level: float = 0.95   # for safety stock z-score
    lead_time_days: int = 7
    holding_cost_pct: float = 0.20
    stockout_cost_multiplier: float = 3.0


@dataclass
class RoutingThresholdsConfig:
    intermittency: float = 0.4      # zero-ratio threshold for intermittent flag
    cv_volatile: float = 1.5        # coefficient-of-variation threshold for volatile flag
    seasonal_strength: float = 0.3  # STL seasonal-strength threshold for seasonal flag


@dataclass
class ModelRoutingConfig:
    enabled: bool = True          # False = run all models on all SKUs
    thresholds: RoutingThresholdsConfig = field(default_factory=RoutingThresholdsConfig)


# ---------------------------------------------------------------------------
# Main SessionConfig
# ---------------------------------------------------------------------------

@dataclass
class SessionConfig:
    """
    Complete configuration for one forecasting session.

    Attributes:
        name:     Unique session identifier.
        data:     Dataset source configuration.
        columns:  Column mapping (target, date, group_keys).
        features: Feature engineering parameters.
        models:   Dict of {model_name: hyperparameters}.
        training: Train/validation strategy.
        forecast: Prediction horizon and quantiles.
        business: Business rules for inventory recommendations.
        routing:  Model routing strategy.
        granularity: Data Alignment Wizard strategy (native vs. aggregate-resample).
    """

    name: str = "session_1"
    data: DataConfig = field(default_factory=DataConfig)
    columns: ColumnsConfig = field(default_factory=ColumnsConfig)
    transforms: TransformConfig = field(default_factory=TransformConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    models: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "lightgbm": {"n_estimators": 300, "learning_rate": 0.05},
    })
    training: TrainingConfig = field(default_factory=TrainingConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    business: BusinessConfig = field(default_factory=BusinessConfig)
    routing: ModelRoutingConfig = field(default_factory=ModelRoutingConfig)
    granularity: GranularityConfig = field(default_factory=GranularityConfig)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        cfg = cls(name=d.get("name", "session_1"))
        if "data" in d:
            cfg.data = DataConfig(**{k: v for k, v in d["data"].items()
                                     if k in DataConfig.__dataclass_fields__})
        if "transforms" in d:
            td = d["transforms"]
            cfg.transforms = TransformConfig(
                columns=td.get("columns", {}),
                auto_apply=td.get("auto_apply", False),
            )
        if "columns" in d:
            cfg.columns = ColumnsConfig(**{k: v for k, v in d["columns"].items()
                                           if k in ColumnsConfig.__dataclass_fields__})
        if "features" in d:
            cfg.features = FeaturesConfig(**{k: v for k, v in d["features"].items()
                                             if k in FeaturesConfig.__dataclass_fields__})
        if "models" in d:
            cfg.models = d["models"]
        if "training" in d:
            cfg.training = TrainingConfig(**{k: v for k, v in d["training"].items()
                                             if k in TrainingConfig.__dataclass_fields__})
        if "forecast" in d:
            cfg.forecast = ForecastConfig(**{k: v for k, v in d["forecast"].items()
                                             if k in ForecastConfig.__dataclass_fields__})
        if "business" in d:
            cfg.business = BusinessConfig(**{k: v for k, v in d["business"].items()
                                             if k in BusinessConfig.__dataclass_fields__})
        if "routing" in d:
            rd = d["routing"]
            thresholds = RoutingThresholdsConfig(
                **{k: v for k, v in rd.get("thresholds", {}).items()
                   if k in RoutingThresholdsConfig.__dataclass_fields__}
            ) if "thresholds" in rd else RoutingThresholdsConfig()
            cfg.routing = ModelRoutingConfig(
                enabled=rd.get("enabled", True),
                thresholds=thresholds,
            )
        if "granularity" in d:
            gd = d["granularity"]
            cfg.granularity = GranularityConfig(
                strategy=gd.get("strategy", "native"),
                target_freq=gd.get("target_freq"),
            )
        cfg.validate()
        return cfg

    @classmethod
    def from_json(cls, path: str) -> "SessionConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @property
    def hash(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()[:8]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self):
        if not self.columns.target:
            raise ConfigError("columns.target is required")
        if not self.columns.date:
            raise ConfigError("columns.date is required")
        if not isinstance(self.columns.group_keys, list) or len(self.columns.group_keys) == 0:
            raise ConfigError("columns.group_keys must be a non-empty list")
        if not self.models:
            raise ConfigError("At least one model must be defined in models")
        if not 0 < self.training.train_ratio < 1:
            raise ConfigError("training.train_ratio must be between 0 and 1")
        if self.forecast.horizon < 1:
            raise ConfigError("forecast.horizon must be >= 1")
        return self

    # ------------------------------------------------------------------
    # Schema for frontend — describes all configurable parameters
    # ------------------------------------------------------------------

    @staticmethod
    def schema() -> dict:
        """
        Returns the complete schema of all configurable parameters.
        Used by the API/frontend to build dynamic forms.
        """
        return {
            "training": {
                "train_ratio":    {"type": "float",   "default": 0.8,   "min": 0.5, "max": 0.95, "label": "Train ratio"},
                "walk_forward":   {"type": "bool",    "default": True,              "label": "Walk-forward validation"},
                "wfv_splits":     {"type": "int",     "default": 3,     "min": 1,   "label": "WFV splits"},
                "min_history":    {"type": "int",     "default": 20,    "min": 5,   "label": "Min history rows"},
                "seasonal_period":{"type": "int",     "default": 7,     "min": 2,   "label": "Seasonal period"},
                "tuning":         {"type": "bool",    "default": False,             "label": "Hyperparameter tuning (Optuna)"},
                "tuning_trials":  {"type": "int",     "default": 30,    "min": 5,   "label": "Tuning trials"},
            },
            "features": {
                "lags":    {"type": "int_list", "default": [1,7,14],    "label": "Lag periods"},
                "diffs":   {"type": "int_list", "default": [1,7],       "label": "Diff periods"},
                "rolling": {"type": "int_list", "default": [7,14,28],   "label": "Rolling windows"},
                "calendar":{"type": "bool",     "default": True,        "label": "Calendar features"},
                "ewm_spans":{"type":"int_list", "default": [],          "label": "EWM spans"},
            },
            "forecast": {
                "horizon":   {"type": "int",       "default": 14, "min": 1, "label": "Forecast horizon (days)"},
                "quantiles": {"type": "float_list","default": [0.5, 0.9, 0.95], "label": "Forecast quantiles"},
            },
            "business": {
                "service_level":            {"type": "float", "default": 0.95, "min": 0.5,  "max": 0.999, "label": "Service level"},
                "lead_time_days":           {"type": "int",   "default": 7,   "min": 1,                  "label": "Lead time (days)"},
                "holding_cost_pct":         {"type": "float", "default": 0.20,"min": 0.01,               "label": "Holding cost %"},
                "stockout_cost_multiplier": {"type": "float", "default": 3.0, "min": 1.0,               "label": "Stockout cost multiplier"},
            },
            "routing": {
                "enabled":   {"type": "bool",  "default": True,  "label": "Enable model routing by series type"},
                "thresholds": {
                    "intermittency":     {"type": "float", "default": 0.4,  "min": 0.1, "max": 0.9, "label": "Intermittency threshold (zero ratio)"},
                    "cv_volatile":       {"type": "float", "default": 1.5,  "min": 0.5,             "label": "CV threshold for volatile"},
                    "seasonal_strength": {"type": "float", "default": 0.3,  "min": 0.1, "max": 0.9, "label": "STL seasonal strength threshold"},
                },
            },
            "available_models": [
                "lightgbm", "xgboost", "arima", "sarimax", "prophet", "ets", "croston", "lstm"
            ],
            "transforms": {
                "auto_apply": {"type": "bool", "default": False, "label": "Auto-apply suggestions"},
                "column_spec": {
                    "impute":     {"type": "enum", "options": ["none","mean","median","mode","forward","interpolate","zero","smart"], "default": "none"},
                    "encode":     {"type": "enum", "options": ["none","label","one_hot","ordinal","binary","auto"], "default": "none"},
                    "scale":      {"type": "enum", "options": ["none","standard","minmax","robust","log","power"], "default": "none"},
                    "log_offset": {"type": "float", "default": 1.0, "label": "Log offset (scale=log only)"},
                    "log_base":   {"type": "float", "default": None, "label": "Log base (null = natural log)"},
                },
            },
        }

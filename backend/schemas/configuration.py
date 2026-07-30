from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, Any, List


class AttachDatasetRequest(BaseModel):
    dataset_id: str


class CovariateEntry(BaseModel):
    name: str
    type: str  # numeric | categorical | binary


class OutlierConfig(BaseModel):
    """Outlier treatment configuration — applied per-SKU before training."""
    strategy: str = "leave"
    # winsorize_sigma params
    n_sigma: float = 3.0
    # winsorize_pct params: clip bottom p% and top p%
    percentile: float = 1.0
    # iqr_fence params
    iqr_k: float = 1.5
    # per-SKU overrides (map: sku_name → strategy)
    per_sku_overrides: Dict[str, str] = {}
    per_sku_n_sigma: Dict[str, float] = {}
    per_sku_percentile: Dict[str, float] = {}
    per_sku_iqr_k: Dict[str, float] = {}


class ColumnsConfigRequest(BaseModel):
    date_column: str
    target_column: str
    sku_column: Optional[str] = None
    exogenous: List[str] = []
    transforms: Dict[str, Dict[str, str]] = {}  # {col: {impute, encode, scale}}
    gap_fill: Optional[str] = "leave"  # zero | mean | forward | interpolate | leave
    outlier_config: OutlierConfig = OutlierConfig()


class CanonicalColumnsRequest(BaseModel):
    """New canonical 14-field column mapping request."""
    canonical_mapping: Dict[str, Optional[str]] = {}
    defaults_override: Dict[str, Any] = {}

    def validate_required(self, available_columns: list[str]) -> None:
        """
        Raise HTTPException-compatible ValueError if required fields are missing
        or mapped to columns that don't exist.
        """
        from forecasting_core.data.canonical import REQUIRED_FIELDS
        errors: list[str] = []
        for field in REQUIRED_FIELDS:
            src = self.canonical_mapping.get(field)
            if not src:
                errors.append(f"'{field}' es requerido y no tiene columna mapeada.")
            elif src not in available_columns:
                errors.append(
                    f"'{field}' → columna '{src}' no existe en el archivo. "
                    f"Columnas disponibles: {', '.join(available_columns)}."
                )
        for field, src in self.canonical_mapping.items():
            if src and src not in available_columns and field not in REQUIRED_FIELDS:
                errors.append(
                    f"'{field}' → columna '{src}' no existe en el archivo."
                )
        if errors:
            raise ValueError("; ".join(errors))


class FeaturesConfigRequest(BaseModel):
    lags: List[int] = [1, 7, 14, 28]
    rolling: List[int] = [7, 14, 28]
    diffs: List[int] = [1]
    calendar: bool = True
    ewm_spans: List[int] = []
    fourier_periods: List[int] = []   # e.g. [7, 30, 365]
    fourier_K: int = 2                # harmonics per period


class ModelEntry(BaseModel):
    name: str
    params: Dict[str, Any] = {}


class ModelsConfigRequest(BaseModel):
    mode: str = "selected"  # selected | all
    selected_models: List[str] = []
    hyperparameters: Dict[str, Dict[str, Any]] = {}
    auto_select_best: bool = True
    selection_metric: str = "wape"


class ValidationConfigRequest(BaseModel):
    # Bounded here because the ENGINE bounds it: `SessionConfig.validate()` raises
    # ConfigError("training.train_ratio must be between 0 and 1"), and the runner
    # copies this value into the engine config verbatim. Unbounded, the wizard
    # accepted 1.5, stored it, reported success, and then the training job died on
    # a config the API had already blessed — a session sold that cannot train.
    train_ratio: float = Field(default=0.8, gt=0, lt=1)
    walk_forward: bool = True
    # The three below are NOT checked by `SessionConfig.validate()`, so the bounds
    # are the engine's own published ones — the ranges it declares as legal in
    # `validation/schema.py` `_RULES` and `SessionConfig.schema()`. Inventing a
    # tighter range here would reject configs the engine accepts, which is the same
    # class of lie as accepting ones it rejects.
    wfv_splits: int = Field(default=3, ge=1, le=10)
    min_history: int = Field(default=20, ge=5)
    seasonal_period: int = Field(default=7, ge=2)
    horizon: int = Field(default=14, ge=1)  # `SessionConfig.validate()`: horizon >= 1


_HORIZON_LIMITS = {"D": (1, 30), "W": (1, 12), "2W": (1, 6), "MS": (1, 12)}


class ForecastConfigRequest(BaseModel):
    horizon: int = 14
    quantiles: List[float] = [0.1, 0.9]
    horizon_mode: str = "unified"                            # "unified" | "segmented"
    horizon_by_freq: Optional[Dict[str, int]] = None         # {"D": 10, "W": 4, ...}

    @model_validator(mode="after")
    def _validate_horizon_by_freq(self):
        if self.horizon_by_freq:
            for freq, value in self.horizon_by_freq.items():
                bounds = _HORIZON_LIMITS.get(freq)
                if bounds is None:
                    raise ValueError(f"Unknown frequency bucket '{freq}'")
                lo, hi = bounds
                if not (lo <= value <= hi):
                    raise ValueError(f"horizon_by_freq['{freq}']={value} must be between {lo} and {hi}")
        return self


class BusinessConfigRequest(BaseModel):
    service_level: float = 0.95
    lead_time_days: int = 7
    holding_cost_pct: float = 0.20
    # The MILP optimizer (backend/inventory/optimizer_service.py) derives
    # stockout_cost = order_cost * stockout_cost_multiplier. A value < 1
    # would make stockout_cost < order_cost, at which point the solver finds
    # it mathematically cheaper to leave demand permanently unmet than to
    # ever place an order — silently zeroing out every recommendation.
    stockout_cost_multiplier: float = Field(default=3.0, ge=1.0)

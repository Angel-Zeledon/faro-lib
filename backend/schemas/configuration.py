from pydantic import BaseModel
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
    train_ratio: float = 0.8
    walk_forward: bool = True
    wfv_splits: int = 3
    min_history: int = 20
    seasonal_period: int = 7
    horizon: int = 14


class ForecastConfigRequest(BaseModel):
    horizon: int = 14
    quantiles: List[float] = [0.1, 0.9]


class BusinessConfigRequest(BaseModel):
    service_level: float = 0.95
    lead_time_days: int = 7
    holding_cost_pct: float = 0.20
    stockout_cost_multiplier: float = 3.0

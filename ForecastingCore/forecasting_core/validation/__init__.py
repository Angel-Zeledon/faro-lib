"""
forecasting_core.validation — four-layer validation and resilience system.

Layers:
  1. schema       — config structure and value ranges
  2. semantic     — config vs data consistency
  3. data_validator — per-SKU data quality
  4. compatibility  — model vs series suitability

Additional modules:
  leakage           — temporal, target, and group leakage detection
  auto_correct      — safe config and data auto-corrections
  pipeline_resilience — partial failure handling and model fallback chains
  exceptions        — full exception hierarchy
  modes             — ValidationMode enum and ValidationResult dataclass
"""

from forecasting_core.validation.exceptions import (
    ForecastLibraryError,
    SchemaValidationError,
    SemanticValidationError,
    DataValidationError,
    InsufficientDataError,
    DataLeakageError,
    CompatibilityError,
    ModelTrainingError,
    SKUTrainingError,
    ConfigurationError,
    PipelineError,
)
from forecasting_core.validation.modes import ValidationMode, ValidationResult
from forecasting_core.validation.schema import validate_schema
from forecasting_core.validation.semantic import validate_semantic
from forecasting_core.validation.data_validator import validate_data
from forecasting_core.validation.compatibility import check_model_compatibility
from forecasting_core.validation.leakage import detect_leakage
from forecasting_core.validation.auto_correct import (
    auto_correct_config,
    auto_correct_data,
    CorrectionLog,
)
from forecasting_core.validation.pipeline_resilience import (
    PartialResultCollector,
    FallbackChain,
    naive_forecast,
    seasonal_naive_forecast,
)

__all__ = [
    # Exceptions
    "ForecastLibraryError", "SchemaValidationError", "SemanticValidationError",
    "DataValidationError", "InsufficientDataError", "DataLeakageError",
    "CompatibilityError", "ModelTrainingError", "SKUTrainingError",
    "ConfigurationError", "PipelineError",
    # Modes
    "ValidationMode", "ValidationResult",
    # Validators
    "validate_schema", "validate_semantic", "validate_data",
    "check_model_compatibility", "detect_leakage",
    # Auto-correct
    "auto_correct_config", "auto_correct_data", "CorrectionLog",
    # Resilience
    "PartialResultCollector", "FallbackChain",
    "naive_forecast", "seasonal_naive_forecast",
]

"""
Exception hierarchy for forecasting_core.

All exceptions carry structured context for the API layer to serialize:
  error_id, severity, suggestions, possible_fixes, context.

Hierarchy:
    ForecastLibraryError
        SchemaValidationError
        SemanticValidationError
        DataValidationError
            InsufficientDataError
            DataLeakageError
        CompatibilityError
        ModelTrainingError
            SKUTrainingError
        ConfigurationError
        PipelineError
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


class ForecastLibraryError(Exception):
    """Base class for all forecasting_core errors."""

    def __init__(
        self,
        message: str,
        error_id: Optional[str] = None,
        severity: str = "error",
        suggestions: Optional[List[str]] = None,
        possible_fixes: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.error_id = error_id or self.__class__.__name__
        self.severity = severity  # "error" | "warning" | "info"
        self.suggestions = suggestions or []
        self.possible_fixes = possible_fixes or []
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "error_id": self.error_id,
            "message": str(self),
            "severity": self.severity,
            "suggestions": self.suggestions,
            "possible_fixes": self.possible_fixes,
            "context": self.context,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.error_id!r}: {self})"


# ---------------------------------------------------------------------------
# Validation layers
# ---------------------------------------------------------------------------

class SchemaValidationError(ForecastLibraryError):
    """Config key missing, wrong type, or out-of-range value."""


class SemanticValidationError(ForecastLibraryError):
    """Config is structurally valid but semantically impossible
    (e.g. target column not in data, horizon > series length)."""


class DataValidationError(ForecastLibraryError):
    """Data content fails validation (nulls, wrong dtype, etc.)."""


class InsufficientDataError(DataValidationError):
    """Series has fewer rows than the minimum required for training."""


class DataLeakageError(DataValidationError):
    """Potential data leakage detected in features or splits."""


class CompatibilityError(ForecastLibraryError):
    """A model is incompatible with the data characteristics of a SKU."""


# ---------------------------------------------------------------------------
# Training / pipeline
# ---------------------------------------------------------------------------

class ModelTrainingError(ForecastLibraryError):
    """A model failed to fit or predict."""


class SKUTrainingError(ModelTrainingError):
    """Training failed for a specific SKU (partial failure)."""

    def __init__(self, sku: str, model: str, cause: Exception, **kwargs):
        super().__init__(
            f"Training failed for SKU={sku!r} model={model!r}: {cause}",
            context={"sku": sku, "model": model, "cause": repr(cause)},
            **kwargs,
        )
        self.sku = sku
        self.model = model
        self.cause = cause


class ConfigurationError(ForecastLibraryError):
    """Engine called in wrong order or with conflicting config."""


class PipelineError(ForecastLibraryError):
    """Unrecoverable error during Pipeline.run()."""

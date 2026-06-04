"""Validation mode enum and result dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from forecasting_core.validation.exceptions import ForecastLibraryError


class ValidationMode(Enum):
    STRICT = "strict"        # any error raises immediately
    WARNING = "warning"      # errors become warnings, collected and returned
    AUTO_FIX = "auto_fix"    # attempt to correct problems automatically
    PERMISSIVE = "permissive" # log only, never raise


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ForecastLibraryError] = field(default_factory=list)
    warnings: List[ForecastLibraryError] = field(default_factory=list)
    corrections: List[dict] = field(default_factory=list)

    def raise_if_invalid(self):
        if not self.valid and self.errors:
            raise self.errors[0]

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "corrections": self.corrections,
        }

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(
            valid=self.valid and other.valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
            corrections=self.corrections + other.corrections,
        )

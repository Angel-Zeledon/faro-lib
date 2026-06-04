"""
forecastlib exception hierarchy.

Every exception carries a human-readable message that explains:
  1. What went wrong
  2. Why it went wrong
  3. How to fix it

This design makes the library self-documenting through its errors.
"""

from __future__ import annotations
from typing import List, Optional


class ForecastLibError(Exception):
    """Base class for all forecastlib errors."""

    def __init__(
        self,
        message: str,
        suggestions: Optional[List[str]] = None,
        context: Optional[dict] = None,
    ):
        self.suggestions = suggestions or []
        self.context = context or {}
        full_msg = self._format(message)
        super().__init__(full_msg)

    def _format(self, message: str) -> str:
        if not self.suggestions:
            return message
        lines = [message, "", "Suggested alternatives:"]
        for s in self.suggestions:
            lines.append(f"  • {s}")
        return "\n".join(lines)


class LoadError(ForecastLibError):
    """Failed to load data from a source."""


class SchemaError(ForecastLibError):
    """Schema inference or column-role assignment failed."""


class TransformError(ForecastLibError):
    """A data transformation was applied incorrectly."""


class ValidationError(ForecastLibError):
    """Data or configuration failed validation."""


class SelectionError(ForecastLibError):
    """Column selection is empty or invalid."""


class LeakageError(ForecastLibError):
    """Potential data leakage detected in a transformation."""


class ConfigurationError(ForecastLibError):
    """Engine or pipeline called in wrong order or with conflicting config."""


class InspectionError(ForecastLibError):
    """Could not inspect the dataset (e.g. no numeric columns for correlations)."""


class PipelineError(ForecastLibError):
    """Pipeline step failed during fit or transform."""


class ConnectionError(ForecastLibError):  # noqa: A001
    """Could not establish a database connection."""

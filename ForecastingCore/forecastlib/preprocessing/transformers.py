"""
Transformation registry — tracks all transformations applied to a Dataset.

Every time a transformation is applied, a TransformStep is added to the
Dataset's _transform_log.  This log is used by Pipeline to replay or
re-fit transformations on new data.
"""

from __future__ import annotations

import json
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# TransformStep — an individual recorded transformation
# ---------------------------------------------------------------------------

@dataclass
class TransformStep:
    """
    Represents one transformation in the pipeline.

    Attributes:
        name:     Human-readable label (e.g. "StandardScaler on ['price']").
        kind:     Machine-readable type (e.g. "scale.standard").
        columns:  Columns the transformation operates on.
        params:   Hyperparameters (e.g. {"feature_range": (0, 1)}).
        fitted:   Fitted sklearn-like transformer (has transform() method).
                  None for stateless transforms (lags, calendar, etc.).
    """

    name:    str
    kind:    str
    columns: List[str]
    params:  Dict[str, Any]         = field(default_factory=dict)
    fitted:  Optional[Any]          = field(default=None, repr=False)

    def is_stateful(self) -> bool:
        """True if this step has a fitted object that must be serialized."""
        return self.fitted is not None

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the fitted transformation to a new DataFrame."""
        if self.fitted is None:
            raise RuntimeError(
                f"Step '{self.name}' has no fitted transformer. "
                f"Call Pipeline.fit() before transform()."
            )
        df = df.copy()
        valid_cols = [c for c in self.columns if c in df.columns]
        if not valid_cols:
            return df
        df[valid_cols] = self.fitted.transform(df[valid_cols])
        return df

    def to_dict(self) -> dict:
        return {
            "name":    self.name,
            "kind":    self.kind,
            "columns": self.columns,
            "params":  self.params,
        }


# ---------------------------------------------------------------------------
# BaseTransformer — ABC for custom stateful transformers
# ---------------------------------------------------------------------------

class BaseTransformer(ABC):
    """Sklearn-compatible base for all stateful transformers."""

    @abstractmethod
    def fit(self, df: pd.DataFrame, columns: List[str]) -> "BaseTransformer":
        ...

    @abstractmethod
    def transform(self, df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
        ...

    def fit_transform(self, df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
        return self.fit(df, columns).transform(df, columns)


# ---------------------------------------------------------------------------
# TransformRegistry — ordered list of steps with fit/transform API
# ---------------------------------------------------------------------------

class TransformRegistry:
    """
    Ordered collection of TransformStep objects.

    This registry is attached to every Dataset and powers the Pipeline's
    fit/transform reproducibility.
    """

    def __init__(self):
        self._steps: List[TransformStep] = []

    def add(self, step: TransformStep) -> None:
        self._steps.append(step)

    @property
    def steps(self) -> List[TransformStep]:
        return list(self._steps)

    def __len__(self) -> int:
        return len(self._steps)

    def __repr__(self) -> str:
        if not self._steps:
            return "TransformRegistry(empty)"
        lines = ["TransformRegistry("]
        for i, s in enumerate(self._steps):
            lines.append(f"  [{i}] {s.name}")
        lines.append(")")
        return "\n".join(lines)

    def summary(self) -> List[dict]:
        return [s.to_dict() for s in self._steps]

    def copy(self) -> "TransformRegistry":
        import copy
        reg = TransformRegistry()
        reg._steps = copy.deepcopy(self._steps)
        return reg

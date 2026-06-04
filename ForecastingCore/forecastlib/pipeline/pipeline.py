"""
Pipeline — reproducible, serialisable transformation pipeline.

A Pipeline is created from a Dataset's transformation log and supports:
  * fit(df)           — fit stateful transformers on training data
  * transform(df)     — apply fitted transformers to new data
  * fit_transform(df) — fit and transform in one step
  * save(path)        — pickle to disk
  * load(path)        — restore from disk

Example
-------
    # Build and fit a pipeline from a processed Dataset
    pipeline = dataset.to_pipeline()
    pipeline.fit(train_df)

    # Apply the same transformations to test data
    test_processed = pipeline.transform(test_df)

    # Save for production
    pipeline.save("pipeline.pkl")

    # Restore later
    pipeline = Pipeline.load("pipeline.pkl")
    live_processed = pipeline.transform(live_df)
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

import pandas as pd

from forecastlib.data.schema import SchemaInfo
from forecastlib.exceptions import PipelineError
from forecastlib.preprocessing.transformers import TransformRegistry, TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class Pipeline:
    """
    Reproducible, serialisable transformation pipeline.

    Parameters
    ----------
    steps : list of TransformStep
        Ordered list of transformation steps.
    schema : SchemaInfo
        Semantic schema (target, datetime, group) of the fitted data.
    name : str, optional
        Human-readable pipeline name.

    Attributes
    ----------
    is_fitted : bool
        True after fit() has been called.
    """

    def __init__(
        self,
        steps: List[TransformStep],
        schema: Optional[SchemaInfo] = None,
        name: str = "Pipeline",
    ):
        self.steps    = list(steps)
        self.schema   = schema or SchemaInfo()
        self.name     = name
        self.is_fitted = any(s.is_stateful() for s in self.steps)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_registry(
        cls,
        registry: TransformRegistry,
        schema: Optional[SchemaInfo] = None,
    ) -> "Pipeline":
        """Build a Pipeline from a Dataset's TransformRegistry."""
        return cls(steps=registry.steps, schema=schema)

    @classmethod
    def from_dataset(cls, dataset: "Dataset") -> "Pipeline":
        """Build a Pipeline directly from a Dataset."""
        return cls.from_registry(dataset._registry, dataset._schema)

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> "Pipeline":
        """
        Fit all stateful steps (scalers, encoders) on the given DataFrame.

        Stateless steps (lags, calendar, etc.) are skipped.

        Parameters
        ----------
        df : pd.DataFrame
            Training data.

        Returns
        -------
        Pipeline
            Self, for chaining.
        """
        current = df.copy()

        for step in self.steps:
            if step.fitted is None:
                # Stateless: apply directly to advance the DataFrame state
                try:
                    current = self._apply_stateless(current, step)
                except Exception as e:
                    raise PipelineError(
                        f"Step '{step.name}' failed during fit: {e}",
                        context={"step": step.name, "kind": step.kind},
                    ) from e
            else:
                # Stateful: re-fit on current data
                try:
                    valid_cols = [c for c in step.columns if c in current.columns]
                    if valid_cols and hasattr(step.fitted, 'scaler'):
                        step.fitted.scaler.fit(current[valid_cols])
                    current = step.fitted.transform(current)
                except Exception as e:
                    raise PipelineError(
                        f"Stateful step '{step.name}' failed during fit: {e}",
                        context={"step": step.name, "kind": step.kind},
                    ) from e

        self.is_fitted = True
        return self

    # ------------------------------------------------------------------
    # Transform
    # ------------------------------------------------------------------

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply all fitted transformations to new data.

        Parameters
        ----------
        df : pd.DataFrame

        Returns
        -------
        pd.DataFrame
        """
        current = df.copy()

        for step in self.steps:
            try:
                if step.fitted is not None:
                    current = step.fitted.transform(current)
                else:
                    current = self._apply_stateless(current, step)
            except Exception as e:
                raise PipelineError(
                    f"Step '{step.name}' failed during transform: {e}",
                    context={"step": step.name},
                ) from e

        return current

    # ------------------------------------------------------------------
    # Fit + transform in one step
    # ------------------------------------------------------------------

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fit the pipeline and immediately transform the same data.

        Equivalent to pipeline.fit(df).transform(df) but slightly more
        efficient for stateful steps.
        """
        return self.fit(df).transform(df)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """
        Pickle the pipeline to disk.

        Parameters
        ----------
        path : str
            Output file path (e.g. "pipeline.pkl").

        >>> pipeline.save("models/preprocessing.pkl")
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        print(f"Pipeline saved to '{path}'.")

    @classmethod
    def load(cls, path: str) -> "Pipeline":
        """
        Load a saved Pipeline from disk.

        >>> pipeline = Pipeline.load("models/preprocessing.pkl")
        """
        path = Path(path)
        if not path.exists():
            raise PipelineError(f"Pipeline file not found: {path}")
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise PipelineError(f"File '{path}' does not contain a Pipeline object.")
        return obj

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def summary(self) -> None:
        """Print a human-readable summary of all steps."""
        print(f"\nPipeline: {self.name}  ({len(self.steps)} steps)")
        print("-" * 50)
        for i, step in enumerate(self.steps):
            stateful = " [fitted]" if step.is_stateful() else ""
            print(f"  [{i:02d}] {step.kind:<30} {step.name[:40]}{stateful}")
        print("-" * 50 + "\n")

    def __repr__(self) -> str:
        return f"Pipeline('{self.name}', steps={len(self.steps)}, fitted={self.is_fitted})"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_stateless(self, df: pd.DataFrame, step: TransformStep) -> pd.DataFrame:
        """Re-apply a stateless transformation to a new DataFrame."""
        kind = step.kind

        # Lags
        if kind == "ts.lags":
            from forecastlib.time_series.features import _apply_lags
            # Reconstruct a minimal Dataset-like object
            tmp = _StatelessDataset(df, self.schema)
            _apply_lags(tmp, step.columns, step.params["periods"])
            return tmp._df

        # Diffs
        if kind == "ts.diffs":
            from forecastlib.time_series.features import _apply_diffs
            tmp = _StatelessDataset(df, self.schema)
            _apply_diffs(tmp, step.columns, step.params["periods"])
            return tmp._df

        # EWM
        if kind == "ts.ewm":
            from forecastlib.time_series.features import _apply_ewm
            tmp = _StatelessDataset(df, self.schema)
            _apply_ewm(tmp, step.columns, step.params["spans"])
            return tmp._df

        # Rolling
        if kind.startswith("ts.rolling."):
            fn = kind.split(".")[-1]
            from forecastlib.time_series.features import RollingAccessor
            tmp = _StatelessDataset(df, self.schema)
            RollingAccessor(tmp, step.columns)._apply(step.params["windows"], fn)
            return tmp._df

        # Calendar
        if kind == "ts.calendar":
            from forecastlib.time_series.calendar import FeaturesAccessor
            tmp = _StatelessDataset(df, self.schema)
            FeaturesAccessor(tmp, step.columns).calendar()
            return tmp._df

        # Holiday
        if kind == "ts.holidays":
            from forecastlib.time_series.calendar import FeaturesAccessor
            tmp = _StatelessDataset(df, self.schema)
            FeaturesAccessor(tmp, step.columns).holidays(country=step.params.get("country", "CO"))
            return tmp._df

        # Fill (stateless variants)
        if kind == "fill.forward":
            from forecastlib.preprocessing.missing import FillAccessor
            tmp = _StatelessDataset(df, self.schema)
            FillAccessor(tmp, step.columns).forward(step.params.get("limit"))
            return tmp._df

        if kind == "fill.smart" or kind == "fill.time_series":
            from forecastlib.preprocessing.missing import FillAccessor
            tmp = _StatelessDataset(df, self.schema)
            getattr(FillAccessor(tmp, step.columns), kind.split(".")[-1])()
            return tmp._df

        # Clean operations
        if kind == "clean.drop_duplicates":
            return df.drop_duplicates(
                subset=step.params.get("subset"),
                keep=step.params.get("keep", "first"),
            ).reset_index(drop=True)

        if kind == "clean.sort":
            sort_by = step.params.get("by", [])
            if sort_by and all(c in df.columns for c in sort_by):
                return df.sort_values(sort_by).reset_index(drop=True)
            return df

        if kind == "clean.rename":
            return df.rename(columns=step.params.get("mapping", {}))

        # Clip
        if kind == "clean.clip":
            df = df.copy()
            for col in step.columns:
                if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].clip(
                        lower=step.params.get("lower"),
                        upper=step.params.get("upper"),
                    )
            return df

        # Log scale (stateless)
        if kind == "scale.log":
            import numpy as np
            df = df.copy()
            base   = step.params.get("base")
            offset = step.params.get("offset", 1.0)
            for col in step.columns:
                if col in df.columns:
                    vals = df[col].astype(float) + offset
                    df[col] = np.log(vals) if base is None else np.log(vals) / np.log(base)
            return df

        # One-hot encoding (stateless)
        if kind == "encode.one_hot":
            drop_first = step.params.get("drop_first", False)
            for col in step.columns:
                if col not in df.columns:
                    continue
                dummies = pd.get_dummies(df[col], prefix=col, drop_first=drop_first, dtype=int)
                df = pd.concat([df.drop(columns=[col]), dummies], axis=1)
            return df

        # Default: return unchanged
        return df


class _StatelessDataset:
    """Minimal Dataset-like object for stateless pipeline replay."""

    def __init__(self, df: pd.DataFrame, schema: SchemaInfo):
        self._df     = df.copy()
        self._schema = schema
        from forecastlib.preprocessing.transformers import TransformRegistry
        self._registry = TransformRegistry()

    def _mutate(self, new_df: pd.DataFrame, step: TransformStep) -> "_StatelessDataset":
        self._df = new_df
        return self

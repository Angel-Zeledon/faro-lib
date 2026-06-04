"""
DataTransformer — applies per-column transforms (impute → encode → scale)
and supports inverse-transforming the target for forecast de-normalization.

Transform order per column: impute → encode → scale

Serializable: the DataTransformer instance is stored alongside fitted models
in the session's joblib payload so predict() can correctly invert target scaling.

Example:
    transformer = DataTransformer({
        "sales":  {"impute": "median", "scale": "log"},
        "price":  {"scale": "minmax"},
        "region": {"encode": "label"},
        "demand": {"impute": "smart", "scale": "standard"},
    })
    df_clean = transformer.fit_transform(df)

    # After model training + forecasting:
    raw_fc   = model.predict(...)
    real_fc  = transformer.inverse_transform_target(raw_fc, "sales")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Valid strategy names — validated in configure_transforms()
VALID_IMPUTE  = {"none", "mean", "median", "mode", "forward", "interpolate", "zero", "smart"}
VALID_ENCODE  = {"none", "label", "one_hot", "ordinal", "binary", "auto"}
VALID_SCALE   = {"none", "standard", "minmax", "robust", "log", "power"}


class DataTransformer:
    """
    Stateful per-column transformer for the forecasting pipeline.

    Parameters
    ----------
    specs : dict
        {column_name: {impute, encode, scale, log_offset, log_base}}

        impute  : none | mean | median | mode | forward | interpolate | zero | smart
        encode  : none | label | one_hot | ordinal | binary | auto
        scale   : none | standard | minmax | robust | log | power
        log_offset: float (default 1.0) — added before log to ensure x > 0
        log_base  : float | None  — None = natural log
    """

    def __init__(self, specs: Dict[str, dict]):
        self._specs: Dict[str, dict] = {col: dict(s) for col, s in specs.items()}
        self._fitted_scalers:  Dict[str, Any] = {}   # col → sklearn scaler or log params
        self._fitted_encoders: Dict[str, Any] = {}   # col → sklearn encoder or dummy col list
        self._impute_values:   Dict[str, Any] = {}   # col → fill value stored at fit time
        self._new_cols_map:    Dict[str, List[str]] = {}  # col → expanded col names (one_hot/binary)
        self._is_fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fit on df, apply all transforms, return transformed DataFrame.
        Stores all fitted state for later use in transform() and inverse_transform_target().
        """
        df = df.copy()
        for col, spec in self._specs.items():
            if col not in df.columns:
                log.debug(f"DataTransformer: '{col}' not found in DataFrame — skipping.")
                continue
            impute = spec.get("impute", "none")
            encode = spec.get("encode", "none")
            scale  = spec.get("scale",  "none")
            if impute != "none":
                df = self._fit_impute(df, col, impute)
            if encode != "none":
                df = self._fit_encode(df, col, encode)
            # col may have been removed by one_hot/binary encoding
            if scale != "none" and col in df.columns:
                df = self._fit_scale(df, col, scale, spec)
        self._is_fitted = True
        return df

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply pre-fitted transforms to new data (e.g. for inference on test/future rows).
        Must call fit_transform() first.
        """
        if not self._is_fitted:
            raise RuntimeError("DataTransformer: call fit_transform() before transform().")
        df = df.copy()
        for col, spec in self._specs.items():
            if col not in df.columns:
                continue
            impute = spec.get("impute", "none")
            encode = spec.get("encode", "none")
            scale  = spec.get("scale",  "none")
            if impute != "none":
                df = self._apply_impute(df, col, impute)
            if encode != "none":
                df = self._apply_encode(df, col, encode)
            if scale != "none" and col in df.columns and col in self._fitted_scalers:
                df = self._apply_scale(df, col, scale)
        return df

    def inverse_transform_target(self, values, target_col: str) -> np.ndarray:
        """
        Undo scaling on target column forecast values.

        Call this on raw model output before returning forecasts to the user
        whenever the target column was scaled during training.

        Parameters
        ----------
        values     : array-like of scaled forecast values
        target_col : name of the target column

        Returns
        -------
        np.ndarray in original scale
        """
        arr = np.asarray(values, dtype=float)
        if target_col not in self._fitted_scalers:
            return arr
        scale_kind = self._specs.get(target_col, {}).get("scale", "none")
        fitted = self._fitted_scalers[target_col]
        if scale_kind == "log":
            offset = fitted.get("offset", 1.0)
            base   = fitted.get("base")
            result = np.exp(arr) - offset if base is None else (base ** arr) - offset
            return np.maximum(result, 0.0)
        if hasattr(fitted, "inverse_transform"):
            return fitted.inverse_transform(arr.reshape(-1, 1)).ravel()
        return arr

    def has_target_scaling(self, target_col: str) -> bool:
        """True if the target was scaled and forecast output needs inverse transform."""
        spec = self._specs.get(target_col, {})
        return spec.get("scale", "none") != "none" and target_col in self._fitted_scalers

    def fitted_columns(self, original_cols: List[str]) -> List[str]:
        """
        Return the column list after transforms that may add/remove columns
        (one_hot and binary encoding expand a single col into several).
        """
        result = []
        for col in original_cols:
            if col in self._new_cols_map:
                result.extend(self._new_cols_map[col])
            elif col not in {c for spec_cols in self._new_cols_map.values()
                             for c in spec_cols}:
                result.append(col)
        return result

    def to_dict(self) -> dict:
        """Serialise specs (not the fitted objects — those are included in joblib payload)."""
        return {"specs": self._specs, "is_fitted": self._is_fitted}

    # ------------------------------------------------------------------
    # Imputation — fit
    # ------------------------------------------------------------------

    def _fit_impute(self, df: pd.DataFrame, col: str, strategy: str) -> pd.DataFrame:
        s = df[col]
        if strategy == "mean" and pd.api.types.is_numeric_dtype(s):
            val = float(s.mean())
            self._impute_values[col] = val
            df[col] = s.fillna(val)
        elif strategy == "median" and pd.api.types.is_numeric_dtype(s):
            val = float(s.median())
            self._impute_values[col] = val
            df[col] = s.fillna(val)
        elif strategy in ("mode", "smart"):
            if pd.api.types.is_numeric_dtype(s) and strategy == "smart":
                null_rate = s.isna().mean()
                if null_rate < 0.05:
                    val = float(s.median())
                else:
                    df[col] = s.interpolate(method="linear").ffill().bfill()
                    self._impute_values[col] = float(s.median())
                    return df
                self._impute_values[col] = val
                df[col] = s.fillna(val)
            elif pd.api.types.is_datetime64_any_dtype(s):
                df[col] = s.ffill()
            else:
                mode_val = s.mode()
                val = mode_val.iloc[0] if len(mode_val) > 0 else ""
                self._impute_values[col] = val
                df[col] = s.fillna(val)
        elif strategy == "zero":
            self._impute_values[col] = 0
            df[col] = s.fillna(0)
        elif strategy == "forward":
            df[col] = s.ffill().bfill()
        elif strategy == "interpolate" and pd.api.types.is_numeric_dtype(s):
            df[col] = s.interpolate(method="linear").ffill().bfill()
        return df

    # ------------------------------------------------------------------
    # Imputation — apply (inference)
    # ------------------------------------------------------------------

    def _apply_impute(self, df: pd.DataFrame, col: str, strategy: str) -> pd.DataFrame:
        s = df[col]
        if col in self._impute_values:
            df[col] = s.fillna(self._impute_values[col])
        elif strategy == "forward":
            df[col] = s.ffill().bfill()
        elif strategy == "interpolate" and pd.api.types.is_numeric_dtype(s):
            df[col] = s.interpolate(method="linear").ffill().bfill()
        return df

    # ------------------------------------------------------------------
    # Encoding — fit
    # ------------------------------------------------------------------

    def _fit_encode(self, df: pd.DataFrame, col: str, strategy: str) -> pd.DataFrame:
        n = df[col].nunique()
        if strategy == "auto":
            if n <= 15:
                strategy = "one_hot"
            elif n <= 200:
                strategy = "label"
            else:
                strategy = "binary"

        if strategy == "label":
            from sklearn.preprocessing import LabelEncoder
            le = LabelEncoder()
            df[col] = le.fit_transform(df[col].astype(str))
            self._fitted_encoders[col] = le

        elif strategy == "one_hot":
            dummies = pd.get_dummies(df[col], prefix=col, dtype=int)
            new_cols = dummies.columns.tolist()
            self._new_cols_map[col] = new_cols
            self._fitted_encoders[col] = new_cols
            df = pd.concat([df.drop(columns=[col]), dummies], axis=1)

        elif strategy == "ordinal":
            from sklearn.preprocessing import OrdinalEncoder
            oe = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
            df[[col]] = oe.fit_transform(df[[col]].astype(str))
            self._fitted_encoders[col] = oe

        elif strategy == "binary":
            codes = df[col].astype("category").cat.codes
            n_bits = 8
            new_cols = [f"{col}_bin{b}" for b in range(n_bits)]
            for bit in range(n_bits):
                df[f"{col}_bin{bit}"] = ((codes >> bit) & 1).astype(int)
            self._new_cols_map[col] = new_cols
            self._fitted_encoders[col] = new_cols
            df = df.drop(columns=[col])

        return df

    # ------------------------------------------------------------------
    # Encoding — apply (inference)
    # ------------------------------------------------------------------

    def _apply_encode(self, df: pd.DataFrame, col: str, strategy: str) -> pd.DataFrame:
        fitted = self._fitted_encoders.get(col)
        if fitted is None:
            return df

        if strategy in ("label", "auto") and hasattr(fitted, "transform"):
            known = set(fitted.classes_)
            df[col] = df[col].astype(str).map(
                lambda x: x if x in known else fitted.classes_[0]
            )
            df[col] = fitted.transform(df[col].astype(str))

        elif strategy in ("one_hot", "auto") and isinstance(fitted, list):
            dummies = pd.get_dummies(df[col], prefix=col, dtype=int)
            for c in fitted:
                if c not in dummies.columns:
                    dummies[c] = 0
            df = pd.concat([df.drop(columns=[col]), dummies[fitted]], axis=1)

        elif strategy == "binary" and isinstance(fitted, list):
            codes = df[col].astype("category").cat.codes
            for bit, new_col in enumerate(fitted):
                df[new_col] = ((codes >> bit) & 1).astype(int)
            df = df.drop(columns=[col])

        elif strategy == "ordinal" and hasattr(fitted, "transform"):
            df[[col]] = fitted.transform(df[[col]].astype(str))

        return df

    # ------------------------------------------------------------------
    # Scaling — fit
    # ------------------------------------------------------------------

    def _fit_scale(self, df: pd.DataFrame, col: str, scale: str, spec: dict) -> pd.DataFrame:
        if scale == "standard":
            from sklearn.preprocessing import StandardScaler
            sc = StandardScaler()
            df[[col]] = sc.fit_transform(df[[col]])
            self._fitted_scalers[col] = sc

        elif scale == "minmax":
            from sklearn.preprocessing import MinMaxScaler
            sc = MinMaxScaler()
            df[[col]] = sc.fit_transform(df[[col]])
            self._fitted_scalers[col] = sc

        elif scale == "robust":
            from sklearn.preprocessing import RobustScaler
            sc = RobustScaler()
            df[[col]] = sc.fit_transform(df[[col]])
            self._fitted_scalers[col] = sc

        elif scale == "power":
            from sklearn.preprocessing import PowerTransformer
            pt = PowerTransformer(method="yeo-johnson", standardize=True)
            df[[col]] = pt.fit_transform(df[[col]])
            self._fitted_scalers[col] = pt

        elif scale == "log":
            offset = float(spec.get("log_offset", 1.0))
            base   = spec.get("log_base")
            vals   = df[col].astype(float) + offset
            df[col] = np.log(vals) if base is None else np.log(vals) / np.log(base)
            self._fitted_scalers[col] = {"offset": offset, "base": base}

        return df

    # ------------------------------------------------------------------
    # Scaling — apply (inference)
    # ------------------------------------------------------------------

    def _apply_scale(self, df: pd.DataFrame, col: str, scale: str) -> pd.DataFrame:
        fitted = self._fitted_scalers.get(col)
        if fitted is None:
            return df
        if scale == "log":
            offset = fitted.get("offset", 1.0)
            base   = fitted.get("base")
            vals   = df[col].astype(float) + offset
            df[col] = np.log(vals) if base is None else np.log(vals) / np.log(base)
        elif hasattr(fitted, "transform"):
            df[[col]] = fitted.transform(df[[col]])
        return df

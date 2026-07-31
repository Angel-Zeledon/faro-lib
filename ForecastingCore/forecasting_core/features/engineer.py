"""
FeatureEngineer — transforms raw data into ML-ready features.

Order of operations (matters for leakage prevention):
  1. Calendar features (no leakage — only uses date column)
  2. Lag features      (shift-based, no leakage)
  3. Rolling features  (shift(1) before rolling, no leakage)

Example:
    from forecasting_core.config.config import FeaturesConfig
    engineer = FeatureEngineer(FeaturesConfig(lags=[1,7], rolling=[7,14]))
    df_features = engineer.transform(df)
"""

import numpy as np
import pandas as pd
from typing import List, Optional

from forecasting_core.features.calendar import (
    CALENDAR_COLUMNS, HolidayCalendar, calendar_frame, fourier_frame,
)


class FeatureEngineer:

    def __init__(self, features_config, dt_col: str = "date",
                 target: str = "sales",
                 group_cols: Optional[List[str]] = None):
        if group_cols is None:
            group_cols = []
        self._group_cols = group_cols
        self.cfg    = features_config
        self.dt_col = dt_col
        self.target = target
        # The predictor builds future rows from this same calendar, so a holiday
        # is a holiday on both sides of the forecast boundary.
        self.calendar = HolidayCalendar(getattr(features_config, "holiday_country", None))

    def _groupby(self, df: pd.DataFrame):
        """Return a DataFrameGroupBy using the configured group columns, or None."""
        if not self._group_cols:
            return None
        if len(self._group_cols) == 1:
            return df.groupby(self._group_cols[0])
        return df.groupby(self._group_cols)

    def transform(self, df: pd.DataFrame, drop_warmup: bool = True) -> pd.DataFrame:
        """
        Build the feature frame.

        `drop_warmup=False` keeps the rows whose lag/rolling features are still
        NaN because the series has not run long enough to fill the longest
        window. That matters for one caller specifically: a series shorter than
        the widest lag loses EVERY row to the warm-up drop — a 24-bucket SKU
        against a `lag_28` config produces an empty frame — and the global
        cross-learning model exists precisely to serve those series. Discarding
        them before it runs removes the model's whole reason for being.

        Keeping them is safe for gradient-boosted trees, which learn a split
        direction for missing values natively rather than needing them filled.
        Anything that cannot handle NaN must keep the default.
        """
        if self.target in df.columns and not pd.api.types.is_numeric_dtype(df[self.target]):
            raise TypeError(
                f"Target column '{self.target}' must be numeric, "
                f"got dtype '{df[self.target].dtype}'. "
                f"Cast it first: df['{self.target}'] = pd.to_numeric(df['{self.target}'], errors='coerce')"
            )
        df = df.copy()
        input_cols = set(df.columns)
        if self.cfg.calendar:
            df = self._calendar(df)
        fourier_periods = getattr(self.cfg, "fourier_periods", [])
        fourier_K = getattr(self.cfg, "fourier_K", 2)
        if fourier_periods:
            df = self._fourier(df, fourier_periods, fourier_K)
        df = self._lags(df)
        df = self._rolling(df)
        if self.cfg.ewm_spans:
            df = self._ewm(df)
        df = df.replace([float("inf"), -float("inf")], None)

        # Drop only the warm-up rows where GENERATED features (lags/rollings)
        # are NaN — never rows that are NaN in pass-through input columns.
        # A blanket dropna() emptied the whole dataset whenever the input had
        # an all-NaN column (e.g. unmapped canonical fields like 'price'),
        # which silently disabled every ML model.
        generated = [c for c in df.columns if c not in input_cols]
        all_nan = [c for c in generated if df[c].isna().all()]
        if all_nan:
            # e.g. days_to_holiday when the holidays lib is unavailable:
            # carries no signal — drop the column, not every row.
            df = df.drop(columns=all_nan)
        if not drop_warmup:
            # The target itself still has to be known — a row with no observed
            # value is not a training example, it is a gap.
            return df.dropna(subset=[self.target]) if self.target in df.columns else df

        subset = [c for c in generated if c not in all_nan]
        if self.target in df.columns:
            subset.append(self.target)
        df = df.dropna(subset=subset) if subset else df
        return df

    def _calendar(self, df):
        """Attach the shared calendar features (see features/calendar.py).

        Deliberately NOT a private reimplementation: the predictor builds future
        rows from the same `calendar_frame`, and the only way those two can stay
        in agreement is by being the same code.
        """
        frame = calendar_frame(df[self.dt_col], self.calendar)
        frame.index = df.index
        for col in CALENDAR_COLUMNS:
            df[col] = frame[col]
        return df

    def _lags(self, df):
        g = self._groupby(df)
        for l in self.cfg.lags:
            df[f"lag_{l}"] = (g[self.target].shift(l) if g is not None
                              else df[self.target].shift(l))
        # Diffs are taken on the shift(1) series: an unshifted diff contains the
        # row's own target (y[t] = lag_1[t] + diff_1[t]), which hands the model
        # the answer and makes forecasts extrapolate the last observed slope.
        for d in self.cfg.diffs:
            if g is not None:
                df[f"diff_{d}"] = g[self.target].transform(
                    lambda x: x.shift(1).diff(d)
                )
                df[f"pct_change_{d}"] = g[self.target].transform(
                    lambda x: x.shift(1).pct_change(d)
                )
            else:
                shifted = df[self.target].shift(1)
                df[f"diff_{d}"]       = shifted.diff(d)
                df[f"pct_change_{d}"] = shifted.pct_change(d)
        return df

    def _rolling(self, df):
        grp = self._groupby(df)
        for w in self.cfg.rolling:
            shifted = (df[self.target].shift(1) if grp is None
                       else grp[self.target].shift(1))
            def _roll(fn):
                if grp is None:
                    return shifted.rolling(w, min_periods=1).__getattribute__(fn)()
                return grp[self.target].transform(
                    lambda x: x.shift(1).rolling(w, min_periods=1).__getattribute__(fn)()
                )
            df[f"roll_mean_{w}"] = _roll("mean")
            df[f"roll_std_{w}"]  = _roll("std")
            df[f"roll_min_{w}"]  = _roll("min")
            df[f"roll_max_{w}"]  = _roll("max")
            df[f"cv_{w}"] = df[f"roll_std_{w}"] / (df[f"roll_mean_{w}"].abs() + 1e-9)
        return df

    def _ewm(self, df):
        grp = self._groupby(df)
        for span in self.cfg.ewm_spans:
            if grp is None:
                df[f"ewm_{span}"] = df[self.target].shift(1).ewm(span=span).mean()
            else:
                df[f"ewm_{span}"] = grp[self.target].transform(
                    lambda x: x.shift(1).ewm(span=span).mean()
                )
        return df

    def _fourier(self, df: pd.DataFrame, periods: list, K: int) -> pd.DataFrame:
        """Fourier terms for cyclic seasonality. No leakage — only uses date column."""
        frame = fourier_frame(df[self.dt_col], periods, K)
        frame.index = df.index
        for col in frame.columns:
            df[col] = frame[col]
        return df

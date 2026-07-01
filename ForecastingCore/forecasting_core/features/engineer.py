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

    def _groupby(self, df: pd.DataFrame):
        """Return a DataFrameGroupBy using the configured group columns, or None."""
        if not self._group_cols:
            return None
        if len(self._group_cols) == 1:
            return df.groupby(self._group_cols[0])
        return df.groupby(self._group_cols)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.target in df.columns and not pd.api.types.is_numeric_dtype(df[self.target]):
            raise TypeError(
                f"Target column '{self.target}' must be numeric, "
                f"got dtype '{df[self.target].dtype}'. "
                f"Cast it first: df['{self.target}'] = pd.to_numeric(df['{self.target}'], errors='coerce')"
            )
        df = df.copy()
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
        df = df.replace([float("inf"), -float("inf")], None).dropna()
        return df

    @staticmethod
    def _easter_date(year: int) -> pd.Timestamp:
        """Anonymous Gregorian algorithm for Easter Sunday."""
        a = year % 19
        b, c = divmod(year, 100)
        d, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = divmod(c, 4)
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = (h + l - 7 * m + 114) % 31 + 1
        return pd.Timestamp(year=year, month=month, day=day)

    def _calendar(self, df):
        dt = pd.to_datetime(df[self.dt_col])
        df["year"]       = dt.dt.year
        df["month"]      = dt.dt.month
        df["day"]        = dt.dt.day
        df["dow"]        = dt.dt.dayofweek
        df["week"]       = dt.dt.isocalendar().week.astype(int)
        df["quarter"]    = dt.dt.quarter
        df["is_weekend"] = (dt.dt.dayofweek >= 5).astype(int)
        df["sin_month"]  = np.sin(2 * np.pi * dt.dt.month / 12)
        df["cos_month"]  = np.cos(2 * np.pi * dt.dt.month / 12)
        df["sin_dow"]    = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
        df["cos_dow"]    = np.cos(2 * np.pi * dt.dt.dayofweek / 7)

        years = dt.dt.year.unique()

        # Colombia national holidays
        holiday_dates: set = set()
        try:
            import holidays as hol_lib
            col_hols = hol_lib.Colombia(years=years)
            holiday_dates = set(pd.to_datetime(list(col_hols.keys())))
        except Exception:
            pass

        easter_dates = {self._easter_date(y) for y in years}
        xmas_dates   = {pd.Timestamp(year=y, month=12, day=25) for y in years}
        normalized   = dt.dt.normalize()

        df["is_holiday"]   = normalized.isin(holiday_dates).astype(int)
        df["is_easter"]    = normalized.isin(easter_dates).astype(int)
        df["is_christmas"] = normalized.isin(xmas_dates).astype(int)

        def _min_days(x, dates):
            return min(abs((x - d).days) for d in dates) if dates else np.nan

        df["days_to_holiday"] = dt.apply(lambda x: _min_days(x, holiday_dates))
        df["days_to_easter"]  = dt.apply(lambda x: _min_days(x, easter_dates))
        df["days_to_xmas"]    = dt.apply(lambda x: _min_days(x, xmas_dates))

        # composite holiday pressure score
        df["holiday_intensity"] = df["is_holiday"] * 3 + df["is_easter"] * 5 + df["is_christmas"] * 5
        df["christmas_season"]  = dt.dt.month.isin([11, 12]).astype(int)
        return df

    def _lags(self, df):
        g = self._groupby(df)
        for l in self.cfg.lags:
            df[f"lag_{l}"] = (g[self.target].shift(l) if g is not None
                              else df[self.target].shift(l))
        for d in self.cfg.diffs:
            df[f"diff_{d}"]       = (g[self.target].diff(d)       if g is not None
                                     else df[self.target].diff(d))
            df[f"pct_change_{d}"] = (g[self.target].pct_change(d) if g is not None
                                     else df[self.target].pct_change(d))
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
        dt = pd.to_datetime(df[self.dt_col])
        t = (dt - dt.min()).dt.days.values.astype(float)
        for period in periods:
            for k in range(1, K + 1):
                angle = 2 * np.pi * k * t / period
                df[f"fourier_sin_{period}_{k}"] = np.sin(angle)
                df[f"fourier_cos_{period}_{k}"] = np.cos(angle)
        return df

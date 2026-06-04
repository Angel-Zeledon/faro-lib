"""
Calendar and holiday feature generation.

Accessed via:
    dataset.datetime().features.calendar()
    dataset.datetime().features.holidays(country="CO")
    dataset.datetime().features.all(country="CO")
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class FeaturesAccessor:
    """
    Datetime-based feature generation.

    Accessed via ``ColumnView.features`` (where ColumnView contains datetime columns):

        dataset.datetime().features.calendar()
        dataset.datetime().features.holidays(country="CO")
        dataset.datetime().features.all(country="CO")
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = columns

    def _dt_columns(self) -> List[str]:
        df  = self._dataset._df
        return [c for c in self._columns
                if c in df.columns and pd.api.types.is_datetime64_any_dtype(df[c])]

    # ------------------------------------------------------------------
    # Calendar features
    # ------------------------------------------------------------------

    def calendar(self) -> "Dataset":
        """
        Add calendar-based features for each datetime column.

        Generated features (prefix = column name):
          year, month, day, day_of_week, day_of_year, week_of_year,
          quarter, is_weekend, is_month_start, is_month_end,
          sin_month, cos_month, sin_dow, cos_dow

        >>> dataset.datetime().features.calendar()
        """
        dt_cols   = self._dt_columns()
        df        = self._dataset._df.copy()
        new_feats = []

        for col in dt_cols:
            dt = pd.to_datetime(df[col])
            p  = f"{col}_"  # feature prefix

            df[f"{p}year"]         = dt.dt.year.astype("int16")
            df[f"{p}month"]        = dt.dt.month.astype("int8")
            df[f"{p}day"]          = dt.dt.day.astype("int8")
            df[f"{p}dow"]          = dt.dt.dayofweek.astype("int8")
            df[f"{p}doy"]          = dt.dt.day_of_year.astype("int16")
            df[f"{p}week"]         = dt.dt.isocalendar().week.astype("int8")
            df[f"{p}quarter"]      = dt.dt.quarter.astype("int8")
            df[f"{p}is_weekend"]   = (dt.dt.dayofweek >= 5).astype("int8")
            df[f"{p}is_month_start"] = dt.dt.is_month_start.astype("int8")
            df[f"{p}is_month_end"]   = dt.dt.is_month_end.astype("int8")

            # Cyclical encodings — preserve periodicity for ML
            df[f"{p}sin_month"] = np.sin(2 * np.pi * dt.dt.month / 12).astype("float32")
            df[f"{p}cos_month"] = np.cos(2 * np.pi * dt.dt.month / 12).astype("float32")
            df[f"{p}sin_dow"]   = np.sin(2 * np.pi * dt.dt.dayofweek / 7).astype("float32")
            df[f"{p}cos_dow"]   = np.cos(2 * np.pi * dt.dt.dayofweek / 7).astype("float32")
            df[f"{p}sin_doy"]   = np.sin(2 * np.pi * dt.dt.day_of_year / 365).astype("float32")
            df[f"{p}cos_doy"]   = np.cos(2 * np.pi * dt.dt.day_of_year / 365).astype("float32")

            new_feats += [f"{p}{s}" for s in [
                "year","month","day","dow","doy","week","quarter",
                "is_weekend","is_month_start","is_month_end",
                "sin_month","cos_month","sin_dow","cos_dow","sin_doy","cos_doy",
            ]]

        step = TransformStep(
            name=f"calendar features on {dt_cols} ({len(new_feats)} new cols)",
            kind="ts.calendar",
            columns=dt_cols,
            params={"new_features": new_feats},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Holiday features
    # ------------------------------------------------------------------

    def holidays(self, country: str = "CO") -> "Dataset":
        """
        Add holiday indicator and proximity features.

        Requires the ``holidays`` Python package (``pip install holidays``).

        Generated features (prefix = column name):
          is_holiday, days_to_holiday, days_since_holiday, holiday_name

        Parameters
        ----------
        country : str, default "CO" (Colombia)
            ISO 3166-1 alpha-2 country code (e.g. "US", "GB", "CO", "MX").

        >>> dataset.datetime().features.holidays(country="CO")
        >>> dataset.datetime().features.holidays(country="US")
        """
        try:
            import holidays as hol_lib
        except ImportError:
            from forecastlib.exceptions import LoadError
            raise LoadError(
                "The 'holidays' package is required for holiday features.",
                suggestions=["pip install holidays"],
            )

        dt_cols = self._dt_columns()
        df      = self._dataset._df.copy()
        new_feats = []

        for col in dt_cols:
            dt   = pd.to_datetime(df[col])
            years = dt.dt.year.unique().tolist()
            p    = f"{col}_"

            try:
                hol_obj   = hol_lib.country_holidays(country, years=years)
                hol_dates = set(pd.to_datetime(list(hol_obj.keys())).normalize())
                hol_names = {pd.Timestamp(k).normalize(): v for k, v in hol_obj.items()}
            except Exception as e:
                import warnings
                warnings.warn(f"Could not load holidays for country '{country}': {e}")
                continue

            normalized = dt.dt.normalize()
            df[f"{p}is_holiday"] = normalized.isin(hol_dates).astype("int8")
            df[f"{p}holiday_name"] = normalized.map(hol_names).fillna("").astype("category")

            # Days to/since nearest holiday
            hol_ts = sorted(hol_dates)
            if hol_ts:
                df[f"{p}days_to_holiday"]    = dt.apply(
                    lambda x, ts=hol_ts: min(
                        abs((x.normalize() - h).days) for h in ts
                    )
                ).astype("int16")
                df[f"{p}days_since_holiday"] = dt.apply(
                    lambda x, ts=hol_ts: min(
                        (x.normalize() - h).days for h in ts if h <= x.normalize()
                    ) if any(h <= x.normalize() for h in ts) else -1
                ).astype("int16")

            new_feats += [f"{p}is_holiday", f"{p}holiday_name",
                          f"{p}days_to_holiday", f"{p}days_since_holiday"]

        step = TransformStep(
            name=f"holidays(country={country!r}) on {dt_cols}",
            kind="ts.holidays",
            columns=dt_cols,
            params={"country": country, "new_features": new_feats},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # All features at once
    # ------------------------------------------------------------------

    def all(self, country: Optional[str] = None) -> "Dataset":
        """
        Apply calendar features and (optionally) holiday features.

        Parameters
        ----------
        country : str, optional
            If provided, also add holiday features for this country.

        >>> dataset.datetime().features.all(country="CO")
        """
        self.calendar()
        if country:
            self.holidays(country=country)
        return self._dataset

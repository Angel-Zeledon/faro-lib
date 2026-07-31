"""
Calendar features — the ONE source of date-derived features.

Both training and inference call `calendar_frame()`. That is the whole point of
this module: the two used to be separate implementations, and they disagreed.
`FeatureEngineer._calendar` computed real Colombian holidays over the training
dates, while the predictor hardcoded `is_holiday = 0` and `days_to_holiday = 0`
for EVERY future date. So a model that had learned "demand jumps around
holidays" was asked, in production, to forecast a world with no holidays in it —
the feature was informative in training and constant-zero at serve time, which
is worse than not having it at all.

The country is a parameter, not a constant. The product sells across LatAm; the
holiday set of one country is the wrong calendar for a distributor in another.

Example:
    cal = HolidayCalendar("MX")
    df = calendar_frame(pd.to_datetime(df["date"]), cal)   # same columns, any dates
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional, Set

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_COUNTRY = "CO"

# Emitted by calendar_frame(), in this order. Anything reading calendar features
# (the feature engineer, the predictor, tests) refers to this list rather than
# re-listing the names, so a new feature cannot reach training without also
# reaching inference.
CALENDAR_COLUMNS: list[str] = [
    "year", "month", "day", "dow", "week", "quarter", "is_weekend",
    "sin_month", "cos_month", "sin_dow", "cos_dow",
    "is_holiday", "is_easter", "is_christmas",
    "days_to_holiday", "days_to_easter", "days_to_xmas",
    "holiday_intensity", "christmas_season",
]


def easter_date(year: int) -> pd.Timestamp:
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


class HolidayCalendar:
    """
    National holidays for one country, resolved lazily per year and cached.

    Future years are as available as past ones: the `holidays` library computes
    them from each country's rules rather than reading a fixed table, so asking
    for next year's calendar is not a special case. When the library is missing
    or does not know the country, the calendar degrades to Easter + Christmas
    (both computable here) instead of to nothing — a degraded holiday signal
    still beats a signal that silently flips to zero at the forecast boundary.
    """

    def __init__(self, country: Optional[str] = None):
        self.country = (country or DEFAULT_COUNTRY).upper()
        self._by_year: dict[int, Set[pd.Timestamp]] = {}
        self._library_ok = True

    def _load_year(self, year: int) -> Set[pd.Timestamp]:
        cached = self._by_year.get(year)
        if cached is not None:
            return cached

        dates: Set[pd.Timestamp] = set()
        if self._library_ok:
            try:
                import holidays as hol_lib
                dates = {
                    pd.Timestamp(d)
                    for d in hol_lib.country_holidays(self.country, years=[year]).keys()
                }
            except Exception as exc:
                # One warning per calendar, not one per year.
                if self._library_ok:
                    log.warning(
                        "Holiday calendar for %r unavailable (%s) — falling back to "
                        "Easter + Christmas only.", self.country, exc,
                    )
                self._library_ok = False

        # Always present, library or not, so the fallback is never empty.
        dates.add(easter_date(year))
        dates.add(pd.Timestamp(year=year, month=12, day=25))

        self._by_year[year] = dates
        return dates

    def holidays_for(self, years: Iterable[int]) -> Set[pd.Timestamp]:
        out: Set[pd.Timestamp] = set()
        for y in {int(y) for y in years}:
            out |= self._load_year(y)
        return out

    def is_holiday(self, dt: pd.Timestamp) -> bool:
        return pd.Timestamp(dt).normalize() in self._load_year(int(dt.year))


def _min_day_distance(dates: pd.Series, targets: Set[pd.Timestamp]) -> np.ndarray:
    """
    Days from each date to the nearest date in `targets`, as a non-negative
    float array. Vectorised: the previous per-row `min(abs(...))` over the whole
    holiday set was O(rows x holidays) and dominated feature engineering on
    multi-year daily datasets.
    """
    if not targets:
        return np.full(len(dates), np.nan)
    # Day-resolution integers on both sides. Converting to datetime64[D] rather
    # than reading the raw int64 keeps this correct whatever resolution pandas
    # inferred for the column (2.x yields `us` for Timestamp input and `ns` for
    # parsed strings — mixing the two silently produced distances off by 1000x).
    to_days = lambda idx: idx.normalize().to_numpy(dtype="datetime64[D]").astype("int64")
    target_ord = np.sort(to_days(pd.DatetimeIndex(list(targets))))
    own = to_days(pd.DatetimeIndex(dates))
    pos = np.searchsorted(target_ord, own)
    left = np.clip(pos - 1, 0, len(target_ord) - 1)
    right = np.clip(pos, 0, len(target_ord) - 1)
    d_left = np.abs(own - target_ord[left])
    d_right = np.abs(own - target_ord[right])
    return np.minimum(d_left, d_right).astype(float)


def calendar_frame(dates, calendar: Optional[HolidayCalendar] = None) -> pd.DataFrame:
    """
    Build every calendar feature for an arbitrary set of dates.

    Args:
        dates:    Anything pd.to_datetime accepts (a Series, index or list).
                  Past or future — the function does not care, which is the
                  invariant that keeps training and inference identical.
        calendar: HolidayCalendar; defaults to DEFAULT_COUNTRY.

    Returns:
        DataFrame with exactly CALENDAR_COLUMNS, indexed like `dates`.
    """
    dt = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    cal = calendar or HolidayCalendar()

    years = dt.dt.year.unique().tolist()
    holiday_dates = cal.holidays_for(years)
    easter_dates = {easter_date(int(y)) for y in years}
    xmas_dates = {pd.Timestamp(year=int(y), month=12, day=25) for y in years}
    normalized = dt.dt.normalize()

    out = pd.DataFrame(index=dt.index)
    out["year"] = dt.dt.year
    out["month"] = dt.dt.month
    out["day"] = dt.dt.day
    out["dow"] = dt.dt.dayofweek
    out["week"] = dt.dt.isocalendar().week.astype(int)
    out["quarter"] = dt.dt.quarter
    out["is_weekend"] = (dt.dt.dayofweek >= 5).astype(int)
    out["sin_month"] = np.sin(2 * np.pi * dt.dt.month / 12)
    out["cos_month"] = np.cos(2 * np.pi * dt.dt.month / 12)
    out["sin_dow"] = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
    out["cos_dow"] = np.cos(2 * np.pi * dt.dt.dayofweek / 7)

    out["is_holiday"] = normalized.isin(holiday_dates).astype(int)
    out["is_easter"] = normalized.isin(easter_dates).astype(int)
    out["is_christmas"] = normalized.isin(xmas_dates).astype(int)

    out["days_to_holiday"] = _min_day_distance(dt, holiday_dates)
    out["days_to_easter"] = _min_day_distance(dt, easter_dates)
    out["days_to_xmas"] = _min_day_distance(dt, xmas_dates)

    out["holiday_intensity"] = (
        out["is_holiday"] * 3 + out["is_easter"] * 5 + out["is_christmas"] * 5
    )
    out["christmas_season"] = dt.dt.month.isin([11, 12]).astype(int)

    return out[CALENDAR_COLUMNS]


# Fourier terms count days from a FIXED epoch, not from the dataset's own first
# date. With a dataset-relative origin the phase of every term depended on where
# the upload happened to start, so the predictor — which never saw that date —
# could not reproduce the feature and filled it with 0.0. Same class of bug as
# the holiday features: informative in training, constant at serve time.
FOURIER_EPOCH = pd.Timestamp("1970-01-01")


def fourier_columns(periods: Iterable[int], K: int) -> list[str]:
    """The ordered names fourier_frame() produces for this configuration."""
    return [
        f"fourier_{fn}_{period}_{k}"
        for period in periods
        for k in range(1, K + 1)
        for fn in ("sin", "cos")
    ]


def fourier_frame(dates, periods: Iterable[int], K: int) -> pd.DataFrame:
    """Fourier seasonality terms for arbitrary dates (past or future)."""
    dt = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    t = (dt - FOURIER_EPOCH).dt.days.to_numpy().astype(float)
    out = pd.DataFrame(index=dt.index)
    for period in periods:
        for k in range(1, int(K) + 1):
            angle = 2 * np.pi * k * t / float(period)
            out[f"fourier_sin_{period}_{k}"] = np.sin(angle)
            out[f"fourier_cos_{period}_{k}"] = np.cos(angle)
    return out


def calendar_row(dt: pd.Timestamp, calendar: Optional[HolidayCalendar] = None) -> dict:
    """One future date's calendar features, as the dict the predictor wants."""
    frame = calendar_frame([pd.Timestamp(dt)], calendar)
    row = frame.iloc[0].to_dict()
    # numpy scalars would travel into a DataFrame the model then sees as object
    return {k: (float(v) if isinstance(v, float) else int(v)) for k, v in row.items()}

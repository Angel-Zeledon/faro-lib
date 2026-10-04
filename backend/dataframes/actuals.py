"""Realized sales of a dataset, in the shape a forecast can be compared against.

Plain Python out (``{series_key: {iso_period: total}}``), pandas inside. The
bucketing mirrors the engine's own resample, so a weekly session's forecast
dates and these actuals use the same period labels.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from backend.dataframes.io import read_dataframe


def _bucket_is_complete(label: pd.Timestamp, target_freq: Optional[str], last_day: pd.Timestamp) -> bool:
    """A resample bucket only counts once the data covers all of it.

    pandas labels ``W-*`` buckets by their END day and ``MS`` buckets by their
    START, so the end of a monthly bucket is its last calendar day.
    """
    if not target_freq:
        return label <= last_day
    if target_freq.upper().startswith("W"):
        return label <= last_day
    if target_freq.upper() in ("MS", "M"):
        return (label + pd.offsets.MonthEnd(0)) <= last_day
    return label <= last_day


def load_actual_series(
    path: str,
    date_col: str,
    target_col: str,
    group_cols: list[str],
    target_freq: Optional[str] = None,
) -> dict:
    """Sum the dataset's sales per series and period.

    Returns ``{"series": {key: {"YYYY-MM-DD": total}}, "first_date", "last_date"}``
    or ``{"error": "columns_missing"}`` when the file does not have the mapped
    columns (a later upload may use a different layout). ``key`` is the engine's
    ``series_key`` when two group columns are mapped, the plain value otherwise.
    """
    df = read_dataframe(path)
    needed = [date_col, target_col, *group_cols]
    if any(c not in df.columns for c in needed):
        return {"error": "columns_missing"}

    work = df[needed].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
    work = work.dropna(subset=[date_col, target_col])
    if work.empty:
        return {"error": "no_rows"}

    first_day, last_day = work[date_col].min(), work[date_col].max()
    keys = group_cols[:2]
    if keys:
        for k in keys:
            work[k] = work[k].astype(str)
        work = work.groupby([date_col, *keys], as_index=False)[target_col].sum()
    else:
        work = work.groupby(date_col, as_index=False)[target_col].sum()

    if target_freq:
        from forecasting_core.data.resampler import resample_to_frequency
        # The engine resamples on one group column; with two keys, collapse them
        # to the engine's series key first so the buckets stay per series.
        if len(keys) == 2:
            from forecasting_core.data.canonical import series_key
            work["__key"] = [series_key(a, b) for a, b in zip(work[keys[0]], work[keys[1]])]
            work = work.groupby([date_col, "__key"], as_index=False)[target_col].sum()
            grp = "__key"
        else:
            grp = keys[0] if keys else None
        work = resample_to_frequency(work, date_col, grp, target_col, target_freq)
        if grp is None:
            work["__key"] = "__all__"
            grp = "__key"
    else:
        if len(keys) == 2:
            from forecasting_core.data.canonical import series_key
            work["__key"] = [series_key(a, b) for a, b in zip(work[keys[0]], work[keys[1]])]
            grp = "__key"
        elif keys:
            grp = keys[0]
        else:
            work["__key"] = "__all__"
            grp = "__key"

    series: dict[str, dict[str, float]] = {}
    for d, k, v in zip(work[date_col], work[grp], work[target_col]):
        if not _bucket_is_complete(d, target_freq, last_day):
            continue
        series.setdefault(str(k), {})[d.strftime("%Y-%m-%d")] = float(v)
    return {
        "series": series,
        "first_date": first_day.strftime("%Y-%m-%d"),
        "last_date": last_day.strftime("%Y-%m-%d"),
    }

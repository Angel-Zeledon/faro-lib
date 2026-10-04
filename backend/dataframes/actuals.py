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


# (path, mtime_ns, size, date_col) -> {"first_date", "last_date"} | {"error"}.
# A dataset file never changes in place (a replace writes a new file), so the
# range is read once and reused by every screen that asks.
_RANGE_CACHE: dict[tuple, dict] = {}
_RANGE_CACHE_MAX = 2000


def dataset_date_range(path: str, date_col: str) -> dict:
    """First and last date of a dataset's date column, cached per file.

    Returns ``{"first_date", "last_date"}`` or ``{"error": code}`` where the code
    is ``columns_missing`` / ``no_rows`` / ``unreadable`` — a file that cannot
    answer is a reason to show, not an exception.
    """
    import os
    try:
        st = os.stat(path)
    except OSError:
        return {"error": "unreadable"}
    key = (path, st.st_mtime_ns, st.st_size, date_col)
    hit = _RANGE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        df = read_dataframe(path)
        if date_col not in df.columns:
            out: dict = {"error": "columns_missing"}
        else:
            dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
            out = ({"error": "no_rows"} if dates.empty else
                   {"first_date": dates.min().strftime("%Y-%m-%d"),
                    "last_date": dates.max().strftime("%Y-%m-%d")})
    except Exception:  # noqa: BLE001 - an unreadable file is a reason, not a 500
        out = {"error": "unreadable"}
    if len(_RANGE_CACHE) >= _RANGE_CACHE_MAX:
        _RANGE_CACHE.clear()
    _RANGE_CACHE[key] = out
    return out


def write_holdout_copy(
    src_path: str, dst_path: str, date_col: str, holdout_periods: int,
    target_freq: Optional[str],
) -> dict:
    """Write a copy of a dataset without its last ``holdout_periods`` periods.

    The period is the session's own grain (``W-MON`` weekly, ``MS`` monthly,
    otherwise days). The cut falls on the end of a period so no period is left
    half-observed. Returns ``{"cutoff", "last_date", "rows_kept", "rows_total"}``
    or ``{"error": code}``; the original file is never touched.
    """
    df = read_dataframe(src_path)
    if date_col not in df.columns:
        return {"error": "columns_missing"}
    dates = pd.to_datetime(df[date_col], errors="coerce")
    valid = dates.dropna()
    if valid.empty:
        return {"error": "no_rows"}
    last_day = valid.max().normalize()
    freq = (target_freq or "").upper()
    if freq.startswith("W"):
        # Land on the week's closing day so the last kept week is whole.
        cutoff = pd.tseries.frequencies.to_offset(freq).rollback(
            last_day - pd.Timedelta(days=7 * holdout_periods))
    elif freq in ("MS", "M"):
        cutoff = (last_day - pd.DateOffset(months=holdout_periods)) + pd.offsets.MonthEnd(0)
        if cutoff >= last_day:
            cutoff = (last_day - pd.DateOffset(months=holdout_periods + 1)) + pd.offsets.MonthEnd(0)
    else:
        cutoff = last_day - pd.Timedelta(days=holdout_periods)
    kept = df[dates <= cutoff + pd.Timedelta(hours=23, minutes=59, seconds=59)]
    if kept.empty or kept[date_col].nunique() < 2:
        return {"error": "holdout_too_large"}
    suffix = str(dst_path).rsplit(".", 1)[-1].lower()
    if suffix == "csv":
        kept.to_csv(dst_path, index=False)
    elif suffix == "parquet":
        kept.to_parquet(dst_path, index=False)
    elif suffix in ("xlsx", "xls"):
        kept.to_excel(dst_path, index=False)
    elif suffix == "json":
        kept.to_json(dst_path, orient="records", date_format="iso")
    else:
        return {"error": "unreadable"}
    return {"cutoff": cutoff.strftime("%Y-%m-%d"), "last_date": last_day.strftime("%Y-%m-%d"),
            "rows_kept": int(len(kept)), "rows_total": int(len(df))}

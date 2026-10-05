"""
The history, prepared the way the training pipeline prepares it.

`reforecast` has to see the series exactly as `Pipeline.run` saw them at training
time (same ordering, same resampling, same outlier clipping and censored-demand
recovery) or its answer for UNCHANGED history would differ from the parent's by
more than nothing. The steps are the pipeline's own public functions, in the
pipeline's order; ``tests/test_reforecast.py`` pins the equivalence by asserting
that unchanged history reproduces the parent's forecast exactly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

import numpy as np
import pandas as pd


def _primary_group(c) -> Optional[str]:
    return c.group_keys[0] if c.group_keys else None


def prepare_history(config, df: pd.DataFrame) -> pd.DataFrame:
    """`Pipeline.run` steps 1, 1b, 2b and 2c on ``df``; no quality filtering."""
    from forecasting_core.data.censoring import recover_censored_demand
    from forecasting_core.pipelines.pipeline import Pipeline, _config_as_validation_dict
    from forecasting_core.validation.auto_correct import auto_correct_data

    c = config.columns
    out = df.copy()
    out[c.date] = pd.to_datetime(out[c.date])
    out = out.dropna(subset=[c.target]).sort_values(
        [_primary_group(c), c.date] if _primary_group(c) else [c.date]
    ).reset_index(drop=True)
    out = Pipeline(config)._maybe_resample(out)
    out, _ = auto_correct_data(out, _config_as_validation_dict(config), clip_outliers=True)
    out, _ = recover_censored_demand(
        out, date_col=c.date, target_col=c.target,
        group_col=_primary_group(c),
        inventory_col=getattr(c, "inventory", "") or "",
    )
    return out


def bucket_seconds(df: pd.DataFrame, date_col: str) -> float:
    """The dataset's cadence: the median gap between its distinct dates."""
    ordered = pd.Series(pd.to_datetime(df[date_col]).unique()).sort_values()
    gaps = ordered.diff().dropna()
    gaps = gaps[gaps > pd.Timedelta(0)]
    if gaps.empty:
        return 86400.0
    return float(pd.Timedelta(gaps.median()).total_seconds())


def series_anchors(config, df: pd.DataFrame) -> Dict[str, dict]:
    """{primary key: {first, last, n}} of the prepared history."""
    c = config.columns
    primary = _primary_group(c)
    if primary and primary in df.columns:
        groups = df.groupby(df[primary].astype(str))
    else:
        groups = [("__all__", df)]
    out: Dict[str, dict] = {}
    for key, g in groups:
        dates = pd.to_datetime(g[c.date])
        out[str(key)] = {
            "first": str(dates.min())[:10],
            "last": str(dates.max())[:10],
            "n": int(len(g)),
        }
    return out


def input_schema(config) -> dict:
    """What a model's inputs are defined by. Two runs with the same schema read
    the same columns and build the same features."""
    c, f, g, t = config.columns, config.features, config.granularity, config.training
    return {
        "date": c.date,
        "target": c.target,
        "group_keys": list(c.group_keys or []),
        "features": {
            "lags": list(f.lags or []), "diffs": list(f.diffs or []),
            "rolling": list(f.rolling or []), "calendar": bool(f.calendar),
            "ewm_spans": list(f.ewm_spans or []),
            "fourier_periods": list(f.fourier_periods or []),
            "fourier_K": int(f.fourier_K),
            "holiday_country": f.holiday_country,
        },
        "granularity": {"strategy": g.strategy, "target_freq": g.target_freq},
        "seasonal_period": int(t.seasonal_period),
    }


def schema_hash(schema: dict) -> str:
    blob = json.dumps(schema, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff_schema(trained: dict, current: dict) -> Dict[str, dict]:
    """{field: {trained, current}} for every top-level field that differs."""
    out = {}
    for key in sorted(set(trained) | set(current)):
        if trained.get(key) != current.get(key):
            out[key] = {"trained": trained.get(key), "current": current.get(key)}
    return out


def numeric_view(df: pd.DataFrame) -> pd.DataFrame:
    """The pipeline's ``sanitize_ml_dataframe``: an object column that is almost
    entirely numeric becomes numeric, the rest become categories."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            converted = pd.to_numeric(out[col], errors="coerce")
            if converted.notna().mean() > 0.8:
                out[col] = converted
            else:
                out[col] = out[col].astype("category")
    return out


def finite(values) -> np.ndarray:
    return np.asarray(values, dtype=float)

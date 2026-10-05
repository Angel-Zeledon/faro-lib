"""
Resampler — aggregates a mixed-frequency dataset to one common frequency
("Estrategia B" of the Data Alignment Wizard). Runs once, in memory, at the
start of the pipeline; the resulting dataframe is homogeneous and the rest
of the pipeline (FeatureEngineer, Trainer, models) never knows resampling
happened.
"""

from __future__ import annotations

import logging

import pandas as pd
from typing import Dict, Optional

log = logging.getLogger(__name__)


def resample_to_frequency(
    df: pd.DataFrame,
    date_col: str,
    group_col: Optional[str],
    target_col: str,
    target_freq: str,
    extra_cols: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Aggregate demand to `target_freq` by summing within each bucket, per
    group if a group column is given.

    Args:
        df:          Raw dataframe (any native per-row cadence).
        date_col:    Name of the date column.
        group_col:   Name of the SKU/group column, or None for a single series.
        target_col:  Name of the demand/target column to sum.
        target_freq: Pandas offset alias to resample to ("D", "W", "2W", "MS").
        extra_cols:  {column: pandas agg} for columns that must SURVIVE the
                     resample. Everything not named here is dropped.

                     This exists because dropping silently is a real failure,
                     not a tidy simplification. The function used to return
                     exactly [date, group, target], so a session on Estrategia B
                     lost its inventory column on the way in — and censored
                     demand recovery, which is the one thing that column is for,
                     switched itself off with the "no inventory mapped" reason
                     and told nobody. The forecast then carried the full
                     stockout bias while the app reported a clean run.

                     For inventory the right aggregation is `min`: a bucket in
                     which stock touched zero at any point is a bucket in which
                     demand was truncated, and `last` would miss it entirely
                     whenever the shelf was restocked before the week closed.

    Returns:
        One row per (group, resampled bucket), with the target summed and any
        `extra_cols` aggregated as requested.
    """
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")

    extras = {c: how for c, how in (extra_cols or {}).items()
              if c and c in work.columns and c not in (date_col, group_col, target_col)}
    missing = [c for c in (extra_cols or {}) if c and c not in work.columns]
    if missing:
        log.warning(
            "resample_to_frequency: asked to carry %s through the resample, but "
            "the frame has no such column(s); they will be absent downstream.",
            missing,
        )

    agg = {target_col: "sum", **extras}

    if group_col:
        out = (
            work.set_index(date_col)
            .groupby(group_col)
            .resample(target_freq)
            .agg(agg)
            .reset_index()
        )
        return out[[date_col, group_col, target_col, *extras]]

    out = (
        work.set_index(date_col)
        .resample(target_freq)
        .agg(agg)
        .reset_index()
    )
    return out[[date_col, target_col, *extras]]

"""
Resampler — aggregates a mixed-frequency dataset to one common frequency
("Estrategia B" of the Data Alignment Wizard). Runs once, in memory, at the
start of the pipeline; the resulting dataframe is homogeneous and the rest
of the pipeline (FeatureEngineer, Trainer, models) never knows resampling
happened.
"""

from __future__ import annotations

import pandas as pd
from typing import Optional


def resample_to_frequency(
    df: pd.DataFrame,
    date_col: str,
    group_col: Optional[str],
    target_col: str,
    target_freq: str,
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

    Returns:
        A new dataframe with columns [date_col, group_col?, target_col],
        one row per (group, resampled bucket), target summed within it.
    """
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")

    if group_col:
        out = (
            work.set_index(date_col)
            .groupby(group_col)[target_col]
            .resample(target_freq)
            .sum()
            .reset_index()
        )
        return out[[date_col, group_col, target_col]]

    out = (
        work.set_index(date_col)[target_col]
        .resample(target_freq)
        .sum()
        .reset_index()
    )
    return out[[date_col, target_col]]

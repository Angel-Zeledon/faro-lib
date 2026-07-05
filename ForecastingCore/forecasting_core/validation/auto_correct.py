"""
Auto-correction system.

Applies safe, low-risk corrections to config and data before training.
Each correction is logged in a CorrectionLog for audit trail.

Risk levels:
  LOW    — purely additive (setting defaults, clipping to valid range)
  MEDIUM — data transformation (filling NaNs, clipping outliers)
  HIGH   — structural change (dropping SKUs, removing models)

Only LOW and MEDIUM corrections are applied automatically in AUTO_FIX mode.
HIGH corrections require explicit user confirmation (returned as suggestions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd


@dataclass
class Correction:
    action: str
    risk: str               # "low" | "medium" | "high"
    description: str
    before: Any = None
    after: Any = None
    skus_affected: List[str] = field(default_factory=list)


@dataclass
class CorrectionLog:
    corrections: List[Correction] = field(default_factory=list)

    def add(self, correction: Correction):
        self.corrections.append(correction)

    def to_list(self) -> List[dict]:
        return [
            {
                "action": c.action,
                "risk": c.risk,
                "description": c.description,
                "before": c.before,
                "after": c.after,
                "skus_affected": c.skus_affected,
            }
            for c in self.corrections
        ]

    def has_high_risk(self) -> bool:
        return any(c.risk == "high" for c in self.corrections)


def auto_correct_config(config: Dict[str, Any]) -> CorrectionLog:
    """Apply safe corrections to a config dict in-place. Returns a log."""
    log = CorrectionLog()

    # Default train_ratio
    if "train_ratio" not in config:
        config["train_ratio"] = 0.8
        log.add(Correction("set_default", "low", "Set train_ratio=0.8 (default)", None, 0.8))

    # Default prediction_horizon
    if "prediction_horizon" not in config:
        config["prediction_horizon"] = 7
        log.add(Correction("set_default", "low", "Set prediction_horizon=7 (default)", None, 7))

    # Default min_history
    if "min_history" not in config:
        config["min_history"] = 20
        log.add(Correction("set_default", "low", "Set min_history=20 (default)", None, 20))

    # Clamp train_ratio
    tr = float(config.get("train_ratio", 0.8))
    if not (0.1 <= tr <= 0.95):
        clamped = max(0.1, min(0.95, tr))
        config["train_ratio"] = clamped
        log.add(Correction("clamp", "low", f"Clamped train_ratio to [{0.1}, {0.95}]", tr, clamped))

    # Ensure features is a dict
    if "features" not in config or not isinstance(config["features"], dict):
        config["features"] = {}
        log.add(Correction("set_default", "low", "Initialized empty features config", None, {}))

    features = config["features"]
    if "lags" not in features:
        features["lags"] = [1, 7, 14]
        log.add(Correction("set_default", "low", "Set default lags=[1,7,14]", None, [1, 7, 14]))
    if "rolling" not in features:
        features["rolling"] = [7, 14]
        log.add(Correction("set_default", "low", "Set default rolling=[7,14]", None, [7, 14]))

    return log


def auto_correct_data(
    df: pd.DataFrame,
    config: Dict[str, Any],
    clip_outliers: bool = True,
    fill_missing_dates: bool = True,
) -> tuple[pd.DataFrame, CorrectionLog]:
    """Apply medium-risk data corrections. Returns (corrected_df, log)."""
    log = CorrectionLog()
    df = df.copy()

    target_col = config.get("target")
    dt_col     = config.get("dt")
    group_col  = config.get("group_id")
    freq       = config.get("freq")

    if not target_col or target_col not in df.columns:
        return df, log

    # Fill leading/trailing NaN in target per SKU
    groups = df.groupby(group_col) if group_col and group_col in df.columns else [("__all__", df)]
    nan_skus = []
    for sku, g in groups:
        nan_count = int(g[target_col].isna().sum())
        if nan_count > 0:
            nan_skus.append(str(sku))

    if nan_skus:
        if group_col and group_col in df.columns:
            df[target_col] = (
                df.groupby(group_col)[target_col]
                .transform(lambda s: s.ffill().bfill().fillna(0))
            )
        else:
            df[target_col] = df[target_col].ffill().bfill().fillna(0)
        log.add(Correction(
            "fill_nan_target", "medium",
            f"Filled NaN target values via ffill→bfill→0 for {len(nan_skus)} SKU(s)",
            skus_affected=nan_skus[:20],
        ))

    # Clip outliers per SKU (IQR × 3)
    if clip_outliers:
        clipped_skus = []
        groups2 = df.groupby(group_col).groups if group_col and group_col in df.columns else {"__all__": df.index}
        for sku, idx in groups2.items():
            s = df.loc[idx, target_col]
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
            clipped = ((s < lo) | (s > hi)).sum()
            if clipped > 0:
                df.loc[idx, target_col] = s.clip(lower=lo, upper=hi)
                clipped_skus.append(str(sku))
        if clipped_skus:
            log.add(Correction(
                "clip_outliers", "medium",
                f"Clipped outliers (IQR×3) in {len(clipped_skus)} SKU(s)",
                skus_affected=clipped_skus[:20],
            ))

    return df, log

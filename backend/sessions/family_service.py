"""
Session family fan-out (multi-period planning, Phase A).

A single training launch produces one session per supported granularity
(daily/weekly/monthly, gated by how much history the data holds), all sharing
a family_id, each pre-forecast to a generous reach. The engine is unchanged:
each sibling just carries a different granularity_cfg (aggregate + target_freq)
and forecast_cfg.horizon, which runner.py already consumes.
"""

from __future__ import annotations

import logging

from backend.utils.temporal_agg import detect_frequency, planning_granularities

log = logging.getLogger(__name__)

# Steps of the grain to pre-forecast, so the admin's chosen horizon (Phase B)
# is a window into an already-computed reach rather than a re-train.
GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}
# A grain is offered only if the history spans >= this many of its buckets.
MIN_BUCKETS_FOR_GRANULARITY = 20
# pandas resample rule each grain trains at; None = native (no aggregation).
TARGET_FREQ = {"daily": None, "weekly": "W-MON", "monthly": "MS"}


def plan_family(dates: list[str]) -> list[dict]:
    """Decide which granularities to train and with what config. Pure — no DB.

    Returns finest-first, one dict per available grain:
      {granularity, target_freq, horizon, is_base}.
    The base (finest detected) grain trains natively (target_freq None).
    """
    base_freq = detect_frequency(dates)
    if base_freq not in GENEROUS_REACH:
        base_freq = "daily"
    grains = planning_granularities(base_freq, dates, MIN_BUCKETS_FOR_GRANULARITY)
    specs = []
    for g in grains:
        specs.append({
            "granularity": g,
            "target_freq": None if g == base_freq else TARGET_FREQ[g],
            "horizon": GENEROUS_REACH[g],
            "is_base": g == base_freq,
        })
    return specs

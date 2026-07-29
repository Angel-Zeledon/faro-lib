"""Seasonal-period policy for series decomposition.

Pure Python — no pandas/numpy, no DB, no engine import. It answers one question:
given the grain a session is expressed at, how many buckets make up one full
seasonal cycle? That number is the `period` STL needs, and the cycle it names is
what the frontend must label the seasonal panel with.

Kept out of the endpoint so the policy is unit-testable without a database, and
so the frontend contract (`period` + `seasonal_cycle`) has exactly one source.
"""

from __future__ import annotations

from typing import Optional

# Buckets in one full seasonal cycle, per granularity.
#
#   daily     -> 7   the day-of-week cycle. The annual cycle at a daily grain is
#                    365, which would demand two full years of daily rows before
#                    anything could be drawn — history almost no SMB upload has —
#                    and STL at that period is slow and numerically fragile. Week
#                    is the cycle daily data can actually support.
#   weekly    -> 52  the annual cycle, in weeks.
#   monthly   -> 12  the annual cycle, in months.
#   quarterly -> 4   the annual cycle, in quarters.
#
# `yearly` is deliberately absent: a yearly series has no shorter cycle left to
# separate out, so there is nothing a decomposition could recover.
SEASONAL_PERIOD: dict[str, int] = {
    "daily":     7,
    "weekly":    52,
    "monthly":   12,
    "quarterly": 4,
}

# The cycle the period above represents, as a stable English key. The frontend
# maps it to Spanish copy for the seasonal panel's title/axis.
SEASONAL_CYCLE: dict[str, str] = {
    "daily":     "weekly",
    "weekly":    "annual",
    "monthly":   "annual",
    "quarterly": "annual",
}

# STL cannot separate a cycle it has never seen repeat. Two full cycles is the
# floor the engine itself enforces (`decomposition.decompose`).
MIN_CYCLES = 2

# pandas resample rules stored in `session_configs.granularity_cfg.target_freq`
# (written by `sessions/family_service.py`) mapped back to grain names.
_TARGET_FREQ_GRAIN: dict[str, str] = {
    "D":     "daily",
    "W-MON": "weekly",
    "W":     "weekly",
    "MS":    "monthly",
    "M":     "monthly",
    "QS":    "quarterly",
    "Q":     "quarterly",
    "YS":    "yearly",
    "Y":     "yearly",
}


def granularity_from_target_freq(target_freq: Optional[str]) -> Optional[str]:
    """Grain name for a stored `target_freq` resample rule, or None when the
    session trains natively (`target_freq` is NULL) or the rule is unknown."""
    if not target_freq:
        return None
    return _TARGET_FREQ_GRAIN.get(str(target_freq).upper())


def seasonal_period(granularity: str) -> Optional[int]:
    """Seasonal period for a grain, or None if the grain has no usable cycle."""
    return SEASONAL_PERIOD.get(granularity)


def min_points(granularity: str) -> Optional[int]:
    """Minimum observations a decomposition needs at this grain, or None if the
    grain is not decomposable at all."""
    period = seasonal_period(granularity)
    return None if period is None else MIN_CYCLES * period


def is_decomposable(granularity: str, n_points: int) -> bool:
    """True when `n_points` observations at this grain span at least MIN_CYCLES
    full seasonal cycles."""
    required = min_points(granularity)
    return required is not None and n_points >= required

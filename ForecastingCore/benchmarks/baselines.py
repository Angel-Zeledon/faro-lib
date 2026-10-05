"""Reference forecasters the engine has to beat. Deterministic, no fitting."""

from __future__ import annotations

import numpy as np


def naive(history, h: int, season: int = 1) -> np.ndarray:
    x = np.asarray(history, dtype=float)
    return np.repeat(x[-1], h)


def seasonal_naive(history, h: int, season: int) -> np.ndarray:
    x = np.asarray(history, dtype=float)
    if season <= 1 or len(x) < season:
        return naive(x, h)
    last = x[-season:]
    return np.array([last[i % season] for i in range(h)])


def moving_average(history, h: int, season: int = 1, window: int = 4) -> np.ndarray:
    x = np.asarray(history, dtype=float)
    w = max(1, min(window, len(x)))
    return np.repeat(x[-w:].mean(), h)


BASELINES = {
    "naive": naive,
    "seasonal_naive": seasonal_naive,
    "moving_average": moving_average,
}

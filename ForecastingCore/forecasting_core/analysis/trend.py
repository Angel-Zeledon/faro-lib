"""Trend analysis — Mann-Kendall, Sen's slope, linear regression, change points."""

from __future__ import annotations

import numpy as np


def mann_kendall(series: np.ndarray) -> dict:
    """
    Mann-Kendall trend test via Kendall's tau (O(n log n), scipy).

    H0: no monotonic trend.
    Reject H0 (p < 0.05) → significant trend.

    Returns:
        {
            tau:       float  Kendall correlation with time index,
            statistic: float  z-score,
            pvalue:    float,
            direction: "increasing" | "decreasing" | "no_trend",
            has_trend: bool,
        }
    """
    from scipy.stats import kendalltau

    t = np.arange(len(series), dtype=float)
    tau, pvalue = kendalltau(t, series)
    has_trend = bool(pvalue < 0.05)

    if has_trend:
        direction = "increasing" if tau > 0 else "decreasing"
    else:
        direction = "no_trend"

    return {
        "tau":       round(float(tau), 4),
        "pvalue":    round(float(pvalue), 4),
        "direction": direction,
        "has_trend": has_trend,
    }


def sens_slope(series: np.ndarray) -> float:
    """
    Theil-Sen robust slope estimator (O(n log n), scipy).
    Units: target_units / time_step.
    """
    from scipy.stats import theilslopes

    if len(series) < 2:
        return 0.0
    result = theilslopes(series, np.arange(len(series), dtype=float))
    return round(float(result.slope), 6)


def linear_trend(series: np.ndarray) -> dict:
    """
    Ordinary least-squares linear trend.

    Returns:
        {
            slope:       float  units/step,
            intercept:   float,
            r2:          float  0–1,
            pvalue:      float,
            significant: bool,
        }
    """
    from scipy.stats import linregress

    x = np.arange(len(series), dtype=float)
    slope, intercept, r, p, _ = linregress(x, series.astype(float))
    return {
        "slope":       round(float(slope),     6),
        "intercept":   round(float(intercept), 4),
        "r2":          round(float(r ** 2),    4),
        "pvalue":      round(float(p),         4),
        "significant": bool(p < 0.05),
    }


def detect_change_points(series: np.ndarray, max_points: int = 5) -> list[int]:
    """
    Structural change-point detection using PELT (requires `ruptures`).
    Returns indices (0-based) where the series changes level or slope.
    Returns [] gracefully if ruptures is not installed.
    """
    try:
        import ruptures as rpt
        algo = rpt.Pelt(model="rbf", min_size=max(2, len(series) // 10)).fit(
            series.reshape(-1, 1)
        )
        breakpoints = algo.predict(pen=3)
        return [int(b) for b in breakpoints[:-1]][:max_points]
    except Exception:
        return []


def trend_report(series: np.ndarray) -> dict:
    """
    Full trend analysis report for a single series.

    Returns:
        {
            mann_kendall:  dict  (see mann_kendall),
            sens_slope:    float,
            linear:        dict  (see linear_trend),
            change_points: list[int],
        }
    """
    if len(series) < 4:
        return {
            "mann_kendall":  {"direction": "no_trend", "has_trend": False},
            "sens_slope":    0.0,
            "linear":        {},
            "change_points": [],
        }

    return {
        "mann_kendall":  mann_kendall(series),
        "sens_slope":    sens_slope(series),
        "linear":        linear_trend(series),
        "change_points": detect_change_points(series),
    }

"""STL decomposition — trend, seasonal, and residual components per series."""

from __future__ import annotations

import numpy as np


def decompose(series: np.ndarray, period: int, robust: bool = True) -> dict:
    """
    STL (Seasonal-Trend decomposition using LOESS) for a single series.

    Args:
        series:  1-D array of target values (no NaNs).
        period:  Seasonal period (e.g. 7 for weekly, 12 for monthly).
        robust:  Use robust fitting (down-weights outliers). Recommended.

    Returns:
        {
            trend:             list of float,
            seasonal:          list of float,
            residual:          list of float,
            trend_strength:    float  0–1  (1 = dominated by trend),
            seasonal_strength: float  0–1  (1 = dominated by seasonality),
        }

    Raises:
        ValueError: if series is too short (< 2 * period).
    """
    from statsmodels.tsa.seasonal import STL

    if len(series) < 2 * period:
        raise ValueError(
            f"Series length ({len(series)}) must be >= 2 × period ({period})."
        )

    result = STL(series, period=period, robust=robust).fit()

    trend    = result.trend
    seasonal = result.seasonal
    residual = result.resid

    var_r  = float(np.var(residual))
    var_sr = float(np.var(seasonal + residual))
    var_tr = float(np.var(trend    + residual))

    seasonal_strength = max(0.0, 1.0 - var_r / var_sr) if var_sr > 1e-10 else 0.0
    trend_strength    = max(0.0, 1.0 - var_r / var_tr) if var_tr > 1e-10 else 0.0

    return {
        "trend":             [round(float(v), 4) for v in trend],
        "seasonal":          [round(float(v), 4) for v in seasonal],
        "residual":          [round(float(v), 4) for v in residual],
        "trend_strength":    round(min(1.0, trend_strength),    4),
        "seasonal_strength": round(min(1.0, seasonal_strength), 4),
    }

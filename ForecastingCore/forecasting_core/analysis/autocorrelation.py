"""Autocorrelation analysis — ACF, PACF, Ljung-Box, suggested ARIMA orders."""

from __future__ import annotations

import numpy as np


def autocorrelation_report(series: np.ndarray, n_lags: int = None) -> dict:
    """
    Compute ACF, PACF, Ljung-Box white-noise test, and suggest AR/MA orders.

    The suggested orders are heuristic (last significant lag in PACF/ACF).
    They can be used to pre-configure ARIMA hyperparameters.

    Args:
        series: 1-D array, no NaNs.
        n_lags: Number of lags to compute. Defaults to min(len/2 - 1, 40).

    Returns:
        {
            n_lags:           int,
            acf:              list[float]  lag 0 … n_lags,
            pacf:             list[float]  lag 0 … n_lags,
            ljung_box: {
                statistic:    float,
                pvalue:       float,
                is_white_noise: bool,
            },
            suggested_ar_order: int,
            suggested_ma_order: int,
        }
    """
    from statsmodels.tsa.stattools import acf, pacf
    from statsmodels.stats.diagnostic import acorr_ljungbox

    n = len(series)
    if n < 6:
        return {"n_lags": 0, "acf": [], "pacf": [], "ljung_box": {}, "suggested_ar_order": 0, "suggested_ma_order": 0}

    if n_lags is None:
        n_lags = min(n // 2 - 1, 40)
    n_lags = max(1, n_lags)

    acf_vals  = acf(series,  nlags=n_lags, fft=True)
    pacf_vals = pacf(series, nlags=n_lags, method="ywm")

    # Ljung-Box at n_lags (or 20 if n_lags > 20)
    lb_lags = min(n_lags, 20)
    lb = acorr_ljungbox(series, lags=lb_lags, return_df=True)
    lb_stat  = float(lb["lb_stat"].iloc[-1])
    lb_pval  = float(lb["lb_pvalue"].iloc[-1])

    # Significance threshold: ±1.96 / sqrt(n)
    ci = 1.96 / np.sqrt(n)

    # Suggested AR order: last lag where PACF is outside CI (skip lag 0)
    ar_order = 0
    for i in range(1, len(pacf_vals)):
        if abs(pacf_vals[i]) > ci:
            ar_order = i

    # Suggested MA order: last lag where ACF is outside CI (skip lag 0)
    ma_order = 0
    for i in range(1, len(acf_vals)):
        if abs(acf_vals[i]) > ci:
            ma_order = i

    return {
        "n_lags":             n_lags,
        "acf":                [round(float(v), 4) for v in acf_vals],
        "pacf":               [round(float(v), 4) for v in pacf_vals],
        "confidence_interval": round(float(ci), 4),
        "ljung_box": {
            "statistic":      round(lb_stat, 4),
            "pvalue":         round(lb_pval, 4),
            "is_white_noise": bool(lb_pval > 0.05),
        },
        "suggested_ar_order": ar_order,
        "suggested_ma_order": ma_order,
    }

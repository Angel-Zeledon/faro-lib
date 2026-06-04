"""Stationarity tests — ADF, KPSS, and combined verdict."""

from __future__ import annotations

import warnings
import numpy as np


def adf_test(series: np.ndarray) -> dict:
    """
    Augmented Dickey-Fuller test.
    H0: series has a unit root (non-stationary).
    Reject H0 (p < 0.05) → stationary.
    """
    from statsmodels.tsa.stattools import adfuller

    result = adfuller(series, autolag="AIC")
    return {
        "statistic":      round(float(result[0]), 4),
        "pvalue":         round(float(result[1]), 4),
        "n_lags_used":    int(result[2]),
        "n_obs":          int(result[3]),
        "critical_values": {k: round(float(v), 4) for k, v in result[4].items()},
        "is_stationary":  bool(result[1] < 0.05),
    }


def kpss_test(series: np.ndarray) -> dict:
    """
    KPSS test.
    H0: series is stationary (level or trend).
    Reject H0 (p < 0.05) → non-stationary.
    """
    from statsmodels.tsa.stattools import kpss

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = kpss(series, regression="c", nlags="auto")

    return {
        "statistic":      round(float(result[0]), 4),
        "pvalue":         round(float(result[1]), 4),
        "n_lags_used":    int(result[2]),
        "critical_values": {k: round(float(v), 4) for k, v in result[3].items()},
        "is_stationary":  bool(result[1] > 0.05),
    }


def stationarity_report(series: np.ndarray) -> dict:
    """
    Combined stationarity report (ADF + KPSS) with plain-language verdict.

    Returns:
        {
            adf:        dict  (see adf_test),
            kpss:       dict  (see kpss_test),
            verdict:    "stationary" | "non-stationary" | "mixed",
            diff_order: int   recommended number of differences (0, 1, or 2),
        }
    """
    if len(series) < 8:
        return {"verdict": "insufficient_data", "diff_order": 0}

    adf  = adf_test(series)
    kpss = kpss_test(series)

    adf_stat  = adf["is_stationary"]
    kpss_stat = kpss["is_stationary"]

    if adf_stat and kpss_stat:
        verdict = "stationary"
    elif not adf_stat and not kpss_stat:
        verdict = "non-stationary"
    else:
        verdict = "mixed"

    # Recommended differencing order
    diff_order = 0
    if verdict in ("non-stationary", "mixed") and len(series) > 12:
        diff1 = np.diff(series)
        try:
            adf_d1  = adf_test(diff1)
            kpss_d1 = kpss_test(diff1)
            diff_order = 1
            if not adf_d1["is_stationary"] or not kpss_d1["is_stationary"]:
                diff_order = 2
        except Exception:
            diff_order = 1

    return {
        "adf":        adf,
        "kpss":       kpss,
        "verdict":    verdict,
        "diff_order": diff_order,
    }

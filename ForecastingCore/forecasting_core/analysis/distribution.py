"""
Distribution analysis — descriptive statistics, normality, outliers,
intermittency (ADI / CV²), and distribution fitting.
"""

from __future__ import annotations

import numpy as np


# ── Descriptive statistics ─────────────────────────────────────────────────

def descriptive_stats(series: np.ndarray) -> dict:
    """
    Full set of descriptive statistics for a numeric series.

    Returns:
        {
            n, mean, std, min, p5, p25 (q1), median, p75 (q3), p95, max,
            iqr, range, cv, skewness, kurtosis,
            zero_pct, missing_pct,
            outlier_count, outlier_pct,   (IQR × 1.5 rule)
        }
    """
    from scipy.stats import skew, kurtosis as kurt

    missing = int(np.isnan(series).sum())
    clean   = series[~np.isnan(series)]
    n = len(clean)

    if n == 0:
        return {"n": 0, "missing_pct": 100.0}

    mean   = float(np.mean(clean))
    std    = float(np.std(clean, ddof=1)) if n > 1 else 0.0
    pcts   = np.percentile(clean, [5, 25, 50, 75, 95])
    q1, q3 = float(pcts[1]), float(pcts[3])
    iqr    = q3 - q1

    # Outliers: outside [Q1 − 1.5·IQR, Q3 + 1.5·IQR]
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    n_outliers = int(((clean < lo) | (clean > hi)).sum())

    return {
        "n":            n,
        "mean":         round(mean, 4),
        "std":          round(std, 4),
        "min":          round(float(np.min(clean)), 4),
        "p5":           round(float(pcts[0]), 4),
        "q1":           round(q1, 4),
        "median":       round(float(pcts[2]), 4),
        "q3":           round(q3, 4),
        "p95":          round(float(pcts[4]), 4),
        "max":          round(float(np.max(clean)), 4),
        "iqr":          round(iqr, 4),
        "range":        round(float(np.max(clean) - np.min(clean)), 4),
        "cv":           round(std / (abs(mean) + 1e-9), 4),
        "skewness":     round(float(skew(clean)),    4),
        "kurtosis":     round(float(kurt(clean)),    4),
        "zero_pct":     round(float((clean == 0).mean() * 100), 2),
        "missing_pct":  round(float(missing / max(len(series), 1) * 100), 2),
        "outlier_count": n_outliers,
        "outlier_pct":  round(float(n_outliers / n * 100), 2),
    }


# ── Normality test ──────────────────────────────────────────────────────────

def normality_test(series: np.ndarray) -> dict:
    """
    Shapiro-Wilk test (n ≤ 5000) or Jarque-Bera (n > 5000).

    Returns:
        {test, statistic, pvalue, is_normal}
    """
    from scipy.stats import shapiro, jarque_bera

    clean = series[~np.isnan(series)]
    n = len(clean)

    if n < 8:
        return {"test": None, "is_normal": None}

    if n <= 5000:
        stat, pval = shapiro(clean)
        test = "shapiro-wilk"
    else:
        stat, pval = jarque_bera(clean)
        test = "jarque-bera"

    return {
        "test":      test,
        "statistic": round(float(stat), 4),
        "pvalue":    round(float(pval), 4),
        "is_normal": bool(pval > 0.05),
    }


# ── Intermittency (Croston ADI / CV²) ──────────────────────────────────────

def croston_classification(series: np.ndarray) -> dict:
    """
    Classify demand using the ADI–CV² matrix (Syntetos & Boylan 2005).

    ADI (Average Demand Interval): mean gap between non-zero demands.
    CV² (Squared Coefficient of Variation): variability of non-zero demand sizes.

    Classification:
        smooth       ADI < 1.32, CV² < 0.49
        intermittent ADI ≥ 1.32, CV² < 0.49
        erratic      ADI < 1.32, CV² ≥ 0.49
        lumpy        ADI ≥ 1.32, CV² ≥ 0.49

    Returns:
        {adi, cv2, classification}
    """
    clean = series[~np.isnan(series)]
    demand_idx = np.where(clean > 0)[0]

    if len(demand_idx) > 1:
        adi = float(np.mean(np.diff(demand_idx)))
    elif len(demand_idx) == 1:
        adi = float(len(clean))
    else:
        adi = float(len(clean))

    non_zero = clean[clean > 0]
    if len(non_zero) > 1:
        cv2 = float((np.std(non_zero) / (np.mean(non_zero) + 1e-9)) ** 2)
    else:
        cv2 = 0.0

    if adi < 1.32 and cv2 < 0.49:
        classification = "smooth"
    elif adi >= 1.32 and cv2 < 0.49:
        classification = "intermittent"
    elif adi < 1.32 and cv2 >= 0.49:
        classification = "erratic"
    else:
        classification = "lumpy"

    return {
        "adi":            round(adi, 4),
        "cv2":            round(cv2, 4),
        "classification": classification,
    }


# ── Distribution fitting ────────────────────────────────────────────────────

def fit_distribution(series: np.ndarray) -> dict:
    """
    Fit candidate distributions to the non-zero values and select the best by AIC.

    Candidates: normal, lognormal, gamma, exponential.

    Returns:
        {best_fit: str, aic: float}
    """
    from scipy.stats import norm, lognorm, gamma, expon

    candidates = {
        "normal":      norm,
        "lognormal":   lognorm,
        "gamma":       gamma,
        "exponential": expon,
    }

    data = series[~np.isnan(series)]
    data = data[data > 0]
    if len(data) < 4:
        return {"best_fit": "unknown", "aic": float("nan")}

    best_name = "normal"
    best_aic  = float("inf")

    for name, dist in candidates.items():
        try:
            params = dist.fit(data)
            ll     = float(np.sum(dist.logpdf(data, *params)))
            aic    = 2 * len(params) - 2 * ll
            if np.isfinite(aic) and aic < best_aic:
                best_aic  = aic
                best_name = name
        except Exception:
            pass

    return {"best_fit": best_name, "aic": round(best_aic, 2)}


# ── Full report ─────────────────────────────────────────────────────────────

def distribution_report(series: np.ndarray) -> dict:
    """
    Complete distribution and descriptive-statistics report.

    Returns a merged dict of:
        descriptive_stats  +  normality_test  +  croston_classification  +  fit_distribution
    """
    result = descriptive_stats(series)
    result["normality"]         = normality_test(series)
    result["croston"]           = croston_classification(series)
    result["best_distribution"] = fit_distribution(series)["best_fit"]
    return result

"""Seasonality detection — dominant periods, ACF peaks, seasonal strength."""

from __future__ import annotations

import numpy as np


def detect_seasonality(series: np.ndarray, max_period: int = None) -> dict:
    """
    Detect seasonal periods in a time series using FFT and ACF.

    Args:
        series:     1-D array, no NaNs.
        max_period: Maximum period to consider (default: min(len/3, 365)).

    Returns:
        {
            dominant_period:   int | None,
            top_periods:       list[int]   up to 3 dominant periods by FFT power,
            acf_peaks:         list[{lag, acf}]  ACF local maxima > 0.1,
            seasonal_strength: float  0–1  (ACF at dominant period),
            classification:    "none" | "weak" | "moderate" | "strong",
        }
    """
    from statsmodels.tsa.stattools import acf as _acf

    n = len(series)
    if n < 6:
        return {
            "dominant_period": None,
            "top_periods": [],
            "acf_peaks": [],
            "seasonal_strength": 0.0,
            "classification": "none",
        }

    if max_period is None:
        max_period = min(n // 3, 365)
    max_period = max(2, max_period)

    # ── FFT periodogram ────────────────────────────────────────────────
    centered = series - np.mean(series)
    fft_vals = np.fft.rfft(centered)
    power    = np.abs(fft_vals) ** 2
    freqs    = np.fft.rfftfreq(n)

    # Convert frequencies to periods, keep range [2, max_period]
    with np.errstate(divide="ignore", invalid="ignore"):
        periods = np.where(freqs > 0, 1.0 / freqs, 0.0)

    mask = (periods >= 2) & (periods <= max_period)
    cand_periods = periods[mask]
    cand_power   = power[mask]

    top_periods: list[int] = []
    if len(cand_periods) > 0:
        order = np.argsort(cand_power)[::-1]
        seen: set[int] = set()
        for idx in order:
            p = int(round(cand_periods[idx]))
            if p >= 2 and p not in seen:
                seen.add(p)
                top_periods.append(p)
            if len(top_periods) == 3:
                break

    # ── ACF peaks ─────────────────────────────────────────────────────
    acf_lags = min(n // 2 - 1, max_period)
    if acf_lags < 1:
        acf_lags = 1
    acf_vals = _acf(series, nlags=acf_lags, fft=True)   # includes lag 0

    peaks: list[dict] = []
    for i in range(1, len(acf_vals) - 1):
        if acf_vals[i] > acf_vals[i - 1] and acf_vals[i] > acf_vals[i + 1]:
            if acf_vals[i] > 0.1:
                peaks.append({"lag": i, "acf": round(float(acf_vals[i]), 4)})
    peaks.sort(key=lambda x: x["acf"], reverse=True)

    # ── Dominant period & strength ─────────────────────────────────────
    dominant_period = top_periods[0] if top_periods else None

    strength = 0.0
    if dominant_period is not None and dominant_period < len(acf_vals):
        strength = max(0.0, float(acf_vals[dominant_period]))

    if strength < 0.10:
        classification = "none"
    elif strength < 0.30:
        classification = "weak"
    elif strength < 0.60:
        classification = "moderate"
    else:
        classification = "strong"

    return {
        "dominant_period":   dominant_period,
        "top_periods":       top_periods[:3],
        "acf_peaks":         peaks[:5],
        "seasonal_strength": round(strength, 4),
        "classification":    classification,
    }

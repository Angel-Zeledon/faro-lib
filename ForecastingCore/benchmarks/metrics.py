"""Accuracy metrics and demand segmentation for the benchmark.

Pure numpy, no engine imports, so the definitions are unit-testable against
known values. Conventions (stated once, used everywhere):

* ``y`` actuals, ``f`` forecasts, both 1-D arrays over the holdout horizon.
* Errors are ``f - y`` (positive bias = over-forecast).
* ``wape`` and ``bias`` are *ratios to total actual demand*; they are NaN when
  the actuals sum to zero (undefined, never silently 0).
* ``smape`` is the 0-200 % form; a 0/0 step counts as a perfect forecast.
* ``mase`` divides the holdout MAE by the in-sample seasonal-naive MAE.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional, Sequence

import numpy as np

# Syntetos-Boylan cut-offs (Syntetos, Boylan & Croston 2005).
ADI_CUTOFF = 1.32
CV2_CUTOFF = 0.49

SEGMENTS = ("smooth", "erratic", "intermittent", "lumpy")


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float).ravel()


def mae(y, f) -> float:
    y, f = _arr(y), _arr(f)
    return float(np.mean(np.abs(f - y)))


def wape(y, f) -> float:
    y, f = _arr(y), _arr(f)
    total = float(np.sum(np.abs(y)))
    if total == 0.0:
        return float("nan")
    return float(np.sum(np.abs(f - y)) / total)


def bias(y, f) -> float:
    """Relative bias: sum(f - y) / sum(y)."""
    y, f = _arr(y), _arr(f)
    total = float(np.sum(np.abs(y)))
    if total == 0.0:
        return float("nan")
    return float(np.sum(f - y) / total)


def smape(y, f) -> float:
    y, f = _arr(y), _arr(f)
    denom = np.abs(y) + np.abs(f)
    out = np.zeros_like(denom)
    nz = denom > 0
    out[nz] = 200.0 * np.abs(f - y)[nz] / denom[nz]
    return float(np.mean(out))


def mase_scale(insample, m: int = 1) -> float:
    """In-sample mean |y_t - y_{t-m}|. Falls back to m=1 if the series is
    shorter than m+1. Returns NaN when the scale is zero (flat series)."""
    x = _arr(insample)
    if len(x) <= m:
        m = 1
    if len(x) <= m:
        return float("nan")
    s = float(np.mean(np.abs(x[m:] - x[:-m])))
    return s if s > 0 else float("nan")


def mase(y, f, scale: float) -> float:
    if scale is None or not math.isfinite(scale) or scale <= 0:
        return float("nan")
    return mae(y, f) / scale


def pinball(y, q_forecast: Dict[float, Sequence[float]]) -> float:
    """Mean pinball (quantile) loss over the given quantile levels.

    ``q_forecast`` maps a level in (0, 1) to the forecast of that quantile.
    """
    y = _arr(y)
    losses = []
    for tau, qf in q_forecast.items():
        qf = _arr(qf)
        d = y - qf
        losses.append(np.mean(np.maximum(tau * d, (tau - 1.0) * d)))
    return float(np.mean(losses)) if losses else float("nan")


def coverage(y, lower, upper) -> float:
    """Fraction of actuals inside [lower, upper]."""
    y, lo, hi = _arr(y), _arr(lower), _arr(upper)
    return float(np.mean((y >= lo) & (y <= hi)))


def adi_cv2(history) -> tuple:
    """Average demand interval and squared CV of the non-zero demand sizes.

    ADI = n_periods / n_nonzero_periods. CV2 = (std / mean)^2 of the non-zero
    sizes (population std). A series with no demand returns (inf, nan).
    """
    x = _arr(history)
    nz = x[x > 0]
    if len(nz) == 0:
        return float("inf"), float("nan")
    adi = len(x) / len(nz)
    mean = float(np.mean(nz))
    cv2 = float((np.std(nz) / mean) ** 2) if mean > 0 else float("nan")
    return float(adi), cv2


def classify_segment(history) -> str:
    """Syntetos-Boylan quadrant of a training window."""
    adi, cv2 = adi_cv2(history)
    if not math.isfinite(adi) or not math.isfinite(cv2):
        return "intermittent"
    if adi < ADI_CUTOFF:
        return "smooth" if cv2 < CV2_CUTOFF else "erratic"
    return "intermittent" if cv2 < CV2_CUTOFF else "lumpy"


# Same default as the engine's DEFAULT_STOCKOUT_MULTIPLIER
# (forecasting_core/evaluation/metrics.py): a unit short costs 3x a unit over.
# Duplicated, not imported, to keep this module engine-free.
STOCKOUT_MULTIPLIER = 3.0


def asymmetric_cost_sum(y, f, stockout_multiplier: float = STOCKOUT_MULTIPLIER) -> float:
    """Total asymmetric cost: sum(over + multiplier * under). Pooled over
    series and divided by total actual demand it becomes a scale-free ratio
    (``cost_ratio`` in the report), the same objective the engine's tuner and
    champion race minimise."""
    y, f = _arr(y), _arr(f)
    return float(np.sum(np.maximum(f - y, 0.0) + stockout_multiplier * np.maximum(y - f, 0.0)))


def fva(err_method: float, err_naive: float) -> float:
    """Forecast value added: 1 - err_method / err_naive (positive = better than
    naive). NaN if the naive error is zero."""
    if err_naive is None or not math.isfinite(err_naive) or err_naive <= 0:
        return float("nan")
    return 1.0 - err_method / err_naive


def nanmean(values: Iterable[float]) -> float:
    v = [x for x in values if x is not None and math.isfinite(x)]
    return float(np.mean(v)) if v else float("nan")


def score_forecast(y, f, insample, season: int,
                   quantiles: Optional[Dict[float, Sequence[float]]] = None) -> dict:
    """All per-series metrics for one forecast against one holdout."""
    scale = mase_scale(insample, season)
    out = {
        "mae": mae(y, f),
        "mase": mase(y, f, scale),
        "wape": wape(y, f),
        "smape": smape(y, f),
        "bias": bias(y, f),
        "abs_err_sum": float(np.sum(np.abs(_arr(f) - _arr(y)))),
        "actual_sum": float(np.sum(np.abs(_arr(y)))),
        "signed_err_sum": float(np.sum(_arr(f) - _arr(y))),
        "cost_sum": asymmetric_cost_sum(y, f),
    }
    if quantiles:
        pb = pinball(y, quantiles)
        out["pinball"] = pb
        out["pinball_scaled"] = (pb / scale) if math.isfinite(scale) and scale > 0 else float("nan")
        lo = [t for t in quantiles if abs(t - 0.10) < 1e-9]
        hi = [t for t in quantiles if abs(t - 0.90) < 1e-9]
        if lo and hi:
            out["coverage80"] = coverage(y, quantiles[lo[0]], quantiles[hi[0]])
    return out

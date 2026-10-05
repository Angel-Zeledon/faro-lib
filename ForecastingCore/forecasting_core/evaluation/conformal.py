"""
Conformal prediction intervals, calibrated per forecast horizon.

The problem this replaces
-------------------------
Intervals used to be `value + norm.ppf(q) * std(residuals)`, where the residuals
were one-step errors and the same standard deviation was reused for every step
of the horizon. Three assumptions in one line, none of which hold for retail
demand:

  * that the error is normal — demand is non-negative, discrete and frequently
    zero, so its error distribution is skewed and bounded below;
  * that the error is symmetric — over- and under-forecasting are not mirror
    images once the series has a floor at zero;
  * that a 14-step-ahead error is the same size as a 1-step-ahead error — it is
    not, it is much larger, so the band was far too narrow exactly where the
    buyer most needs it wide.

What replaces it
----------------
Empirical quantiles of the actual backtest residuals, computed SEPARATELY for
each horizon. No distributional assumption, and the band widens with h because
the residuals do. Residuals are pooled across the catalogue in scaled units,
which is what makes this work for a SKU with two months of history: it inherits
the shape of the catalogue's error distribution instead of estimating its own
from a handful of points.

Coverage is finite-sample valid under exchangeability of the residuals. Demand
residuals are not perfectly exchangeable across time, so treat the guarantee as
strong-in-practice rather than exact — it is still a far better claim than the
normal approximation could make.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

# Below this many residuals a per-horizon quantile is noise. The horizon falls
# back to the pooled bank across all horizons, which is biased (it mixes easy
# short steps with hard long ones) but stable — and stating the fallback beats
# emitting a confident number computed from four points.
MIN_RESIDUALS_PER_HORIZON = 30


def _empirical_quantile(values: np.ndarray, q: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.quantile(values, q))


def horizon_bands(
    residuals_by_horizon: Mapping[int, np.ndarray],
    quantiles: Sequence[float],
    horizons: Optional[Iterable[int]] = None,
    pool_across_horizons: bool = True,
) -> Dict[int, Dict[float, float]]:
    """
    Residual offsets per (horizon, quantile), in the residuals' own units.

    Args:
        residuals_by_horizon: {h: array of (actual - predicted)}.
        quantiles:            e.g. [0.1, 0.5, 0.9].
        horizons:             horizons to emit; defaults to the keys present.
        pool_across_horizons: when a horizon has too few residuals, borrow the
                              bank pooled over every horizon.

                              Correct for PER-STEP residuals, whose scale is
                              broadly comparable across h. WRONG for CUMULATIVE
                              residuals, whose scale grows with h by
                              construction — pooling them averages the error of
                              a one-bucket sum with that of a fourteen-bucket
                              sum and returns a number that describes neither.
                              Callers working with cumulative residuals must
                              pass False.

    Returns:
        {h: {q: offset}} — add the offset to the point forecast to get that
        quantile. A positive offset at q>0.5 means the model under-forecasts
        there, which is exactly the asymmetry the normal approximation erased.
    """
    if not residuals_by_horizon:
        return {}

    pooled = np.concatenate([np.asarray(v, float) for v in residuals_by_horizon.values()
                             if np.asarray(v).size]) if residuals_by_horizon else np.array([])
    wanted = list(horizons) if horizons is not None else sorted(residuals_by_horizon)

    bands: Dict[int, Dict[float, float]] = {}
    for h in wanted:
        sample = np.asarray(residuals_by_horizon.get(int(h), []), dtype=float)
        sample = sample[np.isfinite(sample)]
        if pool_across_horizons and sample.size < MIN_RESIDUALS_PER_HORIZON:
            sample = pooled[np.isfinite(pooled)] if pooled.size else sample
        bands[int(h)] = {float(q): _empirical_quantile(sample, float(q)) for q in quantiles}
    return bands


def enforce_horizon_monotonic(
    bands: Dict[int, Dict[float, float]],
) -> Dict[int, Dict[float, float]]:
    """
    Make CUMULATIVE bands non-decreasing in the horizon (and non-increasing
    below the median).

    The uncertainty of a sum cannot shrink as terms are added to it. With few
    backtest origins the empirical quantile at some horizon can nonetheless come
    out below the horizon before it — sampling noise, not a real property — and
    a reorder point built on the dip would be short exactly where the lead time
    is longest. Imposing the structure the quantity is known to have costs
    nothing and removes that failure.
    """
    if not bands:
        return bands
    horizons = sorted(bands)
    quantile_levels = sorted({q for band in bands.values() for q in band})
    fixed = {h: dict(bands[h]) for h in horizons}

    for q in quantile_levels:
        running = None
        for h in horizons:
            value = fixed[h].get(q)
            if value is None:
                continue
            if running is not None:
                value = max(value, running) if q >= 0.5 else min(value, running)
            fixed[h][q] = value
            running = value
    return fixed


def apply_bands(
    point: float,
    bands: Mapping[float, float],
    scale: float = 1.0,
    floor: float = 0.0,
) -> Dict[str, float]:
    """
    Turn a point forecast plus residual offsets into named quantile keys.

    `scale` rescales offsets that were calibrated in normalized units back into
    this series' units — the step that lets one catalogue-wide residual bank
    serve series of wildly different volumes.
    """
    out: Dict[str, float] = {}
    for q, offset in sorted(bands.items()):
        value = max(floor, point + float(offset) * float(scale))
        out[f"q{int(round(float(q) * 100))}"] = round(value, 4)

    lows = [q for q in bands if q < 0.5]
    highs = [q for q in bands if q >= 0.5]
    out["lower"] = out[f"q{int(round(min(lows) * 100))}"] if lows else round(max(floor, point), 4)
    out["upper"] = out[f"q{int(round(max(highs) * 100))}"] if highs else round(max(floor, point), 4)
    out["p10"] = out.get("q10", out["lower"])
    out["p50"] = out.get("q50", round(max(floor, point), 4))
    out["p90"] = out.get("q90", out["upper"])
    return out


def is_monotonic(band: Mapping[float, float]) -> bool:
    """Quantiles must not cross — a q90 below a q50 is a broken interval."""
    items = [band[q] for q in sorted(band)]
    return all(a <= b + 1e-9 for a, b in zip(items, items[1:]))


def enforce_monotonic(band: Dict[float, float]) -> Dict[float, float]:
    """
    Repair crossed quantiles by taking a running maximum.

    Independently-estimated quantiles can cross when the sample is small; the
    cheapest correct repair is to make the sequence non-decreasing, which
    preserves every quantile that was already consistent.
    """
    ordered = sorted(band)
    running = -np.inf
    fixed: Dict[float, float] = {}
    for q in ordered:
        running = max(running, float(band[q]))
        fixed[q] = running
    return fixed


def lead_time_demand_quantile(
    point_forecast: Sequence[float],
    cumulative_bands: Mapping[int, Mapping[float, float]],
    lead_time_buckets: int,
    quantile: float,
    scale: float = 1.0,
) -> float:
    """
    The quantile of TOTAL demand over the lead time — what a reorder point needs.

    A safety stock protects against the demand that accumulates while the order
    is in transit, so the relevant random variable is the SUM over L buckets,
    not any single bucket. The classic `z * sigma_daily * sqrt(L)` is an
    approximation of this quantity that assumes normal, independent daily
    errors. Neither holds here, and both are avoidable: the rolling-origin
    backtest measured the cumulative error directly.

    Args:
        point_forecast:   Per-bucket point forecasts, index 0 = bucket 1.
        cumulative_bands: {L: {q: offset}} from horizon_bands() fitted on
                          CUMULATIVE residuals.
        lead_time_buckets: L (clamped to what the backtest actually covers).
        quantile:         Service level, e.g. 0.95.
        scale:            Series scale, if the bands are in normalized units.
    """
    if lead_time_buckets < 1:
        return 0.0
    horizon = min(int(lead_time_buckets), len(point_forecast))
    if horizon < 1:
        return 0.0
    expected = float(np.sum(np.asarray(point_forecast[:horizon], dtype=float)))

    available = sorted(cumulative_bands)
    if not available:
        return max(0.0, expected)
    # A lead time longer than anything the backtest measured falls back to the
    # longest horizon it did measure, rather than extrapolating a band nobody
    # verified. The caller sees a conservative-but-known number.
    key = horizon if horizon in cumulative_bands else max(
        [h for h in available if h <= horizon] or [available[0]]
    )
    band = cumulative_bands[key]
    offset = band.get(float(quantile))
    if offset is None:
        nearest = min(band, key=lambda q: abs(float(q) - float(quantile)))
        offset = band[nearest]
    return max(0.0, expected + float(offset) * float(scale))


def empirical_coverage(
    actuals: Sequence[float], lowers: Sequence[float], uppers: Sequence[float],
) -> float:
    """Fraction of actuals that landed inside the band — the honesty check."""
    a = np.asarray(actuals, float)
    lo = np.asarray(lowers, float)
    hi = np.asarray(uppers, float)
    if a.size == 0:
        return float("nan")
    return float(np.mean((a >= lo) & (a <= hi)))

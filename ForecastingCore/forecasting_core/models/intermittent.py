"""
Intermittent-demand models beyond Croston/SBA: TSB, ADIDA and IMAPA.

Pure numpy. Each one is a selectable statistical model with the same result
contract as `models/croston.py` (metrics over the held-out tail, a windowed
`cost_horizon`, the future `forecast`, `residuals`, and a `state` that
`reforecast` re-runs over newer history). None of them is in the default
model set and none of them is added by the router: they run only when the
user declares them.

TSB  (Teunter, Syntetos & Babai 2011)
    Croston smooths the demand SIZE and the INTERVAL between demands, and only
    updates when a demand happens. A SKU that stops selling therefore keeps its
    last forecast forever: no demand, no update. TSB smooths the PROBABILITY of
    a demand instead, and updates it EVERY period (towards 1 on a demand
    period, towards 0 on an empty one), so the forecast decays while a product
    dies. Forecast = probability x size.

ADIDA  (Nikolopoulos et al. 2011)
    Aggregate the series into non-overlapping buckets of `k` periods so the
    zeros mostly disappear, forecast the aggregate with simple exponential
    smoothing, and spread the bucket forecast back evenly (`/ k`). `k`
    defaults to the series' own average demand interval (ADI).

IMAPA  (Petropoulos & Kourentzes 2015)
    ADIDA at every aggregation level 1..k_max, averaged. Removes the need to
    pick one `k`.

All three return a FLAT forecast over the horizon, like Croston: they estimate
a demand rate, not a path.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Optional

import numpy as np

from forecasting_core.evaluation.metrics import evaluate_all
from forecasting_core.pipelines.progress import ticking

log = logging.getLogger(__name__)

# Smoothing constants. Fixed, as Croston's is: with the short, sparse histories
# these models exist for, an in-sample optimised constant mostly fits noise.
TSB_ALPHA = 0.1   # demand size
TSB_BETA = 0.1    # demand probability
SES_ALPHA = 0.1   # ADIDA / IMAPA base forecaster

# An aggregation level is only used when it leaves at least this many buckets
# to smooth; below that the "forecast" is one or two raw numbers.
MIN_AGG_BUCKETS = 3
# IMAPA's top level is capped so a series that sold once in 100 periods does
# not average 100 forecasts, most of them from a handful of buckets.
IMAPA_MAX_LEVEL = 12


def _clean(series) -> np.ndarray:
    """Float array, NaN treated as no demand, negatives clipped to zero.

    Demand cannot be negative; a return booked as negative sales would
    otherwise pull the rate below zero and the purchase quantity with it."""
    x = np.asarray(series, dtype=float).ravel()
    x = np.where(np.isfinite(x), x, 0.0)
    return np.maximum(x, 0.0)


def _ses_level(x: np.ndarray, alpha: float) -> float:
    """Final level of simple exponential smoothing, seeded with the mean.

    Seeding with the first value (the textbook choice) is a poor start for an
    aggregated intermittent series, whose first bucket is often zero: at
    alpha=0.1 the level then needs dozens of buckets to climb out — the same
    defect Croston's interval seed had (see `croston_forecast`)."""
    if len(x) == 0:
        return 0.0
    level = float(np.mean(x))
    for v in x:
        level = alpha * float(v) + (1.0 - alpha) * level
    return level


def average_demand_interval(series) -> float:
    """Periods per demand occasion (n / n_nonzero). inf for an all-zero series."""
    x = _clean(series)
    nz = int(np.count_nonzero(x))
    return float(len(x)) / nz if nz else float("inf")


# ---------------------------------------------------------------------------
# TSB
# ---------------------------------------------------------------------------

def tsb_forecast(series, alpha: float = TSB_ALPHA, beta: float = TSB_BETA,
                 n_ahead: int = 1) -> np.ndarray:
    """Teunter-Syntetos-Babai: smoothed probability x smoothed size.

    Initialisation: the probability starts at the series' own demand frequency
    and the size at the mean of its non-zero demands. Both then run through
    every period, so the END of the history weighs most — a series whose
    sales stopped a while ago ends with a probability well below its average.
    """
    x = _clean(series)
    n_ahead = max(int(n_ahead), 0)
    nz = x[x > 0]
    if len(nz) == 0:
        return np.zeros(n_ahead)
    prob = float(len(nz)) / float(len(x))
    size = float(np.mean(nz))
    for v in x:
        if v > 0:
            prob += beta * (1.0 - prob)
            size += alpha * (v - size)
        else:
            prob += beta * (0.0 - prob)
    return np.full(n_ahead, max(prob * size, 0.0))


# ---------------------------------------------------------------------------
# ADIDA / IMAPA
# ---------------------------------------------------------------------------

def _aggregate(x: np.ndarray, k: int) -> np.ndarray:
    """Non-overlapping sums of `k` periods, aligned to the END of the series:
    the most recent bucket is always complete, and the leftover oldest
    periods (len % k) are dropped rather than forming a partial bucket that
    would look like a sales drop."""
    usable = (len(x) // k) * k
    if usable == 0:
        return np.array([], dtype=float)
    return x[len(x) - usable:].reshape(-1, k).sum(axis=1)


def _max_level(n: int) -> int:
    """The largest aggregation level that still leaves MIN_AGG_BUCKETS buckets."""
    return max(1, n // MIN_AGG_BUCKETS)


def adida_level(series) -> int:
    """Default ADIDA aggregation level: the ADI rounded up, capped so at least
    MIN_AGG_BUCKETS buckets remain. 1 for a dense series (no aggregation)."""
    x = _clean(series)
    adi = average_demand_interval(x)
    k = int(math.ceil(adi)) if math.isfinite(adi) else _max_level(len(x))
    return int(min(max(k, 1), _max_level(len(x))))


def _adida_rate(x: np.ndarray, k: int, alpha: float) -> float:
    agg = _aggregate(x, k)
    if len(agg) == 0:
        return 0.0
    return max(_ses_level(agg, alpha), 0.0) / float(k)


def adida_forecast(series, k: Optional[int] = None, alpha: float = SES_ALPHA,
                   n_ahead: int = 1) -> np.ndarray:
    """ADIDA: aggregate by `k`, SES on the aggregate, disaggregate evenly.

    `k=None` uses `adida_level(series)`. A `k` too large for the series is
    capped (never silently replaced by 1) so at least MIN_AGG_BUCKETS buckets
    are smoothed."""
    x = _clean(series)
    n_ahead = max(int(n_ahead), 0)
    if len(x) == 0 or not np.any(x > 0):
        return np.zeros(n_ahead)
    level = adida_level(x) if k is None else int(min(max(int(k), 1), _max_level(len(x))))
    return np.full(n_ahead, _adida_rate(x, level, alpha))


def imapa_forecast(series, max_level: Optional[int] = None, alpha: float = SES_ALPHA,
                   n_ahead: int = 1) -> np.ndarray:
    """IMAPA: the mean of the ADIDA per-period rates at levels 1..max_level.

    `max_level=None` uses the ADIDA level (ADI rounded up), capped at
    IMAPA_MAX_LEVEL and at what the series length can fund."""
    x = _clean(series)
    n_ahead = max(int(n_ahead), 0)
    if len(x) == 0 or not np.any(x > 0):
        return np.zeros(n_ahead)
    top = adida_level(x) if max_level is None else int(max_level)
    top = int(min(max(top, 1), IMAPA_MAX_LEVEL, _max_level(len(x))))
    rates = [_adida_rate(x, k, alpha) for k in range(1, top + 1)]
    return np.full(n_ahead, float(np.mean(rates)))


# ---------------------------------------------------------------------------
# Runners — the contract `Pipeline` and `reforecast` consume
# ---------------------------------------------------------------------------

def forecast_from_state(model: str, state: dict, series, n_ahead: int) -> Optional[np.ndarray]:
    """Re-run a model over newer history with the constants it was trained
    with. None when `state` does not belong to `model`."""
    if not state or state.get("kind") != model:
        return None
    if model == "tsb":
        return tsb_forecast(series, alpha=float(state["alpha"]), beta=float(state["beta"]),
                            n_ahead=n_ahead)
    if model == "adida":
        # The level is re-derived from the new history on purpose: it is a
        # property of the series (its ADI), not a fitted parameter.
        return adida_forecast(series, alpha=float(state["alpha"]), n_ahead=n_ahead)
    if model == "imapa":
        return imapa_forecast(series, alpha=float(state["alpha"]), n_ahead=n_ahead)
    return None


def _run_core(model: str, fc: Callable[[np.ndarray, int], np.ndarray], state: dict,
              df, dt, target, group, train_ratio, min_rows, horizon: int, on_unit):
    """Shared per-SKU loop, identical in shape to `run_croston_core`."""
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in ticking(src, on_unit):
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float).values
        if len(series) < min_rows:
            continue
        cut = int(len(series) * train_ratio)
        if cut < 5 or cut >= len(series):
            continue
        key = str(sku) if sku is not None else "__all__"
        try:
            preds = fc(series[:cut], len(series) - cut)
            test_actual = series[cut:]
            result = evaluate_all(test_actual, preds)
            # Same windowing as every other family (see models/ets.py): the
            # champion race asks each model the same h-step question.
            result["cost_horizon"] = None
            result["horizon_steps"] = None
            if horizon > 0:
                h_steps = min(horizon, len(test_actual))
                result["cost_horizon"] = evaluate_all(
                    test_actual[:h_steps], preds[:h_steps]
                )["cost"]
                result["horizon_steps"] = h_steps
                result["forecast"] = fc(series, horizon)
                train_preds = fc(series[:cut], cut)
                result["residuals"] = series[:cut] - train_preds
                result["state"] = dict(state)
            results[key] = result
        except Exception as e:  # noqa: BLE001 — one SKU must not sink the family
            log.warning(f"{model} failed SKU={sku}: {e}")
    return results


def run_tsb_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                 alpha: float = TSB_ALPHA, beta: float = TSB_BETA,
                 horizon: int = 0, on_unit=None):
    return _run_core(
        "tsb", lambda s, n: tsb_forecast(s, alpha=alpha, beta=beta, n_ahead=n),
        {"kind": "tsb", "alpha": float(alpha), "beta": float(beta)},
        df, dt, target, group, train_ratio, min_rows, horizon, on_unit)


def run_adida_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                   alpha: float = SES_ALPHA, horizon: int = 0, on_unit=None):
    return _run_core(
        "adida", lambda s, n: adida_forecast(s, alpha=alpha, n_ahead=n),
        {"kind": "adida", "alpha": float(alpha)},
        df, dt, target, group, train_ratio, min_rows, horizon, on_unit)


def run_imapa_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                   alpha: float = SES_ALPHA, horizon: int = 0, on_unit=None):
    return _run_core(
        "imapa", lambda s, n: imapa_forecast(s, alpha=alpha, n_ahead=n),
        {"kind": "imapa", "alpha": float(alpha)},
        df, dt, target, group, train_ratio, min_rows, horizon, on_unit)

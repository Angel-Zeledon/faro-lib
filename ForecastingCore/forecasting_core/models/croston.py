"""Croston's method for intermittent demand series."""
import logging
import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all

from forecasting_core.pipelines.progress import ticking

log = logging.getLogger(__name__)


def _smooth(series, alpha):
    level = series[0]
    for v in series[1:]:
        level = alpha * v + (1 - alpha) * level
    return float(level)


def croston_forecast(series: np.ndarray, alpha: float = 0.1, n_ahead: int = 1) -> np.ndarray:
    """
    Syntetos-Boylan Approximation (SBA) of Croston's method: smooth the demand
    SIZES and the INTERVALS between them separately, divide, and de-bias the
    ratio.

    The intervals are the gaps BETWEEN demand instants. They used to be built
    with `np.diff(idx, prepend=idx[0])`, which makes the first interval 0 — a
    gap between a demand and itself. That zero is not a harmless extra point:
    `_smooth` seeds its level with the first value, so the whole interval
    estimate started at 0 and, at alpha=0.1, needed dozens of demand events to
    climb out. A too-small interval is a too-large demand rate, so the method
    over-forecast exactly the intermittent SKUs the router sends to it, and
    Croston is in the default model set.

    With a single demand in the whole series there is no observed gap at all.
    The honest estimate is then the series' own length per demand event, not a
    borrowed number: one sale in 90 buckets is a rate of 1/90, not of 1.

    Classic Croston forms the rate as z_lvl / p_lvl, the ratio of two
    independently exponentially-smoothed quantities. That ratio is a biased
    estimator of the true demand rate even when each of z_lvl and p_lvl is
    individually unbiased: E[z/p] != E[z]/E[p] by Jensen's inequality (1/p is
    convex in p), and the bias is systematically positive, i.e. classic
    Croston over-forecasts on average. Syntetos and Boylan (2001, 2005)
    derived the standard correction for this: multiply the rate by
    `(1 - alpha/2)`. That factor is what turns this function into SBA rather
    than classic Croston — it is applied unconditionally below, there is no
    "classic" mode, because the bias it corrects is inherent to the ratio
    itself, not a special case.
    """
    idx = np.where(series > 0)[0]
    if len(idx) == 0:
        return np.zeros(n_ahead)
    z = series[idx]
    p = np.diff(idx)
    z_lvl = _smooth(z, alpha)
    p_lvl = _smooth(p, alpha) if len(p) else float(max(len(series), 1))
    rate = (z_lvl / max(p_lvl, 1e-8)) * (1 - alpha / 2)
    return np.full(n_ahead, rate)


def estimate_intermittent_components(series: np.ndarray, alpha: float = 0.1):
    """
    The same two pieces `croston_forecast` smooths and collapses into one
    point-rate, kept SEPARATE — for `evaluation/compound.py`, which needs a
    distribution of the lead-time sum, not a single expected value.

    Returns (rate, sizes):

      * `rate` — probability of a demand occasion in a bucket, i.e.
        `1 / p_lvl`, the same alpha-smoothed inter-arrival level
        `croston_forecast` computes. No Syntetos-Boylan `(1 - alpha/2)`
        correction here: that factor corrects the bias of the RATIO
        `z_lvl / p_lvl` (Jensen's inequality on two independently smoothed
        quantities — see `croston_forecast`), and `rate` alone is not that
        ratio, so there is no such bias to correct.
      * `sizes` — the RAW positive demand values observed, unsmoothed. A
        point estimate of "typical size" throws away the shape a quantile of
        the sum needs; the raw sample is what a bootstrap of that shape is
        drawn from.

    Empty `sizes` (a series with no demand at all) signals the caller there
    is nothing to build a distribution from.
    """
    idx = np.where(series > 0)[0]
    if len(idx) == 0:
        return 0.0, np.array([], dtype=float)
    z = series[idx]
    p = np.diff(idx)
    p_lvl = _smooth(p, alpha) if len(p) else float(max(len(series), 1))
    rate = 1.0 / max(p_lvl, 1e-8)
    return float(rate), z.astype(float)


def run_croston_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                     alpha=0.1, horizon: int = 0, on_unit=None):
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in ticking(src, on_unit):
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float).values
        if len(series) < min_rows: continue
        cut = int(len(series) * train_ratio)
        if cut < 5 or cut >= len(series): continue
        key = str(sku) if sku is not None else "__all__"
        try:
            preds = croston_forecast(series[:cut], alpha=alpha, n_ahead=len(series) - cut)
            test_actual = series[cut:]
            result = evaluate_all(test_actual, preds)
            # See models/ets.py for the full rationale: `cost_horizon` is
            # windowed to `min(horizon, len(test))` so Croston is asked the
            # same h-step question as every other family, while
            # mae/rmse/wape/bias/mape/smape/cost keep covering the whole
            # held-out tail — a separate, still-useful question.
            result["cost_horizon"] = None
            result["horizon_steps"] = None
            if horizon > 0:
                h_steps = min(horizon, len(test_actual))
                result["cost_horizon"] = evaluate_all(
                    test_actual[:h_steps], preds[:h_steps]
                )["cost"]
                result["horizon_steps"] = h_steps
                result["forecast"] = croston_forecast(series, alpha=alpha, n_ahead=horizon)
                train_preds = croston_forecast(series[:cut], alpha=alpha, n_ahead=len(series[:cut]))
                result["residuals"] = series[:cut] - train_preds
            results[key] = result
        except Exception as e:
            log.warning(f"Croston failed SKU={sku}: {e}")
    return results

"""
Compound-distribution quantile of intermittent lead-time demand.

stability.md 17(b) closed the "wrong shape" hypothesis: the pooled empirical
L-sum bank was stratified by `classify_series` — the obvious next key — and
coverage did not move (52.8% -> 50.0% against a nominal 95%, measured end to
end through the real Pipeline). The document's own conclusion: "the next idea
should not be another key."

This is not another key. It replaces the INSTRUMENT, not the pooling. An
empirical quantile of the lead-time sum needs enough independent L-bucket
windows to describe its own 95th percentile — and a catalogue funds a handful
of rolling origins per series, nowhere near enough for a zero-inflated,
skewed sum. But the sum is not an opaque quantity: over L buckets it is a
COUNT of demand occasions times a SIZE per occasion, and both pieces are
estimable from far less data than the sum's tail needs — every single day is
one more observation of "did it happen" and, when it did, "how much" is one
more observation of the size distribution. A 400-day intermittent SKU gives
~400 data points about the rate and ~60-150 about the size; it gives 3-8
data points about the 95th percentile of a 15-day sum.

`forecasting_core.models.croston.estimate_intermittent_components` already
computes exactly these two pieces (it is what SBA Croston forecasts FROM,
just never exposed separately before). This module turns them into a Monte
Carlo distribution of the L-bucket cumulative sum, and `training/trainer.py`
folds that distribution into the SAME pooled-residual-bank machinery every
other stratum already uses — so `evaluation/conformal.py` and
`pipelines/pipeline.py::_demand_risk` need no new vocabulary to read it.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# Below this many observed demand occasions, a size DISTRIBUTION (not just a
# level) is a guess dressed as an estimate — bootstrapping five points cannot
# describe a tail. The caller falls back to whatever the pooled empirical
# bank already provides instead.
MIN_POSITIVE_OBSERVATIONS = 5

# Monte Carlo draws per (fold, model) call. Large enough that the 95th
# percentile of the simulated sum is stable to within a fraction of a unit on
# a catalogue this size, cheap enough (a few milliseconds, pure numpy) that
# running it once per fold per model costs nothing next to the model fits
# already paid for.
DEFAULT_N_SIM = 2000


def simulate_cumulative_demand(
    rate: float,
    sizes: np.ndarray,
    horizon: int,
    n_sim: int = DEFAULT_N_SIM,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """
    Monte Carlo draws of the cumulative demand over `horizon` buckets.

    Each of the `horizon` buckets is an independent Bernoulli(rate) "does a
    demand occasion happen", and when it does, its size is resampled with
    replacement from `sizes` — the series' own observed positive values.
    Nonparametric on purpose: no Poisson/Gamma/lognormal shape is assumed for
    the size, only that the buckets are independent and each occasion's size
    is drawn from what this SKU has actually shown.

    Returns an (n_sim, horizon) array; column h-1 holds `n_sim` draws of the
    sum over the FIRST h buckets (a running cumulative sum, matching what a
    lead time of h buckets is exposed to — see `lead_time_demand_quantile`
    in `conformal.py` for the same cumulative convention).

    `rate` and `sizes` are estimated from history that ends at a fold's
    cutoff (see `Trainer._bank_fold_cumulative_residuals`); nothing here
    looks forward.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    n_sim = max(1, int(n_sim))
    horizon = max(1, int(horizon))
    if sizes.size < MIN_POSITIVE_OBSERVATIONS or rate <= 0:
        return np.zeros((n_sim, horizon))
    rate = float(np.clip(rate, 0.0, 1.0))
    hits = rng.random((n_sim, horizon)) < rate
    draws = rng.choice(sizes, size=(n_sim, horizon), replace=True)
    per_bucket = np.where(hits, draws, 0.0)
    return np.cumsum(per_bucket, axis=1)

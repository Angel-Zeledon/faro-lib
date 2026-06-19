"""Croston's method for intermittent demand series."""
import logging
import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


def _smooth(series, alpha):
    level = series[0]
    for v in series[1:]:
        level = alpha * v + (1 - alpha) * level
    return float(level)


def croston_forecast(series: np.ndarray, alpha: float = 0.1, n_ahead: int = 1) -> np.ndarray:
    idx = np.where(series > 0)[0]
    if len(idx) == 0:
        return np.zeros(n_ahead)
    z = series[idx]
    p = np.diff(idx, prepend=idx[0])
    z_lvl = _smooth(z, alpha)
    p_lvl = _smooth(p, alpha) if len(p) > 1 else float(p[0])
    return np.full(n_ahead, z_lvl / max(p_lvl, 1e-8))


def run_croston_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                     alpha=0.1, horizon: int = 0):
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float).values
        if len(series) < min_rows: continue
        cut = int(len(series) * train_ratio)
        if cut < 5 or cut >= len(series): continue
        key = str(sku) if sku is not None else "__all__"
        try:
            preds = croston_forecast(series[:cut], alpha=alpha, n_ahead=len(series) - cut)
            result = evaluate_all(series[cut:], preds)
            if horizon > 0:
                result["forecast"] = croston_forecast(series, alpha=alpha, n_ahead=horizon)
                train_preds = croston_forecast(series[:cut], alpha=alpha, n_ahead=len(series[:cut]))
                result["residuals"] = series[:cut] - train_preds
            results[key] = result
        except Exception as e:
            log.warning(f"Croston failed SKU={sku}: {e}")
    return results

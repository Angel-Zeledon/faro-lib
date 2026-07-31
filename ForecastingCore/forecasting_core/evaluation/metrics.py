"""
Evaluation metrics for forecasting.

Functions:
    mae, rmse, wape, bias, mape, smape — scalar metrics
    evaluate_all                        — all metrics at once
    evaluate_by_horizon                 — MAE@h for each step
    forecast_intervals                  — empirical P50/P90/P95
    business_loss                       — cost-weighted error

Example:
    from forecasting_core.evaluation.metrics import evaluate_all
    metrics = evaluate_all(y_true, y_pred)
    # → {"mae": 4.2, "rmse": 5.8, "wape": 0.12, "bias": -0.3, "mape": 0.09, "smape": 0.10}
"""

import numpy as np
from typing import Dict, List


def mae(y, yhat) -> float:
    return float(np.mean(np.abs(np.array(y, float) - np.array(yhat, float))))

def rmse(y, yhat) -> float:
    return float(np.sqrt(np.mean((np.array(y, float) - np.array(yhat, float)) ** 2)))

def wape(y, yhat) -> float:
    y, yhat = np.array(y, float), np.array(yhat, float)
    return float(np.sum(np.abs(y - yhat)) / (np.sum(np.abs(y)) + 1e-8))

def bias(y, yhat) -> float:
    """Mean signed error. Positive = over-forecast, negative = under-forecast."""
    return float(np.mean(np.array(yhat, float) - np.array(y, float)))

def mape(y, yhat, eps: float = 1e-8) -> float:
    y, yhat = np.array(y, float), np.array(yhat, float)
    return float(np.mean(np.abs((y - yhat) / (np.abs(y) + eps))))

def smape(y, yhat, eps: float = 1e-8) -> float:
    """Symmetric MAPE. Bounded in [0, 2]; avoids MAPE's blow-up near y≈0."""
    y, yhat = np.array(y, float), np.array(yhat, float)
    return float(np.mean(2 * np.abs(y - yhat) / (np.abs(y) + np.abs(yhat) + eps)))

# How much worse a stockout is than the same number of units sitting in the
# warehouse. Mirrors BusinessConfig.stockout_cost_multiplier so a caller that
# does not pass one still gets the product's own default rather than a
# symmetric assumption nobody in this business would make.
DEFAULT_STOCKOUT_MULTIPLIER = 3.0

# The order in which metrics are tried when crowning each SKU's champion, best
# candidate first. Every layer that picks "the model this SKU is planned from"
# MUST walk this same list.
#
# Two layers do: `Pipeline._select_champions` here, and
# `backend/inventory/service.py::best_model_by_sku`. They are allowed to drift
# for exactly as long as it takes someone to add a metric to one of them —
# measured on a real 13-SKU session, adding `cost_horizon` to the engine alone
# made the two disagree on 8 of them, so the engine computed its
# recommendations from one model while the semáforo and the purchase quantity
# came from another, and the accuracy on screen described a third thing.
#
# `cost_horizon` leads because it is the only column measured the same way for
# every model family. `cost` is the one-step-ahead version, kept for results
# produced before that existed; `wape` and `mae` are the pre-cost fallbacks.
CHAMPION_METRIC_ORDER = ("cost_horizon", "cost", "wape", "mae")


def asymmetric_cost(y, yhat, stockout_multiplier: float = DEFAULT_STOCKOUT_MULTIPLIER) -> float:
    """
    Mean per-unit cost of the error, counting a shortfall as worse than a surplus.

    MAE says a forecast that is 10 units low and one that is 10 units high are
    equally wrong. For a distributor they are not: the surplus costs warehouse
    space and cash, the shortfall costs the sale, sometimes the customer. Every
    metric in this module except this one is blind to that difference, and the
    per-SKU champion used to be picked with one of the blind ones.

    Normalised by the number of observations so it is comparable across series
    of different lengths (business_loss returns the un-normalised total).
    """
    y_arr, yhat_arr = np.array(y, float), np.array(yhat, float)
    if y_arr.size == 0:
        return float("nan")
    over = np.maximum(yhat_arr - y_arr, 0.0)
    under = np.maximum(y_arr - yhat_arr, 0.0)
    return float(np.mean(over + float(stockout_multiplier) * under))


def pinball_loss(y, yhat, quantile: float) -> float:
    """
    Pinball (quantile) loss — the proper scoring rule for a quantile forecast.

    A reorder point is not a mean, it is a high quantile of demand: the number
    that covers demand `service_level` of the time. Pinball loss is the metric
    that quantity is actually optimal for, so it is what a quantile forecast has
    to be scored with. Scoring a q95 forecast with MAE rewards it for being
    close to the middle, which is precisely what it must not be.
    """
    y_arr, yhat_arr = np.array(y, float), np.array(yhat, float)
    if y_arr.size == 0:
        return float("nan")
    diff = y_arr - yhat_arr
    q = float(quantile)
    return float(np.mean(np.maximum(q * diff, (q - 1.0) * diff)))


def evaluate_all(y, yhat, stockout_multiplier: float = DEFAULT_STOCKOUT_MULTIPLIER) -> Dict[str, float]:
    """All metrics in one dict, including the asymmetric `cost`."""
    return {"mae": mae(y, yhat), "rmse": rmse(y, yhat),
            "wape": wape(y, yhat), "bias": bias(y, yhat),
            "mape": mape(y, yhat), "smape": smape(y, yhat),
            "cost": asymmetric_cost(y, yhat, stockout_multiplier)}


def evaluate_by_horizon(y, yhat) -> Dict[str, float]:
    """MAE at each forecast step h=1..H."""
    y, yhat = np.array(y, float), np.array(yhat, float)
    return {f"mae@{h+1}": float(np.abs(y[h] - yhat[h])) for h in range(len(y))}


def forecast_intervals(
    residuals: np.ndarray,
    forecast: np.ndarray,
    quantiles: List[float] = (0.5, 0.9, 0.95),
) -> Dict[str, np.ndarray]:
    """
    Empirical prediction intervals from in-sample residuals.

    Args:
        residuals: In-sample errors (y_true - y_pred).
        forecast:  Point forecast array.
        quantiles: Coverage levels to compute.

    Returns:
        {"p50_lo": [...], "p50_hi": [...], "p90_lo": [...], ...}
    """
    result = {}
    for q in quantiles:
        bound = float(np.quantile(np.abs(residuals), q))
        key = f"p{int(q * 100)}"
        result[f"{key}_lo"] = forecast - bound
        result[f"{key}_hi"] = forecast + bound
    return result


def global_wape(y, yhat) -> float:
    """WAPE aggregated across all observations (alias of wape — kept for call-site clarity)."""
    return wape(y, yhat)


def business_loss(
    y: np.ndarray,
    yhat: np.ndarray,
    overforecast_cost: float = 1.0,
    underforecast_cost: float = 3.0,
) -> float:
    """
    Asymmetric cost-weighted loss.

    Args:
        y:                  Actual demand.
        yhat:               Forecast.
        overforecast_cost:  Cost per unit of over-forecast (holding/waste).
        underforecast_cost: Cost per unit of under-forecast (stockout/lost sales).

    Returns:
        Total business loss (lower is better).
    """
    y, yhat = np.array(y, float), np.array(yhat, float)
    over  = np.maximum(yhat - y, 0)
    under = np.maximum(y - yhat, 0)
    return float(np.sum(overforecast_cost * over + underforecast_cost * under))

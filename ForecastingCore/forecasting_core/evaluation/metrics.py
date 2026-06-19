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

def evaluate_all(y, yhat) -> Dict[str, float]:
    """Returns all metrics in one dict."""
    return {"mae": mae(y, yhat), "rmse": rmse(y, yhat),
            "wape": wape(y, yhat), "bias": bias(y, yhat),
            "mape": mape(y, yhat), "smape": smape(y, yhat)}


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

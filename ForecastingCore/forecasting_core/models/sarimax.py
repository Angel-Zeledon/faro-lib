"""
SARIMAX model wrapper for the forecasting_core pipeline.

SARIMAX (Seasonal AutoRegressive Integrated Moving Average with eXogenous variables)
is suited for series with:
  - Seasonal patterns (use seasonal_order)
  - External drivers (promotions, price, weather — use exog_cols)

If no exogenous columns are provided the model degrades to SARIMA, which is
redundant with ARIMA. The function logs a warning but still runs.

Returns the same interface as all other stat models:
    {sku: {"mae": float, "forecast": ndarray, "residuals": ndarray}}
"""

import logging
import numpy as np

log = logging.getLogger(__name__)


def _safe_order(value, default):
    """Parse order from list/tuple/string, return default on failure."""
    try:
        if isinstance(value, (list, tuple)):
            return tuple(int(x) for x in value)
        if isinstance(value, str):
            return tuple(int(x) for x in value.split(","))
    except Exception:
        pass
    return default


def run_sarimax_core(
    df,
    dt: str,
    target: str,
    group,
    train_ratio: float = 0.8,
    min_rows: int = 20,
    seasonal_period: int = 7,
    exog_cols=None,
    order=(1, 1, 1),
    seasonal_order=None,
    horizon: int = 0,
):
    """
    Train a SARIMAX model per SKU.

    Signature matches other stat model run_*_core functions.

    Args:
        df:              Raw DataFrame.
        dt:              Date column name.
        target:          Target column name.
        group:           Group / SKU column (None = single series).
        train_ratio:     Fraction of data for training.
        min_rows:        Minimum rows required per SKU.
        seasonal_period: Seasonal period — used in seasonal_order (S).
        exog_cols:       List of exogenous column names. If None or empty,
                         runs as SARIMA (no exogenous regressors).
        order:           (p, d, q) ARIMA order.
        seasonal_order:  (P, D, Q, S) seasonal order. Defaults to
                         (1, 1, 1, seasonal_period).
        horizon:         Steps ahead for future forecast (0 = eval only).

    Returns:
        {sku: {"mae": float}} when horizon=0
        {sku: {"mae": float, "forecast": ndarray, "residuals": ndarray}} when horizon>0
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    import warnings

    exog_cols = [c for c in (exog_cols or []) if c in df.columns]
    if not exog_cols:
        log.warning(
            "SARIMAX called with no exogenous columns — running as SARIMA. "
            "Consider using ARIMA instead for better performance."
        )

    s_order = seasonal_order or (1, 1, 1, seasonal_period)
    order   = _safe_order(order, (1, 1, 1))

    results = {}
    src = df.groupby(group) if group else [(None, df)]

    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float)

        if len(series) < min_rows:
            continue

        cut = int(len(series) * train_ratio)
        if cut < max(5, seasonal_period * 2) or cut >= len(series):
            continue

        # Exogenous arrays (aligned to series)
        exog_train = g[exog_cols].iloc[:cut].values if exog_cols else None
        exog_test  = g[exog_cols].iloc[cut:].values if exog_cols else None

        key = str(sku) if sku is not None else "__all__"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model_fit = SARIMAX(
                    series.iloc[:cut],
                    exog=exog_train,
                    order=order,
                    seasonal_order=s_order,
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                ).fit(disp=False, maxiter=100)

            # Evaluation on test window
            fc_test = model_fit.forecast(
                steps=len(series) - cut,
                exog=exog_test,
            )
            from forecasting_core.evaluation.metrics import evaluate_all
            result = evaluate_all(series.iloc[cut:].values, fc_test.values)

            if horizon > 0:
                # Re-fit on full series for future forecast
                exog_full = g[exog_cols].values if exog_cols else None
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    full_fit = SARIMAX(
                        series,
                        exog=exog_full,
                        order=order,
                        seasonal_order=s_order,
                        enforce_stationarity=False,
                        enforce_invertibility=False,
                    ).fit(disp=False, maxiter=100)

                # Future exog: repeat last row (naive fill) if exog available
                if exog_cols:
                    last_exog = g[exog_cols].iloc[[-1]].values
                    future_exog = np.repeat(last_exog, horizon, axis=0)
                else:
                    future_exog = None

                result["forecast"] = full_fit.forecast(
                    steps=horizon, exog=future_exog
                ).values

                # In-sample residuals
                in_sample = model_fit.predict(start=0, end=cut - 1, exog=exog_train)
                result["residuals"] = series.iloc[:cut].values - in_sample.values

            results[key] = result

        except Exception as e:
            log.warning(f"SARIMAX failed SKU={sku}: {e}")

    return results

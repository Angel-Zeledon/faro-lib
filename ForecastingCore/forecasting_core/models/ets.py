"""ETS / Holt-Winters wrapper for the forecasting_core pipeline."""
import logging
from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


def run_ets_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                 horizon: int = 0):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float).values
        if len(series) < min_rows: continue
        cut = int(len(series) * train_ratio)
        if cut < seasonal_period * 2 or cut >= len(series): continue
        train, test = series[:cut], series[cut:]
        use_seasonal = len(train) >= seasonal_period * 2 and (train > 0).all()
        key = str(sku) if sku is not None else "__all__"
        try:
            model = ExponentialSmoothing(
                train, trend="add",
                seasonal="add" if use_seasonal else None,
                seasonal_periods=seasonal_period if use_seasonal else None,
                initialization_method="estimated",
            ).fit(optimized=True)
            result = evaluate_all(test, model.forecast(len(test)))
            if horizon > 0:
                full_model = ExponentialSmoothing(
                    series, trend="add",
                    seasonal="add" if use_seasonal else None,
                    seasonal_periods=seasonal_period if use_seasonal else None,
                    initialization_method="estimated",
                ).fit(optimized=True)
                result["forecast"] = full_model.forecast(horizon)
                result["residuals"] = train - model.fittedvalues
            results[key] = result
        except Exception as e:
            log.warning(f"ETS failed SKU={sku}: {e}")
    return results

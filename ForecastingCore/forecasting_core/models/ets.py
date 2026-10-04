"""ETS / Holt-Winters wrapper for the forecasting_core pipeline."""
import logging
from forecasting_core.evaluation.metrics import evaluate_all

from forecasting_core.pipelines.progress import ticking

log = logging.getLogger(__name__)


def run_ets_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                 horizon: int = 0, on_unit=None):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in ticking(src, on_unit):
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
            test_forecast = model.forecast(len(test))
            result = evaluate_all(test, test_forecast)
            # `cost_horizon` has to answer the same question for every model
            # family: "how wrong is this model over the first `horizon` steps
            # of the held-out window?" (docs/stability.md #17(d)). ETS forecasts
            # its WHOLE test tail above — `len(test)` steps, which on a long
            # series is far more than `horizon` — and used to hand that number
            # straight to `cost_horizon`, so a statistical model answering a
            # 90-step question was ranked against an ML model answering a
            # 30-step one. `mae`/`rmse`/`wape`/`bias`/`mape`/`smape`/`cost`
            # above are left covering the whole tail on purpose: "how does ETS
            # do over everything it was asked to forecast" is a real, useful
            # question, distinct from the champion-race one, and nothing else
            # in the product reads those columns as if they were windowed.
            # `cost_horizon` alone is windowed to `min(horizon, len(test))`,
            # and `horizon_steps` says how many steps that was — a tail
            # shorter than `horizon` is scored over what it has rather than
            # padded or silently averaged over a longer window.
            result["cost_horizon"] = None
            result["horizon_steps"] = None
            if horizon > 0:
                h_steps = min(horizon, len(test))
                result["cost_horizon"] = evaluate_all(
                    test[:h_steps], test_forecast[:h_steps]
                )["cost"]
                result["horizon_steps"] = h_steps
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

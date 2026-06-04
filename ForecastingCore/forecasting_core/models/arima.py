"""ARIMA model wrapper for the forecasting_core pipeline."""
import logging
import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


def _parse_order(value, default=(5, 1, 2)):
    if isinstance(value, (list, tuple)): return tuple(int(x) for x in value)
    if isinstance(value, str): return tuple(int(x) for x in value.split(","))
    return default


def run_arima_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                   order=(5, 1, 2), horizon: int = 0):
    from statsmodels.tsa.arima.model import ARIMA
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float)
        if len(series) < min_rows: continue
        cut = int(len(series) * train_ratio)
        if cut < 5 or cut >= len(series): continue
        key = str(sku) if sku is not None else "__all__"
        try:
            model = ARIMA(series.iloc[:cut], order=order).fit()
            preds = model.forecast(len(series) - cut)
            mae_val = evaluate_all(series.iloc[cut:].values, preds.values)["mae"]
            result = {"mae": mae_val}
            if horizon > 0:
                full_model = ARIMA(series, order=order).fit()
                fc_obj = full_model.get_forecast(steps=horizon)
                fc_mean = fc_obj.predicted_mean.values
                ci = fc_obj.conf_int(alpha=0.2)  # 80% CI → p10/p90
                result["forecast"] = fc_mean
                result["p50"] = fc_mean
                result["p10"] = np.maximum(0.0, ci.iloc[:, 0].values)
                result["p90"] = np.maximum(0.0, ci.iloc[:, 1].values)
                in_sample = model.predict(start=0, end=cut - 1)
                result["residuals"] = series.iloc[:cut].values - in_sample.values
            results[key] = result
        except Exception as e:
            log.warning(f"ARIMA failed SKU={sku}: {e}")
    return results

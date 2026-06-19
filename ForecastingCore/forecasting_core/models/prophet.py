"""Prophet model wrapper for the forecasting_core pipeline."""
import logging
import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


def run_prophet_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                     regressors=None, horizon: int = 0):
    from prophet import Prophet
    import pandas as _pd
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    regressors = regressors or []
    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        if len(g) < min_rows: continue
        avail = [c for c in regressors if c in g.columns]
        d = g[[dt, target] + avail].rename(columns={dt: "ds", target: "y"})
        cut = int(len(d) * train_ratio)
        if cut < 5 or cut >= len(d): continue
        key = str(sku) if sku is not None else "__all__"
        try:
            m = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False)
            for col in avail: m.add_regressor(col)
            m.fit(d.iloc[:cut])
            fc = m.predict(d.iloc[cut:][["ds"] + avail])
            result = evaluate_all(d.iloc[cut:]["y"].values, fc["yhat"].values)
            if horizon > 0:
                full_m = Prophet(yearly_seasonality=True, weekly_seasonality=True,
                                 daily_seasonality=False)
                for col in avail: full_m.add_regressor(col)
                full_m.fit(d)
                freq = _pd.infer_freq(d["ds"]) or "D"
                future = full_m.make_future_dataframe(periods=horizon, freq=freq)
                future_fc = full_m.predict(future)
                fh = future_fc.iloc[-horizon:]
                result["forecast"] = fh["yhat"].values
                result["p50"] = fh["yhat"].values
                result["p10"] = np.maximum(0.0, fh["yhat_lower"].values)
                result["p90"] = np.maximum(0.0, fh["yhat_upper"].values)
                train_fc = m.predict(d.iloc[:cut][["ds"] + avail])
                result["residuals"] = d.iloc[:cut]["y"].values - train_fc["yhat"].values
            results[key] = result
        except Exception as e:
            log.warning(f"Prophet failed SKU={sku}: {e}")
    return results

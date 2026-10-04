"""Prophet model wrapper for the forecasting_core pipeline."""
import logging
import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all

from forecasting_core.pipelines.progress import ticking

log = logging.getLogger(__name__)


def _build(regressors, holiday_country: str):
    """One Prophet, configured once.

    Both the model that is graded and the model that forecasts have to be
    built the same way; when they were built separately, a change to one was a
    change to a model nobody scored.

    `yearly_seasonality` and `weekly_seasonality` are left at Prophet's own
    'auto'. They used to be forced to True, which meant a tenant with eight or
    fourteen months of history got a yearly cycle fitted to a single incomplete
    pass and then extrapolated — Prophet's 'auto' exists to switch that off
    below two years for exactly this reason. Forced weekly was the same mistake
    on the other axis: on a session resampled to monthly buckets there is no
    week to fit.
    """
    from prophet import Prophet

    m = Prophet(yearly_seasonality="auto", weekly_seasonality="auto",
                daily_seasonality=False)
    # The product knows which country's calendar this tenant sells in and hands
    # it to the ML path (features/calendar.py). Prophet — the model family whose
    # whole argument is calendar effects — was the one flying without it.
    if holiday_country:
        try:
            m.add_country_holidays(country_name=holiday_country)
        except Exception as e:
            log.warning(f"Prophet: no holiday table for {holiday_country!r} ({e})")
    for col in regressors:
        m.add_regressor(col)
    return m


def run_prophet_core(df, dt, target, group, train_ratio, min_rows, seasonal_period,
                     regressors=None, horizon: int = 0, holiday_country: str = "", on_unit=None):
    import pandas as _pd
    results = {}
    src = df.groupby(group) if group else [(None, df)]
    regressors = regressors or []
    for sku, g in ticking(src, on_unit):
        g = g.sort_values(dt).reset_index(drop=True)
        if len(g) < min_rows: continue
        avail = [c for c in regressors if c in g.columns]
        d = g[[dt, target] + avail].rename(columns={dt: "ds", target: "y"})
        cut = int(len(d) * train_ratio)
        if cut < 5 or cut >= len(d): continue
        key = str(sku) if sku is not None else "__all__"
        try:
            m = _build(avail, holiday_country)
            m.fit(d.iloc[:cut])
            fc = m.predict(d.iloc[cut:][["ds"] + avail])
            test_actual = d.iloc[cut:]["y"].values
            test_pred = fc["yhat"].values
            result = evaluate_all(test_actual, test_pred)
            # See models/ets.py for the full rationale: `cost_horizon` is
            # windowed to `min(horizon, len(test))` so Prophet is asked the
            # same h-step question as every other family, while
            # mae/rmse/wape/bias/mape/smape/cost keep covering the whole
            # held-out tail — a separate, still-useful question.
            result["cost_horizon"] = None
            result["horizon_steps"] = None
            if horizon > 0:
                h_steps = min(horizon, len(test_actual))
                result["cost_horizon"] = evaluate_all(
                    test_actual[:h_steps], test_pred[:h_steps]
                )["cost"]
                result["horizon_steps"] = h_steps
                full_m = _build(avail, holiday_country)
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

### m4_weekly (M4 Weekly (Mcompetitions/M4-methods), seeded sample of 60 of 359 eligible series (seed=42), last <= 260 observations of train+test; dates are synthetic (M4 ships none))

series=30, origins=2, horizon=13, scored series-origins=60, engine produced no forecast for 0 series-origins.

**Segment `all`** (n=60 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.464 | 4.0% | 5.3 | 0.5% | 37.7% | 63.8% | 40/20 | 0.191 | 68.1% |
| model:arima | 0.536 | 1.9% | 2.5 | 0.6% | 10.1% | 51.5% | 16/8 | n/a | n/a |
| model:ensemble | 0.458 | 3.7% | 4.8 | -0.2% | 42.7% | 66.7% | 43/17 | n/a | n/a |
| model:ets | 0.387 | 4.9% | 7.1 | -0.8% | 47.6% | 69.1% | 29/7 | n/a | n/a |
| model:lightgbm | 0.541 | 4.5% | 6.1 | -0.7% | 30.2% | 59.4% | 31/29 | n/a | n/a |
| model:xgboost | 0.585 | 4.8% | 7.4 | -0.5% | 25.7% | 56.8% | 30/30 | n/a | n/a |
| moving_average | 0.723 | 6.6% | 9.6 | -2.4% | -1.4% | 41.0% | 31/29 | n/a | n/a |
| naive | 0.712 | 6.5% | 9.2 | -1.7% | n/a | n/a | - | n/a | n/a |
| seasonal_naive | 0.981 | 11.1% | 11.4 | -7.4% | -71.9% | 0.0% | 18/42 | n/a | n/a |

**Segment `smooth`** (n=60 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.464 | 4.0% | 5.3 | 0.5% | 37.7% | 63.8% | 40/20 | 0.191 | 68.1% |
| model:arima | 0.536 | 1.9% | 2.5 | 0.6% | 10.1% | 51.5% | 16/8 | n/a | n/a |
| model:ensemble | 0.458 | 3.7% | 4.8 | -0.2% | 42.7% | 66.7% | 43/17 | n/a | n/a |
| model:ets | 0.387 | 4.9% | 7.1 | -0.8% | 47.6% | 69.1% | 29/7 | n/a | n/a |
| model:lightgbm | 0.541 | 4.5% | 6.1 | -0.7% | 30.2% | 59.4% | 31/29 | n/a | n/a |
| model:xgboost | 0.585 | 4.8% | 7.4 | -0.5% | 25.7% | 56.8% | 30/30 | n/a | n/a |
| moving_average | 0.723 | 6.6% | 9.6 | -2.4% | -1.4% | 41.0% | 31/29 | n/a | n/a |
| naive | 0.712 | 6.5% | 9.2 | -1.7% | n/a | n/a | - | n/a | n/a |
| seasonal_naive | 0.981 | 11.1% | 11.4 | -7.4% | -71.9% | 0.0% | 18/42 | n/a | n/a |

Champion model chosen by the engine: ets=25, xgboost=13, lightgbm=13, arima=9

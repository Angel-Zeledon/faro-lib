### m4_monthly (M4 Monthly (Mcompetitions/M4-methods), seeded sample of 60 of 47981 eligible series (seed=42), last <= 144 observations of train+test; dates are synthetic (M4 ships none))

series=45, origins=2, horizon=18, scored series-origins=90, engine produced no forecast for 0 series-origins.

**Segment `all`** (n=90 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.878 | 10.3% | 11.8 | 1.7% | 10.8% | 18.1% | 57/33 | 0.300 | 60.1% |
| model:arima | 0.649 | 11.1% | 10.8 | 0.1% | 8.2% | 21.3% | 19/12 | n/a | n/a |
| model:ensemble | 0.870 | 9.7% | 11.4 | 1.4% | 15.6% | 22.6% | 53/37 | n/a | n/a |
| model:ets | 0.917 | 8.8% | 11.9 | 2.5% | 21.3% | 24.5% | 44/15 | n/a | n/a |
| model:lightgbm | 1.142 | 11.7% | 13.2 | 1.4% | -0.9% | 7.4% | 41/49 | n/a | n/a |
| model:xgboost | 1.131 | 11.4% | 13.5 | 0.3% | 1.1% | 9.3% | 34/56 | n/a | n/a |
| moving_average | 1.001 | 10.7% | 12.2 | -0.2% | 7.3% | 14.9% | 41/48 | n/a | n/a |
| naive | 1.091 | 11.5% | 14.2 | 1.1% | n/a | n/a | - | n/a | n/a |
| seasonal_naive | 1.164 | 12.6% | 13.6 | 0.3% | -9.0% | 0.0% | 38/52 | n/a | n/a |

**Segment `smooth`** (n=90 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.878 | 10.3% | 11.8 | 1.7% | 10.8% | 18.1% | 57/33 | 0.300 | 60.1% |
| model:arima | 0.649 | 11.1% | 10.8 | 0.1% | 8.2% | 21.3% | 19/12 | n/a | n/a |
| model:ensemble | 0.870 | 9.7% | 11.4 | 1.4% | 15.6% | 22.6% | 53/37 | n/a | n/a |
| model:ets | 0.917 | 8.8% | 11.9 | 2.5% | 21.3% | 24.5% | 44/15 | n/a | n/a |
| model:lightgbm | 1.142 | 11.7% | 13.2 | 1.4% | -0.9% | 7.4% | 41/49 | n/a | n/a |
| model:xgboost | 1.131 | 11.4% | 13.5 | 0.3% | 1.1% | 9.3% | 34/56 | n/a | n/a |
| moving_average | 1.001 | 10.7% | 12.2 | -0.2% | 7.3% | 14.9% | 41/48 | n/a | n/a |
| naive | 1.091 | 11.5% | 14.2 | 1.1% | n/a | n/a | - | n/a | n/a |
| seasonal_naive | 1.164 | 12.6% | 13.6 | 0.3% | -9.0% | 0.0% | 38/52 | n/a | n/a |

Champion model chosen by the engine: ets=45, lightgbm=17, xgboost=15, arima=13

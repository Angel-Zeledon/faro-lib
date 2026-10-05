### synthetic (synthetic retail generator (seed=42, 90 series, 156 weekly periods, types=smooth/seasonal/erratic/intermittent/lumpy/promo))

series=60, origins=2, horizon=8, scored series-origins=120, engine produced no forecast for 0 series-origins.

**Segment `all`** (n=120 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.858 | 22.8% | 50.4 | -4.0% | 31.5% | 21.1% | 54/46 | 0.306 | 76.6% |
| model:arima | 0.770 | 138.7% | 189.8 | -29.2% | -8.6% | 35.7% | 1/9 | n/a | n/a |
| model:croston | 0.905 | 105.9% | 88.7 | -85.5% | 34.3% | 31.7% | 9/10 | n/a | n/a |
| model:ensemble | 0.819 | 22.1% | 50.3 | -4.3% | 33.7% | 23.7% | 57/43 | n/a | n/a |
| model:ets | 0.823 | 22.1% | 50.3 | -4.6% | 33.6% | 23.6% | 60/40 | n/a | n/a |
| model:lightgbm | 0.851 | 23.1% | 51.0 | -5.0% | 30.7% | 20.2% | 54/46 | n/a | n/a |
| model:xgboost | 0.879 | 24.3% | 51.1 | -3.3% | 27.1% | 16.1% | 47/53 | n/a | n/a |
| moving_average | 1.036 | 25.2% | 62.4 | -3.2% | 24.3% | 12.9% | 55/46 | n/a | n/a |
| naive | 1.287 | 33.3% | 51.7 | 1.6% | n/a | n/a | - | n/a | n/a |
| oracle_true_mean | 0.779 | 17.4% | 76.9 | 0.4% | 47.9% | 40.1% | 72/46 | n/a | n/a |
| seasonal_naive | 1.062 | 28.9% | 48.7 | -0.3% | 13.2% | 0.0% | 49/64 | n/a | n/a |

**Segment `smooth`** (n=60 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.881 | 14.8% | 15.5 | -1.9% | 27.7% | 15.2% | 33/27 | 0.282 | 69.0% |
| model:ensemble | 0.799 | 13.6% | 14.1 | -2.6% | 33.5% | 22.1% | 38/22 | n/a | n/a |
| model:ets | 0.839 | 14.3% | 14.8 | -2.7% | 30.0% | 18.0% | 36/24 | n/a | n/a |
| model:lightgbm | 0.826 | 14.2% | 14.7 | -3.0% | 30.5% | 18.5% | 35/25 | n/a | n/a |
| model:xgboost | 0.878 | 14.8% | 15.4 | -2.4% | 27.8% | 15.4% | 30/30 | n/a | n/a |
| moving_average | 0.968 | 16.6% | 16.5 | -1.8% | 18.8% | 4.8% | 34/24 | n/a | n/a |
| naive | 1.158 | 20.4% | 19.4 | -0.3% | n/a | n/a | - | n/a | n/a |
| oracle_true_mean | 0.531 | 7.7% | 8.4 | 0.0% | 62.2% | 55.7% | 53/7 | n/a | n/a |
| seasonal_naive | 1.013 | 17.5% | 17.5 | -1.0% | 14.7% | 0.0% | 28/32 | n/a | n/a |

**Segment `erratic`** (n=20 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.640 | 80.2% | 81.5 | -5.0% | 36.8% | 26.9% | 12/8 | 0.198 | 91.2% |
| model:ensemble | 0.669 | 83.8% | 82.8 | -2.0% | 34.1% | 23.6% | 10/10 | n/a | n/a |
| model:ets | 0.616 | 77.5% | 79.7 | -3.6% | 39.0% | 29.3% | 14/6 | n/a | n/a |
| model:lightgbm | 0.719 | 88.0% | 90.2 | -6.7% | 30.7% | 19.8% | 10/10 | n/a | n/a |
| model:xgboost | 0.768 | 95.9% | 88.5 | 5.2% | 24.5% | 12.6% | 8/12 | n/a | n/a |
| moving_average | 0.689 | 83.4% | 87.0 | -7.3% | 34.4% | 24.0% | 13/7 | n/a | n/a |
| naive | 1.108 | 127.0% | 112.6 | 24.6% | n/a | n/a | - | n/a | n/a |
| oracle_true_mean | 0.648 | 82.8% | 81.6 | 12.8% | 34.8% | 24.5% | 10/10 | n/a | n/a |
| seasonal_naive | 0.937 | 109.7% | 99.2 | 13.8% | 13.6% | 0.0% | 10/10 | n/a | n/a |

**Segment `intermittent`** (n=21 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 0.781 | 129.3% | 105.4 | -38.3% | 3.2% | 14.0% | 4/8 | 0.270 | 83.3% |
| model:arima | 0.810 | 182.0% | 191.9 | 15.7% | 12.7% | 19.1% | 1/6 | n/a | n/a |
| model:croston | 0.718 | 118.1% | 107.9 | -51.9% | 11.5% | 21.3% | 4/7 | n/a | n/a |
| model:ensemble | 0.751 | 124.7% | 107.9 | -43.1% | 6.5% | 17.0% | 4/8 | n/a | n/a |
| model:ets | 0.715 | 117.5% | 108.3 | -53.7% | 12.0% | 21.8% | 5/7 | n/a | n/a |
| model:lightgbm | 0.811 | 135.1% | 101.9 | -32.3% | -1.2% | 10.0% | 4/8 | n/a | n/a |
| model:xgboost | 0.775 | 130.9% | 103.6 | -36.9% | 1.9% | 12.8% | 4/8 | n/a | n/a |
| moving_average | 0.803 | 132.2% | 93.1 | -37.2% | 0.9% | 12.0% | 4/5 | n/a | n/a |
| naive | 0.791 | 133.5% | 57.6 | -39.7% | n/a | n/a | - | n/a | n/a |
| oracle_true_mean | 0.935 | 142.5% | 180.1 | -8.5% | -6.8% | 5.1% | 4/15 | n/a | n/a |
| seasonal_naive | 0.925 | 150.2% | 66.6 | -17.2% | -12.5% | 0.0% | 5/11 | n/a | n/a |

**Segment `lumpy`** (n=19 series-origins)

| method | MASE | WAPE | sMAPE | bias | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |
|---|---|---|---|---|---|---|---|---|---|
| engine | 1.104 | 101.8% | 67.1 | -91.3% | 39.1% | 34.8% | 5/3 | 0.536 | 77.6% |
| model:arima | 0.677 | 125.8% | 184.9 | -42.6% | -21.3% | 40.9% | 0/3 | n/a | n/a |
| model:croston | 1.111 | 103.2% | 67.5 | -92.9% | 38.3% | 33.9% | 5/3 | n/a | n/a |
| model:ensemble | 1.112 | 103.3% | 66.9 | -91.1% | 38.3% | 33.9% | 5/3 | n/a | n/a |
| model:ets | 1.109 | 102.8% | 67.5 | -93.5% | 38.5% | 34.2% | 5/3 | n/a | n/a |
| model:lightgbm | 1.116 | 104.2% | 68.0 | -90.6% | 37.7% | 33.3% | 5/3 | n/a | n/a |
| model:xgboost | 1.116 | 103.7% | 66.4 | -87.0% | 38.0% | 33.6% | 5/3 | n/a | n/a |
| moving_average | 1.878 | 129.0% | 147.4 | -44.0% | 22.9% | 17.4% | 4/10 | n/a | n/a |
| naive | 2.432 | 167.2% | 83.2 | -12.3% | n/a | n/a | - | n/a | n/a |
| oracle_true_mean | 1.526 | 129.7% | 174.0 | -42.5% | 22.4% | 16.9% | 5/14 | n/a | n/a |
| seasonal_naive | 1.501 | 156.2% | 74.4 | -31.1% | 6.6% | 0.0% | 6/11 | n/a | n/a |

Champion model chosen by the engine: lightgbm=57, ets=29, xgboost=29, arima=4, croston=1

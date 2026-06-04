# faro-core

[![PyPI version](https://img.shields.io/pypi/v/faro-core)](https://pypi.org/project/faro-core/)
[![Python](https://img.shields.io/pypi/pyversions/faro-core)](https://pypi.org/project/faro-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Enterprise-grade multi-SKU time-series forecasting engine. Train and compare multiple model families (LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX, Croston) per product/group simultaneously, with automatic feature engineering, walk-forward validation, inventory optimization, and what-if scenario analysis.

---

## Installation

```bash
pip install faro-core
```

**Optional extras:**
```bash
pip install faro-core[api]   # FastAPI integration
pip install faro-core[dl]    # LSTM / TensorFlow support
pip install faro-core[dev]   # Development tools (pytest, ruff, black)
```

---

## Quick Start

```python
from forecasting_core import ForecastEngine

engine = (
    ForecastEngine()
    .load_data("sales.csv")
    .choose_columns(target="sales", date="date", sku="item_id")
    .configure_features(lags=[1, 7, 14], rolling=[7, 14, 28], calendar=True)
    .configure_training(walk_forward=True, wfv_splits=3)
    .configure_forecast(horizon=14)
    .configure_business(service_level=0.95, lead_time_days=7)
    .select_models(["lightgbm", "prophet", "ets"])
    .train()
)

metrics   = engine.get_metrics()
forecast  = engine.predict(horizon=14)
inventory = engine.get_inventory_report()
```

---

## Loading Data

```python
engine = ForecastEngine()

# From file path (CSV, Excel, Parquet auto-detected)
engine.load_data("sales.csv")
engine.load_data("sales.xlsx")
engine.load_data("sales.parquet")

# From a pandas DataFrame
engine.load_data(my_dataframe)
```

---

## Column Configuration

```python
engine.choose_columns(
    target="sales",       # Column to forecast (required)
    date="date",          # Date / timestamp column (required)
    sku="item_id",        # Group / SKU column (optional — omit for single series)
    exogenous=["price", "promo"],  # Regressors for Prophet / SARIMAX (optional)
)
```

---

## Data Inspection

Run these after `load_data()` to understand the dataset before configuring:

```python
# Auto-detected column roles and stats
profile = engine.get_profile()
print(profile["recommended"])   # {"date": "date", "target": "sales", "group": "item_id"}
print(profile["columns"])       # list of column metadata dicts

# Dropdown-ready candidate columns per role
options = engine.get_column_options()
# {"date_candidates": [...], "target_candidates": [...], "group_candidates": [...]}

# Per-column transform suggestions (impute / encode / scale)
suggestions = engine.get_transform_suggestions()
for s in suggestions:
    print(s["column"], s["suggested_spec"], s["reasons"])

# Data quality per SKU (run after choose_columns)
quality = engine.get_data_quality_report()
# {"SKU_A": {"quality_score": 0.92, "series_type": "regular", "warnings": [...]}}

# Model routing preview — which models will run on which SKUs
routing = engine.get_routing_plan()
# {"SKU_A": {"models": ["lightgbm", "prophet"], "flags": ["regular"]}}

# Full schema of all configurable parameters
schema = engine.get_config_schema()
```

---

## Feature Engineering

```python
engine.configure_features(
    lags=[1, 7, 14],          # Lag features: sales_lag1, sales_lag7, sales_lag14
    rolling=[7, 14, 28],      # Rolling mean/std: sales_rollmean_7, ...
    diffs=[1, 7],             # Differencing periods
    calendar=True,            # Month, DOW, week-of-year, sin/cos cyclical, holidays
    ewm_spans=[7, 14],        # Exponential weighted mean spans
)
```

---

## Data Transforms (per-column)

Apply imputation, encoding, and scaling before feature engineering:

```python
engine.configure_transforms({
    "sales":   {"impute": "median",  "scale": "log"},
    "price":   {"scale": "minmax"},
    "region":  {"encode": "label"},
    "channel": {"encode": "one_hot"},
    "promo":   {"impute": "zero"},
})
```

Valid values:

| Parameter | Options |
|-----------|---------|
| `impute`  | `none` `mean` `median` `mode` `forward` `interpolate` `zero` `smart` |
| `encode`  | `none` `label` `one_hot` `ordinal` `binary` `auto` |
| `scale`   | `none` `standard` `minmax` `robust` `log` `power` |

> **Note:** If the target column is scaled (e.g. `log`), forecasts are automatically inverted to the original scale.

---

## Training Configuration

```python
engine.configure_training(
    train_ratio=0.8,        # Fraction of data used for training
    walk_forward=True,      # Use walk-forward validation (recommended)
    wfv_splits=3,           # Number of walk-forward splits
    min_history=20,         # Minimum data points required per SKU
    seasonal_period=7,      # Seasonal period (7=weekly, 12=monthly, 52=annual)
)
```

---

## Model Selection

```python
engine.select_models(
    models=["lightgbm", "xgboost", "prophet", "arima", "ets", "sarimax", "croston"],
    hyperparams={
        "lightgbm": {"n_estimators": 200, "learning_rate": 0.05},
        "xgboost":  {"n_estimators": 150, "max_depth": 6},
        "prophet":  {"changepoint_prior_scale": 0.5},
    }
)
```

**Available models:**

| Name | Type | Best for |
|------|------|----------|
| `lightgbm` | ML | Large datasets, many features |
| `xgboost` | ML | General purpose, robust |
| `prophet` | Statistical | Trend + seasonality, business calendars |
| `arima` | Statistical | Short univariate series |
| `ets` | Statistical | Exponential smoothing, non-seasonal |
| `sarimax` | Statistical | Seasonal + exogenous regressors |
| `croston` | Statistical | Intermittent / sparse demand |

---

## Forecast Configuration

```python
engine.configure_forecast(
    horizon=14,                   # Steps ahead to forecast
    quantiles=[0.1, 0.5, 0.9],   # Confidence interval levels
)
```

---

## Business Rules

```python
engine.configure_business(
    service_level=0.95,            # Target fill rate (0–1)
    lead_time_days=7,              # Supplier lead time
    holding_cost_pct=0.20,         # Annual holding cost as % of inventory value
    stockout_cost_multiplier=3.0,  # Stockout cost relative to holding cost
)
```

---

## Training

```python
# Simple
engine.train()

# With live progress callbacks (e.g., streaming to a WebSocket)
def on_progress(event):
    print(f"[{event['pct']}%] {event['message']}")

engine.train(on_progress=on_progress)
```

---

## Reading Results

```python
# Training metrics per model/SKU
metrics = engine.get_metrics()
# {
#   "rows": [{"sku": "A", "model": "lightgbm", "mae": 12.3, "rmse": 15.1, ...}],
#   "by_model": {"lightgbm": {"avg_mae": 12.3, "avg_rmse": 15.1, "avg_wape": 0.08}},
#   "shap": {"SKU_A": {"lightgbm": {"price": 0.42, "lag1": 0.35, ...}}}
# }

# Point forecasts as DataFrame
forecast_df = engine.predict(horizon=14)
# Columns: sku, model, date, forecast, p90_lo, p90_hi, step

# Point forecasts for a single SKU
sku_forecast = engine.predict_by_sku("SKU_A", horizon=14)

# Forecast as nested dict {sku: {model: [{date, value, lower, upper}]}}
forecast_dict = engine.get_forecast_dict()

# Inventory recommendations
inventory = engine.get_inventory_report()
# {"recommendations": [{"sku": "A", "reorder_point": 120, "safety_stock": 35, ...}]}

# Full report (metrics + inventory + config)
report = engine.generate_report()
print(report["run_id"])
```

---

## Time-Series Analysis

Run exploratory analysis per SKU:

```python
# Full analysis for one SKU
analysis = engine.analyze(sku="SKU_A")
# Includes: stationarity, seasonality, trend, autocorrelation, outliers, distribution

# Summary DataFrame (all SKUs in one table)
summary_df = engine.get_analysis_summary()
# Columns: sku, n, mean, cv, zero_pct, stationarity, seasonal_strength,
#          trend_direction, suggested_ar_order, dominant_period, ...

# STL decomposition chart data
decomp = engine.get_decomposition_chart(sku="SKU_A")
# {"dates": [...], "original": [...], "trend": [...], "seasonal": [...], "residual": [...]}

# Seasonal indices
seasonality = engine.get_seasonality_chart(sku="SKU_A")
# {"indices": [1.2, 0.8, ...], "labels": ["Mon", "Tue", ...], "grand_mean": 100.0}
```

---

## What-If Scenarios

Adjust forecasts without retraining:

```python
# +10% across all SKUs, floor at 0
result = engine.apply_scenario([
    {"multiplier": 1.10},
    {"floor": 0.0},
])

# +25% for SKU_A in June only
result = engine.apply_scenario([
    {
        "sku":        "SKU_A",
        "date_start": "2025-06-01",
        "date_end":   "2025-06-30",
        "multiplier": 1.25,
        "label":      "June promo",
    }
])

# Apply inplace (replaces the active forecast)
engine.apply_scenario([{"multiplier": 1.10}], inplace=True)
```

`ScenarioRule` fields:

| Field | Description |
|-------|-------------|
| `sku` | Filter to specific SKU (omit = all) |
| `model` | Filter to specific model (omit = all) |
| `date_start` / `date_end` | Date range filter (`"YYYY-MM-DD"`) |
| `multiplier` | Scale forecast by this factor (e.g. `1.10` = +10%) |
| `offset` | Add a fixed amount to each forecast value |
| `floor` | Minimum allowed forecast value |
| `ceiling` | Maximum allowed forecast value |
| `label` | Human-readable name for the scenario |

---

## Drift Detection

Monitor production data for distribution shifts:

```python
drift = engine.detect_drift("new_data.csv")
# Or: engine.detect_drift(new_dataframe)

print(drift["has_drift"])            # True / False
print(drift["n_drifted_features"])   # Number of drifted columns
print(drift["alerts"])               # ["price: PSI=0.28 (HIGH)", ...]
print(drift["feature_drift"])        # Per-column PSI and KS-test results
```

---

## Save and Load Models

Persist trained models to avoid retraining:

```python
# After training
engine.save("models/session_jan.joblib")

# Later, restore and predict without retraining
engine = ForecastEngine.load("models/session_jan.joblib")
forecast = engine.predict(horizon=14)
```

---

## Configuration Files

Drive the engine from a JSON config file:

```python
engine = ForecastEngine.from_config("session_config.json")
engine.train()

# Export current config for reproducibility
engine.export_config("my_session.json")
```

**`session_config.json` structure:**

```json
{
  "data":     {"path": "sales.csv"},
  "columns":  {"target": "sales", "date": "date", "group": "item_id"},
  "models":   {"lightgbm": {}, "prophet": {}},
  "features": {"lags": [1, 7, 14], "rolling": [7, 14], "calendar": true},
  "training": {"walk_forward": true, "wfv_splits": 3, "seasonal_period": 7},
  "forecast": {"horizon": 14},
  "business": {"service_level": 0.95, "lead_time_days": 7}
}
```

---

## License

MIT — see [LICENSE](LICENSE)

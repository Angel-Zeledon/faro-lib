# faro-core

[![PyPI version](https://img.shields.io/pypi/v/faro-core)](https://pypi.org/project/faro-core/)
[![Python](https://img.shields.io/pypi/pyversions/faro-core)](https://pypi.org/project/faro-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Enterprise-grade time-series forecasting and preprocessing library.

`faro-core` ships two modules in a single install:

| Module | Import from | Purpose |
|--------|------------|---------|
| **Forecasting engine** | `forecasting_core` | Train multiple model families per SKU, get forecasts, inventory recs, scenarios |
| **Preprocessing** | `forecastlib` | Load, clean, encode, scale, engineer time-series features with a fluent API |

```bash
pip install faro-core
```

---

## Table of Contents

1. [Forecasting Engine (`forecasting_core`)](#forecasting-engine)
   - [Constructors](#constructors)
   - [Load Data](#load-data)
   - [Inspect Before Configuring](#inspect-before-configuring)
   - [Configure Columns](#configure-columns)
   - [Data Quality and Routing](#data-quality-and-routing)
   - [Configure Features](#configure-features)
   - [Configure Data Transforms](#configure-data-transforms)
   - [Configure Training](#configure-training)
   - [Select Models](#select-models)
   - [Configure Forecast and Business Rules](#configure-forecast-and-business-rules)
   - [Train](#train)
   - [Read Results](#read-results)
   - [Time-Series Analysis](#time-series-analysis)
   - [What-If Scenarios](#what-if-scenarios)
   - [Drift Detection](#drift-detection)
   - [Save and Load](#save-and-load)
   - [Configuration Files](#configuration-files)
2. [Preprocessing (`forecastlib`)](#preprocessing)
   - [Loading Data](#loading-data)
   - [Assign Column Roles](#assign-column-roles)
   - [Cleaning](#cleaning)
   - [Filling Missing Values](#filling-missing-values)
   - [Column Selection](#column-selection)
   - [Encoding](#encoding)
   - [Scaling](#scaling)
   - [Time-Series Features](#time-series-features)
   - [Calendar Features](#calendar-features)
   - [Inspection](#inspection)
   - [Dataset Properties](#dataset-properties)
   - [Pipeline](#pipeline)
   - [Train/Test Splitting](#traintest-splitting)
   - [Data Quality Validation](#data-quality-validation)

---

# Forecasting Engine

```python
from forecasting_core import ForecastEngine
```

Trains and evaluates multiple model families simultaneously per SKU/group, with automatic feature engineering, walk-forward validation, inventory optimization, and what-if scenario analysis.

**Available models:**

| Name | Type | Best for |
|------|------|----------|
| `lightgbm` | ML | Large datasets, many features, fast training |
| `xgboost` | ML | General purpose, robust to outliers |
| `prophet` | Statistical | Trend + seasonality + calendars, tolerates missing data |
| `arima` | Statistical | Short univariate series, well-understood patterns |
| `ets` | Statistical | Exponential smoothing, fast, no regressors needed |
| `sarimax` | Statistical | Seasonal patterns + external regressors |
| `croston` | Statistical | Intermittent / sparse demand (many zeros) |

## Constructors

```python
# Start empty and configure step by step
engine = ForecastEngine()

# From a JSON config file
engine = ForecastEngine.from_config("session_config.json")

# From a Python dict (for API integrations)
engine = ForecastEngine.from_dict({
    "data":     {"path": "sales.csv"},
    "columns":  {"target": "sales", "date": "date", "group": "item_id"},
    "models":   {"lightgbm": {}, "prophet": {}},
    "features": {"lags": [1, 7, 14], "rolling": [7, 14], "calendar": True},
    "training": {"walk_forward": True, "wfv_splits": 3},
    "forecast": {"horizon": 14},
})

# Replace the full config on an existing engine
engine.set_config(config_dict)
```

## Load Data

```python
engine.load_data("sales.csv")         # CSV (auto-detected)
engine.load_data("sales.xlsx")        # Excel
engine.load_data("sales.parquet")     # Parquet

import pandas as pd
engine.load_data(pd.read_csv("sales.csv"))  # pandas DataFrame
```

## Inspect Before Configuring

Run these after `load_data()` to understand the dataset before setting column roles.

```python
# Full column metadata + auto-detected roles
profile = engine.get_profile()
print(profile["recommended"])
# {"date": "order_date", "target": "sales_qty", "group": "sku_id"}

# Candidate columns per role (for building dropdowns in a UI)
options = engine.get_column_options()
# {"date_candidates": [...], "target_candidates": [...], ...}

# Per-column transform suggestions based on data characteristics
suggestions = engine.get_transform_suggestions()
for s in suggestions:
    print(s["column"], "→", s["suggested_spec"], "|", s["reasons"])
# sales  → {"scale": "log"}      | ["skewness=3.8 → log transform improves fit"]
# region → {"encode": "one_hot"} | ["5 categories → one-hot encoding"]

# Full schema of all configurable parameters with defaults
schema = engine.get_config_schema()

# All supported model names
models = engine.get_available_models()
# ["lightgbm", "xgboost", "prophet", "arima", "ets", "sarimax", "croston"]
```

## Configure Columns

```python
engine.choose_columns(
    target="sales",                     # Column to forecast — required
    date="date",                        # Date/timestamp column — required
    sku="item_id",                      # Group key (SKU, store, product) — optional
    exogenous=["price", "promo_flag"],  # External regressors for Prophet/SARIMAX — optional
)
```

## Data Quality and Routing

```python
# Per-SKU health score and demand pattern classification
quality = engine.get_data_quality_report()
# {
#   "SKU_A": {"quality_score": 0.92, "series_type": "regular",      "warnings": []},
#   "SKU_B": {"quality_score": 0.61, "series_type": "intermittent", "warnings": ["60% zeros"]},
# }

# Which models will be assigned to which SKUs (before training)
routing = engine.get_routing_plan()
# {
#   "SKU_A": {"models": ["lightgbm", "prophet"], "flags": ["regular", "seasonal"]},
#   "SKU_B": {"models": ["croston"],              "flags": ["intermittent"]},
# }
```

## Configure Features

Feature engineering applies to ML models (LightGBM, XGBoost). Statistical models receive the raw series.

```python
engine.configure_features(
    lags=[1, 7, 14, 28],    # Lag features — "what were sales 1, 7, 14, 28 days ago?"
    rolling=[7, 14, 28],    # Rolling mean + std over these windows
    diffs=[1, 7],           # Day-over-day and week-over-week change
    calendar=True,          # Month, DOW, week, quarter, sin/cos cyclical, Colombia holidays
    ewm_spans=[7, 14],      # Exponential weighted mean spans
)
```

**Choosing lag values:** Match your seasonal period — for daily/weekly data use `[1, 7, 14, 28]`, for monthly use `[1, 3, 6, 12]`.

## Configure Data Transforms

Per-column imputation, encoding, and scaling applied before feature engineering. If the target column is scaled, forecasts are **automatically inverted** to the original scale.

```python
engine.configure_transforms({
    "sales":      {"impute": "median", "scale": "log"},
    "price":      {"scale": "minmax"},
    "region":     {"encode": "label"},
    "channel":    {"impute": "mode",   "encode": "one_hot"},
    "promo_flag": {"impute": "zero"},
})
```

| Parameter | Options |
|-----------|---------|
| `impute`  | `none` `mean` `median` `mode` `forward` `interpolate` `zero` `smart` |
| `encode`  | `none` `label` `one_hot` `ordinal` `binary` `auto` |
| `scale`   | `none` `standard` `minmax` `robust` `log` `power` |

Auto-suggest transforms from the data:

```python
suggestions = engine.get_transform_suggestions()
specs = {s["column"]: s["suggested_spec"] for s in suggestions if s["auto_apply"]}
engine.configure_transforms(specs, auto_apply=True)
```

## Configure Training

```python
engine.configure_training(
    train_ratio=0.8,       # Fraction used for training (rest = validation)
    walk_forward=True,     # Walk-forward validation — strongly recommended
    wfv_splits=3,          # Number of folds
    min_history=20,        # Minimum rows required per SKU
    seasonal_period=7,     # 7=weekly, 12=monthly, 52=annual weekly
)
```

Walk-forward validation trains on data up to a cutoff and validates on the next window, repeating `wfv_splits` times — correctly simulates production forecasting with no look-ahead bias.

## Select Models

```python
engine.select_models(
    models=["lightgbm", "xgboost", "prophet", "ets"],
    hyperparams={
        "lightgbm": {"n_estimators": 200, "learning_rate": 0.05, "num_leaves": 64},
        "xgboost":  {"n_estimators": 150, "max_depth": 6, "subsample": 0.8},
        "prophet":  {"changepoint_prior_scale": 0.5, "seasonality_mode": "multiplicative"},
    }
)
```

## Configure Forecast and Business Rules

```python
engine.configure_forecast(
    horizon=14,
    quantiles=[0.1, 0.5, 0.9],   # Confidence interval levels
)

engine.configure_business(
    service_level=0.95,            # Target fill rate (95% = stock-outs in ≤5% of cycles)
    lead_time_days=7,              # Days between placing and receiving an order
    holding_cost_pct=0.20,         # Annual holding cost as % of inventory value
    stockout_cost_multiplier=3.0,  # How much more a stock-out costs vs. holding one unit
)
```

## Train

```python
engine.train()

# With live progress callbacks
def on_progress(event):
    print(f"[{event['pct']:3d}%] {event['message']}")

engine.train(on_progress=on_progress)
```

The pipeline runs: DataTransformer → DataQualityChecker → ModelRouter → FeatureEngineer → Trainer (walk-forward) → WeightedEnsemble → Registry.

## Read Results

```python
# Training metrics per model/SKU
metrics = engine.get_metrics()
# {
#   "rows": [{"sku": "A", "model": "lightgbm", "mae": 12.3, "rmse": 15.1, "wape": 0.08}],
#   "by_model": {"lightgbm": {"avg_mae": 12.3, "avg_rmse": 15.1, "avg_wape": 0.08}},
#   "shap": {"SKU_A": {"lightgbm": {"price": 0.42, "sales_lag7": 0.35, ...}}}
# }

# Point forecasts as a pandas DataFrame
forecast_df = engine.predict(horizon=14)
# Columns: sku, model, date, forecast, p90_lo, p90_hi, step

# Single SKU
sku_df = engine.predict_by_sku("SKU_A", horizon=14)

# JSON-serializable dict (dates as ISO strings) — same format as REST API response
forecast_json = engine.get_forecast()
# {"rows": [...], "n_skus": 5, "horizon": 14}

# Nested dict {sku: {model: [{date, value, lower, upper}]}}
forecast_dict = engine.get_forecast_dict()

# Inventory recommendations
inventory = engine.get_inventory_report()
# {"recommendations": [{"sku": "A", "reorder_point": 120, "safety_stock": 35, ...}]}

# Full report (metrics + inventory + config)
report = engine.generate_report()
print(report["run_id"])
```

`predict()` tries: cached forecast → re-generate from fitted models → full pipeline re-run.

## Time-Series Analysis

```python
# Full statistical analysis for one SKU
# Covers: stationarity (ADF+KPSS), STL decomposition, seasonality (FFT+ACF),
# trend (Mann-Kendall + Sen's slope + change points), autocorrelation, outliers, distribution
analysis = engine.analyze(sku="SKU_A")

# Summary DataFrame — all SKUs in one table
# Columns: sku, n, mean, cv, zero_pct, stationarity, seasonal_strength,
#          trend_direction, dominant_period, suggested_ar_order, is_white_noise, ...
summary_df = engine.get_analysis_summary()

# STL decomposition chart data (trend + seasonal + residual with real dates)
decomp = engine.get_decomposition_chart(sku="SKU_A")
# {"dates": [...], "original": [...], "trend": [...], "seasonal": [...],
#  "residual": [...], "trend_strength": 0.82, "seasonal_strength": 0.67}

# Seasonal indices (how demand at each cycle position compares to the average)
seasonality = engine.get_seasonality_chart(sku="SKU_A")
# {"indices": [0.85, 0.90, 1.02, 1.08, 1.15, 1.25, 0.75],
#  "labels":  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
#  "grand_mean": 98.4}
# index > 1.0 = above-average demand at that position in the cycle
```

## What-If Scenarios

Adjust forecasts without retraining, filtering by SKU, model, and date range.

```python
# +10% across all SKUs, never below 0
result = engine.apply_scenario([
    {"multiplier": 1.10},
    {"floor": 0.0},
])

# +25% for SKU_A in June only
result = engine.apply_scenario([
    {"sku": "SKU_A", "date_start": "2025-06-01", "date_end": "2025-06-30",
     "multiplier": 1.25, "label": "June promotion"},
])

# -15% for LightGBM only, capped at 200 units
result = engine.apply_scenario([
    {"model": "lightgbm", "multiplier": 0.85, "ceiling": 200.0}
])

# Apply inplace (replaces the engine's active forecast)
engine.apply_scenario([{"multiplier": 1.10}], inplace=True)

# Returns in nested dict format — same as get_forecast_dict()
scenario_dict = engine.get_scenario_dict([{"sku": "SKU_A", "multiplier": 1.10}])
```

`ScenarioRule` fields:

| Field | Type | Description |
|-------|------|-------------|
| `sku` | str | Filter to a specific SKU. Omit for all. |
| `model` | str | Filter to a specific model. Omit for all. |
| `date_start` / `date_end` | `"YYYY-MM-DD"` | Date range filter. |
| `multiplier` | float | Scale by factor — `1.10` = +10%, `0.85` = −15%. |
| `offset` | float | Add a fixed amount to each value. |
| `floor` | float | Minimum allowed value. |
| `ceiling` | float | Maximum allowed value. |
| `label` | str | Human-readable name for this rule. |

## Drift Detection

```python
drift = engine.detect_drift("new_data.csv")
# Or: engine.detect_drift(new_dataframe)

print(drift["has_drift"])           # True / False
print(drift["n_drifted_features"])  # How many columns drifted
print(drift["alerts"])
# ["price: PSI=0.28 (HIGH drift — recommend retraining)"]

print(drift["feature_drift"])
# {"price": {"psi": 0.28, "psi_level": "HIGH", "ks_p_value": 0.001, "drift": True}, ...}
```

PSI thresholds: `< 0.10` = LOW (no concern) · `0.10–0.25` = MEDIUM (monitor) · `> 0.25` = HIGH (retrain).

## Save and Load

```python
engine.save("models/session_jan2025.joblib")

engine = ForecastEngine.load("models/session_jan2025.joblib")
forecast_df = engine.predict(horizon=14)   # No retraining needed
```

## Configuration Files

```json
{
  "data":     {"path": "sales.csv"},
  "columns":  {"target": "sales", "date": "date", "group": "item_id",
               "exogenous": ["price", "promo_flag"]},
  "models":   {"lightgbm": {"n_estimators": 200}, "prophet": {}, "ets": {}},
  "features": {"lags": [1,7,14,28], "rolling": [7,14,28], "calendar": true, "ewm_spans": [7,14]},
  "training": {"train_ratio": 0.8, "walk_forward": true, "wfv_splits": 3, "seasonal_period": 7},
  "forecast": {"horizon": 14, "quantiles": [0.1, 0.5, 0.9]},
  "business": {"service_level": 0.95, "lead_time_days": 7,
               "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
  "transforms": {"sales": {"impute": "median", "scale": "log"},
                 "price": {"scale": "minmax"}, "region": {"encode": "label"}}
}
```

```python
engine = ForecastEngine.from_config("session_config.json")
engine.train()
engine.export_config("output/reproducible_config.json")
```

---

# Preprocessing

```python
from forecastlib.data import Loader
```

Fluent, chainable preprocessing API. Every transformation is recorded and can be saved as a reproducible `Pipeline` for production use.

## Loading Data

The `Loader` auto-detects dtypes, attempts to parse date columns, and warns about data quality issues (duplicates, high null rates, large files).

```python
from forecastlib.data import Loader

# Files
ds = Loader.from_csv("sales.csv")
ds = Loader.from_csv("sales.csv", sep=";", encoding="latin-1")
ds = Loader.from_excel("sales.xlsx")
ds = Loader.from_excel("sales.xlsx", sheet_name="Ventas")
ds = Loader.from_parquet("sales.parquet")
ds = Loader.from_json("sales.json")

# pandas DataFrame
import pandas as pd
ds = Loader.from_dataframe(pd.read_csv("sales.csv"))

# SQL databases — requires the matching driver (psycopg2, pymysql, pyodbc)
ds = Loader.from_sql(
    db="postgresql",          # "postgresql" | "mysql" | "sqlite" | "mssql"
    host="localhost",
    port=5432,                # optional — defaults per db type
    database="sales_db",
    user="admin",
    password="secret",
    table="transactions",     # either table= or query=
)

# Custom SQL query (filter/join before loading)
ds = Loader.from_sql(
    db="postgresql", host="localhost", database="sales_db", user="u", password="p",
    query="SELECT * FROM sales WHERE year >= 2023",
)

# Large tables — read in chunks to avoid memory issues
ds = Loader.from_sql(
    db="postgresql", host="localhost", database="sales_db", user="u", password="p",
    table="transactions", chunk_size=100_000,
)
```

## Assign Column Roles

```python
ds = ds.select(
    target="sales",   # Column to forecast — required for feature engineering methods
    datetime="date",  # Date/timestamp column — required for calendar features and sorting
    group="store",    # Group key (SKU, store, product) — optional, for panel datasets
)
```

After `.select()`, methods like `.target().lags()` and `.datetime().features.calendar()` know which columns to use automatically.

## Cleaning

```python
# Parse string dates to datetime64
ds = ds.clean.fix_datetime()                       # Auto-detect format
ds = ds.clean.fix_datetime(format="%d/%m/%Y")      # Explicit format

# Remove duplicate rows
ds = ds.clean.drop_duplicates()                             # All columns
ds = ds.clean.drop_duplicates(subset=["date", "store"])    # Check only these columns
ds = ds.clean.drop_duplicates(keep="last")                 # "first" (default) | "last" | False

# Drop rows that have missing values
ds = ds.clean.drop_nulls()                         # Any null → drop row
ds = ds.clean.drop_nulls(subset=["sales"])         # Only if target is null
ds = ds.clean.drop_nulls(thresh=5)                 # Keep rows with at least 5 non-null values

# Drop columns that have a single unique value (no information for the model)
ds = ds.clean.drop_constant()

# Clamp values to a range (removes extreme outliers)
ds = ds.cols(["sales"]).clean.clip(lower=0)            # No negative sales
ds = ds.cols(["age"]).clean.clip(lower=0, upper=120)   # Range clip

# Strip leading/trailing whitespace from string columns (run before encoding)
ds = ds.categorical().clean.strip()

# Auto-cast dtypes: numeric strings → float, low-cardinality strings → category
ds = ds.clean.fix_dtypes()

# Rename columns (updates schema roles automatically if renamed column has a role)
ds = ds.clean.rename({"Fecha": "date", "Ventas": "sales"})

# Sort rows — required before building lag/rolling features
ds = ds.clean.sort()                              # Sort by configured datetime column
ds = ds.clean.sort(by="date")                     # Explicit column
ds = ds.clean.sort(by=["store", "date"])          # Multi-column
ds = ds.clean.sort(by="date", ascending=False)    # Descending
```

## Filling Missing Values

Dropping rows in time-series data creates gaps that corrupt lag features — always prefer filling.

```python
# ── Smart auto-fill (recommended starting point) ──────────────────────────────
# Numeric <5% nulls → median | Numeric ≥5% → interpolate | Categorical → mode | Datetime → ffill
ds = ds.fill.smart()

# ── Panel-aware fill (recommended for multi-SKU data) ────────────────────────
# Fills within each group independently — prevents data from one SKU polluting another.
# After ffill: remaining leading nulls → bfill → 0 for numeric.
ds = ds.fill.time_series()

# ── Statistical fills ─────────────────────────────────────────────────────────
ds = ds.fill.mean()           # Column mean — sensitive to outliers
ds = ds.fill.median()         # Column median — robust (preferred over mean)
ds = ds.fill.mode()           # Most frequent value — works for any dtype
ds = ds.fill.constant(0)      # Fixed constant — use when 0 means "no activity"

# ── Temporal fills ────────────────────────────────────────────────────────────
ds = ds.fill.forward()              # Carry last known value forward (LOCF)
ds = ds.fill.forward(limit=3)       # Forward fill at most 3 consecutive NaNs
ds = ds.fill.backward()             # Carry next known value backward
ds = ds.fill.backward(limit=3)

ds = ds.fill.interpolate()                         # Linear interpolation
ds = ds.fill.interpolate(method="time")            # Time-weighted interpolation
ds = ds.fill.interpolate(method="polynomial")      # Polynomial interpolation
ds = ds.fill.interpolate(method="spline")          # Cubic spline

# ── KNN imputation ────────────────────────────────────────────────────────────
# Imputes based on nearest neighbors — better when missingness is not random
ds = ds.numeric().fill.knn()               # 5 neighbors (default)
ds = ds.numeric().fill.knn(n_neighbors=3)

# ── Apply fill to specific columns ────────────────────────────────────────────
ds = ds.cols(["sales"]).fill.forward()
ds = ds.cols(["price", "promo"]).fill.constant(0)
ds = ds.categorical().fill.mode()
```

## Column Selection

Narrow which columns a transformation applies to. All selectors chain into `.scale`, `.encode`, `.fill`, and `.clean`.

```python
ds.numeric()                       # All numeric columns (int, float)
ds.categorical()                   # All object / category columns
ds.target()                        # Target column only (requires .select())
ds.datetime()                      # Datetime column only (requires .select())
ds.cols(["price", "promo"])        # Explicit column list
ds.regex("price|promo")            # Columns matching a regex pattern

# Exclude specific columns from any selection
ds.numeric().exclude(["sales"])              # All numeric except the target
ds.cols(["a", "b", "c"]).exclude(["b"])     # ["a", "c"]
```

## Encoding

Always encode categorical columns before scaling or feature engineering — ML models require numeric inputs.

```python
# Auto: one-hot for ≤15 categories, label for 16–200, binary for >200
ds = ds.categorical().encode.auto()

# One-hot: creates <col>_<value> binary columns, drops original
ds = ds.categorical().encode.one_hot()
ds = ds.categorical().encode.one_hot(drop_first=True)   # Avoid multicollinearity in linear models

# Label: replace each category with an integer code 0..n-1
# Good for tree-based models (LightGBM, XGBoost), NOT for linear models
ds = ds.categorical().encode.label()

# Ordinal: encode with a specific natural order
ds = ds.cols(["size"]).encode.ordinal()

# Binary (hash-based): for very high cardinality (>200 unique values)
ds = ds.cols(["product_id"]).encode.binary()

# Apply to specific columns
ds = ds.cols(["region", "channel"]).encode.one_hot()
```

## Scaling

Scale numeric features so gradient-based models converge faster. Tree-based models are scale-invariant but benefit from consistent ranges. **Always exclude the target from scaling**, or if you do scale it, invert the scaling on predictions.

```python
# Z-score normalization: (x - mean) / std — general default
ds = ds.numeric().scale.standard()

# Scale to [0, 1] — sensitive to outliers; use robust if outliers exist
ds = ds.numeric().scale.minmax()

# Median-centered, IQR-scaled — outlier-resistant (best for retail/supply chain data with spikes)
ds = ds.numeric().scale.robust()

# Natural log: log(x + 1) — reduces right skew in sales/revenue/count data
# Requires non-negative values; use clip(lower=0) first if needed
ds = ds.cols(["sales"]).scale.log()

# Yeo-Johnson power transform — handles negatives, finds optimal normalization automatically
ds = ds.numeric().scale.power()

# Best practice: scale features, leave target untouched
ds = ds.numeric().exclude(["sales"]).scale.robust()
```

## Time-Series Features

These require `.select()` to have been called. Apply **after** cleaning and filling — lags computed on data with nulls will propagate NaNs into all derived features.

```python
# Lag features — "what were sales k days ago?"
ds = ds.target().lags([1, 7, 14, 28])
# Creates: sales_lag1, sales_lag7, sales_lag14, sales_lag28

# Rolling mean — captures the recent trend (smoothed signal)
ds = ds.target().rolling.mean([7, 14, 30])
# Creates: sales_rollmea7, sales_rollmea14, sales_rollmea30

# Rolling std — measures volatility / demand uncertainty
ds = ds.target().rolling.std([7])
# Creates: sales_rollstd7

# Rolling min / max
ds = ds.target().rolling.min([7, 14])
ds = ds.target().rolling.max([7, 14])

# Exponential weighted mean — weights recent values more heavily
# span=7: recent 7 periods contribute ~63% of the total weight
ds = ds.target().ewm([7, 14])
# Creates: sales_ewm7, sales_ewm14

# Differencing — models the change rather than the level
ds = ds.target().diffs([1, 7])
# Creates: sales_diff1 (day-over-day), sales_diff7 (week-over-week)
```

**Choosing lag values:** Use multiples of your natural seasonal period. Daily/weekly data: `[1, 7, 14, 28]`. Monthly: `[1, 3, 6, 12]`.

## Calendar Features

```python
ds = ds.datetime().features.calendar()
```

Creates the following columns (prefixed with the datetime column name, e.g., `date_*`):

| Column | Description | Range |
|--------|-------------|-------|
| `date_year` | Calendar year | 2020, 2021, … |
| `date_month` | Month | 1–12 |
| `date_day` | Day of month | 1–31 |
| `date_dow` | Day of week (0 = Monday) | 0–6 |
| `date_week` | ISO week number | 1–53 |
| `date_quarter` | Quarter | 1–4 |
| `date_is_weekend` | 1 if Sat or Sun | 0 or 1 |
| `date_sin_month` | Cyclical sin of month | −1 … +1 |
| `date_cos_month` | Cyclical cos of month | −1 … +1 |
| `date_sin_dow` | Cyclical sin of day-of-week | −1 … +1 |
| `date_cos_dow` | Cyclical cos of day-of-week | −1 … +1 |
| `date_days_to_easter` | Days until (+) or since (−) Easter | integer |
| `date_days_to_christmas` | Days until (+) or since (−) Christmas | integer |

**Why cyclical encodings?** Month 12 and month 1 are consecutive, but `12 − 1 = 11` implies they are far apart. The sin/cos encoding maps the cycle onto a unit circle so December and January are correctly adjacent.

**Why holiday distances?** A binary `is_holiday` flag misses the demand ramp-up before a holiday and the hangover after. The distance feature captures the temporal proximity effect.

## Inspection

```python
# Full summary: dtype, null count, null %, min, max, mean, unique count
summary = ds.inspect.summary()

# Only null information — sorted by null %
nulls = ds.inspect.nulls()

# Column types and inferred roles (target, datetime, group, feature)
types = ds.inspect.types()

# Memory usage per column
memory = ds.inspect.memory(verbose=False)
# Columns: column, KB, MB
```

## Dataset Properties

```python
len(ds)            # Number of rows
ds.shape           # Tuple (rows, cols)
ds.columns         # List of column names
ds.dtypes          # pandas Series of dtypes
ds.head(n=5)       # First n rows as pandas DataFrame
ds.to_dataframe()  # Full pandas DataFrame — use this when done chaining

ds.copy()
# Fully independent deep copy — mutations to the copy do not affect the original.
# Use before branching into two different preprocessing paths from the same base.
```

## Pipeline

Every transformation is silently recorded. `.to_pipeline()` packages all steps into a serializable `Pipeline` that can be replayed on new data — guaranteeing that production preprocessing is identical to training.

```python
from forecastlib.pipeline import Pipeline

pipeline = ds.to_pipeline()
pipeline.summary()
# Step 1: clean.fix_datetime on ['date']
# Step 2: fill.time_series on ['sales', 'price']
# Step 3: encode.one_hot on ['channel', 'region']
# Step 4: scale.robust on ['price', 'promo']
# Step 5: target.lags([1, 7, 14]) on sales
# Step 6: calendar on date

pipeline.save("models/sales_pipeline.pkl")

loaded = Pipeline.load("models/sales_pipeline.pkl")
print(f"{len(loaded.steps)} steps recorded")
```

> **Best practice:** Save the pipeline alongside the trained model. At inference time, load both, apply the pipeline to raw incoming data, then pass the result to the model.

## Train/Test Splitting

```python
from forecastlib.time_series import TimeSeriesSplitter

splitter = TimeSeriesSplitter()

# Simple chronological split — NOT a random shuffle
train, test = splitter.train_test_split(ds, test_ratio=0.2)
df_train = train.to_dataframe()
df_test  = test.to_dataframe()

# Walk-forward expanding-window cross-validation
# Each fold: all data up to cutoff → train, next window → test
splitter_cv = TimeSeriesSplitter(n_splits=5)
for fold_n, (train_fold, test_fold) in enumerate(splitter_cv.split(ds)):
    df_train = train_fold.to_dataframe()
    df_test  = test_fold.to_dataframe()
    # train your model on df_train, evaluate on df_test
```

Walk-forward CV avoids look-ahead bias — standard k-fold randomly leaks future data into training, making models score unrealistically well on time-series problems.

## Data Quality Validation

```python
from forecastlib.time_series import TimeSeriesValidator

validator = TimeSeriesValidator()
report = validator.check(ds, datetime_col="date")

print(report.sorted)          # True if rows are chronologically ordered
print(report.has_gaps)        # True if time steps are missing (e.g., no row for 2024-03-15)
print(report.has_duplicates)  # True if the same (date, group) pair appears more than once
```

Run this before building lag features — if `has_gaps` is True, lag-1 will point to the wrong row.

---

## Complete Example

```python
from forecasting_core import ForecastEngine
from forecastlib.data import Loader
from forecastlib.pipeline import Pipeline
from forecastlib.time_series import TimeSeriesSplitter

# ── 1. Preprocess with forecastlib ───────────────────────────────────────────
ds = (
    Loader.from_csv("sales.csv")
    .select(target="sales", datetime="date", group="store")
    .clean.fix_datetime()
    .clean.drop_duplicates()
    .clean.sort()
    .fill.time_series()
    .categorical().clean.strip()
    .categorical().encode.auto()
    .numeric().exclude(["sales"]).scale.robust()
    .target().lags([1, 7, 14, 28])
    .target().rolling.mean([7, 14, 30])
    .target().rolling.std([7])
    .target().ewm([7, 14])
    .target().diffs([1, 7])
    .datetime().features.calendar()
)

pipeline = ds.to_pipeline()
pipeline.save("models/pipeline.pkl")
df = ds.to_dataframe()

# ── 2. Forecast with forecasting_core ────────────────────────────────────────
engine = (
    ForecastEngine()
    .load_data("sales.csv")
    .choose_columns(target="sales", date="date", sku="store")
    .configure_features(lags=[1, 7, 14], rolling=[7, 14], calendar=True)
    .configure_training(walk_forward=True, wfv_splits=3)
    .configure_forecast(horizon=14)
    .configure_business(service_level=0.95, lead_time_days=7)
    .select_models(["lightgbm", "prophet", "ets"])
    .train()
)

print(engine.get_metrics()["by_model"])
forecast = engine.predict(horizon=14)
inventory = engine.get_inventory_report()

engine.save("models/engine.joblib")
```

---

## License

MIT — see [LICENSE](LICENSE)

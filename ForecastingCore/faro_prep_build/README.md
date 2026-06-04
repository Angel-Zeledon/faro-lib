# faro-prep

[![PyPI version](https://img.shields.io/pypi/v/faro-prep)](https://pypi.org/project/faro-prep/)
[![Python](https://img.shields.io/pypi/pyversions/faro-prep)](https://pypi.org/project/faro-prep/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Data preprocessing and feature engineering library for time-series forecasting.
Fluent, chainable API — load, clean, fill, encode, scale, engineer features, inspect — and produce a serializable preprocessing pipeline so the same transformations can be replayed on new data exactly.

---

## Why faro-prep?

Preprocessing is the most error-prone and time-consuming part of a forecasting project. `faro-prep` eliminates boilerplate by giving you:

- **One consistent API** for all preprocessing steps (no more juggling sklearn, pandas, and custom scripts).
- **Chainable operations** that read like a recipe and are easy to audit and modify.
- **Reproducible pipelines** — every transformation is recorded and can be saved to disk, then replayed on production data identically.
- **Time-series aware** operations that understand group/SKU structure so data from one series never pollutes another.

---

## Installation

```bash
pip install faro-prep
```

---

## Quick Start

```python
from forecastlib.data import Loader

ds = (
    Loader.from_csv("sales.csv")
    .select(target="sales", datetime="date", group="store")
    .clean.fix_datetime()
    .clean.drop_duplicates()
    .fill.smart()
    .categorical().encode.auto()
    .numeric().exclude(["sales"]).scale.standard()
    .target().lags([1, 7, 14])
    .target().rolling.mean([7, 30])
    .target().ewm([7, 14])
    .datetime().features.calendar()
)

df       = ds.to_dataframe()      # pandas DataFrame ready for ML
pipeline = ds.to_pipeline()       # reproducible pipeline
pipeline.save("pipeline.pkl")
```

---

## Step 1 — Loading Data

### From a file

The `Loader` auto-detects types, optimizes dtypes (downcasts floats/ints, converts low-cardinality strings to `category`), attempts to parse date columns, and warns you about data quality issues (duplicates, high null rates, large file sizes).

```python
from forecastlib.data import Loader

# CSV (default separator ",", encoding "utf-8")
ds = Loader.from_csv("sales.csv")

# CSV with non-standard separator or encoding
ds = Loader.from_csv("sales.csv", sep=";", encoding="latin-1")

# Excel — reads first sheet by default
ds = Loader.from_excel("sales.xlsx")
ds = Loader.from_excel("sales.xlsx", sheet_name="Ventas")
ds = Loader.from_excel("sales.xlsx", sheet_name=1)   # Second sheet by index

# Parquet (columnar format — fastest for large datasets)
ds = Loader.from_parquet("sales.parquet")

# JSON
ds = Loader.from_json("sales.json")

# Wrap an existing pandas DataFrame
import pandas as pd
ds = Loader.from_dataframe(pd.read_csv("sales.csv"))
```

### From SQL

Supports PostgreSQL, MySQL, SQLite, and SQL Server. Requires the matching driver installed separately.

```python
# PostgreSQL (requires: pip install psycopg2-binary)
ds = Loader.from_sql(
    db="postgresql",
    host="localhost",
    port=5432,            # optional, defaults to 5432
    database="sales_db",
    user="admin",
    password="secret",
    table="transactions", # read entire table
)

# Custom SQL query — use this to filter or join before loading
ds = Loader.from_sql(
    db="postgresql",
    host="localhost",
    database="sales_db",
    user="admin",
    password="secret",
    query="SELECT * FROM transactions WHERE store_id = 'S001' AND year >= 2023",
)

# MySQL (requires: pip install pymysql)
ds = Loader.from_sql(
    db="mysql", host="localhost",
    database="mydb", user="root", password="pass",
    table="sales",
)

# SQLite — local file, no password needed
ds = Loader.from_sql(db="sqlite", database="local.db", table="sales")

# SQL Server (requires: pip install pyodbc)
ds = Loader.from_sql(
    db="mssql", host="my-server",
    database="SalesDB", user="sa", password="pass",
    table="dbo.Transactions", schema="dbo",
)

# Large tables: read in chunks to avoid memory issues
ds = Loader.from_sql(
    db="postgresql", host="localhost", database="sales_db",
    user="admin", password="secret",
    table="transactions", chunk_size=100_000,
)
```

---

## Step 2 — Assign Column Roles

Tell the library which column is the target to forecast, which is the date, and which is the group key (e.g., SKU, store, product). All subsequent time-series operations use these roles automatically.

```python
ds = ds.select(
    target="sales",    # Column you want to forecast — required
    datetime="date",   # Date or timestamp column — required
    group="store",     # Group / SKU key for panel datasets — optional
                       # Omit if you have a single time series
)
```

After calling `.select()`, the dataset knows which column is the target, so `.target().lags(...)` and `.target().rolling.*` know exactly which column to transform without you repeating the name.

---

## Step 3 — Cleaning

Cleaning methods are accessed via `.clean.*`. They apply globally (to all selected columns) unless you narrow the selection first with `.cols()` or `.categorical()`.

### `fix_datetime()` — Parse string dates to datetime64

Many CSVs store dates as strings like `"2024-01-15"` or `"15/01/2024"`. This method converts them to proper `datetime64` so time-series operations work correctly. It detects the format automatically or you can specify it.

```python
ds = ds.clean.fix_datetime()                      # Auto-detect format
ds = ds.clean.fix_datetime(format="%d/%m/%Y")     # Explicit format
ds = ds.cols(["ship_date"]).clean.fix_datetime()  # On a specific column
```

> **When to use:** Always run this if your date column was loaded as a string. Without it, sort order and time-series features may be wrong.

### `drop_duplicates()` — Remove identical rows

```python
ds = ds.clean.drop_duplicates()                          # Remove exact duplicate rows
ds = ds.clean.drop_duplicates(subset=["date", "store"])  # Only check these columns for duplicates
ds = ds.clean.drop_duplicates(keep="last")               # keep: "first" (default) | "last" | False
```

> **When to use:** After loading, before anything else. A single duplicated row can inflate metrics and corrupt lag features.

### `drop_nulls()` — Remove rows with missing values

```python
ds = ds.clean.drop_nulls()                        # Drop any row that has at least one null
ds = ds.clean.drop_nulls(subset=["sales"])        # Drop only if the target column is null
ds = ds.clean.drop_nulls(thresh=5)               # Keep rows that have at least 5 non-null values
```

> **When to use:** Prefer `.fill.*` over `drop_nulls` for time-series data — dropping rows creates gaps that break lag features. Use `drop_nulls(subset=["target"])` only to remove rows where the target is missing and cannot be inferred.

### `drop_constant()` — Remove zero-variance columns

Columns with a single unique value carry no information for the model and can cause issues with some scalers.

```python
ds = ds.clean.drop_constant()
```

> **When to use:** After encoding, in case one-hot encoding produced an all-zero column.

### `clip()` — Clamp values to a range

Removes extreme outliers by capping values to `[lower, upper]`.

```python
ds = ds.cols(["sales"]).clean.clip(lower=0)            # No negative sales
ds = ds.cols(["age"]).clean.clip(lower=0, upper=120)   # Reasonable age range
ds = ds.cols(["price"]).clean.clip(upper=9999)         # Cap extreme prices
```

> **When to use:** When you know that values outside a range are data entry errors (e.g., negative sales, prices of 0). Prefer this over removing rows.

### `strip()` — Remove whitespace from strings

```python
ds = ds.categorical().clean.strip()     # All categorical/object columns
ds = ds.cols(["region"]).clean.strip()  # Specific column
```

> **When to use:** Before encoding. Leading/trailing spaces cause categories like `"north"` and `"north "` to be treated as different values.

### `fix_dtypes()` — Auto-cast to optimal dtypes

```python
ds = ds.clean.fix_dtypes()
```

Converts numeric strings to float/int, low-cardinality strings to `category`. Reduces memory usage and speeds up downstream operations.

### `rename()` — Rename columns

```python
ds = ds.clean.rename({"Fecha": "date", "Ventas": "sales", "Tienda": "store"})
```

> If you rename a column that has a semantic role (target, datetime, group), the schema is updated automatically.

### `sort()` — Sort rows

```python
ds = ds.clean.sort()                             # Sort by the configured datetime column
ds = ds.clean.sort(by="date")                    # Explicit column
ds = ds.clean.sort(by=["store", "date"])         # Multi-column sort
ds = ds.clean.sort(by="date", ascending=False)   # Descending
```

> **When to use:** After loading and fixing datetime. Lag and rolling features require chronological order within each group to be computed correctly.

---

## Step 4 — Filling Missing Values

Missing values in time-series data need careful handling. Simply dropping rows creates temporal gaps that corrupt lag features. The right strategy depends on the cause of the missing data.

### `fill.smart()` — Recommended starting point

Automatically selects the best strategy per column:

| Condition | Strategy |
|-----------|----------|
| Numeric, < 5% nulls | Median |
| Numeric, ≥ 5% nulls | Linear interpolation |
| Categorical / object | Mode (most frequent value) |
| Datetime | Forward fill |

```python
ds = ds.fill.smart()
```

> **When to use:** As the first fill step. If you have panel data (multiple SKUs), use `fill.time_series()` instead.

### `fill.time_series()` — Panel-aware fill (recommended for multi-SKU)

For panel datasets, fills each SKU/group independently so data from one series cannot bleed into another. After forward-filling, any remaining leading nulls are backward-filled, and any still-remaining numeric nulls are set to 0.

```python
ds = ds.fill.time_series()
```

> **When to use:** Any dataset with a group column (multiple stores, SKUs, products). This is safer than `fill.smart()` for panel data.

### Statistical fills

```python
ds = ds.fill.mean()       # Column mean — sensitive to outliers
ds = ds.fill.median()     # Column median — robust to outliers (better than mean)
ds = ds.fill.mode()       # Most frequent value — works for any dtype
ds = ds.fill.constant(0)  # Fixed constant — use when 0 is the correct interpretation (e.g., no sales)
```

### Temporal fills

```python
ds = ds.fill.forward()             # Carry last known value forward (last-observation-carry-forward)
ds = ds.fill.forward(limit=3)      # Forward fill at most 3 consecutive NaNs
ds = ds.fill.backward()            # Carry next known value backward
ds = ds.fill.backward(limit=3)

ds = ds.fill.interpolate()                        # Linear interpolation between surrounding values
ds = ds.fill.interpolate(method="time")           # Time-weighted interpolation
ds = ds.fill.interpolate(method="polynomial")     # Polynomial interpolation
ds = ds.fill.interpolate(method="spline")         # Cubic spline
```

> Use `interpolate(method="time")` when values should change gradually between known points (e.g., monthly GDP data resampled to daily).

### KNN imputation

```python
ds = ds.numeric().fill.knn()               # 5 nearest neighbors (default)
ds = ds.numeric().fill.knn(n_neighbors=3)  # Custom neighbor count
```

> **When to use:** When missing values have a pattern related to other features (e.g., missing price correlated with category). Slower than other methods but often more accurate for structured missingness.

### Apply fill to specific columns

```python
ds = ds.cols(["sales"]).fill.forward()               # Only forward-fill sales
ds = ds.cols(["price", "promo"]).fill.constant(0)    # Fill price and promo with 0
ds = ds.categorical().fill.mode()                    # Fill all categoricals with mode
```

---

## Step 5 — Column Selection

Use these selectors to narrow which columns a transformation applies to. They return a `ColumnView` — a lightweight wrapper that chains into `.scale`, `.encode`, `.fill`, and `.clean`.

```python
ds.numeric()                     # All numeric columns (int, float)
ds.categorical()                 # All object / category columns
ds.target()                      # The target column only (requires .select() first)
ds.datetime()                    # The datetime column only (requires .select() first)
ds.cols(["price", "promo"])      # Explicit list of column names
ds.regex("price|promo")          # Columns whose name matches a regex pattern
ds.numeric().exclude(["sales"])  # Numeric columns excluding "sales"
```

The `.exclude()` modifier works on any selector:

```python
ds.cols(["a", "b", "c"]).exclude(["b"])   # → applies to ["a", "c"]
ds.numeric().exclude(["sales", "promo"])   # All numeric except sales and promo
```

---

## Step 6 — Encoding Categorical Columns

Machine learning models require numeric inputs. Encode categorical columns before scaling or feature engineering.

### `encode.auto()` — Smart auto-encoding

Chooses the encoding method based on cardinality (number of unique values):

| Cardinality | Method |
|-------------|--------|
| ≤ 15 unique values | One-hot encoding |
| 16 – 200 unique values | Label encoding |
| > 200 unique values | Binary (hash-based) encoding |

```python
ds = ds.categorical().encode.auto()
```

### `encode.one_hot()` — Binary indicator columns

Creates one column per category, named `<col>_<value>`. The original column is dropped. Best for low-cardinality features (< 15 categories) where the model should treat each category independently.

```python
ds = ds.categorical().encode.one_hot()
ds = ds.categorical().encode.one_hot(drop_first=True)  # Avoid multicollinearity in linear models
ds = ds.cols(["channel", "region"]).encode.one_hot()   # Specific columns only
```

> **Example:** A column `channel` with values `["online", "retail", "wholesale"]` becomes three columns: `channel_online`, `channel_retail`, `channel_wholesale`.

> **When to use `drop_first=True`:** Linear regression and logistic regression — avoids the dummy variable trap. For tree-based models (LightGBM, XGBoost), `drop_first=False` is fine.

### `encode.label()` — Integer codes

Replaces each category with an integer (0, 1, 2, …). Compact but implies a false ordering between categories.

```python
ds = ds.categorical().encode.label()
ds = ds.cols(["category"]).encode.label()
```

> **When to use:** High-cardinality columns (50–200 unique values) with tree-based models (LightGBM, XGBoost handle label encoding well). Do NOT use with linear models — the integer order has no meaning.

### `encode.ordinal()` — Order-preserving encoding

```python
ds = ds.cols(["size"]).encode.ordinal()
```

> **When to use:** Columns with a natural ordering like `["small", "medium", "large"]` or `["low", "medium", "high"]`.

### `encode.binary()` — Hash-based for high cardinality

Uses a hashing trick to create a fixed-size binary representation. Handles thousands of unique values without exploding the number of columns.

```python
ds = ds.cols(["product_id"]).encode.binary()
```

> **When to use:** Columns with > 200 unique values (e.g., product IDs, zip codes, customer IDs) where one-hot encoding would be impractical.

---

## Step 7 — Scaling Numeric Columns

Scale numeric features so models with gradient-based optimization converge faster. Tree-based models (LightGBM, XGBoost) are scale-invariant, but scaling still helps for regularization.

> **Important:** Always exclude the target column from scaling if you're using it as a forecasting target — or if you do scale it, make sure to invert the scaling on the predictions.

```python
# Scale everything except the target
ds = ds.numeric().exclude(["sales"]).scale.standard()
```

### `scale.standard()` — Z-score normalization

Transforms to zero mean and unit variance: `(x - mean) / std`. The most common choice.

```python
ds = ds.numeric().scale.standard()
ds = ds.cols(["price", "revenue"]).scale.standard()
```

> **When to use:** General default. Works well when data is roughly normally distributed. Required for distance-based algorithms (KNN, SVM) and neural networks.

### `scale.minmax()` — Scale to [0, 1]

Transforms to `(x - min) / (max - min)`. Every value ends up between 0 and 1.

```python
ds = ds.numeric().scale.minmax()
ds = ds.cols(["promo", "holiday_flag"]).scale.minmax()
```

> **When to use:** When you need values in a bounded range (e.g., neural networks with sigmoid activations). **Sensitive to outliers** — one extreme value compresses all others near 0. Use `robust` if outliers are present.

### `scale.robust()` — Outlier-resistant scaling

Scales using the median and interquartile range (IQR): `(x - median) / IQR`. Outliers do not dominate the scaling.

```python
ds = ds.numeric().scale.robust()
```

> **When to use:** When the data has outliers that you cannot or do not want to remove. Typical for financial, retail, and supply chain data with intermittent spikes.

### `scale.log()` — Log transform

Applies `log(x + 1)` to reduce right skew. Common for sales data, revenue, and any count variable that can span several orders of magnitude.

```python
ds = ds.cols(["sales"]).scale.log()
ds = ds.cols(["revenue"]).scale.log()
```

> **When to use:** When the column's histogram is highly right-skewed (long tail of large values). Check with `ds.inspect.summary()` — if `max / mean > 10`, log transform is worth trying.
>
> **Gotcha:** The column must have non-negative values. Negative values after `log(x + 1)` → undefined. Use `clip(lower=0)` first if needed.

### `scale.power()` — Yeo-Johnson transform

A generalization of the Box-Cox transform that handles negative values. Finds the optimal lambda to make the distribution as normal as possible.

```python
ds = ds.numeric().scale.power()
```

> **When to use:** When `log` is not enough to normalize the distribution, or when the column has negative values. More computationally expensive than `log` but handles edge cases better.

---

## Step 8 — Time-Series Feature Engineering

These methods create new columns from the target column's history. They require `.select()` to have been called first so the library knows which column is the target and which is the datetime.

> **Important:** Apply these **after** cleaning and filling. Lags and rolling stats computed on data with missing values will propagate those NaNs into all derived features.

### `target().lags()` — Lag features

A lag feature is the target's value at a past time step. Lag 1 is "yesterday's sales", lag 7 is "last week's sales at the same weekday".

```python
ds = ds.target().lags([1, 7, 14])
# Creates: sales_lag1, sales_lag7, sales_lag14
```

> **How to choose lags:** Use the partial autocorrelation function (ACF/PACF) from `TimeSeriesValidator` or pick lags that correspond to meaningful business cycles (1 = yesterday, 7 = same weekday last week, 28 = same period last month).

> **Gotcha:** Lags introduce NaN at the start of each series (the first `max_lag` rows have no history). Drop these rows or fill them before training.

### `target().rolling.*` — Rolling statistics

Compute a statistic over a sliding window of past values. Captures recent trends and volatility.

```python
ds = ds.target().rolling.mean([7, 30])    # 7-day and 30-day moving average
# Creates: sales_rollmea7, sales_rollmea30

ds = ds.target().rolling.std([7])         # 7-day rolling standard deviation (volatility)
# Creates: sales_rollstd7

ds = ds.target().rolling.min([7, 14])     # 7-day and 14-day rolling minimum
ds = ds.target().rolling.max([7, 14])     # 7-day and 14-day rolling maximum
```

> **When to use:** `rolling.mean` captures the recent trend (smoothed signal). `rolling.std` captures volatility — useful when demand uncertainty matters. Larger windows give smoother signals; smaller windows react faster to changes.

### `target().ewm()` — Exponential weighted mean

Like a rolling mean, but more recent values are weighted more heavily. The `span` controls how quickly the weight decays.

```python
ds = ds.target().ewm([7, 14])
# Creates: sales_ewm7, sales_ewm14
# span=7: recent 7 periods contribute ~63% of the weight
```

> **When to use:** When recent data is more informative than older data (common in fast-moving consumer goods). Less sensitive to outliers than simple rolling means because old extreme values fade out faster.

### `target().diffs()` — Differencing

Computes the change between the current and a past value: `sales_t - sales_{t-k}`.

```python
ds = ds.target().diffs([1, 7])
# Creates: sales_diff1 (day-over-day change), sales_diff7 (week-over-week change)
```

> **When to use:** When you want to model the change in demand rather than the level. Also useful to stationarize a trending series before feeding it to ARIMA-style models.

---

## Step 9 — Calendar Features

Extracts temporal features from the datetime column, including cyclical encodings that let models understand that December is close to January.

```python
ds = ds.datetime().features.calendar()
```

Creates the following columns (prefixed with your datetime column name, e.g., `date_*`):

| Column | Description | Range |
|--------|-------------|-------|
| `date_year` | Calendar year | 2020, 2021, … |
| `date_month` | Month of year | 1–12 |
| `date_day` | Day of month | 1–31 |
| `date_dow` | Day of week | 0=Mon … 6=Sun |
| `date_week` | ISO week number | 1–53 |
| `date_quarter` | Quarter | 1–4 |
| `date_is_weekend` | Binary: 1 if Sat or Sun | 0 or 1 |
| `date_sin_month` | Cyclical sin of month | -1 … +1 |
| `date_cos_month` | Cyclical cos of month | -1 … +1 |
| `date_sin_dow` | Cyclical sin of day-of-week | -1 … +1 |
| `date_cos_dow` | Cyclical cos of day-of-week | -1 … +1 |
| `date_days_to_easter` | Days until (+) or since (−) Easter | integer |
| `date_days_to_christmas` | Days until (+) or since (−) Christmas | integer |

> **Why cyclical encodings?** Month 12 (December) and month 1 (January) are consecutive, but `12 - 1 = 11` suggests they are far apart. The sin/cos encoding maps the cycle onto a unit circle, so December and January are correctly represented as adjacent.

> **Why holiday distances?** A fixed binary `is_holiday` flag misses the ramp-up before a holiday and the hangover after. The distance feature captures the temporal proximity effect on demand.

---

## Step 10 — Inspection

Inspect the dataset at any point in the chain to understand what transformations have done.

```python
# Full column summary: dtype, null count, null %, min, max, mean, unique count
summary = ds.inspect.summary()
print(summary)

# Only null information
nulls = ds.inspect.nulls()
# Columns with null_count > 0, sorted by null_pct

# Column types and their inferred roles (target, datetime, group, feature)
types = ds.inspect.types()

# Memory usage per column
memory = ds.inspect.memory(verbose=False)
# Columns: column, KB, MB
```

---

## Step 11 — Dataset Properties

```python
len(ds)            # Number of rows
ds.shape           # Tuple (rows, cols)
ds.columns         # List of column names
ds.dtypes          # pandas Series of column dtypes
ds.head(n=5)       # First n rows as a pandas DataFrame
ds.to_dataframe()  # Full pandas DataFrame — use this when done chaining

ds.copy()
# Returns a fully independent deep copy of the Dataset.
# Mutations to the copy do NOT affect the original, and vice versa.
# Use before branching into two different preprocessing paths from the same base.
```

---

## Step 12 — Preprocessing Pipeline

Every transformation you apply is silently recorded in a `TransformRegistry`. When you call `.to_pipeline()`, those steps are packaged into a `Pipeline` that can be saved to disk and replayed on new data — guaranteeing that production preprocessing is identical to training preprocessing.

```python
from forecastlib.pipeline import Pipeline

# After any sequence of transforms
pipeline = ds.to_pipeline()

# Describe all recorded steps
pipeline.summary()
# Output example:
#   Step 1: clean.fix_datetime on ['date']
#   Step 2: fill.smart (actions: {'sales': 'median (98.2)', 'price': 'interpolate'})
#   Step 3: encode.one_hot on ['channel', 'region']
#   Step 4: scale.standard on ['price', 'promo']
#   Step 5: target.lags([1, 7, 14]) on sales
#   Step 6: calendar on date

# Save to disk
pipeline.save("models/sales_pipeline.pkl")

# Load later and inspect
loaded = Pipeline.load("models/sales_pipeline.pkl")
print(f"{len(loaded.steps)} steps recorded")
```

> **Best practice:** Save the pipeline alongside your trained model. At inference time, load both the pipeline and the model, apply the pipeline to new raw data, then feed the result to the model.

---

## Train/Test Splitting

### Simple split

```python
from forecastlib.time_series import TimeSeriesSplitter

splitter = TimeSeriesSplitter()
train, test = splitter.train_test_split(ds, test_ratio=0.2)

print(f"Train: {len(train)} rows, Test: {len(test)} rows")

df_train = train.to_dataframe()
df_test  = test.to_dataframe()
```

The split is done by time (not random). The last `test_ratio` fraction of rows becomes the test set, preserving chronological order.

### Walk-forward cross-validation

Walk-forward (expanding window) CV is the standard evaluation method for time-series models. Each fold uses all data up to a split point for training, and the next window for testing.

```python
splitter = TimeSeriesSplitter(n_splits=5)

for fold_n, (train_fold, test_fold) in enumerate(splitter.split(ds)):
    df_train = train_fold.to_dataframe()
    df_test  = test_fold.to_dataframe()
    print(f"Fold {fold_n+1}: train={len(df_train)}, test={len(df_test)}")
    # ... train your model on df_train, evaluate on df_test ...
```

> **Why walk-forward instead of k-fold?** Standard k-fold shuffles rows randomly, which leaks future information into the training set (a model that "knows" next month's sales from a future row in the training set will score unrealistically well). Walk-forward respects the arrow of time.

---

## Data Quality Validation

```python
from forecastlib.time_series import TimeSeriesValidator

validator = TimeSeriesValidator()
report = validator.check(ds, datetime_col="date")

print(report.sorted)          # True if rows are in chronological order
print(report.has_gaps)        # True if there are missing time steps (e.g., no data for 2024-03-15)
print(report.has_duplicates)  # True if the same (date, group) pair appears more than once
```

> Always run this before building lag features. If `has_gaps` is True and you build lag-1, the lag will point to the wrong row.

---

## Transform Registry (audit trail)

Every operation is recorded with its parameters. You can inspect what was done:

```python
steps = ds._registry.summary()
for step in steps:
    print(step)
# {"op": "clean.fix_datetime", "cols": ["date"], "params": {...}}
# {"op": "fill.smart", "cols": ["sales", "price"], "params": {"actions": {...}}}
# ...
```

---

## Complete Example

```python
from forecastlib.data import Loader
from forecastlib.pipeline import Pipeline
from forecastlib.time_series import TimeSeriesSplitter, TimeSeriesValidator

# ── 1. Load ──────────────────────────────────────────────────────────────
ds = Loader.from_csv("sales.csv")

# ── 2. Assign roles ──────────────────────────────────────────────────────
ds = ds.select(target="sales", datetime="date", group="store")

# ── 3. Validate ──────────────────────────────────────────────────────────
validator = TimeSeriesValidator()
report = validator.check(ds, datetime_col="date")
if report.has_gaps:
    print("Warning: time gaps detected — lags will be misaligned")
if report.has_duplicates:
    print("Warning: duplicate (date, store) pairs found")

# ── 4. Clean ─────────────────────────────────────────────────────────────
ds = (
    ds
    .clean.fix_datetime()          # Parse date strings
    .clean.drop_duplicates()       # Remove exact duplicate rows
    .clean.drop_constant()         # Remove zero-variance columns
    .clean.sort(by="date")         # Sort chronologically
)

# ── 5. Fill ──────────────────────────────────────────────────────────────
ds = ds.fill.time_series()         # Group-aware fill (safe for panel data)

# ── 6. Encode ────────────────────────────────────────────────────────────
ds = ds.categorical().clean.strip()   # Remove whitespace first
ds = ds.categorical().encode.auto()   # Auto-choose encoding per column

# ── 7. Scale features (not the target) ───────────────────────────────────
ds = ds.numeric().exclude(["sales"]).scale.robust()

# ── 8. Feature engineering ───────────────────────────────────────────────
ds = (
    ds
    .target().lags([1, 7, 14, 28])         # Lag features
    .target().rolling.mean([7, 14, 30])    # Moving average
    .target().rolling.std([7])             # Volatility
    .target().ewm([7, 14])                 # Exponential weighted mean
    .target().diffs([1, 7])               # Day-over-day and week-over-week change
    .datetime().features.calendar()        # Calendar + holiday features
)

# ── 9. Inspect ───────────────────────────────────────────────────────────
print(ds.inspect.summary())
print(f"Final shape: {ds.shape}")

# ── 10. Split ────────────────────────────────────────────────────────────
splitter = TimeSeriesSplitter(n_splits=3)
for train, test in splitter.split(ds):
    df_train = train.to_dataframe()
    df_test  = test.to_dataframe()
    # ... train your model ...

# ── 11. Save pipeline ────────────────────────────────────────────────────
pipeline = ds.to_pipeline()
pipeline.save("models/sales_pipeline.pkl")
print(f"Pipeline saved with {len(pipeline.steps)} steps")
```

---

## License

MIT — see [LICENSE](LICENSE)

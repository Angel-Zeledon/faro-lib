# faro-prep

[![PyPI version](https://img.shields.io/pypi/v/faro-prep)](https://pypi.org/project/faro-prep/)
[![Python](https://img.shields.io/pypi/pyversions/faro-prep)](https://pypi.org/project/faro-prep/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Data preprocessing and feature engineering library for time-series forecasting.
Fluent, chainable API that reads like a recipe — load, clean, encode, scale, engineer, inspect — and produces a serializable preprocessing pipeline for reproducibility.

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

df       = ds.to_dataframe()      # Final pandas DataFrame
pipeline = ds.to_pipeline()       # Reproducible pipeline
pipeline.save("pipeline.pkl")
```

---

## Loading Data

### From files

```python
from forecastlib.data import Loader

ds = Loader.from_csv("sales.csv")
ds = Loader.from_csv("sales.csv", sep=";", encoding="latin-1")  # custom params

ds = Loader.from_excel("sales.xlsx")
ds = Loader.from_excel("sales.xlsx", sheet_name="Sheet2")

ds = Loader.from_parquet("sales.parquet")

ds = Loader.from_json("sales.json")
```

### From a DataFrame

```python
import pandas as pd
df = pd.read_csv("sales.csv")
ds = Loader.from_dataframe(df)
```

### From SQL

```python
# PostgreSQL
ds = Loader.from_sql(
    db="postgresql",
    host="localhost",
    database="sales_db",
    user="admin",
    password="secret",
    table="transactions",
)

# MySQL
ds = Loader.from_sql(
    db="mysql", host="localhost", database="mydb",
    user="root", password="pass",
    query="SELECT * FROM sales WHERE year = 2024",
)

# SQLite
ds = Loader.from_sql(db="sqlite", database="local.db", table="sales")

# SQL Server
ds = Loader.from_sql(db="mssql", host="srv", database="db", user="u", password="p", table="t")
```

Supported databases: `postgresql`, `mysql`, `sqlite`, `mssql`.

> Requires the matching driver: `psycopg2`, `pymysql`, or `pyodbc`.

---

## Column Role Assignment

Tell the library which columns play which roles:

```python
ds = ds.select(
    target="sales",       # Column to forecast (required)
    datetime="date",      # Date / timestamp column (required)
    group="store",        # Group key — SKU, store, region (optional)
)
```

---

## Cleaning

```python
ds = ds.clean.fix_datetime()                            # Parse date strings → datetime64
ds = ds.clean.fix_datetime(format="%d/%m/%Y")           # Explicit format
ds = ds.cols(["date_str"]).clean.fix_datetime()         # On specific columns

ds = ds.clean.drop_duplicates()                         # Remove exact duplicate rows
ds = ds.clean.drop_duplicates(subset=["date", "store"]) # Consider only these columns
ds = ds.clean.drop_duplicates(keep="last")              # keep: "first" | "last" | False

ds = ds.clean.drop_nulls()                              # Drop rows with any null
ds = ds.clean.drop_nulls(subset=["sales", "date"])      # Only check these columns
ds = ds.clean.drop_nulls(thresh=5)                      # Require at least 5 non-null values

ds = ds.clean.drop_constant()                           # Remove columns with a single unique value

ds = ds.cols(["price"]).clean.clip(lower=0)             # Clip to minimum
ds = ds.cols(["age"]).clean.clip(lower=0, upper=120)    # Clip to range

ds = ds.categorical().clean.strip()                     # Strip whitespace from string columns

ds = ds.clean.fix_dtypes()                              # Auto-cast: numeric strings→float, low-cardinality→category

ds = ds.clean.rename({"old_col": "new_col"})            # Rename columns

ds = ds.clean.sort(by="date")                           # Sort ascending by column
ds = ds.clean.sort(by=["store", "date"])                # Sort by multiple columns
ds = ds.clean.sort(by="date", ascending=False)          # Descending sort
```

---

## Filling Missing Values

```python
# Smart auto-select per column (recommended starting point)
ds = ds.fill.smart()
# Logic: numeric <5% nulls → median, numeric ≥5% → interpolate, categorical → mode, datetime → ffill

# Statistical fills
ds = ds.fill.mean()               # Column mean (numeric only)
ds = ds.fill.median()             # Column median (numeric only)
ds = ds.fill.mode()               # Most frequent value (any dtype)
ds = ds.fill.constant(0)          # Fixed constant for all NaN

# Temporal fills
ds = ds.fill.forward()            # Forward fill (ffill)
ds = ds.fill.forward(limit=3)     # Forward fill but at most 3 consecutive NaNs
ds = ds.fill.backward()           # Backward fill (bfill)
ds = ds.fill.backward(limit=3)    # Backward fill with limit

# Interpolation
ds = ds.fill.interpolate()               # Linear interpolation (default)
ds = ds.fill.interpolate(method="time")  # Time-based interpolation
ds = ds.fill.interpolate(method="polynomial")  # Any method from pd.Series.interpolate

# Panel-aware fill (group-aware — correct for multi-SKU data)
ds = ds.fill.time_series()
# Fills within each group separately; remaining leading NaNs → bfill → 0

# KNN imputation
ds = ds.numeric().fill.knn()              # Default 5 neighbors
ds = ds.numeric().fill.knn(n_neighbors=3)
```

Apply any fill to specific columns:

```python
ds = ds.cols(["sales", "price"]).fill.forward()
ds = ds.cols(["sales"]).fill.interpolate(method="time")
ds = ds.numeric().fill.knn()
```

---

## Column Selection

Select subsets of columns before applying a transformation:

```python
# By type
ds.numeric()             # All numeric columns
ds.categorical()         # All object / category columns
ds.target()              # The target column only (requires .select() first)
ds.datetime()            # The datetime column only

# By name
ds.cols(["price", "promo"])

# By regex
ds.regex("price|promo")

# Exclude specific columns from a type selection
ds.numeric().exclude(["sales"])    # All numeric except "sales"
```

---

## Encoding Categorical Columns

```python
# Auto: one-hot for ≤15 categories, label for ≤200, binary for >200
ds = ds.categorical().encode.auto()

# One-hot: creates <col>_<value> binary columns, drops original
ds = ds.categorical().encode.one_hot()
ds = ds.categorical().encode.one_hot(drop_first=True)   # Drop first category (avoids multicollinearity)

# Label encoding: replaces values with integer codes 0..n-1
ds = ds.categorical().encode.label()

# Ordinal: encode with a specific order
ds = ds.cols(["size"]).encode.ordinal()

# Binary (hash-based): for very high cardinality (>200 unique values)
ds = ds.cols(["product_id"]).encode.binary()

# Target specific columns
ds = ds.cols(["region", "channel"]).encode.one_hot()
ds = ds.cols(["category"]).encode.label()
```

---

## Scaling Numeric Columns

```python
ds = ds.numeric().scale.standard()          # Z-score: (x - mean) / std
ds = ds.numeric().scale.minmax()            # Scale to [0, 1]
ds = ds.numeric().scale.robust()            # Median-centered, IQR-scaled (outlier-resistant)
ds = ds.numeric().scale.log()               # Natural log: log(x + 1)
ds = ds.numeric().scale.power()             # Yeo-Johnson power transform (handles negatives)

# Scale features, leave target untouched
ds = ds.numeric().exclude(["sales"]).scale.standard()

# Scale specific columns
ds = ds.cols(["price", "promo"]).scale.minmax()
ds = ds.cols(["revenue"]).scale.log()
```

---

## Time-Series Feature Engineering

These methods require `.select()` to have been called first.

### Lag Features

```python
ds = ds.target().lags([1, 7, 14])
# Creates: sales_lag1, sales_lag7, sales_lag14
```

### Rolling Statistics

```python
ds = ds.target().rolling.mean([7, 30])    # → sales_rollmea7, sales_rollmea30
ds = ds.target().rolling.std([7])         # → sales_rollstd7
ds = ds.target().rolling.min([7, 14])     # → sales_rollmin7, sales_rollmin14
ds = ds.target().rolling.max([7, 14])     # → sales_rollmax7, sales_rollmax14
```

### Exponential Weighted Mean

```python
ds = ds.target().ewm([7, 14])
# Creates: sales_ewm7, sales_ewm14
```

### Differencing

```python
ds = ds.target().diffs([1, 7])
# Creates: sales_diff1, sales_diff7
```

---

## Calendar Features

```python
ds = ds.datetime().features.calendar()
```

Creates the following columns (prefixed with the datetime column name):

| Column | Description |
|--------|-------------|
| `date_year` | Year (integer) |
| `date_month` | Month 1–12 |
| `date_day` | Day of month |
| `date_dow` | Day of week (0=Monday) |
| `date_week` | ISO week number |
| `date_quarter` | Quarter 1–4 |
| `date_is_weekend` | 1 if Saturday or Sunday |
| `date_sin_month` | Cyclical sin encoding of month |
| `date_cos_month` | Cyclical cos encoding of month |
| `date_sin_dow` | Cyclical sin encoding of day-of-week |
| `date_cos_dow` | Cyclical cos encoding of day-of-week |
| `date_days_to_easter` | Days until/since Easter (Colombia-calibrated) |
| `date_days_to_christmas` | Days until/since Christmas |

---

## Inspection

```python
summary = ds.inspect.summary()    # DataFrame: column, dtype, nulls, nunique, min, max, mean
nulls   = ds.inspect.nulls()      # DataFrame: column, null_count, null_pct
types   = ds.inspect.types()      # DataFrame: column, dtype, inferred_role
memory  = ds.inspect.memory()     # DataFrame: column, KB, MB
```

---

## Dataset Properties

```python
len(ds)           # Number of rows
ds.shape          # (rows, cols)
ds.columns        # List of column names
ds.dtypes         # Series of dtypes
ds.head(n=5)      # First n rows as DataFrame
ds.to_dataframe() # Full pandas DataFrame
ds.copy()         # Deep copy — fully independent from the original
                  # (mutations to ds do not affect the copy and vice versa)
```

---

## Preprocessing Pipeline

Capture all transformations as a reproducible pipeline:

```python
from forecastlib.pipeline import Pipeline

# After any chain of transforms
pipeline = ds.to_pipeline()
pipeline.summary()           # Print all steps

# Save to disk
pipeline.save("pipeline.pkl")

# Load and inspect later
loaded = Pipeline.load("pipeline.pkl")
print(loaded.steps)
```

---

## Train / Test Splitting

```python
from forecastlib.time_series import TimeSeriesSplitter

splitter = TimeSeriesSplitter()

# Simple train/test split
train, test = splitter.train_test_split(ds, test_ratio=0.2)
print(len(train), len(test))

# Walk-forward cross-validation (expanding window)
splitter_cv = TimeSeriesSplitter(n_splits=5)
for train_fold, test_fold in splitter_cv.split(ds):
    print(f"  train={len(train_fold)}, test={len(test_fold)}")
```

---

## Data Quality Validation

```python
from forecastlib.time_series import TimeSeriesValidator

validator = TimeSeriesValidator()
report = validator.check(ds, datetime_col="date")

print(report.sorted)           # True if sorted chronologically
print(report.has_gaps)         # True if there are missing time steps
print(report.has_duplicates)   # True if duplicate timestamps exist
```

---

## Transform Registry

Every operation is recorded and can be audited:

```python
steps = ds._registry.summary()
for step in steps:
    print(step)   # e.g., {"op": "scale.standard", "cols": ["price"], ...}
```

---

## Complete Example

```python
from forecastlib.data import Loader
from forecastlib.pipeline import Pipeline
from forecastlib.time_series import TimeSeriesSplitter, TimeSeriesValidator

# 1. Load
ds = Loader.from_csv("sales.csv")

# 2. Assign roles
ds = ds.select(target="sales", datetime="date", group="store")

# 3. Validate before transforming
validator = TimeSeriesValidator()
report = validator.check(ds, datetime_col="date")
if report.has_gaps:
    print("Warning: time gaps detected")

# 4. Clean
ds = (
    ds
    .clean.fix_datetime()
    .clean.drop_duplicates()
    .clean.sort(by="date")
)

# 5. Fill
ds = ds.fill.smart()

# 6. Encode
ds = ds.categorical().encode.auto()

# 7. Scale features (not target)
ds = ds.numeric().exclude(["sales"]).scale.standard()

# 8. Time-series features
ds = (
    ds
    .target().lags([1, 7, 14])
    .target().rolling.mean([7, 30])
    .target().rolling.std([7])
    .target().ewm([7, 14])
    .target().diffs([1])
    .datetime().features.calendar()
)

# 9. Inspect
print(ds.inspect.summary())

# 10. Split
splitter = TimeSeriesSplitter(n_splits=3)
for train, test in splitter.split(ds):
    df_train = train.to_dataframe()
    df_test  = test.to_dataframe()
    # ... train your model ...

# 11. Save pipeline
pipeline = ds.to_pipeline()
pipeline.save("sales_pipeline.pkl")
```

---

## License

MIT — see [LICENSE](LICENSE)

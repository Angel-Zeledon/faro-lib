"""
forecastlib smoke test — covers the full API chain end-to-end.

Run:
    python smoke_test.py
"""

import sys, traceback, tempfile, os
from pathlib import Path

# ── helpers ────────────────────────────────────────────────────────────────────

PASS = "\033[32m PASS\033[0m"
FAIL = "\033[31m FAIL\033[0m"
results = []

def check(name):
    """Context manager that records pass/fail."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        try:
            yield
            results.append((name, True, None))
            print(f"[{PASS}] {name}")
        except Exception as exc:
            results.append((name, False, exc))
            print(f"[{FAIL}] {name}")
            traceback.print_exc()

    return _ctx()


# ── synthetic data ──────────────────────────────────────────────────────────────

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)

N_STORES = 3
N_DAYS   = 120

dates  = pd.date_range("2023-01-01", periods=N_DAYS, freq="D")
stores = ["A", "B", "C"]

rows = []
for store in stores:
    for date in dates:
        rows.append({
            "date":     date.strftime("%Y-%m-%d"),   # string, needs fix_datetime
            "store":    store,
            "sales":    max(0, rng.normal(100, 20)) if rng.random() > 0.05 else np.nan,
            "price":    round(float(rng.uniform(5, 50)), 2),
            "promo":    int(rng.random() > 0.8),
            "channel":  rng.choice(["online", "retail", "wholesale"]),
            "region":   rng.choice(["north", "south"]),
        })

raw_df = pd.DataFrame(rows)

# ── import checks ───────────────────────────────────────────────────────────────

with check("import forecastlib"):
    import forecastlib
    print(f"       version: {forecastlib.__version__}")

with check("import Loader, Dataset"):
    from forecastlib.data import Loader, Dataset

with check("import Pipeline"):
    from forecastlib.pipeline import Pipeline

with check("import TimeSeriesSplitter, TimeSeriesValidator"):
    from forecastlib.time_series import TimeSeriesSplitter, TimeSeriesValidator

with check("import all preprocessing"):
    from forecastlib.preprocessing import (
        ColumnView, ScaleAccessor, EncodeAccessor,
        FillAccessor, CleanAccessor, TransformStep, TransformRegistry,
    )

with check("import InspectAccessor"):
    from forecastlib.inspection import InspectAccessor

# ── Loader ──────────────────────────────────────────────────────────────────────

with check("Loader.from_dataframe"):
    ds = Loader.from_dataframe(raw_df.copy())
    assert len(ds) == N_STORES * N_DAYS, f"Expected {N_STORES * N_DAYS} rows, got {len(ds)}"

with check("Loader.from_csv (roundtrip)"):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        raw_df.to_csv(f, index=False)
        tmp_path = f.name
    ds_csv = Loader.from_csv(tmp_path)
    os.unlink(tmp_path)
    assert len(ds_csv) == N_STORES * N_DAYS

# ── .select ─────────────────────────────────────────────────────────────────────

with check(".select(target, datetime, group)"):
    ds = Loader.from_dataframe(raw_df.copy())
    ds = ds.select(target="sales", datetime="date", group="store")
    assert ds.schema.target_col   == "sales"
    assert ds.schema.datetime_col == "date"
    assert ds.schema.group_col    == "store"

# ── .clean ──────────────────────────────────────────────────────────────────────

with check(".clean.fix_datetime()"):
    # Loader.from_dataframe may already convert dates (category) — just ensure
    # fix_datetime() produces a proper datetime64 column regardless.
    ds = ds.clean.fix_datetime()
    assert pd.api.types.is_datetime64_any_dtype(ds._df["date"]), \
        f"Expected datetime64, got {ds._df['date'].dtype}"

with check(".clean.drop_duplicates()"):
    n_before = len(ds)
    ds = ds.clean.drop_duplicates()
    assert len(ds) == n_before   # no actual duplicates in synthetic data

with check(".clean.sort(by='date')"):
    ds = ds.clean.sort(by="date")

# ── .fill ───────────────────────────────────────────────────────────────────────

with check(".fill.smart() removes NaNs from sales"):
    null_before = ds._df["sales"].isna().sum()
    assert null_before > 0, "Synthetic data should have some NaN sales"
    ds = ds.fill.smart()
    null_after  = ds._df["sales"].isna().sum()
    assert null_after == 0, f"Still {null_after} NaN after fill.smart()"

# ── .categorical().encode ────────────────────────────────────────────────────────

with check(".categorical().encode.auto()"):
    cats_before = ds._df.select_dtypes(include=["object", "category"]).columns.tolist()
    cats_before = [c for c in cats_before if c not in ("date",)]
    ds = ds.categorical().encode.auto()
    # check that original categorical columns are gone (replaced by encoded)
    for c in ["channel", "region"]:
        assert c not in ds._df.columns or \
               not ds._df[c].dtype == "object", \
               f"Column {c!r} still object after encode.auto()"

# ── .numeric().scale ─────────────────────────────────────────────────────────────

with check(".numeric().exclude(['sales']).scale.standard()"):
    # Scale features but NOT the target
    ds = ds.numeric().exclude(["sales"]).scale.standard()
    # price should now be roughly mean=0, std=1
    assert abs(ds._df["price"].mean()) < 2.0, "price mean should be near 0 after standard scaling"

# ── .target().lags ───────────────────────────────────────────────────────────────

with check(".target().lags([1, 7, 14])"):
    cols_before = set(ds._df.columns)
    ds = ds.target().lags([1, 7, 14])
    new_cols = set(ds._df.columns) - cols_before
    assert "sales_lag1"  in ds._df.columns, "Missing sales_lag1"
    assert "sales_lag7"  in ds._df.columns, "Missing sales_lag7"
    assert "sales_lag14" in ds._df.columns, "Missing sales_lag14"

# ── .target().rolling ────────────────────────────────────────────────────────────

with check(".target().rolling.mean([7, 30])"):
    ds = ds.target().rolling.mean([7, 30])
    assert "sales_rollmea7"  in ds._df.columns, "Missing rolling mean 7"
    assert "sales_rollmea30" in ds._df.columns, "Missing rolling mean 30"

with check(".target().rolling.std([7])"):
    ds = ds.target().rolling.std([7])
    assert "sales_rollstd7" in ds._df.columns

# ── .target().ewm ────────────────────────────────────────────────────────────────

with check(".target().ewm([7, 14])"):
    ds = ds.target().ewm([7, 14])
    assert "sales_ewm7"  in ds._df.columns
    assert "sales_ewm14" in ds._df.columns

# ── .target().diffs ──────────────────────────────────────────────────────────────

with check(".target().diffs([1])"):
    ds = ds.target().diffs([1])
    assert "sales_diff1" in ds._df.columns

# ── .datetime().features.calendar() ─────────────────────────────────────────────

with check(".datetime().features.calendar()"):
    cols_before = set(ds._df.columns)
    ds = ds.datetime().features.calendar()
    assert "date_year"     in ds._df.columns, "Missing date_year"
    assert "date_month"    in ds._df.columns, "Missing date_month"
    assert "date_dow"      in ds._df.columns, "Missing date_dow"
    assert "date_sin_month" in ds._df.columns, "Missing date_sin_month"

# ── .inspect ─────────────────────────────────────────────────────────────────────

with check(".inspect.summary()"):
    summary = ds.inspect.summary()
    assert isinstance(summary, pd.DataFrame), "summary() should return a DataFrame"
    assert len(summary) == len(ds._df.columns)

with check(".inspect.nulls()"):
    nulls = ds.inspect.nulls()
    assert isinstance(nulls, pd.DataFrame)

with check(".inspect.types()"):
    types = ds.inspect.types()
    assert isinstance(types, pd.DataFrame)

with check(".inspect.memory()"):
    mem = ds.inspect.memory(verbose=False)
    assert isinstance(mem, pd.DataFrame)
    assert "KB" in mem.columns and "MB" in mem.columns

# ── repr ──────────────────────────────────────────────────────────────────────────

with check("Dataset.__repr__"):
    r = repr(ds)
    assert "Dataset(" in r
    assert "rows" in r
    assert "sales" in r

# ── transform registry ────────────────────────────────────────────────────────────

with check("TransformRegistry.summary()"):
    s = ds._registry.summary()
    assert isinstance(s, list)
    assert len(s) > 0, "Registry should have recorded steps"
    print(f"       {len(s)} steps recorded")

# ── to_pipeline ───────────────────────────────────────────────────────────────────

with check("Dataset.to_pipeline()"):
    pipeline = ds.to_pipeline()
    assert isinstance(pipeline, Pipeline)

with check("Pipeline.summary()"):
    pipeline.summary()

with check("Pipeline.save() / Pipeline.load()"):
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
        pkl_path = f.name
    pipeline.save(pkl_path)
    loaded = Pipeline.load(pkl_path)
    os.unlink(pkl_path)
    assert len(loaded.steps) == len(pipeline.steps), "Loaded pipeline has different step count"

# ── TimeSeriesSplitter ────────────────────────────────────────────────────────────

with check("TimeSeriesSplitter.train_test_split()"):
    splitter = TimeSeriesSplitter()
    train, test = splitter.train_test_split(ds, test_ratio=0.2)
    assert len(train) + len(test) == len(ds), "Split sizes don't add up"
    assert len(test) > 0
    assert len(train) > 0

with check("TimeSeriesSplitter.split() expanding window"):
    splitter2 = TimeSeriesSplitter(n_splits=3)
    splits = list(splitter2.split(ds))
    assert len(splits) == 3, f"Expected 3 splits, got {len(splits)}"
    for tr, te in splits:
        assert len(tr) > 0
        assert len(te) > 0

# ── TimeSeriesValidator ───────────────────────────────────────────────────────────

with check("TimeSeriesValidator.check()"):
    validator = TimeSeriesValidator()
    report = validator.check(ds, datetime_col="date")
    assert hasattr(report, "sorted")
    assert hasattr(report, "has_gaps")
    assert hasattr(report, "has_duplicates")
    print(f"       sorted={report.sorted}  gaps={report.has_gaps}  dupes={report.has_duplicates}")

# ── cols().encode / cols().scale ─────────────────────────────────────────────────

with check(".cols(['promo']).scale.minmax()"):
    ds2 = Loader.from_dataframe(raw_df.copy())
    ds2 = ds2.select(target="sales", datetime="date", group="store")
    ds2 = ds2.cols(["price", "promo"]).scale.minmax()
    assert ds2._df["price"].min() >= -0.01
    assert ds2._df["price"].max() <=  1.01

with check(".cols(['channel']).encode.one_hot()"):
    ds3 = Loader.from_dataframe(raw_df.copy())
    ds3 = ds3.cols(["channel"]).encode.one_hot()
    assert "channel" not in ds3._df.columns, "Original column should be dropped after one-hot"
    ohe_cols = [c for c in ds3._df.columns if c.startswith("channel_")]
    assert len(ohe_cols) == 3, f"Expected 3 OHE columns for channel, got {len(ohe_cols)}: {ohe_cols}"

# ── regex selection ───────────────────────────────────────────────────────────────

with check(".regex('price|promo').scale.robust()"):
    ds4 = Loader.from_dataframe(raw_df.copy())
    ds4 = ds4.regex("price|promo").scale.robust()
    assert "price" in ds4._df.columns

# ── fill strategies ───────────────────────────────────────────────────────────────

with check(".fill.forward() on cols with NaN"):
    df_nan = raw_df.copy()
    df_nan.loc[0:5, "sales"] = np.nan
    ds5 = Loader.from_dataframe(df_nan)
    ds5 = ds5.cols(["sales"]).fill.forward()
    # first row may still be nan (nothing before it), that's fine
    assert ds5._df["sales"].isna().sum() <= 1

with check(".fill.median() on numeric"):
    ds6 = Loader.from_dataframe(raw_df.copy())
    ds6 = ds6.numeric().fill.median()

with check(".fill.constant(0) on all"):
    ds7 = Loader.from_dataframe(raw_df.copy())
    ds7 = ds7.fill.constant(0)
    assert ds7._df.isna().sum().sum() == 0

# ── exclude() ─────────────────────────────────────────────────────────────────────

with check(".numeric().exclude(['price']).scale.log()"):
    ds8 = Loader.from_dataframe(raw_df.copy())
    ds8 = ds8.fill.constant(1.0)       # ensure positive for log
    ds8 = ds8.numeric().exclude(["price"]).scale.log()

# ── Dataset.copy() ────────────────────────────────────────────────────────────────

with check("Dataset.copy() deep-copies state"):
    a = Loader.from_dataframe(raw_df.copy())
    b = a.copy()
    a._df["sales"] = 0
    assert b._df["sales"].mean() != 0, "copy() should be independent"

# ── shape / columns / dtypes passthrough ──────────────────────────────────────────

with check("shape / columns / dtypes / head()"):
    assert ds.shape[0] > 0
    assert len(ds.columns) > 0
    assert len(ds.dtypes)  > 0
    assert len(ds.head())  == 5

# ── end-of-chain to_dataframe ────────────────────────────────────────────────────

with check(".to_dataframe() returns clean DataFrame"):
    final_df = ds.to_dataframe()
    assert isinstance(final_df, pd.DataFrame)
    assert len(final_df) == len(ds)

# ── summary ───────────────────────────────────────────────────────────────────────

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total  = len(results)

print()
print("=" * 60)
print(f"  forecastlib smoke test:  {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    print()
    print("Failed tests:")
    for name, ok, exc in results:
        if not ok:
            print(f"  - {name}: {exc}")
else:
    print("  — all green")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)

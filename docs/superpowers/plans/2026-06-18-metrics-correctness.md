# Metrics Correctness Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the systemic bug where statistical/DL forecasting models (ARIMA, Prophet, ETS, Croston, SARIMAX, LSTM) compute full evaluation metrics (MAE, RMSE, WAPE, Bias, MAPE) but discard everything except MAE before they reach the results table; add the missing sMAPE metric; remove the duplicate `global_wape()` body; and make the frontend display all metrics consistently with what the backend now reports.

**Architecture:** All metric math lives in one place (`forecasting_core/evaluation/metrics.py`). Every model wrapper (`models/*.py`) already calls `evaluate_all()` for its eval split but unpacks only `["mae"]` before building its result dict — the fix is to keep the full dict instead of cherry-picking one key. `pipelines/pipeline.py::_flatten()` and `engine.py::get_metrics()` then need to forward the now-available keys into the metrics DataFrame and the `by_model` aggregate instead of silently dropping them. The frontend (`types.ts`, `forecast/page.tsx` Step 9) is a pure consumer and just needs its types and render code extended to show the new fields — no new backend endpoints are needed.

**Tech Stack:** Python (pandas, numpy, statsmodels, Prophet, TensorFlow/Keras for LSTM), pytest. Next.js/TypeScript frontend (no new deps).

## Global Constraints

- Do not change the public signature of `evaluate_all()`, `mae()`, `rmse()`, `wape()`, `bias()`, `mape()` — only add `smape()` and extend the dict `evaluate_all()` returns.
- Do not change the public signature of `global_wape()` — same inputs/outputs, just delegate to `wape()` internally to remove the duplicate body.
- Do not change the `run_*_core(...)` function signatures in `models/*.py` — only what their returned `result` dict contains.
- Existing tests in `ForecastingCore/tests/` that assert `"mae" in results[...]` or `results[...]["mae"] >= 0` must continue to pass unmodified (adding keys is additive, not breaking).
- Run all commands from the repo root `C:\Users\Jahir\Documents\forecasting` unless stated otherwise.

---

### Task 1: Add `smape()` and remove the `global_wape()` duplicate

**Files:**
- Modify: `ForecastingCore/forecasting_core/evaluation/metrics.py`
- Test: `ForecastingCore/tests/test_metrics.py`

**Interfaces:**
- Produces: `smape(y, yhat, eps: float = 1e-8) -> float` — symmetric MAPE, `mean(2*|y-yhat| / (|y|+|yhat|+eps))`, range [0, 2].
- Produces: `evaluate_all(y, yhat) -> Dict[str, float]` now returns keys `{"mae", "rmse", "wape", "bias", "mape", "smape"}`.

- [ ] **Step 1: Write the failing tests**

Add to `ForecastingCore/tests/test_metrics.py`, inside `class TestScalarMetrics` add these methods, and update `TestEvaluateAll.test_returns_all_keys`:

```python
    def test_smape_perfect(self):
        assert smape([10, 20, 30], [10, 20, 30]) == 0.0

    def test_smape_zero_protection(self):
        # both y and yhat zero → denominator guarded by eps, must not raise/inf/nan
        result = smape([0, 0], [0, 0])
        assert np.isfinite(result)

    def test_smape_known_value(self):
        # |10-12| = 2, denom = (10+12)/2 = 11 → 2/11 ≈ 0.1818
        assert smape([10], [12]) == pytest.approx(2 * 2 / 22, abs=1e-6)

    def test_smape_bounded(self):
        # sMAPE is bounded in [0, 2] by construction
        result = smape([1, 1000], [1000, 1])
        assert 0.0 <= result <= 2.0
```

Update the import line at the top of the file:

```python
from forecasting_core.evaluation.metrics import (
    mae, rmse, wape, bias, mape, smape,
    evaluate_all, evaluate_by_horizon,
    forecast_intervals, global_wape, business_loss,
)
```

Update `test_returns_all_keys` in `class TestEvaluateAll`:

```python
    def test_returns_all_keys(self):
        result = evaluate_all([1, 2, 3], [1, 2, 3])
        assert set(result.keys()) == {"mae", "rmse", "wape", "bias", "mape", "smape"}
```

Add a new test class confirming `global_wape` is no longer an independent implementation (behavioral, not structural — we verify it always matches `wape` exactly, which is the contract that lets us collapse the duplicate body):

```python
class TestGlobalWapeDelegatesToWape:

    def test_matches_wape_exactly_on_random_data(self):
        rng = np.random.default_rng(42)
        y = rng.normal(100, 20, 50)
        yhat = y + rng.normal(0, 5, 50)
        assert global_wape(y, yhat) == wape(y, yhat)

    def test_matches_wape_on_zero_actuals(self):
        assert global_wape([0, 0, 0], [1, 2, 3]) == wape([0, 0, 0], [1, 2, 3])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ForecastingCore/tests/test_metrics.py -v`
Expected: `ImportError: cannot import name 'smape'` (or `NameError`), and `test_returns_all_keys` FAILS on the old key set.

- [ ] **Step 3: Implement**

In `ForecastingCore/forecasting_core/evaluation/metrics.py`, add `smape` after `mape` (after line 37):

```python
def smape(y, yhat, eps: float = 1e-8) -> float:
    """Symmetric MAPE. Bounded in [0, 2]; avoids MAPE's blow-up near y≈0."""
    y, yhat = np.array(y, float), np.array(yhat, float)
    return float(np.mean(2 * np.abs(y - yhat) / (np.abs(y) + np.abs(yhat) + eps)))
```

Update `evaluate_all` (currently lines 40-43):

```python
def evaluate_all(y, yhat) -> Dict[str, float]:
    """Returns all metrics in one dict."""
    return {"mae": mae(y, yhat), "rmse": rmse(y, yhat),
            "wape": wape(y, yhat), "bias": bias(y, yhat),
            "mape": mape(y, yhat), "smape": smape(y, yhat)}
```

Update the module docstring example at the top (lines 1-15) to mention `smape` in the function list and example output — replace:

```python
"""
Evaluation metrics for forecasting.

Functions:
    mae, rmse, wape, bias, mape, smape — scalar metrics
    evaluate_all                        — all metrics at once
    evaluate_by_horizon                 — MAE@h for each step
    forecast_intervals                  — empirical P50/P90/P95
    business_loss                       — cost-weighted error

Example:
    from forecasting_core.evaluation.metrics import evaluate_all
    metrics = evaluate_all(y_true, y_pred)
    # → {"mae": 4.2, "rmse": 5.8, "wape": 0.12, "bias": -0.3, "mape": 0.09, "smape": 0.10}
"""
```

Replace `global_wape` (currently lines 77-80) to delegate instead of duplicating:

```python
def global_wape(y, yhat) -> float:
    """WAPE aggregated across all observations (alias of wape — kept for call-site clarity)."""
    return wape(y, yhat)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ForecastingCore/tests/test_metrics.py -v`
Expected: PASS (all tests, including the pre-existing `TestGlobalWapeBusinessLoss` class).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/evaluation/metrics.py ForecastingCore/tests/test_metrics.py
git commit -m "fix: add sMAPE metric and remove global_wape duplicate body"
```

---

### Task 2: Stop discarding RMSE/WAPE/Bias/MAPE/sMAPE in statistical model wrappers

**Files:**
- Modify: `ForecastingCore/forecasting_core/models/arima.py`
- Modify: `ForecastingCore/forecasting_core/models/ets.py`
- Modify: `ForecastingCore/forecasting_core/models/prophet.py`
- Modify: `ForecastingCore/forecasting_core/models/croston.py`
- Modify: `ForecastingCore/forecasting_core/models/sarimax.py`
- Test: `ForecastingCore/tests/test_models.py`
- Test: `ForecastingCore/tests/test_prophet.py`
- Test: `ForecastingCore/tests/test_sarimax.py`

**Interfaces:**
- Consumes: `evaluate_all(y, yhat) -> Dict[str, float]` from Task 1 (now includes `smape`).
- Produces: `run_arima_core(...)`, `run_ets_core(...)`, `run_prophet_core(...)`, `run_croston_core(...)`, `run_sarimax_core(...)` each return, per SKU, a dict that always contains `{"mae", "rmse", "wape", "bias", "mape", "smape"}` (plus `forecast`/`p10`/`p50`/`p90`/`residuals` when `horizon > 0`, unchanged from today).

- [ ] **Step 1: Write the failing tests**

Add to `ForecastingCore/tests/test_models.py` (append a new class; check the existing imports at the top of the file already import `run_arima_core`, `run_ets_core`, `run_croston_core` — reuse the same fixture data pattern already used by the existing tests in this file for SKU "A"/"B"):

```python
class TestStatModelsReportFullMetrics:
    """Every stat model must report the full metric set, not just MAE (regression
    guard for the bug where wrappers called evaluate_all() then kept only ['mae'])."""

    FULL_KEYS = {"mae", "rmse", "wape", "bias", "mape", "smape"}

    def test_arima_reports_full_metrics(self):
        df = _make_series_df()  # reuse whatever helper/fixture this file already uses for ARIMA tests
        results = run_arima_core(df, "date", "y", "sku", train_ratio=0.8,
                                  min_rows=10, seasonal_period=7)
        assert self.FULL_KEYS.issubset(results["A"].keys())

    def test_ets_reports_full_metrics(self):
        df = _make_series_df()
        results = run_ets_core(df, "date", "y", "sku", train_ratio=0.8,
                                min_rows=10, seasonal_period=7)
        assert self.FULL_KEYS.issubset(results["A"].keys())

    def test_croston_reports_full_metrics(self):
        df = _make_intermittent_series_df()  # reuse this file's intermittent-series helper
        results = run_croston_core(df, "date", "y", "sku", train_ratio=0.8,
                                    min_rows=10, seasonal_period=7)
        assert self.FULL_KEYS.issubset(results["A"].keys())
```

If `test_models.py` does not already have helpers named `_make_series_df` / `_make_intermittent_series_df`, open the file first and reuse whatever fixture/helper the existing ARIMA/ETS/Croston tests in that file already call (the existing tests at lines ~55 and ~89 and ~129 prove such a helper exists — read the file to get its exact name before writing this test, then use that exact name instead of the placeholder names above).

Add to `ForecastingCore/tests/test_prophet.py` (reuse that file's existing fixture for a valid Prophet-trainable dataframe):

```python
def test_prophet_reports_full_metrics(<reuse existing fixture args from this file>):
    results = run_prophet_core(<same args the existing passing test in this file uses>)
    full_keys = {"mae", "rmse", "wape", "bias", "mape", "smape"}
    sku_result = next(iter(results.values()))
    assert full_keys.issubset(sku_result.keys())
```

Add to `ForecastingCore/tests/test_sarimax.py` (reuse that file's existing fixture):

```python
def test_sarimax_reports_full_metrics(<reuse existing fixture args from this file>):
    results = run_sarimax_core(<same args the existing passing test in this file uses>)
    full_keys = {"mae", "rmse", "wape", "bias", "mape", "smape"}
    sku_result = next(iter(results.values()))
    assert full_keys.issubset(sku_result.keys())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ForecastingCore/tests/test_models.py ForecastingCore/tests/test_prophet.py ForecastingCore/tests/test_sarimax.py -v -k "full_metrics"`
Expected: FAIL — `assert {"rmse", "wape", "bias", "mape", "smape"}.issubset({"mae"})` is false (each wrapper's result dict currently has only `"mae"`).

- [ ] **Step 3: Implement**

`ForecastingCore/forecasting_core/models/arima.py` — replace lines 30-31:

```python
            mae_val = evaluate_all(series.iloc[cut:].values, preds.values)["mae"]
            result = {"mae": mae_val}
```

with:

```python
            result = evaluate_all(series.iloc[cut:].values, preds.values)
```

`ForecastingCore/forecasting_core/models/ets.py` — replace lines 30-31:

```python
            mae_val = evaluate_all(test, model.forecast(len(test)))["mae"]
            result = {"mae": mae_val}
```

with:

```python
            result = evaluate_all(test, model.forecast(len(test)))
```

`ForecastingCore/forecasting_core/models/prophet.py` — replace lines 29-30:

```python
            mae_val = evaluate_all(d.iloc[cut:]["y"].values, fc["yhat"].values)["mae"]
            result = {"mae": mae_val}
```

with:

```python
            result = evaluate_all(d.iloc[cut:]["y"].values, fc["yhat"].values)
```

`ForecastingCore/forecasting_core/models/croston.py` — replace lines 40-41:

```python
            mae_val = evaluate_all(series[cut:], preds)["mae"]
            result = {"mae": mae_val}
```

with:

```python
            result = evaluate_all(series[cut:], preds)
```

`ForecastingCore/forecasting_core/models/sarimax.py` — replace lines 120-122:

```python
            from forecasting_core.evaluation.metrics import evaluate_all
            mae_val = evaluate_all(series.iloc[cut:].values, fc_test.values)["mae"]
            result = {"mae": mae_val}
```

with:

```python
            from forecasting_core.evaluation.metrics import evaluate_all
            result = evaluate_all(series.iloc[cut:].values, fc_test.values)
```

In every file above, leave everything after the replaced lines untouched (`if horizon > 0: result["forecast"] = ...` etc. already mutate the same `result` dict, so they keep working unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ForecastingCore/tests/test_models.py ForecastingCore/tests/test_prophet.py ForecastingCore/tests/test_sarimax.py ForecastingCore/tests/test_metrics.py -v`
Expected: PASS (full suite for these files, including all pre-existing tests — confirms the fix is additive).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/models/arima.py ForecastingCore/forecasting_core/models/ets.py ForecastingCore/forecasting_core/models/prophet.py ForecastingCore/forecasting_core/models/croston.py ForecastingCore/forecasting_core/models/sarimax.py ForecastingCore/tests/test_models.py ForecastingCore/tests/test_prophet.py ForecastingCore/tests/test_sarimax.py
git commit -m "fix: stop discarding RMSE/WAPE/Bias/MAPE/sMAPE in stat model wrappers"
```

---

### Task 3: Fix LSTM to compute the full metric set via `evaluate_all()`

**Files:**
- Modify: `ForecastingCore/forecasting_core/models/lstm.py`
- Test: `ForecastingCore/tests/test_models.py` (or wherever existing LSTM tests live — search `run_lstm_core` across `ForecastingCore/tests/` first to find the right file before adding)

**Interfaces:**
- Consumes: `evaluate_all` from Task 1.
- Produces: `run_lstm_core(...)` per-SKU result dict now contains `{"mae", "rmse", "wape", "bias", "mape", "smape"}` instead of only `"mae"`.

- [ ] **Step 1: Write the failing test**

First run `grep -rn "run_lstm_core" ForecastingCore/tests/` to find the existing LSTM test file and its fixture/skip-marker pattern (LSTM requires TensorFlow, so existing tests likely skip if TF is unavailable — match that pattern exactly). Then add, in that file, reusing its existing fixture and skip marker:

```python
def test_lstm_reports_full_metrics(<reuse existing fixture args from this file>):
    results = run_lstm_core(<same args the existing passing LSTM test in this file uses>)
    full_keys = {"mae", "rmse", "wape", "bias", "mape", "smape"}
    sku_result = next(iter(results.values()))
    assert full_keys.issubset(sku_result.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest ForecastingCore/tests/test_models.py -v -k "lstm and full_metrics"`
Expected: FAIL (current result dict only has `"mae"`), or SKIPPED if TensorFlow is not installed in this environment — if skipped, still proceed with Step 3 (the fix must be correct regardless of whether this environment can execute it).

- [ ] **Step 3: Implement**

In `ForecastingCore/forecasting_core/models/lstm.py`, add the import near the top (after line 13 `import numpy as np`):

```python
from forecasting_core.evaluation.metrics import evaluate_all
```

Replace lines 207-208:

```python
            mae_val = float(np.mean(np.abs(y_te_real - preds_real)))
            result = {"mae": mae_val}
```

with:

```python
            result = evaluate_all(y_te_real, preds_real)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest ForecastingCore/tests/test_models.py -v -k "lstm and full_metrics"`
Expected: PASS or SKIPPED (skip only if TensorFlow genuinely unavailable — if it was passing before this change with TF installed, it must still pass now).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/models/lstm.py ForecastingCore/tests/test_models.py
git commit -m "fix: compute full metric set for LSTM instead of MAE-only"
```

---

### Task 4: Forward the new metric fields through `Pipeline._flatten()`

**Files:**
- Modify: `ForecastingCore/forecasting_core/pipelines/pipeline.py`
- Test: `ForecastingCore/tests/test_pipeline.py`

**Interfaces:**
- Consumes: per-SKU result dicts from Tasks 2-3, each now containing `{"mae", "rmse", "wape", "bias", "mape", "smape"}`.
- Produces: `Pipeline._flatten(...)` returns a `pd.DataFrame` where **every** row (ML, stat, dl) has non-null `mae, rmse, wape, bias, mape, smape` columns whenever the underlying model produced them (baseline rows keep their existing behavior of spreading whatever `evaluate_baselines` already returns, which is unaffected by this plan).

- [ ] **Step 1: Write the failing test**

Add to `ForecastingCore/tests/test_pipeline.py` (this file already builds a `Pipeline` and inspects `results.metrics_df` per the existing tests around line 147/217/227 — reuse whatever fixture builds a real trained `Pipeline` with at least one statistical model selected, e.g. `arima` or `ets`):

```python
class TestFlattenForwardsFullMetricSet:

    def test_stat_model_rows_have_full_metric_columns(self, <reuse this file's existing trained-pipeline fixture>):
        df = results.metrics_df  # adapt variable name to whatever the fixture returns
        stat_rows = df[df["type"].isin(["stat", "dl"])]
        assert not stat_rows.empty, "fixture must include at least one stat/dl model"
        for col in ["rmse", "wape", "bias", "mape", "smape"]:
            assert col in stat_rows.columns
            assert stat_rows[col].notna().any(), f"{col} is all-null for stat/dl rows"

    def test_ml_model_rows_have_mape_and_smape(self, <reuse this file's existing trained-pipeline fixture with an ML model>):
        df = results.metrics_df
        ml_rows = df[df["type"] == "ml"]
        assert not ml_rows.empty
        for col in ["mape", "smape"]:
            assert col in ml_rows.columns
            assert ml_rows[col].notna().any()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest ForecastingCore/tests/test_pipeline.py -v -k "FullMetricSet"`
Expected: FAIL — `rmse`/`wape`/`bias`/`mape`/`smape` columns are either missing or all-null for stat/dl rows (and `mape`/`smape` missing for ml rows), because `_flatten()` doesn't forward them yet.

- [ ] **Step 3: Implement**

In `ForecastingCore/forecasting_core/pipelines/pipeline.py`, replace `_flatten()` (currently lines 603-621):

```python
    def _flatten(self, results_ml, results_stat, baselines) -> pd.DataFrame:
        rows = []
        for key, res in results_ml.items():
            rows.append({
                "model": res.get("model", key), "type": "ml",
                "sku": res.get("sku", key), "mae": res.get("mae"),
                "rmse": res.get("rmse"), "wape": res.get("wape"),
                "bias": res.get("bias"), "n_folds": res.get("n_folds"),
                "validation": res.get("validation"),
            })
        for model_name, res_dict in results_stat.items():
            model_type = "dl" if model_name == "lstm" else "stat"
            for sku, res in res_dict.items():
                mae_val = res.get("mae") if isinstance(res, dict) else float(res)
                rows.append({"model": model_name, "type": model_type, "sku": sku, "mae": mae_val})
        for sku, blines in baselines.items():
            for bname, bm in blines.items():
                rows.append({"model": bname, "type": "baseline", "sku": sku, **bm})
        return pd.DataFrame(rows)
```

with:

```python
    def _flatten(self, results_ml, results_stat, baselines) -> pd.DataFrame:
        rows = []
        for key, res in results_ml.items():
            rows.append({
                "model": res.get("model", key), "type": "ml",
                "sku": res.get("sku", key), "mae": res.get("mae"),
                "rmse": res.get("rmse"), "wape": res.get("wape"),
                "bias": res.get("bias"), "mape": res.get("mape"),
                "smape": res.get("smape"), "n_folds": res.get("n_folds"),
                "validation": res.get("validation"),
            })
        for model_name, res_dict in results_stat.items():
            model_type = "dl" if model_name == "lstm" else "stat"
            for sku, res in res_dict.items():
                if isinstance(res, dict):
                    rows.append({
                        "model": model_name, "type": model_type, "sku": sku,
                        "mae": res.get("mae"), "rmse": res.get("rmse"),
                        "wape": res.get("wape"), "bias": res.get("bias"),
                        "mape": res.get("mape"), "smape": res.get("smape"),
                    })
                else:
                    rows.append({"model": model_name, "type": model_type, "sku": sku, "mae": float(res)})
        for sku, blines in baselines.items():
            for bname, bm in blines.items():
                rows.append({"model": bname, "type": "baseline", "sku": sku, **bm})
        return pd.DataFrame(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest ForecastingCore/tests/test_pipeline.py -v`
Expected: PASS (new tests plus all pre-existing tests in this file, e.g. the `mae_col = df["mae"].dropna()` test around line 217 must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/pipelines/pipeline.py ForecastingCore/tests/test_pipeline.py
git commit -m "fix: forward mape/smape (ml) and rmse/wape/bias/mape/smape (stat/dl) through _flatten"
```

---

### Task 5: Extend `by_model` aggregation in `ForecastEngine.get_metrics()`

**Files:**
- Modify: `ForecastingCore/forecasting_core/engine.py`
- Test: `ForecastingCore/tests/test_engine.py`

**Interfaces:**
- Consumes: `self._metrics_df` now has `mape`/`smape` columns for all model types (Task 4).
- Produces: `get_metrics()["by_model"][model]` now includes `avg_mae, avg_rmse, avg_wape, avg_bias, avg_mape, avg_smape` (previously only `avg_mae, avg_rmse, avg_wape`).

- [ ] **Step 1: Write the failing test**

Add to `ForecastingCore/tests/test_engine.py`, near the existing `test_get_metrics_after_train` (line 216), reusing the same `_trained_engine(tmp_path)` fixture:

```python
    def test_by_model_includes_all_avg_metrics(self, tmp_path):
        engine = _trained_engine(tmp_path)
        metrics = engine.get_metrics()
        for model_stats in metrics["by_model"].values():
            for key in ["avg_mae", "avg_rmse", "avg_wape", "avg_bias", "avg_mape", "avg_smape"]:
                assert key in model_stats, f"{key} missing from by_model entry: {model_stats}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest ForecastingCore/tests/test_engine.py -v -k "by_model_includes_all_avg_metrics"`
Expected: FAIL — `KeyError`/`AssertionError` on `avg_bias`, `avg_mape`, `avg_smape` (current aggregation only computes `avg_mae`, `avg_rmse`, `avg_wape`).

- [ ] **Step 3: Implement**

In `ForecastingCore/forecasting_core/engine.py`, replace lines 787-790:

```python
        by_model = (df.groupby("model")
                    .agg(avg_mae=("mae", "mean"), avg_rmse=("rmse", "mean"),
                         avg_wape=("wape", "mean"))
                    .round(4).to_dict(orient="index"))
```

with:

```python
        by_model = (df.groupby("model")
                    .agg(avg_mae=("mae", "mean"), avg_rmse=("rmse", "mean"),
                         avg_wape=("wape", "mean"), avg_bias=("bias", "mean"),
                         avg_mape=("mape", "mean"), avg_smape=("smape", "mean"))
                    .round(4).to_dict(orient="index"))
```

Also update the docstring on line 778 to reflect the full key set — replace:

```python
            {"rows": [...], "by_model": {model: {avg_mae, avg_rmse, ...}}, "shap": {...}}
```

with:

```python
            {"rows": [...], "by_model": {model: {avg_mae, avg_rmse, avg_wape, avg_bias, avg_mape, avg_smape}}, "shap": {...}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest ForecastingCore/tests/test_engine.py -v`
Expected: PASS (new test plus all pre-existing engine tests, including `test_get_metrics_after_train`).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/engine.py ForecastingCore/tests/test_engine.py
git commit -m "fix: include bias/mape/smape averages in get_metrics by_model aggregate"
```

---

### Task 6: Update `ForecastingCore` README/docs metric references

**Files:**
- Modify: `ForecastingCore/README.md`
- Modify: `ForecastingCore/docs/engine.txt`
- Modify: `ForecastingCore/docs/api_reference.txt`

**Interfaces:**
- No code interfaces — documentation only, so the README example output isn't misleading about what `get_metrics()` actually returns post-Task-5.

- [ ] **Step 1: Update README.md**

In `ForecastingCore/README.md` around line 280, replace:

```
#   "by_model": {"lightgbm": {"avg_mae": 12.3, "avg_rmse": 15.1, "avg_wape": 0.08}},
```

with:

```
#   "by_model": {"lightgbm": {"avg_mae": 12.3, "avg_rmse": 15.1, "avg_wape": 0.08, "avg_bias": -0.4, "avg_mape": 0.09, "avg_smape": 0.10}},
```

- [ ] **Step 2: Update docs/engine.txt**

Around line 148, replace:

```
        Returns: {rows: [...], by_model: {model: {avg_mae, avg_rmse, avg_wape}}}
```

with:

```
        Returns: {rows: [...], by_model: {model: {avg_mae, avg_rmse, avg_wape, avg_bias, avg_mape, avg_smape}}}
```

- [ ] **Step 3: Update docs/api_reference.txt**

Around line 110, replace:

```
    Returns: {rows: [...], by_model: {model: {avg_mae, avg_rmse, avg_wape}}}
```

with:

```
    Returns: {rows: [...], by_model: {model: {avg_mae, avg_rmse, avg_wape, avg_bias, avg_mape, avg_smape}}}
```

- [ ] **Step 4: Commit**

```bash
git add ForecastingCore/README.md ForecastingCore/docs/engine.txt ForecastingCore/docs/api_reference.txt
git commit -m "docs: reflect full metric set returned by get_metrics().by_model"
```

---

### Task 7: Extend frontend types for the new metric fields

**Files:**
- Modify: `Frontend/src/lib/types.ts`

**Interfaces:**
- Consumes: backend JSON from `GET /sessions/{id}/metrics`, now including `mape`/`smape` per row and `avg_bias`/`avg_mape`/`avg_smape` per model (Tasks 4-5).
- Produces: `MetricRow` and `MetricsResponse` types that match the real backend payload exactly, so Task 8's UI code type-checks against real fields instead of `any`/missing properties.

- [ ] **Step 1: Update `MetricRow` and `MetricsResponse`**

In `Frontend/src/lib/types.ts`, replace lines 210-225:

```typescript
export interface MetricRow {
  model:      string
  type:       string
  sku:        string | null
  mae:        number | null
  rmse:       number | null
  wape:       number | null
  bias:       number | null
  n_folds:    number | null
  validation: string | null
}

export interface MetricsResponse {
  rows:     MetricRow[]
  by_model: Record<string, { avg_mae: number; avg_rmse: number; avg_wape: number }>
}
```

with:

```typescript
export interface MetricRow {
  model:      string
  type:       string
  sku:        string | null
  mae:        number | null
  rmse:       number | null
  wape:       number | null
  bias:       number | null
  mape:       number | null
  smape:      number | null
  n_folds:    number | null
  validation: string | null
}

export interface MetricsResponse {
  rows:     MetricRow[]
  by_model: Record<string, {
    avg_mae:   number
    avg_rmse:  number
    avg_wape:  number
    avg_bias:  number
    avg_mape:  number
    avg_smape: number
  }>
}
```

- [ ] **Step 2: Verify the frontend still type-checks**

Run: `cd Frontend && npx tsc --noEmit`
Expected: No new errors. (This step has no "test" in the pytest sense — TypeScript compilation is the check. Any pre-existing unrelated errors in the repo are out of scope; only confirm no *new* errors reference `MetricRow`/`MetricsResponse`.)

- [ ] **Step 3: Commit**

```bash
git add Frontend/src/lib/types.ts
git commit -m "feat: add mape/smape/bias fields to MetricRow and MetricsResponse types"
```

---

### Task 8: Display MAPE/sMAPE in the Results screen (Step 9)

**Files:**
- Modify: `Frontend/src/app/forecast/page.tsx`

**Interfaces:**
- Consumes: `MetricRow.mape`, `MetricRow.smape`, and `MetricsResponse.by_model[model].avg_mape`/`avg_smape` from Task 7.
- Produces: no new exported interface — this is leaf UI rendering inside `Step9`.

- [ ] **Step 1: Locate the current rendering code**

Read `Frontend/src/app/forecast/page.tsx` lines 1571-1613 (the `by_model` summary cards and the detailed `data.rows` table inside `Step9`) to get the exact current JSX before editing — line numbers may have shifted slightly since the audit; search for the literal strings `by_model` and `avg_mae` within `Step9` to relocate them precisely if so.

- [ ] **Step 2: Add MAPE/sMAPE to the by-model summary cards**

In the `by_model` cards block (around line 1571-1583), extend whatever fields are currently destructured/rendered per model (currently `avg_mae`, `avg_rmse`, `avg_wape`) to also render `avg_mape` and `avg_smape`, formatted as percentages consistent with how `avg_wape` is already formatted in that same block (reuse the existing formatting helper/pattern in this block rather than introducing a new one).

- [ ] **Step 3: Add MAPE/sMAPE columns to the detailed rows table**

In the detailed table block (around line 1596-1613) that currently renders `model`, `type`, `sku`, `mae`, `rmse`, `wape`, `n_folds` columns, add `mape` and `smape` columns using the same 4-decimal formatting already applied to `mae`/`rmse`/`wape` in that block (lines 1578/1606-1608 per the audit — reuse that exact formatting call).

- [ ] **Step 4: Manually verify in the browser**

Run: `cd Frontend && npm run dev`, navigate to `/forecast`, complete a wizard run (or open an existing completed session if one exists in `backend/storage/`), reach Step 9, and visually confirm:
- MAPE and sMAPE columns/cards are visible and show numeric (not `null`/`NaN`/blank) values for at least the ML model rows.
- For stat-model rows (e.g. arima, prophet, ets, croston if selected), confirm RMSE/WAPE/Bias/MAPE/sMAPE now show real numbers instead of being blank — this is the direct user-visible confirmation that Task 2's backend fix closes the loop end-to-end.

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/app/forecast/page.tsx
git commit -m "feat: show MAPE/sMAPE in Results screen for all model types"
```

---

### Task 9: Full regression run and evidence capture

**Files:** none (verification only)

**Interfaces:** none

- [ ] **Step 1: Run the full ForecastingCore test suite**

Run: `pytest ForecastingCore/tests/ -v`
Expected: PASS, 0 failures. Capture the summary line (e.g. `XX passed in Ys`) as evidence that Tasks 1-5 did not regress anything else in the suite (`test_data.py`, `test_features.py`, `test_inventory.py`, `test_validation.py`, `test_training.py`, `test_drift.py`, `test_tuner.py`, etc.).

- [ ] **Step 2: Run the backend offline test suite**

Run: `pytest backend/tests/test_endpoints_offline.py -v`
Expected: PASS — confirms no backend API contract broke from the `MetricRow`-shaped payload change (this test file was identified during investigation as referencing metrics-adjacent endpoints).

- [ ] **Step 3: Record before/after evidence**

Using a small synthetic dataset with at least one SKU classified as `seasonal` and one as `intermittent` (reuse `ForecastingCore/tests/conftest.py` fixtures if they already provide one, otherwise build a minimal CSV inline with pandas in a throwaway script), run a full `ForecastEngine` train with `models=["lightgbm", "arima", "prophet", "ets", "croston"]` selected, call `get_metrics()`, and print the resulting `rows` and `by_model` dicts. Confirm in the printed output that:
- Every stat-model row (`arima`, `prophet`, `ets`, `croston`) now has non-null `rmse`, `wape`, `bias`, `mape`, `smape` — not just `mae`.
- `by_model` entries for those models have non-null `avg_rmse`, `avg_wape`, `avg_bias`, `avg_mape`, `avg_smape`.

This is the concrete "before vs. after" evidence requested for the audit deliverable — paste the printed dict into the final report/commit message or PR description.

- [ ] **Step 4: No commit for this task** — it is verification-only; if Step 3's throwaway script is useful to keep as a regression fixture, that decision belongs to a follow-up task, not this one.

---

## Self-Review Notes

- **Spec coverage:** Every concrete bug found during the audit (MAE-only stat models, missing sMAPE, duplicate `global_wape`, frontend type/UI gaps) maps to a task above (Tasks 1-3 fix the metric math and model wrappers, Tasks 4-5 fix the data plumbing, Tasks 6-8 fix docs/types/UI, Task 9 proves it end-to-end).
- **Out of scope for this plan** (deferred to the next prioritized phase per user's stated priority order): the model-recommendation system fix (hardcoded `MODEL_SERIES_FIT` in `forecast/page.tsx` vs. real `ROUTING_TABLE`), exposing per-SKU classification `reasons` to the UI, full end-to-end multi-dataset testing, and stress testing. These were identified during the audit but intentionally not bundled here to keep this plan independently shippable.
- **Type/signature consistency check:** `evaluate_all()` (Task 1) → consumed identically by `trainer.py` (already spreads `**metrics`, untouched) and by every `models/*.py` wrapper (Tasks 2-3, now keeps the full dict) → consumed by `_flatten()` (Task 4, now reads `mape`/`smape` keys that exist) → consumed by `get_metrics()` (Task 5, now aggregates `bias`/`mape`/`smape` columns that exist) → consumed by `MetricRow`/`MetricsResponse` (Task 7, now typed) → rendered in `Step9` (Task 8). No naming drift across tasks (`mape`/`smape`/`bias` spelled identically end-to-end).

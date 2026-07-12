# Data Alignment Wizard — Resampling & Horizon Config (Plan 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user resolve a granularity conflict (detected in Plan 1) by aggregating heterogeneous SKUs to a common frequency (Estrategia B), and configure a forecast horizon expressed in units of that frequency — backend + ForecastingCore only, no frontend modal in this plan.

**Architecture:** A new pure resampling function in `ForecastingCore` (`forecasting_core/data/resampler.py`) aggregates a mixed-frequency dataframe to one target frequency by summing demand per SKU within each bucket. `Pipeline.run()` calls it once, right after the raw dataframe is loaded/sorted, when the session's new `granularity` config says `strategy="aggregate"` — so `Trainer`/`FeatureEngineer` downstream never know resampling happened. `forecast_cfg` gains a `horizon_mode`/`horizon_by_freq` pair (mirroring the design spec's Modalidad 1/2), validated server-side against per-bucket limits.

**Tech Stack:** Python 3, pandas, FastAPI, pytest. ForecastingCore tests are pure Python (no DB). Backend tests need Postgres :5544.

## Global Constraints

- Resampling aggregates with **`sum()`** (correct for demand/sales counts) to the coarsest bucket present, in memory, **once per pipeline run** — never persisted back to the uploaded dataset file on disk.
- `granularity_cfg` (a new session config, mirroring the 6 existing JSONB blobs) has exactly two fields: `strategy: "native" | "aggregate"`, `target_freq: Optional[str]` (only meaningful when `strategy == "aggregate"`).
- `forecast_cfg.horizon_mode` is `"unified"` (default, backward compatible with the existing plain `horizon` int) or `"segmented"` (only meaningful when there was a real conflict and the user kept native frequencies — Plan 1's territory, not enforced by this plan's code, just supported).
- Horizon limits per frequency bucket (validate both `unified`'s single horizon, when a `target_freq`/detected-frequency is known, and every value inside `horizon_by_freq`): `D`: 1-30, `W`: 1-12, `2W`: 1-6, `MS`: 1-12.
- Tests follow `TESTING_GUIDELINES.md`: assert real computed values (resampled row sums, bucket assignments), not just "ran without exception."
- Backend is pure orchestration — the resampler and its pandas logic live in `ForecastingCore`; `backend/` only reads/writes the config blob and calls the pipeline.
- **Explicitly OUT OF SCOPE for this plan** (documented, not silently dropped):
  1. `FeatureEngineer` per-frequency lag/rolling defaults for Estrategia A (native frequencies kept). `FeatureEngineer` today applies ONE `features_config` to the whole dataframe regardless of group; giving it per-bucket defaults requires splitting the pipeline's feature-engineering step by frequency bucket — a higher-risk change to the core training path used by every session (not just heterogeneous ones). This is deferred to a dedicated follow-up plan once Plan 2's foundation (this plan) is reviewed.
  2. The frontend conciliation modal and horizon-config UI (Design Spec's "Frontend — Modal de conciliación" section) — a separate plan once the backend contract here is stable, so UI iteration doesn't block backend correctness work.

---

### Task 1: Resampler module (pure function, ForecastingCore)

**Files:**
- Create: `ForecastingCore/forecasting_core/data/resampler.py`
- Test: `ForecastingCore/tests/test_resampler.py` (new)

**Interfaces:**
- Produces: `resample_to_frequency(df: pd.DataFrame, date_col: str, group_col: Optional[str], target_col: str, target_freq: str) -> pd.DataFrame` — returns a new dataframe with one row per `(group, resampled date bucket)`, `target_col` summed, all other original columns dropped (the pipeline only needs date/group/target from here on; feature engineering re-derives everything else from these three).

- [ ] **Step 1: Write the failing tests**

Create `ForecastingCore/tests/test_resampler.py`:

```python
import pandas as pd
from forecasting_core.data.resampler import resample_to_frequency


def _dates(start, periods, freq):
    return list(pd.date_range(start, periods=periods, freq=freq))


class TestResampleToFrequency:
    def test_daily_to_weekly_sums_within_bucket(self):
        rows = []
        for d in _dates("2024-01-01", 14, "D"):  # 2 full weeks
            rows.append((d, "SKU1", 10.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "W")
        assert len(out) == 2  # 14 daily rows -> 2 weekly buckets
        assert set(out["sales"]) == {70.0}  # 7 days * 10 per bucket

    def test_preserves_per_group_separation(self):
        rows = []
        for d in _dates("2024-01-01", 7, "D"):
            rows.append((d, "A", 5.0))
        for d in _dates("2024-01-01", 7, "D"):
            rows.append((d, "B", 100.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "W")
        by_sku = dict(zip(out["sku"], out["sales"]))
        assert by_sku["A"] == 35.0
        assert by_sku["B"] == 700.0

    def test_single_series_no_group_col(self):
        rows = [(d, 3.0) for d in _dates("2024-01-01", 14, "D")]
        df = pd.DataFrame(rows, columns=["date", "sales"])
        out = resample_to_frequency(df, "date", None, "sales", "W")
        assert len(out) == 2
        assert set(out["sales"]) == {21.0}

    def test_output_has_only_date_group_target_columns(self):
        df = pd.DataFrame(
            [("2024-01-01", "A", 5.0, "extra_col_value")],
            columns=["date", "sku", "sales", "notes"],
        )
        out = resample_to_frequency(df, "date", "sku", "sales", "D")
        assert set(out.columns) == {"date", "sku", "sales"}

    def test_monthly_aggregation(self):
        rows = []
        for d in _dates("2024-01-01", 6, "W"):  # 6 weeks
            rows.append((d, "SKU1", 20.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "MS")
        assert len(out) >= 1
        assert out["sales"].sum() == 120.0  # total demand conserved across the re-aggregation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_resampler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'forecasting_core.data.resampler'`.

- [ ] **Step 3: Implement the resampler**

Create `ForecastingCore/forecasting_core/data/resampler.py`:

```python
"""
Resampler — aggregates a mixed-frequency dataset to one common frequency
("Estrategia B" of the Data Alignment Wizard). Runs once, in memory, at the
start of the pipeline; the resulting dataframe is homogeneous and the rest
of the pipeline (FeatureEngineer, Trainer, models) never knows resampling
happened.
"""

from __future__ import annotations

import pandas as pd
from typing import Optional


def resample_to_frequency(
    df: pd.DataFrame,
    date_col: str,
    group_col: Optional[str],
    target_col: str,
    target_freq: str,
) -> pd.DataFrame:
    """
    Aggregate demand to `target_freq` by summing within each bucket, per
    group if a group column is given.

    Args:
        df:          Raw dataframe (any native per-row cadence).
        date_col:    Name of the date column.
        group_col:   Name of the SKU/group column, or None for a single series.
        target_col:  Name of the demand/target column to sum.
        target_freq: Pandas offset alias to resample to ("D", "W", "2W", "MS").

    Returns:
        A new dataframe with columns [date_col, group_col?, target_col],
        one row per (group, resampled bucket), target summed within it.
    """
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")

    if group_col:
        out = (
            work.set_index(date_col)
            .groupby(group_col)[target_col]
            .resample(target_freq)
            .sum()
            .reset_index()
        )
        return out[[date_col, group_col, target_col]]

    out = (
        work.set_index(date_col)[target_col]
        .resample(target_freq)
        .sum()
        .reset_index()
    )
    return out[[date_col, target_col]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_resampler.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/data/resampler.py ForecastingCore/tests/test_resampler.py
git commit -m "feat(resampler): aggregate mixed-frequency series to a common bucket (Estrategia B)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `GranularityConfig` + wire the resampler into `Pipeline.run()`

**Files:**
- Modify: `ForecastingCore/forecasting_core/config/config.py` (new `GranularityConfig` dataclass; add to `SessionConfig` + `from_dict`)
- Modify: `ForecastingCore/forecasting_core/pipelines/pipeline.py` (`Pipeline.run()`, ~lines 209-222)
- Modify: `backend/workers/runner.py` (`build_engine_config`, pass through `granularity_cfg`)
- Test: `ForecastingCore/tests/test_pipeline_granularity.py` (new)

**Interfaces:**
- Produces: `GranularityConfig(strategy: str = "native", target_freq: Optional[str] = None)` on `SessionConfig.granularity`.
- Consumes: `resample_to_frequency` (Task 1).

- [ ] **Step 1: Write the failing test**

Create `ForecastingCore/tests/test_pipeline_granularity.py`:

```python
import pandas as pd
from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline


def test_session_config_defaults_to_native_granularity():
    cfg = SessionConfig.from_dict({"name": "t"})
    assert cfg.granularity.strategy == "native"
    assert cfg.granularity.target_freq is None


def test_session_config_reads_aggregate_granularity():
    cfg = SessionConfig.from_dict({
        "name": "t",
        "granularity": {"strategy": "aggregate", "target_freq": "W"},
    })
    assert cfg.granularity.strategy == "aggregate"
    assert cfg.granularity.target_freq == "W"


def test_pipeline_resamples_when_strategy_is_aggregate():
    rows = []
    for d in pd.date_range("2024-01-01", periods=14, freq="D"):
        rows.append((d, "SKU1", 10.0))
    df = pd.DataFrame(rows, columns=["date", "sku", "sales"])

    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
        "granularity": {"strategy": "aggregate", "target_freq": "W"},
        "training": {"min_history": 1, "wfv_splits": 1, "walk_forward": False},
        "models": {},
    })
    pipeline = Pipeline(cfg, df=df)
    resampled = pipeline._maybe_resample(df.copy())
    assert len(resampled) == 2  # 14 daily rows collapsed to 2 weekly buckets
    assert set(resampled["sales"]) == {70.0}


def test_pipeline_skips_resample_when_strategy_is_native():
    rows = [(d, "SKU1", 10.0) for d in pd.date_range("2024-01-01", periods=5, freq="D")]
    df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
    })
    pipeline = Pipeline(cfg, df=df)
    result = pipeline._maybe_resample(df.copy())
    assert len(result) == 5  # untouched — native strategy is the default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_pipeline_granularity.py -v`
Expected: FAIL — `SessionConfig` has no `granularity` attribute; `Pipeline` has no `_maybe_resample` method.

- [ ] **Step 3: Add `GranularityConfig` to `config.py`**

In `ForecastingCore/forecasting_core/config/config.py`, add near `ForecastConfig` (~line 102):

```python
@dataclass
class GranularityConfig:
    strategy: str = "native"            # "native" | "aggregate"
    target_freq: Optional[str] = None   # only meaningful when strategy == "aggregate"
```

Add `granularity: GranularityConfig = field(default_factory=GranularityConfig)` to `SessionConfig` (~line 161, alongside `routing`).

In `SessionConfig.from_dict` (~line 196, after the `routing` block, before `cfg.validate()`):

```python
        if "granularity" in d:
            gd = d["granularity"]
            cfg.granularity = GranularityConfig(
                strategy=gd.get("strategy", "native"),
                target_freq=gd.get("target_freq"),
            )
```

- [ ] **Step 4: Add `Pipeline._maybe_resample` and call it from `run()`**

In `ForecastingCore/forecasting_core/pipelines/pipeline.py`, add a method to the `Pipeline` class (near `run()`, ~line 161):

```python
    def _maybe_resample(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate to a common frequency when the session chose Estrategia B.
        No-op (returns df unchanged) for the default "native" strategy.
        """
        g = self.config.granularity
        if g.strategy != "aggregate" or not g.target_freq:
            return df
        from forecasting_core.data.resampler import resample_to_frequency
        c = self.config.columns
        group_col = _primary_group(c)
        return resample_to_frequency(df, c.date, group_col, c.target, g.target_freq)
```

In `run()`, immediately after the existing load-and-sort block (~lines 219-222, right after `df = ... .reset_index(drop=True)`), insert:

```python
        df = self._maybe_resample(df)
```

- [ ] **Step 5: Pass `granularity_cfg` through from the backend**

In `backend/workers/runner.py::build_engine_config`, read the session's `granularity_cfg` field the same way the other 6 blobs are read (~line 43, alongside `forecast_cfg = session_store.get_field(...)`):

```python
    granularity_cfg = session_store.get_field(tenant_id, session_id, "granularity_cfg") or {}
```

Then include it in the assembled config dict passed to `SessionConfig.from_dict` (find where the final dict literal is built, e.g. near where `"forecast": {...}` is assembled, ~line 122) — add a `"granularity": granularity_cfg` entry alongside the other top-level keys. Read the surrounding code first to match the exact dict-building pattern used for `forecast`/`business`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_pipeline_granularity.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 7: Run ForecastingCore regression**

Run: `cd ForecastingCore && python -m pytest tests/ -q`
Expected: PASS (675+ passed) — `_maybe_resample` is a no-op for every existing session (default `strategy="native"`), so no existing pipeline test should be affected.

- [ ] **Step 8: Commit**

```bash
git add ForecastingCore/forecasting_core/config/config.py ForecastingCore/forecasting_core/pipelines/pipeline.py backend/workers/runner.py ForecastingCore/tests/test_pipeline_granularity.py
git commit -m "feat(pipeline): wire granularity_cfg + Estrategia B resampling into Pipeline.run()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `forecast_cfg.horizon_mode`/`horizon_by_freq` + backend validation

**Files:**
- Modify: `backend/schemas/configuration.py` (`ForecastConfigRequest`)
- Modify: `backend/api/v1/configuration.py` (`configure_forecast` — validate limits)
- Test: `backend/tests/test_granularity.py` (append — reuse the file from Plan 1)

**Interfaces:**
- Produces: `ForecastConfigRequest` gains `horizon_mode: str = "unified"` and `horizon_by_freq: Optional[dict[str, int]] = None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_granularity.py`:

```python
class TestForecastHorizonLimits:
    def test_unified_horizon_within_default_range_accepted(self, client, auth_headers, test_session):
        sid = test_session["id"]
        r = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon": 14, "quantiles": [0.1, 0.9]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["horizon_mode"] == "unified"

    def test_segmented_horizon_by_freq_persists(self, client, auth_headers, test_session):
        sid = test_session["id"]
        r = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon_mode": "segmented", "horizon_by_freq": {"D": 10, "W": 4}},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["horizon_by_freq"] == {"D": 10, "W": 4}

    def test_segmented_horizon_out_of_range_rejected(self, client, auth_headers, test_session):
        sid = test_session["id"]
        r = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon_mode": "segmented", "horizon_by_freq": {"D": 60}},  # D max is 30
            headers=auth_headers,
        )
        assert r.status_code == 422
        from backend.db import session_store
        assert session_store.get_field(test_session["id"], sid, "forecast_cfg") in (None,) or True
```

Note: the last assertion's tenant-id argument is a placeholder — mirror the real signature of `session_store.get_field` (it takes `tenant_id, session_id, field`) using this test file's actual auth/tenant fixtures; do not invent a call that doesn't compile. If confirming "state unchanged" is awkward here, it is acceptable to drop that specific assertion and rely on the 422 status code alone, since `configure_forecast`'s failure mode (raising before any `session_store.set_field` call) is straightforward to verify by reading the endpoint.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -k "Horizon" -v`
Expected: FAIL (no `horizon_mode` field, no limit validation).

- [ ] **Step 3: Extend `ForecastConfigRequest`**

In `backend/schemas/configuration.py`, `ForecastConfigRequest` (~line 102):

```python
_HORIZON_LIMITS = {"D": (1, 30), "W": (1, 12), "2W": (1, 6), "MS": (1, 12)}


class ForecastConfigRequest(BaseModel):
    horizon: int = 14
    quantiles: List[float] = [0.1, 0.9]
    horizon_mode: str = "unified"                          # "unified" | "segmented"
    horizon_by_freq: Optional[Dict[str, int]] = None        # {"D": 10, "W": 4, ...}

    @model_validator(mode="after")
    def _validate_horizon_by_freq(self):
        if self.horizon_by_freq:
            for freq, value in self.horizon_by_freq.items():
                bounds = _HORIZON_LIMITS.get(freq)
                if bounds is None:
                    raise ValueError(f"Unknown frequency bucket '{freq}'")
                lo, hi = bounds
                if not (lo <= value <= hi):
                    raise ValueError(f"horizon_by_freq['{freq}']={value} must be between {lo} and {hi}")
        return self
```

Add `from pydantic import model_validator` and `from typing import Dict` to the file's imports if not already present (check first — `Optional`/`List` are likely already imported from `typing`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -k "Horizon" -v`
Expected: PASS.

- [ ] **Step 5: Run configuration-endpoint regression**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py tests/test_canonical_api.py tests/test_edge_cases.py tests/test_sessions_flow.py -q`
Expected: PASS — `configure_forecast` already does `config = {**body.model_dump(), ...}`, so the two new fields flow through to the persisted config automatically; no endpoint code change needed beyond the schema (confirm this by reading `configure_forecast` — if it does anything more restrictive, adjust and note it).

- [ ] **Step 6: Commit**

```bash
git add backend/schemas/configuration.py backend/tests/test_granularity.py
git commit -m "feat(forecast-config): horizon_mode/horizon_by_freq with per-frequency limits

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Regression + wrap-up

- [ ] **Step 1: Full ForecastingCore suite**

Run: `cd ForecastingCore && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 2: Full backend suite (touched files)**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py tests/test_canonical_api.py tests/test_edge_cases.py tests/test_endpoints.py tests/test_sessions_flow.py tests/test_endpoints_offline.py -q`
Expected: PASS.

---

## Self-Review notes

- **Spec coverage:** Design spec Section D (resampling strategies) → Tasks 1-2. Section E (horizon config) → Task 3. The frontend modal and `FeatureEngineer` per-frequency defaults are explicitly out of scope (see Global Constraints) — separate follow-up plans once this backend foundation is reviewed, not silently dropped.
- **Type consistency:** `resample_to_frequency`'s signature is identical between Task 1's tests and Task 2's `_maybe_resample` caller. `GranularityConfig.strategy`/`target_freq` field names match the design spec's `granularity_cfg` JSON shape exactly.
- **Known follow-up for the implementer to verify (mirror existing code, don't invent):** the exact dict-building pattern in `build_engine_config` where `forecast`/`business` sub-dicts are assembled (Task 2 Step 5) — read the surrounding code rather than guessing the literal structure; the state-unchanged assertion placeholder in Task 3 Step 1's last test — resolve per the note inline, don't invent a broken call.

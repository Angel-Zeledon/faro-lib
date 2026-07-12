# Data Alignment Wizard — Granularity Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect per-SKU temporal granularity (daily/weekly/biweekly/monthly), block the wizard with a structured conflict payload when a dataset mixes frequencies, and re-validate after the user confirms columns — the backend foundation the Data Alignment Wizard's frontend modal will consume.

**Architecture:** A new pure function `DataProfiler.detect_granularity(df, date_col, group_col)` in `ForecastingCore` classifies each SKU's median date-gap into a frequency bucket and reports conflicts. `GET /sessions/{id}/inspect` calls it with the profiler's auto-detected date/group columns and attaches the result as `inspection["granularity"]`. `POST /sessions/{id}/configure/columns` re-runs the same detection with the user's *confirmed* columns (reloading the dataset) and returns the fresh result alongside the saved config, so a correction in step 2 that changes the outcome is caught before training. No frontend, no resampling, no horizon config in this plan — those are Plan 2, built on top of this payload contract.

**Tech Stack:** Python 3, pandas, FastAPI, pytest. Backend tests need local Postgres on :5544 (`backend/.venv/Scripts/python.exe -m pytest tests/... -q` from `backend/`). ForecastingCore tests are pure Python, no DB (`ForecastingCore/.venv` or the shared venv, `python -m pytest tests/... -q` from `ForecastingCore/`).

## Global Constraints

- `detect_granularity` classifies each SKU by the median gap between its sorted, deduplicated dates: `1 day → "D"`, `6-8 days → "W"`, `13-15 days → "2W"`, `28-35 days → "MS"`, anything else or fewer than 3 dates → excluded from the conflict determination entirely (not a fifth bucket — just absent from `detected`/`skus_by_frequency`).
- `detected` is always sorted **finest → coarsest**: `["D", "W", "2W", "MS"]` order, filtered to only the buckets actually present. Never re-sort or re-derive this order downstream — it is the single source of truth for "which is coarser."
- `status` is `"homogeneous"` when 0 or 1 bucket is present, `"conflict"` when 2+, `"unknown"` when there is no usable date column.
- A dataset with no group/SKU column (single series) can never conflict with itself — always `"homogeneous"`.
- `suggested_target` is the **coarsest** bucket present (last element of `detected`), or `None` if `detected` is empty.
- `skus_by_frequency` lists the **full** SKU list per bucket (not just counts) — per product decision.
- Re-validation in `configure_columns` is a **safety net, not a hard requirement**: if the dataset file can't be reloaded for any reason, log a warning and omit `granularity` from the response rather than failing the column-confirmation request.
- Tests follow `TESTING_GUIDELINES.md`: assert the actual computed values (bucket assignments, `detected` order, `skus_by_frequency` contents), not just that an endpoint returned 200. New/changed mutating-endpoint behavior gets a viewer-denied / analyst-allowed pair where applicable (`configure_columns` already requires `require_analyst_or_above` — no new permission surface is introduced, but the existing pair must still pass).
- Backend has NO pandas/ML business logic — `detect_granularity` and its helper belong in `ForecastingCore`; `backend/api/v1/configuration.py` only calls it and shapes the HTTP response.

---

### Task 1: `DataProfiler.detect_granularity` (pure function, ForecastingCore)

**Files:**
- Modify: `ForecastingCore/forecasting_core/data/profiler.py` (add `_FREQ_BUCKETS` class constant and two methods near `_detect_freq`, ~line 703)
- Test: `ForecastingCore/tests/test_granularity.py` (new file)

**Interfaces:**
- Produces: `DataProfiler.detect_granularity(self, df: pd.DataFrame, date_col: Optional[str], group_col: Optional[str]) -> dict` returning `{"status": "homogeneous"|"conflict"|"unknown", "detected": list[str], "skus_by_frequency": dict[str, list[str]], "suggested_target": Optional[str]}`.
- Produces (internal helper): `DataProfiler._classify_sku_frequency(self, dates: pd.Series) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

Create `ForecastingCore/tests/test_granularity.py`:

```python
import pandas as pd
import pytest
from forecasting_core.data.profiler import DataProfiler


def _dates(start: str, periods: int, freq: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=periods, freq=freq)]


def _df(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "sku", "sales"])


class TestDetectGranularity:
    def test_homogeneous_daily(self):
        rows = []
        for sku in ("A", "B"):
            for d in _dates("2024-01-01", 10, "D"):
                rows.append((d, sku, 5.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]
        assert set(result["skus_by_frequency"]["D"]) == {"A", "B"}
        assert result["suggested_target"] == "D"

    def test_conflict_daily_and_weekly_sorted_fine_to_coarse(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "DAILY_SKU", 5.0))
        for d in _dates("2024-01-01", 6, "W"):
            rows.append((d, "WEEKLY_SKU", 20.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "conflict"
        assert result["detected"] == ["D", "W"]
        assert result["skus_by_frequency"]["D"] == ["DAILY_SKU"]
        assert result["skus_by_frequency"]["W"] == ["WEEKLY_SKU"]
        assert result["suggested_target"] == "W"  # coarsest present

    def test_conflict_three_frequencies_target_is_coarsest(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "D_SKU", 5.0))
        for d in _dates("2024-01-01", 6, "W"):
            rows.append((d, "W_SKU", 20.0))
        for d in _dates("2024-01-01", 4, "MS"):
            rows.append((d, "MS_SKU", 90.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "conflict"
        assert result["detected"] == ["D", "W", "MS"]
        assert result["suggested_target"] == "MS"

    def test_irregular_sku_excluded_from_conflict(self):
        rows = []
        for d in _dates("2024-01-01", 10, "D"):
            rows.append((d, "REGULAR", 5.0))
        # Only 2 erratic points — insufficient history, must not create a false conflict.
        rows.append(("2024-01-01", "SHORT", 1.0))
        rows.append(("2024-03-15", "SHORT", 1.0))
        result = DataProfiler().detect_granularity(_df(rows), "date", "sku")
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]
        assert "SHORT" not in result["skus_by_frequency"].get("D", [])

    def test_single_series_no_group_column_is_always_homogeneous(self):
        rows = [(d, "__all__", 5.0) for d in _dates("2024-01-01", 10, "D")]
        result = DataProfiler().detect_granularity(_df(rows), "date", None)
        assert result["status"] == "homogeneous"
        assert result["detected"] == ["D"]

    def test_missing_date_column_returns_unknown(self):
        df = _df([("2024-01-01", "A", 5.0)])
        result = DataProfiler().detect_granularity(df, None, "sku")
        assert result["status"] == "unknown"
        assert result["detected"] == []
        assert result["suggested_target"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_granularity.py -v`
Expected: FAIL with `AttributeError: 'DataProfiler' object has no attribute 'detect_granularity'`.

- [ ] **Step 3: Implement `_classify_sku_frequency` and `detect_granularity`**

In `ForecastingCore/forecasting_core/data/profiler.py`, add near the existing `_detect_freq` method (after it, ~line 720):

```python
    # Canonical frequency bucket order, finest to coarsest. Every consumer of
    # detect_granularity's "detected" list relies on this exact order to know
    # which frequency is coarser without re-deriving it.
    _FREQ_BUCKETS = ["D", "W", "2W", "MS"]

    def _classify_sku_frequency(self, dates: pd.Series) -> Optional[str]:
        """
        Classify one SKU's date series into a frequency bucket by the median
        gap between consecutive (sorted, deduplicated) dates. Returns None for
        insufficient history or an irregular cadence — such SKUs are excluded
        from the granularity conflict determination entirely, not treated as
        a fifth bucket.
        """
        d = pd.to_datetime(dates, errors="coerce").dropna().sort_values().drop_duplicates()
        if len(d) < 3:
            return None
        median_gap = d.diff().dropna().median()
        days = median_gap.days
        if days == 1:
            return "D"
        if 6 <= days <= 8:
            return "W"
        if 13 <= days <= 15:
            return "2W"
        if 28 <= days <= 35:
            return "MS"
        return None

    def detect_granularity(
        self, df: pd.DataFrame, date_col: Optional[str], group_col: Optional[str],
    ) -> dict:
        """
        Detect per-SKU temporal granularity and flag heterogeneous conflicts.

        Returns:
            {
              "status":             "homogeneous" | "conflict" | "unknown",
              "detected":           [...],  # sorted finest→coarsest, only buckets present
              "skus_by_frequency":  {bucket: [sku, ...]},  # full SKU list per bucket
              "suggested_target":   "..." | None,           # coarsest bucket present
            }
        """
        empty = {"status": "unknown", "detected": [], "skus_by_frequency": {}, "suggested_target": None}
        if not date_col or date_col not in df.columns:
            return empty

        if not group_col or group_col not in df.columns:
            # Single series — it can never conflict with itself.
            bucket = self._classify_sku_frequency(df[date_col])
            detected = [bucket] if bucket else []
            return {
                "status": "homogeneous",
                "detected": detected,
                "skus_by_frequency": {bucket: ["__all__"]} if bucket else {},
                "suggested_target": bucket,
            }

        skus_by_frequency: dict = {}
        for sku, g in df.groupby(group_col):
            bucket = self._classify_sku_frequency(g[date_col])
            if bucket is None:
                continue  # irregular/insufficient — excluded from the conflict determination
            skus_by_frequency.setdefault(bucket, []).append(str(sku))

        detected = [b for b in self._FREQ_BUCKETS if b in skus_by_frequency]
        status = "homogeneous" if len(detected) <= 1 else "conflict"
        suggested_target = detected[-1] if detected else None

        return {
            "status": status,
            "detected": detected,
            "skus_by_frequency": skus_by_frequency,
            "suggested_target": suggested_target,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_granularity.py -v`
Expected: PASS, all 6 tests.

- [ ] **Step 5: Run the full ForecastingCore profiler test suite for regressions**

Run: `cd ForecastingCore && python -m pytest tests/test_data.py tests/test_profiler_canonical.py tests/test_profiler_single_sku.py -q`
Expected: PASS — `detect_granularity`/`_classify_sku_frequency` are new additions with no changes to `profile()`/`_detect_freq`/existing methods, so nothing here should break.

- [ ] **Step 6: Commit**

```bash
git add ForecastingCore/forecasting_core/data/profiler.py ForecastingCore/tests/test_granularity.py
git commit -m "feat(profiler): per-SKU granularity detection with conflict payload

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Wire `granularity` into `GET /sessions/{id}/inspect`

**Files:**
- Modify: `backend/api/v1/configuration.py` (`inspect_dataset`, ~lines 100-120)
- Test: `backend/tests/test_granularity.py` (new file)

**Interfaces:**
- Consumes: `DataProfiler.detect_granularity` (Task 1).
- Produces: `inspection["granularity"]` — the same dict shape from Task 1, present in the `GET /inspect` JSON response for every session going forward (both fresh and cached inspections computed after this change).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_granularity.py`:

```python
"""
Integration tests for granularity detection wired into the inspect/columns
wizard endpoints (Data Alignment Wizard — detection phase).
"""
import io


def _csv_bytes(rows: list[tuple[str, str, float]]) -> bytes:
    buf = io.StringIO()
    buf.write("date,sku,sales\n")
    for d, sku, sales in rows:
        buf.write(f"{d},{sku},{sales}\n")
    return buf.getvalue().encode("utf-8")


def _daily_and_weekly_rows() -> list[tuple[str, str, float]]:
    import pandas as pd
    rows = []
    for d in pd.date_range("2024-01-01", periods=10, freq="D"):
        rows.append((d.strftime("%Y-%m-%d"), "DAILY_SKU", 5.0))
    for d in pd.date_range("2024-01-01", periods=6, freq="W"):
        rows.append((d.strftime("%Y-%m-%d"), "WEEKLY_SKU", 20.0))
    return rows


class TestInspectGranularity:
    def test_inspect_reports_conflict_for_mixed_frequencies(self, client, auth_headers, test_session):
        sid = test_session["id"]
        csv_bytes = _csv_bytes(_daily_and_weekly_rows())
        up = client.post(
            "/api/v1/datasets",
            files={"file": ("mixed_freq.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert up.status_code == 201, up.text
        dataset_id = up.json()["data"]["id"]

        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id}, headers=auth_headers)
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "conflict"
        assert granularity["detected"] == ["D", "W"]
        assert "DAILY_SKU" in granularity["skus_by_frequency"]["D"]
        assert "WEEKLY_SKU" in granularity["skus_by_frequency"]["W"]
        assert granularity["suggested_target"] == "W"

    def test_inspect_reports_homogeneous_for_uniform_frequency(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "homogeneous"
```

`uploaded_dataset` (from `conftest.py`) is the existing 5-SKU × 60-day synthetic CSV, generated at a uniform daily cadence — it exercises the homogeneous path without needing a new fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -v`
Expected: FAIL with `KeyError: 'granularity'` (the response has no such key yet).

- [ ] **Step 3: Wire the call into `inspect_dataset`**

In `backend/api/v1/configuration.py`, inside `inspect_dataset` (the `try:` block that builds `profile`, `col_options`, etc., ~lines 101-119), after `canonical_suggestions = profiler.get_canonical_mapping(engine._df)` add:

```python
        recommended = (profile or {}).get("recommended", {})
        granularity = profiler.detect_granularity(
            engine._df, recommended.get("date"), recommended.get("group"),
        )
```

And add `"granularity": granularity,` to the `inspection = {...}` dict literal (alongside `"profile"`, `"column_options"`, etc.).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Run the existing inspect-endpoint test suites for regressions**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_canonical_api.py tests/test_sessions_flow.py tests/test_endpoints_offline.py -q`
Expected: PASS — the new field is additive to the response dict; no existing test asserts an exact/closed key set for `inspection` (verify this holds; if one does, add `granularity` to its expected-keys list rather than treating it as a real regression).

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/configuration.py backend/tests/test_granularity.py
git commit -m "feat(inspect): surface per-SKU granularity conflicts in GET /inspect

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Re-validate granularity in `POST /configure/columns`

**Files:**
- Modify: `backend/api/v1/configuration.py` (new module-level helper + call site inside `configure_columns`, ~line 158-230)
- Test: `backend/tests/test_granularity.py`

**Interfaces:**
- Consumes: `DataProfiler.detect_granularity` (Task 1).
- Produces: `_run_granularity_detection(tenant_id, dataset_id, date_col, group_col) -> Optional[dict]` — a module-level helper in `configuration.py`, reusable if a later plan needs the same reload-and-detect pattern.
- Produces: `configure_columns`'s JSON response gains a `granularity` key (same shape as Task 1) whenever detection succeeds; omitted (not `null`) if the dataset file can't be reloaded.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_granularity.py`:

```python
    def test_configure_columns_revalidates_with_confirmed_columns(
        self, client, auth_headers, test_session,
    ):
        """
        The auto-detected group column can differ from what the user confirms.
        This dataset has an extra numeric column ("region") that the profiler
        might not pick as the SKU/group column; confirming "sku" explicitly as
        the group column must make the re-validation see the true per-SKU
        frequency split, not whatever the profiler guessed at /inspect time.
        """
        sid = test_session["id"]
        csv_bytes = _csv_bytes(_daily_and_weekly_rows())
        up = client.post(
            "/api/v1/datasets",
            files={"file": ("mixed_freq2.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        dataset_id = up.json()["data"]["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id}, headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "conflict"
        assert granularity["detected"] == ["D", "W"]

    def test_configure_columns_viewer_denied_state_unchanged(
        self, client, auth_headers, viewer_headers, test_session, uploaded_dataset,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        vr = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=viewer_headers,
        )
        assert vr.status_code == 403
        from backend.db import session_store
        assert session_store.get_field(auth_headers and None, sid, "columns_cfg") in (None,)
```

Note: the last assertion's tenant-id argument needs the real tenant id, not `auth_headers and None` — when implementing, read `test_session`'s fixture in `conftest.py` to find how to obtain `registered_user["tenant"]["id"]` (or equivalent) in this test file, and use that instead of a placeholder. Mirror whatever pattern the neighboring viewer-permission tests in `test_edge_cases.py::test_viewer_cannot_configure_columns` already use for this exact assertion — do not invent a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -k revalidates -v`
Expected: FAIL with `KeyError: 'granularity'`.

- [ ] **Step 3: Add the reload-and-detect helper**

In `backend/api/v1/configuration.py`, add a module-level function after `_now()` (~line 51):

```python
def _run_granularity_detection(
    tenant_id: str, dataset_id: str, date_col: Optional[str], group_col: Optional[str],
) -> Optional[dict]:
    """
    Reload the dataset file and re-classify per-SKU granularity using the
    user's CONFIRMED columns. Used by configure_columns as a safety net over
    the profiler's auto-detection at /inspect time — if the user corrects the
    date/SKU mapping, a conflict that wasn't visible before (or one that was
    a false positive) must be caught before training. Non-fatal: returns None
    (and logs) if the file can't be reloaded, since re-validation is a safety
    net, not a hard requirement to save the column configuration.
    """
    import os
    ds_meta = get_dataset(tenant_id, dataset_id)
    if not ds_meta or not os.path.exists(ds_meta["file_path"]):
        return None
    try:
        from forecasting_core.engine import ForecastEngine
        from forecasting_core.data.profiler import DataProfiler
        engine = ForecastEngine()
        engine.load_data(ds_meta["file_path"])
        return DataProfiler().detect_granularity(engine._df, date_col, group_col)
    except Exception as e:
        log.warning("granularity re-validation failed dataset=%s: %s", dataset_id, e)
        return None
```

- [ ] **Step 4: Call it from `configure_columns` and attach to the response**

In `backend/api/v1/configuration.py`, `configure_columns` builds `config` differently for the canonical vs. legacy path (~lines 189-227) and then does `session_store.set_field(user.tenant_id, session_id, "columns_cfg", config)` (~line 229). Immediately after that `set_field` call, before the function's final `return`, add:

```python
    confirmed_date  = config.get("date_column") or (config.get("canonical_mapping") or {}).get("date")
    confirmed_group = config.get("sku_column") or (config.get("canonical_mapping") or {}).get("sku")
    granularity = _run_granularity_detection(user.tenant_id, s["dataset_id"], confirmed_date, confirmed_group)

    response = dict(config)
    if granularity is not None:
        response["granularity"] = granularity
```

Then change whatever the function currently returns (find the existing `return ok(config)` — or equivalent — at the end of `configure_columns`) to `return ok(response)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py -v`
Expected: PASS, all 4 tests in this file (2 from Task 2, 2 new here).

- [ ] **Step 6: Run the full configure_columns-related test suites for regressions**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_canonical_api.py tests/test_edge_cases.py tests/test_endpoints.py tests/test_sessions_flow.py tests/test_endpoints_offline.py -q`
Expected: PASS — the response gained one additional key (`granularity`); if any existing test asserts an exact response shape (unlikely, but check), reconcile by adding `granularity` to its expectations rather than removing this feature's field.

- [ ] **Step 7: Commit**

```bash
git add backend/api/v1/configuration.py backend/tests/test_granularity.py
git commit -m "feat(configure-columns): re-validate granularity with user-confirmed columns

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Regression + wrap-up

- [ ] **Step 1: Full ForecastingCore suite**

Run: `cd ForecastingCore && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 2: Full backend suite (or at minimum every file touched/adjacent)**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_granularity.py tests/test_canonical_api.py tests/test_edge_cases.py tests/test_endpoints.py tests/test_sessions_flow.py tests/test_endpoints_offline.py -q`
Expected: PASS.

- [ ] **Step 3: Manual/API sanity check (optional but recommended)**

Via an isolated backend instance against the test DB (same pattern as prior sessions in this project): sign up, upload a CSV mixing daily and weekly SKUs, call `GET /inspect`, confirm `granularity.status == "conflict"` in the raw JSON response, then call `POST /configure/columns` and confirm the response also carries a fresh `granularity` block.

---

## Self-Review notes

- **Spec coverage:** Design spec Sections A (classification) → Task 1. Section B (payload contract) → Task 1 return shape + Task 2 wiring. Section C (re-validation on column change) → Task 3. Sections D (resampling) and E (horizon config) are explicitly out of scope for this plan — they are Plan 2, to be written and brainstormed for UI details after this backend foundation lands and is reviewed.
- **Type consistency:** `detect_granularity`'s return shape (`status`/`detected`/`skus_by_frequency`/`suggested_target`) is identical across Task 1's tests, Task 2's endpoint wiring, and Task 3's re-validation — no field renamed or reshaped between tasks.
- **Ambiguity resolved:** `detected` order (finest→coarsest, `_FREQ_BUCKETS` constant) and `suggested_target` (coarsest present) are pinned in Global Constraints and enforced by every task's tests, including the 3-frequency case in Task 1 Step 1.
- **Known follow-up for the implementer to verify (mirror existing code, don't invent):** the exact tenant-id-lookup pattern for the viewer-permission "state unchanged" assertion in Task 3's `test_configure_columns_viewer_denied_state_unchanged` — mirror `test_edge_cases.py::test_viewer_cannot_configure_columns`, don't guess a new pattern.

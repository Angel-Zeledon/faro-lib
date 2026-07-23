# Pandas Boundary Implementation Plan (`backend/dataframes/`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Concentrate all pandas use in the backend into one boundary package `backend/dataframes/` so `backend/api/v1/*`, `backend/inventory/service.py`, `backend/datasources/service.py` and `backend/sessions/family_service.py` stop importing pandas — a behavior-preserving refactor (spec: `docs/superpowers/specs/2026-07-23-pandas-boundary-design.md`).

**Architecture:** A new `backend/dataframes/` package (io / series / analysis / stock) is the only application-layer place that imports pandas; its functions take paths/bytes/DataFrames and return plain Python (`list[dict]`, `dict`, `list[str]`) — a DataFrame never crosses back out. Each consumer swaps its inline pandas for a boundary call, keeping all business logic (floors, quotas, upsert, DB updates) in place. A final architecture test greps `backend/` and asserts pandas lives only in `dataframes/`, `utils/temporal_agg.py`, and `workers/runner.py`.

**Tech Stack:** FastAPI + psycopg2; pandas (confined to the boundary); pytest against local Postgres :5544 (docker `faro_db`).

## Global Constraints

- All code, comments, tests, commit messages in **English** (CLAUDE.md). Chat replies to the user are Spanish; the only Spanish in the repo is `translations.ts` `es` values + backend end-user copy.
- **Behavior-preserving:** no endpoint output changes. The existing suite's assertions must NOT change — that is the proof. Only add tests (boundary unit tests + the architecture grep test).
- pandas/numpy may be imported ONLY in `backend/dataframes/`, `backend/utils/temporal_agg.py`, `backend/workers/runner.py`. Nowhere else under `backend/` (excluding `backend/tests/`).
- Boundary functions return plain Python only. NaN cells become `None`. A DataFrame is never returned to a consumer (the sole DataFrame-IN function is `stock.last_row_per_group`).
- Follow the repo's existing lazy-import style where a module imports pandas inside a function; inside `backend/dataframes/` module-level pandas imports are fine.
- Run tests: `cd backend && python -m pytest tests/<file> -q` (needs Postgres :5544).

---

### Task 1: Scaffold `backend/dataframes/` + `io.read_rows` / `read_columns`

**Files:**
- Create: `backend/dataframes/__init__.py`
- Create: `backend/dataframes/io.py`
- Test: `backend/tests/test_dataframes_boundary.py` (new)

**Interfaces:**
- Produces:
  - `read_rows(source, fmt: str | None = None, nrows: int | None = None) -> list[dict]` — `source` is a filesystem path (str) or raw `bytes`; `fmt` is `"csv"|"excel"|"json"|"parquet"` (inferred from a path's extension when omitted; required when `source` is bytes). Returns row dicts; NaN → `None`; numpy scalars → Python scalars.
  - `read_columns(path: str, cols: list[str]) -> list[dict]` — reads only `cols` (the `usecols` fast path). NaN → `None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_dataframes_boundary.py
"""Boundary package: plain-Python I/O over pandas (pandas-boundary refactor)."""

import io as _io
import math


def _csv_bytes():
    return b"sku,fecha,cantidad\nA,2026-01-01,5\nB,2026-01-02,\n"


class TestReadRows:
    def test_read_rows_from_csv_bytes(self):
        from backend.dataframes.io import read_rows
        rows = read_rows(_csv_bytes(), fmt="csv")
        assert rows[0] == {"sku": "A", "fecha": "2026-01-01", "cantidad": 5}
        # Empty numeric cell -> None (not NaN), and no numpy types leak.
        assert rows[1]["cantidad"] is None
        assert all(not (isinstance(v, float) and math.isnan(v))
                   for r in rows for v in r.values())

    def test_read_rows_from_path_infers_csv(self, tmp_path):
        from backend.dataframes.io import read_rows
        p = tmp_path / "d.csv"
        p.write_bytes(_csv_bytes())
        rows = read_rows(str(p))
        assert len(rows) == 2 and rows[0]["sku"] == "A"

    def test_read_rows_nrows_limit(self):
        from backend.dataframes.io import read_rows
        rows = read_rows(_csv_bytes(), fmt="csv", nrows=1)
        assert len(rows) == 1

    def test_read_columns_subset(self, tmp_path):
        from backend.dataframes.io import read_columns
        p = tmp_path / "d.csv"
        p.write_bytes(_csv_bytes())
        rows = read_columns(str(p), ["fecha"])
        assert rows[0] == {"fecha": "2026-01-01"}
        assert "sku" not in rows[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "ReadRows"`
Expected: FAIL — `backend.dataframes` does not exist.

- [ ] **Step 3: Implement `backend/dataframes/__init__.py`**

```python
"""
Data-file boundary (pandas-boundary refactor).

This package is the ONLY place in the application layer that imports pandas.
Every function takes a path / bytes / DataFrame and returns plain Python
(list[dict], dict, list[str]) — a DataFrame never crosses back out to a
consumer. Keeping pandas here (plus utils/temporal_agg.py and workers/runner.py)
lets the rest of backend/ stay pandas-free, which the architecture test in
tests/test_no_pandas_in_backend.py enforces.
"""
```

- [ ] **Step 4: Implement `backend/dataframes/io.py`**

```python
"""Tabular file reads returning plain Python rows."""
from __future__ import annotations

import io as _io
import math
from typing import Optional, Union

import pandas as pd

_Source = Union[str, bytes]


def _fmt_from_path(path: str) -> str:
    p = path.lower()
    if p.endswith((".xlsx", ".xls")):
        return "excel"
    if p.endswith(".json"):
        return "json"
    if p.endswith(".parquet"):
        return "parquet"
    return "csv"


def _to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list[dict] with NaN -> None and numpy scalars -> Python."""
    df = df.where(pd.notna(df), None)
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "item"):          # numpy scalar
                row[k] = v.item()
            elif isinstance(v, float) and math.isnan(v):
                row[k] = None
    return records


def _read_df(source: _Source, fmt: Optional[str], nrows: Optional[int]) -> pd.DataFrame:
    if isinstance(source, bytes):
        if fmt is None:
            raise ValueError("fmt is required when reading from bytes")
        buf: object = _io.BytesIO(source)
    else:
        fmt = fmt or _fmt_from_path(source)
        buf = source
    if fmt == "excel":
        return pd.read_excel(buf, nrows=nrows)
    if fmt == "json":
        df = pd.read_json(buf)
        return df.head(nrows) if nrows is not None else df
    if fmt == "parquet":
        df = pd.read_parquet(buf)
        return df.head(nrows) if nrows is not None else df
    return pd.read_csv(buf, nrows=nrows)


def read_rows(source: _Source, fmt: Optional[str] = None,
              nrows: Optional[int] = None) -> list[dict]:
    """Read a tabular file/bytes into plain row dicts. NaN -> None."""
    return _to_records(_read_df(source, fmt, nrows))


def read_columns(path: str, cols: list[str]) -> list[dict]:
    """Read only `cols` from a CSV/Excel file into plain row dicts."""
    fmt = _fmt_from_path(path)
    if fmt == "excel":
        df = pd.read_excel(path, usecols=cols)
    else:
        df = pd.read_csv(path, usecols=cols)
    return _to_records(df)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "ReadRows"`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/dataframes/ backend/tests/test_dataframes_boundary.py
git commit -m "feat(dataframes): boundary package + io.read_rows/read_columns"
```

---

### Task 2: `io.dataset_preview` + migrate datasources preview & `family_service`

**Files:**
- Modify: `backend/dataframes/io.py` (add `dataset_preview`)
- Modify: `backend/datasources/service.py` (`preview_source`, ~line 408-470 — the pandas block)
- Modify: `backend/sessions/family_service.py` (`_read_dataset_dates`, ~line with `import pandas as pd`)
- Test: `backend/tests/test_dataframes_boundary.py` (extend)

**Interfaces:**
- Consumes: Task 1 `read_rows`, `read_columns`.
- Produces: `dataset_preview(path: str, rows: int, sheet: str | None = None) -> dict` returning `{"columns": list[str], "rows": list[dict], "sheets": list[str] | None, "active_sheet": str | None, "total_rows": int | None}`. It does NOT touch the DB — the caller keeps the `row_count` UPDATE side effect (`total_rows` is returned for that).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_dataframes_boundary.py
class TestDatasetPreview:
    def test_preview_csv(self, tmp_path):
        from backend.dataframes.io import dataset_preview
        p = tmp_path / "d.csv"
        p.write_bytes(b"sku,cantidad\nA,5\nB,7\nC,9\n")
        out = dataset_preview(str(p), rows=2)
        assert out["columns"] == ["sku", "cantidad"]
        assert len(out["rows"]) == 2               # limited to `rows`
        assert out["rows"][0] == {"sku": "A", "cantidad": 5}
        assert out["sheets"] is None
        assert out["total_rows"] == 3              # full count, not the preview slice
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "DatasetPreview"`
Expected: FAIL — `dataset_preview` undefined.

- [ ] **Step 3: Implement `dataset_preview` in `backend/dataframes/io.py`**

```python
def dataset_preview(path: str, rows: int, sheet: Optional[str] = None) -> dict:
    """Preview shape for the datasources UI: first `rows` rows + column names,
    plus Excel sheet names and the full row count (for the caller's DB update).
    Returns plain Python; the DB write stays in the caller."""
    fmt = _fmt_from_path(path)
    sheets: Optional[list] = None
    total_rows: Optional[int] = None

    if fmt == "excel":
        xf = pd.ExcelFile(path)
        sheets = list(xf.sheet_names)
        target = sheet or (sheets[0] if sheets else None)
        full = pd.read_excel(path, sheet_name=target)
        total_rows = len(full)
        df = full.head(rows)
    elif fmt == "json":
        full = pd.read_json(path)
        total_rows = len(full)
        df = full.head(rows)
    elif fmt == "parquet":
        try:
            import pyarrow.parquet as pq
            total_rows = pq.read_metadata(path).num_rows
        except Exception:
            total_rows = None
        df = pd.read_parquet(path).head(rows)
    else:
        df = pd.read_csv(path, nrows=rows)

    return {
        "columns": list(df.columns),
        "rows": _to_records(df),
        "sheets": sheets,
        "active_sheet": sheet or (sheets[0] if sheets else None),
        "total_rows": total_rows,
    }
```

- [ ] **Step 4: Migrate `backend/datasources/service.py`**

Replace the pandas block in `preview_source` (from `import pandas as pd` through the `return {...}`) with a call to the boundary, keeping the CSV line-count fallback and the DB `UPDATE` (those are NOT pandas). New body of that section:

```python
    from backend.dataframes.io import dataset_preview
    preview = dataset_preview(file_path, rows, sheet=sheet)
    columns = preview["columns"]
    data_rows = preview["rows"]
    sheets = preview["sheets"]
    total_rows = preview["total_rows"]

    if not src.get("row_count"):
        try:
            if total_rows is None:
                with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                    total_rows = sum(1 for _ in _f) - 1
            execute(
                "UPDATE datasets SET row_count=%s, column_count=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
                (total_rows, len(columns), source_id, tenant_id),
            )
        except Exception as _e:
            log.warning("[preview] failed to update row count for source=%s: %s", source_id, _e)

    return {
        "columns": columns,
        "rows": data_rows,
        "row_count": len(data_rows),
        "sheets": sheets,
        "active_sheet": sheet or (sheets[0] if sheets else None),
        "truncated": len(data_rows) >= rows,
    }
```

Grep the file afterward for any remaining `pd.`/`import pandas`: there is a SECOND pandas block further down (`get_source_series`/dtype helpers around line 521-577). Leave those for Task 3 (series). If `preview_source` was the only pandas in this file's preview path, only that block changes here.

- [ ] **Step 5: Migrate `backend/sessions/family_service.py`**

In `_read_dataset_dates`, replace the `import pandas as pd` + `pd.read_csv/read_excel(usecols=[date_col])` block with:

```python
    from backend.dataframes.io import read_columns
    try:
        rows = read_columns(path, [date_col])
        return [str(r[date_col])[:10] for r in rows if r.get(date_col) is not None]
    except Exception as e:
        log.warning("family: could not read dates for session=%s: %s", session_id, e)
        return []
```

- [ ] **Step 6: Run new + regression**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py tests/test_session_family.py tests/test_datasources.py -q`
Expected: all pass (family fan-out still reads dates; datasource preview unchanged output). If `test_datasources.py` doesn't exist, grep for the preview test: `cd backend && python -m pytest tests/ -q -k "preview" ` and run that.

- [ ] **Step 7: Commit**

```bash
git add backend/dataframes/io.py backend/datasources/service.py backend/sessions/family_service.py backend/tests/test_dataframes_boundary.py
git commit -m "feat(dataframes): dataset_preview; migrate datasources preview + family_service dates"
```

---

### Task 3: `series.py` + migrate `forecasts.py` & `datasources` series/date-filter

**Files:**
- Create: `backend/dataframes/series.py`
- Modify: `backend/api/v1/forecasts.py` (historical reads ~lines 354, 427; reference/current compare ~lines 602-612)
- Modify: `backend/api/v1/datasources.py` (date filter ~lines 321-326, 375-395)
- Modify: `backend/datasources/service.py` (`get_source_series`/dtype block ~521-577, if it produces a series)
- Test: `backend/tests/test_dataframes_boundary.py` (extend)

**Interfaces:**
- Consumes: Task 1 `read_rows`.
- Produces:
  - `historical_series(path: str, date_col: str, target_col: str, sku_col: str | None, sku: str | None = None) -> list[dict]` — `[{"date": "YYYY-MM-DD", "value": float | None}]`, filtered to `sku` when given, sorted ascending by date.
  - `filter_rows_by_date(rows: list[dict], date_col: str, date_from: str | None, date_to: str | None) -> list[dict]` — plain-list inclusive date-range filter; rows whose `date_col` doesn't parse are dropped.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_dataframes_boundary.py
class TestSeries:
    def _write(self, tmp_path):
        p = tmp_path / "s.csv"
        p.write_bytes(
            b"sku,fecha,ventas\n"
            b"A,2026-01-02,5\nA,2026-01-01,3\nB,2026-01-01,9\n")
        return str(p)

    def test_historical_series_sorted_and_filtered(self, tmp_path):
        from backend.dataframes.series import historical_series
        out = historical_series(self._write(tmp_path), "fecha", "ventas", "sku", sku="A")
        assert out == [{"date": "2026-01-01", "value": 3.0},
                       {"date": "2026-01-02", "value": 5.0}]

    def test_historical_series_all_skus_when_none(self, tmp_path):
        from backend.dataframes.series import historical_series
        out = historical_series(self._write(tmp_path), "fecha", "ventas", "sku", sku=None)
        assert len(out) == 3

    def test_filter_rows_by_date_inclusive(self):
        from backend.dataframes.series import filter_rows_by_date
        rows = [{"d": "2026-01-01"}, {"d": "2026-01-05"}, {"d": "bad"}]
        out = filter_rows_by_date(rows, "d", "2026-01-02", None)
        assert out == [{"d": "2026-01-05"}]   # in-range kept, unparseable dropped
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "Series"`
Expected: FAIL — `backend.dataframes.series` missing.

- [ ] **Step 3: Implement `backend/dataframes/series.py`**

```python
"""Series extraction / date filtering over files, returning plain Python."""
from __future__ import annotations

from typing import Optional

import pandas as pd


def historical_series(path: str, date_col: str, target_col: str,
                      sku_col: Optional[str], sku: Optional[str] = None) -> list[dict]:
    """[{date: 'YYYY-MM-DD', value: float|None}] from a file, optionally for one
    SKU, sorted ascending by date."""
    df = pd.read_csv(path) if str(path).endswith(".csv") else pd.read_excel(path)
    if sku is not None and sku_col and sku_col in df.columns:
        df = df[df[sku_col].astype(str) == str(sku)]
    df = df.sort_values(date_col)
    out: list[dict] = []
    for _, row in df.iterrows():
        val = row[target_col]
        out.append({
            "date": str(row[date_col])[:10],
            "value": float(val) if pd.notna(val) else None,
        })
    return out


def filter_rows_by_date(rows: list[dict], date_col: str,
                        date_from: Optional[str], date_to: Optional[str]) -> list[dict]:
    """Inclusive date-range filter on plain rows; unparseable dates are dropped."""
    if not rows:
        return []
    lo = pd.Timestamp(date_from) if date_from else None
    hi = pd.Timestamp(date_to) if date_to else None
    out: list[dict] = []
    for r in rows:
        ts = pd.to_datetime(r.get(date_col), errors="coerce")
        if pd.isna(ts):
            continue
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts > hi:
            continue
        out.append(r)
    return out
```

- [ ] **Step 4: Migrate the consumers**

In `backend/api/v1/forecasts.py`, replace each historical-read block (the `df = pd.read_csv(...) if ... else pd.read_excel(...)` then the row loop building `{date, value}`) with:

```python
    from backend.dataframes.series import historical_series
    historical_raw = historical_series(data_path, dt_col, target_col, sku_col, sku=sku)
```

(Use the `dt_col`/`target_col`/`sku_col` already resolved in scope at each site; the two sites at ~354 and ~427 share this shape.) For the reference/current comparison read (~602-612), replace `pd.read_csv/read_excel(...)` + `pd.read_excel(io.BytesIO(content))` with `dataframes.io.read_rows(path)` / `read_rows(content, fmt="excel"|"csv")` and adapt the downstream comparison to iterate plain rows (it already extracts `{date, value}` per row).

In `backend/api/v1/datasources.py`, replace the two date-filter blocks (`pd.to_datetime` + `pd.Timestamp` comparisons, ~321-326 and ~375-395) with `dataframes.series.filter_rows_by_date(rows, date_col, date_from, date_to)`, where `rows` is obtained via `dataframes.io.read_rows(...)` instead of a DataFrame; the `{date, value}` projection (~393-395) becomes a plain comprehension over the filtered rows.

In `backend/datasources/service.py`, if `get_source_series` (~521-577) builds a series, route it through `historical_series` / `read_rows` + a plain dtype check (numeric vs categorical can be inferred with `isinstance(v, (int, float))` over the column's non-None values instead of `pd.api.types.is_numeric_dtype`).

- [ ] **Step 5: Run new + regression**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py tests/test_endpoints.py tests/test_endpoints_offline.py -q -k "series or forecast or Series or Forecast or datasource"`
Expected: all pass. Then a broader net: `cd backend && python -m pytest tests/test_forecast_flow.py -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/dataframes/series.py backend/api/v1/forecasts.py backend/api/v1/datasources.py backend/datasources/service.py backend/tests/test_dataframes_boundary.py
git commit -m "feat(dataframes): series module; migrate forecasts + datasources series"
```

---

### Task 4: `analysis.py` + migrate `configuration.py`

**Files:**
- Create: `backend/dataframes/analysis.py`
- Modify: `backend/api/v1/configuration.py` (all `pd.` sites: ~5 (module import), 169/177 (`pd.errors`), 237-253, 354-364, 427-436, 544-546, 602-612, 653-689)
- Test: `backend/tests/test_dataframes_boundary.py` (extend)

**Interfaces:**
- Consumes: Task 1 `read_rows`, Task 3 `historical_series`.
- Produces:
  - `analyze_dataset(path: str) -> dict` — the analysis payload `configuration.py`'s `_get_dataset_analysis_impl` returns: `{"columns": [{name, dtype, role, null_pct, n_unique}], "temporal": {date_min, date_max, n_periods, freq_days, gap_count, freq_label} | None, "seasonality": {...} | None, "sku_stats": {...} | None}`. Move the existing pandas analysis body verbatim into this function (it already produces that dict) — only its home changes.
  - `accuracy_actuals(path: str, date_col: str, target_col: str) -> list[dict]` — `[{date, value}]` actuals for the accuracy-compare endpoint (the ~237-253 block), value `None` when the cell is NaN.
  - `read_error_message(exc: Exception) -> str | None` — maps a pandas read error (`EmptyDataError`/`ParserError`) to the user string `configuration.py` currently builds, so the endpoint keeps its 422 copy without importing `pd.errors`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_dataframes_boundary.py
class TestAnalysis:
    def test_analyze_dataset_basic(self, tmp_path):
        from backend.dataframes.analysis import analyze_dataset
        p = tmp_path / "a.csv"
        # 3 SKUs, daily dates -> temporal detected, columns classified.
        rows = ["sku,fecha,ventas"]
        for i in range(40):
            rows.append(f"A,2026-01-{(i % 28) + 1:02d},{i}")
        p.write_text("\n".join(rows))
        out = analyze_dataset(str(p))
        assert "columns" in out and "temporal" in out
        names = {c["name"] for c in out["columns"]}
        assert {"sku", "fecha", "ventas"} <= names
        # ventas is numeric, sku categorical
        role = {c["name"]: c["role"] for c in out["columns"]}
        assert role["ventas"] == "numeric" and role["sku"] == "categorical"

    def test_accuracy_actuals(self, tmp_path):
        from backend.dataframes.analysis import accuracy_actuals
        p = tmp_path / "b.csv"
        p.write_text("fecha,ventas\n2026-01-01,5\n2026-01-02,\n")
        out = accuracy_actuals(str(p), "fecha", "ventas")
        assert out[0] == {"date": "2026-01-01", "value": 5.0}
        assert out[1]["value"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "Analysis"`
Expected: FAIL — `backend.dataframes.analysis` missing.

- [ ] **Step 3: Implement `backend/dataframes/analysis.py`**

Read the current `_get_dataset_analysis_impl` in `backend/api/v1/configuration.py` (the function around line 613-700 that builds the analysis dict) and MOVE its pandas body into `analyze_dataset(path)` verbatim — the read (`pd.read_csv/read_excel`), the per-column dtype/role/null/nunique loop (`is_numeric_dtype`), the temporal block (`to_datetime`, `.dropna().sort_values()`, freq/gaps), the seasonality + sku_stats. It already returns the target dict; only the file read + `path` argument are new at the top. Then:

```python
"""Dataset analysis (columns, temporal, seasonality, sku stats) as plain Python."""
from __future__ import annotations

import pandas as pd


def _read(path: str) -> pd.DataFrame:
    return pd.read_csv(path) if str(path).endswith(".csv") else pd.read_excel(path)


def read_error_message(exc: Exception) -> str | None:
    """User-facing message for a pandas read failure, or None if not one."""
    if isinstance(exc, pd.errors.EmptyDataError):
        return "The file is empty or has no readable rows."
    if isinstance(exc, pd.errors.ParserError):
        return f"The file could not be parsed: {exc}"
    return None


def accuracy_actuals(path: str, date_col: str, target_col: str) -> list[dict]:
    df = _read(path)
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    out = []
    for _, row in df.iterrows():
        val = row[target_col]
        out.append({"date": row[date_col],
                    "value": float(val) if pd.notna(val) else None})
    return out


def analyze_dataset(path: str) -> dict:
    df = _read(path)
    # <-- MOVE the existing _get_dataset_analysis_impl analysis body here,
    #     operating on `df`, returning {columns, temporal, seasonality, sku_stats}.
    ...
```

Match the exact wording of `read_error_message` to whatever `configuration.py` currently raises for those two exceptions (grep lines 169/177) so the endpoint's 422 detail is byte-identical.

- [ ] **Step 4: Migrate `backend/api/v1/configuration.py`**

- Remove the module-level `import pandas as pd` (line 5 / 22).
- The two `except pd.errors.EmptyDataError/ParserError` handlers: catch `Exception as e`, call `msg = analysis.read_error_message(e)`; if `msg` is not None raise the existing 422 with it, else re-raise.
- `_get_dataset_analysis_impl`: replace its whole pandas body with `return analysis.analyze_dataset(data_path)`.
- The accuracy-compare block (~237-253): `actuals = analysis.accuracy_actuals(path, date_col, target_col)`.
- The historical reads (~354-364, 427-436): `dataframes.series.historical_series(...)`.
- The reference/current compare read (~602-612): `dataframes.io.read_rows(...)`.
- Add `from backend.dataframes import analysis, io, series` (or per-function lazy imports matching the file's style).

- [ ] **Step 5: Run new + regression**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py tests/test_granularity.py tests/test_endpoints.py -q -k "analysis or granular or configuration or Analysis or dataset"`
Expected: all pass. Confirm no `pd.` remains: `grep -n "pd\.\|import pandas" backend/api/v1/configuration.py` → no matches.

- [ ] **Step 6: Commit**

```bash
git add backend/dataframes/analysis.py backend/api/v1/configuration.py backend/tests/test_dataframes_boundary.py
git commit -m "feat(dataframes): analysis module; migrate configuration.py off pandas"
```

---

### Task 5: `stock.py` + migrate `sync_stock_from_dataset`

**Files:**
- Create: `backend/dataframes/stock.py`
- Modify: `backend/inventory/service.py` (`sync_stock_from_dataset`, ~237-322)
- Test: `backend/tests/test_dataframes_boundary.py` (extend)

**Interfaces:**
- Produces: `last_row_per_group(df, group_col: str | None, date_col: str, columns: list[str]) -> list[tuple[str, dict]]` — the ONLY boundary function that accepts a DataFrame (ForecastingCore's `engine._df`). Sorts by `date_col` when present, groups by `group_col` (or one `"__all__"` group), takes the last row, and returns `[(sku, {col: raw_value})]` for the `columns` present, dropping NaN cells. Raw values are NOT floored/typed — the caller keeps that logic.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_dataframes_boundary.py
class TestStockExtract:
    def test_last_row_per_group(self):
        import pandas as pd
        from backend.dataframes.stock import last_row_per_group
        df = pd.DataFrame({
            "sku":   ["A", "A", "B"],
            "fecha": ["2026-01-01", "2026-01-03", "2026-01-02"],
            "current_stock": [5, 9, 7],
            "moq": [1, 1, None],
        })
        out = dict(last_row_per_group(df, "sku", "fecha", ["current_stock", "moq"]))
        assert out["A"] == {"current_stock": 9, "moq": 1}   # latest A row
        assert out["B"] == {"current_stock": 7}             # NaN moq dropped
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py -q -k "StockExtract"`
Expected: FAIL — `backend.dataframes.stock` missing.

- [ ] **Step 3: Implement `backend/dataframes/stock.py`**

```python
"""The one boundary function that takes a DataFrame in (ForecastingCore's)."""
from __future__ import annotations

from typing import Optional

import pandas as pd


def last_row_per_group(df, group_col: Optional[str], date_col: str,
                       columns: list[str]) -> list[tuple[str, dict]]:
    """Latest row per group, projected to `columns` (NaN cells dropped), as
    plain (sku, dict) pairs. No flooring/typing — the caller owns that."""
    if df is None or df.empty:
        return []
    present = [c for c in df.columns if c in columns]
    if not present:
        return []
    work = df.copy()
    if date_col in work.columns:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values(date_col)
    groups = (work.groupby(group_col) if group_col and group_col in work.columns
              else [("__all__", work)])
    out: list[tuple[str, dict]] = []
    for sku, g in groups:
        last = g.iloc[-1]
        data: dict = {}
        for col in present:
            val = last[col]
            if pd.isna(val):
                continue
            data[col] = val
        out.append((str(sku), data))
    return out
```

- [ ] **Step 4: Migrate `backend/inventory/service.py`**

In `sync_stock_from_dataset`, remove `import pandas as pd`. Replace the read/group/extract section (from `work = df.copy()` through building `entries`) with:

```python
    from backend.dataframes.stock import last_row_per_group
    raw_entries = last_row_per_group(df, group_col, date_col, _DATASET_STOCK_COLS)

    # Resolve the per-SKU payload with the numeric floors up front (before the
    # max_skus check), exactly as before — only the pandas extraction moved out.
    entries: list[tuple[str, dict]] = []
    for sku, raw in raw_entries:
        data: dict = {}
        for col, val in raw.items():
            if col in _DATASET_STOCK_FLOAT_COLS:
                parsed_float = float(val)
                floor = _DATASET_STOCK_MIN.get(col)
                if floor is not None and parsed_float < floor:
                    continue
                data[col] = parsed_float
            elif col in _DATASET_STOCK_INT_COLS:
                parsed_int = int(val)
                floor = _DATASET_STOCK_MIN.get(col)
                if floor is not None and parsed_int < floor:
                    continue
                data[col] = parsed_int
            else:
                data[col] = str(val)
        if not data:
            continue
        entries.append((sku, data))
```

Keep the `if df is None or df.empty: return 0` guard by moving it into the boundary (already handled: `last_row_per_group` returns `[]` for empty) — replace that early return with `if not raw_entries: return 0` AFTER the call, and drop the `df.empty` reference so the module has no pandas. Everything below (`max_skus` `enforce_limit`, the `upsert_stock` loop) is unchanged.

- [ ] **Step 5: Run new + regression**

Run: `cd backend && python -m pytest tests/test_dataframes_boundary.py tests/test_inventory.py tests/test_entitlements.py -q -k "StockExtract or DatasetSync or dataset_sync or sanitiz"`
Expected: all pass (the direct `sync_stock_from_dataset(df, ...)` tests still pass — signature accepts a DataFrame). Confirm: `grep -n "pd\.\|import pandas" backend/inventory/service.py` → no matches.

- [ ] **Step 6: Commit**

```bash
git add backend/dataframes/stock.py backend/inventory/service.py backend/tests/test_dataframes_boundary.py
git commit -m "feat(dataframes): stock extraction; migrate sync_stock_from_dataset off pandas"
```

---

### Task 6: Architecture test + full regression

**Files:**
- Create: `backend/tests/test_no_pandas_in_backend.py`
- Modify: `CLAUDE.md` (reword the layer rule) + `docs/plan_general_faro_2026-07-18.md` (mark the debt closed)

**Interfaces:**
- Consumes: the whole refactor.

- [ ] **Step 1: Write the architecture test**

```python
# backend/tests/test_no_pandas_in_backend.py
"""Enforce the pandas boundary: pandas/numpy live only in dataframes/,
utils/temporal_agg.py, and workers/runner.py (pandas-boundary refactor)."""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent  # backend/
_ALLOWED = {
    ("dataframes",),          # any file under backend/dataframes/
    ("utils", "temporal_agg.py"),
    ("workers", "runner.py"),
}
_PATTERN = re.compile(r"^\s*(import|from)\s+(pandas|numpy)\b", re.MULTILINE)


def _is_allowed(rel: pathlib.PurePath) -> bool:
    parts = rel.parts
    if parts and parts[0] == "tests":
        return True
    if parts and parts[0] == "dataframes":
        return True
    return parts in _ALLOWED


def test_pandas_only_in_boundary_modules():
    offenders = []
    for py in _ROOT.rglob("*.py"):
        rel = py.relative_to(_ROOT)
        if _is_allowed(rel):
            continue
        if _PATTERN.search(py.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(rel))
    assert offenders == [], (
        "pandas/numpy imported outside the boundary: " + ", ".join(sorted(offenders)))
```

- [ ] **Step 2: Run it**

Run: `cd backend && python -m pytest tests/test_no_pandas_in_backend.py -q`
Expected: PASS. If it lists offenders, those are files Tasks 2-5 missed — fix each by routing through the boundary, then re-run. Do not weaken the test to make it pass.

- [ ] **Step 3: Full backend suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 0 failures beyond the known machine-load-flaky `test_stress.py::test_login_responds_under_2s` (re-run it alone to confirm). Every refactored endpoint's existing tests must pass with unchanged assertions — that is the behavior-preservation proof.

- [ ] **Step 4: Reword the rule + close the debt**

In `CLAUDE.md`, under the layer-separation section, change the "no pandas in backend" statement to: "pandas lives only in `backend/dataframes/`, `backend/utils/temporal_agg.py`, and `backend/workers/runner.py`; no other backend module imports it (enforced by `tests/test_no_pandas_in_backend.py`)."

In `docs/plan_general_faro_2026-07-18.md`, mark the transversal tech-debt item #1 (pandas out of api/v1 + inventory/service.py) as done, referencing `backend/dataframes/` and the architecture test.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_no_pandas_in_backend.py CLAUDE.md docs/plan_general_faro_2026-07-18.md
git commit -m "test(arch): enforce pandas boundary; close the pandas-in-backend debt"
```

## Out of scope (this plan)

- Any change to endpoint outputs or business logic (floors, quotas, upsert, DB writes).
- `temporal_agg.py` / `runner.py` internals (accepted boundaries).
- Removing the pandas dependency (the boundary keeps it — the goal is concentration).

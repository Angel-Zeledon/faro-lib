# Pandas Boundary — `backend/dataframes/` Design

> Date: 2026-07-23
> Status: approved in brainstorm
> Motivation: CLAUDE.md declares "no pandas/ML in backend/", but today
> `backend/api/v1/{configuration,forecasts,datasources}.py`,
> `backend/inventory/service.py`, `backend/datasources/service.py` and
> `backend/sessions/family_service.py` all import pandas — the #1 open tech-debt
> item. Reading user-uploaded CSV/Excel is legitimate backend I/O, not ML logic,
> so the fix is to concentrate that I/O behind one boundary rather than ban it.

## Goal

Make pandas live in **exactly three** backend modules — a new
`backend/dataframes/` package, the existing `backend/utils/temporal_agg.py`, and
`backend/workers/runner.py` (the ForecastingCore bridge) — and nowhere else.
Every other backend module receives plain Python structures (`list[dict]`,
`dict`, `list[str]`), never a DataFrame. This is a **behavior-preserving
refactor**: no endpoint output changes; the existing test suite is the safety
net, plus one new architecture test that enforces the boundary forever.

## The rule (reformulated, verifiable)

> pandas (and numpy) may be imported **only** in `backend/dataframes/`,
> `backend/utils/temporal_agg.py`, and `backend/workers/runner.py`. No other
> module under `backend/` (excluding `backend/tests/`) imports them.

A new test greps `backend/` for `import pandas` / `import numpy` / `from pandas`
/ `from numpy` and asserts the only matches are those three modules. This turns
the layer rule into something CI enforces.

## Current pandas usage (verified 2026-07-23)

Production modules to refactor:
- `backend/inventory/service.py` — `sync_stock_from_dataset(tenant_id, df, group_col, date_col)`: `df.copy()`, `to_datetime`, `sort_values`, `groupby`, `iloc[-1]`, `pd.isna`. Called from `runner.py:549` with `engine._df` (a ForecastingCore DataFrame).
- `backend/api/v1/configuration.py` — `pd.read_csv/read_excel`, `to_datetime`, `notna`, `pd.errors.*`, and dataset analysis (`is_numeric_dtype`, seasonality/sku stats, date span).
- `backend/api/v1/forecasts.py` — `pd.read_csv/read_excel`, `to_datetime`, `notna` for historical series + a reference/current comparison read.
- `backend/api/v1/datasources.py` — `to_datetime`, date-range filtering, `notna` → `{date, value}` rows.
- `backend/datasources/service.py` — `pd.ExcelFile/read_excel/read_json/read_parquet/read_csv`, `where(notna)`, `is_datetime64_any_dtype` for previews.
- `backend/sessions/family_service.py` — `_read_dataset_dates`: `pd.read_csv/read_excel(usecols=[date_col])` (the "documented exception" added in multi-period Phase A).

Left as accepted boundaries (unchanged): `backend/utils/temporal_agg.py`,
`backend/workers/runner.py`.

## The boundary package — `backend/dataframes/`

Four focused modules, each returning plain Python. All pandas imports are local
(inside functions) matching the existing lazy-import style, or module-level
inside `dataframes/` only.

### `dataframes/io.py` — tabular reads
- `read_rows(source, fmt: str | None = None, nrows: int | None = None) -> list[dict]`
  — `source` is a path or bytes; `fmt` inferred from extension when a path.
  Reads CSV/Excel/JSON/Parquet, returns row dicts with `None` for NaN.
- `read_columns(path: str, cols: list[str]) -> list[dict]` — reads only the
  named columns (the `usecols` fast path family_service needs).
- `dataset_preview(path: str, rows: int) -> dict` — `{columns: [{name, dtype,
  role, ...}], rows: list[dict], n_rows, ...}` — the datasources preview shape,
  with dtype→role ('numeric'|'categorical'|'datetime') inference done here.

### `dataframes/series.py` — series from files
- `historical_series(path: str, date_col: str, target_col: str, sku_col: str | None, sku: str | None = None) -> list[dict]`
  — `[{date: 'YYYY-MM-DD', value: float|None}]`, optionally filtered to one SKU,
  sorted by date.
- `filter_rows_by_date(rows: list[dict], date_col: str, date_from: str | None, date_to: str | None) -> list[dict]`
  — plain-list date-range filter (datasources).

### `dataframes/analysis.py` — dataset analysis
- `analyze_dataset(path: str) -> dict` — the `configuration.py` analysis payload:
  `{columns, temporal: {date_min, date_max, n_periods, freq_days, gap_count,
  freq_label}, seasonality, sku_stats}`.
- `accuracy_actuals(path: str, date_col: str, target_col: str) -> list[dict]`
  — actual values for the accuracy backfill/compare paths, as `{date, value}`.

### `dataframes/stock.py` — the one DataFrame-in function
- `last_row_per_group(df, group_col: str | None, date_col: str, columns: list[str]) -> list[tuple[str, dict]]`
  — takes the ForecastingCore DataFrame (the ONLY function accepting one),
  does the `to_datetime`/`sort_values`/`groupby`/`iloc[-1]`/`isna` dance, and
  returns `[(sku, {col: raw_value})]` with NaN cells dropped. All the numeric
  floors / max_skus enforcement / upsert stay in `inventory/service.py`.

## Refactored consumers

- **`inventory/service.py`** — `sync_stock_from_dataset` no longer imports pandas.
  It calls `dataframes.stock.last_row_per_group(df, group_col, date_col,
  _DATASET_STOCK_COLS)` to get plain `(sku, dict)` entries, then keeps every bit
  of business logic unchanged: the `_DATASET_STOCK_MIN` floor filtering, the
  pre-loop `max_skus` `enforce_limit`, and the `upsert_stock` loop. Signature
  still takes `df: Any` (it comes from runner, the allowed layer) — the module
  simply never touches `pd.*` again.
- **`api/v1/configuration.py`** — each `pd.read_*`/`to_datetime`/`notna` becomes
  a `dataframes.io`/`dataframes.series`/`dataframes.analysis` call. The
  `pd.errors.EmptyDataError`/`ParserError` handling moves into `dataframes.io`
  (it raises a plain `ValueError` with the same message the endpoint already maps).
- **`api/v1/forecasts.py`** — historical reads → `dataframes.series.historical_series`;
  the reference/current comparison read → `dataframes.io.read_rows`.
- **`api/v1/datasources.py`** — date filtering/series → `dataframes.series`.
- **`datasources/service.py`** — preview → `dataframes.io.dataset_preview`.
- **`sessions/family_service.py`** — `_read_dataset_dates` → `dataframes.io.read_columns`
  (removes the Phase-A documented exception).

## Testing

- **Safety net = the existing suite, unchanged assertions.** Every refactored
  path already has tests: `test_inventory.py::TestDatasetSyncSanitization`,
  `test_entitlements.py::test_dataset_sync_respects_max_skus`,
  `test_granularity.py`, `test_endpoints*.py` (configuration/forecasts/preview),
  `test_endpoints_offline.py`. They must stay green with NO assertion changes —
  that IS the proof of behavior preservation.
- The direct-`sync_stock_from_dataset(...)` tests keep passing (signature still
  accepts a `pd.DataFrame`).
- **New architecture test** `backend/tests/test_no_pandas_in_backend.py`: walk
  `backend/` (excluding `tests/`), grep each `.py` for `import pandas|import numpy|
  from pandas|from numpy`, assert the only files that match are
  `dataframes/*.py`, `utils/temporal_agg.py`, `workers/runner.py`. Fails loudly
  if anyone reintroduces pandas elsewhere.
- Focused unit tests for the new boundary functions (round-trip a small CSV
  through `read_rows`/`historical_series`/`dataset_preview`, and
  `last_row_per_group` on a tiny DataFrame) so the boundary has its own coverage,
  not only the endpoint tests above it.

## Phasing (behavior-preserving, each phase green on its own)

1. **Scaffold + io**: create `backend/dataframes/` (`io.py`); migrate the
   datasources preview + `family_service._read_dataset_dates`; unit tests for io.
2. **series**: `series.py`; migrate `forecasts.py` + `datasources.py` reads.
3. **analysis**: `analysis.py`; migrate `configuration.py`.
4. **stock**: `stock.py`; migrate `inventory/service.py sync_stock_from_dataset`.
5. **enforce**: the architecture test; delete now-dead pandas imports; run the
   full suite + confirm the grep test passes.

## Out of scope

- Any change to endpoint outputs or business logic (floors, quotas, upsert).
- `temporal_agg.py` / `runner.py` internals (accepted boundaries).
- Replacing pandas with the stdlib `csv` module (the boundary keeps pandas; the
  point is to concentrate it, not remove the dependency).
- Moving analysis into ForecastingCore (rejected in brainstorm — file I/O is
  backend's job, not the ML engine's).

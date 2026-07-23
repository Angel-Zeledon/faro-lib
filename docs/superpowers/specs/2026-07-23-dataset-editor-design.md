# In-App Dataset Editor (Edit & Save-As-New) — Design

**Date:** 2026-07-23
**Status:** Approved for planning

## Goal

Let a user open an uploaded dataset, edit it as a spreadsheet in the browser
(fix cell values, delete/add rows, rename/drop columns), and save the result as
a **new** dataset that carries a lineage link to the original. The original is
never modified. The new dataset is a normal dataset row: trainable immediately,
listed alongside the rest, and re-editable.

## Scope

**In scope (v1):**
- Open a file-backed dataset into an editable grid (full table loaded to the
  browser).
- Edit operations: change a cell value, delete rows, add rows, rename a column,
  drop a column.
- Save-as-new: the browser posts the edited table; the backend writes a new
  file on disk and a new `datasets` row with `parent_id` = the source dataset.
- A size guard: files above a configured threshold are not editable inline; the
  UI shows a clear "file too large to edit online" message instead of freezing.
- Output is always CSV.

**Out of scope (v1):**
- Editing SQL-backed data sources (only file sources are editable).
- Formulas, cell formatting, multi-sheet editing, undo/redo history persistence.
- In-place editing of the original (save-as-new only, by design).
- Operations-recipe / server-side patch model (we send the full edited table).

## Architecture

The browser is the editing surface. On open, the full table (up to the size
guard) is loaded into an editable grid. All edits happen client-side in memory.
On save, the browser posts the **entire edited table** (columns + rows); the
backend writes it as a new CSV dataset with lineage to the source. pandas stays
behind the `backend/dataframes/` boundary — the new write goes there.

### Data flow

```
Open dataset → GET  /api/v1/datasources/{id}/edit-table
  · size guard: if row_count / size over threshold → 400 "too large to edit"
  · else return { columns, rows }  (full table, via dataframes boundary)

Edit in browser (grid): cell edits, add/delete rows, rename/drop columns

Save → POST /api/v1/datasources/{id}/save-as-new  { name, columns, rows }
  · require analyst-or-above (creating a dataset is a mutation)
  · sanitize every cell against CSV formula injection (csv_safe)
  · write a new CSV under a fresh dataset dir (dataframes boundary write_rows)
  · INSERT a new datasets row: parent_id = source id, source_type 'file',
    row_count / column_count computed, name defaults to "<original> (editado)"
  · return the new dataset (public shape)
```

### Size guard

Editing loads the whole file into the browser, which is fine for the common
case (a few thousand rows) but must not be promised for the 50 MB upload
ceiling. `edit-table` refuses when the source exceeds a configured threshold
(rows and/or bytes), returning a clear message. The threshold lives in config
so it can be tuned without code changes. The guard is checked from the stored
`datasets.row_count` / `size_bytes` **before** loading the file, so a huge file
is never read into memory.

## Components

- `backend/api/v1/datasources.py` — **MODIFY.** Add two endpoints:
  - `GET  /{id}/edit-table` (read; `get_current_user`) — returns the full
    editable table or a 400 if over the guard.
  - `POST /{id}/save-as-new` (mutation; `require_analyst_or_above`) — accepts
    the edited table, returns the new dataset.
- `backend/datasources/service.py` — **MODIFY.** Add:
  - `load_editable_table(tenant, source_id) -> {columns, rows}` — enforces the
    size guard, reads file sources only (SQL sources rejected).
  - `save_edited_as_new(tenant, user, source_id, name, columns, rows) -> dict` —
    sanitizes cells, writes the new CSV via the boundary, inserts the new
    `datasets` row with `parent_id`, returns the public dataset.
- `backend/dataframes/io.py` — **MODIFY.** Add `write_rows(path, columns, rows,
  fmt="csv")` — the one place that writes an edited table to disk (pandas here).
  Add `read_table(path, sheet=None) -> {columns, rows}` for the full-table load
  (columns preserved even when there are zero rows).
- `backend/utils/csv_safe.py` — **REUSE.** Every cell written to the new CSV is
  passed through the existing formula-injection sanitizer.
- **DB migration** — add `datasets.parent_id` (TEXT, nullable) referencing the
  source dataset id; set on save-as-new, null for uploads.
- `Frontend/` — **MODIFY.** An editable grid view reachable from the data-source
  detail/preview screen: load via `edit-table`, edit in memory, "Guardar como
  nuevo" posts to `save-as-new`; over-guard files show the too-large message.

### Reuse (no reimplementation)

- File storage layout + atomic-write discipline: mirror `create_file_source` /
  `replace_file_source` (`storage.paths.dataset_dir`, tmp-then-swap).
- Row/column counting: the same eager-count pattern already in the service.
- CSV injection guard: the existing `csv_safe` module (added during
  multi-warehouse QA).
- Preview/read: the `dataframes/` boundary; no pandas leaks into the service.

## Error handling & safety

- **Over the size guard** → 400 with a clear message; file never loaded.
- **SQL source** → 400 "only file datasets are editable".
- **CSV formula injection** → every edited cell sanitized before write, so a
  cell like `=cmd()` can't become an executable formula in a downloaded CSV.
- **Atomic write** → new file written to `.tmp` then swapped, so a failed/partial
  write never leaves a corrupt dataset (matches `replace_file_source`).
- **Original untouched** → save always creates a new id + new file; the source
  dataset's row and file are never modified.
- **Tenant scope** → both endpoints resolve the source by `(id, tenant_id)`; no
  cross-tenant read or save.
- **Empty result** → saving a table with all rows deleted is allowed but keeps
  the columns (header-only CSV); `read_table` preserves columns at zero rows.

## Testing

Following the repo mandate — assert DB + on-disk state directly, permission
pairs, no tests that can't fail.

- **Load:** `edit-table` on a small file returns all rows + columns; on a file
  over the guard returns 400 with the too-large message and does not read it.
- **Save creates a distinct dataset:** `save-as-new` inserts a new `datasets`
  row with `parent_id` = source id, a new file on disk, correct `row_count`;
  the original row and original file bytes are unchanged (assert both).
- **Edits are persisted:** a changed cell, a deleted row, a renamed column, and
  a dropped column are each reflected when the new file is read back (assert
  content, not just status).
- **Permission pair:** viewer → `save-as-new` 403 and **no** new `datasets` row;
  analyst → success (new row present).
- **Tenant isolation:** editing/saving another tenant's source id → not found.
- **CSV injection:** a cell value starting with `=`/`+`/`-`/`@` is sanitized in
  the saved file (assert the written bytes are neutralized).
- **SQL source rejected:** `edit-table` / `save-as-new` on a SQL source → 400.

## Open questions

None blocking. The exact size-guard threshold is a config value chosen during
planning (starting point: a few thousand rows / a few MB); it is tunable.

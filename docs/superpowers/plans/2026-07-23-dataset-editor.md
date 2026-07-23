# In-App Dataset Editor (Edit & Save-As-New) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user open a file-backed dataset, edit it as a spreadsheet in the browser, and save the result as a new dataset that carries a `parent_id` lineage link to the original — the original is never modified.

**Architecture:** The browser loads the full table (guarded by a size threshold) into an editable grid, edits happen client-side, and on save the entire edited table is POSTed. The backend sanitizes every cell (`csv_safe`), writes a new CSV atomically through the `backend/dataframes/` pandas boundary, and inserts a new `datasets` row with `parent_id` set. pandas never leaks into the service/API layer.

**Tech Stack:** FastAPI, psycopg2/Postgres, pandas (only under `backend/dataframes/`), Next.js 14 + React, TypeScript.

## Global Constraints

- All code, comments, identifiers, DB columns, API fields, commit messages in English. Only end-user UI copy is Spanish, added as `es` values in `Frontend/src/i18n/translations.ts` (keys are English).
- pandas/numpy may be imported ONLY under `backend/dataframes/` (enforced by `backend/tests/test_no_pandas_in_backend.py`). The new file write/read go through `backend/dataframes/io.py`.
- Every cell written to the new CSV passes through `backend/utils/csv_safe.py::csv_safe`.
- Mutating endpoint (`save-as-new`) requires `require_analyst_or_above`; read endpoint (`edit-table`) requires `get_current_user`.
- Tests assert DB + on-disk state directly, include a viewer/analyst permission pair, and never use either/or asserts.
- Output is always CSV. The original dataset's row and file bytes must never change on save.
- Size guard is checked from stored `datasets.row_count` / `size_bytes` BEFORE reading the file. Threshold values: `dataset_editor_max_rows = 50_000`, `dataset_editor_max_mb = 10`.

---

### Task 1: Add `datasets.parent_id` migration + editor size-guard config

**Files:**
- Modify: `backend/db/migrations.py` (append to `_MIGRATIONS` list, after the last incremental entry)
- Modify: `backend/config.py` (add two settings in the Upload section)

**Interfaces:**
- Produces: DB column `datasets.parent_id TEXT` (nullable); `settings.dataset_editor_max_rows: int`, `settings.dataset_editor_max_mb: int`.

- [ ] **Step 1: Add the migration entry.** In `backend/db/migrations.py`, append to the `_MIGRATIONS` list (just before the closing `]`):

```python
    ("add_datasets_parent_id",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS parent_id TEXT"),
```

- [ ] **Step 2: Add config settings.** In `backend/config.py`, after the `max_upload_size_mb` line, add:

```python
    # In-app dataset editor size guard — checked from stored row_count/size_bytes
    # BEFORE reading the file, so a huge file is never loaded into memory.
    dataset_editor_max_rows: int = 50_000
    dataset_editor_max_mb: int = 10
```

- [ ] **Step 3: Verify import + migration apply.** Run:

```bash
cd backend && python -c "from backend.config import settings; print(settings.dataset_editor_max_rows, settings.dataset_editor_max_mb)"
```

Expected: `50000 10`

- [ ] **Step 4: Commit**

```bash
git add backend/db/migrations.py backend/config.py
git commit -m "feat(datasets): parent_id migration + editor size-guard config"
```

---

### Task 2: `dataframes.io.read_table` + `write_rows`

**Files:**
- Modify: `backend/dataframes/io.py` (add two functions after `dataset_preview`)
- Test: `backend/tests/test_dataframes_io_editor.py` (new)

**Interfaces:**
- Produces:
  - `read_table(path: str, sheet: Optional[str] = None) -> dict` returning `{"columns": list[str], "rows": list[dict]}`, columns preserved even with zero rows.
  - `write_rows(path: str, columns: list[str], rows: list[dict], fmt: str = "csv") -> None` — writes a tabular file to `path`. Column order follows `columns`; header written even with zero rows.

- [ ] **Step 1: Write the failing test.** Create `backend/tests/test_dataframes_io_editor.py`:

```python
"""Unit tests for the editor read/write boundary helpers."""
import pytest

from backend.dataframes.io import read_table, write_rows


@pytest.mark.offline
def test_write_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "data.csv")
    write_rows(path, ["sku", "qty"], [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}])
    out = read_table(path)
    assert out["columns"] == ["sku", "qty"]
    assert out["rows"] == [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]


@pytest.mark.offline
def test_write_preserves_column_order_and_zero_rows(tmp_path):
    path = str(tmp_path / "empty.csv")
    write_rows(path, ["b", "a"], [])
    text = open(path, encoding="utf-8").read().splitlines()
    assert text[0] == "b,a"          # header preserved, order follows columns
    assert len(text) == 1            # header only, no data rows
    out = read_table(path)
    assert out["columns"] == ["b", "a"]
    assert out["rows"] == []


@pytest.mark.offline
def test_write_only_declared_columns(tmp_path):
    path = str(tmp_path / "sel.csv")
    # rows may carry extra keys; only declared columns are written, in order
    write_rows(path, ["a"], [{"a": 1, "extra": 9}])
    out = read_table(path)
    assert out["columns"] == ["a"]
    assert out["rows"] == [{"a": 1}]
```

- [ ] **Step 2: Run test to verify it fails.** Run:

```bash
cd backend && python -m pytest tests/test_dataframes_io_editor.py -q
```

Expected: FAIL with `ImportError: cannot import name 'read_table'`.

- [ ] **Step 3: Implement.** In `backend/dataframes/io.py`, append after `dataset_preview`:

```python
def read_table(path: str, sheet: Optional[str] = None) -> dict:
    """Full-table load for the in-app editor: all columns + all rows as plain
    Python. Columns are preserved even when there are zero data rows."""
    fmt = _fmt_from_path(path)
    if fmt == "excel":
        target = sheet or (pd.ExcelFile(path).sheet_names[0])
        df = pd.read_excel(path, sheet_name=target)
    elif fmt == "json":
        df = pd.read_json(path)
    elif fmt == "parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    return {"columns": list(df.columns), "rows": _to_records(df)}


def write_rows(path: str, columns: list[str], rows: list[dict],
               fmt: str = "csv") -> None:
    """Write an edited table to disk — the ONE place edits get persisted.
    Column order follows `columns`; a header is written even with zero rows.
    Cells are written verbatim (callers sanitize before passing them in)."""
    df = pd.DataFrame([{c: r.get(c) for c in columns} for r in rows], columns=columns)
    if fmt == "excel":
        df.to_excel(path, index=False)
    elif fmt == "json":
        df.to_json(path, orient="records")
    elif fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
```

- [ ] **Step 4: Run tests to verify they pass.** Run:

```bash
cd backend && python -m pytest tests/test_dataframes_io_editor.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Verify pandas boundary still clean.** Run:

```bash
cd backend && python -m pytest tests/test_no_pandas_in_backend.py -q
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/dataframes/io.py backend/tests/test_dataframes_io_editor.py
git commit -m "feat(dataframes): read_table + write_rows editor boundary helpers"
```

---

### Task 3: Service layer — `load_editable_table` + `save_edited_as_new`

**Files:**
- Modify: `backend/datasources/service.py` (add both functions after `get_preview`)
- Test: covered end-to-end in Task 5 (endpoint tests exercise the service).

**Interfaces:**
- Consumes: `settings.dataset_editor_max_rows`, `settings.dataset_editor_max_mb`; `read_table`, `write_rows` from Task 2; `csv_safe`; `paths.dataset_dir`; `get_source`, `_public`, `generate_id`.
- Produces:
  - `load_editable_table(tenant_id: str, source_id: str) -> dict` → `{"columns": list[str], "rows": list[dict]}`. Raises `ValueError` for missing source, SQL source, missing file, or over-guard (message contains `"too large to edit online"`).
  - `save_edited_as_new(tenant_id: str, user_id: str, source_id: str, name: Optional[str], columns: list[str], rows: list[dict]) -> dict` → public dataset dict. Raises `ValueError` for missing/SQL source or empty columns.

- [ ] **Step 1: Implement `load_editable_table`.** In `backend/datasources/service.py`, add after `get_preview` (before `# ── CRUD ──`):

```python
# ── In-app editor ────────────────────────────────────────────────────────────

def load_editable_table(tenant_id: str, source_id: str) -> dict:
    """Load a file dataset's full table for in-browser editing. Enforces the
    size guard from the STORED row_count/size_bytes before touching the file, so
    an over-threshold dataset is never read into memory. SQL sources rejected."""
    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")
    if src.get("source_type") == "sql":
        raise ValueError("Only file datasets are editable")

    max_rows = settings.dataset_editor_max_rows
    max_bytes = settings.dataset_editor_max_mb * 1024 * 1024
    row_count = src.get("row_count")
    size_bytes = src.get("size_bytes")
    if row_count is not None and row_count > max_rows:
        raise ValueError(
            f"Dataset too large to edit online ({row_count} rows exceeds the "
            f"{max_rows}-row limit). Edit it offline and re-upload."
        )
    if size_bytes is not None and size_bytes > max_bytes:
        raise ValueError(
            f"Dataset too large to edit online "
            f"({size_bytes / 1024 / 1024:.1f} MB exceeds the "
            f"{settings.dataset_editor_max_mb} MB limit). Edit it offline and re-upload."
        )

    file_path = src.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise ValueError("File not found on disk. Please re-upload.")

    from backend.dataframes.io import read_table
    return read_table(file_path)
```

- [ ] **Step 2: Implement `save_edited_as_new`.** Immediately below `load_editable_table`, add:

```python
def save_edited_as_new(
    tenant_id: str,
    user_id: str,
    source_id: str,
    name: Optional[str],
    columns: list[str],
    rows: list[dict],
) -> dict:
    """Write the edited table as a NEW CSV dataset with `parent_id` = source id.
    The original dataset row and file are never touched. Every cell (and header)
    is passed through the CSV formula-injection guard before it is written."""
    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")
    if src.get("source_type") == "sql":
        raise ValueError("Only file datasets are editable")
    if not columns or not isinstance(columns, list):
        raise ValueError("At least one column is required")

    from backend.utils.csv_safe import csv_safe
    from backend.dataframes.io import write_rows
    from backend.storage import paths

    safe_columns = [csv_safe(c) for c in columns]
    safe_rows = [
        {safe_columns[i]: csv_safe(row.get(columns[i])) for i in range(len(columns))}
        for row in rows
    ]

    new_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, new_id)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to .tmp, verify, then swap — a partial write never
    # leaves a corrupt dataset (mirrors replace_file_source).
    file_path = dst_dir / "data.csv"
    tmp_path = dst_dir / "data.csv.tmp"
    try:
        write_rows(str(tmp_path), safe_columns, safe_rows, fmt="csv")
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise ValueError("File write verification failed.")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    tmp_path.replace(file_path)

    size_bytes = file_path.stat().st_size
    row_count = len(rows)
    col_count = len(columns)
    display_name = name.strip() if (name and name.strip()) else f"{src.get('name')} (editado)"

    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, original_filename, file_type, file_path,
            size_bytes, row_count, column_count, source_type, connection_status,
            parent_id, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'csv',%s,%s,%s,%s,'file','connected',%s,%s,NOW(),NOW())""",
        (
            new_id, tenant_id, display_name, src.get("description"),
            f"{display_name}.csv", str(file_path),
            size_bytes, row_count, col_count, source_id, user_id,
        ),
    )
    return _public(get_source(tenant_id, new_id))
```

- [ ] **Step 3: Verify no pandas import crept into the service.** Run:

```bash
cd backend && python -m pytest tests/test_no_pandas_in_backend.py -q
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/datasources/service.py
git commit -m "feat(datasources): load_editable_table + save_edited_as_new service"
```

---

### Task 4: API endpoints — `GET /{id}/edit-table` + `POST /{id}/save-as-new`

**Files:**
- Modify: `backend/api/v1/datasources.py` (import `require_analyst_or_above`, add a request model + two endpoints)
- Test: Task 5.

**Interfaces:**
- Consumes: `svc.load_editable_table`, `svc.save_edited_as_new` from Task 3.
- Produces:
  - `GET /api/v1/data-sources/{source_id}/edit-table` → `ok({"columns", "rows"})`; 400 on ValueError; 404 if not found; auth `get_current_user`.
  - `POST /api/v1/data-sources/{source_id}/save-as-new` body `{name?: str, columns: list[str], rows: list[dict]}` → `ok(new_dataset)`; 400 on ValueError; 404 if not found; auth `require_analyst_or_above`.

- [ ] **Step 1: Update the auth import.** In `backend/api/v1/datasources.py`, change the guards import line to:

```python
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
```

- [ ] **Step 2: Add the request model.** After `class RenameSourceRequest(...)` add:

```python
class SaveAsNewRequest(BaseModel):
    name:    Optional[str] = None
    columns: list[str]
    rows:    list[dict]

    @field_validator("columns")
    @classmethod
    def _cols_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one column is required")
        return v
```

- [ ] **Step 3: Add the endpoints.** After the Preview endpoint (`get_preview`) add:

```python
# ── In-app editor ────────────────────────────────────────────────────────────

@router.get("/{source_id}/edit-table")
def edit_table(
    source_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        result = svc.load_editable_table(user.tenant_id, source_id)
        return ok(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{source_id}/save-as-new")
def save_as_new(
    source_id: str,
    body: SaveAsNewRequest,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _ds_or_404(user.tenant_id, source_id)
    try:
        new_ds = svc.save_edited_as_new(
            user.tenant_id, user.user_id, source_id, body.name, body.columns, body.rows
        )
        return ok(new_ds)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Smoke-check the app imports.** Run:

```bash
cd backend && python -c "from backend.main import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/datasources.py
git commit -m "feat(api): dataset editor edit-table + save-as-new endpoints"
```

---

### Task 5: Backend endpoint tests (full spec Testing section)

**Files:**
- Test: `backend/tests/test_datasources_editor.py` (new)

**Interfaces:**
- Consumes: endpoints from Task 4; conftest fixtures `client`, `auth_headers`, `analyst_headers`, `viewer_headers`, `make_tenant_user_headers`, `test_tenant`.

- [ ] **Step 1: Write the tests.** Create `backend/tests/test_datasources_editor.py`:

```python
"""End-to-end tests for the in-app dataset editor (edit-table + save-as-new).

Asserts DB + on-disk state directly, a viewer/analyst permission pair, tenant
isolation, CSV-injection neutralization, and SQL-source rejection.
"""
from pathlib import Path

from backend.db.connection import query_one, query


def _upload_csv(client, headers, content: bytes, name="editor_src.csv"):
    resp = client.post(
        "/api/v1/data-sources/file",
        files={"file": (name, content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


SMALL_CSV = b"sku,qty,note\nA,1,hello\nB,2,world\nC,3,foo\n"


def test_edit_table_loads_small_file(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["columns"] == ["sku", "qty", "note"]
    assert len(data["rows"]) == 3
    assert data["rows"][0] == {"sku": "A", "qty": 1, "note": "hello"}


def test_edit_table_rejects_over_row_guard(client, analyst_headers):
    from backend.db.connection import execute
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    # Force the stored row_count over the guard without a huge file: the guard
    # must trip from stored stats BEFORE the (small) file would be read.
    execute("UPDATE datasets SET row_count=%s WHERE id=%s", (10_000_000, src["id"]))
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 400
    assert "too large to edit online" in resp.json()["detail"]


def test_edit_table_rejects_over_byte_guard(client, analyst_headers):
    from backend.db.connection import execute
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    execute("UPDATE datasets SET size_bytes=%s WHERE id=%s", (999_000_000, src["id"]))
    resp = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=analyst_headers)
    assert resp.status_code == 400
    assert "too large to edit online" in resp.json()["detail"]


def test_save_as_new_creates_distinct_dataset(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    orig_path = query_one("SELECT file_path FROM datasets WHERE id=%s", (src["id"],))["file_path"]
    orig_bytes = Path(orig_path).read_bytes()

    body = {
        "name": "My Edited Set",
        "columns": ["sku", "qty", "note"],
        "rows": [
            {"sku": "A", "qty": 1, "note": "hello"},
            {"sku": "B", "qty": 2, "note": "world"},
            {"sku": "C", "qty": 3, "note": "foo"},
        ],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_ds = resp.json()["data"]
    assert new_ds["id"] != src["id"]

    # DB: new row exists with parent_id = source id, correct row_count
    db_row = query_one("SELECT parent_id, row_count, file_path, name FROM datasets WHERE id=%s", (new_ds["id"],))
    assert db_row["parent_id"] == src["id"]
    assert db_row["row_count"] == 3
    assert db_row["name"] == "My Edited Set"

    # On disk: new file exists and is a distinct path
    assert db_row["file_path"] != orig_path
    assert Path(db_row["file_path"]).exists()

    # Original untouched: row unchanged AND file bytes unchanged
    orig_after = query_one("SELECT row_count, parent_id FROM datasets WHERE id=%s", (src["id"],))
    assert orig_after["parent_id"] is None
    assert Path(orig_path).read_bytes() == orig_bytes


def test_save_as_new_persists_edits(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    # changed cell (A qty 1->99), deleted row (C removed), renamed column
    # (note->comment), dropped column (qty gone from columns list).
    body = {
        "name": "edited",
        "columns": ["sku", "comment"],
        "rows": [
            {"sku": "A", "comment": "changed"},
            {"sku": "B", "comment": "world"},
        ],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]

    read = client.get(f"/api/v1/data-sources/{new_id}/edit-table", headers=analyst_headers)
    data = read.json()["data"]
    assert data["columns"] == ["sku", "comment"]          # renamed + dropped column
    assert len(data["rows"]) == 2                          # deleted row gone
    assert data["rows"][0] == {"sku": "A", "comment": "changed"}  # changed cell


def test_save_as_new_permission_pair(client, viewer_headers, analyst_headers):
    # Source uploaded by analyst; both users share the same tenant via test_tenant.
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    before = query_one("SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=(SELECT tenant_id FROM datasets WHERE id=%s)", (src["id"],))["c"]

    body = {"name": "x", "columns": ["sku"], "rows": [{"sku": "A"}]}

    # Viewer denied — 403 and no new row
    denied = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=viewer_headers)
    assert denied.status_code == 403
    after_denied = query_one("SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=(SELECT tenant_id FROM datasets WHERE id=%s)", (src["id"],))["c"]
    assert after_denied == before

    # Analyst allowed — success and a new row present
    allowed = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert allowed.status_code == 200, allowed.text
    new_id = allowed.json()["data"]["id"]
    assert query_one("SELECT id FROM datasets WHERE id=%s", (new_id,)) is not None


def test_tenant_isolation(client, analyst_headers, make_tenant_user_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)   # tenant A
    other = make_tenant_user_headers(role="analyst")        # tenant B

    r1 = client.get(f"/api/v1/data-sources/{src['id']}/edit-table", headers=other)
    assert r1.status_code == 404
    r2 = client.post(
        f"/api/v1/data-sources/{src['id']}/save-as-new",
        json={"name": "x", "columns": ["sku"], "rows": [{"sku": "A"}]},
        headers=other,
    )
    assert r2.status_code == 404


def test_csv_injection_sanitized(client, analyst_headers):
    src = _upload_csv(client, analyst_headers, SMALL_CSV)
    body = {
        "name": "danger",
        "columns": ["sku", "payload"],
        "rows": [{"sku": "A", "payload": "=cmd()|calc"}],
    }
    resp = client.post(f"/api/v1/data-sources/{src['id']}/save-as-new", json=body, headers=analyst_headers)
    assert resp.status_code == 200, resp.text
    new_path = query_one("SELECT file_path FROM datasets WHERE id=%s", (resp.json()["data"]["id"],))["file_path"]
    written = Path(new_path).read_text(encoding="utf-8")
    # The dangerous value is neutralized with a leading quote, so no raw "=cmd"
    assert "'=cmd()|calc" in written
    assert "\n=cmd" not in written and ",=cmd" not in written


def test_sql_source_rejected(client, analyst_headers):
    # Create a SQL source (no connection needed — editor rejects the type outright)
    created = client.post(
        "/api/v1/data-sources/sql",
        json={
            "name": "sqlsrc", "host": "localhost", "port": 5432,
            "database": "db", "username": "u", "password": "p", "engine": "postgresql",
        },
        headers=analyst_headers,
    )
    assert created.status_code == 200, created.text
    sid = created.json()["data"]["id"]

    r1 = client.get(f"/api/v1/data-sources/{sid}/edit-table", headers=analyst_headers)
    assert r1.status_code == 400
    assert "file datasets are editable" in r1.json()["detail"]

    r2 = client.post(
        f"/api/v1/data-sources/{sid}/save-as-new",
        json={"name": "x", "columns": ["a"], "rows": []},
        headers=analyst_headers,
    )
    assert r2.status_code == 400
```

- [ ] **Step 2: Run the tests.** Run:

```bash
cd backend && python -m pytest tests/test_datasources_editor.py -q
```

Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_datasources_editor.py
git commit -m "test(datasources): full editor spec suite (load/save/perm/tenant/injection/sql)"
```

---

### Task 6: Frontend — types, api client, i18n keys

**Files:**
- Modify: `Frontend/src/lib/types.ts` (add `parent_id` to `DataSource`, add `EditableTable`)
- Modify: `Frontend/src/lib/api.ts` (add `getEditableTable`, `saveDatasetAsNew`)
- Modify: `Frontend/src/i18n/translations.ts` (add editor keys to both `es` and `en`)

**Interfaces:**
- Produces: `getEditableTable(id: string) => Promise<EditableTable>`; `saveDatasetAsNew(id, body) => Promise<DataSource>`; type `EditableTable { columns: string[]; rows: Record<string, unknown>[] }`.

- [ ] **Step 1: Add types.** In `Frontend/src/lib/types.ts`, add `parent_id: string | null` to the `DataSource` interface (after `uploaded_by`), and after the `DataPreview` interface add:

```typescript
export interface EditableTable {
  columns: string[]
  rows:    Record<string, unknown>[]
}
```

- [ ] **Step 2: Add api functions.** In `Frontend/src/lib/api.ts`, add `EditableTable` to the type import from `./types`, and after `getDataSourcePreview` add:

```typescript
export const getEditableTable = (id: string) =>
  request<EditableTable>('GET', `/data-sources/${id}/edit-table`)

export const saveDatasetAsNew = (id: string, body: {
  name?: string; columns: string[]; rows: Record<string, unknown>[]
}) => request<DataSource>('POST', `/data-sources/${id}/save-as-new`, body)
```

- [ ] **Step 3: Add i18n keys.** In `Frontend/src/i18n/translations.ts`, in the `es` block near `'data.tab_replace_file'` add:

```typescript
    'data.tab_edit':                  'Editar',
    'data.editor_add_row':            'Añadir fila',
    'data.editor_delete_row':         'Eliminar fila',
    'data.editor_rename_column':      'Renombrar columna',
    'data.editor_drop_column':        'Eliminar columna',
    'data.editor_new_name':           'Nombre del nuevo dataset',
    'data.editor_save_as_new':        'Guardar como nuevo',
    'data.editor_saving':             'Guardando…',
    'data.editor_loading':            'Cargando tabla para editar…',
    'data.editor_too_large':          'Este archivo es demasiado grande para editar en línea. Edítalo fuera de línea y vuelve a subirlo.',
    'data.editor_saved':              'Dataset guardado como nuevo',
    'data.editor_save_failed':        'No se pudo guardar el dataset',
    'data.editor_rows_count':         'filas',
    'data.editor_new_column':         'Columna nueva',
```

And in the `en` block near the English `'data.tab_replace_file'` add:

```typescript
    'data.tab_edit':                  'Edit',
    'data.editor_add_row':            'Add row',
    'data.editor_delete_row':         'Delete row',
    'data.editor_rename_column':      'Rename column',
    'data.editor_drop_column':        'Drop column',
    'data.editor_new_name':           'New dataset name',
    'data.editor_save_as_new':        'Save as new',
    'data.editor_saving':             'Saving…',
    'data.editor_loading':            'Loading table for editing…',
    'data.editor_too_large':          'This file is too large to edit online. Edit it offline and re-upload.',
    'data.editor_saved':              'Dataset saved as new',
    'data.editor_save_failed':        'Could not save the dataset',
    'data.editor_rows_count':         'rows',
    'data.editor_new_column':         'New column',
```

- [ ] **Step 4: Typecheck.** Run from `Frontend/`:

```bash
node ./node_modules/typescript/bin/tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts Frontend/src/i18n/translations.ts
git commit -m "feat(frontend): editor types, api client, i18n keys"
```

---

### Task 7: Frontend — editable grid panel + Edit tab wiring

**Files:**
- Modify: `Frontend/src/app/data/page.tsx` (add a `DatasetEditorPanel` component; add an `edit` tab for file sources in `SourceDetail`)

**Interfaces:**
- Consumes: `getEditableTable`, `saveDatasetAsNew` from Task 6; `DataSource`, `EditableTable` types; `useToast`, `useLanguage`.

- [ ] **Step 1: Import the new api functions.** In `Frontend/src/app/data/page.tsx`, add `getEditableTable, saveDatasetAsNew` to the existing import from `@/lib/api`, and add `EditableTable` to the type import from `@/lib/types`. Add `Pencil, Columns, Plus as PlusIcon` are not needed — reuse existing `Plus`, `Trash2`, `Save`, `X`, `Edit2` icons already imported.

- [ ] **Step 2: Add the `DatasetEditorPanel` component.** In `Frontend/src/app/data/page.tsx`, add before `// ── Right panel: detail for a selected source ──`:

```tsx
// ── Dataset editor (edit as spreadsheet, save as new) ─────────────────────────
function DatasetEditorPanel({ source, onCreated }: {
 source: DataSource; onCreated: (s: DataSource) => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const [loading, setLoading] = useState(true)
 const [loadErr, setLoadErr] = useState<string | null>(null)
 const [columns, setColumns] = useState<string[]>([])
 const [rows, setRows] = useState<Record<string, unknown>[]>([])
 const [name, setName] = useState(`${source.name} (editado)`)
 const [saving, setSaving] = useState(false)

 useEffect(() => {
  let alive = true
  setLoading(true); setLoadErr(null)
  getEditableTable(source.id)
   .then(tbl => { if (alive) { setColumns(tbl.columns); setRows(tbl.rows) } })
   .catch((e: any) => { if (alive) setLoadErr(e.message || t('data.editor_too_large')) })
   .finally(() => { if (alive) setLoading(false) })
  return () => { alive = false }
 }, [source.id]) // eslint-disable-line

 const setCell = (ri: number, col: string, val: string) =>
  setRows(rs => rs.map((r, i) => i === ri ? { ...r, [col]: val } : r))
 const addRow = () =>
  setRows(rs => [...rs, Object.fromEntries(columns.map(c => [c, '']))])
 const deleteRow = (ri: number) =>
  setRows(rs => rs.filter((_, i) => i !== ri))
 const dropColumn = (col: string) => {
  setColumns(cs => cs.filter(c => c !== col))
  setRows(rs => rs.map(r => { const { [col]: _drop, ...rest } = r; return rest }))
 }
 const renameColumn = (col: string) => {
  const next = window.prompt(t('data.editor_rename_column'), col)
  if (!next || next === col || columns.includes(next)) return
  setColumns(cs => cs.map(c => c === col ? next : c))
  setRows(rs => rs.map(r => { const { [col]: v, ...rest } = r; return { ...rest, [next]: v } }))
 }
 const addColumn = () => {
  const nm = window.prompt(t('data.editor_new_column'), '')
  if (!nm || columns.includes(nm)) return
  setColumns(cs => [...cs, nm])
  setRows(rs => rs.map(r => ({ ...r, [nm]: '' })))
 }

 const save = async () => {
  setSaving(true)
  try {
   const created = await saveDatasetAsNew(source.id, { name: name.trim() || undefined, columns, rows })
   addToast(t('data.editor_saved'), created.name, 'success')
   onCreated(created)
  } catch (e: any) {
   addToast(t('data.editor_save_failed'), e.message, 'error')
  } finally { setSaving(false) }
 }

 if (loading) return (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '40px 0', justifyContent: 'center' }}>
   <Spinner size={20} /> <span style={{ color: C.muted }}>{t('data.editor_loading')}</span>
  </div>
 )
 if (loadErr) return (
  <div style={{ background: C.redDim, border: `1px solid ${C.red}30`, borderRadius: 8,
   padding: '14px 16px', color: C.red, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
   <AlertTriangle size={14} /> {loadErr}
  </div>
 )

 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
   <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
    <input value={name} onChange={e => setName(e.target.value)}
     placeholder={t('data.editor_new_name')}
     style={{ flex: 1, minWidth: 200, background: C.surface, border: `1px solid ${C.border2}`,
      borderRadius: 8, padding: '8px 12px', color: C.text, fontSize: 13, outline: 'none' }} />
    <button onClick={addRow}
     style={{ padding: '8px 14px', borderRadius: 8, background: 'transparent',
      border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
     <Plus size={12} /> {t('data.editor_add_row')}
    </button>
    <button onClick={addColumn}
     style={{ padding: '8px 14px', borderRadius: 8, background: 'transparent',
      border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
     <Plus size={12} /> {t('data.editor_new_column')}
    </button>
    <button onClick={save} disabled={saving || !columns.length}
     style={{ padding: '8px 18px', borderRadius: 8, background: C.green, border: 'none',
      color: '#fff', fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer',
      opacity: saving || !columns.length ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
     {saving ? <Spinner size={12} /> : <Save size={12} />} {saving ? t('data.editor_saving') : t('data.editor_save_as_new')}
    </button>
   </div>
   <div style={{ color: C.muted, fontSize: 12 }}>{rows.length} {t('data.editor_rows_count')}</div>
   <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 420, borderRadius: 8, border: `1px solid ${C.border}` }}>
    <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
     <thead>
      <tr>
       <th style={{ padding: '6px 8px', background: 'var(--surface-2)', borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0 }} />
       {columns.map(c => (
        <th key={c} style={{ padding: '6px 10px', textAlign: 'left', whiteSpace: 'nowrap',
         background: 'var(--surface-2)', color: C.muted, fontWeight: 600, fontSize: 11,
         borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0 }}>
         <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {c}
          <button onClick={() => renameColumn(c)} title={t('data.editor_rename_column')}
           style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer', padding: 0 }}>
           <Edit2 size={11} />
          </button>
          <button onClick={() => dropColumn(c)} title={t('data.editor_drop_column')}
           style={{ background: 'transparent', border: 'none', color: C.red, cursor: 'pointer', padding: 0 }}>
           <X size={11} />
          </button>
         </span>
        </th>
       ))}
      </tr>
     </thead>
     <tbody>
      {rows.map((row, ri) => (
       <tr key={ri} style={{ background: ri % 2 === 0 ? C.card : C.surface }}>
        <td style={{ padding: '2px 6px', borderBottom: `1px solid ${C.border}` }}>
         <button onClick={() => deleteRow(ri)} title={t('data.editor_delete_row')}
          style={{ background: 'transparent', border: 'none', color: C.red, cursor: 'pointer', padding: 2 }}>
          <Trash2 size={12} />
         </button>
        </td>
        {columns.map(c => (
         <td key={c} style={{ padding: 0, borderBottom: `1px solid ${C.border}` }}>
          <input value={row[c] == null ? '' : String(row[c])}
           onChange={e => setCell(ri, c, e.target.value)}
           style={{ width: '100%', minWidth: 90, background: 'transparent', border: 'none',
            padding: '6px 10px', color: C.text, fontSize: 12, outline: 'none', boxSizing: 'border-box' }} />
         </td>
        ))}
       </tr>
      ))}
     </tbody>
    </table>
   </div>
  </div>
 )
}
```

- [ ] **Step 3: Wire the Edit tab into `SourceDetail`.** In the `SourceDetail` component, extend the tab-state union and the file-source `tabs` array and content.

Change the `tab` state type to include `'edit'`:

```tsx
 const [tab, setTab] = useState<'preview' | 'analysis' | 'edit' | 'sql-editor' | 'connection'>('preview')
```

In the `tabs` computation, change the file-source branch to include the Edit tab:

```tsx
 const tabs = isSql
  ? [{ id: 'sql-editor', label: t('data.tab_query_editor') }, { id: 'connection', label: t('data.tab_connection') }]
  : [{ id: 'preview', label: t('data.tab_data_preview') }, { id: 'edit', label: t('data.tab_edit') }, { id: 'analysis', label: t('data.tab_analysis') }, { id: 'connection', label: t('data.tab_replace_file') }]
```

After the Analysis tab content block (the `{tab === 'analysis' && !isSql && (...)}` block), add the Edit tab content:

```tsx
 {/* Edit tab */}
 {tab === 'edit' && !isSql && (
  <DatasetEditorPanel
   source={source}
   onCreated={(created) => { onUpdated(created) }}
  />
 )}
```

- [ ] **Step 4: Typecheck.** Run from `Frontend/`:

```bash
node ./node_modules/typescript/bin/tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add Frontend/src/app/data/page.tsx
git commit -m "feat(frontend): editable dataset grid + Edit tab (save-as-new)"
```

---

### Task 8: Full verification

- [ ] **Step 1: Run the full backend suite.** Run:

```bash
cd backend && python -m pytest tests/ -q
```

Expected: baseline (1232 passed, 19 skipped) plus the new tests (3 io + 9 editor) all passing.

- [ ] **Step 2: Frontend typecheck.** Run from `Frontend/`:

```bash
node ./node_modules/typescript/bin/tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Final commit if anything outstanding** (plan doc, etc.):

```bash
git add -A && git commit -m "chore: dataset editor plan + finalization" || true
```

---

## Self-Review Notes

- **Spec coverage:** parent_id migration (T1) · read_table/write_rows (T2) · load_editable_table with size guard from stored stats + SQL reject (T3) · save_edited_as_new sanitize+atomic+parent_id+"(editado)" default (T3) · edit-table GET + save-as-new POST analyst-gated (T4) · every Testing-section case (T5) · frontend grid + save + i18n + too-large (T6, T7). All covered.
- **Size guard threshold:** `dataset_editor_max_rows = 50_000`, `dataset_editor_max_mb = 10` (config, tunable). Rationale: comfortably covers the common "a few thousand rows" case while not promising the 50 MB upload ceiling.
- **CSV output only:** `save_edited_as_new` always writes `data.csv` with `fmt="csv"`.
- **Original immutability:** new id + new dir + new file always; test asserts original row and original file bytes byte-for-byte unchanged.

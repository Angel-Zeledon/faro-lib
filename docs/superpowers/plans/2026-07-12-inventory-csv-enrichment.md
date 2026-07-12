# Inventory CSV Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the inventory CSV import and edit form with five catalog fields (`precio_venta`, `categoria`, `marca`, `unidad_medida`, `codigo_barras`), add a downloadable CSV template endpoint, and fix bulk import silently dropping `proveedor`/`notas`.

**Architecture:** Five nullable columns are added to `inventory_stock` via additive migrations. The single write path (`upsert_stock`) and both import surfaces (direct `PATCH`/`PUT` and CSV `bulk_import`) accept them. A new read-only endpoint serves the canonical template. The frontend gains a "download template" button and edit-form inputs. No new business logic (no margin, no filters) — fields are stored, edited, and exported only.

**Tech Stack:** Python 3 / FastAPI / psycopg2 (backend), pytest (tests), Next.js 14 / React / TypeScript (frontend).

## Global Constraints

- Backend is pure orchestration — no ML/pandas business logic (these are CRUD/schema changes in `backend/inventory/` + `backend/api/v1/`).
- New columns are nullable; no destructive data change. Added with `ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS`, appended to the `_MIGRATIONS` list in `backend/db/migrations.py`.
- `precio_venta` validates `>= 0`; the four text fields are free strings.
- Canonical template column order (exact): `sku, display_name, categoria, marca, unidad_medida, codigo_barras, stock_actual, stock_minimo, lead_time_dias, costo_unitario, precio_venta, moq, proveedor, notas`.
- Tests follow `TESTING_GUIDELINES.md`: assert persisted DB state with direct queries (not just HTTP 200); every mutating endpoint gets a permission pair (viewer 403 + state unchanged, analyst success).
- Backend tests need local Postgres on :5544; run from `backend/` with `backend/.venv/Scripts/python.exe -m pytest tests/... -q`. Frontend typecheck from `Frontend/`: `npx tsc --noEmit`. Do NOT run `npm run build` / `next dev`.
- Spanish user-facing copy; new strings go in BOTH `es` and `en` blocks of `Frontend/src/i18n/translations.ts`.

---

### Task 1: Schema + direct-write persistence of the five fields

Add the columns, accept them in the Pydantic models and `upsert_stock`, and return them from `get_inventory_status`.

**Files:**
- Modify: `backend/db/migrations.py` (append to `_MIGRATIONS`, after the `add_service_level_to_inventory_stock` entry ~line 368)
- Modify: `backend/inventory/service.py` (`upsert_stock` `allowed` set ~line 25; `get_inventory_status` item dict ~line 401)
- Modify: `backend/api/v1/inventory.py` (`StockUpsert` ~line 31, `StockPatch` ~line 42)
- Test: `backend/tests/test_inventory.py` (add near the CRUD tests, e.g. after `test_patch_partial_update` ~line 109)

**Interfaces:**
- Produces: `inventory_stock` columns `precio_venta FLOAT`, `categoria TEXT`, `marca TEXT`, `unidad_medida TEXT`, `codigo_barras TEXT`; `upsert_stock` accepts these keys; `get_inventory_status` items include them.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_inventory.py` (mirror the existing CRUD tests' use of `client`/`auth_headers`/`viewer_headers`):

```python
    def test_patch_persists_catalog_fields(self, client, auth_headers, viewer_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"stock_actual": 10, "lead_time_dias": 7},
                   headers=auth_headers)
        payload = {"precio_venta": 19.99, "categoria": "Bebidas",
                   "marca": "AguaPura", "unidad_medida": "caja",
                   "codigo_barras": "7501234567890"}
        # viewer denied + state unchanged
        vr = client.patch(f"/api/v1/inventory/stock/{sku}", json=payload, headers=viewer_headers)
        assert vr.status_code == 403
        from backend.db.connection import query_one
        row0 = query_one("SELECT precio_venta FROM inventory_stock WHERE sku = %s", (sku,))
        assert row0["precio_venta"] is None
        # analyst succeeds + DB reflects every field
        r = client.patch(f"/api/v1/inventory/stock/{sku}", json=payload, headers=auth_headers)
        assert r.status_code == 200
        row = query_one(
            "SELECT precio_venta, categoria, marca, unidad_medida, codigo_barras "
            "FROM inventory_stock WHERE sku = %s", (sku,))
        assert float(row["precio_venta"]) == 19.99
        assert row["categoria"] == "Bebidas"
        assert row["marca"] == "AguaPura"
        assert row["unidad_medida"] == "caja"
        assert row["codigo_barras"] == "7501234567890"
```

If `_sku()` / `viewer_headers` aren't already used in this file, mirror whatever the neighbouring CRUD tests (`test_upsert_creates_sku`, `test_patch_partial_update`) use for a unique SKU and the viewer fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k patch_persists_catalog -v`
Expected: FAIL (columns don't exist / fields rejected — e.g. `psycopg2` UndefinedColumn or the values are dropped so the assert fails).

- [ ] **Step 3: Add migrations**

In `backend/db/migrations.py`, immediately after the `add_service_level_to_inventory_stock` tuple (~line 368) add:

```python
    ("add_precio_venta_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS precio_venta FLOAT"),
    ("add_categoria_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS categoria TEXT"),
    ("add_marca_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS marca TEXT"),
    ("add_unidad_medida_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS unidad_medida TEXT"),
    ("add_codigo_barras_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS codigo_barras TEXT"),
```

- [ ] **Step 4: Accept fields in `upsert_stock`**

In `backend/inventory/service.py`, extend the `allowed` set in `upsert_stock` (~line 25) to include the five new keys:

```python
    allowed = {
        "display_name", "stock_actual", "stock_minimo",
        "lead_time_dias", "costo_unitario", "moq", "proveedor", "notas",
        "service_level",
        "precio_venta", "categoria", "marca", "unidad_medida", "codigo_barras",
    }
```

- [ ] **Step 5: Return fields from `get_inventory_status`**

In `backend/inventory/service.py`, in the item dict appended inside `get_inventory_status` (~line 401, alongside `"proveedor"`/`"notas"`), add:

```python
            "precio_venta":       float(stock["precio_venta"]) if stock and stock.get("precio_venta") is not None else None,
            "categoria":          stock.get("categoria") if stock else None,
            "marca":              stock.get("marca") if stock else None,
            "unidad_medida":      stock.get("unidad_medida") if stock else None,
            "codigo_barras":      stock.get("codigo_barras") if stock else None,
```

- [ ] **Step 6: Add fields to the Pydantic models**

In `backend/api/v1/inventory.py`, add to BOTH `StockUpsert` (~line 31) and `StockPatch` (~line 42):

```python
    precio_venta:   Optional[float] = Field(default=None, ge=0)
    categoria:      Optional[str]   = None
    marca:          Optional[str]   = None
    unidad_medida:  Optional[str]   = None
    codigo_barras:  Optional[str]   = None
```

Also add `proveedor: Optional[str] = None` and `notas: Optional[str] = None` to `StockPatch` (it currently lacks them — see Task 2 for why this matters; adding them here is required so `PATCH` and the bulk validator accept them).

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k patch_persists_catalog -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/db/migrations.py backend/inventory/service.py backend/api/v1/inventory.py backend/tests/test_inventory.py
git commit -m "feat(inventory): add catalog fields (precio_venta, categoria, marca, unidad, codigo_barras)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: CSV bulk import parses the new fields (and fixes proveedor/notas drop)

**Files:**
- Modify: `backend/api/v1/inventory.py` (`bulk_import` ~line 104-168; docstring ~line 111)
- Modify: `backend/inventory/service.py` (`_DATASET_STOCK_FLOAT_COLS` / `_DATASET_STOCK_STR_COLS` ~line 78-81)
- Test: `backend/tests/test_inventory.py` (in the bulk-import test group, near `test_bulk_import_creates_skus` ~line 172)

**Interfaces:**
- Consumes: `StockPatch` now carrying `precio_venta`/`categoria`/`marca`/`unidad_medida`/`codigo_barras`/`proveedor`/`notas` (Task 1 Step 6).

- [ ] **Step 1: Write the failing test**

```python
    def test_bulk_import_persists_catalog_and_proveedor(self, client, auth_headers):
        sku = _sku()
        csv_text = (
            "sku,stock_actual,precio_venta,categoria,marca,unidad_medida,codigo_barras,proveedor,notas\n"
            f"{sku},50,12.5,Lacteos,Alpina,litro,7700000000001,Distribuidora Sur,fragil\n"
        )
        r = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_text.encode("utf-8"), "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 200
        from backend.db.connection import query_one
        row = query_one(
            "SELECT stock_actual, precio_venta, categoria, marca, unidad_medida, "
            "codigo_barras, proveedor, notas FROM inventory_stock WHERE sku = %s", (sku,))
        assert float(row["stock_actual"]) == 50
        assert float(row["precio_venta"]) == 12.5
        assert row["categoria"] == "Lacteos"
        assert row["marca"] == "Alpina"
        assert row["unidad_medida"] == "litro"
        assert row["codigo_barras"] == "7700000000001"
        assert row["proveedor"] == "Distribuidora Sur"   # regression: was silently dropped
        assert row["notas"] == "fragil"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k bulk_import_persists_catalog -v`
Expected: FAIL — `precio_venta`/`categoria`/etc are None and `proveedor` is None (dropped by the old `StockPatch`).

- [ ] **Step 3: Parse the new fields in `bulk_import`**

In `backend/api/v1/inventory.py`, in the per-row parse loop of `bulk_import` (~line 138-147), extend the string and float field lists:

```python
        for fld in ("display_name", "proveedor", "notas",
                    "categoria", "marca", "unidad_medida", "codigo_barras"):
            if fld in row:
                parsed[fld] = row[fld]
        for fld in ("stock_actual", "stock_minimo", "costo_unitario", "moq", "precio_venta"):
            v = _float(fld)
            if v is not None:
                parsed[fld] = v
```

Update the docstring (~line 111) to list the full accepted column set:

```python
    Expected columns (case-insensitive): sku, display_name, categoria, marca,
    unidad_medida, codigo_barras, stock_actual, stock_minimo, lead_time_dias,
    costo_unitario, precio_venta, moq, proveedor, notas
```

(The rows still validate through `StockPatch`, which now carries all these fields after Task 1 Step 6, so `model_dump(exclude_none=True)` keeps them.)

- [ ] **Step 4: Seed the fields from training-dataset uploads**

In `backend/inventory/service.py` (~line 78-81), add the new columns to the dataset-recognition sets so a training upload carrying them also seeds stock:

```python
_DATASET_STOCK_FLOAT_COLS = {"stock_actual", "stock_minimo", "costo_unitario", "moq", "service_level", "precio_venta"}
_DATASET_STOCK_INT_COLS   = {"lead_time_dias"}
_DATASET_STOCK_STR_COLS   = {"proveedor", "notas", "display_name", "categoria", "marca", "unidad_medida", "codigo_barras"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k "bulk_import" -v`
Expected: PASS (new test + existing bulk-import tests).

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/inventory/service.py backend/tests/test_inventory.py
git commit -m "feat(inventory): bulk CSV import accepts catalog fields; fix dropped proveedor/notas

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Downloadable template endpoint

**Files:**
- Modify: `backend/api/v1/inventory.py` (add a `GET /template.csv` route; place it near `bulk_import` ~line 104)
- Test: `backend/tests/test_inventory.py`
- Modify: `Frontend/src/lib/api.ts` (add `downloadInventoryTemplate`)

**Interfaces:**
- Produces: `GET /inventory/template.csv` → `text/csv` attachment with the canonical header row + one example data row.
- Produces: `downloadInventoryTemplate()` in `api.ts`.

- [ ] **Step 1: Write the failing test**

```python
    def test_template_csv_has_canonical_header(self, client, auth_headers):
        r = client.get("/api/v1/inventory/template.csv", headers=auth_headers)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        text = r.content.decode("utf-8-sig")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        header = lines[0].split(",")
        expected = ["sku", "display_name", "categoria", "marca", "unidad_medida",
                    "codigo_barras", "stock_actual", "stock_minimo", "lead_time_dias",
                    "costo_unitario", "precio_venta", "moq", "proveedor", "notas"]
        assert header == expected
        assert len(lines) >= 2          # header + at least one example row
        assert len(lines[1].split(",")) == len(expected)   # example row is parseable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k template_csv -v`
Expected: FAIL with 404 (route not defined).

- [ ] **Step 3: Add the endpoint**

In `backend/api/v1/inventory.py`, near `bulk_import` add (uses the already-imported `io`/`csv` and `Response`; if `Response` isn't imported, import it from `fastapi`):

```python
_TEMPLATE_COLUMNS = [
    "sku", "display_name", "categoria", "marca", "unidad_medida", "codigo_barras",
    "stock_actual", "stock_minimo", "lead_time_dias", "costo_unitario",
    "precio_venta", "moq", "proveedor", "notas",
]
_TEMPLATE_EXAMPLE = [
    "SKU001", "Agua 600ml", "Bebidas", "AguaPura", "caja", "7501234567890",
    "120", "20", "7", "3.50", "5.90", "12", "Distribuidora Sur", "producto de ejemplo",
]


@router.get("/template.csv")
def download_template(user: CurrentUser = Depends(get_current_user)):
    """Canonical inventory import template: header row + one example row."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_TEMPLATE_COLUMNS)
    w.writerow(_TEMPLATE_EXAMPLE)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="plantilla_inventario.csv"'},
    )
```

Verify `Response` is imported at the top of the file (`from fastapi import ..., Response`); add it if missing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -k template_csv -v`
Expected: PASS.

- [ ] **Step 5: Add the frontend API client**

In `Frontend/src/lib/api.ts`, near `exportInventoryPO` (~line 612), add a downloader that mirrors the existing blob-download pattern:

```ts
export const downloadInventoryTemplate = async () => {
  const token = getToken()
  const res = await fetch(`${BASE}/inventory/template.csv`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('No se pudo descargar la plantilla')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = 'plantilla_inventario.csv'; a.click()
  URL.revokeObjectURL(url)
}
```

Match `BASE` / `getToken()` to how the sibling functions in this file reference them.

- [ ] **Step 6: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_inventory.py Frontend/src/lib/api.ts
git commit -m "feat(inventory): downloadable CSV import template endpoint + client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — template button + edit-form fields

**Files:**
- Modify: `Frontend/src/app/inventory/page.tsx` (`EditState` ~line 435; `rowToEdit` ~line 436; `commitEdit` upsert call ~line 797; edit-row inputs ~line 1355-1406; header actions ~line 833)
- Modify: `Frontend/src/lib/types.ts` (`InventoryStatusItem` — add the five optional fields)
- Modify: `Frontend/src/i18n/translations.ts` (new strings, `es` + `en`)

**Interfaces:**
- Consumes: `downloadInventoryTemplate` (Task 3), `upsertInventoryStock` (existing).

- [ ] **Step 1: Extend the item type**

In `Frontend/src/lib/types.ts`, find `InventoryStatusItem` and add:

```ts
  precio_venta?:   number | null
  categoria?:      string | null
  marca?:          string | null
  unidad_medida?:  string | null
  codigo_barras?:  string | null
```

- [ ] **Step 2: Extend `EditState` + `rowToEdit`**

In `Frontend/src/app/inventory/page.tsx`, extend the `EditState` interface (line 435) with five string fields, and `rowToEdit` (line 436-438) to seed them:

```ts
interface EditState { stock_actual: string; lead_time_dias: string; costo_unitario: string; moq: string; proveedor: string; display_name: string; service_level: string; precio_venta: string; categoria: string; marca: string; unidad_medida: string; codigo_barras: string }
function rowToEdit(item: InventoryStatusItem): EditState {
 return { stock_actual: String(item.stock_actual ?? ''), lead_time_dias: String(item.lead_time_dias ?? 15), costo_unitario: String(item.costo_unitario ?? ''), moq: String(item.moq ?? 1), proveedor: item.proveedor ?? '', display_name: item.display_name ?? '', service_level: String(item.service_level ?? 0.95), precio_venta: String(item.precio_venta ?? ''), categoria: item.categoria ?? '', marca: item.marca ?? '', unidad_medida: item.unidad_medida ?? '', codigo_barras: item.codigo_barras ?? '' }
}
```

- [ ] **Step 3: Send the fields on save**

In `commitEdit` (~line 797), extend the `upsertInventoryStock` payload:

```ts
  await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, stock_actual: parseFloat(editState.stock_actual) || 0, lead_time_dias: parseInt(editState.lead_time_dias) || 15, costo_unitario: editState.costo_unitario ? parseFloat(editState.costo_unitario) : undefined, moq: parseFloat(editState.moq) || 1, proveedor: editState.proveedor || undefined, service_level: parseFloat(editState.service_level) || 0.95, precio_venta: editState.precio_venta ? parseFloat(editState.precio_venta) : undefined, categoria: editState.categoria || undefined, marca: editState.marca || undefined, unidad_medida: editState.unidad_medida || undefined, codigo_barras: editState.codigo_barras || undefined })
```

If `upsertInventoryStock`'s TypeScript param type is a fixed shape (in `api.ts`/`types.ts`), add the five optional fields to that type too so this compiles.

- [ ] **Step 4: Render the new inputs in the edit row**

In the edit-row render, inside the SKU/name `<td>` (after the `display_name` input + hint, ~line 1358), add compact catalog inputs:

```tsx
 <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
  <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_categoria')} value={editState.categoria} onChange={e => setEditState(s => s ? { ...s, categoria: e.target.value } : s)} />
  <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_marca')} value={editState.marca} onChange={e => setEditState(s => s ? { ...s, marca: e.target.value } : s)} />
  <input style={{ ...inputS, width: 70 }} placeholder={t('inventory.edit_unidad')} value={editState.unidad_medida} onChange={e => setEditState(s => s ? { ...s, unidad_medida: e.target.value } : s)} />
  <input style={{ ...inputS, width: 120 }} placeholder={t('inventory.edit_codigo_barras')} value={editState.codigo_barras} onChange={e => setEditState(s => s ? { ...s, codigo_barras: e.target.value } : s)} />
 </div>
```

And in the cost `<td>` (after the `costo_unitario` input, ~line 1393), add the sale price input:

```tsx
 <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 4 }}>
  <span style={{ fontSize: 11, color: C.dim }}>$</span>
  <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder={t('inventory.edit_precio_venta')} value={editState.precio_venta} onChange={e => setEditState(s => s ? { ...s, precio_venta: e.target.value } : s)} />
 </div>
```

- [ ] **Step 5: Add the "download template" button**

In the header actions, next to the existing CSV import button (~line 833), add:

```tsx
 <button onClick={() => downloadInventoryTemplate().catch(err => setError(err instanceof Error ? err.message : String(err)))} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
  <Download size={12} /> {t('inventory.btn_template')}
 </button>
```

Import `downloadInventoryTemplate` from `@/lib/api` at the top of the file (add to the existing import list).

- [ ] **Step 6: Add translation strings**

In `Frontend/src/i18n/translations.ts`, `es` block (near other `inventory.*`):

```ts
    'inventory.btn_template': 'Plantilla',
    'inventory.edit_categoria': 'Categoría',
    'inventory.edit_marca': 'Marca',
    'inventory.edit_unidad': 'Unidad',
    'inventory.edit_codigo_barras': 'Código de barras',
    'inventory.edit_precio_venta': 'Precio venta',
```

`en` block:

```ts
    'inventory.btn_template': 'Template',
    'inventory.edit_categoria': 'Category',
    'inventory.edit_marca': 'Brand',
    'inventory.edit_unidad': 'Unit',
    'inventory.edit_codigo_barras': 'Barcode',
    'inventory.edit_precio_venta': 'Sale price',
```

- [ ] **Step 7: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: 0 errors. (If `upsertInventoryStock`'s param type rejects the new keys, extend that type in `api.ts`/`types.ts`.)

- [ ] **Step 8: Manual verification**

With the app running + demo data: open `/inventory`, click "Plantilla" → a `plantilla_inventario.csv` downloads with the 14-column header + example row. Edit a SKU → the Categoría/Marca/Unidad/Código de barras/Precio venta inputs appear, save, reopen → values persisted.

- [ ] **Step 9: Commit**

```bash
git add Frontend/src/app/inventory/page.tsx Frontend/src/lib/types.ts Frontend/src/lib/api.ts Frontend/src/i18n/translations.ts
git commit -m "feat(inventory): template download button + catalog fields in edit form

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Regression + wrap-up

- [ ] **Step 1: Backend suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -q`
Expected: PASS (new tests + all existing). Fix any regression.

- [ ] **Step 2: Frontend typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 3: End-to-end smoke (isolated, optional but recommended)**

If a smoke is desired, exercise the import path against the test DB: `POST /inventory/bulk` with a CSV built from `GET /inventory/template.csv`, then `GET /inventory/status` and confirm the catalog fields round-trip. (The controller ran a similar isolated-backend smoke for the previous branch.)

---

## Self-Review notes

- **Spec coverage:** Schema+persistence → Task 1. Bulk import + proveedor/notas fix → Task 2. Template endpoint → Task 3. Frontend button + edit form + types → Task 4. Regression → Task 5.
- **Type consistency:** `_TEMPLATE_COLUMNS` order matches the Global Constraints order and the Task 3 test's `expected`. `EditState`/`rowToEdit`/`commitEdit` field names (`precio_venta`, `categoria`, `marca`, `unidad_medida`, `codigo_barras`) are identical across steps and match the backend column names.
- **Assumptions to verify at implementation time (mirror existing code):** the `_sku()` helper and `viewer_headers` fixture in `test_inventory.py`; whether `Response` and `csv`/`io` are already imported in `inventory.py`; the exact `InventoryStatusItem` type name and `upsertInventoryStock` param type in the frontend; `get_current_user` import already present in `inventory.py`.

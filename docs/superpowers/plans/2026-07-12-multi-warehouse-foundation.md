# Multi-Warehouse Foundation (MW-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a warehouse (`bodega`) dimension to inventory — schema, CSV import, warehouse CRUD, and an idempotent coherent mock-data seeder — so a SKU can hold stock in multiple warehouses, without breaking any single-warehouse behavior. This is the foundation the MILP optimizer (Plan MW-2) and its UI (MW-3) build on.

**Architecture:** `inventory_stock` gains a `bodega TEXT NOT NULL DEFAULT 'principal'` column and its uniqueness moves from `(tenant_id, sku)` to `(tenant_id, sku, bodega)`. A new `warehouses` table holds warehouse metadata. All existing write paths (`upsert_stock`, `bulk_import`) accept `bodega`, defaulting to `'principal'` so pre-existing data and CSVs keep working. A seed script populates a coherent multi-warehouse demo tenant.

**Tech Stack:** Python 3 / FastAPI / psycopg2 (backend), pytest (needs Postgres :5544).

## Global Constraints

- New columns/tables are additive; the constraint swap is done in ordered migrations appended to `_MIGRATIONS` in `backend/db/migrations.py` (column FIRST, then drop old unique, then add new unique) so a partially-migrated DB never loses uniqueness.
- `bodega` defaults to `'principal'` everywhere it's optional — existing single-warehouse tenants and CSVs without a `bodega` column keep working unchanged.
- Backend is pure orchestration (no ML/pandas); warehouse logic is CRUD + SQL.
- Tests follow `TESTING_GUIDELINES.md`: assert persisted DB state with direct queries; mutating endpoints get a viewer-denied / analyst-allowed pair.
- Seeder inserts REAL rows only — it never fakes API responses (that would violate the no-mock-API-responses policy). It must be idempotent (safe to re-run) and produce business-coherent data (costo < precio_venta, stock ≥ 0, every SKU has sales in ≥1 warehouse, lead times 3-30 days).
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest tests/... -q` from `backend/`.

---

### Task 1: Warehouse schema + constraint swap + upsert wiring

**Files:**
- Modify: `backend/db/migrations.py` (append migrations after the last inventory_stock migration ~line 368)
- Modify: `backend/inventory/service.py` (`upsert_stock` — `allowed` set + `ON CONFLICT` clause ~lines 25-46)
- Test: `backend/tests/test_warehouses.py` (new)

**Interfaces:**
- Produces: `warehouses` table; `inventory_stock.bodega` column; uniqueness on `(tenant_id, sku, bodega)`; `upsert_stock` accepts a `bodega` key (default `'principal'`).

- [ ] **Step 1: Verify the current unique constraint name (do this FIRST)**

The migration must drop the existing `UNIQUE (tenant_id, sku)` constraint by name. Postgres's default name for it is `inventory_stock_tenant_id_sku_key`, but VERIFY before writing the migration: start the test app (which runs `migrations.run_all()` against docker `faro_db` :5544) or query it. From `backend/`, run:

```
backend/.venv/Scripts/python.exe -c "import backend.main; from backend.db.connection import query; print([r['conname'] for r in query(\"SELECT conname FROM pg_constraint c JOIN pg_class t ON c.conrelid=t.oid WHERE t.relname='inventory_stock' AND c.contype='u'\")])"
```

(Importing `backend.main` initializes the DB pool.) Use the actual name returned in Step 2's `DROP CONSTRAINT`. If it is not `inventory_stock_tenant_id_sku_key`, adjust accordingly.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_warehouses.py`:

```python
"""Multi-warehouse foundation: a SKU can hold stock in multiple warehouses."""
from uuid import uuid4


def _sku():
    return f"WH_{uuid4().hex[:8]}"


class TestWarehouseStock:
    def test_same_sku_two_warehouses_creates_two_rows(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})
        svc.upsert_stock(tid, sku, {"stock_actual": 40,  "bodega": "Sur"})
        from backend.db.connection import query
        rows = query(
            "SELECT bodega, stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s ORDER BY bodega",
            (tid, sku),
        )
        assert len(rows) == 2
        by_bodega = {r["bodega"]: float(r["stock_actual"]) for r in rows}
        assert by_bodega == {"Norte": 100.0, "Sur": 40.0}

    def test_upsert_without_bodega_defaults_to_principal(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query_one
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 12})
        row = query_one("SELECT bodega FROM inventory_stock WHERE tenant_id=%s AND sku=%s", (tid, sku))
        assert row["bodega"] == "principal"

    def test_upsert_same_sku_same_bodega_updates_not_duplicates(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        from backend.db.connection import query
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 5, "bodega": "Norte"})
        svc.upsert_stock(tid, sku, {"stock_actual": 9, "bodega": "Norte"})
        rows = query("SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Norte'", (tid, sku))
        assert len(rows) == 1 and float(rows[0]["stock_actual"]) == 9.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_warehouses.py -v`
Expected: FAIL (no `bodega` column / upsert doesn't accept it).

- [ ] **Step 4: Add the migrations**

In `backend/db/migrations.py`, append after the `add_service_level_to_inventory_stock` entry (~line 368):

```python
    ("create_warehouses",
     """CREATE TABLE IF NOT EXISTS warehouses (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         name       TEXT NOT NULL,
         is_default BOOLEAN NOT NULL DEFAULT FALSE,
         created_at TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, name)
     )"""),
    ("add_bodega_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS bodega TEXT NOT NULL DEFAULT 'principal'"),
    ("drop_inventory_stock_tenant_sku_unique",
     "ALTER TABLE inventory_stock DROP CONSTRAINT IF EXISTS inventory_stock_tenant_id_sku_key"),
    ("add_inventory_stock_tenant_sku_bodega_unique",
     """DO $$ BEGIN
         IF NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'inventory_stock_tenant_sku_bodega_key'
         ) THEN
           ALTER TABLE inventory_stock
             ADD CONSTRAINT inventory_stock_tenant_sku_bodega_key UNIQUE (tenant_id, sku, bodega);
         END IF;
       END $$"""),
```

Use the constraint name confirmed in Step 1 for the DROP. If `migrations.run_all()` executes statements one-per-call and can't handle the `DO $$` block, instead guard the ADD by catching the duplicate-object error the same way other idempotent migrations in this file do — inspect how `run_all()` executes entries and match its capabilities.

- [ ] **Step 5: Wire `bodega` into `upsert_stock`**

In `backend/inventory/service.py`, `upsert_stock`: add `"bodega"` to the `allowed` set, and change the SQL from `ON CONFLICT (tenant_id, sku)` to `ON CONFLICT (tenant_id, sku, bodega)`. Because `bodega` has a NOT NULL default, ensure the INSERT provides it: if `"bodega"` isn't in the incoming `data`, inject `data = {**data, "bodega": "principal"}` at the top of the function (before building `safe`) so the conflict target always has a value. Also update `get_stock`/`list_stock` if they must remain keyed by `(tenant, sku)` — for now `get_stock(tenant, sku)` returns the FIRST matching row; add a note that warehouse-aware reads come in a later task. (Do not break existing callers — verify `get_stock`'s existing single-row callers still behave.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_warehouses.py -v`
Expected: PASS.

- [ ] **Step 7: Run inventory regression**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_inventory.py -q`
Expected: PASS — if any test breaks because `upsert_stock`/`get_stock` now behaves per-warehouse, reconcile carefully (existing single-warehouse tests should still pass since everything defaults to `'principal'`).

- [ ] **Step 8: Commit**

```bash
git add backend/db/migrations.py backend/inventory/service.py backend/tests/test_warehouses.py
git commit -m "feat(inventory): add warehouse (bodega) dimension to inventory_stock

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `bodega` in CSV import + template + Pydantic models + auto-create warehouse

**Files:**
- Modify: `backend/api/v1/inventory.py` (`StockUpsert`/`StockPatch`, `bulk_import`, `_TEMPLATE_COLUMNS`/`_TEMPLATE_EXAMPLE`)
- Modify: `backend/inventory/service.py` (auto-create warehouse row on upsert with a new bodega)
- Test: `backend/tests/test_warehouses.py` (append)

**Interfaces:**
- Consumes: `upsert_stock` with `bodega` (Task 1).
- Produces: CSV import persists `bodega`; a previously-unseen `bodega` auto-creates a `warehouses` row.

- [ ] **Step 1: Write the failing test** — a CSV with a `bodega` column persists the warehouse and auto-creates it in `warehouses`; the template header includes `bodega`. (Mirror the catalog-field import test in `test_inventory.py::test_bulk_import_persists_catalog_and_proveedor` for structure; assert both the `inventory_stock.bodega` value and a matching `warehouses` row via direct query.)

- [ ] **Step 2: Run test to verify it fails.** Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_warehouses.py -k bulk -v` → FAIL.

- [ ] **Step 3: Add `bodega` to the models + parse loop + template.** In `backend/api/v1/inventory.py`: add `bodega: Optional[str] = None` to `StockUpsert` and `StockPatch`; add `"bodega"` to `bulk_import`'s string-field parse loop; insert `"bodega"` into `_TEMPLATE_COLUMNS` (right after `sku`) and a sample value into `_TEMPLATE_EXAMPLE` (keep header/example column counts equal — update the template test's `expected` list accordingly in `test_inventory.py`).

- [ ] **Step 4: Auto-create the warehouse on upsert.** In `backend/inventory/service.py::upsert_stock`, after the stock row is written, upsert the warehouse: `INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) ON CONFLICT (tenant_id, name) DO NOTHING` using the effective `bodega`. Keep it best-effort (wrapped so a warehouse-insert hiccup never fails the stock write).

- [ ] **Step 5: Run tests + template regression.** Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_warehouses.py tests/test_inventory.py -q` → PASS (fix the template-header test's expected columns to include `bodega`).

- [ ] **Step 6: Commit.** `git add backend/api/v1/inventory.py backend/inventory/service.py backend/tests/test_warehouses.py backend/tests/test_inventory.py` then commit `feat(inventory): CSV import + template gain bodega column; auto-create warehouse`.

---

### Task 3: Warehouses service + list/create endpoints

**Files:**
- Create: `backend/inventory/warehouse_service.py`
- Modify: `backend/api/v1/inventory.py` (add `GET /inventory/warehouses`, `POST /inventory/warehouses`)
- Test: `backend/tests/test_warehouses.py` (append)

**Interfaces:**
- Produces: `list_warehouses(tenant_id) -> list[dict]`, `create_warehouse(tenant_id, name, is_default=False) -> dict`, `ensure_default_warehouse(tenant_id)`.

- [ ] **Step 1: Write the failing test** — `POST /inventory/warehouses` viewer-denied (403, no row) / analyst-allowed (201, row exists via direct query); `GET /inventory/warehouses` returns created warehouses. Duplicate name → idempotent or 409 (pick one, assert it).

- [ ] **Step 2: Run → FAIL** (routes 404).

- [ ] **Step 3: Implement the service** — thin CRUD over `warehouses` in `warehouse_service.py` (`list_warehouses`, `create_warehouse` with `ON CONFLICT (tenant_id, name) DO NOTHING` + return existing, `ensure_default_warehouse` creating `('principal', is_default=True)` if none).

- [ ] **Step 4: Add the endpoints** in `inventory.py` — `GET` uses `get_current_user`; `POST` uses `require_analyst_or_above`; mirror the existing `suppliers` endpoints' shape (there's a `POST /inventory/suppliers` nearby to copy structure/validation from).

- [ ] **Step 5: Run tests** → PASS.

- [ ] **Step 6: Commit** `feat(inventory): warehouses list/create endpoints + service`.

---

### Task 4: Idempotent coherent mock-data seeder

**Files:**
- Create: `backend/db/seed_mock.py`
- Test: `backend/tests/test_seed_mock.py` (new)

**Interfaces:**
- Produces: `seed_mock_tenant(tenant_id: str, *, warehouses=3, skus=12, days=120) -> dict` — populates a coherent multi-warehouse dataset for the given tenant and returns a summary; safe to re-run (idempotent).

- [ ] **Step 1: Write the failing test** — after `seed_mock_tenant(tid)`: (a) `warehouses` has ≥3 rows for the tenant; (b) `inventory_stock` has rows spanning multiple bodegas with `stock_actual >= 0` and `costo_unitario < precio_venta` for every row that has both; (c) running it a SECOND time does not increase row counts (idempotent) — assert counts equal before/after the second call. Assert all via direct DB queries.

- [ ] **Step 2: Run → FAIL** (module doesn't exist).

- [ ] **Step 3: Implement `seed_mock.py`** — for the given tenant: create `warehouses` (`principal` default + `Norte`/`Sur`); generate `skus` SKUs each with a random-but-coherent cost/price (`precio_venta = costo * (1.2..2.0)`), lead time 3-30d, and per-warehouse `stock_actual`; optionally seed a small sales-history dataset row set if a sales table exists (if sales history lives only in uploaded dataset files, skip that and note it — do NOT invent a schema). Idempotency: derive deterministic SKU ids from a fixed seed prefix (e.g. `MOCK_{i:03d}`) and use `upsert_stock` / `ON CONFLICT` so re-running overwrites rather than appends. Insert REAL rows only.

- [ ] **Step 4: Run tests** → PASS (idempotency + coherence assertions).

- [ ] **Step 5: Wire into bootstrap (optional, guarded)** — if the project has a startup hook that seeds demo data, add a guarded call so a fresh DB gets a populated demo tenant; otherwise expose `seed_mock.py` as a runnable module (`python -m backend.db.seed_mock <tenant_id>`) and note how to run it. Do NOT auto-seed inside pytest runs (guard on an env flag or explicit call).

- [ ] **Step 6: Commit** `feat(db): idempotent coherent multi-warehouse mock-data seeder`.

---

### Task 5: Regression + wrap-up

- [ ] **Step 1:** `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_warehouses.py tests/test_seed_mock.py tests/test_inventory.py -q` → PASS.
- [ ] **Step 2:** Confirm no existing inventory behavior regressed for single-warehouse tenants (everything defaults to `principal`).

---

## Self-Review notes
- **Spec coverage:** Sub-project 4 schema → Task 1; import/template → Task 2; warehouse CRUD → Task 3; mock-data directive → Task 4.
- **Riskiest step:** the constraint swap (Task 1) — the implementer MUST verify the real constraint name (Step 1) and audit `upsert_stock`/`get_stock` callers so single-warehouse behavior is preserved.
- **Deferred to MW-2/MW-3:** warehouse-aware reads in `get_inventory_status` (per-warehouse semáforo), the MILP optimizer, and the transfers UI — those plans are written after this foundation + seed land.

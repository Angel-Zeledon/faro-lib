# Warehouse-Aware Reads/Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three correctness gaps flagged by the Multi-Warehouse Foundation (MW-1) final review — all silently wrong the moment a tenant has a real second warehouse, all currently masked because no tenant does yet.

**Architecture:** Three independent fixes in `backend/inventory/`:
1. `inventory_po_items` gains a `bodega` column (default `'principal'`); `receive_po`'s stock-increment UPDATE filters by it, so receiving a PO destined for one warehouse no longer inflates every warehouse's stock for that SKU.
2. `get_inventory_status`'s `stock_map` sums `stock_actual` across all bodega rows per SKU (instead of keeping one arbitrary row), so the semáforo and inventory valuation reflect the tenant's true total stock.
3. `upsert_stock` returns the row matching the bodega it just wrote (not an arbitrary one), fixing the PUT/PATCH stock endpoints' response echo for multi-warehouse writes.

**Tech Stack:** Python 3, FastAPI, psycopg2, pytest (needs Postgres :5544 — the SAME instance MW-1 already uses in this worktree; no isolation concern, this plan builds directly on MW-1's schema).

## Global Constraints

- All three fixes must preserve single-warehouse behavior exactly (everything still defaults to `'principal'`; a tenant with one warehouse must see identical behavior before and after).
- The new `bodega` column on `inventory_po_items` is additive (`ADD COLUMN IF NOT EXISTS ... DEFAULT 'principal'`), following the same migration pattern MW-1 already used.
- Tests assert real computed values via direct DB queries (a two-warehouse scenario where the naive/buggy code would give a visibly wrong answer), not just "didn't error."
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest tests/... -q` — but this worktree has no venv of its own; invoke the shared venv by absolute path with cwd set to this worktree's `backend/` directory:
  ```
  cd C:\Users\Jahir\Documents\forecasting-mw\backend
  C:\Users\Jahir\Documents\forecasting\backend\.venv\Scripts\python.exe -m pytest tests/... -q
  ```

---

### Task 1: `bodega` on `inventory_po_items` + reception filters by it

**Files:**
- Modify: `backend/db/migrations.py` (append `add_bodega_to_inventory_po_items`)
- Modify: `backend/api/v1/inventory.py` (`POLineItem` gains `bodega`)
- Modify: `backend/inventory/roi_service.py` (`log_po_generation`'s INSERT into `inventory_po_items` includes `bodega`)
- Modify: `backend/inventory/reception_service.py` (`receive_po`'s stock-increment UPDATE filters by `bodega`; the "SKU is new" branch's `upsert_stock` call passes it through)
- Test: `backend/tests/test_reception_bodega.py` (new)

**Interfaces:**
- Produces: `inventory_po_items.bodega TEXT NOT NULL DEFAULT 'principal'`; `POLineItem.bodega: Optional[str] = None` (defaults to `'principal'` when persisted); `receive_po` increments stock ONLY in the PO line's destination bodega.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reception_bodega.py`:

```python
"""
Reception must not inflate stock across every warehouse for a SKU — only
the PO line's own destination bodega should receive the incoming units.
"""
from uuid import uuid4


def _sku():
    return f"RCV_{uuid4().hex[:8]}"


class TestReceptionRespectsBodega:
    def test_receiving_a_po_only_increments_its_own_bodega(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        # Seed the SAME sku in two warehouses with known starting stock.
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 50, "bodega": "Sur"})

        # Log a PO destined for "Norte" only.
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 20, "status": "approved",
            "bodega": "Norte",
        }])

        rec_svc.receive_po(tid, po["id"], user_id="u1")

        norte = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Norte'",
            (tid, sku),
        )
        sur = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Sur'",
            (tid, sku),
        )
        assert float(norte["stock_actual"]) == 120.0  # 100 + 20 received
        assert float(sur["stock_actual"]) == 50.0      # untouched — this is the regression guard
```

Read `backend/tests/test_po_reception.py` first to mirror the exact fixture usage (`test_tenant`, `analyst_headers`) this file's existing tests already rely on — do not invent a different pattern for constructing/receiving a PO if an existing helper already does it.

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`, shared venv): `... -m pytest tests/test_reception_bodega.py -v`
Expected: FAIL — `sur["stock_actual"]` is `70.0` (50 + 20, wrongly inflated) instead of `50.0`.

- [ ] **Step 3: Add the migration**

In `backend/db/migrations.py`, append after the `inventory_po_items` table/index entries:

```python
    ("add_bodega_to_inventory_po_items",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS bodega TEXT NOT NULL DEFAULT 'principal'"),
```

- [ ] **Step 4: Add `bodega` to `POLineItem` and persist it**

In `backend/api/v1/inventory.py`, add `bodega: Optional[str] = None` to `POLineItem` (~line 458-466).

In `backend/inventory/roi_service.py::log_po_generation`, find the `INSERT INTO inventory_po_items (...)` statement and add `bodega` to its column list and values, defaulting to `"principal"` when the incoming item dict doesn't have one: `item.get("bodega") or "principal"`.

- [ ] **Step 5: Filter the reception UPDATE by bodega**

In `backend/inventory/reception_service.py`, in `receive_po`'s stock-increment loop (the `for i in ordered:` block that does `UPDATE inventory_stock SET stock_actual = stock_actual + %s WHERE tenant_id = %s AND sku = %s`), add `AND bodega = %s` to the WHERE clause and pass `i["bodega"]` (the PO item's own bodega, now selected in `get_po_items`'s query — check whether `get_po_items` already does `SELECT *` or an explicit column list; if explicit, add `bodega` to it) as an additional bind parameter. Do the same for the "SKU is new" branch's `upsert_stock` call — pass `"bodega": i.get("bodega") or "principal"` in its data dict.

- [ ] **Step 6: Run tests to verify they pass**

Run: `... -m pytest tests/test_reception_bodega.py -v`
Expected: PASS.

- [ ] **Step 7: Run PO/reception regression**

Run: `... -m pytest tests/test_reception_bodega.py tests/test_po_reception.py tests/test_supplier_scorecard.py tests/test_proactive_and_roi.py -q`
Expected: PASS — existing single-bodega PO tests are unaffected since every line defaults to `'principal'` and every tenant in those tests only ever has one warehouse's worth of stock per SKU.

- [ ] **Step 8: Commit**

```bash
git add backend/db/migrations.py backend/api/v1/inventory.py backend/inventory/roi_service.py backend/inventory/reception_service.py backend/tests/test_reception_bodega.py
git commit -m "fix(reception): PO reception only increments its own destination bodega

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `get_inventory_status` sums stock across bodegas per SKU

**Files:**
- Modify: `backend/inventory/service.py` (`get_inventory_status`'s `stock_map` construction, ~line 379-380)
- Test: `backend/tests/test_inventory_multi_bodega.py` (new)

**Interfaces:**
- Produces: for a SKU with stock rows in multiple bodegas, `get_inventory_status`'s returned item reflects the SUM of `stock_actual` across all of that SKU's bodega rows (not one arbitrary row's value).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_inventory_multi_bodega.py`:

```python
"""get_inventory_status must sum stock across bodegas per SKU, not pick one."""
from uuid import uuid4


def _sku():
    return f"MB_{uuid4().hex[:8]}"


class TestInventoryStatusMultiBodega:
    def test_stock_actual_is_summed_across_bodegas(self, client, auth_headers, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.db import session_store

        tid = test_tenant["id"]
        sid = "sess-mb-test"
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"stock_actual": 100, "lead_time_dias": 10, "bodega": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 40, "lead_time_dias": 10, "bodega": "Sur"})

        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        items = inv_svc.get_inventory_status(tid, sid)
        item = next(i for i in items if i["sku"] == sku)
        assert item["stock_actual"] == 140.0  # 100 + 40, not just one bodega's value
```

Read the existing `test_inventory.py` for how `session_store.set_forecasts` is used elsewhere in this test suite (already used by `test_status_gates_recommended_qty_by_signal`) to mirror the exact call signature — do not guess its shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `... -m pytest tests/test_inventory_multi_bodega.py -v`
Expected: FAIL — `item["stock_actual"]` is `100.0` or `40.0` (whichever row the dict comprehension kept), not `140.0`.

- [ ] **Step 3: Fix `stock_map` to aggregate per SKU**

In `backend/inventory/service.py`, replace the line `stock_map = {r["sku"]: r for r in stock_rows}` (~line 380) with an aggregating helper:

```python
    stock_map = _aggregate_stock_rows_by_sku(stock_rows)
```

Add the helper function above `get_inventory_status` (or near the other private helpers in this file):

```python
def _aggregate_stock_rows_by_sku(stock_rows: list[dict]) -> dict[str, dict]:
    """
    Collapse per-bodega inventory_stock rows into one summary row per SKU:
    stock_actual is SUMMED across bodegas (true total stock the tenant
    holds); every other field (lead_time_dias, costo_unitario, proveedor,
    etc.) is taken from a single deterministic representative row (the
    'principal' bodega if present, else the alphabetically-first bodega) —
    those are per-SKU catalog attributes, not per-warehouse quantities, so
    picking one is correct as long as it's deterministic.
    """
    by_sku: dict[str, list[dict]] = {}
    for r in stock_rows:
        by_sku.setdefault(r["sku"], []).append(r)

    result: dict[str, dict] = {}
    for sku, rows in by_sku.items():
        rows_sorted = sorted(rows, key=lambda r: (r.get("bodega") != "principal", r.get("bodega") or ""))
        representative = dict(rows_sorted[0])
        representative["stock_actual"] = sum(float(r["stock_actual"] or 0) for r in rows)
        result[sku] = representative
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `... -m pytest tests/test_inventory_multi_bodega.py -v`
Expected: PASS.

- [ ] **Step 5: Run inventory-status regression**

Run: `... -m pytest tests/test_inventory_multi_bodega.py tests/test_inventory.py tests/test_stress_audit.py tests/test_calculation_audit.py -q`
Expected: PASS — a single-warehouse tenant has exactly one row per SKU, so `_aggregate_stock_rows_by_sku` returns that row's own `stock_actual` unchanged (sum of one value = itself); no behavior change for the current single-warehouse production reality.

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_inventory_multi_bodega.py
git commit -m "fix(inventory): sum stock_actual across bodegas per SKU in get_inventory_status

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `upsert_stock` echoes the correct bodega row

**Files:**
- Modify: `backend/inventory/service.py` (`upsert_stock`'s return, ~line 71; `get_stock`, ~line 94)
- Test: `backend/tests/test_warehouses.py` (append)

**Interfaces:**
- Produces: `get_stock(tenant_id, sku, bodega=None)` — when `bodega` is provided, filters to that exact row; when omitted, preserves today's behavior (any matching row) for backward compatibility with existing callers.
- `upsert_stock`'s return value is the row for the bodega it JUST wrote, not an arbitrary one.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_warehouses.py`:

```python
    def test_upsert_stock_returns_the_bodega_it_wrote(self, client, auth_headers, test_tenant):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        sku = _sku()
        svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})
        result = svc.upsert_stock(tid, sku, {"stock_actual": 40, "bodega": "Sur"})
        assert result["bodega"] == "Sur"
        assert float(result["stock_actual"]) == 40.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... -m pytest tests/test_warehouses.py -k returns_the_bodega -v`
Expected: FAIL — `result["bodega"]` may be `"Norte"` (whichever row Postgres returns first with no ORDER BY/filter).

- [ ] **Step 3: Add an optional `bodega` filter to `get_stock` and use it in `upsert_stock`**

In `backend/inventory/service.py`:

```python
def get_stock(tenant_id: str, sku: str, bodega: Optional[str] = None) -> Optional[dict]:
    if bodega is not None:
        return query_one(
            "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND bodega = %s",
            (tenant_id, sku, bodega),
        )
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
```

In `upsert_stock`, change `row = get_stock(tenant_id, sku)` to `row = get_stock(tenant_id, sku, bodega=safe["bodega"])`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `... -m pytest tests/test_warehouses.py -v`
Expected: PASS, all tests in the file (the new one plus all pre-existing ones — `get_stock`'s default `bodega=None` behavior is unchanged for every existing caller that doesn't pass it).

- [ ] **Step 5: Run full regression**

Run: `... -m pytest tests/test_warehouses.py tests/test_seed_mock.py tests/test_inventory.py tests/test_inventory_multi_bodega.py tests/test_reception_bodega.py tests/test_po_reception.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_warehouses.py
git commit -m "fix(inventory): upsert_stock echoes the bodega row it just wrote

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Regression + wrap-up + close the deferred-scope note

- [ ] **Step 1: Full regression**

Run: `... -m pytest tests/test_warehouses.py tests/test_seed_mock.py tests/test_inventory.py tests/test_inventory_multi_bodega.py tests/test_reception_bodega.py tests/test_po_reception.py tests/test_supplier_scorecard.py tests/test_proactive_and_roi.py tests/test_stress_audit.py tests/test_calculation_audit.py -q`
Expected: PASS.

- [ ] **Step 2: Update the deferred-scope note in `service.py`**

The comment added during MW-1's final review (in `upsert_stock`, listing the three now-fixed gaps) should be updated to reflect that all three are now resolved — either remove it or replace it with a brief note that these were fixed in this plan, so a future reader doesn't think the gaps are still open.

---

## Self-Review notes

- **Spec coverage:** All 3 Important findings from MW-1's final review (reception's un-filtered UPDATE, get_inventory_status's arbitrary-row pick, upsert_stock's wrong-row echo) → Tasks 1, 2, 3 respectively.
- **Type consistency:** `get_stock`'s new `bodega` parameter is optional and defaults to `None` (today's behavior) everywhere it's referenced across Task 3.
- **Backward compatibility:** every fix is additive/backward-compatible — a single-warehouse tenant (the entire current production population) sees byte-identical behavior before and after this plan, verified by each task's regression step.

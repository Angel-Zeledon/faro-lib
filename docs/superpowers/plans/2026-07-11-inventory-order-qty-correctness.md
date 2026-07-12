# Inventory Order-Qty Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the recommended purchase quantity honest — never suggest ordering when the semáforo says stock is sufficient, never let a 0-unit line reach a purchase order, and let the buyer edit the quantity wherever it is shown.

**Architecture:** The recommendation lives in one backend function (`get_inventory_status`) consumed by every surface (`/inventory`, `/hoy`, PDF, alerts, export). We add two small pure helpers in `backend/inventory/` (one gates the quantity by signal, one strips 0-unit ordered lines) so the business rules are unit-testable without a DB. The frontend then (a) stops offering "approve" on 0-unit cart lines in `/hoy` and (b) makes the "Pedir" column editable in `/inventory`, exporting the edited quantities through the existing `logPOGeneration` path.

**Tech Stack:** Python 3 / FastAPI / psycopg2 (backend), pytest (tests), Next.js 14 / React / TypeScript (frontend).

## Global Constraints

- Backend has NO ML/pandas logic — pure orchestration (`ForecastingCore` owns ML). These changes are business rules in `backend/inventory/`, which is correct.
- Recommended quantity is non-zero ONLY when `signal` ∈ {`PEDIR_YA`, `PEDIR_PRONTO`}.
- No purchase-order line (CSV export or `inventory_po_items` row counted as ordered) may have quantity ≤ 0.
- Tests follow `TESTING_GUIDELINES.md`: assert the computed business value, not just that code ran. Mutating endpoints need a viewer-denied / analyst-allowed pair where an endpoint is touched.
- Backend tests need local Postgres on :5544. Pure-unit tests (no `client`/`auth_headers` fixture) do not.
- Run backend tests from `backend/`: `python -m pytest tests/ -q`. Typecheck frontend from `Frontend/`: `npx tsc --noEmit`.
- Do NOT run `npm run build` while `next dev` is running.
- Spanish user-facing copy; add new strings to BOTH `es` and `en` blocks in `Frontend/src/i18n/translations.ts`.

---

### Task 1: Gate recommended quantity by signal (point 1)

Add a pure helper that zeroes the recommendation for non-ordering signals, unit-test it, then wire it into `get_inventory_status` and blank the calc tooltip for those rows.

**Files:**
- Modify: `backend/inventory/service.py` (add helper near `_calc_recommended` ~line 289; call it inside `get_inventory_status` ~line 358-384)
- Test: `backend/tests/test_inventory.py` (add to the pure-unit test class alongside `test_recommended_order_*`, ~line 289)

**Interfaces:**
- Produces: `_gate_recommended_by_signal(signal: str, recomendado: float) -> float` — returns `recomendado` when `signal` ∈ {`PEDIR_YA`, `PEDIR_PRONTO`}, else `0.0`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_inventory.py` (inside the same class that holds `test_recommended_order_respects_moq`):

```python
    def test_recommended_gated_to_ordering_signals(self):
        from backend.inventory.service import _gate_recommended_by_signal
        # Ordering signals keep the computed quantity
        assert _gate_recommended_by_signal("PEDIR_YA", 120.0) == 120.0
        assert _gate_recommended_by_signal("PEDIR_PRONTO", 45.0) == 45.0
        # "Enough stock" signals never suggest ordering, even if the raw math > 0
        assert _gate_recommended_by_signal("OK", 30.0) == 0.0
        assert _gate_recommended_by_signal("SOBRESTOCK", 5.0) == 0.0
        assert _gate_recommended_by_signal("SIN_DATOS", 10.0) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_inventory.py -k recommended_gated -v`
Expected: FAIL with `ImportError: cannot import name '_gate_recommended_by_signal'`

- [ ] **Step 3: Add the helper**

In `backend/inventory/service.py`, immediately after `_calc_recommended` (ends ~line 303), add:

```python
# Signals for which recommending an order is meaningful. On any other signal
# (OK / SOBRESTOCK / SIN_DATOS) the semáforo says stock is sufficient, so the
# suggested quantity MUST be 0 — otherwise a healthy SKU shows "pedir N".
_ORDERING_SIGNALS = ("PEDIR_YA", "PEDIR_PRONTO")


def _gate_recommended_by_signal(signal: str, recomendado: float) -> float:
    """Zero the recommendation unless the signal actually calls for ordering."""
    if signal in _ORDERING_SIGNALS:
        return float(recomendado)
    return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_inventory.py -k recommended_gated -v`
Expected: PASS

- [ ] **Step 5: Wire the helper into `get_inventory_status`**

In `backend/inventory/service.py`, inside the `if has_forecast and has_stock:` branch of `get_inventory_status`, the block currently computes `recomendado` then builds `calc_explanation`. Replace the tail of that branch (from the `recomendado = _calc_recommended(...)` call through the `calc_explanation = {...}` assignment, ~lines 358-377) with:

```python
            recomendado = _calc_recommended(
                stock_actual, avg_daily, avg_std, lead_time, moq, sku_service_level
            )
            recomendado = _gate_recommended_by_signal(signal, recomendado)
            valor_inventario = (
                round(stock_actual * float(stock["costo_unitario"]), 2)
                if stock.get("costo_unitario") is not None else None
            )
            if recomendado > 0:
                _demanda_lt  = round(avg_daily * lead_time, 2)
                _safety      = round(z * avg_std * math.sqrt(lead_time), 2)
                _antes_moq   = round(max(0.0, _demanda_lt + _safety - stock_actual), 2)
                calc_explanation = {
                    "demanda_diaria":    round(avg_daily, 2),
                    "lead_time_dias":    lead_time,
                    "demanda_lead_time": _demanda_lt,
                    "safety_stock":      _safety,
                    "stock_actual":      stock_actual,
                    "antes_moq":         _antes_moq,
                    "moq":               moq,
                    "cantidad_final":    recomendado,
                }
            else:
                # Enough stock: no order suggested, so no cálculo to explain.
                calc_explanation = {"suficiente": True}
```

Note the frontend already renders `cantidad_recomendada === 0` as "no pedir" (`Frontend/src/app/inventory/page.tsx:1373`), so this needs no frontend change to display correctly.

- [ ] **Step 6: Update the CalcExplainer to handle the "suficiente" shape**

In `Frontend/src/app/inventory/page.tsx`, find the `CalcExplainer` component (referenced ~line 138, `exp.cantidad_final`). At the top of its render, add an early return so the "suficiente" tooltip doesn't try to read numeric fields:

```tsx
  if ((exp as { suficiente?: boolean }).suficiente) {
    return (
      <div style={{ fontSize: 11, color: 'var(--dim)', padding: '4px 0' }}>
        {t('inventory.calc_enough_stock')}
      </div>
    )
  }
```

Add the translation key in `Frontend/src/i18n/translations.ts` — in the `es` block near other `inventory.calc_*` keys:

```ts
    'inventory.calc_enough_stock': 'Stock suficiente — no se recomienda pedir.',
```

and in the `en` block:

```ts
    'inventory.calc_enough_stock': 'Enough stock — ordering is not recommended.',
```

Also relax the `calc_explanation` type if it is strongly typed. In `Frontend/src/lib/types.ts`, find the `calc_explanation` field on the inventory item type and make the numeric fields optional plus add `suficiente?: boolean` (e.g. `calc_explanation?: { suficiente?: boolean; demanda_diaria?: number; lead_time_dias?: number; demanda_lead_time?: number; safety_stock?: number; stock_actual?: number; antes_moq?: number; moq?: number; cantidad_final?: number } | null`).

- [ ] **Step 7: Run backend tests + frontend typecheck**

Run: `cd backend && python -m pytest tests/test_inventory.py -q`
Expected: PASS (fix any existing test that assumed a non-zero recommendation for an `OK`/`SOBRESTOCK` SKU — update it to expect `0`, since that is now the correct business outcome).

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add backend/inventory/service.py backend/tests/test_inventory.py Frontend/src/app/inventory/page.tsx Frontend/src/i18n/translations.ts Frontend/src/lib/types.ts
git commit -m "fix(inventory): recommend 0 units when stock is sufficient (OK/SOBRESTOCK)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Never log a 0-unit ordered line (point 2, backend defense)

Factor decision normalization into a pure helper that downgrades any "ordered" line with quantity ≤ 0 to `rejected`, so no code path (including the direct API) can record a 0-unit order. Unit-test it, then use it in `log_po_generation`.

**Files:**
- Modify: `backend/inventory/roi_service.py` (add helper after `_ordered_qty` ~line 29; call it at the top of `log_po_generation` ~line 47-53)
- Test: `backend/tests/test_proactive_and_roi.py` (pure-unit test, no DB fixture needed)

**Interfaces:**
- Consumes: `_ordered_qty(item: dict) -> float` (existing).
- Produces: `_normalize_decisions(items: list[dict]) -> list[dict]` — returns each item with a normalized `status`; any item whose status was `approved`/`modified` but whose ordered qty ≤ 0 is returned with `status = "rejected"`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_proactive_and_roi.py` (a plain test function, no fixtures):

```python
def test_normalize_decisions_downgrades_zero_qty_orders():
    from backend.inventory.roi_service import _normalize_decisions
    out = _normalize_decisions([
        {"sku": "A", "status": "approved", "cantidad_final": 0},
        {"sku": "B", "status": "modified", "cantidad_final": 12},
        {"sku": "C", "status": "approved", "cantidad_final": 5},
        {"sku": "D", "status": "rejected", "cantidad_final": 0},
    ])
    by_sku = {i["sku"]: i for i in out}
    # A ordered 0 units -> not a real order
    assert by_sku["A"]["status"] == "rejected"
    # B and C keep their ordering status
    assert by_sku["B"]["status"] == "modified"
    assert by_sku["C"]["status"] == "approved"
    # D was already rejected
    assert by_sku["D"]["status"] == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_proactive_and_roi.py -k normalize_decisions -v`
Expected: FAIL with `ImportError: cannot import name '_normalize_decisions'`

- [ ] **Step 3: Add the helper and use it**

In `backend/inventory/roi_service.py`, after `_ordered_qty` (ends ~line 29) add:

```python
def _normalize_decisions(items: list[dict]) -> list[dict]:
    """
    Normalize each buyer decision's status and forbid 0-unit orders.

    A line marked approved/modified but with an ordered quantity <= 0 is not a
    real order (it would create a purchase-order line for 0 units), so it is
    downgraded to 'rejected'. This is the single guard every PO path passes
    through, including the direct API.
    """
    norm: list[dict] = []
    for i in items:
        status = (i.get("status") or "approved").lower()
        if status not in ("approved", "modified", "rejected"):
            status = "approved"
        if status in _ORDERED and _ordered_qty(i) <= 0:
            status = "rejected"
        norm.append({**i, "status": status})
    return norm
```

Then in `log_po_generation`, replace the existing normalization loop (~lines 47-53, the `norm: list[dict] = []` / `for i in items:` block ending at `norm.append(...)`) with:

```python
    # Normalize status + forbid 0-unit orders (see _normalize_decisions).
    norm = _normalize_decisions(items)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_proactive_and_roi.py -k normalize_decisions -v`
Expected: PASS

- [ ] **Step 5: Add an end-to-end guard test through the endpoint**

This proves the 0-unit line never becomes an ordered `inventory_po_items` row. Add to `backend/tests/test_inventory.py` (needs DB + a completed session; reuse the same session-seeding helper the existing `test_status_*` / export tests use in this file — match their fixture usage):

```python
    def test_log_po_excludes_zero_qty_ordered_line(self, client, auth_headers, analyst_headers):
        session_id = self._seed_completed_session(client, auth_headers)  # reuse this file's helper
        body = {"items": [
            {"sku": "A", "signal": "PEDIR_YA",     "cantidad_recomendada": 0, "cantidad_final": 0,  "status": "approved"},
            {"sku": "B", "signal": "PEDIR_PRONTO", "cantidad_recomendada": 20, "cantidad_final": 20, "status": "approved"},
        ]}
        # viewer denied
        vr = client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                         json=body, headers=viewer_headers_for(client))
        assert vr.status_code == 403
        # analyst succeeds
        r = client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                        json=body, headers=analyst_headers)
        assert r.status_code == 201
        po_log_id = r.json()["data"]["id"]
        # Direct DB assertion: A is NOT an ordered line, B is
        from backend.db.connection import query
        rows = {row["sku"]: row for row in query(
            "SELECT sku, status, cantidad_final FROM inventory_po_items WHERE po_log_id = %s",
            (po_log_id,))}
        assert rows["A"]["status"] == "rejected"
        assert float(rows["A"]["cantidad_final"]) == 0
        assert rows["B"]["status"] == "approved"
        assert float(rows["B"]["cantidad_final"]) == 20
```

If `test_inventory.py` has no `_seed_completed_session` helper or `viewer_headers_for`, use whatever completed-session seeding and viewer fixture the neighbouring PO/export tests in this file already use (e.g. `viewer_headers` fixture from `conftest.py`). Do not invent a new pattern — mirror the existing one.

- [ ] **Step 6: Run the guard test**

Run: `cd backend && python -m pytest tests/test_inventory.py -k log_po_excludes_zero -v`
Expected: PASS (requires local Postgres on :5544)

- [ ] **Step 7: Commit**

```bash
git add backend/inventory/roi_service.py backend/tests/test_proactive_and_roi.py backend/tests/test_inventory.py
git commit -m "fix(inventory): drop 0-unit lines from purchase orders (backend guard)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `/hoy` cart — no approving/exporting 0-unit lines (point 2, UI)

Make a 0-unit cart line non-approvable and keep it out of the exported order and the logged decisions.

**Files:**
- Modify: `Frontend/src/app/hoy/page.tsx` (`ActionCard` ~line 187-211; `approved` filter ~line 412; `downloadOC` decisions ~line 433-444)
- Modify: `Frontend/src/i18n/translations.ts` (add `hoy.enough_stock` in `es` + `en`)

**Interfaces:**
- Consumes: `logPOGeneration(sessionId, items?)` (existing, `Frontend/src/lib/api.ts:662`).

- [ ] **Step 1: Guard the approved set**

In `Frontend/src/app/hoy/page.tsx`, change the `approved` derivation (~line 412) to require a positive quantity:

```tsx
 const approved   = cart.filter(i => (i.status === 'approved' || i.status === 'modified') && i.qty > 0)
```

- [ ] **Step 2: Keep 0-unit lines out of the logged decisions**

In `downloadOC` (~line 433), change the decisions filter so approved/modified lines with `qty <= 0` are not sent as orders (they carry no purchasing intent). Replace the `.filter(i => i.status !== 'pending')` with:

```tsx
   const decisions = cart
    .filter(i => i.status !== 'pending')
    .filter(i => i.status === 'rejected' || i.qty > 0)
    .map(i => ({
```

(Rejected lines are kept so adoption tracking still sees them; approved/modified must have qty > 0.)

- [ ] **Step 3: Make the ActionCard non-approvable at qty ≤ 0**

In `ActionCard` (`Frontend/src/app/hoy/page.tsx`), replace the "Action buttons" block (`{!isApproved ? ( ... ) : ( ... )}`, ~lines 187-210) so that when `item.qty <= 0` the approve button is replaced by a non-actionable note. Add, just before the `return (` of `ActionCard`, a derived flag:

```tsx
 const canOrder = item.qty > 0
```

Then change the action area (the `{!isApproved ? (` branch) to:

```tsx
     {!isApproved ? (
      <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
       {canOrder ? (
        <>
         <button onClick={onApprove} style={{
          all: 'unset', cursor: 'pointer', padding: '7px 16px', borderRadius: 8,
          background: '#22c55e', color: '#fff', fontSize: 13, fontWeight: 700,
          display: 'flex', alignItems: 'center', gap: 5,
         }}>
          {t('hoy.btn_approve')}
         </button>
         <button onClick={onReject} style={{
          all: 'unset', cursor: 'pointer', padding: '7px 12px', borderRadius: 8,
          border: '1px solid var(--border)', color: 'var(--dim)', fontSize: 13,
         }}>
          {t('hoy.btn_reject')}
         </button>
        </>
       ) : (
        <span style={{ fontSize: 12, color: 'var(--dim)', fontStyle: 'italic' }}>
         {t('hoy.enough_stock')}
        </span>
       )}
      </div>
     ) : (
```

Leave the `: (` undo branch unchanged.

- [ ] **Step 4: Add the translation strings**

In `Frontend/src/i18n/translations.ts`, `es` block near other `hoy.*` keys:

```ts
    'hoy.enough_stock': 'Stock suficiente por ahora',
```

`en` block:

```ts
    'hoy.enough_stock': 'Enough stock for now',
```

- [ ] **Step 5: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Manual verification**

Start the app per CLAUDE.md (backend on :8010, frontend on :5000), log in, run `/quick-start` → "▶ Probar con datos de ejemplo", open `/hoy`. Confirm: any `PEDIR_PRONTO` card whose quantity shows 0 displays "Stock suficiente por ahora" instead of the Aprobar button, and the exported `orden_de_compra.csv` contains no 0-quantity rows. (If the seeded demo happens to have no 0-qty line, temporarily edit a card's quantity to 0 via the input — it should revert, confirming the qty>0 input guard at `hoy/page.tsx:159` — then rely on the code path for the ≤0 case.)

- [ ] **Step 7: Commit**

```bash
git add Frontend/src/app/hoy/page.tsx Frontend/src/i18n/translations.ts
git commit -m "fix(hoy): cannot approve or export a 0-unit purchase line

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `/inventory` — editable "Pedir" quantity + export edited amounts (point 3)

Let the buyer override the suggested quantity directly in the `/inventory` table and provider views, and export a purchase order that reflects those edits (through `logPOGeneration`, not the server re-derivation).

**Files:**
- Modify: `Frontend/src/app/inventory/page.tsx` (add edited-qty state in the main component; make the "Pedir" cell editable ~line 1370-1376; add an "export edited" action next to the existing export button ~line 836)
- Modify: `Frontend/src/i18n/translations.ts` (add `inventory.edit_qty_title`, `inventory.btn_export_edited` in `es` + `en`)

**Interfaces:**
- Consumes: `logPOGeneration(sessionId, items)` (`Frontend/src/lib/api.ts:662`) with `POLineDecision[]`.
- Produces: local component state `editedQty: Record<string, number>` keyed by SKU, and `exportEditedPO()` which builds the CSV + logs decisions from the current items and their edited quantities.

- [ ] **Step 1: Add edited-qty state**

In the main `InventoryPage` component (the one owning `data`, `sessionId`, `viewMode`), add near the other `useState` hooks:

```tsx
 const [editedQty, setEditedQty] = useState<Record<string, number>>({})
 const [editingQtySku, setEditingQtySku] = useState<string | null>(null)
```

Add a helper (inside the component) that returns the effective order quantity for an item — the edit if present, else the recommendation:

```tsx
 const effectiveQty = (item: InventoryItem): number =>
   editedQty[item.sku] ?? item.cantidad_recomendada ?? 0
```

Use the item type name that `data.items` actually uses (check the import at the top of the file; it is likely `InventoryItem` from `@/lib/types`).

- [ ] **Step 2: Make the "Pedir" table cell editable**

In the table render, replace the "Pedir" `<td>` (currently `Frontend/src/app/inventory/page.tsx:1370-1376`) with an editable cell. Only `PEDIR_YA`/`PEDIR_PRONTO` rows (where a positive quantity is suggested) are editable; `OK`/`SOBRESTOCK` keep showing "no pedir":

```tsx
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
  {item.cantidad_recomendada != null && item.cantidad_recomendada > 0 ? (
   editingQtySku === item.sku ? (
    <input
     type="number" min={1} autoFocus
     defaultValue={effectiveQty(item)}
     onBlur={e => {
      const n = parseInt(e.target.value, 10)
      setEditedQty(prev => (!isNaN(n) && n > 0 ? { ...prev, [item.sku]: n } : prev))
      setEditingQtySku(null)
     }}
     onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
     style={{ width: 70, background: C.card, border: `1px solid ${C.indigo}`, borderRadius: 5, color: C.text, fontSize: 13, fontWeight: 700, padding: '3px 6px', outline: 'none' }}
    />
   ) : (
    <button
     onClick={() => setEditingQtySku(item.sku)}
     title={t('inventory.edit_qty_title')}
     style={{ all: 'unset', cursor: 'pointer', fontWeight: 700, fontSize: 13,
       color: editedQty[item.sku] != null ? C.indigo : C.green,
       borderBottom: `2px dashed ${(editedQty[item.sku] != null ? C.indigo : C.green)}60`, lineHeight: 1 }}
    >
     {fmt(effectiveQty(item), 0)}
    </button>
   )
  ) : item.cantidad_recomendada === 0
   ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
   : '—'}
 </td>
```

- [ ] **Step 3: Add an "export edited PO" action + handler**

In the main component add the handler (mirrors `/hoy`'s `downloadOC`, but sourced from the inventory items and their edited quantities). Only `PEDIR_YA`/`PEDIR_PRONTO` items with a positive effective qty become order lines:

```tsx
 async function exportEditedPO() {
  if (!sessionId || !data) return
  const orderItems = data.items
    .filter(i => (i.signal === 'PEDIR_YA' || i.signal === 'PEDIR_PRONTO') && effectiveQty(i) > 0)
  if (orderItems.length === 0) return
  // CSV
  const rows = ['SKU,Producto,Cantidad,Proveedor,Valor estimado']
  for (const i of orderItems) {
   const qty = effectiveQty(i)
   const val = qty * (i.costo_unitario ?? 0)
   rows.push(`${i.sku},"${i.display_name ?? ''}",${qty},"${i.proveedor ?? ''}",${val}`)
  }
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = 'orden_de_compra.csv'; a.click()
  URL.revokeObjectURL(url)
  // Log decisions (edited => 'modified', otherwise 'approved')
  const decisions = orderItems.map(i => ({
   sku: i.sku,
   display_name: i.display_name ?? undefined,
   proveedor: i.proveedor ?? undefined,
   signal: i.signal,
   cantidad_recomendada: i.cantidad_recomendada ?? 0,
   cantidad_final: effectiveQty(i),
   status: (editedQty[i.sku] != null ? 'modified' : 'approved') as 'approved' | 'modified',
   costo_unitario: i.costo_unitario ?? undefined,
  }))
  logPOGeneration(sessionId, decisions).catch(() => {})
 }
```

Ensure `logPOGeneration` is imported at the top of the file from `@/lib/api` (add it to the existing import list if missing).

Add the button next to the existing export button (~line 836, the `handleExport` button). Insert after it:

```tsx
 <button onClick={exportEditedPO} disabled={!sessionId} style={{ all: 'unset', cursor: !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, opacity: !sessionId ? 0.5 : 1 }}>
  <Download size={12} /> {t('inventory.btn_export_edited')}
 </button>
```

- [ ] **Step 4: Add translation strings**

In `Frontend/src/i18n/translations.ts`, `es` block near other `inventory.*`:

```ts
    'inventory.edit_qty_title': 'Editar cantidad a pedir',
    'inventory.btn_export_edited': 'Exportar OC (editada)',
```

`en` block:

```ts
    'inventory.edit_qty_title': 'Edit order quantity',
    'inventory.btn_export_edited': 'Export PO (edited)',
```

- [ ] **Step 5: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors. (If `InventoryItem` field names differ — e.g. `costo_unitario` nullability — adjust the optional chaining to match `Frontend/src/lib/types.ts`.)

- [ ] **Step 6: Manual verification**

With the app running and the demo data loaded, open `/inventory` (table view). Confirm: the "Pedir" number on a `PEDIR_YA`/`PEDIR_PRONTO` row is a dashed-underline button; clicking it shows a numeric input; entering a new value (e.g. change 120 → 150) turns the number indigo; clicking "Exportar OC (editada)" downloads a CSV whose quantity for that SKU is 150; and `OK`/`SOBRESTOCK` rows still show "no pedir" and are absent from the CSV.

- [ ] **Step 7: Commit**

```bash
git add Frontend/src/app/inventory/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(inventory): editable order quantity + export edited purchase order

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full regression + wrap-up

- [ ] **Step 1: Run the full backend inventory suite**

Run: `cd backend && python -m pytest tests/test_inventory.py tests/test_proactive_and_roi.py tests/test_po_reception.py -q`
Expected: PASS. Investigate and fix any failure caused by the new "recommend 0 when sufficient" rule (update stale expectations to the correct business value).

- [ ] **Step 2: Frontend typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: End-to-end smoke test**

Per CLAUDE.md fastest smoke test: log in → `/quick-start` → "▶ Probar con datos de ejemplo" → lands on `/inventory`. Verify `/inventory` and `/hoy` reflect all three fixes together.

---

## Self-Review notes

- **Spec coverage:** Point 1 → Task 1. Point 2 → Task 2 (backend guard) + Task 3 (`/hoy` UI). Point 3 → Task 4 (`/inventory` editable) with `/hoy` editing preserved and made qty>0-safe in Task 3. Regression → Task 5.
- **Type consistency:** helper names `_gate_recommended_by_signal`, `_normalize_decisions`, `effectiveQty`, state `editedQty`/`editingQtySku`, and `_ORDERING_SIGNALS` are used identically wherever referenced.
- **Known assumptions to verify at implementation time (mirror existing code, do not invent):** the completed-session seeding helper + viewer fixture in `test_inventory.py`; the inventory item TypeScript type name and the exact nullability of `costo_unitario`/`display_name` in `Frontend/src/lib/types.ts`; the `CalcExplainer` prop shape.

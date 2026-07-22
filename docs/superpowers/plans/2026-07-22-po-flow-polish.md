# PO Flow Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three findings from the 0.3 walkthrough: human-readable per-tenant PO numbers surfacing in the supplier email and `/pedidos`, a real confirmation dialog for "Enviar pedido" (replacing the 4-second self-resetting inline confirm), and cart margin copy that no longer reads as a broken total.

**Architecture:** Backend first — `po_number` column assigned transactionally in `log_po_generation`, formatted via one helper per side. Then frontend — `SendPOButton` moves to the app-wide `ConfirmDialog` (promise-based `useConfirm()` hook, provider already mounted in `AppShell`), and the `/hoy` cart copy branches on whether any approved SKU has a sale price.

**Tech Stack:** FastAPI + psycopg2 (raw SQL, no ORM), pytest against local Postgres :5544 (docker `faro_db`), Next.js 14 + TypeScript, i18n via `translations.ts` keys.

**Spec:** `docs/superpowers/specs/2026-07-22-po-flow-polish-design.md`

## Global Constraints

- All code, comments, test names and commit messages in **English**. Spanish appears ONLY in `es` string values of `Frontend/src/i18n/translations.ts` and user-facing copy the backend emits (email bodies/subjects).
- Tests must assert **state changes with direct DB queries**, not just status codes (project testing mandate).
- Backend tests run against local Postgres: docker container `faro_db` must be up (`docker start faro_db`).
- Migration style: idempotent `(name, sql)` tuples appended to `_MIGRATIONS` in `backend/db/migrations.py`; `IF NOT EXISTS` wherever the syntax allows.
- PO number display format is exactly `OC-` + zero-padded 6 digits (`OC-000123`), produced ONLY by `format_po_number()` (backend) / `formatPoNumber()` (frontend) — never inlined.
- Branch: `polish/po-flow-0-3-findings` off `main`.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1: Create the branch**

```bash
cd C:/Users/Jahir/Documents/forecasting
git checkout -b polish/po-flow-0-3-findings main
```

---

### Task 1: `po_number` — schema, assignment, history exposure

**Files:**
- Modify: `backend/db/migrations.py` (append 3 tuples to the `_MIGRATIONS` list, just before its closing `]`)
- Modify: `backend/inventory/roi_service.py` (INSERT in `log_po_generation` ~line 91; `get_po_history` ~line 218; new `format_po_number` helper)
- Test: `backend/tests/test_po_number.py` (new file)

**Interfaces:**
- Consumes: `backend.db.connection.query_one/execute/query` (already imported in `roi_service`); `backend.tenants.service.create_tenant(name) -> dict` (for the cross-tenant test); conftest fixtures `test_tenant`, `client`, `auth_headers`.
- Produces: `inventory_po_log.po_number INT` (unique per tenant, sequential from 1); `roi_service.format_po_number(po_number: int | None, fallback: str) -> str`; `get_po_history` rows now include `po_number`. Task 2 relies on `format_po_number` and on `get_po` (which does `SELECT *`, so it picks up the column with no change).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_po_number.py`:

```python
from uuid import uuid4

from backend.db.connection import execute, query, query_one
from backend.inventory import roi_service
from backend.inventory.roi_service import format_po_number


def _line():
    return {
        "sku": f"PON_{uuid4().hex[:8]}", "final_qty": 5,
        "status": "approved", "supplier": "Acme",
    }


class TestPONumberAssignment:
    def test_sequential_per_tenant_starting_at_one(self, test_tenant):
        tid = test_tenant["id"]
        po1 = roi_service.log_po_generation(tid, "sess-test", [_line()])
        po2 = roi_service.log_po_generation(tid, "sess-test", [_line()])
        row1 = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po1["id"],))
        row2 = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po2["id"],))
        assert row1["po_number"] == 1
        assert row2["po_number"] == 2

    def test_sequences_are_independent_across_tenants(self, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            tid_a, tid_b = test_tenant["id"], other["id"]
            roi_service.log_po_generation(tid_a, "sess-test", [_line()])
            roi_service.log_po_generation(tid_a, "sess-test", [_line()])
            po_b = roi_service.log_po_generation(tid_b, "sess-test", [_line()])
            row_b = query_one(
                "SELECT po_number FROM inventory_po_log WHERE id = %s", (po_b["id"],)
            )
            # Tenant B starts its own sequence — A's two orders must not advance it.
            assert row_b["po_number"] == 1
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_po_history_endpoint_carries_po_number(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po = roi_service.log_po_generation(tid, "sess-test", [_line()])
        resp = client.get("/api/v1/inventory/po-history?limit=5", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.json()["data"]
        match = [r for r in rows if r["id"] == po["id"]]
        assert match and match[0]["po_number"] == 1


class TestPONumberBackfill:
    def test_backfill_numbers_null_rows_in_created_order(self, test_tenant):
        from backend.db.migrations import run_all
        tid = test_tenant["id"]
        # Simulate pre-feature rows: insert directly with NULL po_number and
        # staggered timestamps, oldest first.
        ids = []
        for offset_min in (30, 20, 10):
            row = query_one(
                """INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, generated_at)
                   VALUES (%s, %s, 1, 5, NOW() - (%s || ' minutes')::interval)
                   RETURNING id""",
                (tid, "sess-backfill", offset_min),
            )
            ids.append(row["id"])
        run_all()  # idempotent — re-runs every migration incl. the backfill
        numbered = query(
            """SELECT id, po_number FROM inventory_po_log
               WHERE tenant_id = %s ORDER BY generated_at""",
            (tid,),
        )
        assert [r["id"] for r in numbered] == ids
        assert [r["po_number"] for r in numbered] == [1, 2, 3]


class TestFormatPONumber:
    def test_pads_to_six_digits(self):
        assert format_po_number(123, "fallback") == "OC-000123"

    def test_falls_back_when_unnumbered(self):
        assert format_po_number(None, "abc-uuid") == "abc-uuid"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Users/Jahir/Documents/forecasting/backend
python -m pytest tests/test_po_number.py -v
```
Expected: FAIL — `ImportError: cannot import name 'format_po_number'` (and, once that exists, `po_number` column missing / NULL assertions failing).

- [ ] **Step 3: Add the migrations**

In `backend/db/migrations.py`, append these three tuples at the END of the `_MIGRATIONS` list (immediately before its closing `]`):

```python
    # Human-readable per-tenant order number (spec 2026-07-22-po-flow-polish).
    ("po_log_add_po_number",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS po_number INT"),
    # Backfill pre-feature rows per tenant in generated_at order. Idempotent:
    # only NULL rows are numbered, and post-feature rows are never NULL.
    ("po_log_backfill_po_number",
     """UPDATE inventory_po_log t SET po_number = s.rn
        FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY generated_at, id) AS rn
              FROM inventory_po_log WHERE po_number IS NULL) s
        WHERE t.id = s.id AND t.po_number IS NULL"""),
    # Uniqueness guard: two concurrent inserts computing the same MAX+1 — the
    # loser gets a 23505 and retries (see roi_service.log_po_generation).
    ("po_log_po_number_unique_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS po_log_tenant_po_number_idx ON inventory_po_log (tenant_id, po_number)"),
```

- [ ] **Step 4: Assign the number on insert + expose it**

In `backend/inventory/roi_service.py`:

**(a)** Below the `_ORDERED` constant (~line 18), add:

```python
# SQLSTATE for unique_violation — the losing side of a po_number race.
_UNIQUE_VIOLATION = "23505"


def format_po_number(po_number: int | None, fallback: str) -> str:
    """Human-readable order reference (OC-000123); raw id when unnumbered."""
    return f"OC-{int(po_number):06d}" if po_number else fallback
```

**(b)** Replace the `inserted = query_one(...)` call in `log_po_generation` (~lines 91-101) with:

```python
    def _insert() -> dict | None:
        # po_number is computed inside the INSERT so number and row commit
        # atomically. Volume is human-driven, so MAX+1 contention is rare;
        # the unique index catches the race and we retry once.
        return query_one(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon,
                    suggested_count, approved_count, modified_count, rejected_count,
                    po_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       (SELECT COALESCE(MAX(po_number), 0) + 1
                          FROM inventory_po_log WHERE tenant_id = %s))
               RETURNING *""",
            (tenant_id, session_id, sku_count, total_units, total_value,
             skus_order_now, skus_order_soon,
             suggested_count, approved_count, modified_count, rejected_count,
             tenant_id),
        )

    try:
        inserted = _insert()
    except Exception as exc:
        if getattr(exc, "pgcode", "") != _UNIQUE_VIOLATION:
            raise
        inserted = _insert()
```

**(c)** In `get_po_history` (~line 218), add `po_number` to the SELECT column list:

```python
        """SELECT id, session_id, generated_at, sku_count, total_units,
                  total_value, skus_order_now, skus_order_soon,
                  reception_status, received_at, po_number
           FROM inventory_po_log
           WHERE tenant_id = %s
           ORDER BY generated_at DESC
           LIMIT %s""",
```

- [ ] **Step 5: Run the new tests**

```bash
python -m pytest tests/test_po_number.py -v
```
Expected: 6 PASS. (The migration runs at app startup; conftest boots the app, so the column exists.)

- [ ] **Step 6: Run the neighboring PO suites to catch regressions**

```bash
python -m pytest tests/test_po_send.py tests/test_po_reception.py tests/test_roi_month_report.py tests/test_cash_calendar.py -q
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/db/migrations.py backend/inventory/roi_service.py backend/tests/test_po_number.py
git commit -m "feat(po): sequential per-tenant po_number with backfill

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Supplier email uses the OC reference

**Files:**
- Modify: `backend/notifications/email.py` (`send_po_to_supplier_email`, ~lines 440-488)
- Modify: `backend/api/v1/inventory.py` (`send_po_to_suppliers` endpoint, call site ~line 1006)
- Test: `backend/tests/test_po_send.py` (add one test to the existing class)

**Interfaces:**
- Consumes: `roi_service.format_po_number` (Task 1); `po` dict from `rec_svc.get_po` (a `SELECT *` row — now includes `po_number`).
- Produces: `send_po_to_supplier_email` gains keyword-only param `po_ref: str | None = None`; subject becomes `Orden de compra {po_ref}`.

- [ ] **Step 1: Write the failing test**

Append to the `TestSendPOEndpoint` class in `backend/tests/test_po_send.py`:

```python
    def test_email_subject_uses_readable_po_number(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.db.connection import query_one
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": _sku(), "final_qty": 20, "status": "approved", "supplier": supplier_name,
        }])

        email_calls = []
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                            lambda **kw: email_calls.append(kw) or True)
        monkeypatch.setattr(wa_mod, "send_whatsapp", lambda *a, **kw: True)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200

        row = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po["id"],))
        expected_ref = f"OC-{row['po_number']:06d}"
        assert len(email_calls) == 1
        assert email_calls[0]["po_ref"] == expected_ref
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python -m pytest tests/test_po_send.py::TestSendPOEndpoint::test_email_subject_uses_readable_po_number -v
```
Expected: FAIL with `KeyError: 'po_ref'` (endpoint doesn't pass it yet).

- [ ] **Step 3: Thread the reference through**

**(a)** `backend/notifications/email.py` — change the signature of `send_po_to_supplier_email` (~line 438) to add the keyword param, and use it in body + subject:

```python
def send_po_to_supplier_email(
    *,
    to: str,
    supplier_name: str,
    po_log_id: str,
    items: list[dict],
    pdf_bytes: bytes,
    pdf_filename: str,
    po_ref: str | None = None,
) -> bool:
```

Inside, define once at the top of the function:

```python
    ref = po_ref or po_log_id
```

Then replace the two usages:
- body: `Referencia: {po_log_id}` → `Referencia: {ref}`
- subject: `_send(to, f"Orden de compra — {po_log_id}", html, ...)` → `_send(to, f"Orden de compra {ref}", html, ...)`

**(b)** `backend/api/v1/inventory.py` — in `send_po_to_suppliers`, the endpoint already holds `po` (from `rec_svc.get_po`). Add the import next to the other service imports inside the function:

```python
    from backend.inventory.roi_service import format_po_number
```

and extend the email call (~line 1006):

```python
            email_ok = email_mod.send_po_to_supplier_email(
                to=supplier["email"], supplier_name=supplier_name, po_log_id=po_log_id,
                items=supplier_items, pdf_bytes=pdf_bytes, pdf_filename=pdf_path.name,
                po_ref=format_po_number(po.get("po_number"), po_log_id),
            )
```

- [ ] **Step 4: Run the send suite**

```bash
python -m pytest tests/test_po_send.py -v
```
Expected: all PASS (existing tests monkeypatch the email function with `lambda **kw`, so the new kwarg is absorbed).

- [ ] **Step 5: Commit**

```bash
git add backend/notifications/email.py backend/api/v1/inventory.py backend/tests/test_po_send.py
git commit -m "feat(po): supplier email references OC number instead of raw UUID

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `/pedidos` — Orden column + send confirmation dialog

**Files:**
- Modify: `Frontend/src/lib/types.ts` (`POLogEntry`, ~line 826)
- Create: `Frontend/src/lib/poNumber.ts`
- Modify: `Frontend/src/components/po/POHistory.tsx` (`SendPOButton` ~lines 203-255, `POHistoryTable` ~lines 257-367)
- Modify: `Frontend/src/app/pedidos/page.tsx` (~line 113, pass contact-health names down)
- Modify: `Frontend/src/i18n/translations.ts` (new `po.*` keys in the `es` and `en` blocks; remove `roi.send_po_confirm` from both)

**Interfaces:**
- Consumes: `useConfirm()` from `@/components/ui/ConfirmDialog` (provider already mounted in `AppShell.tsx`); `getPOItems(poLogId)` returning `POItemsResponse` with `items: POItemLine[]` (`supplier: string | null`, `status: string`); `contactHealth: SupplierContactHealthRow[]` already fetched by the pedidos page (`r.supplier` is the name the send path will skip).
- Produces: `POLogEntry.po_number?: number | null`; `formatPoNumber(n?: number | null): string`; `POHistoryTable` gains prop `suppliersWithoutContact: string[]`; `SendPOButton` gains the same prop.

- [ ] **Step 1: Extend the type and add the formatter**

In `Frontend/src/lib/types.ts`, add to `POLogEntry` (after `id`):

```typescript
  po_number?:        number | null
```

Create `Frontend/src/lib/poNumber.ts`:

```typescript
/** Human-readable order reference (OC-000123); em dash when unnumbered. */
export function formatPoNumber(n?: number | null): string {
  return n != null ? `OC-${String(n).padStart(6, '0')}` : '—'
}
```

- [ ] **Step 2: Replace the inline confirm with the dialog**

In `Frontend/src/components/po/POHistory.tsx`:

**(a)** Update imports:

```typescript
import { getPOItems, receivePO, sendPOToSuppliers } from '@/lib/api'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { formatPoNumber } from '@/lib/poNumber'
```

**(b)** Replace the whole `SendPOButton` function (lines 203-255) with:

```typescript
function SendPOButton({ poLogId, suppliersWithoutContact }: {
  poLogId: string
  suppliersWithoutContact: string[]
}) {
  const { t } = useLanguage()
  const confirm = useConfirm()
  const [state, setState] = useState<'idle' | 'sending' | 'done'>('idle')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function handleClick() {
    setState('sending')
    try {
      // Preview what the send will actually do BEFORE doing it: which
      // suppliers get the order, and which get silently skipped for having
      // no contact info on file.
      const res = await getPOItems(poLogId)
      const names = [...new Set(
        res.items
          .filter(i => i.status === 'approved' || i.status === 'modified')
          .map(i => (i.supplier || '').trim())
          .filter(Boolean),
      )]
      const skipped = names.filter(n => suppliersWithoutContact.includes(n))
      const toSend  = names.filter(n => !suppliersWithoutContact.includes(n))

      const lines = [
        toSend.length > 0 ? `${t('po.send_confirm_to')}: ${toSend.join(', ')}.` : '',
        skipped.length > 0 ? `${t('po.send_confirm_skipped')}: ${skipped.join(', ')}.` : '',
      ].filter(Boolean).join(' ')

      const ok = await confirm({
        title: t('po.send_confirm_title'),
        message: lines || t('po.send_confirm_no_suppliers'),
        confirmLabel: t('po.send_confirm_action'),
      })
      if (!ok) { setState('idle'); return }

      const sendRes = await sendPOToSuppliers(poLogId)
      const anySent = sendRes.sent.length > 0
      const anySkipped = sendRes.skipped.length > 0
      const message = !anySent
        ? t('roi.send_po_none_sent')
        : anySkipped ? t('roi.send_po_partial') : t('roi.send_po_success')
      setResult({ ok: anySent, message })
      setState('done')
    } catch (e: unknown) {
      setResult({ ok: false, message: e instanceof Error ? e.message : t('roi.send_po_error') })
      setState('done')
    }
  }

  if (state === 'done' && result) {
    return (
      <span style={{ fontSize: 11, color: result.ok ? C.green : C.red, fontWeight: 600 }}>
        {result.message}
      </span>
    )
  }

  return (
    <button
      onClick={handleClick}
      disabled={state === 'sending'}
      style={{
        all: 'unset', cursor: state === 'sending' ? 'not-allowed' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '3px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600,
        border: `1px solid ${C.border}`, color: C.text,
      }}
    >
      <Send size={11} />
      {state === 'sending' ? t('roi.send_po_sending') : t('roi.send_po')}
    </button>
  )
}
```

Note: the old `useEffect` timer block and the `'confirm'` state are gone entirely.

**(c)** `POHistoryTable` — accept and thread the new prop, and add the leading "Orden" column:

Signature:

```typescript
export function POHistoryTable({ entries, onReceive, suppliersWithoutContact = [] }: {
  entries: POLogEntry[]
  onReceive: (id: string) => void
  suppliersWithoutContact?: string[]
}) {
```

Columns array — add the new header first:

```typescript
  const columns = [
    t('roi.col_order'),
    t('roi.col_datetime'),
    ...
  ]
```

Row — add a first cell before the datetime cell:

```typescript
              <td style={{ padding: '11px 14px', color: C.text, fontFamily: 'monospace', fontWeight: 600 }}>
                {formatPoNumber(entry.po_number)}
              </td>
```

And the call site of `SendPOButton` inside the reception cell becomes:

```typescript
                      <SendPOButton poLogId={entry.id} suppliersWithoutContact={suppliersWithoutContact} />
```

- [ ] **Step 3: Pass contact-health down from the page**

In `Frontend/src/app/pedidos/page.tsx` (~line 113):

```typescript
          <POHistoryTable
            entries={history}
            onReceive={setReceivingPO}
            suppliersWithoutContact={contactHealth.map(r => r.supplier)}
          />
```

(Use the full `contactHealth`, not `relevantContactHealth` — a PO being sent is by definition still open.)

- [ ] **Step 4: Translation keys**

In `Frontend/src/i18n/translations.ts`:

**(a)** REMOVE from both language blocks: `'roi.send_po_confirm'` (es line ~791, en line ~2403).

**(b)** ADD next to the other `roi.` PO keys — `es` block:

```typescript
    'roi.col_order': 'Orden',
    'po.send_confirm_title': '¿Enviar esta orden a tus proveedores?',
    'po.send_confirm_to': 'Se enviará a',
    'po.send_confirm_skipped': 'Se omitirá por falta de email/WhatsApp',
    'po.send_confirm_no_suppliers': 'Ninguna línea de esta orden tiene proveedor asignado; no se enviará a nadie.',
    'po.send_confirm_action': 'Enviar',
```

`en` block:

```typescript
    'roi.col_order': 'Order',
    'po.send_confirm_title': 'Send this order to your suppliers?',
    'po.send_confirm_to': 'Will be sent to',
    'po.send_confirm_skipped': 'Will be skipped (no email/WhatsApp on file)',
    'po.send_confirm_no_suppliers': 'No line in this order has a supplier assigned; nothing will be sent.',
    'po.send_confirm_action': 'Send',
```

- [ ] **Step 5: Typecheck**

```bash
cd C:/Users/Jahir/Documents/forecasting/Frontend
npx tsc --noEmit
```
Expected: exit 0. (If `roi.send_po_confirm` is referenced anywhere else, tsc's `TranslationKey` union will flag it — remove that reference too.)

- [ ] **Step 6: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/poNumber.ts Frontend/src/components/po/POHistory.tsx Frontend/src/app/pedidos/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(po): send confirmation dialog with skip preview + OC column

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Cart margin copy in `/hoy`

**Files:**
- Modify: `Frontend/src/app/hoy/page.tsx` (~lines 1182-1189)
- Modify: `Frontend/src/i18n/translations.ts` (replace 2 keys with 3, in both language blocks)

**Interfaces:**
- Consumes: existing locals in the cart block: `priced` / `unpriced` (`~line 687`), `salesProtected`.
- Produces: nothing downstream.

- [ ] **Step 1: Replace the translation keys**

In `Frontend/src/i18n/translations.ts` REMOVE (both blocks):
- `'hoy.cart_margin_excluded_singular'` (es ~682, en ~2293)
- `'hoy.cart_margin_excluded_plural'` (es ~683, en ~2294)

ADD in the `es` block at the same spot:

```typescript
    'hoy.cart_margin_excludes_prefix': 'El margen protegido no incluye',
    'hoy.cart_margin_excludes_suffix': 'SKU(s) sin precio de venta registrado',
    'hoy.cart_margin_add_prices': 'Para ver el margen que protege este pedido, registra el precio de venta de tus SKUs.',
```

ADD in the `en` block at the same spot:

```typescript
    'hoy.cart_margin_excludes_prefix': 'Protected margin excludes',
    'hoy.cart_margin_excludes_suffix': 'SKU(s) with no sale price on file',
    'hoy.cart_margin_add_prices': 'To see the margin this order protects, add sale prices to your SKUs.',
```

- [ ] **Step 2: Branch the render**

In `Frontend/src/app/hoy/page.tsx`, replace the `unpriced.length > 0` block (~lines 1182-1189):

```tsx
           {/* Margin caveat (2.6/0.3 polish): tie the message to the MARGIN
               figure, never to the money total above it — the total uses cost
               and is complete. Two cases: partial (some approved SKUs priced)
               and none priced, where the margin row is absent entirely and the
               note becomes an invitation instead of a warning. */}
           {unpriced.length > 0 && priced.length > 0 && (
            <div style={{ fontSize: 11, color: C.amber, marginTop: 3 }}>
             {t('hoy.cart_margin_excludes_prefix')} {unpriced.length} {t('hoy.cart_margin_excludes_suffix')}
            </div>
           )}
           {unpriced.length > 0 && priced.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>
             {t('hoy.cart_margin_add_prices')}
            </div>
           )}
```

- [ ] **Step 3: Typecheck**

```bash
cd C:/Users/Jahir/Documents/forecasting/Frontend
npx tsc --noEmit
```
Expected: exit 0 (the union type catches any survivor references to the removed keys).

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/app/hoy/page.tsx Frontend/src/i18n/translations.ts
git commit -m "fix(hoy): cart margin caveat no longer reads as an incomplete total

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

```bash
cd C:/Users/Jahir/Documents/forecasting/backend
python -m pytest tests/ -q
```
Expected: same pass/skip counts as `main` plus the 7 new tests, 0 failures. (Requires docker `faro_db` up.)

- [ ] **Step 2: Frontend typecheck**

```bash
cd C:/Users/Jahir/Documents/forecasting/Frontend
npx tsc --noEmit
```
Expected: exit 0.

- [ ] **Step 3: Browser verification (run skill pattern)**

With backend on :8010 and frontend on :5000, log in as `verificacion-0-3@example.com` / `Verificacion2026!` and verify:

1. `/pedidos` shows the new "Orden" column with `OC-000001` on the existing order (backfilled).
2. Click "Enviar pedido" → a modal dialog opens (no 4-second reset possible), listing "Se enviará a: Distribuidora Andina" and "Se omitirá…: Granos del Valle". Cancel closes with no request; Confirm sends and shows the partial-send result.
3. Backend log shows the email subject `Orden de compra OC-000001`.
4. In `/hoy`, approve the two urgent SKUs → cart note reads "Para ver el margen que protege este pedido, registra el precio de venta de tus SKUs." (demo SKUs have cost but no sale price).

- [ ] **Step 4: Merge decision**

Use the superpowers:finishing-a-development-branch skill: present merge/PR/keep options for `polish/po-flow-0-3-findings`.

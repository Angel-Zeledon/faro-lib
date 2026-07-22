# Multi-Warehouse UI + Alerts Implementation Plan (slices 6–7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the multi-warehouse backend (already on `main`) visible and usable: warehouse selector + per-warehouse semáforo in `/inventory`, transfer suggestions in `/hoy`, a Transferencias tab in `/pedidos`, demand-share editing, and per-warehouse copy in the daily alerts.

**Architecture:** New self-contained components under `Frontend/src/components/inventory/` and `components/po/` that fetch their own data through new `api.ts` clients; the monolithic pages (`inventory` ~2100 lines, `hoy` ~1500) only gain a mount point each. Every multi-warehouse affordance renders only when the tenant has ≥2 warehouses (spec §5: mono-warehouse tenants see zero change).

**Tech Stack:** Next.js 14 (App Router pages under `src/app/`), inline-style components with `C = {var(--...)}` tokens, `useLanguage()` i18n, backend FastAPI endpoints shipped in the backend plan.

## Global Constraints

- All code/comments English; **all user-visible strings via `t('key')`** with `es` AND `en` values in `Frontend/src/i18n/translations.ts` (es block ~line 1190, en block ~line 2805 — keep the two blocks in sync).
- Persisted signals stay `PEDIR_YA/PEDIR_PRONTO/OK/SOBRESTOCK`; API field `recommended_action` values are `"order" | "transfer"`.
- Mono-warehouse tenants must see **zero visual change** — gate every new surface on `warehouses.length >= 2`.
- Frontend verification: `cd Frontend && npx tsc --noEmit` (exit 0). No frontend unit-test framework exists — do not invent one.
- Backend verification: `cd backend && python -m pytest tests/<file> -q` (Postgres :5544 running).
- Do NOT run `npm run build` while `next dev` runs.
- Backend interfaces consumed (already on `main`): `GET /inventory/status?...&by_warehouse=true`, `GET/POST /inventory/transfers`, `POST /inventory/transfers/{id}/receive|cancel`, `GET /inventory/warehouses`, `PATCH /inventory/warehouses/{name}` body `{demand_share: number|null}`.

---

### Task 1: Types + API clients

**Files:**
- Modify: `Frontend/src/lib/types.ts` (append after `CalendarSeedResult`, ~line 620)
- Modify: `Frontend/src/lib/api.ts` (append after `toggleCalendarEntry`, ~line 731)

**Interfaces (produces — later tasks import these exact names):**

```ts
// types.ts
export interface Warehouse {
  id: string; name: string; is_default: boolean; demand_share: number | null
}
export interface TransferSuggestion {
  from_warehouse: string; qty: number; donor_coverage_days_after: number
}
export interface WarehouseStatusItem {
  sku: string; warehouse: string; display_name: string | null
  supplier: string | null; current_stock: number | null
  lead_time_days: number; lead_time_source: 'learned' | 'configured'
  moq: number; daily_demand: number | null; coverage_days: number | null
  reorder_point: number | null; signal: InventorySignal
  recommended_qty: number | null
  recommended_action: 'order' | 'transfer' | null
  transfer_suggestion: TransferSuggestion | null
  unit_cost: number | null
}
export interface WarehouseStatusResponse {
  items: WarehouseStatusItem[]
  summary: { total_rows: number; order_now: number; order_soon: number; transfers_suggested: number }
}
export interface TransferItem {
  id: string; sku: string; qty_sent: number; qty_received: number
}
export interface Transfer {
  id: string; from_warehouse: string; to_warehouse: string
  status: 'in_transit' | 'partial' | 'received' | 'cancelled'
  notes: string | null; created_by: string; created_at: string
  received_at: string | null; items: TransferItem[]
}
```

```ts
// api.ts
export const listWarehouses = () =>
  request<Warehouse[]>('GET', '/inventory/warehouses')
export const patchWarehouse = (name: string, demandShare: number | null) =>
  request<Warehouse>('PATCH', `/inventory/warehouses/${encodeURIComponent(name)}`,
    { demand_share: demandShare })
export const getStatusByWarehouse = (sessionId: string) =>
  request<WarehouseStatusResponse>(
    'GET', `/inventory/status?session_id=${sessionId}&by_warehouse=true`)
export const listTransfers = (status?: string) =>
  request<Transfer[]>('GET', `/inventory/transfers${status ? `?status=${status}` : ''}`)
export const createTransfer = (fromWarehouse: string, toWarehouse: string,
                               items: { sku: string; qty: number }[], notes?: string) =>
  request<Transfer>('POST', '/inventory/transfers',
    { from_warehouse: fromWarehouse, to_warehouse: toWarehouse, items, notes: notes ?? null })
export const receiveTransfer = (transferId: string,
                                lines: { sku: string; received_qty: number }[] | null) =>
  request<Transfer>('POST', `/inventory/transfers/${transferId}/receive`, { lines })
export const cancelTransfer = (transferId: string) =>
  request<Transfer>('POST', `/inventory/transfers/${transferId}/cancel`)
```

Check the actual `request<T>(method, path, body?, opts?)` signature at the top of `api.ts` and the import list in `api.ts` (add the new type names to the existing `import type {...} from './types'` block).

- [ ] **Step 1: Add the types** (code above, appended to `types.ts`)
- [ ] **Step 2: Add the API clients** (code above, appended to `api.ts`; extend the type import)
- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(ui): multi-warehouse types and API clients"
```

---

### Task 2: `useWarehouses` hook + warehouse selector with demand-share editor

**Files:**
- Create: `Frontend/src/components/inventory/WarehouseControls.tsx`
- Modify: `Frontend/src/i18n/translations.ts` (both `es` and `en` blocks)

**Interfaces:**
- Consumes: Task 1 `listWarehouses`, `patchWarehouse`, `Warehouse`.
- Produces: `useWarehouses(): { warehouses: Warehouse[]; multi: boolean; reload: () => void }` and `<WarehouseSelector value={string|null} onChange={(name: string|null) => void} warehouses={Warehouse[]} onSharesChanged={() => void} />`. `value === null` means "Todas". Renders `null` when `warehouses.length < 2`.

- [ ] **Step 1: Create the component**

```tsx
'use client'
// Warehouse selector + manual demand-share editor (feature 5.4).
// Self-contained: pages mount it and only receive the selected warehouse.
// Renders nothing for mono-warehouse tenants (spec: zero visual change).
import { useCallback, useEffect, useState } from 'react'
import { listWarehouses, patchWarehouse } from '@/lib/api'
import type { Warehouse } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { Warehouse as WarehouseIcon, Percent, X } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8',
}

export function useWarehouses() {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const reload = useCallback(() => {
    listWarehouses().then(setWarehouses).catch(() => setWarehouses([]))
  }, [])
  useEffect(() => { reload() }, [reload])
  return { warehouses, multi: warehouses.length >= 2, reload }
}

export function WarehouseSelector({ value, onChange, warehouses, onSharesChanged }: {
  value: string | null
  onChange: (name: string | null) => void
  warehouses: Warehouse[]
  onSharesChanged?: () => void
}) {
  const { t } = useLanguage()
  const [editingShares, setEditingShares] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  if (warehouses.length < 2) return null

  const noShares = warehouses.every(w => w.demand_share == null)

  async function saveShares() {
    setSaving(true)
    try {
      for (const w of warehouses) {
        const raw = draft[w.name]
        if (raw === undefined) continue
        const num = raw === '' ? null : Number(raw)
        if (num !== null && (Number.isNaN(num) || num < 0 || num > 100)) continue
        if (num !== w.demand_share) await patchWarehouse(w.name, num)
      }
      setEditingShares(false)
      onSharesChanged?.()
    } finally { setSaving(false) }
  }

  const pill = (active: boolean): React.CSSProperties => ({
    all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
    fontSize: 11.5, fontWeight: 600,
    background: active ? 'rgba(129,140,248,0.12)' : 'transparent',
    color: active ? C.indigo : C.dim,
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div role="tablist" aria-label={t('inventory.wh_selector_aria')}
           style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        <WarehouseIcon size={14} color={C.dim} />
        <button role="tab" aria-selected={value === null}
                onClick={() => onChange(null)} style={pill(value === null)}>
          {t('inventory.wh_all')}
        </button>
        {warehouses.map(w => (
          <button key={w.id} role="tab" aria-selected={value === w.name}
                  onClick={() => onChange(w.name)} style={pill(value === w.name)}>
            {w.name}
          </button>
        ))}
        <button onClick={() => { setEditingShares(v => !v); setDraft({}) }}
                aria-label={t('inventory.wh_shares_edit_aria')}
                style={{ ...pill(false), display: 'flex', alignItems: 'center', gap: 4 }}>
          <Percent size={12} /> {t('inventory.wh_shares_btn')}
        </button>
      </div>

      {noShares && !editingShares && (
        <div style={{ fontSize: 11, color: C.dim }}>
          {t('inventory.wh_shares_nudge')}
        </div>
      )}

      {editingShares && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          padding: '8px 12px', borderRadius: 8,
          background: C.surface, border: `1px solid ${C.border}`,
        }}>
          <span style={{ fontSize: 11, color: C.dim }}>{t('inventory.wh_shares_label')}</span>
          {warehouses.map(w => (
            <label key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: C.text }}>
              {w.name}
              <input
                type="number" min={0} max={100}
                defaultValue={w.demand_share ?? ''}
                onChange={e => setDraft(d => ({ ...d, [w.name]: e.target.value }))}
                style={{ width: 56, background: 'transparent', border: `1px solid ${C.border}`,
                         borderRadius: 6, color: C.text, fontSize: 12, padding: '3px 6px' }}
              />%
            </label>
          ))}
          <button onClick={saveShares} disabled={saving}
                  style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.indigo }}>
            {saving ? t('common.saving') : t('common.save')}
          </button>
          <button onClick={() => setEditingShares(false)} aria-label={t('common.cancel')}
                  style={{ all: 'unset', cursor: 'pointer', display: 'flex' }}>
            <X size={13} color={C.dim} />
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add translations** (both blocks; `common.save`/`common.saving`/`common.cancel` likely exist — check with grep and only add what's missing)

```ts
// es block:
'inventory.wh_selector_aria': 'Bodega',
'inventory.wh_all': 'Todas',
'inventory.wh_shares_btn': 'Reparto de demanda',
'inventory.wh_shares_edit_aria': 'Editar reparto de demanda por bodega',
'inventory.wh_shares_label': '¿Qué % de la venta sale de cada bodega?',
'inventory.wh_shares_nudge': 'Tienes varias bodegas sin reparto de demanda configurado — Faro asigna toda la demanda a la bodega principal hasta que definas los porcentajes.',
// en block:
'inventory.wh_selector_aria': 'Warehouse',
'inventory.wh_all': 'All',
'inventory.wh_shares_btn': 'Demand split',
'inventory.wh_shares_edit_aria': 'Edit per-warehouse demand split',
'inventory.wh_shares_label': 'What % of sales happens at each warehouse?',
'inventory.wh_shares_nudge': 'You have several warehouses with no demand split configured — Faro assigns all demand to the default warehouse until you set the percentages.',
```

- [ ] **Step 3: Typecheck** — `cd Frontend && npx tsc --noEmit` → exit 0
- [ ] **Step 4: Commit**

```bash
git add Frontend/src/components/inventory/WarehouseControls.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(ui): warehouse selector with demand-share editor"
```

---

### Task 3: Per-warehouse view in `/inventory`

**Files:**
- Create: `Frontend/src/components/inventory/WarehouseStatusTable.tsx`
- Modify: `Frontend/src/app/inventory/page.tsx` (mount selector + conditional table)
- Modify: `Frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: Task 1 `getStatusByWarehouse`, `createTransfer`, `WarehouseStatusItem`; Task 2 `useWarehouses`, `WarehouseSelector`.
- Produces: `<WarehouseStatusTable sessionId={string} warehouse={string} onTransferCreated={() => void} />` — fetches by_warehouse itself, filters to `warehouse`, renders semáforo rows; rows with `recommended_action === 'transfer'` show a 🔁 line + "Crear transferencia" button that calls `createTransfer` pre-filled.

- [ ] **Step 1: Create `WarehouseStatusTable.tsx`**

```tsx
'use client'
// Per-warehouse semáforo table (feature 5.4). Fetches the network-aware
// by-warehouse status and renders one warehouse's rows, including the
// TRANSFER suggestions produced by the backend's network pass.
import { useCallback, useEffect, useState } from 'react'
import { getStatusByWarehouse, createTransfer } from '@/lib/api'
import type { WarehouseStatusItem, InventorySignal } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/States'
import { ArrowLeftRight } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8', green: '#22c55e',
}

const SIGNAL_COLORS: Record<string, string> = {
  PEDIR_YA: 'var(--signal-order-now-fg)',
  PEDIR_PRONTO: 'var(--signal-order-soon-fg)',
  OK: 'var(--signal-ok-fg, #22c55e)',
  SOBRESTOCK: 'var(--signal-overstock-fg, #818cf8)',
  SIN_DATOS: 'var(--dim)',
}

export function WarehouseStatusTable({ sessionId, warehouse, onTransferCreated }: {
  sessionId: string
  warehouse: string
  onTransferCreated?: () => void
}) {
  const { t } = useLanguage()
  const [items, setItems] = useState<WarehouseStatusItem[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [sendingSku, setSendingSku] = useState<string | null>(null)
  const [sentSkus, setSentSkus] = useState<Set<string>>(new Set())

  const load = useCallback(() => {
    setError(null)
    getStatusByWarehouse(sessionId)
      .then(r => setItems(r.items))
      .catch(e => setError(e))
  }, [sessionId])

  useEffect(() => { load() }, [load])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (items === null) return <LoadingState />

  const rows = items.filter(i => i.warehouse === warehouse)
  if (rows.length === 0) {
    return <EmptyState title={t('inventory.wh_empty_title')}
                       subtitle={t('inventory.wh_empty_sub')} />
  }

  async function sendTransfer(row: WarehouseStatusItem) {
    const ts = row.transfer_suggestion
    if (!ts) return
    setSendingSku(row.sku)
    try {
      await createTransfer(ts.from_warehouse, row.warehouse,
                           [{ sku: row.sku, qty: ts.qty }])
      setSentSkus(prev => new Set(prev).add(row.sku))
      onTransferCreated?.()
    } finally { setSendingSku(null) }
  }

  const th: React.CSSProperties = { textAlign: 'left', fontSize: 10.5, color: C.dim,
    fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', padding: '6px 10px' }
  const td: React.CSSProperties = { fontSize: 12.5, color: C.text, padding: '8px 10px',
    borderTop: `1px solid ${C.border}` }

  return (
    <div style={{ overflowX: 'auto', border: `1px solid ${C.border}`, borderRadius: 10 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', background: C.surface }}>
        <thead><tr>
          <th style={th}>{t('inventory.col_sku')}</th>
          <th style={th}>{t('inventory.wh_col_stock')}</th>
          <th style={th}>{t('inventory.wh_col_coverage')}</th>
          <th style={th}>{t('inventory.wh_col_signal')}</th>
          <th style={th}>{t('inventory.wh_col_action')}</th>
        </tr></thead>
        <tbody>
          {rows.map(row => {
            const ts = row.transfer_suggestion
            const sent = sentSkus.has(row.sku)
            return (
              <tr key={`${row.sku}|${row.warehouse}`}>
                <td style={td}>{row.display_name || row.sku}</td>
                <td style={td}>{row.current_stock ?? '—'}</td>
                <td style={td}>{row.coverage_days != null
                  ? `${row.coverage_days} ${t('inventory.wh_days')}` : '—'}</td>
                <td style={{ ...td, color: SIGNAL_COLORS[row.signal] || C.text, fontWeight: 600 }}>
                  {row.signal.replace('_', ' ')}
                </td>
                <td style={td}>
                  {row.recommended_action === 'transfer' && ts ? (
                    sent ? (
                      <span style={{ fontSize: 12, color: C.green, fontWeight: 600 }}>
                        {t('inventory.wh_transfer_sent')}
                      </span>
                    ) : (
                      <button onClick={() => sendTransfer(row)}
                              disabled={sendingSku === row.sku}
                              style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex',
                                       alignItems: 'center', gap: 6, color: C.indigo,
                                       fontSize: 12, fontWeight: 600 }}>
                        <ArrowLeftRight size={13} />
                        {t('inventory.wh_transfer_btn')
                          .replace('{qty}', String(ts.qty))
                          .replace('{from}', ts.from_warehouse)}
                      </button>
                    )
                  ) : row.recommended_action === 'order' && row.recommended_qty ? (
                    <span style={{ fontSize: 12, color: C.dim }}>
                      {t('inventory.wh_order_hint').replace('{qty}', String(row.recommended_qty))}
                    </span>
                  ) : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Mount in `/inventory`**

In `Frontend/src/app/inventory/page.tsx`, inside `InventoryPage()`:
1. Import: `import { useWarehouses, WarehouseSelector } from '@/components/inventory/WarehouseControls'` and `import { WarehouseStatusTable } from '@/components/inventory/WarehouseStatusTable'`.
2. Add state next to the existing `viewMode` state (`const [viewMode, setViewMode] = ...`, ~line 1133):

```tsx
const { warehouses, multi: multiWarehouse, reload: reloadWarehouses } = useWarehouses()
const [selectedWarehouse, setSelectedWarehouse] = useState<string | null>(null)
```

3. Render the selector directly above the main view-mode controls (find the toolbar that switches `viewMode`; place `<WarehouseSelector .../>` immediately before it), and when a warehouse is selected replace the main table with the per-warehouse one:

```tsx
{multiWarehouse && (
  <WarehouseSelector
    value={selectedWarehouse}
    onChange={setSelectedWarehouse}
    warehouses={warehouses}
    onSharesChanged={() => { reloadWarehouses(); /* reuse the page's existing reload of status */ }}
  />
)}
{selectedWarehouse && sessionId ? (
  <WarehouseStatusTable
    sessionId={sessionId}
    warehouse={selectedWarehouse}
    onTransferCreated={() => { /* call the page's existing status reload */ }}
  />
) : (
  /* existing table/view-mode rendering, unchanged */
)}
```

Locate the page's existing "reload status" function (grep `load(` / `fetchStatus` inside `InventoryPage`) and wire it into the two callbacks above. Keep the existing views untouched when `selectedWarehouse === null`.

- [ ] **Step 3: Add translations**

```ts
// es:
'inventory.wh_col_stock': 'Stock',
'inventory.wh_col_coverage': 'Cobertura',
'inventory.wh_col_signal': 'Señal',
'inventory.wh_col_action': 'Acción sugerida',
'inventory.wh_days': 'días',
'inventory.wh_empty_title': 'Sin datos en esta bodega',
'inventory.wh_empty_sub': 'Esta bodega no tiene stock ni demanda asignada para la sesión activa.',
'inventory.wh_transfer_btn': 'Transferir {qty} desde {from}',
'inventory.wh_transfer_sent': 'Transferencia creada ✓',
'inventory.wh_order_hint': 'Pedir {qty} al proveedor',
// en:
'inventory.wh_col_stock': 'Stock',
'inventory.wh_col_coverage': 'Coverage',
'inventory.wh_col_signal': 'Signal',
'inventory.wh_col_action': 'Suggested action',
'inventory.wh_days': 'days',
'inventory.wh_empty_title': 'No data in this warehouse',
'inventory.wh_empty_sub': 'This warehouse has no stock and no assigned demand for the active session.',
'inventory.wh_transfer_btn': 'Transfer {qty} from {from}',
'inventory.wh_transfer_sent': 'Transfer created ✓',
'inventory.wh_order_hint': 'Order {qty} from supplier',
```

- [ ] **Step 4: Typecheck** — `cd Frontend && npx tsc --noEmit` → exit 0
- [ ] **Step 5: Commit**

```bash
git add Frontend/src/components/inventory/WarehouseStatusTable.tsx Frontend/src/app/inventory/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(ui): per-warehouse semaphore view with transfer actions in /inventory"
```

---

### Task 4: Transferencias tab in `/pedidos`

**Files:**
- Create: `Frontend/src/components/po/TransfersPanel.tsx`
- Modify: `Frontend/src/app/pedidos/page.tsx`
- Modify: `Frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: Task 1 `listTransfers`, `receiveTransfer`, `cancelTransfer`, `Transfer`; Task 2 `useWarehouses`.
- Produces: `<TransfersPanel />` — fully self-contained (fetch, receive with partial quantities, cancel with the shared `useConfirm` dialog).

- [ ] **Step 1: Create `TransfersPanel.tsx`**

```tsx
'use client'
// Inter-warehouse transfers list + reception (feature 5.4).
// Mirrors the PO reception UX: partial quantities accumulate until complete.
import { useCallback, useEffect, useState } from 'react'
import { listTransfers, receiveTransfer, cancelTransfer } from '@/lib/api'
import type { Transfer } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { ArrowLeftRight, PackageCheck, XCircle } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8',
  green: '#22c55e', amber: '#f59e0b',
}

const STATUS_STYLE: Record<Transfer['status'], { color: string; key: string }> = {
  in_transit: { color: C.amber,  key: 'transfers.status_in_transit' },
  partial:    { color: C.amber,  key: 'transfers.status_partial' },
  received:   { color: C.green,  key: 'transfers.status_received' },
  cancelled:  { color: C.dim,    key: 'transfers.status_cancelled' },
}

function ReceiveForm({ transfer, onDone }: { transfer: Transfer; onDone: () => void }) {
  const { t } = useLanguage()
  const [qty, setQty] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const outstanding = (it: Transfer['items'][number]) => it.qty_sent - it.qty_received

  async function submit(all: boolean) {
    setSaving(true)
    try {
      const lines = all ? null : transfer.items
        .filter(it => outstanding(it) > 0)
        .map(it => ({ sku: it.sku, received_qty: Number(qty[it.sku] ?? 0) }))
        .filter(l => l.received_qty > 0)
      await receiveTransfer(transfer.id, lines)
      onDone()
    } finally { setSaving(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px',
                  borderTop: `1px dashed ${C.border}` }}>
      {transfer.items.filter(it => outstanding(it) > 0).map(it => (
        <label key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 8,
                                    fontSize: 12.5, color: C.text }}>
          <span style={{ flex: 1 }}>{it.sku}</span>
          <span style={{ fontSize: 11, color: C.dim }}>
            {t('transfers.outstanding').replace('{qty}', String(outstanding(it)))}
          </span>
          <input type="number" min={0} max={outstanding(it)}
                 value={qty[it.sku] ?? ''}
                 placeholder={String(outstanding(it))}
                 onChange={e => setQty(q => ({ ...q, [it.sku]: e.target.value }))}
                 style={{ width: 70, background: 'transparent', border: `1px solid ${C.border}`,
                          borderRadius: 6, color: C.text, fontSize: 12, padding: '4px 7px' }} />
        </label>
      ))}
      <div style={{ display: 'flex', gap: 12 }}>
        <button onClick={() => submit(true)} disabled={saving}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.green }}>
          {t('transfers.receive_all')}
        </button>
        <button onClick={() => submit(false)} disabled={saving}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.indigo }}>
          {t('transfers.receive_partial')}
        </button>
      </div>
    </div>
  )
}

export function TransfersPanel() {
  const { t, lang } = useLanguage()
  const { confirm, dialog } = useConfirm()
  const [transfers, setTransfers] = useState<Transfer[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [receivingId, setReceivingId] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    listTransfers().then(setTransfers).catch(e => setError(e))
  }, [])
  useEffect(() => { load() }, [load])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (transfers === null) return <LoadingState />
  if (transfers.length === 0) {
    return <EmptyState title={t('transfers.empty_title')} subtitle={t('transfers.empty_sub')} />
  }

  async function onCancel(tr: Transfer) {
    const okd = await confirm({
      title: t('transfers.cancel_title'),
      message: t('transfers.cancel_msg')
        .replace('{from}', tr.from_warehouse).replace('{to}', tr.to_warehouse),
    })
    if (!okd) return
    await cancelTransfer(tr.id)
    load()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {dialog}
      {transfers.map(tr => {
        const st = STATUS_STYLE[tr.status]
        const receivable = tr.status === 'in_transit' || tr.status === 'partial'
        return (
          <div key={tr.id} style={{ borderRadius: 10, border: `1px solid ${C.border}`,
                                    background: C.surface }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px' }}>
              <ArrowLeftRight size={15} color={C.indigo} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
                  {tr.from_warehouse} → {tr.to_warehouse}
                </div>
                <div style={{ fontSize: 11, color: C.dim }}>
                  {new Date(tr.created_at).toLocaleDateString(lang === 'es' ? 'es-CR' : 'en-US')}
                  {' · '}{tr.items.length} SKU{tr.items.length !== 1 ? 's' : ''}
                </div>
              </div>
              <span style={{ fontSize: 11.5, fontWeight: 700, color: st.color }}>
                {t(st.key)}
              </span>
              {receivable && (
                <>
                  <button onClick={() => setReceivingId(id => id === tr.id ? null : tr.id)}
                          style={{ all: 'unset', cursor: 'pointer', display: 'flex',
                                   alignItems: 'center', gap: 5, color: C.green,
                                   fontSize: 12, fontWeight: 600 }}>
                    <PackageCheck size={14} /> {t('transfers.receive_btn')}
                  </button>
                  {tr.status === 'in_transit' &&
                    tr.items.every(i => i.qty_received === 0) && (
                    <button onClick={() => onCancel(tr)}
                            aria-label={t('transfers.cancel_btn')}
                            style={{ all: 'unset', cursor: 'pointer', display: 'flex' }}>
                      <XCircle size={14} color={C.dim} />
                    </button>
                  )}
                </>
              )}
            </div>
            {receivingId === tr.id && receivable && (
              <ReceiveForm transfer={tr} onDone={() => { setReceivingId(null); load() }} />
            )}
          </div>
        )
      })}
    </div>
  )
}
```

Check the actual `useConfirm` export shape in `Frontend/src/components/ui/ConfirmDialog.tsx` (grep `export function useConfirm`) and adapt the two-line usage if its API differs.

- [ ] **Step 2: Mount as a tab in `/pedidos`**

In `Frontend/src/app/pedidos/page.tsx`:
1. Imports: `import { TransfersPanel } from '@/components/po/TransfersPanel'`, `import { useWarehouses } from '@/components/inventory/WarehouseControls'`, add `ArrowLeftRight` to the lucide import.
2. Inside `OrdersPage()`: `const { multi: multiWarehouse } = useWarehouses()` and `const [tab, setTab] = useState<'orders' | 'transfers'>('orders')`.
3. Directly under the header block, add the tab bar (renders only when `multiWarehouse`), and wrap the existing content:

```tsx
{multiWarehouse && (
  <div role="tablist" aria-label={t('transfers.tablist_aria')} style={{ display: 'flex', gap: 4 }}>
    <button role="tab" aria-selected={tab === 'orders'} onClick={() => setTab('orders')}
            style={tabStyle(tab === 'orders')}>{t('transfers.tab_orders')}</button>
    <button role="tab" aria-selected={tab === 'transfers'} onClick={() => setTab('transfers')}
            style={tabStyle(tab === 'transfers')}>{t('transfers.tab_transfers')}</button>
  </div>
)}
{tab === 'transfers' && multiWarehouse ? <TransfersPanel /> : (
  /* ALL existing page content below the header, unchanged */
)}
```

with the same pill style used elsewhere:

```tsx
const tabStyle = (active: boolean): React.CSSProperties => ({
  all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
  fontSize: 11.5, fontWeight: 600,
  background: active ? 'rgba(129,140,248,0.12)' : 'transparent',
  color: active ? '#818cf8' : C.dim,
})
```

- [ ] **Step 3: Add translations**

```ts
// es:
'transfers.tablist_aria': 'Pedidos y transferencias',
'transfers.tab_orders': 'Órdenes de compra',
'transfers.tab_transfers': 'Transferencias',
'transfers.status_in_transit': 'En tránsito',
'transfers.status_partial': 'Parcial',
'transfers.status_received': 'Recibida',
'transfers.status_cancelled': 'Cancelada',
'transfers.empty_title': 'Sin transferencias',
'transfers.empty_sub': 'Cuando muevas stock entre bodegas, el envío y la llegada se registran aquí.',
'transfers.receive_btn': 'Registrar llegada',
'transfers.receive_all': 'Llegó todo',
'transfers.receive_partial': 'Registrar cantidades',
'transfers.outstanding': 'Pendiente: {qty}',
'transfers.cancel_btn': 'Cancelar transferencia',
'transfers.cancel_title': '¿Cancelar esta transferencia?',
'transfers.cancel_msg': 'El stock en tránsito de {from} a {to} regresará a {from}.',
// en:
'transfers.tablist_aria': 'Orders and transfers',
'transfers.tab_orders': 'Purchase orders',
'transfers.tab_transfers': 'Transfers',
'transfers.status_in_transit': 'In transit',
'transfers.status_partial': 'Partial',
'transfers.status_received': 'Received',
'transfers.status_cancelled': 'Cancelled',
'transfers.empty_title': 'No transfers',
'transfers.empty_sub': 'When you move stock between warehouses, the dispatch and the arrival are tracked here.',
'transfers.receive_btn': 'Record arrival',
'transfers.receive_all': 'Everything arrived',
'transfers.receive_partial': 'Record quantities',
'transfers.outstanding': 'Outstanding: {qty}',
'transfers.cancel_btn': 'Cancel transfer',
'transfers.cancel_title': 'Cancel this transfer?',
'transfers.cancel_msg': 'Stock in transit from {from} to {to} will return to {from}.',
```

- [ ] **Step 4: Typecheck** — `cd Frontend && npx tsc --noEmit` → exit 0
- [ ] **Step 5: Commit**

```bash
git add Frontend/src/components/po/TransfersPanel.tsx Frontend/src/app/pedidos/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(ui): transfers tab with partial reception in /pedidos"
```

---

### Task 5: Transfer suggestion cards in `/hoy`

**Files:**
- Create: `Frontend/src/components/inventory/TransferSuggestions.tsx`
- Modify: `Frontend/src/app/hoy/page.tsx`
- Modify: `Frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: Task 1 `getStatusByWarehouse`, `createTransfer`, `WarehouseStatusItem`; Task 2 `useWarehouses`.
- Produces: `<TransferSuggestions sessionId={string} />` — self-contained section; renders nothing when the tenant has <2 warehouses or there are no suggestions.

- [ ] **Step 1: Create `TransferSuggestions.tsx`**

```tsx
'use client'
// 🔁 transfer suggestion cards for /hoy (feature 5.4): stock exists in the
// network, just in the wrong place — approving creates the transfer
// (in_transit) instead of adding a purchase to the cart.
import { useCallback, useEffect, useState } from 'react'
import { getStatusByWarehouse, createTransfer } from '@/lib/api'
import type { WarehouseStatusItem } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useWarehouses } from '@/components/inventory/WarehouseControls'
import { ArrowLeftRight } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8', green: '#22c55e',
}

export function TransferSuggestions({ sessionId }: { sessionId: string }) {
  const { t } = useLanguage()
  const { multi } = useWarehouses()
  const [suggestions, setSuggestions] = useState<WarehouseStatusItem[]>([])
  const [busySku, setBusySku] = useState<string | null>(null)
  const [done, setDone] = useState<Set<string>>(new Set())

  const load = useCallback(() => {
    if (!multi) return
    getStatusByWarehouse(sessionId)
      .then(r => setSuggestions(r.items.filter(i => i.recommended_action === 'transfer')))
      .catch(() => setSuggestions([]))
  }, [sessionId, multi])
  useEffect(() => { load() }, [load])

  if (!multi || suggestions.length === 0) return null

  async function approve(row: WarehouseStatusItem) {
    const ts = row.transfer_suggestion
    if (!ts) return
    setBusySku(`${row.sku}|${row.warehouse}`)
    try {
      await createTransfer(ts.from_warehouse, row.warehouse, [{ sku: row.sku, qty: ts.qty }])
      setDone(prev => new Set(prev).add(`${row.sku}|${row.warehouse}`))
    } finally { setBusySku(null) }
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: C.text,
                   display: 'flex', alignItems: 'center', gap: 7 }}>
        <ArrowLeftRight size={15} color={C.indigo} />
        {t('hoy.transfers_title').replace('{n}', String(suggestions.length))}
      </h2>
      <p style={{ margin: 0, fontSize: 11.5, color: C.dim }}>{t('hoy.transfers_sub')}</p>
      {suggestions.map(row => {
        const ts = row.transfer_suggestion!
        const key = `${row.sku}|${row.warehouse}`
        const isDone = done.has(key)
        return (
          <div key={key} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
            borderRadius: 10, background: C.surface,
            border: `1px solid ${C.border}`, borderLeft: `4px solid ${C.indigo}`,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
                {row.display_name || row.sku}
              </div>
              <div style={{ fontSize: 11.5, color: C.dim }}>
                {t('hoy.transfers_line')
                  .replace('{qty}', String(ts.qty))
                  .replace('{from}', ts.from_warehouse)
                  .replace('{to}', row.warehouse)
                  .replace('{days}', String(ts.donor_coverage_days_after))}
              </div>
            </div>
            {isDone ? (
              <span style={{ fontSize: 12, fontWeight: 700, color: C.green }}>
                {t('hoy.transfers_done')}
              </span>
            ) : (
              <button onClick={() => approve(row)} disabled={busySku === key}
                      style={{ all: 'unset', cursor: 'pointer', padding: '6px 14px',
                               borderRadius: 8, background: 'rgba(129,140,248,0.12)',
                               color: C.indigo, fontSize: 12, fontWeight: 700 }}>
                {t('hoy.transfers_approve')}
              </button>
            )}
          </div>
        )
      })}
    </section>
  )
}
```

- [ ] **Step 2: Mount in `/hoy`**

In `Frontend/src/app/hoy/page.tsx`: import the component and render `{sessionId && <TransferSuggestions sessionId={sessionId} />}` immediately BEFORE the urgent `<ActionCard>` list section (~line 1079 — anchor on the JSX block that maps urgent items to `ActionCard`). Transfers come first: they free stock without spending money.

- [ ] **Step 3: Add translations**

```ts
// es:
'hoy.transfers_title': '{n} se resuelven moviendo stock, sin comprar',
'hoy.transfers_sub': 'Otra bodega tiene excedente de estos productos — transferir evita la compra.',
'hoy.transfers_line': 'Mover {qty} desde {from} a {to} — {from} queda con {days} días de cobertura.',
'hoy.transfers_approve': 'Crear transferencia',
'hoy.transfers_done': 'En tránsito ✓',
// en:
'hoy.transfers_title': '{n} can be solved by moving stock, no purchase needed',
'hoy.transfers_sub': 'Another warehouse holds surplus of these products — transferring avoids the purchase.',
'hoy.transfers_line': 'Move {qty} from {from} to {to} — {from} keeps {days} days of coverage.',
'hoy.transfers_approve': 'Create transfer',
'hoy.transfers_done': 'In transit ✓',
```

- [ ] **Step 4: Typecheck** — `cd Frontend && npx tsc --noEmit` → exit 0
- [ ] **Step 5: Commit**

```bash
git add Frontend/src/components/inventory/TransferSuggestions.tsx Frontend/src/app/hoy/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(ui): transfer suggestion cards in /hoy"
```

---

### Task 6: Per-warehouse copy in the daily alerts (backend)

**Files:**
- Modify: `backend/inventory/service.py` (`run_daily_inventory_alerts`, ~line 2035)
- Modify: `backend/notifications/whatsapp.py` (`build_inventory_alert_text`)
- Test: `backend/tests/test_alert_transfer_copy.py`

**Interfaces:**
- Consumes: `get_inventory_status_by_warehouse` (backend plan Task 5), `warehouse_service.count_warehouses`.
- Produces: `build_inventory_alert_text(critical_items, warning_items, inventory_url, transfer_count=0)` — new optional kwarg; when > 0 a line `"🔁 N producto(s) se resuelven moviendo stock, sin comprar"` is inserted before the closing URL line. `run_daily_inventory_alerts` computes `transfer_count` per tenant (0 for mono-warehouse tenants — by_warehouse is never called for them).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_alert_transfer_copy.py
"""Daily alert mentions network transfer suggestions (feature 5.4, spec §2)."""

from backend.notifications.whatsapp import build_inventory_alert_text


class TestAlertTransferCopy:
    def test_transfer_line_present_when_count_positive(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy",
                                          transfer_count=2)
        assert "🔁 2 productos se resuelven moviendo stock" in text
        # URL stays the last line
        assert text.strip().splitlines()[-1].endswith("http://x/hoy")

    def test_singular_form(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy",
                                          transfer_count=1)
        assert "🔁 1 producto se resuelve moviendo stock" in text

    def test_absent_when_zero(self):
        text = build_inventory_alert_text([], [{"sku": "A"}], "http://x/hoy")
        assert "🔁" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_alert_transfer_copy.py -q`
Expected: FAIL — unexpected keyword `transfer_count`.

- [ ] **Step 3: Implement**

In `backend/notifications/whatsapp.py`, change the signature and add the line
before the URL append:

```python
def build_inventory_alert_text(
    critical_items: list[dict],
    warning_items: list[dict],
    inventory_url: str,
    transfer_count: int = 0,
) -> str:
```

```python
    if transfer_count > 0:
        # Network-aware suggestion (feature 5.4): stock exists, it's just in
        # the wrong warehouse — no money needs to be spent.
        lines.append(
            f"🔁 {transfer_count} producto{'s' if transfer_count != 1 else ''} "
            f"se resuelve{'n' if transfer_count != 1 else ''} moviendo stock, sin comprar"
        )
    lines.append(f"Ver y aprobar: {inventory_url}")
```

In `backend/inventory/service.py` `run_daily_inventory_alerts`, after computing
`critical`/`warning`:

```python
            # Transfer suggestions (feature 5.4): only meaningful — and only
            # computed — for tenants with 2+ warehouses.
            from backend.inventory import warehouse_service as wh_svc
            transfer_count = 0
            if wh_svc.count_warehouses(tid) >= 2:
                try:
                    wh_items = get_inventory_status_by_warehouse(tid, session["session_id"])
                    transfer_count = sum(
                        1 for i in wh_items if i.get("recommended_action") == "transfer")
                except Exception as e:
                    log.debug("alert transfer count failed tenant=%s: %s", tid, e)
```

and pass `transfer_count=transfer_count` to the `build_inventory_alert_text` call.
(The email path keeps its current builder — WhatsApp is the channel with the
compact digest; extending the email template is out of scope here.)

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_alert_transfer_copy.py tests/test_notifications.py tests/test_demo_and_alerts.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/notifications/whatsapp.py backend/inventory/service.py backend/tests/test_alert_transfer_copy.py
git commit -m "feat(alerts): mention network transfer suggestions in daily WhatsApp digest"
```

---

### Task 7: Regression + plan doc

- [ ] **Step 1: Backend suite** — `cd backend && python -m pytest tests/ -q` → 0 failures
- [ ] **Step 2: Frontend typecheck** — `cd Frontend && npx tsc --noEmit` → exit 0
- [ ] **Step 3: Update `docs/plan_general_faro_2026-07-18.md`** — change the `5.4 (backend)` row note from "**UI pendiente**" to "✅ UI + alertas (2026-07-22)"; follow-up left: Alegra/Siigo `store` mapping (spec §1c) and Ctrl-K per-warehouse breakdown.
- [ ] **Step 4: Commit**

```bash
git add docs/plan_general_faro_2026-07-18.md
git commit -m "docs: mark 5.4 UI + alerts complete in general plan"
```

## Deliberately out of scope (follow-ups)

- **Alegra/Siigo `store` mapping** (spec §1c): `ProviderSaleLine` has no store field yet; needs provider-API investigation — own mini-spec.
- **Ctrl-K per-warehouse breakdown** (spec §5): low-risk garnish; fold into the next global-search touch.
- **PO destination selector in the `/hoy` cart** (spec §4 UI): the cart flow is being polished by the concurrent PO-flow session — adding the selector now would collide; do it after that lands.

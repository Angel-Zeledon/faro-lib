'use client'
// Warehouse selector + manual demand-share editor (feature 5.4).
// Self-contained: pages mount it and only receive the selected warehouse.
// Renders nothing for mono-warehouse tenants (spec: zero visual change).
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  listWarehouses, patchWarehouse, createWarehouse,
  listTransferLanes, upsertTransferLane, deleteTransferLane,
} from '@/lib/api'
import type { Warehouse, TransferLane } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { Warehouse as WarehouseIcon, Percent, Plus, X, ArrowLeftRight } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: 'var(--accent)',
}

// The tenant default warehouse is a REAL row, not a synthetic entry: the
// backend auto-creates it and every "no destination given" path resolves to it
// by name (backend/inventory/warehouse_service.py DEFAULT_WAREHOUSE, read by
// reception_service/roi_service). Pickers must therefore never render a
// separate "default" option next to it — that shows the same warehouse twice.
export const DEFAULT_WAREHOUSE_NAME = 'principal'

/**
 * The warehouse a purchase order lands in when the user does not pick one.
 * Precedence mirrors the backend rule: the explicit is_default flag, then the
 * canonical DEFAULT_WAREHOUSE_NAME (auto-created rows carry is_default=false,
 * so without this an emoji/Capitalized name would win the alphabetical sort),
 * then the first warehouse on file. Null only when the tenant has none.
 */
export function defaultWarehouse(warehouses: Warehouse[]): Warehouse | null {
  return warehouses.find(w => w.is_default)
    ?? warehouses.find(w => w.name === DEFAULT_WAREHOUSE_NAME)
    ?? warehouses[0]
    ?? null
}

// Single-flight cache: several components mount useWarehouses on one page
// (/hoy renders three), and without this each fired its own identical GET.
// One in-flight promise is shared; reload() busts it (e.g. after creating a
// warehouse) so the refetch hits the network.
let _warehousesPromise: Promise<Warehouse[]> | null = null

function fetchWarehousesShared(): Promise<Warehouse[]> {
  if (!_warehousesPromise) {
    _warehousesPromise = listWarehouses().catch(() => {
      _warehousesPromise = null  // don't cache failures
      return [] as Warehouse[]
    })
  }
  return _warehousesPromise
}

interface WarehousesValue {
  warehouses: Warehouse[]
  multi: boolean
  reload: () => void
}

// Shared warehouse state (review finding #7): without a provider each
// useWarehouses() instance kept its own copy, so after AddWarehouse created
// warehouse #2 only the calling instance flipped `multi` — /hoy's transfer
// panel, /pedidos' tab bar and the global SKU search overlay stayed
// mono-warehouse until remount. The provider owns ONE copy; reload() updates
// it and every consumer re-renders together.
const WarehousesContext = createContext<WarehousesValue | null>(null)

export function WarehousesProvider({ children }: { children: React.ReactNode }) {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const reload = useCallback(() => {
    _warehousesPromise = null
    fetchWarehousesShared().then(setWarehouses)
  }, [])
  useEffect(() => {
    let alive = true
    fetchWarehousesShared().then(w => { if (alive) setWarehouses(w) })
    return () => { alive = false }
  }, [])
  return (
    <WarehousesContext.Provider value={{ warehouses, multi: warehouses.length >= 2, reload }}>
      {children}
    </WarehousesContext.Provider>
  )
}

export function useWarehouses(): WarehousesValue {
  const ctx = useContext(WarehousesContext)
  // Standalone fallback for mounts outside the provider. Hooks must run
  // unconditionally; the effect no-ops when the provider owns the data.
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const reload = useCallback(() => {
    _warehousesPromise = null
    fetchWarehousesShared().then(setWarehouses)
  }, [])
  const standalone = ctx === null
  useEffect(() => {
    if (!standalone) return
    let alive = true
    fetchWarehousesShared().then(w => { if (alive) setWarehouses(w) })
    return () => { alive = false }
  }, [standalone])
  if (ctx) return ctx
  return { warehouses, multi: warehouses.length >= 2, reload }
}

function AddWarehouse({ onCreated, subtle }: { onCreated: () => void; subtle?: boolean }) {
  // The chicken-and-egg closer (walkthrough finding #14): warehouses used to
  // be creatable only via API or a stock CSV, so a customer clicking around
  // could never START using multi-warehouse. For mono-warehouse tenants this
  // renders as one subtle pill — the only multi-warehouse affordance they see.
  const { t } = useLanguage()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)

  async function save() {
    const trimmed = name.trim()
    if (!trimmed) return
    setSaving(true)
    try {
      await createWarehouse(trimmed)
      setName('')
      setAdding(false)
      onCreated()
    } catch {
      // The api.ts interceptor already surfaces a standard toast (e.g. the
      // 403 a viewer gets). Swallow here so the rejection isn't uncaught —
      // the form stays open for a retry.
    } finally { setSaving(false) }
  }

  if (!adding) {
    return (
      <button onClick={() => setAdding(true)}
              style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex',
                       alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 7,
                       fontSize: 11.5, fontWeight: 600,
                       color: subtle ? 'var(--dim)' : C.indigo }}>
        <Plus size={12} /> {t('inventory.wh_add_btn')}
      </button>
    )
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input autoFocus value={name}
             name="warehouse_name" aria-label={t('inventory.wh_add_placeholder')}
             placeholder={t('inventory.wh_add_placeholder')}
             onChange={e => setName(e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setAdding(false) }}
             style={{ width: 140, background: 'transparent', border: `1px solid ${C.border}`,
                      borderRadius: 6, color: C.text, fontSize: 12, padding: '4px 8px' }} />
      <button onClick={save} disabled={saving || !name.trim()}
              style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.indigo }}>
        {saving ? t('common.saving') : t('common.save')}
      </button>
      <button onClick={() => setAdding(false)} aria-label={t('common.cancel')}
              style={{ all: 'unset', cursor: 'pointer', display: 'flex' }}>
        <X size={13} color={C.dim} />
      </button>
    </span>
  )
}

function TransferLanesEditor({ warehouses }: { warehouses: Warehouse[] }) {
  // Transfer lanes (PENDIENTES #2): a lane gives a move between two warehouses
  // a lead time and a cost, which is what lets Faro decide whether moving
  // stock actually beats buying it. Unconfigured pairs use the backend default
  // (1 day, free) — the empty state says so instead of pretending it's broken.
  const { t } = useLanguage()
  const [lanes, setLanes] = useState<TransferLane[] | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [days, setDays] = useState('1')
  const [costPerUnit, setCostPerUnit] = useState('0')
  const [fixedCost, setFixedCost] = useState('0')
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    listTransferLanes().then(setLanes).catch(() => setLanes([]))
  }, [])
  useEffect(() => { load() }, [load])

  const names = warehouses.map(w => w.name)
  const effectiveFrom = from || names[0] || ''
  const effectiveTo = to || names.find(n => n !== effectiveFrom) || ''

  async function save() {
    if (!effectiveFrom || !effectiveTo || effectiveFrom === effectiveTo) return
    setSaving(true)
    try {
      await upsertTransferLane({
        from_warehouse: effectiveFrom, to_warehouse: effectiveTo,
        lead_time_days: Math.max(0, Number(days) || 0),
        cost_per_unit: Math.max(0, Number(costPerUnit) || 0),
        fixed_cost: Math.max(0, Number(fixedCost) || 0),
      })
      load()
    } catch {
      // The api.ts interceptor toasts the failure (e.g. a viewer's 403);
      // swallow so the rejection isn't uncaught and the form stays open.
    } finally { setSaving(false) }
  }

  async function remove(lane: TransferLane) {
    try {
      await deleteTransferLane(lane.from_warehouse, lane.to_warehouse)
      load()
    } catch { /* interceptor toasts it */ }
  }

  const field: React.CSSProperties = {
    background: 'transparent', border: `1px solid ${C.border}`, borderRadius: 6,
    color: C.text, fontSize: 12, padding: '3px 6px',
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      padding: '8px 12px', borderRadius: 8,
      background: C.surface, border: `1px solid ${C.border}`,
    }}>
      <span style={{ fontSize: 11, color: C.dim }}>{t('transfers.lanes_label')}</span>

      {lanes !== null && lanes.length === 0 && (
        <span style={{ fontSize: 11, color: C.dim }}>{t('transfers.lanes_empty')}</span>
      )}
      {(lanes ?? []).map(lane => (
        <div key={lane.id} style={{ display: 'flex', alignItems: 'center', gap: 8,
                                    fontSize: 12, color: C.text }}>
          <span>
            {t('transfers.lanes_row')
              .replace('{from}', lane.from_warehouse)
              .replace('{to}', lane.to_warehouse)
              .replace('{days}', String(lane.lead_time_days))
              .replace('{cost}', String(lane.cost_per_unit))
              .replace('{fixed}', String(lane.fixed_cost))}
          </span>
          <button onClick={() => remove(lane)}
                  aria-label={`${t('transfers.lanes_delete')} ${lane.from_warehouse} ${lane.to_warehouse}`}
                  style={{ all: 'unset', cursor: 'pointer', display: 'flex' }}>
            <X size={12} color={C.dim} />
          </button>
        </div>
      ))}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11, color: C.dim }}>
          {t('transfers.lanes_from')}{' '}
          <select name="lane_from" value={effectiveFrom}
                  onChange={e => setFrom(e.target.value)} style={field}>
            {names.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, color: C.dim }}>
          {t('transfers.lanes_to')}{' '}
          <select name="lane_to" value={effectiveTo}
                  onChange={e => setTo(e.target.value)} style={field}>
            {names.filter(n => n !== effectiveFrom)
                  .map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, color: C.dim }}>
          {t('transfers.lanes_days')}{' '}
          <input type="number" min={0} name="lane_days" value={days}
                 onChange={e => setDays(e.target.value)}
                 style={{ ...field, width: 56 }} />
        </label>
        <label style={{ fontSize: 11, color: C.dim }}>
          {t('transfers.lanes_cost_per_unit')}{' '}
          <input type="number" min={0} step="0.01" name="lane_cost_per_unit"
                 value={costPerUnit} onChange={e => setCostPerUnit(e.target.value)}
                 style={{ ...field, width: 72 }} />
        </label>
        <label style={{ fontSize: 11, color: C.dim }}>
          {t('transfers.lanes_fixed_cost')}{' '}
          <input type="number" min={0} step="0.01" name="lane_fixed_cost"
                 value={fixedCost} onChange={e => setFixedCost(e.target.value)}
                 style={{ ...field, width: 72 }} />
        </label>
        <button onClick={save} disabled={saving || effectiveFrom === effectiveTo}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 12,
                         fontWeight: 600, color: C.indigo }}>
          {saving ? t('common.saving') : t('transfers.lanes_add')}
        </button>
      </div>
    </div>
  )
}


export function WarehouseSelector({ value, onChange, warehouses, onSharesChanged, onCreated }: {
  value: string | null
  onChange: (name: string | null) => void
  warehouses: Warehouse[]
  onSharesChanged?: () => void
  onCreated?: () => void
}) {
  const { t } = useLanguage()
  // Warehouse mutations refresh the shared state directly; the optional
  // callbacks are for page-specific side effects (e.g. reloading status).
  const { reload } = useWarehouses()
  const [editingShares, setEditingShares] = useState(false)
  const [editingLanes, setEditingLanes] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  // Mono-warehouse: no selector, only the discreet add-warehouse entry point.
  if (warehouses.length < 2) {
    return (
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <AddWarehouse subtle onCreated={() => { reload(); onCreated?.() }} />
      </div>
    )
  }

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
      reload()
      onSharesChanged?.()
    } catch {
      // Interceptor toasts the failure (e.g. viewer 403); don't leave the
      // rejection uncaught.
    } finally { setSaving(false) }
  }

  const pill = (active: boolean): React.CSSProperties => ({
    all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
    fontSize: 11.5, fontWeight: 600,
    background: active ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
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
        <button onClick={() => setEditingLanes(v => !v)}
                aria-label={t('transfers.lanes_edit_aria')}
                style={{ ...pill(false), display: 'flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeftRight size={12} /> {t('transfers.lanes_btn')}
        </button>
        <AddWarehouse onCreated={() => { reload(); onCreated?.() }} />
      </div>

      {editingLanes && <TransferLanesEditor warehouses={warehouses} />}

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
                name={`demand-share-${w.name}`}
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

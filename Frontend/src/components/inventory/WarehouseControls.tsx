'use client'
// Warehouse selector + manual demand-share editor (feature 5.4).
// Self-contained: pages mount it and only receive the selected warehouse.
// Renders nothing for mono-warehouse tenants (spec: zero visual change).
import { useCallback, useEffect, useState } from 'react'
import { listWarehouses, patchWarehouse, createWarehouse } from '@/lib/api'
import type { Warehouse } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { Warehouse as WarehouseIcon, Percent, Plus, X } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8',
}

// Single-flight cache: several components mount useWarehouses on one page
// (/hoy renders three), and without this each fired its own identical GET.
// One in-flight promise is shared; reload() busts it (e.g. after creating a
// warehouse) so the next mount refetches.
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

export function useWarehouses() {
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

export function WarehouseSelector({ value, onChange, warehouses, onSharesChanged, onCreated }: {
  value: string | null
  onChange: (name: string | null) => void
  warehouses: Warehouse[]
  onSharesChanged?: () => void
  onCreated?: () => void
}) {
  const { t } = useLanguage()
  const [editingShares, setEditingShares] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  // Mono-warehouse: no selector, only the discreet add-warehouse entry point.
  if (warehouses.length < 2) {
    return (
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <AddWarehouse subtle onCreated={() => onCreated?.()} />
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
        <AddWarehouse onCreated={() => onCreated?.()} />
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

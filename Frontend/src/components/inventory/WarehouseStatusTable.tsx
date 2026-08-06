'use client'
// Per-warehouse semáforo table (feature 5.4). Fetches the network-aware
// by-warehouse status and renders one warehouse's rows, including the
// TRANSFER suggestions produced by the backend's network pass.
import { useCallback, useEffect, useState } from 'react'
import { getStatusByWarehouse, createTransfer, upsertInventoryStock } from '@/lib/api'
import type { WarehouseStatusItem, CoverageUnit } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/States'
import SignalBadge from '@/components/ui/SignalBadge'
import { coverageUnitShort } from '@/lib/period'
import { transferReasonText } from '@/lib/transferReason'
import { ArrowLeftRight } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: 'var(--accent)', green: '#22c55e',
}

export function WarehouseStatusTable({ sessionId, warehouse, onTransferCreated }: {
  sessionId: string
  warehouse: string
  onTransferCreated?: () => void
}) {
  const { t } = useLanguage()
  const [items, setItems] = useState<WarehouseStatusItem[] | null>(null)
  const [coverageUnit, setCoverageUnit] = useState<CoverageUnit>('day')
  const [error, setError] = useState<unknown>(null)
  const [sendingSku, setSendingSku] = useState<string | null>(null)
  const [sentSkus, setSentSkus] = useState<Set<string>>(new Set())
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [savingSku, setSavingSku] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    getStatusByWarehouse(sessionId)
      .then(r => { setItems(r.items); setCoverageUnit(r.coverage_unit ?? 'day') })
      .catch(e => setError(e))
  }, [sessionId])

  useEffect(() => { load() }, [load])

  if (error) return <ErrorState error={error} onRetry={load} />
  if (items === null) return <LoadingState />

  const rows = items.filter(i => i.warehouse === warehouse)
  if (rows.length === 0) {
    return <EmptyState title={t('inventory.wh_empty_title')}
                       body={t('inventory.wh_empty_sub')} />
  }

  /** Count this warehouse's stock, in this warehouse's row.
   *
   *  The "Todas" editor could not do this: it shows the network SUM and posts
   *  without a warehouse, so a typed total landed on one location and the rest
   *  was added on top. Here the destination is the tab the user is looking at.
   */
  async function saveStock(sku: string, raw: string) {
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) return
    setSavingSku(sku)
    try {
      await upsertInventoryStock(sku, { current_stock: value, warehouse })
      setDrafts(prev => { const next = { ...prev }; delete next[sku]; return next })
      load()
    } catch (e) {
      setError(e)
    } finally { setSavingSku(null) }
  }

  /** Move the part a donor CAN spare; the rest stays a purchase. */
  async function sendPartial(row: WarehouseStatusItem) {
    const pt = row.partial_transfer
    if (!pt) return
    const key = `${row.sku}|${row.warehouse}`
    setSendingSku(key)
    try {
      await createTransfer(pt.from_warehouse, row.warehouse,
                           [{ sku: row.sku, qty: pt.qty }])
      setSentSkus(prev => new Set(prev).add(key))
      onTransferCreated?.()
      load()
    } finally { setSendingSku(null) }
  }

  async function sendTransfer(row: WarehouseStatusItem) {
    const ts = row.transfer_suggestion
    if (!ts) return
    // Keyed by (sku, warehouse) — the same SKU can need transfers into
    // several warehouses, and a bare-sku key would conflate them.
    const key = `${row.sku}|${row.warehouse}`
    setSendingSku(key)
    try {
      await createTransfer(ts.from_warehouse, row.warehouse,
                           [{ sku: row.sku, qty: ts.qty }])
      setSentSkus(prev => new Set(prev).add(key))
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
          <th style={th}>SKU</th>
          <th style={th}>{t('inventory.wh_col_stock')}</th>
          <th style={th}>{t('inventory.wh_col_coverage')}</th>
          <th style={th}>{t('inventory.wh_col_signal')}</th>
          <th style={th}>{t('inventory.wh_col_action')}</th>
        </tr></thead>
        <tbody>
          {rows.map(row => {
            const ts = row.transfer_suggestion
            const key = `${row.sku}|${row.warehouse}`
            const sent = sentSkus.has(key)
            const rejected = transferReasonText(row.transfer_rejected_reason, t)
            return (
              <tr key={key}>
                <td style={td}>{row.display_name || row.sku}</td>
                <td style={td}>
                  <input
                    type="number" min={0}
                    name={`wh-stock-${row.sku}`}
                    aria-label={`${t('inventory.wh_col_stock')} — ${row.display_name || row.sku} — ${warehouse}`}
                    disabled={savingSku === row.sku}
                    value={drafts[row.sku] ?? String(row.current_stock ?? '')}
                    onChange={e => setDrafts(p => ({ ...p, [row.sku]: e.target.value }))}
                    onKeyDown={e => {
                      if (e.key === 'Enter') { e.preventDefault(); void saveStock(row.sku, (e.target as HTMLInputElement).value) }
                      if (e.key === 'Escape') setDrafts(p => { const n = { ...p }; delete n[row.sku]; return n })
                    }}
                    onBlur={e => {
                      if (drafts[row.sku] !== undefined) void saveStock(row.sku, e.target.value)
                    }}
                    style={{ width: 92, background: 'var(--bg)', border: `1px solid ${C.border}`,
                             borderRadius: 6, padding: '4px 7px', fontSize: 12, color: C.text,
                             outline: 'none' }}
                  />
                </td>
                <td style={td}>{row.coverage_days != null
                  ? `${row.coverage_days} ${coverageUnitShort(coverageUnit, t)}` : '—'}</td>
                <td style={td}>
                  {/* Shared badge: icon + translated label + WCAG palette —
                      never a raw colored enum (see SignalBadge header). */}
                  <SignalBadge signal={row.signal} />
                </td>
                <td style={td}>
                  {row.recommended_action === 'transfer' && ts ? (
                    sent ? (
                      <span style={{ fontSize: 12, color: C.green, fontWeight: 600 }}>
                        {t('inventory.wh_transfer_sent')}
                      </span>
                    ) : (
                      <button onClick={() => sendTransfer(row)}
                              disabled={sendingSku === key}
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
                      {/* Why a possible transfer lost against buying — plain
                          text, never an alert (it is a recommendation). */}
                      {rejected && (
                        <span style={{ display: 'block', marginTop: 2 }}>{rejected}</span>
                      )}
                      {/* The donor next door cannot cover the whole gap, so
                          buying stays the recommendation — but moving what it
                          has is the buyer's call to make, and they can only make
                          it if it is on screen. Taking it shrinks the purchase on
                          its own: in-transit units net out of the next one. */}
                      {row.partial_transfer && !sent && (
                        <button onClick={() => sendPartial(row)}
                                disabled={sendingSku === key}
                                style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex',
                                         alignItems: 'center', gap: 6, marginTop: 4,
                                         color: C.indigo, fontSize: 12, fontWeight: 600 }}>
                          <ArrowLeftRight size={13} />
                          {t('inventory.wh_partial_transfer_btn')
                            .replace('{qty}', String(row.partial_transfer.qty))
                            .replace('{from}', row.partial_transfer.from_warehouse)
                            .replace('{rest}', String(row.partial_transfer.remaining_qty))}
                        </button>
                      )}
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

'use client'
// Per-warehouse semáforo table (feature 5.4). Fetches the network-aware
// by-warehouse status and renders one warehouse's rows, including the
// TRANSFER suggestions produced by the backend's network pass.
import { useCallback, useEffect, useState } from 'react'
import { getStatusByWarehouse, createTransfer } from '@/lib/api'
import type { WarehouseStatusItem } from '@/lib/types'
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
                       body={t('inventory.wh_empty_sub')} />
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
          <th style={th}>SKU</th>
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

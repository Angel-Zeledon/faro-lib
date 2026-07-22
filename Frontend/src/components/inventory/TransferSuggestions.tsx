'use client'
// 🔁 transfer suggestion cards for /hoy (feature 5.4): stock exists in the
// network, just in the wrong place — approving creates the transfer
// (in_transit) instead of adding a purchase to the cart.
//
// Prop-driven on purpose: the suggestions ride in on the morning briefing
// (computed server-side alongside it), so this component fires ZERO requests
// of its own — /hoy used to re-run the heaviest inventory endpoint just to
// render these cards.
import { useState } from 'react'
import { createTransfer } from '@/lib/api'
import type { WarehouseStatusItem } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { ArrowLeftRight } from 'lucide-react'

const C = {
  surface: 'var(--surface)', border: 'var(--border)',
  text: 'var(--text)', dim: 'var(--dim)', indigo: '#818cf8', green: '#22c55e',
}

export function TransferSuggestions({ suggestions }: {
  suggestions: WarehouseStatusItem[]
}) {
  const { t } = useLanguage()
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [done, setDone] = useState<Set<string>>(new Set())

  if (suggestions.length === 0) return null

  async function approve(row: WarehouseStatusItem) {
    const ts = row.transfer_suggestion
    if (!ts) return
    const key = `${row.sku}|${row.warehouse}`
    setBusyKey(key)
    try {
      await createTransfer(ts.from_warehouse, row.warehouse, [{ sku: row.sku, qty: ts.qty }])
      setDone(prev => new Set(prev).add(key))
    } finally { setBusyKey(null) }
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 24 }}>
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
                {/* null = donor has no measurable demand ("ample coverage") —
                    same null convention as coverage_days across the API. */}
                {(ts.donor_coverage_days_after == null
                  ? t('hoy.transfers_line_ample')
                  : t('hoy.transfers_line')
                      .replace('{days}', String(ts.donor_coverage_days_after)))
                  .replace('{qty}', String(ts.qty))
                  .replace(/\{from\}/g, ts.from_warehouse)
                  .replace('{to}', row.warehouse)}
              </div>
            </div>
            {isDone ? (
              <span style={{ fontSize: 12, fontWeight: 700, color: C.green }}>
                {t('hoy.transfers_done')}
              </span>
            ) : (
              <button onClick={() => approve(row)} disabled={busyKey === key}
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

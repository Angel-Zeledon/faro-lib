'use client'
/**
 * The Pareto view of the stock-configuration wall (friction plan #1, phase 3).
 *
 * The point of this panel is the GOAL it puts on screen. "2.000 products left"
 * is unreachable and gets abandoned; "82% of your monthly purchase covered" is
 * one sitting's work. So the progress bar is filled by MONEY (the backend's
 * `covered_pct`, a share of projected spend), never by row count, and the list
 * is ordered by what each product is worth — with the running cumulative share
 * next to it, so the user can see where they are allowed to stop.
 *
 * Each row is editable in place: stock, cost and lead time saved without
 * leaving the screen, because sending someone to a different page for 40 rows
 * is the same abandonment by another route.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, RefreshCw } from 'lucide-react'

import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import { useSetupCopy } from '@/i18n/useSetupCopy'
import { getSetupGaps, patchInventoryStock, upsertInventoryStock } from '@/lib/api'
import type { SetupGapItem, SetupGapsResponse } from '@/lib/stockSetupTypes'

const GREEN = '#22c55e'
const AMBER = '#f59e0b'

type RowState = { stock: string; cost: string; lead: string; saving: boolean; saved: boolean; failed: boolean }

const emptyRow = (): RowState =>
  ({ stock: '', cost: '', lead: '', saving: false, saved: false, failed: false })

export default function SetupGapsPanel({
  sessionId, horizonDays = 30, onChanged,
}: {
  sessionId?: string
  horizonDays?: number
  /** Fired after a successful inline save so the host page can refresh totals. */
  onChanged?: () => void
}) {
  const c = useSetupCopy()
  const [data, setData]       = useState<SetupGapsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed]   = useState(false)
  const [rows, setRows]       = useState<Record<string, RowState>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getSetupGaps({ sessionId, horizonDays, limit: 50 }, { silent: true })
      setData(res)
      setFailed(false)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [sessionId, horizonDays])

  useEffect(() => { void load() }, [load])

  const money = (n: number) =>
    n >= 1000 ? Math.round(n).toLocaleString('es') : n.toFixed(2)

  async function save(item: SetupGapItem) {
    const state = rows[item.sku] ?? emptyRow()
    const stock = state.stock.trim() === '' ? null : Number(state.stock.replace(',', '.'))
    const cost  = state.cost.trim()  === '' ? null : Number(state.cost.replace(',', '.'))
    const lead  = state.lead.trim()  === '' ? null : Number(state.lead.replace(',', '.'))
    if (stock === null && cost === null && lead === null) return

    setRows(r => ({ ...r, [item.sku]: { ...state, saving: true, failed: false } }))
    try {
      const body: Record<string, number> = {}
      if (stock !== null && Number.isFinite(stock)) body.current_stock = stock
      if (cost !== null && Number.isFinite(cost))   body.unit_cost = cost
      if (lead !== null && Number.isFinite(lead))   body.lead_time_days = Math.round(lead)

      if (item.has_row) {
        await patchInventoryStock(item.sku, body)
      } else {
        // PUT requires a stock figure; a row created here starts at 0 when the
        // user only filled in the cost, which is still more than we had.
        await upsertInventoryStock(item.sku, { current_stock: 0, ...body })
      }
      setRows(r => ({ ...r, [item.sku]: { ...state, saving: false, saved: true, failed: false } }))
      onChanged?.()
      void load()
    } catch {
      setRows(r => ({ ...r, [item.sku]: { ...state, saving: false, saved: false, failed: true } }))
    }
  }

  if (loading && !data) {
    return <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={18} /></div>
  }
  if (failed || !data) {
    return (
      <div style={{ padding: 18, color: 'var(--dim)', fontSize: 13 }}>
        {c('setupStock.gaps.empty')}
      </div>
    )
  }

  const isMoney = data.basis === 'money'
  const headline = data.gap_skus === 0
    ? c('setupStock.gaps.all_done')
    : c(isMoney ? 'setupStock.gaps.headline' : 'setupStock.gaps.headline_units', {
        count: data.recommended_count,
        total: data.total_skus,
        pct:   Math.round(data.recommended_pct),
      })

  return (
    <section style={{
      border: '1px solid var(--border)', borderRadius: 12,
      background: 'var(--surface)', padding: '18px 20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: 0, flex: 1 }}>
          {c('setupStock.gaps.title')}
        </h2>
        <span style={{ fontSize: 11.5, color: 'var(--dim)' }}>
          {c('setupStock.gaps.horizon', { days: data.horizon_days })}
        </span>
        <Button size="sm" variant="ghost" onClick={() => void load()}
                icon={<RefreshCw size={12} />} loading={loading}>
          {c('setupStock.gaps.refresh')}
        </Button>
      </div>

      <p style={{ fontSize: 14.5, color: 'var(--text)', margin: '10px 0 4px', lineHeight: 1.5 }}>
        {headline}
      </p>

      {/* Money-weighted progress. `covered_pct` is a share of projected spend,
          which is why it can read 82% while most rows are still empty. */}
      <div style={{ marginTop: 10 }}>
        <div style={{
          height: 10, borderRadius: 6, background: 'var(--surface-2)', overflow: 'hidden',
          border: '1px solid var(--border)',
        }}>
          <div style={{
            width: `${Math.min(100, Math.max(0, data.covered_pct))}%`, height: '100%',
            background: GREEN, transition: 'width 0.4s',
          }} />
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
            {c('setupStock.gaps.progress_label', { pct: Math.round(data.covered_pct) })}
          </span>
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>
            {c('setupStock.gaps.progress_hint', {
              done: data.configured_skus, total: data.total_skus,
            })}
          </span>
        </div>
      </div>

      {!isMoney && (
        <div style={{
          marginTop: 10, display: 'flex', gap: 8, alignItems: 'flex-start',
          fontSize: 12, color: AMBER,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>{c('setupStock.gaps.basis_units')}</span>
        </div>
      )}

      {data.items.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 720 }}>
            <thead>
              <tr style={{ color: 'var(--dim)', textAlign: 'left' }}>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>#</th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>{c('setupStock.gaps.col_product')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>{c('setupStock.gaps.col_demand')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>{c('setupStock.gaps.col_spend')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>{c('setupStock.gaps.col_share')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>{c('setupStock.gaps.col_cumulative')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>{c('setupStock.gaps.col_missing')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>{c('setupStock.gaps.col_fill')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(item => {
                const state = rows[item.sku] ?? emptyRow()
                const withinTarget = item.rank <= data.recommended_count
                return (
                  <tr key={item.sku} style={{
                    borderTop: '1px solid var(--border)',
                    background: withinTarget ? 'rgba(34,197,94,0.05)' : 'transparent',
                  }}>
                    <td style={{ padding: '7px 8px', color: 'var(--dim)' }}>{item.rank}</td>
                    <td style={{ padding: '7px 8px' }}>
                      <div style={{ fontWeight: 600, color: 'var(--text)' }}>
                        {item.display_name || item.sku}
                      </div>
                      {item.display_name && (
                        <div style={{ color: 'var(--dim)', fontSize: 11 }}>{item.sku}</div>
                      )}
                    </td>
                    <td style={{ padding: '7px 8px', textAlign: 'right', color: 'var(--text)' }}>
                      {Math.round(item.projected_demand).toLocaleString('es')}{' '}
                      <span style={{ color: 'var(--dim)', fontSize: 11 }}>
                        {c('setupStock.gaps.units_suffix')}
                      </span>
                    </td>
                    <td style={{ padding: '7px 8px', textAlign: 'right', color: 'var(--text)' }}>
                      {isMoney ? money(item.projected_spend) : '—'}
                      <div style={{ color: 'var(--dim)', fontSize: 10.5 }}>
                        {c(`setupStock.gaps.price_source.${item.price_source}`)}
                      </div>
                    </td>
                    <td style={{ padding: '7px 8px', textAlign: 'right', color: 'var(--text)' }}>
                      {item.share_pct.toFixed(1)}%
                    </td>
                    <td style={{
                      padding: '7px 8px', textAlign: 'right', fontWeight: 600,
                      color: withinTarget ? GREEN : 'var(--dim)',
                    }}>
                      {item.cumulative_pct.toFixed(1)}%
                    </td>
                    <td style={{ padding: '7px 8px', color: 'var(--dim)' }}>
                      {item.missing.map(f => c(`setupStock.gaps.missing.${f}`)).join(', ')}
                    </td>
                    <td style={{ padding: '7px 8px' }}>
                      <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                        <input
                          value={state.stock}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, stock: e.target.value, saved: false } }))}
                          placeholder={c('setupStock.gaps.field_stock')}
                          inputMode="decimal"
                          style={inputStyle}
                        />
                        <input
                          value={state.cost}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, cost: e.target.value, saved: false } }))}
                          placeholder={c('setupStock.gaps.field_cost')}
                          inputMode="decimal"
                          style={inputStyle}
                        />
                        <input
                          value={state.lead}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, lead: e.target.value, saved: false } }))}
                          placeholder={c('setupStock.gaps.field_lead_time')}
                          inputMode="numeric"
                          style={{ ...inputStyle, width: 62 }}
                        />
                        <Button size="sm" variant="secondary" loading={state.saving}
                                onClick={() => void save(item)}>
                          {state.saved
                            ? <><Check size={12} color={GREEN} /> {c('setupStock.gaps.saved')}</>
                            : c('setupStock.gaps.save')}
                        </Button>
                      </div>
                      {state.failed && (
                        <div style={{ color: '#ef4444', fontSize: 11, marginTop: 3 }}>
                          {c('setupStock.gaps.save_error')}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {data.truncated && (
        <p style={{ fontSize: 12, color: 'var(--dim)', marginTop: 10 }}>
          {c('setupStock.gaps.remaining', {
            count: data.gap_skus - data.items.length,
            pct: Math.max(0, 100 - Math.round(
              data.items[data.items.length - 1]?.cumulative_pct ?? data.covered_pct)),
          })}
        </p>
      )}
    </section>
  )
}

const inputStyle: React.CSSProperties = {
  width: 72, padding: '4px 7px', fontSize: 12,
  border: '1px solid var(--border)', borderRadius: 6,
  background: 'var(--surface-2)', color: 'var(--text)',
}

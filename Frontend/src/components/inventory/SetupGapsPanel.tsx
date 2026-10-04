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
import { AlertTriangle, Check, Info, RefreshCw } from 'lucide-react'

import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import Tooltip from '@/components/ui/Tooltip'
import { useSetupCopy } from '@/i18n/useSetupCopy'
import { getSetupGaps, patchInventoryStock, upsertInventoryStock } from '@/lib/api'
import type { SetupGapItem, SetupGapsResponse } from '@/lib/stockSetupTypes'
import type { InventoryStock } from '@/lib/types'
import { fmtNum } from '@/lib/numberLocale'
import { useIsNarrow } from '@/hooks/useIsNarrow'

const GREEN = '#2E8B62'
const AMBER = '#B7791F'

type RowState = {
  stock: string; cost: string; lead: string
  saving: boolean; saved: boolean; failed: boolean
  /** The row does not exist yet and the user saved without a stock count. */
  needsStock: boolean
}

const emptyRow = (): RowState =>
  ({ stock: '', cost: '', lead: '', saving: false, saved: false, failed: false, needsStock: false })

export default function SetupGapsPanel({
  sessionId, horizonDays = 30, onChanged,
}: {
  sessionId?: string
  horizonDays?: number
  /** Fired after a successful inline save so the host page can refresh totals. */
  onChanged?: () => void
}) {
  const c = useSetupCopy()
  // Phone: the 8-column table (720px minimum) becomes one card per product
  // with the three boxes stacked under it — same rows, same save.
  const narrow = useIsNarrow()
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
    n >= 1000 ? fmtNum(Math.round(n)) : n.toFixed(2)

  async function save(item: SetupGapItem) {
    const state = rows[item.sku] ?? emptyRow()
    const stock = state.stock.trim() === '' ? null : Number(state.stock.replace(',', '.'))
    const cost  = state.cost.trim()  === '' ? null : Number(state.cost.replace(',', '.'))
    const lead  = state.lead.trim()  === '' ? null : Number(state.lead.replace(',', '.'))
    if (stock === null && cost === null && lead === null) return

    const body: Partial<InventoryStock> = {}
    if (stock !== null && Number.isFinite(stock)) body.current_stock = stock
    if (cost !== null && Number.isFinite(cost))   body.unit_cost = cost
    if (lead !== null && Number.isFinite(lead))   body.lead_time_days = Math.round(lead)

    // A product with no stock row yet can only be CREATED with a count, and we
    // ask for it rather than inventing one.
    //
    // `PUT /inventory/stock/{sku}` requires `current_stock` (it is the one
    // non-optional field of StockUpsert), so this panel used to send a 0 when
    // the user had filled in only the cost. Nothing downstream can tell that
    // invented 0 from a counted one — there is no column recording who supplied
    // it — and 0 units means 0 days of coverage, which is exactly what makes the
    // semáforo shout PEDIR_YA. Someone typing costs for thirty products he has
    // full pallets of got thirty emergency purchase orders for goods already on
    // his shelf. Asking for one more number is cheaper than that.
    if (!item.has_row && body.current_stock == null) {
      setRows(r => ({
        ...r,
        [item.sku]: { ...state, saving: false, saved: false, failed: false, needsStock: true },
      }))
      return
    }

    setRows(r => ({ ...r, [item.sku]: { ...state, saving: true, failed: false, needsStock: false } }))
    try {
      if (item.has_row) {
        await patchInventoryStock(item.sku, body)
      } else {
        // Guarded above: `body.current_stock` is a number the user counted.
        await upsertInventoryStock(item.sku, body)
      }
      setRows(r => ({
        ...r,
        [item.sku]: { ...state, saving: false, saved: true, failed: false, needsStock: false },
      }))
      onChanged?.()
      void load()
    } catch {
      setRows(r => ({
        ...r,
        [item.sku]: { ...state, saving: false, saved: false, failed: true, needsStock: false },
      }))
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
      background: 'var(--surface)', padding: narrow ? '16px 14px' : '18px 20px',
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

      {/* What the three boxes at the end of each row want. Said once, above the
          table, rather than as a tooltip repeated on every row: three columns
          times thirty rows of question marks is noise, and the answer is the
          same every time. The cost one is spelled out because entering the sale
          price there is the mistake people actually make. */}
      {data.items.length > 0 && (
        <div style={{
          marginTop: 14, padding: '10px 12px', borderRadius: 8,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.65,
        }}>
          <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 3 }}>
            {c('setupStock.gaps.legend_title')}
          </div>
          <div><b>{c('setupStock.gaps.field_stock')}</b> — {c('setupStock.gaps.legend_stock')}</div>
          <div><b>{c('setupStock.gaps.field_cost')}</b> — {c('setupStock.gaps.legend_cost')}</div>
          <div><b>{c('setupStock.gaps.field_lead_time')}</b> — {c('setupStock.gaps.legend_lead_time')}</div>
        </div>
      )}

      {data.items.length > 0 && narrow && (
        <ol aria-label={c('setupStock.gaps.title')} style={{ listStyle: 'none', margin: '14px -14px 0', padding: 0 }}>
          {data.items.map(item => {
            const state = rows[item.sku] ?? emptyRow()
            const withinTarget = item.rank <= data.recommended_count
            const set = (patch: Partial<RowState>) => setRows(r => ({ ...r, [item.sku]: { ...state, ...patch } }))
            const label = (k: string) => (
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--muted)' }}>{c(k)}</span>
            )
            return (
              <li key={item.sku} style={{
                borderTop: '1px solid var(--border)', padding: '14px',
                background: withinTarget ? 'rgba(46,139,98,0.05)' : 'transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                  <span style={{ fontSize: 13, color: 'var(--dim)', flexShrink: 0 }}>{item.rank}.</span>
                  <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 600, color: 'var(--text)', overflow: 'hidden', overflowWrap: 'anywhere', }}>
                    {item.display_name || item.sku}
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: withinTarget ? GREEN : 'var(--dim)', flexShrink: 0 }}
                        title={c('setupStock.gaps.col_cumulative_tip')}>
                    {item.cumulative_pct.toFixed(1)}%
                  </span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 3, lineHeight: 1.45 }}>
                  {item.display_name ? `${item.sku} · ` : ''}
                  {fmtNum(Math.round(item.projected_demand))} {c('setupStock.gaps.units_suffix')}
                  {isMoney ? ` · ${money(item.projected_spend)}` : ''}
                  {` · ${c('setupStock.gaps.col_share')} ${item.share_pct.toFixed(1)}%`}
                </div>
                <div style={{ fontSize: 12.5, color: AMBER, marginTop: 2 }}>
                  {c('setupStock.gaps.col_missing')}: {item.missing.map(f => c(`setupStock.gaps.missing.${f}`)).join(', ')}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 10 }}>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                    {label('setupStock.gaps.field_stock')}
                    <input value={state.stock} inputMode="decimal" enterKeyHint="next"
                           onChange={e => set({ stock: e.target.value, saved: false, needsStock: false })}
                           aria-label={`${item.sku} — ${c('setupStock.gaps.legend_stock')}`}
                           aria-required={!item.has_row} style={inputStyleNarrow} />
                  </label>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                    {label('setupStock.gaps.field_cost')}
                    <input value={state.cost} inputMode="decimal" enterKeyHint="next"
                           onChange={e => set({ cost: e.target.value, saved: false })}
                           aria-label={`${item.sku} — ${c('setupStock.gaps.legend_cost')}`} style={inputStyleNarrow} />
                  </label>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                    {label('setupStock.gaps.field_lead_time')}
                    <input value={state.lead} inputMode="numeric" enterKeyHint="done"
                           onChange={e => set({ lead: e.target.value, saved: false })}
                           onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void save(item) } }}
                           aria-label={`${item.sku} — ${c('setupStock.gaps.legend_lead_time')}`} style={inputStyleNarrow} />
                  </label>
                </div>
                <button type="button" className="mobile-btn mobile-btn-secondary" disabled={state.saving}
                        onClick={() => void save(item)} style={{ width: '100%', marginTop: 10 }}>
                  {state.saving ? <Spinner size={14} /> : state.saved
                    ? <><Check size={16} color={GREEN} /> {c('setupStock.gaps.saved')}</>
                    : c('setupStock.gaps.save')}
                </button>
                {state.needsStock && (
                  <div role="alert" style={{ color: AMBER, fontSize: 12.5, marginTop: 6, lineHeight: 1.45 }}>
                    {c('setupStock.gaps.stock_required')}
                  </div>
                )}
                {state.failed && (
                  <div role="alert" style={{ color: '#C0504D', fontSize: 12.5, marginTop: 6 }}>
                    {c('setupStock.gaps.save_error')}
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}

      {data.items.length > 0 && !narrow && (
        <div style={{ overflowX: 'auto', marginTop: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 720 }}>
            <thead>
              <tr style={{ color: 'var(--dim)', textAlign: 'left' }}>
                {/* Every column except # and Producto is a derived number, and
                    a heading like "Vale" or "Acumulado" does not say what it
                    was derived from. The tooltip does. */}
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>#</th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>{c('setupStock.gaps.col_product')}</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>
                  <Tooltip text={c('setupStock.gaps.col_demand_tip')} width={250}>
                    <span>{c('setupStock.gaps.col_demand')}</span>
                    <Info size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
                  </Tooltip>
                </th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>
                  <Tooltip text={c('setupStock.gaps.col_spend_tip')} width={250}>
                    <span>{c('setupStock.gaps.col_spend')}</span>
                    <Info size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
                  </Tooltip>
                </th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>
                  <Tooltip text={c('setupStock.gaps.col_share_tip')} width={250}>
                    <span>{c('setupStock.gaps.col_share')}</span>
                    <Info size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
                  </Tooltip>
                </th>
                <th style={{ padding: '6px 8px', fontWeight: 600, textAlign: 'right' }}>
                  <Tooltip text={c('setupStock.gaps.col_cumulative_tip')} width={260}>
                    <span>{c('setupStock.gaps.col_cumulative')}</span>
                    <Info size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
                  </Tooltip>
                </th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>
                  <Tooltip text={c('setupStock.gaps.col_missing_tip')} width={250}>
                    <span>{c('setupStock.gaps.col_missing')}</span>
                    <Info size={10} style={{ opacity: 0.5, flexShrink: 0 }} />
                  </Tooltip>
                </th>
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
                    background: withinTarget ? 'rgba(46,139,98,0.05)' : 'transparent',
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
                      {fmtNum(Math.round(item.projected_demand))}{' '}
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
                        {/* A placeholder alone does not say what the number
                            means, and it disappears the moment you type. The
                            legend above the table carries the explanation once;
                            these carry it to a screen reader per row. */}
                        <input
                          value={state.stock}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, stock: e.target.value, saved: false, needsStock: false } }))}
                          placeholder={c('setupStock.gaps.field_stock')}
                          aria-label={`${item.sku} — ${c('setupStock.gaps.legend_stock')}`}
                          // A product with no row yet cannot be created without
                          // this box — see `save()`.
                          aria-required={!item.has_row}
                          inputMode="decimal"
                          style={inputStyle}
                        />
                        <input
                          value={state.cost}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, cost: e.target.value, saved: false } }))}
                          placeholder={c('setupStock.gaps.field_cost')}
                          aria-label={`${item.sku} — ${c('setupStock.gaps.legend_cost')}`}
                          inputMode="decimal"
                          style={inputStyle}
                        />
                        <input
                          value={state.lead}
                          onChange={e => setRows(r => ({ ...r, [item.sku]: { ...state, lead: e.target.value, saved: false } }))}
                          placeholder={c('setupStock.gaps.field_lead_time')}
                          aria-label={`${item.sku} — ${c('setupStock.gaps.legend_lead_time')}`}
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
                      {state.needsStock && (
                        <div role="alert" style={{ color: AMBER, fontSize: 11, marginTop: 3, maxWidth: 320, lineHeight: 1.45 }}>
                          {c('setupStock.gaps.stock_required')}
                        </div>
                      )}
                      {state.failed && (
                        <div style={{ color: '#C0504D', fontSize: 11, marginTop: 3 }}>
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

const inputStyleNarrow: React.CSSProperties = {
  boxSizing: 'border-box', width: '100%', minWidth: 0, minHeight: 44,
  padding: '8px 10px', fontSize: 16, borderRadius: 10,
  border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--text)',
}

const inputStyle: React.CSSProperties = {
  width: 72, padding: '4px 7px', fontSize: 12,
  border: '1px solid var(--border)', borderRadius: 6,
  background: 'var(--surface-2)', color: 'var(--text)',
}

'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { getSupplierScorecard, getSupplierLeadTimeAlerts, getPOHistory } from '@/lib/api'
import type {
  SupplierScorecardRow, SupplierLeadTimeAlert, POLogEntry,
} from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import Card from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'
import { renderSupplierAlert } from '@/lib/supplierAlertCopy'
import { BarChart3, ArrowLeft, AlertTriangle, Truck, TrendingUp } from 'lucide-react'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { StatusBadge, useMobileHeader } from '@/components/mobile'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#C0504D', amber: '#B7791F', green: '#2E8B62', indigo: 'var(--accent)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null, lang: string): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(lang, { day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtPct(n: number | null): string {
  return n == null ? '—' : `${Math.round(n * 100)}%`
}

function fmtRange(min: number | null, max: number | null): string {
  if (min == null || max == null) return '—'
  return min === max ? `${min}d` : `${min}–${max}d`
}

// ── Table ─────────────────────────────────────────────────────────────────────
function ScorecardTable({ rows, alerts }: {
  rows: SupplierScorecardRow[]
  alerts: Map<string, SupplierLeadTimeAlert>
}) {
  const { t, lang } = useLanguage()
  const columns = [
    t('scorecard.col_supplier'), t('scorecard.col_receptions'), t('scorecard.col_real_lead_time'),
    t('scorecard.col_declared'), t('scorecard.col_trend'), t('scorecard.col_on_time'),
    t('scorecard.col_fill_rate'), t('scorecard.col_purchased_value'), t('scorecard.col_last_reception'),
  ]

  return (
    <Table size="lg">
        <thead>
          <tr style={{ background: C.card }}>
            {columns.map(h => <Th key={h} size="lg">{h}</Th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const onTimeColor = row.on_time_rate == null
              ? C.dim
              : row.on_time_rate >= 0.7 ? C.green : row.on_time_rate >= 0.4 ? C.amber : C.red
            // Feature 3.3 — flagged only when the backend's robust 3-sigma
            // rule fired; absence means "within its normal range", not "no data".
            const alert = alerts.get(row.supplier.toLowerCase())
            return (
              <tr key={row.supplier} style={{
                background: idx % 2 === 0 ? C.surface : C.card,
                borderBottom: `1px solid ${C.border}`,
              }}>
                {/* The divider is on the <tr>, so the cells do not draw their own. */}
                <Td size="lg" divider={false} style={{ fontWeight: 600 }}>{row.supplier}</Td>
                <Td size="lg" divider={false}>{row.n_receptions}</Td>
                {/* Every delivery landed the same day it was ordered, so the
                    observed average is 0 and measures nothing. /proveedores
                    already refuses to learn from this and says why; printing
                    "0d" here beside "10d declarado" invited the buyer to lower
                    their lead time to zero and order too late. Same sentence as
                    that screen — it exists, and it is the one that prevents the
                    wrong move. */}
                <Td size="lg" divider={false} mono>
                  {row.lead_time_unusable ? (
                    <span
                      style={{ color: C.dim }}
                      title={t('scorecard.lead_time_says_nothing_hint')}
                    >
                      {t('scorecard.lead_time_says_nothing')}
                    </span>
                  ) : fmtRange(row.lead_time_real_min, row.lead_time_real_max)}
                </Td>
                <Td size="lg" divider={false} mono style={{ color: C.muted }}>
                  {row.lead_time_declarado != null ? `${row.lead_time_declarado}d` : '—'}
                </Td>
                <Td size="lg" divider={false}>
                  {alert ? (
                    <span
                      title={`${renderSupplierAlert(t, alert)} (z=${alert.z_score}, ${alert.n_recent} ${t('scorecard.trend_tooltip_recent')} ${alert.n_baseline} ${t('scorecard.trend_tooltip_historical')})`}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap',
                        fontSize: 11, fontWeight: 500,
                        color: C.muted, background: C.card, border: `1px solid ${C.border}`,
                      }}
                    >
                      <TrendingUp size={11} aria-hidden="true" /> +{alert.deviation_days}d
                    </span>
                  ) : !row.trend_measurable ? (
                    // "Estable" over a single reception is a claim about a shape
                    // nobody has seen. The comment above says absence of an
                    // alert means "within its normal range" — which is only true
                    // once there IS a range.
                    <span style={{ color: C.dim }} title={t('scorecard.trend_needs_two_hint')}>
                      {t('scorecard.trend_not_measurable')}
                    </span>
                  ) : (
                    <span style={{ color: C.dim }}>{t('scorecard.stable')}</span>
                  )}
                </Td>
                <Td size="lg" divider={false} style={
                  row.on_time_measurable === false
                    ? { color: C.dim }
                    : { color: onTimeColor, fontWeight: 700 }
                }>
                  {/* The backend says whether this percentage means anything —
                      `on_time_measurable` is false below its sample floor — and
                      this cell printed the raw number regardless, so a supplier
                      with ONE delivery that happened to land on time read
                      "100%" in bold green, right beside a "Trend: not
                      measurable" cell that did honour its own flag. The number
                      still shows; it just stops claiming to be a habit. */}
                  {row.on_time_measurable === false ? (
                    <span title={t('scorecard.rate_needs_more_hint')}>
                      {fmtPct(row.on_time_rate)} {t('scorecard.rate_provisional')}
                    </span>
                  ) : fmtPct(row.on_time_rate)}
                </Td>
                <Td size="lg" divider={false} style={row.fill_rate_measurable === false ? { color: C.dim } : undefined}>
                  {/* An empty fill rate with orders still in their delivery
                      window says so. The metric used to include every
                      half-delivered order whatever its deadline, so a supplier
                      with two on-schedule deliveries printed 50% as a
                      performance verdict (stability 11.12); now those orders
                      wait, and "waiting" and "never bought from them" must not
                      look the same. */}
                  {row.fill_rate === null && (row.orders_in_transit ?? 0) > 0 ? (
                    <span style={{ color: C.dim }}
                          title={t('scorecard.fill_rate_in_transit_hint')}>
                      {t('scorecard.fill_rate_in_transit', { n: row.orders_in_transit })}
                    </span>
                  ) : row.fill_rate_measurable === false ? (
                    <span title={t('scorecard.rate_needs_more_hint')}>
                      {fmtPct(row.fill_rate)} {t('scorecard.rate_provisional')}
                    </span>
                  ) : fmtPct(row.fill_rate)}
                </Td>
                <Td size="lg" divider={false} mono style={{ color: C.green, fontWeight: 600 }}>
                  {row.purchased_value === null ? (
                    // Not a zero. No line of their orders carried a unit cost,
                    // so we do not know what we bought from them — the same
                    // dash every other unmeasurable cell on this row uses.
                    <span style={{ color: C.dim }} title={t('scorecard.purchased_value_unknown_hint')}>—</span>
                  ) : !row.purchased_value_complete ? (
                    // Some lines had costs and some did not, so this is a floor,
                    // not the total. Shown, but marked, instead of printing a
                    // partial sum with the confidence of a complete one.
                    <span title={t('scorecard.purchased_value_partial_hint')}>
                      ≥ {formatMoney(row.purchased_value)}
                    </span>
                  ) : (
                    formatMoney(row.purchased_value)
                  )}
                </Td>
                <Td size="lg" divider={false} style={{ color: C.dim }}>{fmtDate(row.last_reception, lang)}</Td>
              </tr>
            )
          })}
        </tbody>
    </Table>
  )
}

// ── Phone: one card per supplier ──────────────────────────────────────────────
// The nine columns as a two-column grid of facts under the supplier's name.
// The hints the table carries in `title` (invisible to a finger) are printed
// as a line wherever a figure is provisional or unknown.
function ScorecardCards({ rows, alerts }: {
  rows: SupplierScorecardRow[]
  alerts: Map<string, SupplierLeadTimeAlert>
}) {
  const { t, lang } = useLanguage()
  const label: React.CSSProperties = { fontSize: 12, color: C.dim }
  const value: React.CSSProperties = { fontSize: 15, fontWeight: 600, color: C.text, marginTop: 2, overflowWrap: 'anywhere' }
  return (
    <ul aria-label={t('scorecard.title')} style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {rows.map(row => {
        const alert = alerts.get(row.supplier.toLowerCase())
        const onTimeColor = row.on_time_rate == null
          ? C.dim
          : row.on_time_rate >= 0.7 ? C.green : row.on_time_rate >= 0.4 ? C.amber : C.red
        const notes: string[] = []
        if (row.lead_time_unusable) notes.push(t('scorecard.lead_time_says_nothing_hint'))
        if (row.on_time_measurable === false || row.fill_rate_measurable === false) notes.push(t('scorecard.rate_needs_more_hint'))
        if (row.purchased_value === null) notes.push(t('scorecard.purchased_value_unknown_hint'))
        else if (!row.purchased_value_complete) notes.push(t('scorecard.purchased_value_partial_hint'))
        return (
          <li key={row.supplier} style={{ padding: '14px', borderRadius: 14, background: C.surface, border: `1px solid ${C.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, marginBottom: 12 }}>
              <span style={{ flex: 1, minWidth: 0, fontSize: 16, fontWeight: 700, color: C.text, overflow: 'hidden', overflowWrap: 'anywhere', }}>{row.supplier}</span>
              {alert ? (
                <StatusBadge tone="neutral" label={<><TrendingUp size={11} aria-hidden="true" /> +{alert.deviation_days}d</>} />
              ) : (
                <StatusBadge tone="neutral" label={row.trend_measurable ? t('scorecard.stable') : t('scorecard.trend_not_measurable')} />
              )}
            </div>
            {alert && (
              <div style={{ fontSize: 13, color: C.muted, marginBottom: 10, lineHeight: 1.45 }}>
                {renderSupplierAlert(t, alert)}
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '12px 12px' }}>
              <div><div style={label}>{t('scorecard.col_on_time')}</div>
                <div style={{ ...value, color: row.on_time_measurable === false ? C.dim : onTimeColor }}>
                  {fmtPct(row.on_time_rate)}{row.on_time_measurable === false ? ` ${t('scorecard.rate_provisional')}` : ''}
                </div></div>
              <div><div style={label}>{t('scorecard.col_fill_rate')}</div>
                <div style={{ ...value, color: row.fill_rate_measurable === false ? C.dim : C.text }}>
                  {row.fill_rate === null && (row.orders_in_transit ?? 0) > 0
                    ? t('scorecard.fill_rate_in_transit', { n: row.orders_in_transit })
                    : `${fmtPct(row.fill_rate)}${row.fill_rate_measurable === false ? ` ${t('scorecard.rate_provisional')}` : ''}`}
                </div></div>
              <div><div style={label}>{t('scorecard.col_real_lead_time')}</div>
                <div style={{ ...value, color: row.lead_time_unusable ? C.dim : C.text }}>
                  {row.lead_time_unusable ? t('scorecard.lead_time_says_nothing') : fmtRange(row.lead_time_real_min, row.lead_time_real_max)}
                </div></div>
              <div><div style={label}>{t('scorecard.col_declared')}</div>
                <div style={{ ...value, color: C.muted }}>{row.lead_time_declarado != null ? `${row.lead_time_declarado}d` : '—'}</div></div>
              <div><div style={label}>{t('scorecard.col_receptions')}</div>
                <div style={value}>{row.n_receptions}</div></div>
              <div><div style={label}>{t('scorecard.col_purchased_value')}</div>
                <div style={{ ...value, color: row.purchased_value === null ? C.dim : C.green }}>
                  {row.purchased_value === null ? '—' : `${row.purchased_value_complete ? '' : '≥ '}${formatMoney(row.purchased_value)}`}
                </div></div>
              <div style={{ gridColumn: '1 / -1' }}><div style={label}>{t('scorecard.col_last_reception')}</div>
                <div style={{ ...value, fontWeight: 500, color: C.muted }}>{fmtDate(row.last_reception, lang)}</div></div>
            </div>
            {notes.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 12, color: C.dim, lineHeight: 1.45, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {notes.map(n => <span key={n}>{n}</span>)}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function SupplierScorecardPage() {
  const { t } = useLanguage()
  // Phone: cards instead of the nine-column table; the compact header carries
  // the title and the back button.
  const narrow = useIsNarrow()
  useMobileHeader({ title: t('scorecard.title') })
  const [rows,    setRows]    = useState<SupplierScorecardRow[]>([])
  const [alerts,  setAlerts]  = useState<SupplierLeadTimeAlert[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  // Whether any purchase order has been received at all — see the load below.
  const [hasReceptions, setHasReceptions] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [scorecard, deviations, poHistory] = await Promise.all([
        getSupplierScorecard(),
        // A failed deviation fetch must not blank the scorecard — the table
        // is still useful without the trend column.
        getSupplierLeadTimeAlerts().catch(() => [] as SupplierLeadTimeAlert[]),
        // Only to tell two very different empty states apart. A scorecard is
        // per SUPPLIER, so an order received with no supplier on its lines
        // scores nobody and the table is legitimately empty — but the copy
        // said "aún no hay recepciones registradas" to a user who had just
        // registered one. Someone who believes their reception did not save
        // registers it again, and the stock is counted twice.
        getPOHistory(50).catch(() => [] as POLogEntry[]),
      ])
      setRows(scorecard)
      setAlerts(deviations)
      setHasReceptions(poHistory.some(p => p.received_at))
    }
    catch (e: unknown) { setError(e instanceof Error ? e.message : t('scorecard.error_loading')) }
    finally { setLoading(false) }
  }, [t])

  useEffect(() => { load() }, [load])

  const alertsBySupplier = new Map(alerts.map(a => [a.supplier.toLowerCase(), a]))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        {/* The top bar / phone header carries the title: only what it measures. */}
        {!narrow && <p style={{ margin: 0, fontSize: 12, color: C.dim, flex: '1 1 240px', minWidth: 0 }}>{t('scorecard.subtitle')}</p>}
        <Link href="/proveedores" style={{
          display: narrow ? 'none' : 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: C.dim, textDecoration: 'none',
          padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
        }}>
          <ArrowLeft size={12} aria-hidden="true" /> {t('scorecard.back_to_suppliers')}
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div role="alert" style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(192,80,77,0.07)', border: '1px solid rgba(192,80,77,0.2)',
          fontSize: 13, color: C.red,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0 }} aria-hidden="true" /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 64, display: 'flex', justifyContent: 'center' }}>
          <Spinner />
        </div>
      ) : rows.length > 0 ? (
        <>
          {/* Feature 3.3 — the deviation is the headline, the table is the detail. */}
          {alerts.length > 0 && (
            <p style={{ margin: 0, fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
              {alerts.length}{' '}
              {alerts.length === 1 ? t('scorecard.deviation_singular') : t('scorecard.deviation_plural')}
              {' · '}{t('scorecard.deviation_method')}
            </p>
          )}
          {narrow ? (
            <ScorecardCards rows={rows} alerts={alertsBySupplier} />
          ) : (
          <Card padding={0} overflow="hidden">
            <ScorecardTable rows={rows} alerts={alertsBySupplier} />
          </Card>
          )}
        </>
      ) : (
        <Card tone="inset" radius={12} padding="40px 24px" style={{ textAlign: 'center' }}>
          <Truck size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} aria-hidden="true" />
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
            {t(hasReceptions ? 'scorecard.empty_no_supplier_title' : 'scorecard.empty_title')}
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>
            {t(hasReceptions ? 'scorecard.empty_no_supplier_body' : 'scorecard.empty_body')}
          </div>
          <Link href="/proveedores" style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
            color: C.indigo, textDecoration: 'none',
            ...(narrow ? { minHeight: 44, boxSizing: 'border-box', fontSize: 14 } : {}),
          }}>
            <Truck size={13} aria-hidden="true" /> {t('scorecard.empty_cta')}
          </Link>
        </Card>
      )}
    </div>
  )
}

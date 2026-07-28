'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { getInventoryROI, getROIMonthly, getROIMonthReport } from '@/lib/api'
import type { InventoryROISummary, ROIMonthlyRow, ROIMonthReport } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { TrendingUp, ArrowLeft, Package, ShoppingCart, Calendar, AlertTriangle } from 'lucide-react'
import Card, { CardHeader } from '@/components/ui/Card'
import Table, { Th, Td } from '@/components/ui/Table'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  red: '#ef4444', amber: '#f59e0b', green: '#22c55e', indigo: 'var(--accent)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
// Map the active UI language to a concrete BCP-47 locale so dates render in the
// user's language instead of a hardcoded one. Anchored to Costa Rica for es.
function localeFor(lang: string): string {
  return lang === 'en' ? 'en-US' : 'es-CR'
}

function fmtDate(iso: string | null, lang: string): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'long', year: 'numeric' })
}

function fmtDateTime(iso: string, lang: string): string {
  return new Date(iso).toLocaleString(localeFor(lang), {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtUnits(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function HeroCard({ roi }: { roi: InventoryROISummary }) {
  const { t, lang } = useLanguage()
  const hasValue = roi.estimated_value_protected > 0

  return (
    <Card radius={14} padding="28px 32px" style={{ borderTop: `4px solid ${C.indigo}` }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.indigo, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 20 }}>
        {t('roi.hero_eyebrow')}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 28 }}>
        {/* POs generated */}
        <div>
          <div style={{ fontSize: 48, fontWeight: 900, color: C.indigo, lineHeight: 1 }}>
            {roi.total_pos_generated}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
            {roi.total_pos_generated === 1 ? t('roi.po_generated_singular') : t('roi.po_generated_plural')}
          </div>
          {roi.first_po_at && (
            <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
              {t('roi.since_prefix')} {fmtDate(roi.first_po_at, lang)}
              {roi.active_days > 0 && (
                <span style={{ marginLeft: 6, padding: '2px 8px', borderRadius: 20, background: 'color-mix(in srgb, var(--accent) 10%, transparent)', color: C.indigo, fontSize: 11 }}>
                  {roi.active_days} {t('roi.active_days_suffix')}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Urgent stockout risks actually acted on */}
        <div>
          <div style={{ fontSize: 48, fontWeight: 900, color: C.red, lineHeight: 1 }}>
            {fmtUnits(roi.total_skus_protected)}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
            {t('roi.stockout_risks_handled')}
          </div>
          <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
            {t('roi.stockout_risks_handled_detail')}
          </div>
        </div>

        {/* Value or units */}
        <div>
          {hasValue ? (
            <>
              <div style={{ fontSize: 42, fontWeight: 900, color: C.green, lineHeight: 1 }}>
                {formatMoney(roi.estimated_value_protected)}
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
                {t('roi.purchases_managed')}
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
                {t('roi.purchases_managed_detail')}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 48, fontWeight: 900, color: C.amber, lineHeight: 1 }}>
                {fmtUnits(roi.total_units_ordered)}
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
                {t('roi.total_units_ordered')}
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
                {t('roi.add_unit_cost_hint')}
              </div>
            </>
          )}
        </div>
      </div>
    </Card>
  )
}

function AdoptionCard({ roi }: { roi: InventoryROISummary }) {
  const { t } = useLanguage()
  // Only meaningful once we have decision data (cart-based POs).
  if (roi.adoption_rate == null || roi.total_suggested === 0) return null

  const pct = Math.round(roi.adoption_rate * 100)
  const color = pct >= 70 ? C.green : pct >= 40 ? C.amber : C.red

  return (
    <Card radius={14} padding="24px 28px" style={{ borderTop: `4px solid ${color}` }}>
      <div style={{ fontSize: 11, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16 }}>
        {t('roi.adoption_eyebrow')}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 28, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 52, fontWeight: 900, color, lineHeight: 1 }}>{pct}%</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginTop: 6 }}>
            {t('roi.adoption_rate_label')}
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <p style={{ margin: 0, fontSize: 14, color: C.muted, lineHeight: 1.6 }}>
            {t('roi.adoption_followed_prefix')} <strong style={{ color: C.text }}>{fmtUnits(roi.total_approved)}</strong> {t('roi.adoption_followed_of')}{' '}
            <strong style={{ color: C.text }}>{fmtUnits(roi.total_suggested)}</strong> {t('roi.adoption_followed_suffix')}
          </p>
          {/* Progress bar */}
          <div style={{ marginTop: 12, height: 8, borderRadius: 6, background: C.card, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: color, transition: 'width 0.4s' }} />
          </div>
          {roi.total_rejected > 0 && (
            <div style={{ fontSize: 12, color: C.dim, marginTop: 8 }}>
              {fmtUnits(roi.total_rejected)} {t('roi.adoption_rejected_suffix')}
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

function fmtMonthLabel(month: string, lang: string): string {
  const [y, m] = month.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString(localeFor(lang), { month: 'long', year: 'numeric' })
}

function MonthlyEvolutionTable({ rows }: { rows: ROIMonthlyRow[] }) {
  const { t, lang } = useLanguage()

  return (
    <Card padding={0} overflow="hidden">
      <CardHeader
        icon={<TrendingUp size={14} color={C.indigo} />}
        title={t('roi.monthly_evolution_title')}
        style={{ background: C.card }}
      />
      <Table size="lg">
          <thead>
            <tr style={{ background: C.card }}>
              {[
                t('roi.col_month'), t('roi.col_orders'), t('roi.col_stockouts_handled'),
                t('roi.col_value_managed'), t('roi.col_adoption'), t('roi.col_capital_freed'),
              ].map(h => <Th key={h} size="lg">{h}</Th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              /* The zebra stripe and the divider both live on the <tr>, so the
                 cells do not draw dividers of their own. */
              <tr key={row.month} style={{
                background: idx % 2 === 0 ? C.surface : C.card,
                borderBottom: `1px solid ${C.border}`,
              }}>
                <Td size="lg" divider={false} style={{ fontWeight: 600, textTransform: 'capitalize' }}>
                  {fmtMonthLabel(row.month, lang)}
                </Td>
                <Td size="lg" divider={false}>{row.pos_count}</Td>
                <Td size="lg" divider={false} style={{ color: row.skus_order_now > 0 ? C.red : C.dim, fontWeight: row.skus_order_now > 0 ? 700 : 400 }}>
                  {row.skus_order_now}
                </Td>
                <Td size="lg" divider={false} mono style={{ color: C.green }}>
                  {formatMoney(row.total_value)}
                </Td>
                <Td size="lg" divider={false}>
                  {row.adoption_rate != null ? `${Math.round(row.adoption_rate * 100)}%` : '—'}
                </Td>
                <Td size="lg" divider={false} mono style={{ color: row.capital_liberado != null ? C.green : C.dim, fontWeight: row.capital_liberado != null ? 600 : 400 }}>
                  {row.capital_liberado != null
                    ? formatMoney(row.capital_liberado)
                    : t('roi.capital_freed_pending')}
                </Td>
              </tr>
            ))}
          </tbody>
      </Table>
    </Card>
  )
}

// ── Monthly recap (feature 3.2) ───────────────────────────────────────────────
// Mirrors the monthly email exactly. A null metric is rendered as explicitly
// unavailable with the reason, never as a zero that could read as an outcome.

function RecapTile({ value, label, note, color, muted }: {
  value: string; label: string; note: string; color: string; muted?: boolean
}) {
  return (
    <div style={{ padding: '16px 18px', background: C.card, borderRadius: 10, minWidth: 0 }}>
      <div style={{
        fontSize: muted ? 15 : 30, fontWeight: muted ? 600 : 800,
        color, lineHeight: 1.15, wordBreak: 'break-word',
      }}>
        {value}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginTop: 6 }}>{label}</div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 4, lineHeight: 1.5 }}>{note}</div>
    </div>
  )
}

function MonthlyRecapCard({ report }: { report: ROIMonthReport }) {
  const { t, lang } = useLanguage()
  const monthLabel = fmtMonthLabel(report.month, lang)

  const header = (
    <CardHeader
      icon={<Calendar size={14} color={C.indigo} />}
      title={`${t('recap.month_of')} ${monthLabel}`}
      style={{ background: C.card }}
      titleStyle={{ textTransform: 'capitalize' }}
    />
  )

  if (!report.has_sufficient_history) {
    return (
      <Card padding={0} overflow="hidden">
        {header}
        <div style={{ padding: '28px 26px', textAlign: 'center' }}>
          <AlertTriangle size={26} color={C.amber} style={{ opacity: 0.7, marginBottom: 12 }} />
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 8 }}>
            {t('recap.insufficient_title')}
          </div>
          <p style={{ margin: '0 auto', maxWidth: 520, fontSize: 12.5, color: C.muted, lineHeight: 1.7 }}>
            {t('recap.insufficient_body')}
          </p>
        </div>
      </Card>
    )
  }

  const headline = report.capital_freed != null
    ? `${formatMoney(report.capital_freed)} ${t('recap.headline_freed')}`
    : t('recap.headline_no_amount')

  return (
    <Card padding={0} overflow="hidden">
      {header}
      <div style={{ padding: '20px 22px' }}>
        <p style={{ margin: '0 0 18px', fontSize: 15, fontWeight: 600, color: C.text }}>
          {headline}
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          <RecapTile
            value={`${report.orders_generated}`}
            label={t('recap.metric_orders')}
            note={t('recap.metric_orders_note')}
            color={C.indigo}
          />

          {report.adoption_rate != null && (
            <RecapTile
              value={`${Math.round(report.adoption_rate * 100)}%`}
              label={t('recap.metric_adoption')}
              note={t('recap.metric_adoption_note')
                .replace('{followed}', String(report.recommendations_followed))
                .replace('{shown}', String(report.recommendations_shown))}
              color={C.indigo}
            />
          )}

          {report.stockout_risks_handled != null && (
            <RecapTile
              value={`${report.stockout_risks_handled}`}
              label={t('recap.metric_risks')}
              note={t('recap.metric_risks_note')}
              color={C.red}
            />
          )}

          <RecapTile
            value={report.capital_freed != null ? formatMoney(report.capital_freed) : t('recap.unavailable')}
            label={t('recap.metric_capital')}
            note={report.capital_freed != null ? t('recap.metric_capital_note') : t('recap.unavailable_capital')}
            color={report.capital_freed != null ? C.green : C.dim}
            muted={report.capital_freed == null}
          />

          <RecapTile
            value={report.managed_purchase_value != null
              ? formatMoney(report.managed_purchase_value)
              : t('recap.unavailable')}
            label={t('recap.metric_managed')}
            note={report.managed_purchase_value != null
              ? t('recap.metric_managed_note')
              : t('recap.unavailable_managed')}
            color={report.managed_purchase_value != null ? C.text : C.dim}
            muted={report.managed_purchase_value == null}
          />
        </div>

        <div style={{
          marginTop: 18, padding: '14px 16px', borderRadius: 10,
          background: 'color-mix(in srgb, var(--accent) 4%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 18%, transparent)',
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.indigo, marginBottom: 6 }}>
            {t('recap.provenance_title')}
          </div>
          <p style={{ margin: 0, fontSize: 12, color: C.muted, lineHeight: 1.7 }}>
            {t('recap.provenance_body')}
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 11, color: C.dim }}>
            {t('recap.email_note')}
          </p>
        </div>
      </div>
    </Card>
  )
}

function WhyItMattersCard() {
  const { t } = useLanguage()
  return (
    <div style={{
      background: 'color-mix(in srgb, var(--accent) 4%, transparent)',
      border: `1px solid color-mix(in srgb, var(--accent) 18%, transparent)`,
      borderRadius: 12, padding: '22px 26px',
    }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.indigo, marginBottom: 12 }}>
        {t('roi.why_matters_title')}
      </div>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.75 }}>
        {t('roi.why_matters_body')}
      </p>
      <p style={{ margin: '12px 0 0', fontSize: 12, color: C.dim, lineHeight: 1.65 }}>
        {t('roi.why_matters_footnote')}
      </p>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ROIPage() {
  const { t, lang } = useLanguage()
  const [roi,     setRoi]     = useState<InventoryROISummary | null>(null)
  const [monthly, setMonthly] = useState<ROIMonthlyRow[]>([])
  const [recap,   setRecap]   = useState<ROIMonthReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setError(null)
    try {
      const [roiData, monthlyData, recapData] = await Promise.all([
        getInventoryROI(),
        getROIMonthly(),
        getROIMonthReport(),
      ])
      setRoi(roiData)
      setMonthly(monthlyData)
      setRecap(recapData)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('roi.error_loading'))
    } finally {
      if (initial) setLoading(false)
    }
  }, [t])

  useEffect(() => { load(true) }, [load])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 9,
            background: 'linear-gradient(135deg, var(--accent), var(--accent))',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <TrendingUp size={17} color="#fff" strokeWidth={2.5} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              {t('roi.page_title')}
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
              {t('roi.page_subtitle')}
            </p>
          </div>
        </div>
        <Link href="/inventory" style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: C.dim, textDecoration: 'none',
          padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
        }}>
          <ArrowLeft size={12} /> {t('roi.back_to_inventory')}
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 13, color: C.red,
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0 }} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 64, display: 'flex', justifyContent: 'center' }}>
          <Spinner />
        </div>
      ) : roi ? (
        <>
          {/* Section 0 — Last closed month, mirrors the monthly email */}
          {recap && <MonthlyRecapCard report={recap} />}

          {/* Section 1 — Hero counters */}
          <HeroCard roi={roi} />

          {/* Section 1b — Adoption (only with decision data) */}
          <AdoptionCard roi={roi} />

          {/* Section 2 — Monthly evolution */}
          <MonthlyEvolutionTable rows={monthly} />

          {/* Orders now live in /orders */}
          <Link href="/pedidos" style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '14px 18px', borderRadius: 12, textDecoration: 'none',
            background: C.surface, border: `1px solid ${C.border}`,
            fontSize: 13, fontWeight: 600, color: C.indigo,
          }}>
            <ShoppingCart size={14} /> {t('roi.see_orders_link')}
          </Link>

          {/* Section 4 — Why it matters */}
          <WhyItMattersCard />

          {/* Empty state hint if no POs yet */}
          {roi.total_pos_generated === 0 && (
            <div style={{
              padding: '24px', borderRadius: 12, textAlign: 'center',
              background: C.card, border: `1px solid ${C.border}`,
            }}>
              <Package size={32} color={C.dim} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
                {t('roi.no_orders_registered')}
              </div>
              <div style={{ fontSize: 12, color: C.dim, marginBottom: 16 }}>
                {t('roi.no_orders_registered_hint')}
              </div>
              <Link href="/inventory" style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
                color: C.indigo, textDecoration: 'none',
              }}>
                <ShoppingCart size={13} /> {t('roi.go_to_inventory')}
              </Link>
            </div>
          )}

          {/* Last updated note */}
          {roi.last_po_at && (
            <div style={{ fontSize: 11, color: C.dim, textAlign: 'center', paddingBottom: 4 }}>
              {t('roi.last_order_registered_prefix')} {fmtDateTime(roi.last_po_at, lang)}
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}

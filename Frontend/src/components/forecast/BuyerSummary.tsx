'use client'
import Link from 'next/link'
import type { CoverageUnit, InventoryStatusItem, QualityReport, SkuIntelligenceData } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { ArrowRight, Info } from 'lucide-react'
import { championError } from './shared'
import { formatQty } from './BuyerChart'
import {
  confidenceOf, isShortHistory, perPeriodKey, stockHorizon, type Confidence,
} from './buyerFacts'

// How many upcoming periods "what will sell" adds up. Four is a month of weeks
// or a season of months: enough to plan an order against.
const SELL_PERIODS = 4
const DANGER = '#C0504D'
const WARN = '#B7791F'
// Past this the exact day stops being information.
const MAX_COVERAGE_DAYS = 365

const PERIODS_KEY: Record<string, string> = {
  daily: 'skus.periods_days', weekly: 'skus.periods_weeks', monthly: 'skus.periods_months',
  quarterly: 'skus.periods_quarters', yearly: 'skus.periods_years',
}

function Card({ label, children, tone, tourAnchor }: {
  label: string; children: React.ReactNode; tone?: 'accent' | 'danger'; tourAnchor?: string
}) {
  return (
    <div
      data-tour={tourAnchor}
      style={{
        flex: '1 1 210px', minWidth: 0, boxSizing: 'border-box',
        padding: '12px 14px', borderRadius: 12,
        background: tone === 'accent'
          ? 'color-mix(in srgb, var(--accent) 7%, var(--surface))' : 'var(--surface-2)',
        border: `1px solid ${tone === 'accent'
          ? 'color-mix(in srgb, var(--accent) 30%, var(--border))'
          : tone === 'danger' ? 'rgba(192,80,77,0.35)' : 'var(--border)'}`,
        display: 'flex', flexDirection: 'column', gap: 3,
      }}
    >
      <div style={{ fontSize: 12, color: 'var(--dim)', fontWeight: 500 }}>{label}</div>
      {children}
    </div>
  )
}

const BIG: React.CSSProperties = { fontSize: 17, fontWeight: 700, lineHeight: 1.25, fontVariantNumeric: 'tabular-nums' }
const SMALL: React.CSSProperties = { fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.45 }

/**
 * The three questions a buyer brings to a product, answered side by side:
 * what will sell, when the stock runs out against the supplier's lead time,
 * and what to order. Figures are the API's (forecast points, live stock
 * status); this only words them.
 */
export function BuyerAnswers({ data, status, coverageUnit, onSeeOrder }: {
  data: SkuIntelligenceData
  status: InventoryStatusItem | undefined
  coverageUnit: CoverageUnit | undefined
  /** Used when there is no live stock row: the Inventory tab still carries the
   *  training-time recommendation. */
  onSeeOrder?: () => void
}) {
  const { t, lang } = useLanguage()
  const locale = lang === 'en' ? 'en' : 'es'
  const next = data.forecast.slice(0, SELL_PERIODS)
  const sellTotal = next.reduce((s, p) => s + p.value, 0)
  const periodWord = t(PERIODS_KEY[data.applied_granularity] ?? 'skus.periods_generic')
  const horizon = stockHorizon(status, coverageUnit)
  const fmtDate = (ts: number) => new Intl.DateTimeFormat(locale, {
    timeZone: 'UTC', day: 'numeric', month: 'short',
  }).format(ts)

  // Question 2 — when does the stock run out against the supplier?
  let stockValue = '—'
  let stockNote = t('skus.answer_stock_unknown')
  let stockTone: 'danger' | undefined
  if (horizon) {
    stockValue = horizon.coverageDays > MAX_COVERAGE_DAYS
      ? t('skus.answer_stock_long') : fmtDate(horizon.runoutTs)
    if (horizon.gapDays > 0) {
      stockTone = 'danger'
      stockNote = t('skus.answer_stock_gap', { gap: horizon.gapDays, lead: Math.round(horizon.leadDays) })
    } else {
      stockNote = t('skus.answer_stock_ok', { lead: Math.round(horizon.leadDays) })
    }
  }

  // Question 3 — what to order?
  const qty = status?.recommended_qty ?? null
  const incoming = status?.incoming_qty ?? 0
  const orderNow = qty != null && qty > 0

  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', padding: '12px 16px 4px' }}>
      <Card label={t('skus.answer_sell_title')} tourAnchor="skus.outlook">
        {next.length > 0 ? (
          <>
            <div style={BIG}>{t('skus.answer_sell_value', { qty: formatQty(sellTotal, locale) })}</div>
            <div style={SMALL}>{t('skus.answer_sell_note', { n: next.length, period: periodWord })}</div>
          </>
        ) : <div style={SMALL}>{t('skus.answer_sell_none')}</div>}
      </Card>

      <Card label={t('skus.answer_stock_title')} tone={stockTone}>
        <div style={{ ...BIG, color: stockTone ? DANGER : 'var(--fg)' }}>{stockValue}</div>
        <div style={SMALL}>{stockNote}</div>
      </Card>

      <Card label={t('skus.answer_order_title')} tone={orderNow ? 'accent' : undefined}>
        {status ? (
          <>
            <div style={BIG}>
              {orderNow
                ? t('skus.answer_order_value', { qty: formatQty(qty!, locale) })
                : t('skus.answer_order_none')}
            </div>
            {incoming > 0 && (
              <div style={SMALL}>{t('skus.answer_order_incoming', { qty: formatQty(incoming, locale) })}</div>
            )}
            {orderNow && (
              <Link
                href="/inventario"
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 4,
                  minHeight: 32, fontSize: 13, fontWeight: 600, color: 'var(--accent)', textDecoration: 'none',
                }}
              >
                {t('skus.answer_order_cta')} <ArrowRight size={13} aria-hidden="true" />
              </Link>
            )}
          </>
        ) : (
          <>
            <div style={SMALL}>{t('skus.answer_order_unknown')}</div>
            {onSeeOrder && (
              <button
                onClick={onSeeOrder}
                style={{
                  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
                  marginTop: 4, minHeight: 32, fontSize: 13, fontWeight: 600, color: 'var(--accent)',
                }}
              >
                {t('skus.outlook_see_order')} <ArrowRight size={13} aria-hidden="true" />
              </button>
            )}
          </>
        )}
      </Card>
    </div>
  )
}

const LEVEL_FILLED: Record<Confidence, number> = { high: 3, medium: 2, low: 1 }
const LEVEL_COLOR: Record<Confidence, string> = { high: '#2E8B62', medium: WARN, low: DANGER }

/** Three dots, filled by level, plus the word: a confidence cue that never
 *  depends on colour alone and never shouts. */
function ConfidenceDots({ level }: { level: Confidence }) {
  const { t } = useLanguage()
  const color = LEVEL_COLOR[level]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span aria-hidden="true" style={{ display: 'inline-flex', gap: 3 }}>
        {[1, 2, 3].map(i => (
          <span key={i} style={{
            width: 7, height: 7, borderRadius: '50%',
            background: i <= LEVEL_FILLED[level] ? color : 'transparent',
            border: `1.5px solid ${i <= LEVEL_FILLED[level] ? color : 'var(--border)'}`,
          }} />
        ))}
      </span>
      <span style={{ color: 'var(--fg)', fontWeight: 600 }}>{t(`skus.confidence_${level}`)}</span>
    </span>
  )
}

/**
 * How far to trust the curve, in plain words: a calm confidence indicator, the
 * typical miss in units (or a percentage when units are unavailable), and a
 * gentle note when the history is short. The precise metric names live in the
 * technical view only.
 */
export function BuyerTrust({ data, quality }: {
  data: SkuIntelligenceData
  quality?: QualityReport[string]
}) {
  const { t, lang } = useLanguage()
  const locale = lang === 'en' ? 'en' : 'es'
  const err = championError(data.metrics)
  const level = confidenceOf(data.metrics, quality?.quality_score)
  const short = isShortHistory(data)

  const parts: string[] = []
  if (err.mae != null && err.mae > 0) {
    parts.push(t('skus.trust_typical_miss', {
      qty: formatQty(err.mae, locale), per: t(perPeriodKey(data.original_freq)),
    }))
  }
  if (err.wape != null) {
    parts.push(t('skus.trust_avg_error', { pct: Math.round(err.wape * 100) }))
  }
  if (level == null && parts.length === 0 && !short) return null

  return (
    <div style={{
      padding: '4px 16px 12px', display: 'flex', flexDirection: 'column', gap: 6,
      fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px 14px', flexWrap: 'wrap' }}>
        {level && <ConfidenceDots level={level} />}
        {parts.length > 0 && <span>{parts.join(' · ')}</span>}
      </div>
      {short && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
          <Info size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
          <span>{t('skus.trust_short_history')}</span>
        </div>
      )}
    </div>
  )
}

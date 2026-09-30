'use client'
import type { ForecastPoint, SkuIntelligenceData } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { ArrowRight } from 'lucide-react'
import { fmtK } from './shared'

// How many upcoming periods the buyer view spells out. Four is a month of
// weeks or a season of months: enough to plan an order against, few enough to
// read in one glance. The full horizon is still on the chart right above.
const OUTLOOK_PERIODS = 4

/** The likely range of one forecast point, read exactly the way the chart's
 *  legend band reads it: P10–P90, falling back to the lower/upper pair of a
 *  session stored before the engine kept quantiles. Display only — the values
 *  are the engine's, nothing is recomputed here. */
function likelyRange(p: ForecastPoint): [number | null, number | null] {
  const rec = p as unknown as Record<string, number | null | undefined>
  const lo = typeof rec['q10'] === 'number' ? rec['q10'] : (typeof p.lower === 'number' ? p.lower : null)
  const hi = typeof rec['q90'] === 'number' ? rec['q90'] : (typeof p.upper === 'number' ? p.upper : null)
  return [lo ?? null, hi ?? null]
}

/**
 * The buyer view's reading of the forecast: what is expected to sell in each
 * of the next few periods, and the range it will probably land in — the chart's
 * band said in numbers. The next step for a buyer is the Inventory tab, where
 * the same forecast becomes a reorder point and a signal, so the strip links
 * there rather than inventing its own ordering advice.
 */
export function BuyerOutlook({ data, formatDate, onSeeOrder }: {
  data: SkuIntelligenceData
  /** The chart's own x-axis formatter, so the strip and the axis name a
   *  period the same way. */
  formatDate: (value: string, index: number) => string
  onSeeOrder?: () => void
}) {
  const { t } = useLanguage()
  const next = data.forecast.slice(0, OUTLOOK_PERIODS)
  if (next.length === 0) return null

  return (
    <div
      data-tour="skus.outlook"
      style={{
        padding: '8px 16px 6px', borderTop: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', gap: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div>
          <span style={{ fontSize: 12, fontWeight: 600 }}>{t('skus.outlook_title')}</span>
          <span style={{ fontSize: 10.5, color: 'var(--dim)', marginLeft: 8 }}>{t('skus.outlook_caption')}</span>
        </div>
        {onSeeOrder && (
          <button
            onClick={onSeeOrder}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, fontWeight: 600, color: 'var(--accent)',
            }}
          >
            {t('skus.outlook_see_order')} <ArrowRight size={11} />
          </button>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {next.map((p, i) => {
          const [lo, hi] = likelyRange(p)
          return (
            <div key={p.date} style={{
              flex: '1 1 110px', minWidth: 0,
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: 8, padding: '6px 10px',
            }}>
              <div style={{ fontSize: 10, color: 'var(--dim)' }}>{formatDate(p.date, i)}</div>
              <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.25 }}>{fmtK(p.value)}</div>
              {lo !== null && hi !== null && (
                <div style={{ fontSize: 10, color: 'var(--dim)' }}>
                  {t('skus.outlook_likely', { lo: fmtK(lo), hi: fmtK(hi) })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

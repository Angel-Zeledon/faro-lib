'use client'
import type { PolicyBacktest } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoneyCompact } from '@/lib/currency'
import { Scale } from 'lucide-react'
import { pct, fmtK } from './shared'

// ── Policy backtest ───────────────────────────────────────────────────────────
//
// The one figure on this screen a distributor can check against their own
// experience. WAPE cannot express it: a forecast biased 20% high posts a
// respectable error while quietly doubling the stock in the warehouse, and a
// forecast biased 20% low posts the SAME error while emptying the shelves. The
// engine therefore replays the ordering policy over demand that actually
// happened — twice, once on the model's forecast and once on "order what we
// ordered last time" — and this panel reports both runs side by side.
//
// Three rules the layout exists to enforce:
//
//   * Never a number without its baseline. "96% fill rate" is unreadable; "96%
//     against 85% ordering as usual" is a claim someone can act on.
//   * Never service level without the inventory it cost. Those two are in
//     tension, and showing only the flattering half is the exact dishonesty
//     this metric was built to prevent.
//   * Never let `n_series` go unsaid. The simulation only covers series whose
//     model ran a rolling-origin backtest, which is normally fewer than the
//     catalogue — so the coverage is stated twice, as a chip and as a sentence.

export function PolicyTile({ value, label, detail }: {
  value: string; label: string; detail: string
}) {
  return (
    <div style={{ flex: 1, minWidth: 128, padding: '9px 12px' }}>
      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg)', lineHeight: 1.2 }}>
        {value}
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
      {detail && (
        <div style={{ fontSize: 10.5, color: 'var(--dim)', opacity: 0.8, marginTop: 3, lineHeight: 1.4 }}>
          {detail}
        </div>
      )}
    </div>
  )
}

export function PolicyBacktestPanel({ backtest, catalogueSize }: {
  backtest: PolicyBacktest | null
  /** SKUs this session actually trained, so the coverage can be stated as a
   *  fraction. 0 when the catalogue is not known yet — the copy drops the
   *  denominator rather than inventing one. */
  catalogueSize: number
}) {
  const { t } = useLanguage()
  const summary = backtest?.summary

  // Absent renders as absent. `policy_backtest` is `{}` for a run whose models
  // produced no rolling-origin backtest and for every session trained before
  // the engine computed this — printing zeros there would be the product making
  // a claim about a simulation it never ran.
  if (!summary || !summary.n_series) return null

  const units = t('skus.policy_units')
  const money = (n: number | null | undefined) =>
    n == null ? '—' : formatMoneyCompact(n)

  const tiles: { value: string; label: string; detail: string }[] = [
    {
      value:  pct(summary.fill_rate),
      label:  t('skus.policy_fill_rate'),
      detail: t('skus.policy_vs_baseline', { baseline: pct(summary.baseline_fill_rate) }),
    },
    {
      value:  String(summary.stockout_buckets),
      label:  t('skus.policy_stockout_buckets'),
      detail: t('skus.policy_vs_baseline', { baseline: String(summary.baseline_stockout_buckets) }),
    },
    {
      value:  `${fmtK(summary.avg_inventory)} ${units}`,
      label:  t('skus.policy_avg_inventory'),
      detail: t('skus.policy_vs_baseline', {
        baseline: `${fmtK(summary.baseline_avg_inventory)} ${units}`,
      }),
    },
  ]
  // No unit cost in the session means no way to value the stock. The tile is
  // dropped rather than shown as zero money.
  if (summary.capital_tied_up != null) {
    tiles.push({
      value:  money(summary.capital_tied_up),
      label:  t('skus.policy_capital'),
      detail: summary.baseline_capital_tied_up != null
        ? t('skus.policy_vs_baseline', { baseline: money(summary.baseline_capital_tied_up) })
        : '',
    })
  }

  const covered  = summary.n_series
  const hasTotal = catalogueSize > 0 && catalogueSize >= covered
  const chip = hasTotal
    ? t('skus.policy_coverage_chip', { n: covered, total: catalogueSize })
    : t('skus.policy_coverage_chip_plain', { n: covered })
  const coverageNote = hasTotal
    ? t('skus.policy_coverage_note', { n: covered, total: catalogueSize })
    : t('skus.policy_coverage_note_plain', { n: covered })

  // Only stated when it is true in that direction. A negative "avoided" is a
  // regression and gets said as one, never folded into a positive-sounding word.
  const avoided = summary.stockouts_avoided
  const avoidedNote = avoided > 0 ? t('skus.policy_stockouts_avoided', { n: avoided })
    : avoided < 0 ? t('skus.policy_stockouts_added', { n: -avoided })
    : ''

  return (
    <div
      role="region"
      aria-label={t('skus.policy_title')}
      style={{
        border: '1px solid var(--border)',
        borderLeft: '4px solid var(--accent)',
        borderRadius: 10,
        background: 'var(--surface)',
        padding: '14px 16px',
        marginBottom: 18,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <Scale size={16} color="var(--accent)" style={{ flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
              {t('skus.policy_title')}
            </span>
            {/* Coverage rides in the header, so the number can never be read
                as catalogue-wide even by someone who only scans the tiles. */}
            <span style={{
              fontSize: 10.5, fontWeight: 700, color: 'var(--accent)',
              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
              padding: '2px 8px', borderRadius: 20, whiteSpace: 'nowrap',
            }}>
              {chip}
            </span>
            {avoidedNote && (
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>{avoidedNote}</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 3, lineHeight: 1.5 }}>
            {t('skus.policy_subtitle')}
          </div>
        </div>
      </div>

      {/* Same stats-strip treatment the SKU panel uses, so the two read as one
          product rather than two dashboards. */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 0, marginTop: 12,
        border: '1px solid var(--border)', borderRadius: 8,
        background: 'var(--surface-2)', overflow: 'hidden',
      }}>
        {tiles.map((tile, i) => (
          <div key={tile.label} style={{
            flex: 1, minWidth: 128,
            borderRight: i < tiles.length - 1 ? '1px solid var(--border)' : undefined,
          }}>
            <PolicyTile {...tile} />
          </div>
        ))}
      </div>

      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 9, lineHeight: 1.55 }}>
        {t('skus.policy_tradeoff')}
      </div>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4, lineHeight: 1.55 }}>
        {coverageNote}
      </div>
    </div>
  )
}

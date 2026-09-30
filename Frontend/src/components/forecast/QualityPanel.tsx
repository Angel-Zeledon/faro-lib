'use client'
import type { QualityReport } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { pct } from './shared'

// ── Quality panel ─────────────────────────────────────────────────────────────

/** The per-SKU quality warnings, in the reader's language.
 *
 * The engine emits them as English sentences — `"1 outliers"`,
 * `"3 missing dates"`, `"Only 12 rows (min=20)"` — and this panel printed the
 * list verbatim. A Spanish user with a spiky series read "1 outliers" beside
 * fully translated labels.
 *
 * Rebuilding them here needs no new backend field and, crucially, invents no
 * thresholds: every line below is driven by a decision the ENGINE already
 * published — its own outlier count, its own missing-date count, its own
 * `has_min_history` boolean, its own `series_flags`. Re-deriving "is this
 * intermittent?" from `zero_ratio` and a guessed cutoff would recreate exactly
 * the 1.5-vs-3.0 IQR split this screen just finished repairing.
 *
 * Anything the engine warns about that we have not modelled still surfaces, in
 * English, rather than disappearing: a warning we drop is a warning the user
 * never learns about.
 */
export function useQualityWarnings() {
  const { t } = useLanguage()
  return (q: QualityReport[string]): string[] => {
    const lines: string[] = []
    const raw: string[] = Array.isArray(q?.warnings) ? q.warnings as string[] : []

    if (q?.has_min_history === false) lines.push(t('skus.quality_warn_short_history', { n: q.n_rows }))
    if ((q?.n_outliers ?? 0) > 0)     lines.push(t('skus.quality_warn_outliers', { n: q.n_outliers }))
    if ((q?.missing_dates ?? 0) > 0)  lines.push(t('skus.quality_warn_missing_dates', { n: q.missing_dates }))
    if (Array.isArray(q?.series_flags) && q.series_flags.includes('intermittent')) {
      lines.push(t('skus.quality_warn_intermittent', { pct: pct(q.zero_ratio) }))
    }

    // Markers of the four the engine currently emits (quality.py `_check_sku`).
    // Used only to answer "did we already say this?" — never to build a
    // sentence — so an engine that grows a fifth warning still shows it.
    const covered = /outliers|missing dates|rows \(min=|Intermittent:/
    lines.push(...raw.filter(w => !covered.test(w)))
    return lines
  }
}

export function QualityPanel({ q }: { q: QualityReport[string] }) {
  const { t } = useLanguage()
  const warningLines = useQualityWarnings()(q)
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
        {[
          { label: t('skus.quality_records'),       value: q.n_rows },
          { label: t('skus.quality_outliers'), value: q.n_outliers },
          { label: t('skus.quality_missing_data'), value: pct(q.missing_pct) },
        ].map(({ label, value }) => (
          <div key={label} style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '10px 12px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>
      {[
        { label: t('skus.quality_score_label'), value: q.quality_score, color: '#22c55e' },
        { label: t('skus.quality_missing_data'),       value: q.missing_pct,   color: '#ef4444' },
      ].map(({ label, value, color }) => (
        <div key={label}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{label}</span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{pct(value)}</span>
          </div>
          <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: value != null && !isNaN(value) ? pct(value) : '0%', background: color, borderRadius: 2, transition: 'width 0.6s ease' }} />
          </div>
        </div>
      ))}
      {warningLines.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {warningLines.map((w, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: '#f59e0b' }}>
              <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />{w}
            </div>
          ))}
        </div>
      )}
      {q.is_valid && warningLines.length === 0 && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#22c55e' }}>
          <CheckCircle2 size={12} /> {t('skus.series_clean_no_warnings')}
        </div>
      )}
    </div>
  )
}

/** The Quality tab (technical view): the summary that is always visible, and
 *  the full statistical detail behind its own disclosure. `showStats` is owned
 *  by the page so it survives switching tabs, as it did before the split. */
export function QualityTab({ q, showStats, onToggleStats }: {
  q: QualityReport[string]
  showStats: boolean
  onToggleStats: () => void
}) {
  const { t } = useLanguage()
  const warnings = useQualityWarnings()(q)
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Always-visible quality summary */}
      <div style={{ padding: '16px 20px 0' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 12 }}>
          {[
            { label: t('skus.quality_records'),        value: q.n_rows },
            { label: t('skus.quality_outliers'), value: q.n_outliers },
            { label: t('skus.quality_missing_data'),  value: pct(q.missing_pct) },
          ].map(({ label, value }) => (
            <div key={label} style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '10px 12px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{value}</div>
              <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
            </div>
          ))}
        </div>
        {q.is_valid && warnings.length === 0 && (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#22c55e', marginBottom: 12 }}>
            <CheckCircle2 size={12} /> {t('skus.series_clean_no_warnings')}
          </div>
        )}
        {/* Same localized lines as QualityPanel — this block is a second
            rendering of the same report, and it was the one actually on screen
            when "1 outliers" showed up in Spanish. */}
        {warnings.length > 0 && <QualityWarningList lines={warnings} style={{ marginBottom: 12 }} />}
      </div>
      {/* Toggle for full statistical detail */}
      <div style={{ padding: '0 20px 16px' }}>
        <button
          onClick={onToggleStats}
          style={{
            all: 'unset', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: 'var(--dim)', padding: '8px 0',
            borderTop: '1px solid var(--border)', width: '100%',
          }}
        >
          <span style={{ fontSize: 10 }}>{showStats ? '▲' : '▼'}</span>
          {showStats ? t('skus.btn_hide') : t('skus.btn_view')} {t('skus.detailed_stat_analysis')}
        </button>
        {showStats && (
          <div style={{ marginTop: 4 }}>
            <QualityPanel q={q} />
          </div>
        )}
      </div>
    </div>
  )
}

/** The engine's warnings about one series, as amber lines. Used by the Quality
 *  tab and, in the buyer view, under the SKU header — a buyer has no use for
 *  the Quality tab, but does need to know when the curve rests on thin data. */
export function QualityWarningList({ lines, style }: { lines: string[]; style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, ...style }}>
      {lines.map((w, i) => (
        <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: '#f59e0b' }}>
          <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />{w}
        </div>
      ))}
    </div>
  )
}

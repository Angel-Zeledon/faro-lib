'use client'
import { useEffect, useState } from 'react'
import { Timer } from 'lucide-react'
import { getRunDurations } from '@/lib/api'
import type { RunDurations, RunDurationSummary } from '@/lib/api'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * How long this company's trainings really take, by catalogue size and by
 * granularity, over the last runs. Read-only evidence for choosing a retrain
 * cadence; it lives under the schedule because that is where the cadence is set.
 */
export default function RunDurationsPanel() {
  const { t } = useLanguage()
  const [data, setData] = useState<RunDurations | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    getRunDurations().then(setData).catch(() => setFailed(true))
  }, [])

  if (failed) return null     // a secondary panel: silence beats a second error banner
  if (!data) return null

  const dur = (s: number) => s < 60
    ? t('runs.seconds', { n: Math.round(s) })
    : t('runs.minutes', { m: Math.floor(s / 60), s: Math.round(s % 60) })

  const sizeLabel = (min: number, max: number | null) =>
    max == null ? t('runs.size_open', { min }) : t('runs.size_range', { min, max })
  const granLabel = (g: string) =>
    ['daily', 'weekly', 'monthly'].includes(g) ? t(`runs.gran_${g}`) : t('runs.gran_unknown')

  const th: React.CSSProperties = {
    textAlign: 'left', fontSize: 11, fontWeight: 600, color: 'var(--dim)', padding: '4px 8px 4px 0',
  }
  const td: React.CSSProperties = {
    fontSize: 12, padding: '5px 8px 5px 0', fontVariantNumeric: 'tabular-nums', borderTop: '1px solid var(--border)',
  }

  const table = (title: string, rows: Array<{ label: string } & RunDurationSummary>) => rows.length === 0 ? null : (
    <div style={{ minWidth: 0, flex: '1 1 260px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{title}</div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={th}>{t('runs.col_group')}</th>
            <th style={th}>{t('runs.col_runs')}</th>
            <th style={th}>{t('runs.col_median')}</th>
            <th style={th}>{t('runs.col_p95')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.label}>
              <td style={td}>{r.label}</td>
              <td style={td}>{r.runs}</td>
              <td style={td}>{dur(r.median_seconds)}</td>
              <td style={td}>{dur(r.p95_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  return (
    <Card tone="inset" padding="16px 18px" data-testid="run-durations"
          style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Timer size={15} color="var(--accent)" aria-hidden="true" />
        <div style={{ fontSize: 14, fontWeight: 700 }}>{t('runs.title')}</div>
      </div>
      {data.overall == null ? (
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('runs.empty')}</div>
      ) : (
        <>
          <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.5 }}>
            {t('runs.subtitle', { n: data.completed_runs })}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20 }}>
            {table(t('runs.by_size'),
              data.by_size.map(b => ({ ...b, label: sizeLabel(b.min_series, b.max_series) })))}
            {table(t('runs.by_granularity'),
              data.by_granularity.map(g => ({ ...g, label: granLabel(g.granularity) })))}
          </div>
          {data.failed_runs > 0 && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>{t('runs.failed_note', { n: data.failed_runs })}</div>
          )}
        </>
      )}
    </Card>
  )
}

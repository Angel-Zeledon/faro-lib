'use client'
import { useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { getAccuracyTracking } from '@/lib/api'
import type { AccuracyTracking } from '@/lib/api'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'
import { fmtNum } from '@/lib/numberLocale'

const COLOR: Record<AccuracyTracking['status'], string> = {
  degraded: '#B7791F', stable: 'var(--accent)', too_little: 'var(--muted)', no_baseline: 'var(--muted)',
}

const pct = (x: number | null) =>
  x == null ? '—' : `${fmtNum(x * 100, { maximumFractionDigits: 1 })}%`

/**
 * The stored automatic reading of how a session's forecast is doing against
 * the sales uploaded after it was made, next to its accuracy at training time.
 * Nothing is computed here: the backend refreshes it when a file lands.
 * Renders nothing until such a reading exists.
 */
export default function AccuracyTrackingCard({ sessionId }: { sessionId: string }) {
  const { t } = useLanguage()
  const [row, setRow] = useState<AccuracyTracking | null>(null)

  useEffect(() => {
    let stale = false
    setRow(null)
    getAccuracyTracking(sessionId).then(r => { if (!stale) setRow(r) }).catch(() => {})
    return () => { stale = true }
  }, [sessionId])

  if (!row) return null

  const headline = row.status === 'degraded'
    ? t('precision.tracking_degraded', { pct: fmtNum(row.degradation_pct ?? 0, { maximumFractionDigits: 0 }) })
    : t(`precision.tracking_${row.status}`)

  return (
    <Card padding={14} data-testid="accuracy-tracking" style={{ borderLeft: `4px solid ${COLOR[row.status]}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>
        <Activity size={12} aria-hidden="true" />{t('precision.tracking_title')}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', lineHeight: 1.4 }}>{headline}</div>
      {row.realised_wape != null && (
        <div style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55, marginTop: 4 }}>
          {t('precision.tracking_detail', {
            baseline: pct(row.baseline_wape), realised: pct(row.realised_wape), n: row.n_points,
            from: row.compared_from ?? '—', to: row.compared_to ?? '—',
          })}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6 }}>{t('precision.tracking_note')}</div>
    </Card>
  )
}

'use client'
import { useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { getAdjustmentValueAdded } from '@/lib/api'
import type { AdjustmentValueAdded, ValueAddedGroup } from '@/lib/types'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'
import { fmtNum } from '@/lib/numberLocale'

const COLOR: Record<ValueAddedGroup['verdict'], string> = {
  improved: 'var(--accent)', worsened: '#B7791F', neutral: 'var(--muted)',
  too_little: 'var(--muted)', no_data: 'var(--muted)',
}

const abs0 = (x: number) => fmtNum(Math.abs(x), { maximumFractionDigits: 0 })

/** One sentence about a group: "improved the error by 12%" / "worsened it by 8%". */
function sentence(t: (k: string, p?: Record<string, string | number>) => string, g: ValueAddedGroup): string {
  if (g.verdict === 'too_little' || g.verdict === 'no_data') return t('forecast_adj.value_too_little', { n: g.n_points })
  if (g.improvement_pct == null || g.verdict === 'neutral') return t('forecast_adj.value_neutral')
  return t(g.improvement_pct > 0 ? 'forecast_adj.value_improved' : 'forecast_adj.value_worsened',
    { pct: abs0(g.improvement_pct) })
}

/**
 * Forecast value added: once real sales arrived, did the adjusted forecast run
 * closer to them than the model's own? Aggregate first, then per person and per
 * reason. Nothing is computed here (ForecastingCore does it); renders nothing
 * until somebody has adjusted a forecast of this run.
 */
export default function AdjustmentValueCard({ sessionId }: { sessionId: string }) {
  const { t } = useLanguage()
  const [d, setD] = useState<AdjustmentValueAdded | null>(null)

  useEffect(() => {
    let stale = false
    setD(null)
    getAdjustmentValueAdded(sessionId, { silent: true }).then(r => { if (!stale) setD(r) }).catch(() => {})
    return () => { stale = true }
  }, [sessionId])

  if (!d || d.status === 'no_adjustments') return null

  const agg = d.aggregate
  const headline = d.status !== 'ok' || !agg
    ? t('forecast_adj.value_waiting', { n: d.n_adjustments })
    : sentence(t, agg)

  return (
    <Card padding={14} data-testid="adjustment-value"
          style={{ borderLeft: `4px solid ${agg ? COLOR[agg.verdict] : 'var(--muted)'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>
        <SlidersHorizontal size={12} aria-hidden="true" />{t('forecast_adj.value_title')}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', lineHeight: 1.4 }}>{headline}</div>
      {agg && d.status === 'ok' && (
        <div style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55, marginTop: 4 }}>
          {t('forecast_adj.value_detail', { n: agg.n_points, better: agg.better_points, worse: agg.worse_points })}
        </div>
      )}
      {d.status === 'ok' && (d.by_user.length > 1 || d.by_reason.length > 1) && (
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginTop: 10 }}>
          {d.by_user.length > 1 && (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, fontSize: 12, display: 'flex', flexDirection: 'column', gap: 3 }}>
              {d.by_user.map(u => (
                <li key={u.user} style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>
                  <strong>{u.name || '—'}</strong> <span style={{ color: 'var(--dim)' }}>{sentence(t, u)}</span>
                </li>
              ))}
            </ul>
          )}
          {d.by_reason.length > 1 && (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, fontSize: 12, display: 'flex', flexDirection: 'column', gap: 3 }}>
              {d.by_reason.map(r => (
                <li key={r.reason} style={{ color: 'var(--text)' }}>
                  <strong>{t(`forecast_adj.reason.${r.reason}`)}</strong> <span style={{ color: 'var(--dim)' }}>{sentence(t, r)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6 }}>{t('forecast_adj.value_note')}</div>
    </Card>
  )
}

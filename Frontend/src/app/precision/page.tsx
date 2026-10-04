'use client'
import { useEffect, useMemo, useState } from 'react'
import { Target } from 'lucide-react'
import { getForecastVsActual, getSessionSummaries } from '@/lib/api'
import type { ForecastVsActual, RealizedPoint } from '@/lib/api'
import type { SessionSummary } from '@/lib/types'
import { EmptyState, InlineError, LoadingState, SkeletonTable } from '@/components/ui/States'
import Card from '@/components/ui/Card'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePlanning } from '@/contexts/PlanningContext'
import { fmtNum } from '@/lib/numberLocale'

// "Forecast vs. reality": what a finished run predicted for dates that have
// since passed, graded against the sales uploaded afterwards. All the maths is
// in ForecastingCore; this screen only shows it and says it in plain words.

const pct = (x: number | null | undefined, signed = false) => {
  if (x == null) return '—'
  const v = x * 100
  return `${signed && v > 0 ? '+' : ''}${fmtNum(v, { maximumFractionDigits: 1 })}%`
}

const VERDICT_COLOR: Record<string, string> = {
  good: 'var(--accent)', fair: 'var(--muted)', poor: 'var(--danger, #dc2626)',
  too_little: 'var(--dim)', no_data: 'var(--dim)',
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card padding={14}>
      <div title={hint} style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </Card>
  )
}

function CompareChart({ series, labels }: { series: RealizedPoint[]; labels: { forecast: string; actual: string } }) {
  const W = 720, H = 240, PAD = { l: 44, r: 12, t: 12, b: 26 }
  if (series.length === 0) return null
  const max = Math.max(1, ...series.flatMap(p => [p.forecast, p.actual]))
  const x = (i: number) => PAD.l + (series.length === 1 ? (W - PAD.l - PAD.r) / 2 : (i * (W - PAD.l - PAD.r)) / (series.length - 1))
  const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b)
  const line = (key: 'forecast' | 'actual') => series.map((p, i) => `${x(i)},${y(p[key])}`).join(' ')
  const ticks = [0, 0.5, 1]
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${labels.forecast} / ${labels.actual}`}
           style={{ width: '100%', height: 'auto', display: 'block' }}>
        {ticks.map(tk => (
          <g key={tk}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(max * tk)} y2={y(max * tk)} stroke="var(--border)" strokeWidth={1} />
            <text x={PAD.l - 6} y={y(max * tk) + 3} textAnchor="end" fontSize={10} fill="var(--dim)">
              {fmtNum(max * tk, { maximumFractionDigits: 0 })}
            </text>
          </g>
        ))}
        <polyline points={line('actual')} fill="none" stroke="var(--text)" strokeWidth={2} />
        <polyline points={line('forecast')} fill="none" stroke="var(--accent)" strokeWidth={2} strokeDasharray="5 3" />
        {series.map((p, i) => (
          <g key={p.date}>
            <circle cx={x(i)} cy={y(p.actual)} r={3} fill="var(--text)"><title>{`${p.date} · ${labels.actual}: ${fmtNum(p.actual, { maximumFractionDigits: 1 })}`}</title></circle>
            <circle cx={x(i)} cy={y(p.forecast)} r={3} fill="var(--accent)"><title>{`${p.date} · ${labels.forecast}: ${fmtNum(p.forecast, { maximumFractionDigits: 1 })}`}</title></circle>
          </g>
        ))}
        <text x={PAD.l} y={H - 8} fontSize={10} fill="var(--dim)">{series[0].date}</text>
        <text x={W - PAD.r} y={H - 8} fontSize={10} fill="var(--dim)" textAnchor="end">{series[series.length - 1].date}</text>
      </svg>
      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--dim)', marginTop: 6 }}>
        <span><span style={{ display: 'inline-block', width: 16, borderTop: '2px dashed var(--accent)', verticalAlign: 'middle', marginRight: 6 }} />{labels.forecast}</span>
        <span><span style={{ display: 'inline-block', width: 16, borderTop: '2px solid var(--text)', verticalAlign: 'middle', marginRight: 6 }} />{labels.actual}</span>
      </div>
    </div>
  )
}

export default function PrecisionPage() {
  const { t, lang } = useLanguage()
  const planning = usePlanning()?.planning ?? null

  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [datasetId, setDatasetId] = useState<string | undefined>(undefined)
  const [data, setData] = useState<ForecastVsActual | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [sku, setSku] = useState<string | null>(null)

  useEffect(() => {
    getSessionSummaries(0, 100)
      .then(r => setSessions((r.items ?? []).filter(s => s.status === 'COMPLETED')))
      .catch(e => { setSessions([]); setError(e) })
  }, [])

  // Default to the session the app plans from.
  useEffect(() => {
    if (sessionId || !sessions?.length) return
    const active = planning?.active_session_id
    setSessionId(sessions.find(s => (s.session_id ?? s.id) === active)?.session_id ?? sessions[0].session_id ?? sessions[0].id)
  }, [sessions, planning, sessionId])

  useEffect(() => {
    if (!sessionId) return
    let stale = false
    setLoading(true); setError(null); setSku(null)
    getForecastVsActual(sessionId, datasetId)
      .then(d => { if (!stale) setData(d) })
      .catch(e => { if (!stale) { setError(e); setData(null) } })
      .finally(() => { if (!stale) setLoading(false) })
    return () => { stale = true }
  }, [sessionId, datasetId])

  const fmtDate = (iso: string) =>
    new Date(iso).toLocaleDateString(lang === 'es' ? 'es' : 'en', { day: 'numeric', month: 'short', year: 'numeric' })

  const agg = data?.result?.aggregate
  const chosenSku = useMemo(
    () => data?.result?.skus.find(s => s.sku === sku) ?? null, [data, sku])
  const chartSeries = chosenSku ? chosenSku.points : agg?.series ?? []
  const verdict = agg?.verdict

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ width: 36, height: 36, borderRadius: 9, background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Target size={17} color="#fff" strokeWidth={2.5} />
      </div>
      <div>
        <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.02em' }}>{t('precision.page_title')}</h1>
        <p style={{ margin: 0, fontSize: 11, color: 'var(--dim)' }}>{t('precision.page_subtitle')}</p>
      </div>
    </div>
  )

  if (sessions === null) {
    return <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>{header}<LoadingState label={t('common.loading')}><SkeletonTable rows={4} columns={4} /></LoadingState></div>
  }
  if (sessions.length === 0) {
    return <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>{header}
      <EmptyState icon={<Target size={22} />} title={t('precision.no_sessions_title')} body={t('precision.no_sessions_body')} /></div>
  }

  const selectStyle: React.CSSProperties = {
    padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)',
    background: 'var(--surface)', color: 'var(--text)', fontSize: 12, maxWidth: 280,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {header}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', fontSize: 12, color: 'var(--dim)' }}>
        <label>{t('precision.session_label')}{' '}
          <select value={sessionId ?? ''} onChange={e => { setSessionId(e.target.value); setDatasetId(undefined) }} style={selectStyle}>
            {sessions.map(s => <option key={s.session_id ?? s.id} value={s.session_id ?? s.id}>{s.name}</option>)}
          </select>
        </label>
        {data && data.candidates.length > 1 && (
          <label>{t('precision.compare_with')}{' '}
            <select value={datasetId ?? data.source?.dataset_id ?? ''} onChange={e => setDatasetId(e.target.value)} style={selectStyle}>
              {data.candidates.map(c => <option key={c.dataset_id} value={c.dataset_id}>{c.name}</option>)}
            </select>
          </label>
        )}
      </div>

      {loading && <LoadingState label={t('common.loading')}><SkeletonTable rows={4} columns={4} /></LoadingState>}
      {!loading && error != null && <InlineError error={error} />}

      {!loading && !error && data && data.status === 'no_later_upload' && (
        <EmptyState icon={<Target size={22} />} title={t('precision.empty_no_later_title')} body={t('precision.empty_no_later_body')}
                    actions={[{ label: t('precision.go_upload'), href: '/archivos' }]} />
      )}
      {!loading && !error && data && data.status === 'no_overlap' && (
        <EmptyState icon={<Target size={22} />} title={t('precision.empty_no_overlap_title')}
                    body={t('precision.empty_no_overlap_body', { from: data.forecast_from ? fmtDate(data.forecast_from) : '—', to: data.forecast_to ? fmtDate(data.forecast_to) : '—' })} />
      )}
      {!loading && !error && data && ['columns_missing', 'no_rows', 'unreadable'].includes(data.status) && (
        <EmptyState icon={<Target size={22} />} title={t('precision.empty_unreadable_title')} body={t('precision.empty_unreadable_body')} />
      )}

      {!loading && !error && data?.status === 'ok' && agg && verdict && (
        <>
          <Card padding={16} style={{ borderLeft: `4px solid ${VERDICT_COLOR[verdict.level]}` }}>
            <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 6 }}>
              {t('precision.source', { name: data.source!.name, date: fmtDate(data.source!.uploaded_at), last: fmtDate(data.source!.last_date) })}
            </div>
            <div data-testid="precision-verdict" style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', lineHeight: 1.5 }}>
              {t(`precision.verdict_${verdict.level}`, { pct: pct(verdict.wape).replace('%', ''), n: verdict.n_points })}
            </div>
            {(verdict.level === 'good' || verdict.level === 'fair' || verdict.level === 'poor') && (
              <div style={{ fontSize: 13, color: 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>
                {t(`precision.direction_${verdict.direction}`, { pct: pct(Math.abs(verdict.bias ?? 0)).replace('%', '') })}
              </div>
            )}
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <Tile label={t('precision.m_wape')} value={pct(agg.wape)} hint={t('precision.m_wape_hint')} />
            <Tile label={t('precision.m_mape')} value={pct(agg.mape)} hint={t('precision.m_mape_hint')} />
            <Tile label={t('precision.m_bias')} value={pct(agg.bias, true)} hint={t('precision.m_bias_hint')} />
            <Tile label={t('precision.m_periods')} value={fmtNum(agg.n_points)} />
            <Tile label={t('precision.m_skus')} value={fmtNum(agg.n_skus)} />
          </div>

          <Card padding={16}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                {t('precision.chart_title')} · {chosenSku ? t('precision.scope_sku', { sku: chosenSku.sku }) : t('precision.scope_all')}
              </div>
              {chosenSku && (
                <button onClick={() => setSku(null)} style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--accent)' }}>
                  {t('precision.show_all')}
                </button>
              )}
            </div>
            <CompareChart series={chartSeries} labels={{ forecast: t('precision.legend_forecast'), actual: t('precision.legend_actual') }} />
          </Card>

          <Card padding={0}>
            <div style={{ padding: '12px 16px', fontSize: 13, fontWeight: 600, color: 'var(--text)', borderBottom: '1px solid var(--border)' }}>
              {t('precision.table_title')}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--dim)', textAlign: 'right' }}>
                    <th style={{ padding: '8px 16px', textAlign: 'left' }}>{t('precision.col_sku')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.col_forecast')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.col_actual')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.m_wape')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.m_bias')}</th>
                    <th style={{ padding: '8px 16px' }}>{t('precision.m_periods')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.result!.skus.map(s => (
                    <tr key={s.sku} onClick={() => setSku(s.sku)}
                        style={{ cursor: 'pointer', textAlign: 'right', borderTop: '1px solid var(--border)', background: s.sku === sku ? 'var(--surface-2)' : undefined, fontVariantNumeric: 'tabular-nums' }}>
                      <td style={{ padding: '8px 16px', textAlign: 'left', fontWeight: 600, color: 'var(--text)' }}>{s.sku}</td>
                      <td style={{ padding: '8px 12px' }}>{fmtNum(s.total_forecast, { maximumFractionDigits: 0 })}</td>
                      <td style={{ padding: '8px 12px' }}>{fmtNum(s.total_actual, { maximumFractionDigits: 0 })}</td>
                      <td style={{ padding: '8px 12px' }}>{pct(s.wape)}</td>
                      <td style={{ padding: '8px 12px' }}>{pct(s.bias, true)}</td>
                      <td style={{ padding: '8px 16px' }}>{s.n_points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.n_skus_total != null && data.n_skus_total > data.result!.skus.length && (
              <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--dim)' }}>
                {t('precision.table_more', { shown: data.result!.skus.length, total: data.n_skus_total })}
              </div>
            )}
          </Card>

          {data.result!.skipped_points > 0 && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>
              {t('precision.skipped_note', { n: data.result!.skipped_points })}
            </div>
          )}
        </>
      )}
    </div>
  )
}

'use client'
import { Suspense, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Target } from 'lucide-react'
import BiasWords from '@/components/precision/BiasWords'
import { getForecastVsActual, getSessionLibrary, startBacktest } from '@/lib/api'
import type { ComparisonCandidate, ForecastVsActual, OverlapReading, RealizedPoint } from '@/lib/api'
import { getUser } from '@/lib/auth'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import type { SessionSummary } from '@/lib/types'
import { EmptyState, InlineError, LoadingState, SkeletonTable } from '@/components/ui/States'
import Card from '@/components/ui/Card'
import AccuracyTrackingCard from '@/components/precision/AccuracyTrackingCard'
import AdjustmentValueCard from '@/components/precision/AdjustmentValueCard'
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

const UNIT_KEY: Record<string, string> = { 'W-MON': 'weeks', MS: 'months' }

/** One plain-language line about how a file's dates sit against the forecast. */
function OverlapLine({ o, fmtDate }: { o: OverlapReading | null; fmtDate: (iso: string) => string }) {
  const { t } = useLanguage()
  if (!o || o.relation === 'unknown') return <span>{t('precision.rel_unknown')}</span>
  switch (o.relation) {
    case 'covers': return <span>{t('precision.rel_covers')}</span>
    case 'partial':
      return <span>{t('precision.rel_partial', {
        from: o.overlap_from ? fmtDate(o.overlap_from) : '—', to: o.overlap_to ? fmtDate(o.overlap_to) : '—' })}</span>
    case 'ends_before_forecast':
      return <span>{t('precision.rel_ends_before', { days: o.gap_days ?? 0 })}</span>
    default:
      return <span>{t('precision.rel_starts_after', { days: o.gap_days ?? 0 })}</span>
  }
}

const REL_COLOR: Record<string, string> = {
  covers: '#2E8B62', partial: '#B7791F', ends_before_forecast: 'var(--dim)',
  starts_after_forecast: 'var(--dim)', unknown: 'var(--dim)',
}

function RelBadge({ c }: { c: ComparisonCandidate }) {
  const { t } = useLanguage()
  const rel = c.overlap?.relation ?? 'unknown'
  const label = c.range_error && !c.overlap ? t(`precision.range_${c.range_error}`) : t(`precision.badge_${rel}`)
  return (
    <span style={{ fontSize: 11, fontWeight: 700, color: REL_COLOR[rel] ?? 'var(--dim)', whiteSpace: 'nowrap' }}>{label}</span>
  )
}

function PrecisionInner() {
  const { t, lang } = useLanguage()
  const router = useRouter()
  const params = useSearchParams()
  const planning = usePlanning()?.planning ?? null
  const canEdit = ['admin', 'analyst'].includes(getUser()?.role ?? '')
  const narrow = useIsNarrow()

  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [datasetId, setDatasetId] = useState<string | undefined>(undefined)
  const [data, setData] = useState<ForecastVsActual | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [sku, setSku] = useState<string | null>(null)
  const [showFiles, setShowFiles] = useState(false)
  const [holdout, setHoldout] = useState('4')
  const [launching, setLaunching] = useState(false)
  const [launched, setLaunched] = useState<{ session_id: string; cutoff: string; holdout_periods: number } | null>(null)

  useEffect(() => {
    // Every finished session, archived ones included: a past run is exactly
    // what somebody comes here to review.
    getSessionLibrary({ status: ['COMPLETED'], archived: 'all', limit: 500, sort: 'created_at', order: 'desc' })
      .then(r => setSessions(r.items ?? []))
      .catch(e => { setSessions([]); setError(e) })
  }, [])

  // Default to the session named in the link, else the one the app plans from.
  useEffect(() => {
    if (sessionId || !sessions?.length) return
    const wanted = params.get('session')
    const active = planning?.active_session_id
    const pick = sessions.find(s => s.session_id === wanted)
      ?? sessions.find(s => s.session_id === active) ?? sessions[0]
    setSessionId(pick.session_id)
  }, [sessions, planning, sessionId, params])

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

  const fmtDate = (iso: string) => {
    const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso)
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString(lang === 'es' ? 'es' : 'en', { day: 'numeric', month: 'short', year: 'numeric' })
  }

  const agg = data?.result?.aggregate
  const chosenSku = useMemo(
    () => data?.result?.skus.find(s => s.sku === sku) ?? null, [data, sku])
  const chartSeries = chosenSku ? chosenSku.points : agg?.series ?? []
  const verdict = agg?.verdict
  const session = sessions?.find(s => s.session_id === sessionId) ?? null
  const trainingFile = data?.candidates.find(c => c.is_training_dataset) ?? null
  const unit = t(`precision.unit_${UNIT_KEY[data?.target_freq ?? ''] ?? 'days'}`)

  const runBacktest = async () => {
    const n = parseInt(holdout, 10)
    if (!sessionId || !Number.isFinite(n) || n < 1 || launching) return
    setLaunching(true)
    try {
      const r = await startBacktest(sessionId, n, t('precision.backtest_session_name', { name: session?.name ?? '', n }))
      setLaunched({ session_id: r.session_id, cutoff: r.cutoff, holdout_periods: r.holdout_periods })
    } catch {
      // The API interceptor already explained (ceiling reached, too little data, ...).
    } finally {
      setLaunching(false)
    }
  }

  // The top bar already names the screen: only the one-line purpose here.
  const header = (
    <p style={{ margin: 0, fontSize: 12, color: 'var(--dim)' }}>{t('precision.page_subtitle')}</p>
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
    background: 'var(--surface)', color: 'var(--text)', fontSize: 12, maxWidth: '100%', minWidth: 0,
  }
  const note: React.CSSProperties = { fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.55 }
  const fileLabel = (c: ComparisonCandidate) =>
    `${c.name}${c.first_date && c.last_date ? ` · ${c.first_date} → ${c.last_date}` : ''}`

  // Why there is nothing to grade, in words — never a blank screen.
  const explain = () => {
    if (!data || data.status === 'ok') return null
    const o = data.overlap
    const src = data.source
    const fcRange = data.forecast_from && data.forecast_to
      ? t('precision.forecast_window', { from: fmtDate(data.forecast_from), to: fmtDate(data.forecast_to) }) : ''
    let title: string, body: string
    switch (data.status) {
      case 'no_later_upload':
        title = t('precision.empty_no_later_title')
        body = t('precision.empty_no_later_body', {
          file: trainingFile?.name ?? '—', last: trainingFile?.last_date ? fmtDate(trainingFile.last_date) : '—',
          window: fcRange })
        break
      case 'no_overlap':
        title = t('precision.empty_no_overlap_title')
        body = src
          ? t('precision.empty_no_overlap_named', {
              file: src.name, first: src.first_date ? fmtDate(src.first_date) : '—',
              last: src.last_date ? fmtDate(src.last_date) : '—', window: fcRange })
          : t('precision.empty_no_overlap_any', { window: fcRange })
        break
      case 'no_matching_series':
        title = t('precision.empty_no_series_title')
        body = t('precision.empty_no_series_body', { file: src?.name ?? '—' })
        break
      case 'dataset_not_found':
        title = t('precision.empty_not_found_title'); body = t('precision.empty_not_found_body'); break
      case 'no_forecast':
        title = t('precision.empty_no_forecast_title'); body = t('precision.empty_no_forecast_body'); break
      default:
        title = t('precision.empty_unreadable_title'); body = t('precision.empty_unreadable_body')
    }
    return (
      <Card padding={20} style={{ borderLeft: '4px solid var(--muted)' }}>
        <div data-testid="precision-empty" style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>{title}</div>
        <div style={note}>{body}</div>
        {o && (data.status === 'no_overlap') && (
          <div style={{ ...note, marginTop: 6 }}><OverlapLine o={o} fmtDate={fmtDate} /></div>
        )}
        <div style={{ ...note, marginTop: 10 }}>{t('precision.empty_what_next')}</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          <button className="btn-secondary" style={{ ...selectStyle, cursor: 'pointer' }} onClick={() => router.push('/archivos')}>
            {t('precision.go_upload')}
          </button>
          <button className="btn-secondary" style={{ ...selectStyle, cursor: 'pointer' }}
                  onClick={() => document.getElementById('precision-backtest')?.scrollIntoView({ behavior: 'smooth' })}>
            {t('precision.go_backtest')}
          </button>
        </div>
      </Card>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {header}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', fontSize: 12, color: 'var(--dim)' }}>
        <label style={{ minWidth: 0, maxWidth: '100%' }}>{t('precision.session_label')}{' '}
          <select value={sessionId ?? ''} onChange={e => { setSessionId(e.target.value); setDatasetId(undefined); setLaunched(null) }}
                  style={{ ...selectStyle, maxWidth: 320 }} data-testid="precision-session">
            {sessions.map(s => (
              <option key={s.session_id} value={s.session_id}>
                {s.name} · {fmtDate(s.created_at)}{s.archived_at ? ` (${t('sessions.tag_archived')})` : ''}{s.is_backtest ? ` (${t('sessions.tag_backtest')})` : ''}
              </option>
            ))}
          </select>
        </label>
        {data && data.candidates.length > 0 && (
          <label style={{ minWidth: 0, maxWidth: '100%' }}>{t('precision.compare_with')}{' '}
            <select value={datasetId ?? ''} onChange={e => setDatasetId(e.target.value || undefined)}
                    style={{ ...selectStyle, maxWidth: 360 }} data-testid="precision-dataset">
              <option value="">{t('precision.auto_pick')}</option>
              {data.candidates.map(c => <option key={c.dataset_id} value={c.dataset_id}>{fileLabel(c)}</option>)}
            </select>
          </label>
        )}
      </div>

      {sessionId && <AccuracyTrackingCard sessionId={sessionId} />}
      {sessionId && <AdjustmentValueCard sessionId={sessionId} />}

      {/* What this run forecast and what it was trained on. */}
      {data && (
        <Card padding={14}>
          <div style={note} data-testid="precision-window">
            {data.forecast_from && data.forecast_to
              ? t('precision.window_line', {
                  from: fmtDate(data.forecast_from), to: fmtDate(data.forecast_to), n: data.forecast_periods ?? 0, unit })
              : t('precision.window_none')}
            {trainingFile && (
              <> {t('precision.trained_on', {
                file: trainingFile.name,
                first: trainingFile.first_date ? fmtDate(trainingFile.first_date) : '—',
                last: trainingFile.last_date ? fmtDate(trainingFile.last_date) : '—' })}</>
            )}
          </div>
          {data.is_backtest && (
            <div style={{ ...note, marginTop: 6, color: 'var(--text)' }}>
              {t('precision.is_backtest', { n: data.backtest_holdout_periods ?? '—' })}
            </div>
          )}
        </Card>
      )}

      {loading && <LoadingState label={t('common.loading')}><SkeletonTable rows={4} columns={4} /></LoadingState>}
      {!loading && error != null && <InlineError error={error} />}

      {!loading && !error && explain()}

      {!loading && !error && data?.status === 'ok' && agg && verdict && (
        <>
          <Card padding={16} style={{ borderLeft: `4px solid ${VERDICT_COLOR[verdict.level]}` }}>
            <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 6 }}>
              {t('precision.source', { name: data.source!.name, date: fmtDate(data.source!.uploaded_at), last: data.source!.last_date ? fmtDate(data.source!.last_date) : '—' })}
              {data.overlap?.compared_periods != null && (
                <> {t('precision.coverage', { n: data.overlap.compared_periods, total: data.overlap.forecast_periods ?? 0 })}</>
              )}
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
            {chosenSku && (
              <div data-testid="precision-sku-bias" style={{ fontSize: 12.5, color: 'var(--dim)', marginBottom: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span>{t('precision.m_wape')}: <strong style={{ color: 'var(--text)' }}>{pct(chosenSku.wape)}</strong></span>
                <span>{t('precision.m_bias')}: <strong><BiasWords bias={chosenSku.bias} /></strong></span>
              </div>
            )}
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
                      <td style={{ padding: '8px 12px' }}>{pct(s.bias, true)}<div style={{ fontSize: 11 }}><BiasWords bias={s.bias} /></div></td>
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

      {/* Every file against this forecast: which dates overlap. */}
      {data && data.candidates.length > 0 && (
        <Card padding={0}>
          <button onClick={() => setShowFiles(v => !v)} aria-expanded={showFiles}
                  style={{ all: 'unset', cursor: 'pointer', display: 'block', width: '100%', boxSizing: 'border-box', padding: '12px 16px', fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
            {t('precision.files_title', { n: data.candidates.length })} {showFiles ? '▾' : '▸'}
          </button>
          {showFiles && narrow && (
            <div data-testid="precision-files" style={{ borderTop: '1px solid var(--border)', maxHeight: 420, overflowY: 'auto' }}>
              {data.candidates.map(c => (
                <div key={c.dataset_id} style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ fontWeight: 600, color: 'var(--text)', fontSize: 13, overflowWrap: 'anywhere' }}>
                    {c.name}{c.is_training_dataset ? ` · ${t('precision.files_training')}` : ''}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                    {c.first_date && c.last_date ? `${fmtDate(c.first_date)} → ${fmtDate(c.last_date)}` : '—'}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <RelBadge c={c} />
                    <button className="btn-secondary" style={{ ...selectStyle, cursor: 'pointer', minHeight: 36 }}
                            onClick={() => setDatasetId(c.dataset_id)}>{t('precision.files_compare')}</button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {showFiles && !narrow && (
            <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto', borderTop: '1px solid var(--border)' }}>
              <table data-testid="precision-files" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--dim)', textAlign: 'left' }}>
                    <th style={{ padding: '8px 16px' }}>{t('precision.files_col_file')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.files_col_dates')}</th>
                    <th style={{ padding: '8px 12px' }}>{t('precision.files_col_overlap')}</th>
                    <th style={{ padding: '8px 12px' }} />
                  </tr>
                </thead>
                <tbody>
                  {data.candidates.map(c => (
                    <tr key={c.dataset_id} style={{ borderTop: '1px solid var(--border)', background: (datasetId ?? data.source?.dataset_id) === c.dataset_id ? 'var(--surface-2)' : undefined }}>
                      <td style={{ padding: '8px 16px', fontWeight: 600, color: 'var(--text)' }}>
                        {c.name}
                        {c.is_training_dataset && <span style={{ fontWeight: 400, color: 'var(--dim)' }}> · {t('precision.files_training')}</span>}
                      </td>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap', color: 'var(--muted)' }}>
                        {c.first_date && c.last_date ? `${fmtDate(c.first_date)} → ${fmtDate(c.last_date)}` : '—'}
                      </td>
                      <td style={{ padding: '8px 12px' }}><RelBadge c={c} /></td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                        <button className="btn-secondary" style={{ ...selectStyle, cursor: 'pointer' }}
                                onClick={() => setDatasetId(c.dataset_id)}>{t('precision.files_compare')}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Back-test: make the missing overlap from history the user already has. */}
      <Card padding={16}>
        <div id="precision-backtest" style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>{t('precision.backtest_title')}</div>
        <div style={note}>{t('precision.backtest_body')}</div>
        {launched ? (
          <div data-testid="precision-backtest-started" style={{ ...note, marginTop: 10, color: 'var(--text)' }}>
            {t('precision.backtest_started', { n: launched.holdout_periods, unit, date: fmtDate(launched.cutoff) })}
            {' '}
            <button onClick={() => router.push('/historial')} style={{ all: 'unset', cursor: 'pointer', color: 'var(--accent)' }}>
              {t('precision.backtest_see_history')}
            </button>
          </div>
        ) : canEdit ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
            <label style={{ fontSize: 12, color: 'var(--dim)' }}>{t('precision.backtest_hold_out')}{' '}
              <input type="number" min={1} max={90} value={holdout} onChange={e => setHoldout(e.target.value)}
                     data-testid="precision-holdout" style={{ ...selectStyle, width: 70 }} /> {unit}
            </label>
            <button className="btn-primary" disabled={launching || !(parseInt(holdout, 10) >= 1)} onClick={runBacktest}
                    data-testid="precision-run-backtest"
                    style={{ ...selectStyle, background: 'var(--accent)', color: '#fff', border: 'none', cursor: launching ? 'wait' : 'pointer', fontWeight: 600 }}>
              {launching ? t('precision.backtest_starting') : t('precision.backtest_run')}
            </button>
          </div>
        ) : (
          <div style={{ ...note, marginTop: 8 }}>{t('precision.backtest_viewer')}</div>
        )}
      </Card>
    </div>
  )
}

export default function PrecisionPage() {
  return (
    <Suspense fallback={null}>
      <PrecisionInner />
    </Suspense>
  )
}

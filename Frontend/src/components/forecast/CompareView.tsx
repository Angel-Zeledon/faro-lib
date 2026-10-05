'use client'
import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { X, Plus, Eye } from 'lucide-react'
import { getSkuIntelligence, getForecastTotal } from '@/lib/api'
import type { SessionInfo, SkuIntelligenceData, ForecastTotalData } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { granularityLabel } from '@/lib/enumLabels'
import { localeFor } from '@/lib/numberLocale'
import { modelLabel } from '@/lib/modelLabel'
import { fmtK, chartChrome } from './shared'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── Session comparison ────────────────────────────────────────────────────────
//
// ONE chart on ONE time axis for every session being compared. The earlier
// version stacked two full single-session panels (toolbar + stat tiles + chart
// + footer each, ~550px) inside a fixed-height, overflow-hidden half of the
// screen: each chart was squeezed, its time axis cut off, and the two charts
// had independent axes and granularities so the same date never lined up.
// Here the sessions are series of a single chart, so a date means one thing.

/** Sentinel for the "all SKUs" scope. Never a real SKU id. */
const TOTAL = '__total__'

/** Paul Tol's "bright" qualitative scheme: designed to stay distinguishable
 *  under the common colour-vision deficiencies, calm rather than saturated, and
 *  legible on both the light and the dark surface. */
export const COMPARE_PALETTE = [
  '#4477AA', // blue
  '#EE6677', // rose
  '#228833', // green
  '#CCBB44', // sand
  '#66CCEE', // cyan
  '#AA3377', // purple
]
/** A second, non-colour cue: each slot also gets its own line pattern. */
const LINE_STYLES: ('solid' | 'dashed' | 'dotted')[] = ['solid', 'dashed', 'dotted', 'solid', 'dashed', 'dotted']
export const MAX_COMPARED = COMPARE_PALETTE.length

const GRAIN_ORDER = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly']
const ACTUALS = '__actuals__'

type Slot = { data?: SkuIntelligenceData; failed?: boolean; loading: boolean }

/** 'YYYY-MM-DD…' as LOCAL midnight. `Date.parse` would read it as UTC and the
 *  time axis (drawn in local time) would label every point a day early in the
 *  Americas. */
function toTs(s: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s)
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime()
  const v = Date.parse(s)
  return Number.isNaN(v) ? NaN : v
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}

interface Pt { ts: number; v: number }

function toPoints(raw: { date: string; value: number }[] | undefined): Pt[] {
  const out: Pt[] = []
  for (const p of raw ?? []) {
    const ts = toTs(p.date)
    if (Number.isFinite(ts) && typeof p.value === 'number' && Number.isFinite(p.value)) out.push({ ts, v: p.value })
  }
  return out.sort((a, b) => a.ts - b.ts)
}

/** The value at `ts`, or undefined when the series has no point within `tol`. */
function valueNear(pts: Pt[], ts: number, tol: number): number | undefined {
  let lo = 0, hi = pts.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (pts[mid].ts < ts) lo = mid + 1; else hi = mid
  }
  let best: Pt | undefined
  for (const i of [lo - 1, lo]) {
    const p = pts[i]
    if (p && Math.abs(p.ts - ts) <= tol && (!best || Math.abs(p.ts - ts) < Math.abs(best.ts - ts))) best = p
  }
  return best?.v
}

function medianStep(pts: Pt[]): number {
  if (pts.length < 2) return 0
  const steps: number[] = []
  const stride = Math.max(1, Math.floor(pts.length / 200))
  for (let i = stride; i < pts.length; i += stride) steps.push(pts[i].ts - pts[i - stride].ts)
  steps.sort((a, b) => a - b)
  return steps[steps.length >> 1] / stride
}

function accuracyOf(d: SkuIntelligenceData, total: boolean): number | null {
  let wape: number | null | undefined
  if (total) wape = (d as ForecastTotalData).accuracy_wape
  else wape = d.metrics.find(r => r.model === d.model && r.sku === d.sku)?.wape
  // Same guards as the single-SKU accuracy on the page: an undefined or absurd
  // WAPE is "no figure", not a confident 0%.
  if (wape == null || !Number.isFinite(wape) || wape >= 1e6) return null
  return Math.max(0, Math.round((1 - wape) * 100))
}

export interface CompareViewProps {
  sessions: SessionInfo[]
  primaryId: string
  /** Sessions compared against the primary, in the order added. */
  extraIds: string[]
  onExtraIds: (ids: string[]) => void
  skus: string[]
  sku: string | null
  onSku: (sku: string) => void
  isDark: boolean
}

export default function CompareView({
  sessions, primaryId, extraIds, onExtraIds, skus, sku, onSku, isDark,
}: CompareViewProps) {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const locale = localeFor(lang)
  const chrome = chartChrome(isDark)

  const [total, setTotal] = useState(false)
  // A SKU picked from the page's list leaves the "all SKUs" scope.
  useEffect(() => { setTotal(false) }, [sku])
  const [userGran, setUserGran] = useState<string | null>(null)
  const [fullRange, setFullRange] = useState(false)
  const [retryNonce, setRetryNonce] = useState(0)
  // Visibility survives a SKU switch on purpose: it is a choice about WHICH
  // UPDATES to look at, not about the product.
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [slots, setSlots] = useState<Record<string, Slot>>({})
  const [slotGran, setSlotGran] = useState<string | null>(null)
  const cache = useRef(new Map<string, SkuIntelligenceData>())

  const ids = useMemo(() => [primaryId, ...extraIds.filter(i => i !== primaryId)].slice(0, MAX_COMPARED), [primaryId, extraIds])
  const scopeSku = total ? TOTAL : sku

  // Colour slots are sticky: removing the second session must not repaint the
  // third, or the legend the user just learned stops being true.
  const slotOf = useRef(new Map<string, number>())
  for (const id of Array.from(slotOf.current.keys())) if (!ids.includes(id)) slotOf.current.delete(id)
  for (const id of ids) {
    if (!slotOf.current.has(id)) {
      const used = new Set(slotOf.current.values())
      let n = 0
      while (used.has(n)) n++
      slotOf.current.set(id, n)
    }
  }
  const colorOf = (id: string) => COMPARE_PALETTE[(slotOf.current.get(id) ?? 0) % COMPARE_PALETTE.length]
  const styleOf = (id: string) => LINE_STYLES[(slotOf.current.get(id) ?? 0) % LINE_STYLES.length]
  const nameOf = useCallback((id: string) => sessions.find(s => s.session_id === id)?.name ?? id, [sessions])

  // ── Data ────────────────────────────────────────────────────────────────────
  const idsKey = ids.join('|')
  useEffect(() => {
    if (!scopeSku) { setSlots({}); return }
    let cancelled = false
    const fetchOne = async (id: string, gran?: string): Promise<SkuIntelligenceData> => {
      const key = `${id}|${scopeSku}|${gran ?? ''}`
      const hit = cache.current.get(key)
      if (hit) return hit
      const d = scopeSku === TOTAL
        ? await getForecastTotal(id, { granularity: gran }, { silent: true })
        : await getSkuIntelligence(id, scopeSku, { granularity: gran, agg: 'sum' }, { silent: true })
      cache.current.set(key, d)
      cache.current.set(`${id}|${scopeSku}|${d.applied_granularity}`, d)
      return d
    }
    setSlots(prev => {
      const next: Record<string, Slot> = {}
      for (const id of ids) next[id] = { data: prev[id]?.data, loading: true }
      return next
    })
    ;(async () => {
      // Pass 1: each session at its own (or the chosen) granularity.
      const first = await Promise.all(ids.map(id =>
        fetchOne(id, userGran ?? undefined).then(d => ({ id, d }), () => ({ id, d: undefined }))))
      if (cancelled) return
      // Pass 2: every session expressed at the COARSEST grain among them, so a
      // daily and a weekly update share an axis without one being 7x the other.
      let target = userGran
      if (!target) {
        const applied = first.filter(r => r.d).map(r => r.d!.applied_granularity)
        target = applied.sort((a, b) => GRAIN_ORDER.indexOf(b) - GRAIN_ORDER.indexOf(a))[0] ?? null
      }
      const second = await Promise.all(first.map(async r => {
        if (!r.d) return r
        if (!target || r.d.applied_granularity === target || !r.d.available_granularities.includes(target)) return r
        try { return { id: r.id, d: await fetchOne(r.id, target) } } catch { return r }
      }))
      if (cancelled) return
      const next: Record<string, Slot> = {}
      for (const r of second) next[r.id] = r.d ? { data: r.d, loading: false } : { failed: true, loading: false }
      setSlots(next)
      setSlotGran(target)
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, scopeSku, userGran, retryNonce])

  const loaded = ids.map(id => ({ id, slot: slots[id] }))
  const anyLoading = loaded.some(r => !r.slot || r.slot.loading)
  const failedIds = loaded.filter(r => r.slot?.failed).map(r => r.id)
  const grainOptions = useMemo(() => {
    const lists = loaded.filter(r => r.slot?.data).map(r => r.slot!.data!.available_granularities)
    if (!lists.length) return []
    return GRAIN_ORDER.filter(g => lists.every(l => l.includes(g)))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slots, idsKey])
  const effectiveGran = userGran ?? slotGran

  // ── Series model ────────────────────────────────────────────────────────────
  const model = useMemo(() => {
    const rows = ids.map(id => {
      const d = slots[id]?.data
      const fc = toPoints(d?.forecast)
      return { id, d, fc, hist: toPoints(d?.historical) }
    })
    // Actuals: the first session that has history wins a date; the others only
    // fill dates it lacks, so sessions trained on different cut-offs still give
    // one continuous line.
    const byTs = new Map<number, number>()
    for (const r of rows) for (const p of r.hist) if (!byTs.has(p.ts)) byTs.set(p.ts, p.v)
    const actuals = Array.from(byTs, ([ts, v]) => ({ ts, v })).sort((a, b) => a.ts - b.ts)
    return { rows, actuals }
  }, [slots, ids])

  const visibleRows = model.rows.filter(r => r.fc.length > 0 && !hidden.has(r.id))
  const showActuals = model.actuals.length > 0 && !hidden.has(ACTUALS)

  // Do the visible forecasts share any dates? Disjoint windows draw fine on the
  // shared axis, but the user asked to compare — so say why nothing meets.
  const noOverlap = useMemo(() => {
    if (visibleRows.length < 2) return false
    const start = Math.max(...visibleRows.map(r => r.fc[0].ts))
    const end = Math.min(...visibleRows.map(r => r.fc[r.fc.length - 1].ts))
    return start > end
  }, [visibleRows])

  const fmtDate = useCallback((ts: number, withDay = true) => {
    const d = new Date(ts)
    if (effectiveGran === 'yearly') return String(d.getFullYear())
    if (effectiveGran === 'monthly' || effectiveGran === 'quarterly') {
      return d.toLocaleDateString(locale, { month: 'short', year: 'numeric' })
    }
    return d.toLocaleDateString(locale, withDay ? { day: 'numeric', month: 'short', year: 'numeric' } : { day: 'numeric', month: 'short' })
  }, [effectiveGran, locale])

  /** The window the user dragged to, in ABSOLUTE time: percentages would point
   *  somewhere else the moment hiding a series changes the data's extent. */
  const zoom = useRef<{ from: number; to: number } | null>(null)
  useEffect(() => { zoom.current = null }, [scopeSku, idsKey, fullRange])

  const option = useMemo(() => {
    const totalPts = model.rows.reduce((n, r) => n + r.fc.length, 0) + model.actuals.length
    const heavy = totalPts > 3000
    const allTs = [
      ...(showActuals ? model.actuals.map(p => p.ts) : []),
      ...visibleRows.flatMap(r => [r.fc[0].ts, r.fc[r.fc.length - 1].ts]),
    ]
    const minTs = allTs.length ? Math.min(...allTs) : 0
    const maxTs = allTs.length ? Math.max(...allTs) : 1
    const starts = Array.from(new Set(visibleRows.map(r => r.fc[0].ts))).sort((a, b) => a - b)
    // Stateless on purpose (the user zooms without re-rendering this): a tick on
    // the 1st carries the month and year, any other tick the day and month, so
    // two ticks in the same month can never read as the same label.
    const axisDate = (v: number) => {
      if (effectiveGran !== 'daily' && effectiveGran !== 'weekly') return fmtDate(v, false)
      const d = new Date(v)
      return d.getDate() === 1
        ? d.toLocaleDateString(locale, { month: 'short', year: '2-digit' })
        : d.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
    }

    // Default window: the forecast with about twice its own span of history
    // before it, so a five-year daily history does not shrink a 12-week
    // forecast to a few pixels. "Full history" lifts it.
    let fromTs = minTs
    if (!fullRange && starts.length && maxTs > minTs) {
      const lastEnd = Math.max(...visibleRows.map(r => r.fc[r.fc.length - 1].ts))
      const span = Math.max(lastEnd - starts[0], 1)
      fromTs = Math.max(minTs, starts[0] - span * 2)
    }
    const kept = zoom.current && zoom.current.to >= minTs && zoom.current.from <= maxTs ? zoom.current : null
    const z = kept ?? { from: fromTs, to: maxTs }

    const lookups = [
      ...visibleRows.map(r => ({
        key: r.id, label: nameOf(r.id), color: colorOf(r.id), pts: r.fc, tol: medianStep(r.fc) / 2 + 1,
      })),
      ...(showActuals ? [{
        key: ACTUALS, label: t('compare.actuals'), color: chrome.dim, pts: model.actuals,
        tol: medianStep(model.actuals) / 2 + 1,
      }] : []),
    ]

    const lineSeries = visibleRows.map(r => ({
      id: r.id,
      name: nameOf(r.id),
      type: 'line' as const,
      data: r.fc.map(p => [p.ts, p.v]),
      showSymbol: r.fc.length <= 60,
      symbolSize: 5,
      sampling: 'lttb' as const,
      lineStyle: { width: 2.2, color: colorOf(r.id), type: styleOf(r.id) },
      itemStyle: { color: colorOf(r.id) },
      emphasis: { focus: 'series' as const, lineStyle: { width: 3 } },
      z: 5,
    }))

    return {
      backgroundColor: 'transparent',
      animation: !heavy,
      textStyle: { fontFamily: 'inherit' },
      grid: { left: narrow ? 8 : 12, right: narrow ? 12 : 20, top: 30, bottom: narrow ? 8 : 44, containLabel: true },
      tooltip: {
        trigger: 'axis' as const,
        confine: true,
        backgroundColor: chrome.tooltipBg,
        borderColor: chrome.tooltipBdr,
        textStyle: { color: chrome.tooltipText, fontSize: narrow ? 11 : 12 },
        extraCssText: 'max-width: min(86vw, 340px); white-space: normal;',
        axisPointer: { type: 'line' as const, lineStyle: { color: chrome.dim, type: 'dashed' as const } },
        formatter: (params: any) => {
          const first = Array.isArray(params) ? params[0] : params
          const ts = Number(first?.axisValue)
          if (!Number.isFinite(ts)) return ''
          const rowsHtml = lookups.map(l => {
            const v = valueNear(l.pts, ts, l.tol)
            return `<div style="display:flex;align-items:center;gap:6px;margin-top:3px">`
              + `<span style="width:9px;height:9px;border-radius:2px;background:${l.color};flex:none"></span>`
              + `<span style="flex:1;min-width:0;overflow-wrap:anywhere">${esc(l.label)}</span>`
              + `<b style="margin-left:8px">${v == null ? '—' : esc(fmtK(v))}</b></div>`
          }).join('')
          return `<div style="font-weight:600">${esc(fmtDate(ts))}</div>${rowsHtml}`
        },
      },
      xAxis: {
        type: 'time' as const,
        splitNumber: narrow ? 3 : 6,
        axisLine: { lineStyle: { color: chrome.gridLine } },
        axisTick: { show: false },
        axisLabel: {
          color: chrome.dim, fontSize: 10, hideOverlap: true,
          formatter: (v: number) => axisDate(v),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value' as const,
        // Not anchored at zero: the sessions are compared by how they differ,
        // which a 0-based axis flattens whenever demand sits far from zero.
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: chrome.dim, fontSize: 10, formatter: (v: number) => fmtK(v) },
        splitLine: { lineStyle: { color: chrome.gridLine, type: 'dashed' as const } },
      },
      dataZoom: [
        { type: 'inside' as const, xAxisIndex: 0, startValue: z.from, endValue: z.to, zoomOnMouseWheel: true,
          moveOnMouseMove: !narrow, preventDefaultMouseMove: false },
        ...(narrow ? [] : [{
          type: 'slider' as const, xAxisIndex: 0, startValue: z.from, endValue: z.to, height: 18, bottom: 6,
          borderColor: chrome.gridLine, textStyle: { color: chrome.dim, fontSize: 10 },
          labelFormatter: (v: number) => fmtDate(v),
        }]),
      ],
      series: [
        // Vertical marker(s) where each visible forecast begins. Its own empty
        // series, so it survives toggling any one session off.
        {
          id: '__marker__', type: 'line' as const, data: [], silent: true, tooltip: { show: false },
          markLine: {
            symbol: 'none', silent: true, animation: false,
            lineStyle: { color: chrome.dim, type: 'dashed' as const, width: 1 },
            label: { color: chrome.dim, fontSize: 10, formatter: t('compare.forecast_start'), position: 'end' as const, rotate: 0, align: 'left' as const },
            data: starts.map((s, i) => ({
              xAxis: s,
              label: { show: i === 0 },
              lineStyle: starts.length > 1 && visibleRows.length
                ? { color: colorOf(visibleRows.find(r => r.fc[0].ts === s)!.id), type: 'dashed' as const, width: 1 }
                : undefined,
            })),
          },
        },
        ...(showActuals ? [{
          id: ACTUALS,
          name: t('compare.actuals'),
          type: 'line' as const,
          data: model.actuals.map(p => [p.ts, p.v]),
          showSymbol: false,
          sampling: 'lttb' as const,
          lineStyle: { width: 1.4, color: chrome.dim, opacity: 0.9 },
          itemStyle: { color: chrome.dim },
          z: 2,
        }] : []),
        ...lineSeries,
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, hidden, visibleRows.length, showActuals, fullRange, narrow, isDark, effectiveGran, lang, t, nameOf])

  const onEvents = useMemo(() => ({
    datazoom: (_e: unknown, chart: any) => {
      const dz = chart?.getOption?.()?.dataZoom?.[0]
      if (typeof dz?.startValue === 'number' && typeof dz?.endValue === 'number') {
        zoom.current = { from: dz.startValue, to: dz.endValue }
      }
    },
  }), [])

  // ── Legend actions ──────────────────────────────────────────────────────────
  const legendKeys = [...ids.filter(id => (slots[id]?.data?.forecast.length ?? 0) > 0), ...(model.actuals.length ? [ACTUALS] : [])]
  const toggle = (k: string) => setHidden(prev => {
    const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n
  })
  const showAll = () => setHidden(new Set())
  const only = (k: string) => setHidden(new Set(legendKeys.filter(x => x !== k)))

  // ── Session picker ──────────────────────────────────────────────────────────
  const trained = sessions.filter(s => s.status === 'COMPLETED')
  const addable = trained.filter(s => !ids.includes(s.session_id))
  const atMax = ids.length >= MAX_COMPARED

  // ── Render ──────────────────────────────────────────────────────────────────
  const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10 }
  const label: React.CSSProperties = { fontSize: narrow ? 12 : 11, color: 'var(--dim)' }
  const ctl: React.CSSProperties = { fontSize: narrow ? 16 : 12, minHeight: narrow ? 44 : 30, minWidth: 0 }
  const chartHeight = narrow ? 300 : 380
  const empty = visibleRows.length === 0 && !showActuals
  const scopeLabel = total ? t('compare.all_skus') : sku ?? ''

  const rangeText = (pts: Pt[]) => pts.length
    ? `${fmtDate(pts[0].ts)} – ${fmtDate(pts[pts.length - 1].ts)}`
    : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: narrow ? 12 : 16, minWidth: 0 }} data-tour="skus.compare_view">
      {/* Scope: which updates, which product, which grain */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: narrow ? '1 1 150px' : '0 1 260px', minWidth: 0 }}>
          <span style={label}>{t('compare.sku_label')}</span>
          <select
            className="form-select"
            aria-label={t('compare.sku_label')}
            style={ctl}
            value={total ? TOTAL : (sku ?? '')}
            onChange={e => {
              if (e.target.value === TOTAL) setTotal(true)
              else { setTotal(false); onSku(e.target.value) }
            }}
          >
            <option value={TOTAL}>{t('compare.all_skus')}</option>
            {sku && !skus.includes(sku) && <option value={sku}>{sku}</option>}
            {skus.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>

        {grainOptions.length > 1 && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: narrow ? '1 1 110px' : '0 1 150px', minWidth: 0 }}>
            <span style={label}>{t('compare.granularity_label')}</span>
            <select
              className="form-select"
              aria-label={t('compare.granularity_label')}
              style={ctl}
              value={effectiveGran ?? ''}
              onChange={e => setUserGran(e.target.value)}
            >
              {grainOptions.map(g => <option key={g} value={g}>{granularityLabel(t, g)}</option>)}
            </select>
          </label>
        )}

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: narrow ? '1 1 100%' : '1 1 240px', minWidth: 0 }}>
          <span style={label}>{t('compare.add_session_label')}</span>
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <Plus size={14} aria-hidden="true" style={{ color: 'var(--dim)', flexShrink: 0 }} />
            <select
              className="form-select"
              aria-label={t('compare.add_session_label')}
              style={{ ...ctl, flex: 1 }}
              value=""
              disabled={atMax || addable.length === 0}
              onChange={e => { if (e.target.value) onExtraIds([...extraIds, e.target.value]) }}
            >
              <option value="">
                {atMax ? t('compare.max_reached', { n: MAX_COMPARED })
                  : addable.length === 0 ? t('compare.no_more_sessions')
                  : t('compare.add_session_placeholder')}
              </option>
              {addable.map(s => <option key={s.session_id} value={s.session_id}>{s.name}</option>)}
            </select>
          </span>
        </label>
      </div>

      {ids.length < 2 && (
        <div role="status" style={{ ...card, padding: '10px 14px', fontSize: 13, color: 'var(--muted)', overflowWrap: 'anywhere' }}>
          {addable.length > 0 ? t('compare.pick_another') : t('compare.need_two_sessions')}
        </div>
      )}

      {/* Legend: a real button per series, so it works from the keyboard */}
      <div role="group" aria-label={t('compare.legend_aria')} style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {legendKeys.map(k => {
          const isActuals = k === ACTUALS
          const on = !hidden.has(k)
          const color = isActuals ? chrome.dim : colorOf(k)
          const name = isActuals ? t('compare.actuals') : nameOf(k)
          return (
            <span key={k} style={{
              display: 'inline-flex', alignItems: 'stretch', maxWidth: '100%',
              border: `1px solid ${on ? color : 'var(--border)'}`, borderRadius: 8, overflow: 'hidden',
              background: on ? 'var(--surface)' : 'transparent',
            }}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => toggle(k)}
                title={on ? t('compare.hide_series', { name }) : t('compare.show_series', { name })}
                style={{
                  all: 'unset', cursor: 'pointer', boxSizing: 'border-box',
                  display: 'inline-flex', alignItems: 'center', gap: 7, minWidth: 0,
                  padding: narrow ? '0 10px' : '4px 9px', minHeight: narrow ? 44 : 28,
                  fontSize: narrow ? 13 : 12, color: on ? 'var(--text)' : 'var(--dim)',
                  textDecoration: on ? 'none' : 'line-through',
                }}
              >
                <span aria-hidden="true" style={{
                  width: 16, height: 0, flexShrink: 0, borderTop: `3px ${isActuals ? 'solid' : styleOf(k)} ${on ? color : 'var(--border)'}`,
                }} />
                <span style={{ overflowWrap: 'anywhere', textAlign: 'left' }}>{name}</span>
              </button>
              {legendKeys.length > 1 && (
                <button
                  type="button"
                  onClick={() => only(k)}
                  title={t('compare.only_title', { name })}
                  aria-label={t('compare.only_title', { name })}
                  style={{
                    all: 'unset', cursor: 'pointer', boxSizing: 'border-box', borderLeft: '1px solid var(--border)',
                    padding: narrow ? '0 10px' : '0 8px', minHeight: narrow ? 44 : 28, display: 'inline-flex', alignItems: 'center',
                    fontSize: narrow ? 12 : 11, color: 'var(--dim)',
                  }}
                >
                  {t('compare.only')}
                </button>
              )}
            </span>
          )
        })}
        {legendKeys.length > 1 && (
          <button
            type="button"
            onClick={showAll}
            disabled={hidden.size === 0}
            style={{
              all: 'unset', cursor: hidden.size === 0 ? 'default' : 'pointer', boxSizing: 'border-box',
              display: 'inline-flex', alignItems: 'center', gap: 5, padding: narrow ? '0 10px' : '4px 9px',
              minHeight: narrow ? 44 : 28, fontSize: narrow ? 13 : 12,
              color: hidden.size === 0 ? 'var(--dim)' : 'var(--accent)', opacity: hidden.size === 0 ? 0.55 : 1,
            }}
          >
            <Eye size={13} aria-hidden="true" /> {t('compare.show_all')}
          </button>
        )}
      </div>

      {failedIds.length > 0 && (
        <div role="alert" style={{
          ...card, padding: '10px 14px', fontSize: 13, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center',
          borderColor: 'var(--signal-order-now-fg, #D07878)', color: 'var(--text)',
        }}>
          <span style={{ flex: '1 1 220px', minWidth: 0, overflowWrap: 'anywhere' }}>
            {t('compare.load_failed', { names: failedIds.map(nameOf).join(', ') })}
          </span>
          <button type="button" className="btn btn-secondary" style={{ minHeight: narrow ? 44 : undefined }}
            onClick={() => { cache.current.clear(); setRetryNonce(n => n + 1) }}>
            {t('compare.retry')}
          </button>
        </div>
      )}

      {noOverlap && (
        <div role="status" style={{ ...card, padding: '10px 14px', fontSize: 13, color: 'var(--muted)', overflowWrap: 'anywhere' }}>
          {t('compare.no_overlap')}
        </div>
      )}

      {/* Chart */}
      <div style={{ ...card, padding: narrow ? '10px 6px 6px' : '12px 12px 8px', position: 'relative', minWidth: 0 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8, padding: '0 6px 6px', alignItems: 'center' }}>
          <div style={{ fontSize: narrow ? 13 : 12, fontWeight: 600, overflowWrap: 'anywhere', minWidth: 0 }}>{scopeLabel}</div>
          <button
            type="button"
            aria-pressed={fullRange}
            onClick={() => setFullRange(v => !v)}
            style={{
              all: 'unset', cursor: 'pointer', boxSizing: 'border-box', fontSize: narrow ? 13 : 11,
              color: 'var(--accent)', padding: narrow ? '0 8px' : '2px 6px', minHeight: narrow ? 44 : undefined,
              display: 'inline-flex', alignItems: 'center',
            }}
          >
            {fullRange ? t('compare.focus_forecast') : t('compare.full_history')}
          </button>
        </div>

        {scopeSku && anyLoading && !loaded.some(r => r.slot?.data) ? (
          <div role="status" aria-busy="true" style={{ height: chartHeight }}>
            <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 8 }}>{t('compare.loading')}</div>
            <div className="skeleton" style={{ height: chartHeight - 30, borderRadius: 8 }} />
          </div>
        ) : empty ? (
          <div role="status" style={{
            height: chartHeight, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center',
            padding: 20, color: 'var(--dim)', fontSize: 13,
          }}>
            {loaded.some(r => r.slot?.data) && legendKeys.length > 0 && legendKeys.every(k => hidden.has(k))
              ? t('compare.all_hidden')
              : t('compare.no_series_for_scope')}
          </div>
        ) : (
          <div role="img" aria-label={t('compare.chart_aria', { scope: scopeLabel })} style={{ opacity: anyLoading ? 0.55 : 1, transition: 'opacity 0.15s' }}>
            <ReactECharts
              option={option}
              style={{ height: chartHeight, width: '100%' }}
              theme={isDark ? 'dark' : undefined}
              opts={{ renderer: 'canvas' }}
              onEvents={onEvents}
              notMerge
            />
          </div>
        )}
      </div>

      {/* Per-session summary. Rows carry the series colour. */}
      <div style={{ ...card, overflow: 'hidden' }}>
        {!narrow && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(0,1.2fr) minmax(0,1.6fr) minmax(0,0.8fr)',
            gap: 12, padding: '8px 14px', fontSize: 11, color: 'var(--dim)', fontWeight: 600,
            borderBottom: '1px solid var(--border)',
          }}>
            <span>{t('compare.col_session')}</span>
            <span>{t('compare.col_model')}</span>
            <span>{t('compare.col_horizon')}</span>
            <span title={t('compare.accuracy_hint')}>{t('compare.col_accuracy')}</span>
          </div>
        )}
        {ids.map((id, i) => {
          const slot = slots[id]
          const d = slot?.data
          const color = colorOf(id)
          const fc = toPoints(d?.forecast)
          const acc = d ? accuracyOf(d, total) : null
          const isHidden = hidden.has(id)
          const modelText = total ? t('compare.model_best_per_sku') : modelLabel(t, d?.model ?? null)
          let horizon: React.ReactNode = '—'
          if (!slot || slot.loading) horizon = t('compare.loading_short')
          else if (slot.failed) horizon = <span style={{ color: 'var(--signal-order-now-fg, #D07878)' }}>{t('compare.row_failed')}</span>
          else if (fc.length === 0) horizon = t('compare.row_no_forecast')
          else horizon = (
            <>
              <div>{t('compare.horizon_value', { n: fc.length, grain: granularityLabel(t, d!.applied_granularity).toLowerCase() })}</div>
              <div style={{ color: 'var(--dim)', fontSize: 11 }}>{rangeText(fc)}</div>
            </>
          )
          const accText = acc == null ? '—' : `${acc}%`
          const nameCell = (
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
              <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 2, background: color, flexShrink: 0 }} />
              <span style={{ overflowWrap: 'anywhere', fontWeight: 600 }}>
                {nameOf(id)}
                {i === 0 && <span style={{ fontWeight: 400, color: 'var(--dim)' }}> · {t('compare.primary_tag')}</span>}
              </span>
            </span>
          )
          const rowStyle: React.CSSProperties = {
            borderLeft: `4px solid ${color}`,
            background: `color-mix(in srgb, ${color} 7%, transparent)`,
            borderTop: i === 0 && narrow ? 'none' : '1px solid var(--border)',
            opacity: isHidden ? 0.55 : 1, fontSize: narrow ? 13 : 12, minWidth: 0,
          }
          if (narrow) {
            return (
              <div key={id} style={{ ...rowStyle, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {nameCell}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px' }}>
                  <div><div style={label}>{t('compare.col_model')}</div><div style={{ overflowWrap: 'anywhere' }}>{modelText}</div></div>
                  <div><div style={label}>{t('compare.col_accuracy')}</div><div>{accText}</div></div>
                  <div style={{ gridColumn: '1 / -1' }}><div style={label}>{t('compare.col_horizon')}</div><div style={{ overflowWrap: 'anywhere' }}>{horizon}</div></div>
                </div>
              </div>
            )
          }
          return (
            <div key={id} style={{
              ...rowStyle, display: 'grid', alignItems: 'start',
              gridTemplateColumns: 'minmax(0,2fr) minmax(0,1.2fr) minmax(0,1.6fr) minmax(0,0.8fr)', gap: 12, padding: '9px 14px',
            }}>
              {nameCell}
              <span style={{ overflowWrap: 'anywhere' }}>{modelText}</span>
              <span style={{ overflowWrap: 'anywhere' }}>{horizon}</span>
              <span style={{ fontWeight: 600 }}>{accText}</span>
            </div>
          )
        })}
        <div style={{ padding: '8px 14px', fontSize: 11, color: 'var(--dim)', borderTop: '1px solid var(--border)', overflowWrap: 'anywhere' }}>
          {t('compare.accuracy_hint')}
        </div>
      </div>

      {/* Chips to drop a session: the primary is the screen's own session */}
      {extraIds.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }} aria-label={t('compare.remove_group_aria')} role="group">
          {extraIds.map(id => (
            <button
              key={id}
              type="button"
              onClick={() => { onExtraIds(extraIds.filter(x => x !== id)); setHidden(prev => { const n = new Set(prev); n.delete(id); return n }) }}
              title={t('compare.remove_session', { name: nameOf(id) })}
              style={{
                all: 'unset', cursor: 'pointer', boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: narrow ? '0 12px' : '3px 9px', minHeight: narrow ? 44 : 26, border: '1px solid var(--border)', borderRadius: 999,
                fontSize: narrow ? 13 : 11, color: 'var(--dim)', maxWidth: '100%',
              }}
            >
              <X size={12} aria-hidden="true" />
              <span style={{ overflowWrap: 'anywhere', textAlign: 'left' }}>{t('compare.remove_session', { name: nameOf(id) })}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

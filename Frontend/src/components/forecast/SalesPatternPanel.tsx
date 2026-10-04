'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { getSkuIntelligence, getSkuDecomposition, ApiError } from '@/lib/api'
import type { SkuIntelligenceData, DecompositionData, DecompositionPoint } from '@/lib/types'
import { ErrorState } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { ChipGroup } from './ChipGroup'
import { PanelPlaceholder } from './PanelChrome'
import { type Translate, fmtK, useCssToken, chartChrome } from './shared'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── Sales pattern ─────────────────────────────────────────────────────────────
//
// Two views of the user's OWN sales history. No model is involved and nothing
// here is a prediction — the copy says so, because a bar chart sitting one tab
// away from a forecast will otherwise be read as one.
//
// Both views live on their own tab rather than under the forecast chart: the
// forecast needs the height it has, and two large charts with room to breathe
// get read where four squeezed ones get ignored.

/** Monday-first — the week a distributor actually plans against. */
export const WEEKDAY_KEYS = [
  'skus.dow_mon', 'skus.dow_tue', 'skus.dow_wed',
  'skus.dow_thu', 'skus.dow_fri', 'skus.dow_sat', 'skus.dow_sun',
]
export const MONTH_KEYS = [
  'skus.month_1', 'skus.month_2',  'skus.month_3',  'skus.month_4',
  'skus.month_5', 'skus.month_6',  'skus.month_7',  'skus.month_8',
  'skus.month_9', 'skus.month_10', 'skus.month_11', 'skus.month_12',
]

// A bar resting on one or two observations is noise drawn as a pattern. Below
// this it is dropped, and the panel says how many were dropped.
export const MIN_OBS_PER_BAR = 3
// Under these the view itself is misleading, not just a bar of it.
export const MIN_WEEKS_FOR_WEEKDAY   = 8
export const MIN_MONTHS_FOR_MONTH    = 12
export const MIN_POINTS_FOR_SPREAD   = 12
// Weekly (or coarser) series land on the same weekday every time, so a
// "weekday profile" of them is one tall bar and six empty ones.
export const MIN_DISTINCT_WEEKDAYS   = 5

// The noun for one point of the series, so the copy says "días" on a daily
// session and "semanas" on a weekly one rather than an abstract "período".
// A granularity outside this map gets the generic wording instead of a unit
// that would be quietly wrong — four quarters are not four months.
export const SERIES_UNIT: Record<string, 'day' | 'week' | 'month'> = {
  daily: 'day', weekly: 'week', monthly: 'month',
}
export function seriesUnitLabel(gran: string, n: number, t: Translate): string | null {
  const unit = SERIES_UNIT[gran]
  if (!unit) return null
  return t(n === 1 ? `period.${unit}_singular` : `period.${unit}_plural`)
}

/** Parses `YYYY-MM-DD` in UTC, so the local timezone can't shift the weekday. */
export function parseSeriesDate(d: string): { ym: string; month: number; dow: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d)
  if (!m) return null
  const dt = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
  if (isNaN(dt.getTime())) return null
  return { ym: `${m[1]}-${m[2]}`, month: Number(m[2]), dow: (dt.getUTCDay() + 6) % 7 }
}

export interface AvgBar { label: string; avg: number; n: number }
export interface AvgProfile { bars: AvgBar[]; dropped: number; eligible: boolean }

export type HistPoint = { date: string; value: number }

/** Average — never sum. A month with more observations would otherwise look
 *  bigger for a reason that has nothing to do with how much sells. */
export function averageProfile(
  points: HistPoint[],
  slots: number,
  slotOf: (p: NonNullable<ReturnType<typeof parseSeriesDate>>) => number,
  labelOf: (slot: number) => string,
): AvgProfile {
  const sum = new Array<number>(slots).fill(0)
  const n   = new Array<number>(slots).fill(0)
  for (const p of points) {
    const d = parseSeriesDate(p.date)
    if (!d || typeof p.value !== 'number' || isNaN(p.value)) continue
    const slot = slotOf(d)
    sum[slot] += p.value
    n[slot]   += 1
  }
  const bars: AvgBar[] = []
  let dropped = 0
  for (let i = 0; i < slots; i++) {
    if (n[i] === 0) continue
    if (n[i] < MIN_OBS_PER_BAR) { dropped++; continue }
    bars.push({ label: labelOf(i), avg: sum[i] / n[i], n: n[i] })
  }
  return { bars, dropped, eligible: bars.length > 1 }
}

export function weekdayProfile(points: HistPoint[], t: Translate): AvgProfile {
  return averageProfile(points, 7, d => d.dow, i => t(WEEKDAY_KEYS[i]))
}
export function monthProfile(points: HistPoint[], t: Translate): AvgProfile {
  return averageProfile(points, 12, d => d.month - 1, i => t(MONTH_KEYS[i]))
}

/** Rounds a raw bucket width up to 1, 2 or 5 × a power of ten, so the axis
 *  labels are numbers a person would say out loud. Keeps the bucketing honest
 *  on a SKU that sells 3 a week and on one that sells 8.000. */
export function niceStep(raw: number): number {
  if (!(raw > 0) || !isFinite(raw)) return 1
  const base = Math.pow(10, Math.floor(Math.log10(raw)))
  const f = raw / base
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * base
}

export interface Spread { labels: string[]; counts: number[]; meanIndex: number; mean: number }

export function buildSpread(points: HistPoint[]): Spread | null {
  const values = points.map(p => p.value).filter(v => typeof v === 'number' && !isNaN(v))
  if (values.length < MIN_POINTS_FOR_SPREAD) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (!(max > min)) return null

  const target = Math.min(12, Math.max(6, Math.round(Math.sqrt(values.length))))
  const step   = niceStep((max - min) / target)
  const start  = Math.floor(min / step) * step
  const bins   = Math.max(1, Math.ceil((max - start) / step + 1e-9))

  const counts = new Array<number>(bins).fill(0)
  for (const v of values) {
    const i = Math.min(bins - 1, Math.floor((v - start) / step + 1e-9))
    counts[i] += 1
  }

  // Whole-number steps get a single number per bucket ("18"), not a range
  // ("18,0–19,0") that says the same thing twice as wide.
  const num = (v: number) => Number.isInteger(step) && Number.isInteger(v)
    ? v.toLocaleString()
    : v.toLocaleString(undefined, { maximumFractionDigits: 1 })
  const labels = counts.map((_, i) => {
    const lo = start + i * step
    return step === 1 ? num(lo) : `${num(lo)}–${num(lo + step)}`
  })

  const mean = values.reduce((a, b) => a + b, 0) / values.length
  return { labels, counts, mean, meanIndex: (mean - start) / step - 0.5 }
}


/** Real sales, faint, with the trend drawn through them.
 *
 *  Deliberately NOT the textbook three-panel decomposition. The seasonal panel
 *  would repeat what the by-weekday / by-month chart on this same tab already
 *  says, in a form that is harder to read, and the residual panel is by
 *  definition the part that carries no pattern — two more charts of noise for a
 *  reader who asked for less. The one thing the split gives that nothing else
 *  on this page can is the trend with the repeating cycle taken out: whether
 *  the product is actually growing, or whether it is just December again. */
export function buildTrendOption(
  series: DecompositionPoint[], accent: string, isDark: boolean,
  labels: { observed: string; trend: string },
) {
  const c = chartChrome(isDark)
  const dates = series.map(p => p.date)
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 18, bottom: 4, left: 4, right: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBdr,
      borderWidth: 1,
      textStyle: { color: c.tooltipText, fontSize: 11 },
      extraCssText: 'padding:8px 10px;border-radius:8px;',
      formatter: (ps: { dataIndex: number }[]) => {
        const p = series[ps[0]?.dataIndex ?? 0]
        if (!p) return ''
        return `<div style="font-size:11px"><b>${p.date}</b><br/>
          ${labels.observed}: ${p.observed.toFixed(1)}<br/>
          <b>${labels.trend}: ${p.trend.toFixed(1)}</b></div>`
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: c.gridLine } },
      axisTick: { show: false },
      axisLabel: { color: c.dim, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: c.dim, fontSize: 10, formatter: (v: number) => fmtK(v) },
      splitLine: { lineStyle: { color: c.gridLine, type: 'dashed' } },
    },
    series: [
      {
        // The raw series is context, not the subject: thin and faint so the
        // trend reads on top of it rather than competing with it.
        name: labels.observed,
        type: 'line',
        data: series.map(p => p.observed),
        symbol: 'none',
        lineStyle: { color: accent, width: 1, opacity: 0.28 },
        z: 2,
      },
      {
        name: labels.trend,
        type: 'line',
        data: series.map(p => p.trend),
        symbol: 'none',
        smooth: true,
        lineStyle: { color: accent, width: 2.6 },
        z: 5,
      },
    ],
  }
}

export function buildProfileOption(profile: AvgProfile, accent: string, isDark: boolean, obsLabel: (n: number) => string) {
  const c = chartChrome(isDark)
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 12, bottom: 4, left: 4, right: 8, containLabel: true },
    tooltip: {
      trigger: 'item',
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBdr,
      borderWidth: 1,
      textStyle: { color: c.tooltipText, fontSize: 11 },
      extraCssText: 'padding:8px 10px;border-radius:8px;',
      formatter: (p: { dataIndex: number; name: string }) => {
        const bar = profile.bars[p.dataIndex]
        if (!bar) return ''
        return `<div style="font-size:11px"><b>${bar.label}</b><br/>${fmtK(bar.avg)}<br/>
          <span style="opacity:0.6">${obsLabel(bar.n)}</span></div>`
      },
    },
    xAxis: {
      type: 'category',
      data: profile.bars.map(b => b.label),
      axisLine: { lineStyle: { color: c.gridLine } },
      axisTick: { show: false },
      axisLabel: { color: c.dim, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: c.dim, fontSize: 10, formatter: (v: number) => fmtK(v) },
      splitLine: { lineStyle: { color: c.gridLine, type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      data: profile.bars.map(b => b.avg),
      barMaxWidth: 52,
      itemStyle: { color: accent, borderRadius: [3, 3, 0, 0] },
    }],
  }
}

export function buildSpreadOption(spread: Spread, accent: string, isDark: boolean, meanLabel: string, daysLabel: (n: number) => string) {
  const c = chartChrome(isDark)
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 18, bottom: 4, left: 4, right: 8, containLabel: true },
    tooltip: {
      trigger: 'item',
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBdr,
      borderWidth: 1,
      textStyle: { color: c.tooltipText, fontSize: 11 },
      extraCssText: 'padding:8px 10px;border-radius:8px;',
      formatter: (p: { dataIndex: number }) =>
        `<div style="font-size:11px"><b>${spread.labels[p.dataIndex]}</b><br/>
          <span style="opacity:0.6">${daysLabel(spread.counts[p.dataIndex])}</span></div>`,
    },
    xAxis: {
      type: 'category',
      data: spread.labels,
      axisLine: { lineStyle: { color: c.gridLine } },
      axisTick: { show: false },
      axisLabel: { color: c.dim, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      // The y-axis counts periods, so it is whole numbers — "150.0" would
      // invite the reader to look for a decimal that cannot exist.
      minInterval: 1,
      axisLabel: { color: c.dim, fontSize: 10, formatter: (v: number) => String(Math.round(v)) },
      splitLine: { lineStyle: { color: c.gridLine, type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      data: spread.counts,
      barMaxWidth: 46,
      barCategoryGap: '8%',
      itemStyle: { color: accent, borderRadius: [3, 3, 0, 0] },
      markLine: {
        silent: true,
        symbol: 'none',
        precision: 2,
        lineStyle: { color: c.dim, type: 'dashed', width: 1 },
        // ECharts rotates a vertical mark line's label to match the line;
        // `rotate: 0` keeps the word horizontal and readable.
        label: {
          formatter: meanLabel, color: c.dim, fontSize: 10,
          position: 'end', rotate: 0, distance: 6,
        },
        data: [{ xAxis: spread.meanIndex }],
      },
    }],
  }
}

export function PatternNote({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: 120, padding: '20px 24px', textAlign: 'center',
      fontSize: 12, color: 'var(--dim)', lineHeight: 1.6,
      border: '1px dashed var(--border)', borderRadius: 10,
    }}>
      {children}
    </div>
  )
}

export type PatternView = 'weekday' | 'month'

/** Phones: the tooltip stays inside the chart (a 360px screen has no room
 *  beside it) and opens on a tap as well as on a drag. */
function touchTooltip<T extends { tooltip?: object }>(option: T, narrow: boolean): T {
  return narrow ? { ...option, tooltip: { ...(option.tooltip ?? {}), confine: true, triggerOn: 'mousemove|click' } } : option
}

export function SalesPatternPanel({ sessionId, sku, isDark }: {
  sessionId: string; sku: string; isDark: boolean
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const [data,    setData]    = useState<SkuIntelligenceData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<unknown>(null)
  const [view,    setView]    = useState<PatternView | null>(null)

  const accent = useCssToken('--accent', isDark ? '#2BA79A' : '#0F766E', isDark)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    getSkuIntelligence(sessionId, sku, { agg: 'sum' }, { silent: true })
      .then(d => setData(d))
      .catch((e: unknown) => setError(e))
      .finally(() => setLoading(false))
  }, [sessionId, sku])

  useEffect(() => { setData(null); setView(null); load() }, [load])

  const points = data?.historical ?? []

  // How much history there is, in the units each view needs.
  const coverage = useMemo(() => {
    const parsed = points.map(p => parseSeriesDate(p.date)).filter(Boolean) as
      NonNullable<ReturnType<typeof parseSeriesDate>>[]
    return {
      months:   new Set(parsed.map(d => d.ym)).size,
      weekdays: new Set(parsed.map(d => d.dow)).size,
      weeks:    points.length >= 2
        ? (Date.parse(points[points.length - 1].date) - Date.parse(points[0].date)) / (7 * 86_400_000)
        : 0,
    }
  }, [points])

  const weekdayOk = coverage.weeks >= MIN_WEEKS_FOR_WEEKDAY && coverage.weekdays >= MIN_DISTINCT_WEEKDAYS
  const monthOk   = coverage.months >= MIN_MONTHS_FOR_MONTH

  // Land on a view that can actually be drawn instead of on an apology.
  useEffect(() => {
    if (view !== null || !data) return
    setView(weekdayOk ? 'weekday' : monthOk ? 'month' : 'weekday')
  }, [view, data, weekdayOk, monthOk])

  const profile = useMemo(() => {
    if (!points.length) return null
    return view === 'month' ? monthProfile(points, t) : weekdayProfile(points, t)
  }, [points, view, t])

  const spread = useMemo(() => buildSpread(points), [points])

  // The trend split is a separate request: it is the only thing on this tab the
  // browser cannot derive from the history it already has. A refusal here is
  // expected, not exceptional — most SKUs simply lack the two full cycles STL
  // needs — so it never surfaces as an error, only as a sentence.
  const [decomp,      setDecomp]      = useState<DecompositionData | null>(null)
  const [decompShort, setDecompShort] = useState<{ required: number; available: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    setDecomp(null)
    setDecompShort(null)
    getSkuDecomposition(sessionId, sku, undefined, { silent: true })
      .then(d => { if (!cancelled) setDecomp(d) })
      .catch((e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.code === 'decomposition_history_too_short') {
          setDecompShort({
            required:  Number(e.params.required),
            available: Number(e.params.available),
          })
        }
      })
    return () => { cancelled = true }
  }, [sessionId, sku])

  // Both charts count points of the SERIES, whatever its granularity — a month
  // bar on a daily session rests on ~30 days, not on 1 month. Saying which unit
  // is what keeps "average of 47" from being read as 47 months.
  const gran = data?.applied_granularity ?? ''
  const countLabel = useCallback((n: number) => {
    const unit = seriesUnitLabel(gran, n, t)
    return unit
      ? t('skus.pattern_based_on', { n, unit })
      : t('skus.pattern_based_on_generic', { n })
  }, [t, gran])
  const periodsLabel = useCallback((n: number) => {
    const unit = seriesUnitLabel(gran, n, t)
    return unit ? `${n} ${unit}` : t('skus.spread_periods_generic', { n })
  }, [t, gran])
  const spreadTitle = (() => {
    const unit = seriesUnitLabel(gran, 1, t)
    return unit ? t('skus.spread_title', { unit }) : t('skus.spread_title_generic')
  })()

  if (loading) return (
    <div style={{ padding: 20 }} role="status" aria-busy="true">
      <div className="skeleton" style={{ height: 220, borderRadius: 10, marginBottom: 24 }} />
      <div className="skeleton" style={{ height: 220, borderRadius: 10 }} />
    </div>
  )
  if (error != null) return (
    <div style={{ padding: 24, display: 'flex', justifyContent: 'center' }}>
      <ErrorState error={error} onRetry={load} />
    </div>
  )
  if (!points.length) return <PanelPlaceholder message={t('skus.pattern_no_history')} />

  const viewOk = view === 'month' ? monthOk : weekdayOk

  return (
    <div style={{ padding: narrow ? '14px 12px 18px' : '18px 20px 24px', display: 'flex', flexDirection: 'column', gap: 22, minWidth: 0 }}>

      {/* ── Is it really growing, or is it just December again? ── */}
      {(decomp || decompShort) && (
        <>
          <section>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{t('skus.trend_title')}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', margin: '5px 0 12px', maxWidth: 620, lineHeight: 1.6 }}>
              {t('skus.trend_caption')}
            </div>
            {decompShort ? (
              <PatternNote>
                {t('skus.trend_too_short', { required: decompShort.required, available: decompShort.available })}
              </PatternNote>
            ) : decomp ? (
              <>
                <ReactECharts
                  option={touchTooltip(buildTrendOption(decomp.series, accent, isDark, {
                    observed: t('skus.trend_observed'),
                    trend:    t('skus.trend_line'),
                  }), narrow)}
                  style={{ height: 200, width: '100%' }}
                  theme={isDark ? 'dark' : undefined}
                  opts={{ renderer: 'canvas' }}
                  notMerge
                />
                {/* The two strengths, said in words. A reader has no use for
                    "seasonal_strength 0.39" but every use for what it means. */}
                <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 8, lineHeight: 1.6 }}>
                  {t(
                    decomp.seasonal_cycle === 'weekly'
                      ? 'skus.trend_strength_weekly'
                      : 'skus.trend_strength_annual',
                    {
                      seasonal: Math.round(decomp.seasonal_strength * 100),
                      trend:    Math.round(decomp.trend_strength * 100),
                    },
                  )}
                </div>
              </>
            ) : null}
          </section>

          <div style={{ height: 1, background: 'var(--border)' }} />
        </>
      )}

      {/* ── Average by weekday / by month ── */}
      <section>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>
            {view === 'month' ? t('skus.pattern_month_title') : t('skus.pattern_weekday_title')}
          </div>
          <ChipGroup
            value={view ?? 'weekday'}
            onChange={(v: PatternView) => setView(v)}
            touch={narrow}
            options={[
              { value: 'weekday' as PatternView, label: t('skus.pattern_by_weekday') },
              { value: 'month'   as PatternView, label: t('skus.pattern_by_month') },
            ]}
          />
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', margin: '5px 0 12px', maxWidth: 620, lineHeight: 1.6 }}>
          {view === 'month' ? t('skus.pattern_month_caption') : t('skus.pattern_weekday_caption')}
        </div>

        {!viewOk ? (
          <PatternNote>
            {view === 'month'
              ? t('skus.pattern_month_too_short', { months: coverage.months, min: MIN_MONTHS_FOR_MONTH })
              : coverage.weekdays < MIN_DISTINCT_WEEKDAYS
                ? t('skus.pattern_weekday_not_daily')
                : t('skus.pattern_weekday_too_short', { weeks: Math.floor(coverage.weeks), min: MIN_WEEKS_FOR_WEEKDAY })}
          </PatternNote>
        ) : !profile || !profile.eligible ? (
          <PatternNote>{t('skus.pattern_not_enough_bars')}</PatternNote>
        ) : (
          <>
            <ReactECharts
              option={touchTooltip(buildProfileOption(profile, accent, isDark, countLabel), narrow)}
              style={{ height: 200, width: '100%' }}
              theme={isDark ? 'dark' : undefined}
              opts={{ renderer: 'canvas' }}
              notMerge
            />
            {profile.dropped > 0 && (
              <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4 }}>
                {t('skus.pattern_dropped_bars', { n: profile.dropped, min: MIN_OBS_PER_BAR })}
              </div>
            )}
          </>
        )}
      </section>

      <div style={{ height: 1, background: 'var(--border)' }} />

      {/* ── How much a period sells ── */}
      <section>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{spreadTitle}</div>
        <div style={{ fontSize: 11, color: 'var(--dim)', margin: '5px 0 12px', maxWidth: 620, lineHeight: 1.6 }}>
          {t('skus.spread_caption')}
        </div>
        {!spread ? (
          <PatternNote>{t('skus.spread_too_short')}</PatternNote>
        ) : (
          <ReactECharts
            option={touchTooltip(buildSpreadOption(spread, accent, isDark, t('skus.spread_average_marker'), periodsLabel), narrow)}
            style={{ height: 200, width: '100%' }}
            theme={isDark ? 'dark' : undefined}
            opts={{ renderer: 'canvas' }}
            notMerge
          />
        )}
      </section>
    </div>
  )
}

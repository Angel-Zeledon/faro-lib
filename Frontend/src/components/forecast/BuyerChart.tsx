'use client'
import { useMemo } from 'react'
import dynamic from 'next/dynamic'
import type { CoverageUnit, InventoryStatusItem, SkuIntelligenceData } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useCssToken, type Translate } from './shared'
import { dateToTs, likelyRange, stockHorizon, todayTs } from './buyerFacts'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// Calm, deliberately few colours: history is a quiet grey-blue, the forecast is
// the page's one accent, its uncertainty a soft tint of that same accent. The
// two stock markers borrow the semaphore's amber and red, and only when there
// is a stock figure to mark.
const HISTORY = { dark: '#8493AB', light: '#7587A3' }
const WARN = '#B7791F'
const DANGER = '#C0504D'

/** Axis and tooltip number: thousands separators, one decimal only when small. */
export function formatQty(v: number, locale: string): string {
  const digits = Math.abs(v) < 10 && !Number.isInteger(v) ? 1 : 0
  return v.toLocaleString(locale, { maximumFractionDigits: digits })
}

function buildOption(args: {
  data: SkuIntelligenceData
  status: InventoryStatusItem | undefined
  coverageUnit: CoverageUnit | undefined
  isDark: boolean
  accent: string
  lang: string
  t: Translate
  narrow: boolean
}) {
  const { data, status, coverageUnit, isDark, accent, lang, t, narrow } = args
  const locale = lang === 'en' ? 'en' : 'es'
  const dim = isDark ? '#94A3B8' : '#64748B'
  const gridLine = isDark ? 'rgba(148,163,184,0.14)' : 'rgba(100,116,139,0.16)'
  const histColor = isDark ? HISTORY.dark : HISTORY.light
  const tipBg = isDark ? '#0f1015' : '#ffffff'
  const tipBorder = isDark ? '#1e2030' : '#e2e8f0'
  const tipText = isDark ? '#e2e8f0' : '#1e293b'

  const hist = data.historical.map(p => [dateToTs(p.date), p.value] as [number, number])
  const fc = data.forecast.map(p => [dateToTs(p.date), p.value] as [number, number])
  const lastHist = hist.length ? hist[hist.length - 1] : null
  // The line starts where the history ends, so the eye reads one story.
  const fcLine = lastHist && fc.length ? [lastHist, ...fc] : fc

  const ranges = data.forecast.map(p => likelyRange(p))
  const hasBand = ranges.some(([lo, hi]) => lo !== null && hi !== null)
  const base: ([number, number] | null)[] = []
  const diff: ([number, number] | null)[] = []
  data.forecast.forEach((p, i) => {
    const [lo, hi] = ranges[i]
    const ts = dateToTs(p.date)
    if (lo === null || hi === null) { base.push(null); diff.push(null); return }
    base.push([ts, lo])
    diff.push([ts, Math.max(0, hi - lo)])
  })

  const minTs = hist.length ? hist[0][0] : fc.length ? fc[0][0] : 0
  const maxTs = fc.length ? fc[fc.length - 1][0] : hist.length ? hist[hist.length - 1][0] : 0
  const now = todayTs()
  const inRange = (ts: number) => ts >= minTs && ts <= maxTs
  const clamp = (ts: number) => Math.min(maxTs, Math.max(minTs, ts))

  const fmtDate = (ts: number, long = false) => new Intl.DateTimeFormat(locale, {
    timeZone: 'UTC', day: 'numeric', month: 'short', year: long ? 'numeric' : undefined,
  }).format(ts)
  const coarse = ['monthly', 'quarterly', 'yearly'].includes(data.applied_granularity)
  const axisDate = (value: number) => {
    const d = new Date(value)
    if (coarse) {
      return new Intl.DateTimeFormat(locale, { timeZone: 'UTC', month: 'short', year: '2-digit' }).format(value)
    }
    // January's first label carries the year so a long axis never needs a legend for it.
    const withYear = d.getUTCMonth() === 0 && d.getUTCDate() <= 7
    return new Intl.DateTimeFormat(locale, {
      timeZone: 'UTC', day: 'numeric', month: 'short', year: withYear ? '2-digit' : undefined,
    }).format(value)
  }

  // Every text marker is a small pill with its own background, so it stays
  // readable wherever it lands and never looks like it is printed under a line.
  const pill = {
    backgroundColor: tipBg, borderColor: tipBorder, borderWidth: 1,
    borderRadius: 9, padding: [2, 7, 2, 7],
  }

  // ── Markers: today, and where stock runs out against the supplier's lead time.
  const horizon = stockHorizon(status, coverageUnit, now)
  const markLines: object[] = []
  if (inRange(now)) {
    markLines.push({
      xAxis: now,
      lineStyle: { color: dim, type: 'dashed', width: 1, opacity: 0.7 },
      // 'end' puts the label just past the top of the line, i.e. in the margin
      // above the plot, not on the gridlines and series.
      label: { show: true, formatter: t('skus.chart_today'), color: dim, fontSize: 11, position: 'end', ...pill },
    })
  }
  const markAreas: object[][] = []
  // "Actual sales" names the history span: a pill above the plot, at its left
  // edge. Skipped when today sits so close to that edge that it would collide
  // with the "Today" pill.
  const plotWidthEstimate = narrow ? 290 : 700
  const historyShare = maxTs > minTs ? (Math.min(now, lastHist ? lastHist[0] : now) - minTs) / (maxTs - minTs) : 0
  if (lastHist && hist.length > 1 && historyShare * plotWidthEstimate > 150) {
    markAreas.push([
      {
        xAxis: minTs,
        itemStyle: { color: 'transparent' },
        label: {
          show: true, formatter: t('skus.chart_sold'), color: dim, fontSize: 11,
          position: 'insideTopLeft', offset: [0, -26], ...pill,
        },
      },
      { xAxis: lastHist[0] },
    ])
  }
  if (horizon) {
    const risky = horizon.gapDays > 0
    if (horizon.runoutTs <= maxTs) {
      markLines.push({
        xAxis: Math.max(minTs, horizon.runoutTs),
        lineStyle: { color: risky ? DANGER : WARN, type: 'solid', width: 1.5 },
        label: {
          show: true, color: risky ? DANGER : WARN, fontSize: 11, fontWeight: 600,
          // rotate 0: a vertical markLine turns its label sideways by default.
          position: 'middle', align: 'left', verticalAlign: 'middle', rotate: 0, offset: [6, 0],
          backgroundColor: tipBg, borderRadius: 9, padding: [2, 7, 2, 7],
          formatter: t('skus.chart_runs_out', { date: fmtDate(horizon.runoutTs) }),
        },
      })
    }
    // The supplier's lead time, as a quiet band from today to arrival.
    markAreas.push([
      {
        xAxis: clamp(now),
        itemStyle: { color: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(100,116,139,0.08)' },
        label: {
          show: true, position: 'insideBottomLeft', color: dim, fontSize: 11,
          backgroundColor: tipBg, borderRadius: 9, padding: [2, 7, 2, 7],
          formatter: t('skus.chart_lead_time', { n: Math.round(horizon.leadDays) }),
        },
      },
      { xAxis: clamp(horizon.arriveTs) },
    ])
    if (risky) {
      // Empty shelf between running out and the order landing: the one thing on
      // this chart that is not calm, because it is the one thing to act on.
      markAreas.push([
        { xAxis: clamp(horizon.runoutTs), itemStyle: { color: 'rgba(192,80,77,0.14)' } },
        { xAxis: clamp(horizon.arriveTs) },
      ])
    }
  }

  const endLabel = (text: string, color: string, bold = false) => ({
    show: true, formatter: text, color, fontSize: 11,
    fontWeight: bold ? 700 : 400, distance: 6,
  })

  const series: object[] = []
  if (hasBand) {
    series.push({
      name: 'band_base', type: 'line', data: base, stack: 'band', symbol: 'none',
      lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false },
      endLabel: endLabel(t('skus.chart_low'), dim),
      labelLayout: { hideOverlap: true },
    })
    series.push({
      name: 'band_fill', type: 'line', data: diff, stack: 'band', symbol: 'none',
      lineStyle: { opacity: 0 }, areaStyle: { color: accent, opacity: isDark ? 0.22 : 0.16 },
      silent: true, tooltip: { show: false },
      endLabel: endLabel(t('skus.chart_high'), dim),
      labelLayout: { hideOverlap: true },
    })
  }
  series.push({
    name: 'history', type: 'line', data: hist, symbol: 'none', smooth: false,
    lineStyle: { color: histColor, width: 1.75 }, itemStyle: { color: histColor }, z: 5,
  })
  series.push({
    name: 'forecast', type: 'line', data: fcLine, symbol: 'none', smooth: false,
    lineStyle: { color: accent, width: 2.75 }, itemStyle: { color: accent }, z: 10,
    endLabel: endLabel(t('skus.chart_likely'), accent, true),
    labelLayout: { hideOverlap: false },
    markLine: markLines.length ? { silent: true, symbol: 'none', animation: false, z: 30, data: markLines } : undefined,
    markArea: markAreas.length ? { silent: true, animation: false, z: 31, data: markAreas } : undefined,
  })

  const histByTs = new Map(hist)
  const fcByIndex = new Map(data.forecast.map((p, i) => [dateToTs(p.date), i] as const))

  return {
    backgroundColor: 'transparent',
    animationDuration: 250,
    textStyle: { fontFamily: 'inherit' },
    grid: { top: 40, bottom: 28, left: 8, right: narrow ? 54 : 84, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: tipBg, borderColor: tipBorder, borderWidth: 1,
      textStyle: { color: tipText, fontSize: 12 },
      extraCssText: 'padding:10px 12px;border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,0.12);',
      axisPointer: { type: 'line', lineStyle: { color: dim, opacity: 0.5, width: 1 } },
      confine: true,
      ...(narrow ? { triggerOn: 'mousemove|click' } : {}),
      formatter: (params: { value?: unknown }[]) => {
        const hit = params.find(p => Array.isArray(p.value)) as { value: [number, number] } | undefined
        if (!hit) return ''
        const ts = hit.value[0]
        const row = (label: string, val: string, color?: string) =>
          `<div style="display:flex;gap:14px;justify-content:space-between;padding:1px 0">`
          + `<span style="opacity:.7">${label}</span>`
          + `<b style="font-variant-numeric:tabular-nums;${color ? `color:${color}` : ''}">${val}</b></div>`
        const head = `<div style="opacity:.55;font-size:11px;margin-bottom:4px">${fmtDate(ts, true)}</div>`
        const fi = fcByIndex.get(ts)
        if (fi !== undefined) {
          const p = data.forecast[fi]
          const [lo, hi] = ranges[fi]
          return `<div style="min-width:150px">${head}${row(t('skus.chart_likely'), formatQty(p.value, locale), accent)}`
            + (lo !== null && hi !== null
              ? `<div style="opacity:.65;font-size:11px;margin-top:2px">${t('skus.tip_between', { lo: formatQty(lo, locale), hi: formatQty(hi, locale) })}</div>`
              : '')
            + `</div>`
        }
        const h = histByTs.get(ts)
        if (h === undefined) return ''
        return `<div style="min-width:140px">${head}${row(t('skus.chart_sold_tip'), formatQty(h, locale))}</div>`
      },
    },
    xAxis: {
      type: 'time', min: minTs, max: maxTs,
      axisLine: { lineStyle: { color: gridLine } }, axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: dim, fontSize: 11, hideOverlap: true, margin: 10,
        formatter: (v: number) => axisDate(v),
      },
    },
    yAxis: {
      type: 'value', min: 0, axisLine: { show: false }, axisTick: { show: false },
      splitNumber: narrow ? 3 : 4,
      splitLine: { lineStyle: { color: gridLine } },
      axisLabel: {
        color: dim, fontSize: 11,
        formatter: (v: number) => new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(v),
      },
    },
    series,
  }
}

/**
 * The buyer view's hero chart. History in a quiet grey-blue, one accent line
 * for the forecast, its uncertainty as a soft tint of that accent, and direct
 * labels at the right edge instead of a legend. When live stock is known, a
 * band marks the supplier's lead time and a line marks the day the stock runs
 * out. Display only: every figure is the API's, none is recomputed here.
 */
export function BuyerChart({ data, status, coverageUnit, isDark, height, onReady }: {
  data: SkuIntelligenceData
  status: InventoryStatusItem | undefined
  coverageUnit: CoverageUnit | undefined
  isDark: boolean
  /** Fixed pixel height on phones; undefined fills the parent. */
  height?: number
  onReady?: (instance: unknown) => void
}) {
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const accent = useCssToken('--accent', isDark ? '#2BA79A' : '#0F766E', isDark)
  const option = useMemo(
    () => buildOption({ data, status, coverageUnit, isDark, accent, lang, t, narrow }),
    [data, status, coverageUnit, isDark, accent, lang, t, narrow],
  )
  const empty = data.historical.length === 0 && data.forecast.length === 0
  if (empty) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: 200, color: 'var(--dim)', fontSize: 14 }}>
        {t('skus.no_series_data')}
      </div>
    )
  }
  return (
    <div
      role="img"
      aria-label={t('skus.chart_aria', { sku: data.sku })}
      style={{ height: height ?? '100%', minHeight: height ?? 340, width: '100%' }}
    >
      <ReactECharts
        option={option}
        style={{ height: '100%', width: '100%' }}
        opts={{ renderer: 'canvas' }}
        onChartReady={(inst: unknown) => onReady?.(inst)}
        notMerge
      />
    </div>
  )
}

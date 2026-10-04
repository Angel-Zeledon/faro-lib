'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import dynamic from 'next/dynamic'
import { getSkuIntelligence } from '@/lib/api'
import type {
  MetricRow, QualityReport, SkuIntelligenceData, ForecastPoint,
} from '@/lib/types'
import { downloadWorkbook } from '@/lib/excel'
import Spinner from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import BottomSheet from '@/components/mobile/BottomSheet'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useLanguage } from '@/contexts/LanguageContext'
import { granularityLabel } from '@/lib/enumLabels'
import { modelLabel } from '@/lib/modelLabel'
import {
  ChevronDown, BarChart2, LineChart as LineChartIcon, Eye, EyeOff, Download,
  Maximize2, X,
} from 'lucide-react'
import { ChipGroup } from './ChipGroup'
import { BuyerOutlook } from './BuyerOutlook'
import {
  type Translate, makeChampionRank, pct, fmt, fmtK, reliabilityInfo,
  downloadCSV, useCssToken,
} from './shared'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

export const GRANULARITY_LABELS: Record<string, string> = {
  daily:     'D',
  weekly:    'W',
  monthly:   'M',
  quarterly: 'Q',
  yearly:    'Y',
}

// Full granularity names are localized — see `granularityLabel`.

export interface FanBand {
  key:      string
  lower:    string
  upper:    string
  /** Plain-language name. Only the `legend` band is named on the chart. */
  labelKey: string
  /** Alpha of THIS ring alone. The rings are nested and stack visually, so the
   *  centre ends up at roughly the sum — see FAN_BANDS. */
  opacity:  number
  /** The one ring that earns a legend entry and the tooltip's headline range. */
  legend?:  boolean
  /** Sessions trained before the engine stored quantiles carry only
   *  lower/upper. Exactly one ring is allowed to fall back to them, so a legacy
   *  session degrades to the single band it has instead of drawing it three
   *  times at triple opacity. */
  fallback?: boolean
}

// The forecast's uncertainty, as nested rings rather than one flat band: a
// single band implies a confidence the forecast does not have. Faintest
// outermost, so the eye reads the centre as most likely without being told.
// The per-ring alphas are deliberately tiny because the rings overlap — the
// centre lands near 0.05+0.07+0.09 ≈ 0.2, which is still quiet enough that the
// forecast line stays the most legible thing on the chart. Opacity does all the
// work; there is no new colour here, the rings are the forecast's own green.
export const FAN_BANDS: FanBand[] = [
  { key: 'fan_wide',   lower: 'q5',  upper: 'q95', labelKey: 'skus.band_range_wide',   opacity: 0.05 },
  { key: 'fan_likely', lower: 'q10', upper: 'q90', labelKey: 'skus.band_range_likely', opacity: 0.07, legend: true, fallback: true },
  { key: 'fan_core',   lower: 'q25', upper: 'q75', labelKey: 'skus.band_range_core',   opacity: 0.09 },
]
export const FAN_LIKELY = FAN_BANDS.find(b => b.legend)!

// Primary (first selected) model keeps the classic forecast green; additional
// overlaid models get a stable color from this palette, indexed by the model's
// position in available_models so colors don't shift as selection changes.
export const PRIMARY_FORECAST_COLOR = '#2E8B62'

// The fan is the forecast's own shadow, so it takes the forecast's colour.
// (A `var(--accent)` here would not survive: ECharts paints onto a canvas,
// where a CSS custom property is not a colour the 2D context can resolve.)
export const FAN_COLOR = PRIMARY_FORECAST_COLOR
export const OVERLAY_COLORS = ['#B7791F', '#3E8E9B', '#B77AA0', '#8C80C0', '#BF7440', '#7A9A4A', '#AD7AA8', '#C99A3E']

export function detectGaps(
  historical: { date: string; value: number }[],
  granularity: string,
): { start: string; end: string }[] {
  if (historical.length < 2) return []
  const thresholds: Record<string, number> = {
    daily:     1.6 * 86_400_000,
    weekly:    8.5 * 86_400_000,
    monthly:   40  * 86_400_000,
    quarterly: 100 * 86_400_000,
    yearly:    400 * 86_400_000,
  }
  const threshold = thresholds[granularity] ?? 2 * 86_400_000
  const gaps: { start: string; end: string }[] = []
  for (let i = 1; i < historical.length; i++) {
    const diff = Date.parse(historical[i].date) - Date.parse(historical[i - 1].date)
    if (diff > threshold) gaps.push({ start: historical[i - 1].date, end: historical[i].date })
  }
  return gaps
}

export function exportChartCSV(sku: string, data: SkuIntelligenceData) {
  const histRows = data.historical.map(p => [p.date, p.value, null, null, null] as (string | number | null)[])
  const fcastRows = data.forecast.map(p => {
    const fp = p as unknown as Record<string, number | null | undefined>
    return [p.date, null, p.value, fp['lower'] ?? fp['q10'] ?? null, fp['upper'] ?? fp['q90'] ?? null] as (string | number | null)[]
  })
  downloadCSV(
    `forecast_${sku}_${data.applied_granularity}.csv`,
    ['date', 'historical', 'forecast', 'lower', 'upper'],
    [...histRows, ...fcastRows],
  )
}

// ── Outlier detection ─────────────────────────────────────────────────────────

// The engine's fence, not a second opinion. This used to use 1.5·IQR — the
// textbook "mild outlier" rule — while the engine's DataQualityChecker uses
// `outlier_iqr_factor = 3.0`. Same maths, different threshold, so the chart
// footer said "5 valores atípicos detectados" one click away from the Calidad
// tab saying "0 — serie limpia, sin advertencias", about the same SKU. Both
// were true to their own rule and the user reads one screen.
//
// The engine is the authority: its count is what the quality score and the
// warnings are computed from. It publishes only a COUNT, never positions, so
// the markers still have to be located here — which is exactly why this
// constant has to keep matching
// `ForecastingCore/forecasting_core/data/quality.py: outlier_iqr_factor`.
export const OUTLIER_IQR_FACTOR = 3.0

export function detectOutliers(points: { date: string; value: number }[]): number[] {
  if (points.length < 6) return []
  const sorted = [...points].sort((a, b) => a.value - b.value)
  const q1  = sorted[Math.floor(sorted.length * 0.25)].value
  const q3  = sorted[Math.floor(sorted.length * 0.75)].value
  const iqr = q3 - q1
  const lo  = q1 - OUTLIER_IQR_FACTOR * iqr
  const hi  = q3 + OUTLIER_IQR_FACTOR * iqr
  return points.reduce<number[]>((acc, p, i) => {
    if (p.value < lo || p.value > hi) acc.push(i)
    return acc
  }, [])
}

// ── Stats strip ───────────────────────────────────────────────────────────────

export function StatsStrip({ data, quality, showTechnical }: {
  data: SkuIntelligenceData
  // Missing in compare mode's second (B) panel — that session's quality
  // report is never fetched, so the tile degrades to '—' there rather than
  // being wired up to fetch a report this view doesn't otherwise need.
  quality?: QualityReport[string]
  showTechnical: boolean
}) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const { stats, metrics, historical, forecast } = data
  // The model this SKU's orders are actually computed from — see makeChampionRank.
  // Baselines are excluded for the same reason the engine excludes them: they
  // are the bar to clear, not a model anyone buys from.
  const rank = makeChampionRank(metrics)
  const bestMetric = metrics.filter(r => r.type !== 'baseline').reduce<MetricRow | null>((b, r) => {
    const value = rank(r)
    if (value === null || value === undefined) return b
    const current = b === null ? null : rank(b)
    return current === null || current === undefined || value < current ? r : b
  }, null)

  const likelyRange = stats?.min != null && stats?.max != null
    ? `${fmtK(stats.min)}–${fmtK(stats.max)}`
    : '—'
  const reliability = quality ? reliabilityInfo(quality.quality_score, t) : null

  // What a buyer acts on: what it usually sells, the range it moves in, and
  // whether to trust the curve — always on screen.
  const primaryItems = [
    { label: t('skus.stat_avg_sales'), value: fmtK(stats?.mean) },
    { label: t('skus.stat_likely_range'), value: likelyRange },
    { label: t('skus.stat_reliability'), value: reliability?.label ?? '—', color: reliability?.color },
  ]

  // An analyst's instrument panel — behind the technical-detail toggle.
  const technicalItems = [
    { label: t('skus.stat_variability'), value: fmtK(stats?.std) },
    { label: t('skus.stat_historical_points'), value: historical.length.toString() },
    { label: t('skus.stat_forecast_steps'), value: forecast.length.toString() },
    // NOT "best WAPE", which is what this tile claimed while showing the WAPE
    // of the CHAMPION — chosen by asymmetric cost, not by WAPE. On a real
    // session it read "Mejor WAPE 24.6%" directly above a table whose own rows
    // showed 17.9% and 21.5%, so the screen contradicted itself in one glance.
    // The value is the right one to show — it is the error of the forecast the
    // orders come from — so the fix is the label, not the number.
    { label: t('skus.stat_champion_wape'), value: bestMetric?.wape != null ? pct(bestMetric.wape) : '—' },
    { label: t('skus.stat_best_model'), value: modelLabel(t, bestMetric?.model) },
  ]

  const row = (items: { label: string; value: string; color?: string }[]) => (
    <div style={{
      display: 'flex', gap: 0,
      borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)',
      background: 'var(--surface-2)',
      // Phones: five technical tiles cannot share 336px, so they wrap into
      // rows of three instead of squeezing their labels to nothing.
      ...(narrow ? { flexWrap: 'wrap' as const } : {}),
    }}>
      {items.map((item, i) => (
        <div key={item.label} style={{
          flex: narrow ? '1 1 33%' : 1, padding: '7px 10px', textAlign: 'center',
          borderRight: i < items.length - 1 ? '1px solid var(--border)' : undefined,
          ...(narrow ? { minWidth: 0, boxSizing: 'border-box' as const, overflowWrap: 'anywhere' as const } : {}),
        }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: item.color ?? 'var(--fg)', lineHeight: 1.2 }}>{item.value}</div>
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{item.label}</div>
        </div>
      ))}
    </div>
  )

  return (
    <>
      {row(primaryItems)}
      {showTechnical && row(technicalItems)}
    </>
  )
}

// ── Confidence band toggle ────────────────────────────────────────────────────

export function BandToggle({ active, onToggle, hasQuantiles, tourAnchor, touch = false }: {
  active: boolean
  onToggle: () => void
  hasQuantiles: boolean
  /** Set on the single-session panel only — a tour anchor has to be unique. */
  tourAnchor?: string
  /** Phone layout: a 44px target. */
  touch?: boolean
}) {
  const { t } = useLanguage()
  const on = active && hasQuantiles
  return (
    <div data-tour={tourAnchor} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <button
        title={hasQuantiles ? t('skus.band_confidence') : t('skus.no_quantile_data_title')}
        onClick={() => hasQuantiles && onToggle()}
        style={{
          all: 'unset', cursor: hasQuantiles ? 'pointer' : 'default',
          display: 'flex', alignItems: 'center', gap: 4,
          padding: touch ? '0 12px' : '3px 8px', borderRadius: 6,
          fontSize: touch ? 13 : 11, fontWeight: 500,
          ...(touch ? { minHeight: 44, boxSizing: 'border-box' as const } : {}),
          border: `1px solid ${on ? FAN_COLOR : 'var(--border)'}`,
          background: on ? FAN_COLOR + '22' : 'transparent',
          color: on ? FAN_COLOR : 'var(--dim)',
          opacity: hasQuantiles ? 1 : 0.45,
          transition: 'all 0.12s',
        }}
      >
        {on ? <Eye size={10} /> : <EyeOff size={10} />}
        {t('skus.band_confidence')}
      </button>
      {!hasQuantiles && (
        <span style={{ fontSize: 10, color: 'var(--dim)', opacity: 0.5 }}>
          {t('skus.no_quantile_data')}
        </span>
      )}
    </div>
  )
}

// ── Forecast chart ────────────────────────────────────────────────────────────

export type ChartType = 'line' | 'bar'

// An extra model's forecast overlaid on the primary chart (multi-model compare).
export interface ModelOverlay {
  model:    string
  color:    string
  forecast: ForecastPoint[]
}

// Shortens ISO date labels on the x-axis by series granularity so they don't
// overlap: daily/weekly show month-day (year only when it changes), monthly
// shows year-month, quarterly shows the quarter, yearly just the year.
// Falls back to the raw value for non-ISO dates. The tooltip keeps full dates.
export function makeAxisDateFormatter(granularity: string, dates: string[]) {
  const parse = (d: string) => /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(d)
  return (value: string, index: number) => {
    const m = parse(value)
    if (!m) return value
    const [, year, month, day] = m
    const prev = index > 0 ? parse(dates[index - 1]) : null
    const yearChanged = !prev || prev[1] !== year
    switch (granularity) {
      case 'yearly':    return year
      case 'quarterly': return `${yearChanged ? year + ' ' : ''}Q${Math.ceil(Number(month) / 3)}`
      case 'monthly':   return `${year}-${month}`
      default:          return yearChanged ? `${year}-${month}-${day ?? '01'}` : `${month}-${day ?? '01'}`
    }
  }
}

export function buildChartOption(
  data: SkuIntelligenceData,
  chartType: ChartType,
  showBand: boolean,
  isDark: boolean,
  gaps: { start: string; end: string }[],
  outlierIndices: number[] = [],
  t: Translate = (k) => k,
  overlays: ModelOverlay[] = [],
  accent = '#0F766E',
  /** Phone layout: the legend scrolls instead of wrapping over the plot, the
   *  tooltip stays inside the chart, and a one-finger drag scrubs the tooltip
   *  instead of panning (pinch still zooms) — so the page keeps scrolling. */
  compact = false,
) {
  const { historical, forecast } = data

  const allDates = [...historical.map(p => p.date), ...forecast.map(p => p.date)]

  // Date-indexed lookups for the custom tooltip
  const forecastByDate  = new Map(forecast.map(p => [p.date, p]))
  const historicalByDate = new Map(historical.map(p => [p.date, p]))

  const dim         = '#64748b'
  const gridLine    = isDark ? '#1e2030' : '#e2e8f0'
  const tooltipBg   = isDark ? '#0f1015' : '#ffffff'
  const tooltipBdr  = isDark ? '#1e2030' : '#e2e8f0'
  const tooltipText = isDark ? '#e2e8f0' : '#1e293b'
  // Resolved, not `var(--accent)`: ECharts paints onto a canvas, where a CSS
  // custom property is not a colour the 2D context can resolve. It silently
  // fell through to whatever ECharts had left in the palette slot, which is why
  // this line used to come out green — and why it disappeared outright once the
  // fan changed how many series precede it.
  const histColor   = accent
  const fcastColor  = PRIMARY_FORECAST_COLOR
  // When several models are overlaid, name the primary series by its model so
  // the legend/tooltip distinguish it from the overlays.
  const primaryName = overlays.length > 0 && data.model
    ? modelLabel(t, data.model)
    : t('skus.series_forecast_p50')

  // Retrieve a quantile value from a forecast point. `allowFallback` is granted
  // to exactly one ring of the fan, so a session stored before quantiles
  // existed degrades to its single lower/upper band instead of painting the
  // same band three times.
  function getQ(p: ForecastPoint, qKey: string, allowFallback = false): number | null {
    const rec = p as unknown as Record<string, number | null | undefined>
    const v = rec[qKey]
    if (typeof v === 'number') return v
    if (!allowFallback) return null
    const n = Number(qKey.slice(1))
    if (n < 50 && typeof p.lower === 'number') return p.lower
    if (n > 50 && typeof p.upper === 'number') return p.upper
    return null
  }

  const series: object[] = []

  // ── Uncertainty fan (rendered first so the P50 line sits on top) ───────────
  const renderedBandLabels: string[] = []

  if (showBand) {
    // Widest first: each ring is painted over the previous one, so the alphas
    // add up towards the centre and the nesting reads without a legend.
    for (const band of FAN_BANDS) {
      // Aligned data arrays (null over historical dates — the fan only exists
      // in the forecast zone).
      const lowerVals: (number | null)[] = [
        ...historical.map(() => null),
        ...forecast.map(p => getQ(p, band.lower, band.fallback)),
      ]
      const fillVals: (number | null)[] = lowerVals.map((lo, i) => {
        if (i < historical.length) return null
        const hi = getQ(forecast[i - historical.length], band.upper, band.fallback)
        if (lo === null || hi === null) return null
        return Math.max(0, hi - lo)
      })
      // A ring whose quantiles this session never stored simply isn't drawn.
      if (!fillVals.some(v => v !== null)) continue

      const fillName = band.legend ? t(band.labelKey) : band.key
      if (band.legend) renderedBandLabels.push(fillName)

      // Invisible base at lower bound (stacked area anchoring technique)
      series.push({
        name:            `${band.key}_base`,
        type:            'line',
        data:            lowerVals,
        lineStyle:       { opacity: 0 },
        areaStyle:       { opacity: 0 },
        stack:           band.key,
        symbol:          'none',
        silent:          true,
        legendHoverLink: false,
        tooltip:         { show: false },
      })

      // Visible fill = (upper − lower) stacked on the invisible base
      series.push({
        name:            fillName,
        type:            'line',
        data:            fillVals,
        lineStyle:       { opacity: 0 },
        areaStyle:       { color: FAN_COLOR, opacity: band.opacity },
        // Without this the legend swatch would be handed the next colour off
        // ECharts' default palette — a pink dot standing for a green band.
        itemStyle:       { color: FAN_COLOR, opacity: 0.35 },
        stack:           band.key,
        symbol:          'none',
        silent:          !band.legend,
        legendHoverLink: !!band.legend,
        tooltip:         { show: false },
      })
    }
  }

  // ── Historical series ──────────────────────────────────────────────────────
  const histData: (number | null)[] = [
    ...historical.map(p => p.value),
    ...forecast.map(() => null),
  ]
  const histSeries: Record<string, unknown> = {
    name:      t('skus.series_historical'),
    data:      histData,
    lineStyle: { color: histColor, width: 2 },
    itemStyle: { color: histColor },
    symbol:    'none',
    smooth:    true,
    z:         5,
  }
  if (chartType === 'bar') {
    histSeries['type'] = 'bar'; histSeries['barMaxWidth'] = 12
    delete histSeries['lineStyle']; delete histSeries['symbol']; delete histSeries['smooth']; delete histSeries['z']
  } else {
    histSeries['type'] = 'line'
  }
  if (outlierIndices.length > 0) {
    histSeries['markPoint'] = {
      symbolSize: 8,
      data: outlierIndices.map(i => ({
        coord:     [historical[i].date, historical[i].value],
        itemStyle: { color: '#B7791F', borderColor: '#fff', borderWidth: 1.5 },
        label:     { show: false },
      })),
      tooltip: {
        formatter: (p: { data: { coord: [string, number] } }) =>
          `<div style="font-size:11px"><b style="color:#B7791F">${t('skus.outlier_label')}</b><br/>${p.data.coord[0]}: ${p.data.coord[1].toFixed(2)}</div>`,
      },
    }
  }

  if (gaps.length > 0) {
    histSeries['markArea'] = {
      silent: true,
      itemStyle: {
        color: isDark ? 'rgba(251,191,36,0.07)' : 'rgba(251,191,36,0.11)',
        borderColor: 'rgba(251,191,36,0.4)',
        borderWidth: 1,
        borderType: 'dashed',
      },
      label: { show: false },
      data: gaps.map(g => [{ xAxis: g.start }, { xAxis: g.end }]),
    }
  }
  series.push(histSeries)

  // ── Forecast (P50) series — rendered on top of CI bands ──────────────────
  // Past this many points the per-point markers stop being readable dots and
  // become a texture, so they are dropped. A 90-day daily horizon is well over
  // it; a 12-month one is not, and there the dots do help.
  const isDenseForecast = forecast.length > 30

  const fcastData: (number | null)[] = [
    ...historical.map(() => null),
    ...forecast.map(p => p.value),
  ]
  if (historical.length > 0 && forecast.length > 0) {
    fcastData[historical.length - 1] = historical[historical.length - 1].value
  }
  const fcastSeries: Record<string, unknown> = {
    name:       primaryName,
    data:       fcastData,
    // Solid, and no per-point dots on a long horizon. This line used to be
    // dashed with a marker on every point: on a 90-day daily forecast that
    // oscillates with the weekday cycle, the dashes never join up and the
    // whole thing reads as scattered confetti instead of a line. What it was
    // trying to say — "past this point it is a prediction" — is now said by
    // the divider below, which costs one thin line instead of the legibility
    // of the main series.
    lineStyle:  { color: fcastColor, width: 2.5 },
    itemStyle:  { color: fcastColor },
    symbol:     chartType === 'bar' || isDenseForecast ? 'none' : 'circle',
    symbolSize: 4,
    smooth:     true,
    z:          10,
  }
  // Where history ends and the forecast begins. Drawn on the forecast series so
  // it disappears with it, and kept deliberately quiet: 1px, dimmed, no arrow.
  if (chartType !== 'bar' && historical.length > 0 && forecast.length > 0) {
    fcastSeries['markLine'] = {
      silent: true,
      symbol: 'none',
      lineStyle: { color: dim, type: 'dashed', width: 1, opacity: 0.55 },
      label: {
        show: true, position: 'insideEndTop', color: dim, fontSize: 10,
        // ECharts rotates a markLine label to follow the line, which on a
        // vertical divider means the caption reads bottom-to-top. Force it flat.
        // Phones: the divider sits near the right edge, so the caption reads
        // leftwards from it instead of running off the canvas.
        rotate: 0, align: compact ? 'right' : 'left', padding: compact ? [0, 6, 4, 0] : [0, 0, 4, 6],
        formatter: t('skus.forecast_starts_here'),
      },
      data: [{ xAxis: historical[historical.length - 1].date }],
    }
  }
  if (chartType === 'bar') {
    fcastSeries['type'] = 'bar'; fcastSeries['barMaxWidth'] = 12
    fcastSeries['itemStyle'] = { color: fcastColor, opacity: 0.85 }
    delete fcastSeries['lineStyle']; delete fcastSeries['symbol']; delete fcastSeries['smooth']; delete fcastSeries['z']
  } else {
    fcastSeries['type'] = 'line'
  }
  series.push(fcastSeries)

  // ── Overlay forecasts for additionally selected models ─────────────────────
  const overlayByDate = overlays.map(ov => ({
    name:   modelLabel(t, ov.model),
    color:  ov.color,
    byDate: new Map(ov.forecast.map(p => [p.date, p.value])),
  }))
  for (const ov of overlayByDate) {
    const vals: (number | null)[] = allDates.map((d, i) =>
      i < historical.length ? null : ov.byDate.get(d) ?? null)
    // Connect the overlay line to the last historical point (same visual
    // continuity trick as the primary forecast); skip for bars.
    if (chartType !== 'bar' && historical.length > 0 && vals.some(v => v !== null)) {
      vals[historical.length - 1] = historical[historical.length - 1].value
    }
    const s: Record<string, unknown> = { name: ov.name, data: vals }
    if (chartType === 'bar') {
      s['type'] = 'bar'; s['barMaxWidth'] = 12
      s['itemStyle'] = { color: ov.color, opacity: 0.85 }
    } else {
      s['type'] = 'line'
      // Solid too, for the same reason as the primary forecast. Overlays are
      // told apart by colour, which survives a jagged series; a dash pattern
      // does not.
      s['lineStyle']  = { color: ov.color, width: 2 }
      s['itemStyle']  = { color: ov.color }
      s['symbol']     = isDenseForecast ? 'none' : 'circle'
      s['symbolSize'] = 3
      s['smooth']     = true
      s['z']          = 9
    }
    series.push(s)
  }

  // ── Tooltip — shows actual percentile values via date lookup ──────────────
  const tooltipFormatter = (params: { axisValue: string }[]) => {
    const date = params[0]?.axisValue
    if (!date) return ''

    const hp = historicalByDate.get(date)
    const fp = forecastByDate.get(date)
    if (!hp && !fp) return ''

    const dot = (color: string, h = 8) =>
      `<span style="display:inline-block;width:${h}px;height:${h}px;border-radius:2px;background:${color};flex-shrink:0"></span>`
    const row = (swatch: string, label: string, val: string) =>
      `<div style="display:flex;align-items:center;gap:7px;padding:2px 0">
        ${swatch}
        <span style="opacity:0.65;white-space:nowrap">${label}</span>
        <span style="font-weight:600;margin-left:auto;padding-left:14px;font-variant-numeric:tabular-nums">${val}</span>
      </div>`

    const lines: string[] = []

    if (hp)
      lines.push(row(dot(histColor), t('skus.series_historical'), hp.value.toFixed(2)))

    if (fp) {
      lines.push(row(dot(fcastColor), primaryName, fp.value.toFixed(2)))
      if (showBand) {
        // Two rows at most. The likely range is what the buyer plans against;
        // the wide one is the tail they are choosing to cover. The innermost
        // ring is left to the picture — a third number here would be noise.
        const bandDot = (alpha: number) =>
          `<span style="display:inline-block;width:12px;height:4px;border-radius:2px;background:${FAN_COLOR};opacity:${alpha};flex-shrink:0"></span>`
        const range = (lo: number | null, hi: number | null) =>
          `${lo !== null ? lo.toFixed(2) : '—'} – ${hi !== null ? hi.toFixed(2) : '—'}`

        const lo = getQ(fp, FAN_LIKELY.lower, true)
        const hi = getQ(fp, FAN_LIKELY.upper, true)
        if (lo !== null || hi !== null) {
          lines.push(row(bandDot(0.8), t(FAN_LIKELY.labelKey), range(lo, hi)))
        }
        const wide = FAN_BANDS[0]
        const wLo = getQ(fp, wide.lower)
        const wHi = getQ(fp, wide.upper)
        if (wLo !== null && wHi !== null) {
          lines.push(row(bandDot(0.4), t(wide.labelKey), range(wLo, wHi)))
        }
      }
      for (const ov of overlayByDate) {
        const v = ov.byDate.get(date)
        if (typeof v === 'number') lines.push(row(dot(ov.color), ov.name, v.toFixed(2)))
      }
    }

    return `<div style="font-size:11px;min-width:${compact ? 140 : 170}px">
      <div style="margin-bottom:5px;opacity:0.45;font-size:10px">${date}</div>
      ${lines.join('')}
    </div>`
  }

  const legendData = [
    t('skus.series_historical'),
    primaryName,
    ...overlayByDate.map(o => o.name),
    ...renderedBandLabels,
  ]

  return {
    backgroundColor: 'transparent',
    animation:       true,
    animationDuration: 300,
    tooltip: {
      trigger:      'axis',
      backgroundColor: tooltipBg,
      borderColor:  tooltipBdr,
      borderWidth:  1,
      textStyle:    { color: tooltipText, fontSize: 11 },
      extraCssText: 'padding:10px 12px;border-radius:8px;',
      formatter:    tooltipFormatter,
      ...(compact ? { confine: true, triggerOn: 'mousemove|click' } : {}),
    },
    legend: compact
      ? {
          data: legendData, type: 'scroll',
          textStyle: { color: dim, fontSize: 10 },
          top: 4, left: 8, right: 8,
          itemWidth: 12, itemHeight: 6, itemGap: 8,
          pageIconSize: 10, pageTextStyle: { color: dim, fontSize: 10 },
        }
      : {
          data:      legendData,
          textStyle: { color: dim, fontSize: 11 },
          top:       6,
          right:     16,
          itemWidth: 16,
          itemHeight: 8,
        },
    // containLabel keeps y-axis labels from clipping; bottom leaves room for
    // the x-axis labels (zoom/pan is gesture-only via the inside dataZoom).
    grid: compact
      ? { top: 50, bottom: 22, left: 4, right: 12, containLabel: true }
      : { top: 40, bottom: 28, left: 12, right: 20, containLabel: true },
    xAxis: {
      type:      'category',
      data:      allDates,
      axisLine:  { lineStyle: { color: gridLine } },
      axisTick:  { show: false },
      axisLabel: {
        color:       dim,
        fontSize:    10,
        hideOverlap: true,
        interval:    'auto',
        margin:      10,
        formatter:   makeAxisDateFormatter(data.applied_granularity, allDates),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type:      'value',
      axisLine:  { show: false },
      axisTick:  { show: false },
      axisLabel: { color: dim, fontSize: 10, formatter: (v: number) => fmtK(v) },
      splitLine: { lineStyle: { color: gridLine, type: 'dashed' } },
    },
    // Zoom/pan stays gesture-only: wheel/pinch to zoom, drag to pan. The
    // slider was removed together with the toolbox — it duplicated the same
    // gestures while eating ~30px of chart height (worst on mobile, where it
    // competed with pinch-zoom).
    dataZoom: [
      compact
        ? { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: false, preventDefaultMouseMove: false }
        : { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
    ],
    series,
  }
}

// ── Main chart panel ──────────────────────────────────────────────────────────

export function ChartPanel({ sessionId, sku, isDark, tourAnchor, quality, showTechnical, onSeeOrder }: {
  sessionId: string; sku: string; isDark: boolean
  /** Set on the single-session panel only — in compare mode two of these are
   *  on screen, and a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
  /** Drives the "how much to trust it" tile. Absent for compare mode's B
   *  panel — that session's quality report isn't fetched. */
  quality?: QualityReport[string]
  /** True in the page's technical view: the rest of the stat tiles, the
   *  model-selection chips and the run details in the footer. The buyer view
   *  gets the upcoming-periods strip in their place — see BuyerOutlook. */
  showTechnical: boolean
  /** Buyer view: where "see what to order" goes (the Inventory tab). */
  onSeeOrder?: () => void
}) {
  const { t } = useLanguage()
  // Phones get the same panel with thumb-sized controls, the export menu as a
  // bottom sheet and a compact chart option — see buildChartOption(compact).
  const narrow = useIsNarrow()
  const [data,        setData]        = useState<SkuIntelligenceData | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [fetching,    setFetching]    = useState(false)
  // Raw error so ErrorState can classify it by kind.
  const [error,       setError]       = useState<unknown>(null)
  const [chartType,   setChartType]   = useState<ChartType>('line')
  const [granularity, setGranularity] = useState<string | null>(null)
  // Selected models for the multi-model overlay. The first entry is the
  // "primary" model (drives axes/stats/band); the rest are overlaid series.
  const [selModels,   setSelModels]   = useState<string[]>([])
  const [overlays,    setOverlays]    = useState<Record<string, SkuIntelligenceData>>({})
  const [showBand,    setShowBand]    = useState(true)
  const [fullscreen,  setFullscreen]  = useState(false)
  const [showExportMenu, setShowExportMenu] = useState(false)
  const echartsRef = useRef<any>(null)
  const accent = useCssToken('--accent', isDark ? '#2BA79A' : '#0F766E', isDark)

  // Cache results by (sku|gran|model) to avoid redundant API calls.
  // Aggregation is fixed to 'sum' — the backend default (forecasts.py
  // `agg: str = Query("sum")`) — since the Sum/Avg toggle was removed.
  const cache = useRef<Map<string, SkuIntelligenceData>>(new Map())
  const cacheKey = (gran: string | null, model: string | undefined) =>
    `${sku}|${gran ?? ''}|${model ?? ''}`

  const fetchData = useCallback((gran?: string, model?: string, isInitial = false) => {
    const key = cacheKey(gran ?? null, model)
    const hit = cache.current.get(key)
    if (hit) {
      setData(hit)
      if (!gran) setGranularity(hit.applied_granularity)
      setLoading(false)
      setFetching(false)
      return
    }
    if (isInitial) setLoading(true); else setFetching(true)
    setError(null)
    // `silent: true` — this panel renders the failure itself as an ErrorState.
    getSkuIntelligence(sessionId, sku, {
      model:       model,
      granularity: gran ?? undefined,
      agg:         'sum',
    }, { silent: true })
      .then(d => {
        cache.current.set(key, d)
        // Pre-cache under applied granularity so the follow-up effect is a cache hit
        cache.current.set(cacheKey(d.applied_granularity, model), d)
        // Also under the resolved model name, so defaulting the selection to
        // the best model (selModels = [d.model]) doesn't refetch.
        if (d.model) cache.current.set(cacheKey(d.applied_granularity, d.model), d)
        setData(d)
        if (!gran) setGranularity(d.applied_granularity)
      })
      .catch((e: unknown) => setError(e))
      .finally(() => { setLoading(false); setFetching(false) })
  }, [sessionId, sku])

  useEffect(() => {
    setData(null)
    setLoading(true)
    setGranularity(null)
    setSelModels([])
    setOverlays({})
    cache.current.clear()
    fetchData(undefined, undefined, true)
  }, [sessionId, sku])

  // Default the selection to the model the API chose (the best model) once the
  // first response lands.
  useEffect(() => {
    if (data && selModels.length === 0 && data.model) setSelModels([data.model])
  }, [data, selModels.length])

  useEffect(() => {
    if (granularity !== null && data) {
      fetchData(granularity, selModels[0])
    }
  }, [granularity, selModels])

  // Fetch overlay datasets for the additionally selected models. Only their
  // forecast series is used; axes/stats stay driven by the primary dataset.
  useEffect(() => {
    const extra = selModels.slice(1)
    if (!extra.length) { setOverlays({}); return }
    if (granularity === null) return
    let cancelled = false
    Promise.all(extra.map(m => {
      const key = cacheKey(granularity, m)
      const hit = cache.current.get(key)
      const p = hit
        ? Promise.resolve(hit)
        : getSkuIntelligence(sessionId, sku, { model: m, granularity, agg: 'sum' }, { silent: true })
            .then(d => { cache.current.set(key, d); return d })
      return p.then(d => [m, d] as const).catch(() => null)
    })).then(results => {
      if (cancelled) return
      const next: Record<string, SkuIntelligenceData> = {}
      for (const r of results) if (r) next[r[0]] = r[1]
      setOverlays(next)
    })
    return () => { cancelled = true }
  }, [selModels, granularity, sessionId, sku])

  // Fullscreen: Escape closes; force an ECharts resize after the container
  // swaps between inline and fixed-overlay layout (echarts-for-react also
  // auto-resizes via its size sensor — this is a belt-and-braces nudge).
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen])

  useEffect(() => {
    const id = window.setTimeout(() => echartsRef.current?.resize?.(), 60)
    return () => window.clearTimeout(id)
  }, [fullscreen])

  const exportPNG = useCallback(() => {
    if (!echartsRef.current) return
    const instance = echartsRef.current
    const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark ? '#0f1015' : '#ffffff' })
    const a = document.createElement('a')
    a.href = url; a.download = `forecast_${sku}.png`; a.click()
    setShowExportMenu(false)
  }, [sku, isDark])

  const exportPDF = useCallback(() => {
    if (!echartsRef.current || !data) return
    const instance = echartsRef.current
    const imgData = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark ? '#0f1015' : '#ffffff' })
    const imgW = instance.getWidth()
    const imgH = instance.getHeight()
    import('jspdf').then(({ jsPDF }) => {
      const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
      const pageW = 210, pageH = 297, margin = 16, contentW = 210 - 16 * 2

      // Header bar
      doc.setFillColor(17, 19, 31)
      doc.rect(0, 0, pageW, 18, 'F')
      doc.setFontSize(11); doc.setTextColor(129, 140, 248)
      doc.text(t('skus.pdf_report_title'), margin, 12)

      // SKU + metadata
      let y = 28
      doc.setFontSize(16); doc.setTextColor(30, 41, 59)
      doc.text(sku, margin, y); y += 7
      doc.setFontSize(9); doc.setTextColor(100, 116, 139)
      doc.text(`${t('skus.pdf_session_label')}: ${sessionId}  ·  ${t('skus.pdf_granularity_label')}: ${data.applied_granularity}  ·  ${t('skus.pdf_model_label')}: ${modelLabel(t, data.model)}`, margin, y)
      y += 8

      // Chart image
      const chartDisplayW = contentW
      const chartDisplayH = (imgH / imgW) * chartDisplayW
      doc.addImage(imgData, 'PNG', margin, y, chartDisplayW, chartDisplayH)
      y += chartDisplayH + 10

      // Stats row
      if (data.stats) {
        const items = [
          { label: t('skus.pdf_stat_mean'),    value: fmtK(data.stats.mean) },
          { label: t('skus.pdf_stat_std_dev'), value: fmtK(data.stats.std) },
          { label: t('skus.pdf_stat_min'),     value: fmtK(data.stats.min) },
          { label: t('skus.pdf_stat_max'),     value: fmtK(data.stats.max) },
        ]
        const boxW = (contentW - 6) / 4
        items.forEach((item, i) => {
          const bx = margin + i * (boxW + 2)
          doc.setFillColor(241, 245, 249); doc.roundedRect(bx, y, boxW, 14, 2, 2, 'F')
          doc.setFontSize(11); doc.setTextColor(30, 41, 59)
          doc.text(item.value, bx + boxW / 2, y + 6, { align: 'center' })
          doc.setFontSize(8); doc.setTextColor(100, 116, 139)
          doc.text(item.label, bx + boxW / 2, y + 11, { align: 'center' })
        })
        y += 20
      }

      // Metrics table
      if (data.metrics.length > 0) {
        doc.setFontSize(10); doc.setTextColor(129, 140, 248)
        doc.text(t('skus.pdf_model_performance'), margin, y); y += 5
        const cols = [t('skus.pdf_col_model'), t('skus.pdf_col_type'), t('skus.pdf_col_cost'), t('skus.pdf_col_mae'), t('skus.pdf_col_rmse'), t('skus.pdf_col_wape'), t('skus.pdf_col_bias')]
        const colW = contentW / cols.length
        doc.setFillColor(129, 140, 248); doc.rect(margin, y, contentW, 7, 'F')
        doc.setFontSize(8); doc.setTextColor(255, 255, 255)
        cols.forEach((c, i) => doc.text(c, margin + i * colW + 2, y + 5))
        y += 7
        // Same order as the screen: by the metric the champion was chosen with.
        const pdfRank = makeChampionRank(data.metrics)
        const sorted = [...data.metrics].sort((a, b) =>
          (pdfRank(a) ?? Infinity) - (pdfRank(b) ?? Infinity))
        sorted.forEach((r, ri) => {
          const even = ri % 2 === 0
          doc.setFillColor(even ? 248 : 255, even ? 250 : 255, even ? 252 : 255)
          doc.rect(margin, y, contentW, 6, 'F')
          doc.setFontSize(7.5); doc.setTextColor(30, 41, 59)
          const vals = [modelLabel(t, r.model), r.type ?? '', fmt(r.cost_horizon ?? r.cost ?? null), fmt(r.mae), fmt(r.rmse), r.wape != null ? pct(r.wape) : '—', fmt(r.bias)]
          vals.forEach((v, i) => doc.text(v, margin + i * colW + 2, y + 4))
          y += 6
        })
      }

      // Footer
      doc.setFillColor(17, 19, 31); doc.rect(0, pageH - 10, pageW, 10, 'F')
      doc.setFontSize(7); doc.setTextColor(100, 116, 139)
      doc.text(`${t('skus.pdf_footer_title')}  ·  ${new Date().toLocaleDateString()}`, margin, pageH - 3.5)

      doc.save(`forecast_${sku}.pdf`)
      setShowExportMenu(false)
    })
  }, [sku, data, sessionId, isDark, t])

  const exportExcel = useCallback(() => {
    if (!data) return
    const forecastRows: (string | number | null)[][] = [
      ['date', 'historical', 'forecast_p50', 'lower', 'upper'],
      ...data.historical.map(p => [p.date, p.value, null, null, null] as (string | number | null)[]),
      ...data.forecast.map(p => {
        const fp = p as unknown as Record<string, number | null | undefined>
        return [p.date, null, p.value, fp['lower'] ?? fp['q10'] ?? null, fp['upper'] ?? fp['q90'] ?? null] as (string | number | null)[]
      }),
    ]
    const metricRows: (string | number | null)[][] = [
      ['model', 'type', 'cost', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
      ...data.metrics.map(r => [modelLabel(t, r.model), r.type, r.cost_horizon ?? r.cost ?? null,
                                r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null] as (string | number | null)[]),
    ]
    // The workbook is opened by the user, so its sheet names and the Summary
    // sheet's row labels are copy, not field names — they go through i18n.
    const summaryRows: (string | number | null)[][] = [
      [t('skus.xls_metric'), t('skus.xls_value')],
      ['SKU', sku],
      [t('skus.xls_model'), modelLabel(t, data.model)],
      [t('skus.xls_granularity'), granularityLabel(t, data.applied_granularity)],
      [t('skus.xls_historical_points'), data.historical.length],
      [t('skus.xls_forecast_steps'), data.forecast.length],
      ...(data.stats ? [
        [t('skus.xls_mean'),   data.stats.mean],
        [t('skus.xls_std'),    data.stats.std],
        [t('skus.xls_min'),    data.stats.min],
        [t('skus.xls_max'),    data.stats.max],
        [t('skus.xls_median'), data.stats.median],
        [t('skus.xls_n'),      data.stats.n],
      ] as (string | number | null)[][] : []),
    ]
    downloadWorkbook(`forecast_${sku}_${data.applied_granularity}.xlsx`, [
      { name: t('skus.xls_sheet_forecast'), rows: forecastRows },
      { name: t('skus.xls_sheet_metrics'),  rows: metricRows },
      { name: t('skus.xls_sheet_summary'),  rows: summaryRows },
    ]).then(() => setShowExportMenu(false))
  }, [sku, data, t])

  const toggleModel = (m: string) => {
    setSelModels(prev => prev.includes(m)
      ? (prev.length > 1 ? prev.filter(x => x !== m) : prev)   // keep at least one selected
      : [...prev, m])
  }

  // Stable per-model overlay color, indexed by the model's position in
  // available_models so it doesn't shift as the selection changes.
  const overlayColor = useCallback((m: string) => {
    const idx = data?.available_models.indexOf(m) ?? -1
    return OVERLAY_COLORS[(idx >= 0 ? idx : 0) % OVERLAY_COLORS.length]
  }, [data])

  const hasQuantiles = useMemo(() => {
    if (!data?.forecast.length) return false
    const fp = data.forecast[0]
    const rec = fp as unknown as Record<string, unknown>
    return typeof fp.lower === 'number' || typeof fp.upper === 'number' ||
      Object.keys(rec).some(k => k.startsWith('q') && typeof rec[k] === 'number')
  }, [data])

  const gaps = useMemo(() => {
    if (!data?.historical.length) return []
    return detectGaps(data.historical, data.applied_granularity)
  }, [data])

  const outliers = useMemo(() => {
    if (!data?.historical.length) return []
    return detectOutliers(data.historical)
  }, [data])

  const overlayList = useMemo<ModelOverlay[]>(() =>
    selModels.slice(1)
      .map(m => ({ model: m, color: overlayColor(m), forecast: overlays[m]?.forecast ?? [] }))
      .filter(o => o.forecast.length > 0)
  , [selModels, overlays, overlayColor])

  // The confidence band only applies when exactly one model is shown —
  // otherwise it would be ambiguous which model it belongs to.
  const singleModel = selModels.length <= 1

  const option = useMemo(() => {
    if (!data) return {}
    return buildChartOption(data, chartType, showBand && singleModel, isDark, gaps, outliers, t, overlayList, accent, narrow)
  }, [data, chartType, showBand, singleModel, isDark, gaps, outliers, t, overlayList, accent, narrow])

  if (loading && !data) return (
    <div style={{ flex: 1, padding: '16px', minHeight: 360 }} role="status" aria-busy="true">
      <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 10 }}>{t('skus.loading_label')}</div>
      <div className="skeleton" style={{ height: 36, width: '60%', marginBottom: 14, borderRadius: 8 }} />
      <div className="skeleton" style={{ height: 280, borderRadius: 10, marginBottom: 14 }} />
      <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'repeat(2, 1fr)' : 'repeat(4, 1fr)', gap: 10, marginBottom: 14 }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="skeleton" style={{ height: 56, borderRadius: 8 }} />
        ))}
      </div>
      <div className="skeleton" style={{ height: 110, borderRadius: 8 }} />
    </div>
  )
  if (error != null && !data) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 240, padding: 24 }}>
      <ErrorState error={error} onRetry={() => fetchData(granularity ?? undefined, selModels[0], true)} />
    </div>
  )
  if (!data) return null

  const validGranularities = data.available_granularities

  return (
    <div style={fullscreen
      // Fullscreen: fixed-inset overlay; the ECharts container resizes with it
      // (echarts-for-react size sensor + the resize nudge effect above).
      ? { position: 'fixed', inset: 0, zIndex: 300, display: 'flex', flexDirection: 'column', background: 'var(--surface)' }
      : { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* Toolbar */}
      <div data-tour={tourAnchor} style={{
        display: 'flex', alignItems: 'center', gap: narrow ? 8 : 10, padding: narrow ? '10px 12px' : '10px 16px',
        flexWrap: 'wrap', borderBottom: '1px solid var(--border)', background: 'var(--surface)',
        ...(fullscreen && narrow ? { paddingTop: 'calc(10px + env(safe-area-inset-top, 0px))' } : {}),
      }}>
        {/* Granularity */}
        <ChipGroup
          tourAnchor={tourAnchor ? 'skus.granularity' : undefined}
          label={t('skus.chip_granularity')}
          value={granularity ?? data.applied_granularity}
          onChange={g => setGranularity(g)}
          touch={narrow}
          options={validGranularities.map(g => ({
            value: g,
            label: GRANULARITY_LABELS[g] ?? g,
            title: granularityLabel(t, g),
          }))}
        />

        {!narrow && <div style={{ width: 1, height: 18, background: 'var(--border)' }} />}

        {/* Chart type */}
        <ChipGroup
          label={t('skus.chip_chart')}
          value={chartType}
          onChange={setChartType}
          touch={narrow}
          options={[
            { value: 'line', label: t('skus.chart_type_line'), icon: <LineChartIcon size={10} /> },
            { value: 'bar',  label: t('skus.chart_type_bar'),  icon: <BarChart2 size={10} /> },
          ]}
        />

        {!narrow && <div style={{ width: 1, height: 18, background: 'var(--border)' }} />}

        {/* Model selection — multi-select chips; each selected model renders
            its own colored series on the same axis. Behind the technical
            toggle: which model produced the curve is not a buyer decision,
            and the champion is already selected by default. */}
        {showTechnical && data.available_models.length > 1 && (
          <div data-tour={tourAnchor ? 'skus.models' : undefined} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('skus.model_label')}</span>
            <div style={{ display: 'flex', gap: 2, background: 'var(--surface-2)', borderRadius: 8, padding: 3, border: '1px solid var(--border)', flexWrap: 'wrap' }}>
              {data.available_models.map(m => {
                const sel = selModels.includes(m)
                const color = m === selModels[0] ? PRIMARY_FORECAST_COLOR : overlayColor(m)
                return (
                  <button
                    key={m}
                    onClick={() => toggleModel(m)}
                    style={{
                      all: 'unset', cursor: 'pointer',
                      padding: narrow ? '0 10px' : '3px 9px', borderRadius: 6,
                      fontSize: narrow ? 13 : 11, fontWeight: 500,
                      display: 'flex', alignItems: 'center', gap: 5,
                      ...(narrow ? { minHeight: 44, boxSizing: 'border-box' as const } : {}),
                      background: sel ? 'var(--surface)' : 'transparent',
                      border: `1px solid ${sel ? color : 'transparent'}`,
                      color: sel ? 'var(--fg)' : 'var(--dim)',
                      transition: 'all 0.12s',
                    }}
                  >
                    <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: sel ? color : 'var(--border)', flexShrink: 0 }} />
                    {modelLabel(t, m)}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Confidence band — only meaningful with a single model selected */}
        {singleModel && (
          <BandToggle
            tourAnchor={tourAnchor ? 'skus.band' : undefined}
            active={showBand} onToggle={() => setShowBand(v => !v)} hasQuantiles={hasQuantiles}
            touch={narrow}
          />
        )}

        {/* Right side controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
          {fetching && <Spinner size={13} />}

          {/* Export dropdown */}
          <div data-tour={tourAnchor ? 'skus.export' : undefined} style={{ position: 'relative' }}>
            <button
              onClick={() => setShowExportMenu(v => !v)}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 4,
                fontSize: narrow ? 13 : 11, color: 'var(--dim)',
                padding: narrow ? '0 12px' : '3px 8px', borderRadius: 6,
                border: '1px solid var(--border)',
                ...(narrow ? { minHeight: 44, boxSizing: 'border-box' as const } : {}),
              }}
            >
              <Download size={11} />
              {t('skus.btn_export')}
              <ChevronDown size={9} />
            </button>
            {showExportMenu && !narrow && (
              <>
                <div
                  onClick={() => setShowExportMenu(false)}
                  style={{ position: 'fixed', inset: 0, zIndex: 99 }}
                />
                <div style={{
                  position: 'absolute', top: 'calc(100% + 4px)', right: 0,
                  zIndex: 100, background: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 8,
                  boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
                  minWidth: 160, overflow: 'hidden',
                }}>
                  {[
                    { label: t('skus.export_csv_chart_data'),  icon: <Download size={11} />, action: () => { exportChartCSV(sku, data); setShowExportMenu(false) } },
                    { label: t('skus.export_png_chart_image'), icon: <Download size={11} />, action: exportPNG },
                    { label: t('skus.export_pdf_full_report'), icon: <Download size={11} />, action: exportPDF },
                    { label: t('skus.export_excel_xlsx'),      icon: <Download size={11} />, action: exportExcel },
                  ].map(item => (
                    <button
                      key={item.label}
                      onClick={item.action}
                      style={{
                        all: 'unset', cursor: 'pointer', display: 'flex',
                        alignItems: 'center', gap: 8, width: '100%',
                        padding: '8px 12px', fontSize: 11, color: 'var(--fg)',
                        boxSizing: 'border-box',
                        borderBottom: '1px solid var(--border)',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    >
                      {item.icon}
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Fullscreen toggle (Escape also exits) */}
          <button
            title={fullscreen ? t('skus.exit_fullscreen_title') : t('skus.fullscreen_title')}
            aria-label={fullscreen ? t('skus.exit_fullscreen_title') : t('skus.fullscreen_title')}
            onClick={() => setFullscreen(v => !v)}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, color: 'var(--dim)',
              padding: '3px 8px', borderRadius: 6,
              border: '1px solid var(--border)',
              ...(narrow ? { width: 44, height: 44, padding: 0, justifyContent: 'center', boxSizing: 'border-box' as const } : {}),
            }}
          >
            {fullscreen ? <X size={narrow ? 16 : 11} /> : <Maximize2 size={narrow ? 16 : 11} />}
          </button>
        </div>
      </div>

      {/* Phones: the export menu is a bottom sheet with full-width rows
          instead of a 160px dropdown anchored to a small button. */}
      {narrow && (
        <BottomSheet open={showExportMenu} onClose={() => setShowExportMenu(false)} title={t('skus.btn_export')}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 8 }}>
            {[
              { label: t('skus.export_csv_chart_data'),  action: () => { exportChartCSV(sku, data); setShowExportMenu(false) } },
              { label: t('skus.export_png_chart_image'), action: exportPNG },
              { label: t('skus.export_pdf_full_report'), action: exportPDF },
              { label: t('skus.export_excel_xlsx'),      action: exportExcel },
            ].map(item => (
              <button
                key={item.label}
                onClick={item.action}
                className="mobile-btn mobile-btn-secondary"
                style={{ justifyContent: 'flex-start', flex: 'none', width: '100%', fontWeight: 600 }}
              >
                <Download size={16} aria-hidden="true" /> {item.label}
              </button>
            ))}
          </div>
        </BottomSheet>
      )}

      {/* Stats strip */}
      <StatsStrip data={data} quality={quality} showTechnical={showTechnical} />

      {/* Chart */}
      <div data-tour={tourAnchor ? 'skus.plot' : undefined} style={narrow && !fullscreen
        // Phones: a fixed height. The page scrolls; the chart does not compete
        // with it for the whole viewport.
        ? { height: 280, padding: '6px 0 0', minWidth: 0 }
        : { flex: 1, minHeight: 300, padding: '8px 0 0' }}>
        {data.historical.length === 0 && data.forecast.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--dim)', fontSize: 13 }}>
            {t('skus.no_series_data')}
          </div>
        ) : (
          <ReactECharts
            option={option}
            style={{ height: '100%', minHeight: narrow && !fullscreen ? 0 : 300, width: '100%' }}
            theme={isDark ? 'dark' : undefined}
            opts={{ renderer: 'canvas' }}
            onChartReady={(inst: any) => { echartsRef.current = inst }}
            notMerge
          />
        )}
      </div>

      {!showTechnical && (
        <BuyerOutlook
          data={data}
          formatDate={makeAxisDateFormatter(data.applied_granularity, data.forecast.map(p => p.date))}
          onSeeOrder={onSeeOrder}
        />
      )}

      {/* Footer info. The run details are the technical view's; the gap and
          outlier notices stay in both, because they change how far the curve
          can be trusted. */}
      <div style={{
        padding: narrow ? '4px 12px 8px' : '4px 16px 8px', display: 'flex', gap: 12,
        fontSize: narrow ? 11 : 10, color: 'var(--dim)',
        ...(narrow ? { flexWrap: 'wrap' as const, rowGap: 4 } : {}),
      }}>
        {showTechnical && (
          <>
            <span>{t('skus.footer_freq')}: <strong>{data.original_freq}</strong></span>
            <span>{t('skus.footer_view')}: <strong>{data.applied_granularity}</strong></span>
            <span>{data.historical.length} {t('skus.footer_historical')} · {data.forecast.length} {t('skus.footer_forecast')}</span>
            {data.model && <span>{t('skus.footer_model')}: <strong>{modelLabel(t, data.model)}</strong></span>}
          </>
        )}
        {gaps.length > 0 && (
          <span style={{ color: '#B7791F', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, background: 'rgba(251,191,36,0.4)', border: '1px dashed rgba(251,191,36,0.7)', borderRadius: 2 }} />
            {gaps.length} {gaps.length > 1 ? t('skus.footer_gaps_detected_plural') : t('skus.footer_gaps_detected_singular')}
          </span>
        )}
        {outliers.length > 0 && (
          <span style={{ color: '#B7791F', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#B7791F' }} />
            {outliers.length} {outliers.length > 1 ? t('skus.footer_outliers_detected_plural') : t('skus.footer_outliers_detected_singular')}
          </span>
        )}
      </div>
    </div>
  )
}

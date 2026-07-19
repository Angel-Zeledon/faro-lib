'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import dynamic from 'next/dynamic'
import {
  getSessions, getMetrics, getInventory, getQuality,
  getSkuIntelligence,
} from '@/lib/api'
import type {
  SessionInfo, MetricRow, InventoryRecommendation, QualityReport,
  SkuIntelligenceData, ForecastPoint, InventorySignal,
} from '@/lib/types'
import { downloadWorkbook } from '@/lib/excel'
import Badge from '@/components/ui/Badge'
import SignalBadge from '@/components/ui/SignalBadge'
import Spinner from '@/components/ui/Spinner'
import Button from '@/components/ui/Button'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  Search, Package, ChevronDown, RefreshCw,
  AlertTriangle, CheckCircle2, TrendingUp, BarChart2,
  LineChart as LineChartIcon, Activity, Layers, Eye, EyeOff, Download, RotateCcw,
  GitCompare, TableProperties, Grid3x3, FileSpreadsheet, Loader2,
} from 'lucide-react'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── Constants ─────────────────────────────────────────────────────────────────

const SERIES_COLOR: Record<string, string> = {
  stable:       '#22c55e',
  seasonal:     '#818cf8',
  volatile:     '#f59e0b',
  intermittent: '#f97316',
  short:        '#06b6d4',
  unknown:      '#64748b',
}

const ACTION_VARIANT: Record<string, 'danger' | 'warning' | 'success'> = {
  REORDER:   'danger',
  OVERSTOCK: 'warning',
  OK:        'success',
}

// La acción de inventario se mostraba con su valor crudo en inglés
// ("REORDER") y el color como único indicador. Se mapea al semáforo
// compartido, que ya trae icono + etiqueta en español (feature 2.8).
const ACTION_SIGNAL: Record<string, InventorySignal> = {
  REORDER:   'PEDIR_YA',
  OVERSTOCK: 'SOBRESTOCK',
  OK:        'OK',
}

function ActionBadge({ action, size }: { action: string; size?: 'sm' | 'md' }) {
  return <SignalBadge signal={ACTION_SIGNAL[action] ?? 'SIN_DATOS'} size={size} />
}

const GRANULARITY_LABELS: Record<string, string> = {
  daily:     'D',
  weekly:    'W',
  monthly:   'M',
  quarterly: 'Q',
  yearly:    'Y',
}

const GRANULARITY_FULL: Record<string, string> = {
  daily:     'Daily',
  weekly:    'Weekly',
  monthly:   'Monthly',
  quarterly: 'Quarterly',
  yearly:    'Yearly',
}

interface CIBand {
  key:    string
  lower:  string
  upper:  string
  label:  string
  color:  string
  opacity: number
}

const CI_BANDS: CIBand[] = [
  { key: 'p5p95',  lower: 'q5',  upper: 'q95', label: 'P5–P95',  color: '#818cf8', opacity: 0.07 },
  { key: 'p10p90', lower: 'q10', upper: 'q90', label: 'P10–P90', color: '#818cf8', opacity: 0.11 },
  { key: 'p20p80', lower: 'q20', upper: 'q80', label: 'P20–P80', color: '#22c55e', opacity: 0.14 },
  { key: 'p25p75', lower: 'q25', upper: 'q75', label: 'P25–P75', color: '#22c55e', opacity: 0.20 },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(n: number | null | undefined) {
  if (n == null || isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}
function fmt(n: number | null | undefined, d = 2) {
  if (n == null || isNaN(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
}
function fmtK(n: number | null | undefined) {
  if (n == null || isNaN(n)) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
  return n.toFixed(1)
}

// ── CSV Export ────────────────────────────────────────────────────────────────

function downloadCSV(filename: string, headers: string[], rows: (string | number | null)[][]) {
  const esc = (v: string | number | null) => {
    if (v == null) return ''
    const s = String(v)
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [headers.map(esc).join(','), ...rows.map(r => r.map(esc).join(','))]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

function exportMetricsCSV(sku: string, rows: MetricRow[]) {
  downloadCSV(
    `metrics_${sku}.csv`,
    ['model', 'type', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
    rows.map(r => [r.model, r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
  )
}

function detectGaps(
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

function exportChartCSV(sku: string, data: SkuIntelligenceData) {
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

function exportMetricsExcel(sku: string, rows: MetricRow[]) {
  downloadWorkbook(`metrics_${sku}.xlsx`, [{
    name: 'Metrics',
    rows: [
      ['model', 'type', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
      ...rows.map(r => [r.model, r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
    ],
  }])
}

// ── Outlier detection ─────────────────────────────────────────────────────────

function detectOutliers(points: { date: string; value: number }[]): number[] {
  if (points.length < 6) return []
  const sorted = [...points].sort((a, b) => a.value - b.value)
  const q1  = sorted[Math.floor(sorted.length * 0.25)].value
  const q3  = sorted[Math.floor(sorted.length * 0.75)].value
  const iqr = q3 - q1
  const lo  = q1 - 1.5 * iqr
  const hi  = q3 + 1.5 * iqr
  return points.reduce<number[]>((acc, p, i) => {
    if (p.value < lo || p.value > hi) acc.push(i)
    return acc
  }, [])
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ values, color = '#818cf8', width = 80, height = 28 }: {
  values: number[]; color?: string; width?: number; height?: number
}) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values)
  const range = max - min || 1
  const xs = values.map((_, i) => (i / (values.length - 1)) * width)
  const ys = values.map(v => height - ((v - min) / range) * (height - 2) - 1)
  const d = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ── Session selector ──────────────────────────────────────────────────────────

function SessionSelector({ sessions, selected, onSelect }: {
  sessions: SessionInfo[]; selected: string | null; onSelect: (id: string) => void
}) {
  const { t } = useLanguage()
  const trained = sessions.filter(s => s.status === 'COMPLETED')
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{t('skus.session_label')}</span>
      <div style={{ position: 'relative' }}>
        <select
          className="form-select"
          value={selected ?? ''}
          onChange={e => onSelect(e.target.value)}
          style={{ paddingRight: 32, minWidth: 220 }}
        >
          <option value="" disabled>{t('skus.select_trained_session')}</option>
          {trained.map(s => <option key={s.session_id} value={s.session_id}>{s.name}</option>)}
        </select>
        <ChevronDown size={12} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--dim)' }} />
      </div>
    </div>
  )
}

// ── SKU card ──────────────────────────────────────────────────────────────────

function SkuCard({ sku, quality, metrics, inventory, selected, onClick }: {
  sku: string
  quality?: QualityReport[string]
  metrics: MetricRow[]
  inventory?: InventoryRecommendation
  selected: boolean
  onClick: () => void
}) {
  const { t } = useLanguage()
  const best = metrics.reduce<MetricRow | null>((b, r) =>
    r.mae !== null && (b === null || (b.mae !== null && r.mae < b.mae)) ? r : b
  , null)
  const seriesType = quality?.series_type ?? 'unknown'
  const color = SERIES_COLOR[seriesType] ?? SERIES_COLOR.unknown
  const sparkVals = metrics.map(r => r.mae).filter((v): v is number => v !== null)

  return (
    <button
      onClick={onClick}
      style={{
        all: 'unset', cursor: 'pointer', display: 'block', width: '100%',
        padding: '11px 14px',
        background: selected ? 'var(--surface-2)' : 'transparent',
        borderLeft: `3px solid ${selected ? color : 'transparent'}`,
        borderBottom: '1px solid var(--border)',
        transition: 'background 0.12s, border-color 0.12s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <Package size={11} color={color} />
          <span style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {sku}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {sparkVals.length > 0 && <Sparkline values={sparkVals} color={color} width={50} height={22} />}
          {inventory && <ActionBadge action={inventory.action} />}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5 }}>
        <span style={{ fontSize: 10, fontWeight: 500, color, background: color + '18', borderRadius: 4, padding: '1px 5px' }}>
          {seriesType}
        </span>
        {best?.mae !== null && best && (
          <span style={{ fontSize: 10, color: 'var(--dim)' }}>MAE {fmt(best.mae)}</span>
        )}
        {quality && (() => {
          const qs = quality.quality_score
          const reliabilityLabel = qs >= 0.7 ? t('skus.reliability_high') : qs >= 0.45 ? t('skus.reliability_medium') : t('skus.reliability_low')
          const reliabilityColor = qs >= 0.7 ? '#22c55e' : qs >= 0.45 ? '#f59e0b' : '#ef4444'
          return (
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
              background: reliabilityColor + '18', color: reliabilityColor,
              marginLeft: 'auto',
            }}>
              {reliabilityLabel}
            </span>
          )
        })()}
      </div>
    </button>
  )
}

// ── Chip button group ─────────────────────────────────────────────────────────

function ChipGroup<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: string; icon?: React.ReactNode; title?: string }[]
  value: T; onChange: (v: T) => void; label?: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {label && <span style={{ fontSize: 11, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{label}</span>}
      <div style={{ display: 'flex', gap: 2, background: 'var(--surface-2)', borderRadius: 8, padding: 3, border: '1px solid var(--border)' }}>
        {options.map(o => (
          <button
            key={o.value}
            title={o.title ?? o.label}
            onClick={() => onChange(o.value)}
            style={{
              all: 'unset', cursor: 'pointer',
              padding: '3px 9px', borderRadius: 6, fontSize: 11, fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 4,
              background: value === o.value ? 'var(--accent)' : 'transparent',
              color: value === o.value ? '#fff' : 'var(--dim)',
              transition: 'all 0.12s',
            }}
          >
            {o.icon}{o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Stats strip ───────────────────────────────────────────────────────────────

function StatsStrip({ data }: { data: SkuIntelligenceData }) {
  const { t } = useLanguage()
  const { stats, metrics, historical, forecast } = data
  const bestMetric = metrics.reduce<MetricRow | null>((b, r) =>
    r.wape !== null && (b === null || (b.wape !== null && r.wape < b.wape)) ? r : b
  , null)

  const items = [
    { label: t('skus.stat_avg_sales'), value: fmtK(stats?.mean) },
    { label: t('skus.stat_variability'), value: fmtK(stats?.std) },
    { label: t('skus.stat_min'), value: fmtK(stats?.min) },
    { label: t('skus.stat_max'), value: fmtK(stats?.max) },
    { label: t('skus.stat_historical_points'), value: historical.length.toString() },
    { label: t('skus.stat_forecast_steps'), value: forecast.length.toString() },
    { label: t('skus.stat_best_wape'), value: bestMetric?.wape != null ? pct(bestMetric.wape) : '—' },
    { label: t('skus.stat_best_model'), value: bestMetric?.model ?? '—' },
  ]

  return (
    <div style={{
      display: 'flex', gap: 0,
      borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)',
      background: 'var(--surface-2)',
    }}>
      {items.map((item, i) => (
        <div key={item.label} style={{
          flex: 1, padding: '7px 10px', textAlign: 'center',
          borderRight: i < items.length - 1 ? '1px solid var(--border)' : undefined,
        }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg)', lineHeight: 1.2 }}>{item.value}</div>
          <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{item.label}</div>
        </div>
      ))}
    </div>
  )
}

// ── CI Band toggles ───────────────────────────────────────────────────────────

function BandToggles({ active, onChange, hasQuantiles }: {
  active: Set<string>
  onChange: (key: string) => void
  hasQuantiles: boolean
}) {
  const { t } = useLanguage()
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ fontSize: 11, color: 'var(--dim)', whiteSpace: 'nowrap' }}>
        {t('skus.uncertainty_label')}
      </span>
      {CI_BANDS.map(b => (
        <button
          key={b.key}
          title={hasQuantiles ? `${t('skus.toggle_band_prefix')} ${b.label} ${t('skus.toggle_band_suffix')}` : t('skus.no_quantile_data_title')}
          onClick={() => hasQuantiles && onChange(b.key)}
          style={{
            all: 'unset', cursor: hasQuantiles ? 'pointer' : 'default',
            display: 'flex', alignItems: 'center', gap: 3,
            padding: '3px 7px', borderRadius: 6, fontSize: 10, fontWeight: 500,
            border: `1px solid ${active.has(b.key) && hasQuantiles ? b.color : 'var(--border)'}`,
            background: active.has(b.key) && hasQuantiles ? b.color + '22' : 'transparent',
            color: active.has(b.key) && hasQuantiles ? b.color : 'var(--dim)',
            opacity: hasQuantiles ? 1 : 0.45,
            transition: 'all 0.12s',
          }}
        >
          {active.has(b.key) && hasQuantiles ? <Eye size={9} /> : <EyeOff size={9} />}
          {b.label}
        </button>
      ))}
      {!hasQuantiles && (
        <span style={{ fontSize: 10, color: 'var(--dim)', opacity: 0.5 }}>
          {t('skus.no_quantile_data')}
        </span>
      )}
    </div>
  )
}

// ── Forecast chart ────────────────────────────────────────────────────────────

type ChartType = 'line' | 'area' | 'bar'

function buildChartOption(
  data: SkuIntelligenceData,
  chartType: ChartType,
  activeBands: Set<string>,
  showLegend: boolean,
  isDark: boolean,
  gaps: { start: string; end: string }[],
  outlierIndices: number[] = [],
  t: (key: string) => string = (k) => k,
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
  const histColor   = '#818cf8'
  const fcastColor  = '#22c55e'

  // Retrieve a quantile value from a forecast point.
  // Falls back to lower/upper for backends that don't return full quantile sets.
  function getQ(p: ForecastPoint, qKey: string): number | null {
    const rec = p as unknown as Record<string, number | null | undefined>
    const v = rec[qKey]
    if (typeof v === 'number') return v
    // Lower-bound quantiles → use lower (or derive symmetrically from upper)
    if ((qKey === 'q5' || qKey === 'q10' || qKey === 'q20' || qKey === 'q25') && typeof p.lower === 'number') return p.lower
    // Upper-bound quantiles → use upper
    if ((qKey === 'q75' || qKey === 'q80' || qKey === 'q90' || qKey === 'q95') && typeof p.upper === 'number') return p.upper
    return null
  }

  const series: object[] = []

  // ── CI bands (rendered first so the P50 line sits on top) ──────────────────
  const renderedBandLabels: string[] = []

  for (const band of CI_BANDS) {
    if (!activeBands.has(band.key)) continue

    // Build aligned data arrays (null for historical dates = band only in forecast zone)
    const lowerVals: (number | null)[] = [
      ...historical.map(() => null),
      ...forecast.map(p => getQ(p, band.lower)),
    ]
    const fillVals: (number | null)[] = lowerVals.map((lo, i) => {
      if (i < historical.length) return null
      const hi = getQ(forecast[i - historical.length], band.upper)
      if (lo === null || hi === null) return null
      return Math.max(0, hi - lo)
    })

    if (!fillVals.some(v => v !== null)) continue   // no data for this band — skip
    renderedBandLabels.push(band.label)

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
      name:            band.label,
      type:            'line',
      data:            fillVals,
      lineStyle:       { opacity: 0 },
      areaStyle:       { color: band.color, opacity: band.opacity + 0.04 },
      stack:           band.key,
      symbol:          'none',
      legendHoverLink: true,
      tooltip:         { show: false },
    })
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
    if (chartType === 'area') histSeries['areaStyle'] = { color: histColor, opacity: 0.07 }
  }
  if (outlierIndices.length > 0) {
    histSeries['markPoint'] = {
      symbolSize: 8,
      data: outlierIndices.map(i => ({
        coord:     [historical[i].date, historical[i].value],
        itemStyle: { color: '#f59e0b', borderColor: '#fff', borderWidth: 1.5 },
        label:     { show: false },
      })),
      tooltip: {
        formatter: (p: { data: { coord: [string, number] } }) =>
          `<div style="font-size:11px"><b style="color:#f59e0b">${t('skus.outlier_label')}</b><br/>${p.data.coord[0]}: ${p.data.coord[1].toFixed(2)}</div>`,
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
  const fcastData: (number | null)[] = [
    ...historical.map(() => null),
    ...forecast.map(p => p.value),
  ]
  if (historical.length > 0 && forecast.length > 0) {
    fcastData[historical.length - 1] = historical[historical.length - 1].value
  }
  const fcastSeries: Record<string, unknown> = {
    name:       t('skus.series_forecast_p50'),
    data:       fcastData,
    lineStyle:  { color: fcastColor, width: 2.5, type: 'dashed' },
    itemStyle:  { color: fcastColor },
    symbol:     chartType === 'bar' ? undefined : 'circle',
    symbolSize: 4,
    smooth:     true,
    z:          10,
  }
  if (chartType === 'bar') {
    fcastSeries['type'] = 'bar'; fcastSeries['barMaxWidth'] = 12
    fcastSeries['itemStyle'] = { color: fcastColor, opacity: 0.85 }
    delete fcastSeries['lineStyle']; delete fcastSeries['symbol']; delete fcastSeries['smooth']; delete fcastSeries['z']
  } else {
    fcastSeries['type'] = 'line'
    if (chartType === 'area') fcastSeries['areaStyle'] = { color: fcastColor, opacity: 0.06 }
  }
  series.push(fcastSeries)

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
      lines.push(row(dot(fcastColor), t('skus.series_forecast_p50'), fp.value.toFixed(2)))
      for (const band of CI_BANDS) {
        if (!activeBands.has(band.key)) continue
        const lo = getQ(fp, band.lower)
        const hi = getQ(fp, band.upper)
        if (lo === null && hi === null) continue
        const bandDot = `<span style="display:inline-block;width:12px;height:4px;border-radius:2px;background:${band.color};opacity:0.7;flex-shrink:0"></span>`
        const loStr = lo !== null ? lo.toFixed(2) : '—'
        const hiStr = hi !== null ? hi.toFixed(2) : '—'
        lines.push(row(bandDot, band.label, `${loStr} – ${hiStr}`))
      }
    }

    return `<div style="font-size:11px;min-width:170px">
      <div style="margin-bottom:5px;opacity:0.45;font-size:10px">${date}</div>
      ${lines.join('')}
    </div>`
  }

  const legendData = [t('skus.series_historical'), t('skus.series_forecast_p50'), ...renderedBandLabels]

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
    },
    legend: showLegend ? {
      data:      legendData,
      textStyle: { color: dim, fontSize: 11 },
      top:       6,
      right:     80,
      itemWidth: 16,
      itemHeight: 8,
    } : { show: false },
    toolbox: {
      right:    16,
      top:      showLegend ? 4 : 2,
      itemSize: 14,
      itemGap:  8,
      feature: {
        dataZoom: {
          yAxisIndex: 0,
          title:      { zoom: t('skus.toolbox_zoom_area'), back: t('skus.toolbox_undo_zoom') },
          iconStyle:  { color: dim, borderColor: 'transparent' },
          emphasis:   { iconStyle: { color: '#818cf8', borderColor: 'transparent' } },
        },
        restore: {
          title:    t('skus.toolbox_reset_zoom'),
          iconStyle: { color: dim, borderColor: 'transparent' },
          emphasis:  { iconStyle: { color: '#22c55e', borderColor: 'transparent' } },
        },
      },
    },
    grid: { top: showLegend ? 40 : 24, bottom: 56, left: 60, right: 20, containLabel: false },
    xAxis: {
      type:      'category',
      data:      allDates,
      axisLine:  { lineStyle: { color: gridLine } },
      axisTick:  { show: false },
      axisLabel: { color: dim, fontSize: 10, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type:      'value',
      axisLine:  { show: false },
      axisTick:  { show: false },
      axisLabel: { color: dim, fontSize: 10, formatter: (v: number) => fmtK(v) },
      splitLine: { lineStyle: { color: gridLine, type: 'dashed' } },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'inside', yAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: false, moveOnMouseMove: false, zoomLock: false },
      {
        type:            'slider',
        xAxisIndex:      0,
        start: 0, end:   100,
        height:          22,
        bottom:          4,
        handleStyle:     { color: '#818cf8' },
        textStyle:       { color: dim, fontSize: 9 },
        fillerColor:     'rgba(129,140,248,0.08)',
        borderColor:     gridLine,
        backgroundColor: 'transparent',
        dataBackground:  { lineStyle: { color: gridLine }, areaStyle: { color: 'transparent' } },
      },
    ],
    series,
  }
}

// ── Main chart panel ──────────────────────────────────────────────────────────

function ChartPanel({ sessionId, sku, isDark }: {
  sessionId: string; sku: string; isDark: boolean
}) {
  const { t } = useLanguage()
  const [data,        setData]        = useState<SkuIntelligenceData | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [fetching,    setFetching]    = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [chartType,   setChartType]   = useState<ChartType>('line')
  const [granularity, setGranularity] = useState<string | null>(null)
  const [selModel,    setSelModel]    = useState<string | undefined>(undefined)
  const [aggMethod,   setAggMethod]   = useState<'sum' | 'mean'>('sum')
  const [activeBands, setActiveBands] = useState<Set<string>>(new Set(['p10p90', 'p25p75']))
  const [showLegend,     setShowLegend]     = useState(true)
  const [showExportMenu, setShowExportMenu] = useState(false)
  const echartsRef = useRef<any>(null)

  // Cache results by (sku|gran|model|agg) to avoid redundant API calls
  const cache = useRef<Map<string, SkuIntelligenceData>>(new Map())
  const cacheKey = (gran: string | null, model: string | undefined, agg: string) =>
    `${sku}|${gran ?? ''}|${model ?? ''}|${agg}`

  const fetchData = useCallback((gran?: string, model?: string, isInitial = false) => {
    const key = cacheKey(gran ?? null, model, aggMethod)
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
    getSkuIntelligence(sessionId, sku, {
      model:       model,
      granularity: gran ?? undefined,
      agg:         aggMethod,
    })
      .then(d => {
        cache.current.set(key, d)
        // Pre-cache under applied granularity so the follow-up effect is a cache hit
        cache.current.set(cacheKey(d.applied_granularity, model, aggMethod), d)
        setData(d)
        if (!gran) setGranularity(d.applied_granularity)
      })
      .catch(e => setError(e.message))
      .finally(() => { setLoading(false); setFetching(false) })
  }, [sessionId, sku, aggMethod])

  useEffect(() => {
    setData(null)
    setLoading(true)
    setGranularity(null)
    setSelModel(undefined)
    cache.current.clear()
    fetchData(undefined, undefined, true)
  }, [sessionId, sku])

  useEffect(() => {
    if (granularity !== null && data) {
      fetchData(granularity, selModel)
    }
  }, [granularity, selModel, aggMethod])

  const exportPNG = useCallback(() => {
    if (!echartsRef.current) return
    const instance = echartsRef.current
    const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark ? '#0f1015' : '#ffffff', excludeComponents: ['toolbox'] })
    const a = document.createElement('a')
    a.href = url; a.download = `forecast_${sku}.png`; a.click()
    setShowExportMenu(false)
  }, [sku, isDark])

  const exportPDF = useCallback(() => {
    if (!echartsRef.current || !data) return
    const instance = echartsRef.current
    const imgData = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: isDark ? '#0f1015' : '#ffffff', excludeComponents: ['toolbox'] })
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
      doc.text(`${t('skus.pdf_session_label')}: ${sessionId}  ·  ${t('skus.pdf_granularity_label')}: ${data.applied_granularity}  ·  ${t('skus.pdf_model_label')}: ${data.model ?? 'N/A'}`, margin, y)
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
        const cols = [t('skus.pdf_col_model'), t('skus.pdf_col_type'), t('skus.pdf_col_mae'), t('skus.pdf_col_rmse'), t('skus.pdf_col_wape'), t('skus.pdf_col_bias')]
        const colW = contentW / cols.length
        doc.setFillColor(129, 140, 248); doc.rect(margin, y, contentW, 7, 'F')
        doc.setFontSize(8); doc.setTextColor(255, 255, 255)
        cols.forEach((c, i) => doc.text(c, margin + i * colW + 2, y + 5))
        y += 7
        const sorted = [...data.metrics].sort((a, b) => (a.wape ?? Infinity) - (b.wape ?? Infinity))
        sorted.forEach((r, ri) => {
          const even = ri % 2 === 0
          doc.setFillColor(even ? 248 : 255, even ? 250 : 255, even ? 252 : 255)
          doc.rect(margin, y, contentW, 6, 'F')
          doc.setFontSize(7.5); doc.setTextColor(30, 41, 59)
          const vals = [r.model ?? '', r.type ?? '', fmt(r.mae), fmt(r.rmse), r.wape != null ? pct(r.wape) : '—', fmt(r.bias)]
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
      ['model', 'type', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
      ...data.metrics.map(r => [r.model, r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null] as (string | number | null)[]),
    ]
    const summaryRows: (string | number | null)[][] = [
      ['Metric', 'Value'],
      ['SKU', sku],
      ['Model', data.model ?? 'N/A'],
      ['Granularity', data.applied_granularity],
      ['Historical Points', data.historical.length],
      ['Forecast Steps', data.forecast.length],
      ...(data.stats ? [
        ['Mean',   data.stats.mean],
        ['Std Dev', data.stats.std],
        ['Min',    data.stats.min],
        ['Max',    data.stats.max],
        ['Median', data.stats.median],
        ['N',      data.stats.n],
      ] as (string | number | null)[][] : []),
    ]
    downloadWorkbook(`forecast_${sku}_${data.applied_granularity}.xlsx`, [
      { name: 'Forecast', rows: forecastRows },
      { name: 'Metrics',  rows: metricRows },
      { name: 'Summary',  rows: summaryRows },
    ]).then(() => setShowExportMenu(false))
  }, [sku, data])

  const toggleBand = (key: string) => {
    setActiveBands(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

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

  const option = useMemo(() => {
    if (!data) return {}
    return buildChartOption(data, chartType, activeBands, showLegend, isDark, gaps, outliers, t)
  }, [data, chartType, activeBands, showLegend, isDark, gaps, outliers, t])

  if (loading && !data) return (
    <div style={{ flex: 1, padding: '16px', minHeight: 360 }}>
      <div className="skeleton" style={{ height: 36, width: '60%', marginBottom: 14, borderRadius: 8 }} />
      <div className="skeleton" style={{ height: 280, borderRadius: 10, marginBottom: 14 }} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 14 }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="skeleton" style={{ height: 56, borderRadius: 8 }} />
        ))}
      </div>
      <div className="skeleton" style={{ height: 110, borderRadius: 8 }} />
    </div>
  )
  if (error && !data) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 200 }}>
      <span style={{ fontSize: 13, color: '#ef4444' }}>{error}</span>
    </div>
  )
  if (!data) return null

  const validGranularities = data.available_granularities

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
        flexWrap: 'wrap', borderBottom: '1px solid var(--border)', background: 'var(--surface)',
      }}>
        {/* Granularity */}
        <ChipGroup
          label={t('skus.chip_granularity')}
          value={granularity ?? data.applied_granularity}
          onChange={g => setGranularity(g)}
          options={validGranularities.map(g => ({
            value: g,
            label: GRANULARITY_LABELS[g] ?? g,
            title: GRANULARITY_FULL[g] ?? g,
          }))}
        />

        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />

        {/* Chart type */}
        <ChipGroup
          label={t('skus.chip_chart')}
          value={chartType}
          onChange={setChartType}
          options={[
            { value: 'line', label: t('skus.chart_type_line'), icon: <LineChartIcon size={10} /> },
            { value: 'area', label: t('skus.chart_type_area'), icon: <Activity size={10} /> },
            { value: 'bar',  label: t('skus.chart_type_bar'),  icon: <BarChart2 size={10} /> },
          ]}
        />

        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />

        {/* Aggregation */}
        <ChipGroup
          label={t('skus.chip_agg')}
          value={aggMethod}
          onChange={setAggMethod}
          options={[
            { value: 'sum',  label: t('skus.agg_sum') },
            { value: 'mean', label: t('skus.agg_avg') },
          ]}
        />

        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />

        {/* Model selector */}
        {data.available_models.length > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('skus.model_label')}</span>
            <div style={{ position: 'relative' }}>
              <select
                className="form-select"
                value={selModel ?? data.model ?? ''}
                onChange={e => setSelModel(e.target.value)}
                style={{ fontSize: 11, padding: '3px 24px 3px 7px', height: 26 }}
              >
                {data.available_models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <ChevronDown size={10} style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--dim)' }} />
            </div>
          </div>
        )}

        {/* CI bands */}
        <BandToggles active={activeBands} onChange={toggleBand} hasQuantiles={hasQuantiles} />

        {/* Right side controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
          {fetching && <Spinner size={13} />}

          {/* Reset zoom */}
          <button
            title={t('skus.reset_zoom_title')}
            onClick={() => echartsRef.current?.dispatchAction({ type: 'restore' })}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, color: 'var(--dim)',
              padding: '3px 8px', borderRadius: 6,
              border: '1px solid var(--border)',
            }}
          >
            <RotateCcw size={11} />
            {t('skus.btn_reset')}
          </button>

          {/* Export dropdown */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setShowExportMenu(v => !v)}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 4,
                fontSize: 11, color: 'var(--dim)',
                padding: '3px 8px', borderRadius: 6,
                border: '1px solid var(--border)',
              }}
            >
              <Download size={11} />
              {t('skus.btn_export')}
              <ChevronDown size={9} />
            </button>
            {showExportMenu && (
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

          <button
            title={t('skus.toggle_legend_title')}
            onClick={() => setShowLegend(v => !v)}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, color: showLegend ? 'var(--accent)' : 'var(--dim)',
            }}
          >
            <Layers size={12} />
            {t('skus.legend_label')}
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <StatsStrip data={data} />

      {/* Chart */}
      <div style={{ flex: 1, minHeight: 0, padding: '8px 0 0' }}>
        {data.historical.length === 0 && data.forecast.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--dim)', fontSize: 13 }}>
            {t('skus.no_series_data')}
          </div>
        ) : (
          <ReactECharts
            option={option}
            style={{ height: '100%', minHeight: 300, width: '100%' }}
            theme={isDark ? 'dark' : undefined}
            opts={{ renderer: 'canvas' }}
            onChartReady={(inst: any) => { echartsRef.current = inst }}
            notMerge
          />
        )}
      </div>

      {/* Footer info */}
      <div style={{ padding: '4px 16px 8px', display: 'flex', gap: 12, fontSize: 10, color: 'var(--dim)' }}>
        <span>{t('skus.footer_freq')}: <strong>{data.original_freq}</strong></span>
        <span>{t('skus.footer_view')}: <strong>{data.applied_granularity}</strong></span>
        <span>{data.historical.length} {t('skus.footer_historical')} · {data.forecast.length} {t('skus.footer_forecast')}</span>
        {data.model && <span>{t('skus.footer_model')}: <strong>{data.model}</strong></span>}
        {gaps.length > 0 && (
          <span style={{ color: '#f59e0b', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, background: 'rgba(251,191,36,0.4)', border: '1px dashed rgba(251,191,36,0.7)', borderRadius: 2 }} />
            {gaps.length} {gaps.length > 1 ? t('skus.footer_gaps_detected_plural') : t('skus.footer_gaps_detected_singular')}
          </span>
        )}
        {outliers.length > 0 && (
          <span style={{ color: '#f59e0b', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f59e0b' }} />
            {outliers.length} {outliers.length > 1 ? t('skus.footer_outliers_detected_plural') : t('skus.footer_outliers_detected_singular')}
          </span>
        )}
      </div>
    </div>
  )
}

// ── Metrics table ─────────────────────────────────────────────────────────────

type MetricViewMode = 'table' | 'heatmap'

function heatCell(val: number | null, min: number, max: number, lowerIsBetter = true): string {
  if (val == null || min === max) return 'transparent'
  const t = (val - min) / (max - min) // 0=low, 1=high
  const bad = lowerIsBetter ? t : 1 - t // 0=good, 1=bad
  const r = Math.round(34  + (239 - 34)  * bad)
  const g = Math.round(197 + (68  - 197) * bad)
  const b = Math.round(94  + (68  - 94)  * bad)
  return `rgba(${r},${g},${b},0.22)`
}

function MetricsTable({ rows, sku }: { rows: MetricRow[]; sku: string }) {
  const { t } = useLanguage()
  const [viewMode, setViewMode] = useState<MetricViewMode>('table')

  if (!rows.length) return (
    <div style={{ padding: '24px', textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>{t('skus.no_metrics')}</div>
  )

  const sorted = [...rows].sort((a, b) => (a.wape ?? Infinity) - (b.wape ?? Infinity))
  const best   = sorted[0]

  const numVals = (col: keyof MetricRow) =>
    sorted.map(r => r[col]).filter((v): v is number => typeof v === 'number')

  const stats = {
    mae:  { min: Math.min(...numVals('mae')),  max: Math.max(...numVals('mae'))  },
    rmse: { min: Math.min(...numVals('rmse')), max: Math.max(...numVals('rmse')) },
    wape: { min: Math.min(...numVals('wape')), max: Math.max(...numVals('wape')) },
    bias: { min: Math.min(...numVals('bias')), max: Math.max(...numVals('bias')) },
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontSize: 11, color: 'var(--dim)' }}>
          {rows.length} {rows.length !== 1 ? t('skus.models_evaluated_plural') : t('skus.models_evaluated_singular')}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {/* View toggle */}
          <div style={{ display: 'flex', background: 'var(--surface-2)', borderRadius: 6, padding: 2, border: '1px solid var(--border)', gap: 1 }}>
            {(['table', 'heatmap'] as MetricViewMode[]).map(m => (
              <button
                key={m}
                title={m === 'table' ? t('skus.table_view_title') : t('skus.heatmap_view_title')}
                onClick={() => setViewMode(m)}
                style={{
                  all: 'unset', cursor: 'pointer',
                  padding: '3px 7px', borderRadius: 4, fontSize: 11,
                  display: 'flex', alignItems: 'center', gap: 3,
                  background: viewMode === m ? 'var(--accent)' : 'transparent',
                  color: viewMode === m ? '#fff' : 'var(--dim)',
                  transition: 'all 0.12s',
                }}
              >
                {m === 'table' ? <TableProperties size={11} /> : <Grid3x3 size={11} />}
                {m === 'table' ? t('skus.view_table') : t('skus.view_heatmap')}
              </button>
            ))}
          </div>
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsCSV(sku, sorted)}>{t('skus.export_csv_short')}</Button>
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsExcel(sku, sorted)}>{t('skus.export_excel_short')}</Button>
        </div>
      </div>

      <div style={{ overflow: 'auto', flex: 1 }}>
        {viewMode === 'heatmap' ? (
          <div style={{ padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
              {t('skus.color_scale_label')}: <span style={{ color: '#22c55e' }}>{t('skus.color_scale_green')}</span> → <span style={{ color: '#ef4444' }}>{t('skus.color_scale_red')}</span>
            </div>
            <table className="data-table" style={{ tableLayout: 'fixed' }}>
              <thead>
                <tr>
                  <th style={{ width: '20%' }}>{t('skus.col_model')}</th>
                  <th style={{ width: '10%' }}>{t('skus.col_type')}</th>
                  <th style={{ width: '15%' }}>{t('skus.col_mae')}</th>
                  <th style={{ width: '15%' }}>{t('skus.col_rmse')}</th>
                  <th style={{ width: '15%' }}>{t('skus.col_wape')}</th>
                  <th style={{ width: '15%' }}>{t('skus.col_bias')}</th>
                  <th style={{ width: '10%' }}>{t('skus.col_folds')}</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: r === best ? 600 : 400 }}>
                      {r.model}
                      {r === best && <span style={{ fontSize: 9, color: '#818cf8', marginLeft: 5 }}>{t('skus.badge_best')}</span>}
                    </td>
                    <td><span style={{ fontSize: 11, color: 'var(--dim)' }}>{r.type}</span></td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.mae,  stats.mae.min,  stats.mae.max),  borderRadius: 4 }}>{fmt(r.mae)}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.rmse, stats.rmse.min, stats.rmse.max), borderRadius: 4 }}>{fmt(r.rmse)}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.wape, stats.wape.min, stats.wape.max), borderRadius: 4 }}>{r.wape !== null ? pct(r.wape) : '—'}</td>
                    <td style={{ fontFamily: 'monospace', background: heatCell(r.bias != null ? Math.abs(r.bias) : null, 0, Math.max(...numVals('bias').map(Math.abs))), borderRadius: 4, color: r.bias !== null && r.bias > 0 ? '#f59e0b' : '#22c55e' }}>{fmt(r.bias)}</td>
                    <td style={{ color: 'var(--dim)' }}>{r.n_folds ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr><th>{t('skus.col_model')}</th><th>{t('skus.col_type')}</th><th>{t('skus.col_mae')}</th><th>{t('skus.col_rmse')}</th><th>{t('skus.col_wape')}</th><th>{t('skus.col_bias')}</th><th>{t('skus.col_folds')}</th></tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i} style={{ background: r === best ? 'rgba(129,140,248,0.06)' : undefined }}>
                  <td style={{ fontWeight: r === best ? 600 : 400 }}>
                    {r.model}{r === best && <span style={{ fontSize: 9, color: '#818cf8', marginLeft: 6 }}>{t('skus.badge_best')}</span>}
                  </td>
                  <td><span style={{ fontSize: 11, color: 'var(--dim)' }}>{r.type}</span></td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(r.mae)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(r.rmse)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{r.wape !== null ? pct(r.wape) : '—'}</td>
                  <td style={{ fontFamily: 'monospace', color: r.bias !== null && r.bias > 0 ? '#f59e0b' : '#22c55e' }}>{fmt(r.bias)}</td>
                  <td style={{ color: 'var(--dim)' }}>{r.n_folds ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Quality panel ─────────────────────────────────────────────────────────────

function QualityPanel({ q }: { q: QualityReport[string] }) {
  const { t } = useLanguage()
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
      {q.warnings?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {q.warnings.map((w, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: '#f59e0b' }}>
              <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />{w}
            </div>
          ))}
        </div>
      )}
      {q.is_valid && !q.warnings?.length && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#22c55e' }}>
          <CheckCircle2 size={12} /> {t('skus.series_clean_no_warnings')}
        </div>
      )}
    </div>
  )
}

// ── Inventory panel ───────────────────────────────────────────────────────────

function InventoryPanel({ inv }: { inv: InventoryRecommendation }) {
  const { t } = useLanguage()
  const fmtNum = (n: number | null | undefined, d = 0) => n != null ? n.toFixed(d) : '—'
  const cards = [
    { label: t('skus.inv_reorder_point'), value: fmtNum(inv.reorder_point),  color: '#818cf8' },
    { label: t('skus.inv_safety_stock'),  value: fmtNum(inv.safety_stock),   color: '#06b6d4' },
    { label: t('skus.inv_stockout_risk'), value: pct(inv.stockout_risk),      color: (inv.stockout_risk ?? 0) > 0.2 ? '#ef4444' : '#22c55e' },
    inv.holding_cost != null
      ? { label: t('skus.inv_holding_cost'),  value: `$${fmtNum(inv.holding_cost, 2)}`, color: '#f59e0b' }
      : { label: t('skus.inv_days_coverage'), value: fmtNum(inv.days_coverage),         color: '#f59e0b' },
  ]
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
        {cards.map(({ label, value, color }) => (
          <div key={label} style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '12px 14px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{
        padding: '10px 14px', borderRadius: 8,
        background: ACTION_VARIANT[inv.action] === 'danger'  ? 'rgba(239,68,68,0.08)'
                  : ACTION_VARIANT[inv.action] === 'warning' ? 'rgba(245,158,11,0.08)'
                  : 'rgba(34,197,94,0.08)',
        border: `1px solid ${
          ACTION_VARIANT[inv.action] === 'danger'  ? 'rgba(239,68,68,0.2)'
          : ACTION_VARIANT[inv.action] === 'warning' ? 'rgba(245,158,11,0.2)'
          : 'rgba(34,197,94,0.2)'
        }`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <ActionBadge action={inv.action} size="md" />
        <span style={{ fontSize: 12 }}>
          {inv.action === 'REORDER'   && t('skus.action_reorder_msg')}
          {inv.action === 'OVERSTOCK' && t('skus.action_overstock_msg')}
          {inv.action === 'OK'        && t('skus.action_ok_msg')}
        </span>
      </div>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ message }: { message: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--dim)', minHeight: 200 }}>
      <Package size={36} strokeWidth={1} style={{ opacity: 0.3 }} />
      <span style={{ fontSize: 13 }}>{message}</span>
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function TabBar({ tabs, active, onChange, labelFor }: { tabs: string[]; active: string; onChange: (tab: string) => void; labelFor?: (tab: string) => string }) {
  return (
    <div style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)', padding: '0 16px', background: 'var(--surface)' }}>
      {tabs.map(tabKey => (
        <button
          key={tabKey}
          onClick={() => onChange(tabKey)}
          style={{
            all: 'unset', cursor: 'pointer',
            padding: '9px 12px', fontSize: 12, fontWeight: 500,
            color: tabKey === active ? 'var(--accent)' : 'var(--dim)',
            borderBottom: `2px solid ${tabKey === active ? 'var(--accent)' : 'transparent'}`,
            marginBottom: -1, transition: 'all 0.12s',
          }}
        >
          {labelFor ? labelFor(tabKey) : tabKey}
        </button>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SkusPage() {
  const { t } = useLanguage()
  const { advancedMode } = useBusinessProfile()
  const [sessions,       setSessions]       = useState<SessionInfo[]>([])
  const [sessionId,      setSessionId]      = useState<string | null>(null)
  const [metrics,        setMetrics]        = useState<MetricRow[]>([])
  const [inventory,      setInventory]      = useState<InventoryRecommendation[]>([])
  const [quality,        setQuality]        = useState<QualityReport>({})
  const [loading,        setLoading]        = useState(false)
  const [search,         setSearch]         = useState('')
  const [selectedSku,    setSelectedSku]    = useState<string | null>(null)
  const [tab,            setTab]            = useState('Forecast')
  const [sessLoading,    setSessLoading]    = useState(true)
  const [sessError,      setSessError]      = useState<string | null>(null)
  const [loadError,      setLoadError]      = useState<string | null>(null)
  const [isDark,         setIsDark]         = useState(true)
  const [showSkuStats,   setShowSkuStats]   = useState(false)
  // Compare mode
  const [compareMode,    setCompareMode]    = useState(false)
  const [cmpSessionId,   setCmpSessionId]   = useState<string | null>(null)
  const [cmpMetrics,     setCmpMetrics]     = useState<MetricRow[]>([])
  const [cmpSku,         setCmpSku]         = useState<string | null>(null)
  const [cmpLoading,     setCmpLoading]     = useState(false)
  const [cmpError,       setCmpError]       = useState<string | null>(null)
  // Bulk export
  const [bulkExporting,  setBulkExporting]  = useState(false)
  const [bulkProgress,   setBulkProgress]   = useState(0)
  const [bulkFailed,     setBulkFailed]     = useState<string[]>([])

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    setIsDark(document.documentElement.dataset.theme !== 'light')
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.dataset.theme !== 'light')
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    getSessions()
      .then(s => { setSessions(s); setSessLoading(false) })
      .catch((e: unknown) => {
        setSessError(e instanceof Error ? e.message : t('skus.err_sessions_load_failed'))
        setSessLoading(false)
      })
  }, [t])

  useEffect(() => {
    if (!sessionId) return
    setLoading(true)
    setLoadError(null)
    setSelectedSku(null)
    const failedParts: string[] = []
    Promise.all([
      getMetrics(sessionId).catch(() => { failedParts.push(t('skus.part_metrics')); return { rows: [], by_model: {} } }),
      getInventory(sessionId).catch(() => { failedParts.push(t('skus.part_inventory')); return { recommendations: [] } }),
      getQuality(sessionId).catch(() => { failedParts.push(t('skus.part_data_quality')); return {} }),
    ]).then(([m, inv, q]) => {
      setMetrics(m.rows)
      setInventory(inv.recommendations)
      setQuality(q as QualityReport)
      const skus = Array.from(new Set(m.rows.map(r => r.sku).filter(Boolean) as string[]))
      if (skus.length) setSelectedSku(skus[0])
      if (failedParts.length) {
        setLoadError(`${t('skus.err_load_failed_prefix')}: ${failedParts.join(', ')}. ${t('skus.err_load_failed_suffix')}`)
      }
    }).finally(() => setLoading(false))
  }, [sessionId, t])

  const skus = useMemo(() =>
    Array.from(new Set(metrics.map(r => r.sku).filter(Boolean) as string[]))
      .filter(s => s.toLowerCase().includes(search.toLowerCase()))
  , [metrics, search])

  const cmpSkus = useMemo(() =>
    Array.from(new Set(cmpMetrics.map(r => r.sku).filter(Boolean) as string[]))
  , [cmpMetrics])

  const skuMetrics   = useMemo(() => metrics.filter(r => r.sku === selectedSku), [metrics, selectedSku])
  const skuInventory = useMemo(() => inventory.find(r => r.sku === selectedSku), [inventory, selectedSku])
  const skuQuality   = useMemo(() => selectedSku ? quality[selectedSku] : undefined, [quality, selectedSku])
  const seriesType   = skuQuality?.series_type ?? 'unknown'
  const skuColor     = SERIES_COLOR[seriesType] ?? SERIES_COLOR.unknown

  // Load compare session metrics
  useEffect(() => {
    if (!cmpSessionId) { setCmpMetrics([]); setCmpSku(null); setCmpError(null); return }
    setCmpLoading(true)
    setCmpError(null)
    getMetrics(cmpSessionId)
      .then(m => {
        setCmpMetrics(m.rows)
        const first = Array.from(new Set(m.rows.map((r: MetricRow) => r.sku).filter(Boolean) as string[]))[0]
        setCmpSku(first ?? null)
      })
      .catch((e: { status?: number } & Error) => {
        const msg = e?.status === 404 ? t('skus.err_session_not_found')
          : e?.status === 403 ? t('skus.err_access_denied')
          : t('skus.err_load_session_metrics')
        setCmpError(msg)
        setCmpMetrics([])
      })
      .finally(() => setCmpLoading(false))
  }, [cmpSessionId, t])

  // Bulk export all SKUs
  const handleBulkExport = useCallback(async () => {
    if (!sessionId || !skus.length) return
    setBulkExporting(true)
    setBulkProgress(0)
    setBulkFailed([])
    const failed: string[] = []
    try {
      const sheets: { name: string; rows: (string | number | null)[][] }[] = []
      for (let i = 0; i < skus.length; i++) {
        const sku = skus[i]
        try {
          const d = await getSkuIntelligence(sessionId, sku, {})
          const rows: (string | number | null)[][] = [
            ['date', 'historical', 'forecast_p50', 'lower', 'upper'],
            ...d.historical.map(p => [p.date, p.value, null, null, null] as (string | number | null)[]),
            ...d.forecast.map(p => {
              const fp = p as unknown as Record<string, number | null | undefined>
              return [p.date, null, p.value, fp['lower'] ?? fp['q10'] ?? null, fp['upper'] ?? fp['q90'] ?? null] as (string | number | null)[]
            }),
          ]
          sheets.push({ name: sku, rows })
        } catch { failed.push(sku) }
        setBulkProgress(i + 1)
      }
      await downloadWorkbook(`forecast_all_skus_${sessionId.slice(0, 8)}.xlsx`, sheets)
      if (failed.length) setBulkFailed(failed)
    } finally {
      setBulkExporting(false)
    }
  }, [sessionId, skus])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 56px)', animation: 'fadeIn 0.3s ease-out' }}>

      {/* Top toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between', paddingBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <TrendingUp size={15} color="var(--accent)" />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{t('skus.page_title')}</span>
          {skus.length > 0 && (
            <span style={{ fontSize: 11, color: 'var(--dim)', marginLeft: 4 }}>
              {skus.length} {skus.length !== 1 ? t('skus.skus_count_plural') : t('skus.skus_count_singular')}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Bulk export */}
          {sessionId && skus.length > 0 && (
            <button
              onClick={handleBulkExport}
              disabled={bulkExporting}
              title={t('skus.export_all_skus_title')}
              style={{
                all: 'unset', cursor: bulkExporting ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 7, fontSize: 11,
                border: '1px solid var(--border)', color: 'var(--dim)',
                background: 'var(--surface)', opacity: bulkExporting ? 0.7 : 1,
              }}
            >
              {bulkExporting
                ? <><Loader2 size={11} style={{ animation: 'spin 1s linear infinite' }} /> {bulkProgress} / {skus.length} {t('skus.skus_count_plural')}…</>
                : <><FileSpreadsheet size={11} /> {t('skus.btn_export_all_skus')}</>
              }
            </button>
          )}
          {bulkFailed.length > 0 && !bulkExporting && (
            <span style={{ fontSize: 10, color: '#f87171' }} title={bulkFailed.join(', ')}>
              {bulkFailed.length} {bulkFailed.length !== 1 ? t('skus.skus_failed_plural') : t('skus.skus_failed_singular')}
            </span>
          )}

          {/* Compare toggle */}
          {sessionId && (
            <button
              onClick={() => { setCompareMode(v => !v); if (compareMode) setCmpSessionId(null) }}
              title={t('skus.compare_sessions_title')}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 7, fontSize: 11,
                border: `1px solid ${compareMode ? 'var(--accent)' : 'var(--border)'}`,
                color: compareMode ? 'var(--accent)' : 'var(--dim)',
                background: compareMode ? 'rgba(129,140,248,0.08)' : 'var(--surface)',
              }}
            >
              <GitCompare size={11} /> {t('skus.btn_compare')}
            </button>
          )}

          {sessLoading ? <Spinner size={13} /> : (
            <SessionSelector sessions={sessions} selected={sessionId} onSelect={id => { setSessionId(id); setTab('Forecast'); setCompareMode(false) }} />
          )}
          {sessionId && (
            <Button
              variant="ghost" size="sm" icon={<RefreshCw size={12} />}
              onClick={() => { const id = sessionId; setSessionId(null); setTimeout(() => setSessionId(id), 10) }}
            >
              {t('skus.btn_refresh')}
            </Button>
          )}
        </div>
      </div>

      {/* Compare bar */}
      {compareMode && sessionId && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
          background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.2)',
          borderRadius: 8, marginBottom: 12, flexWrap: 'wrap',
        }}>
          <GitCompare size={12} color="var(--accent)" />
          <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600 }}>{t('skus.comparing_with_label')}</span>
          <SessionSelector
            sessions={sessions}
            selected={cmpSessionId}
            onSelect={id => { setCmpSessionId(id); setCmpSku(null) }}
          />
          {cmpSkus.length > 0 && (
            <>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('skus.sku_label')}</span>
              <div style={{ position: 'relative' }}>
                <select
                  className="form-select"
                  value={cmpSku ?? ''}
                  onChange={e => setCmpSku(e.target.value)}
                  style={{ paddingRight: 28, minWidth: 140, fontSize: 11, height: 28 }}
                >
                  <option value="" disabled>{t('skus.select_sku_placeholder')}</option>
                  {cmpSkus.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <ChevronDown size={10} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--dim)' }} />
              </div>
            </>
          )}
          {cmpLoading && <Spinner size={12} />}
          {cmpError && (
            <span style={{ fontSize: 11, color: '#f87171' }}>⚠ {cmpError}</span>
          )}
        </div>
      )}

      {(sessError || loadError) && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', marginBottom: 12,
          borderRadius: 8, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
          fontSize: 12, color: '#f87171',
        }}>
          <AlertTriangle size={14} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{sessError || loadError}</span>
        </div>
      )}

      {/* Body */}
      <div style={{ display: 'grid', gridTemplateColumns: '230px 1fr', gap: 16, flex: 1, minHeight: 0 }}>

        {/* SKU list */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column',
        }}>
          <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ position: 'relative' }}>
              <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--dim)' }} />
              <input
                type="text"
                placeholder={t('skus.search_placeholder')}
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="form-input"
                style={{ paddingLeft: 26, fontSize: 12 }}
              />
            </div>
          </div>
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {!sessionId ? (
              <EmptyState message={t('skus.empty_select_trained_session')} />
            ) : loading ? (
              <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
            ) : skus.length === 0 ? (
              <EmptyState message={t('skus.empty_no_skus_found')} />
            ) : (
              skus.map(sku => (
                <SkuCard
                  key={sku}
                  sku={sku}
                  quality={quality[sku]}
                  metrics={metrics.filter(r => r.sku === sku)}
                  inventory={inventory.find(r => r.sku === sku)}
                  selected={sku === selectedSku}
                  onClick={() => { setSelectedSku(sku); setTab('Forecast') }}
                />
              ))
            )}
          </div>
        </div>

        {/* Detail panel */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column',
        }}>
          {!selectedSku ? (
            <EmptyState message={sessionId ? t('skus.empty_select_sku_from_list') : t('skus.empty_no_session_selected')} />
          ) : (
            <>
              {/* SKU header */}
              <div style={{
                padding: '14px 16px', borderBottom: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{
                    width: 30, height: 30, borderRadius: 8,
                    background: skuColor + '18',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Package size={13} color={skuColor} />
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700 }}>{selectedSku}</div>
                    <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>
                      {skuQuality ? (
                        <>
                          <span style={{ color: skuColor }}>{skuQuality.series_type}</span>
                          {' · '}
                          {skuQuality.n_rows} {t('skus.rows_label')} · {pct(skuQuality.quality_score)} {t('skus.quality_label_lower')}
                        </>
                      ) : t('skus.no_quality_data')}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {skuInventory && (
                    <ActionBadge action={skuInventory.action} size="md" />
                  )}
                </div>
              </div>

              {/* Business summary card */}
              {skuQuality && (() => {
                const demandTypeMap: Record<string, string> = {
                  stable:       t('skus.demand_stable'),
                  seasonal:     t('skus.demand_seasonal'),
                  volatile:     t('skus.demand_volatile'),
                  intermittent: t('skus.demand_intermittent'),
                  short:        t('skus.demand_short_history'),
                  unknown:      t('skus.demand_volatile'),
                }
                const demandType = demandTypeMap[seriesType] ?? t('skus.demand_volatile')
                const qualityScore = skuQuality.quality_score
                const predictability = qualityScore >= 0.7 ? t('skus.reliability_high') : qualityScore >= 0.45 ? t('skus.reliability_medium') : t('skus.reliability_low')
                const predictabilityDots = predictability === t('skus.reliability_high') ? 4 : predictability === t('skus.reliability_medium') ? 2 : 1
                const predictabilityColor = predictability === t('skus.reliability_high') ? '#22c55e' : predictability === t('skus.reliability_medium') ? '#f59e0b' : '#ef4444'
                const confidenceMsg = qualityScore >= 0.7
                  ? t('skus.confidence_msg_high')
                  : qualityScore >= 0.45
                  ? t('skus.confidence_msg_medium')
                  : t('skus.confidence_msg_low')

                const bestWapeMetric = [...skuMetrics]
                  .filter(r => r.wape !== null)
                  .sort((a, b) => (a.wape ?? Infinity) - (b.wape ?? Infinity))[0]
                const wapeColor = bestWapeMetric?.wape != null
                  ? (bestWapeMetric.wape < 0.2 ? '#22c55e' : bestWapeMetric.wape < 0.35 ? '#f59e0b' : '#ef4444')
                  : skuColor

                return (
                  <div style={{
                    margin: '12px 16px 0',
                    padding: '14px 16px',
                    borderRadius: 10,
                    background: wapeColor + '08',
                    border: `1px solid ${wapeColor}25`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 2 }}>
                          {demandType}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
                          {t('skus.predictability_label')}
                          <span style={{ color: predictabilityColor, fontWeight: 600 }}>{predictability}</span>
                          <span>
                            {[1, 2, 3, 4].map(i => (
                              <span
                                key={i}
                                style={{
                                  display: 'inline-block',
                                  width: 7, height: 7,
                                  borderRadius: '50%',
                                  marginLeft: 2,
                                  background: i <= predictabilityDots ? predictabilityColor : 'var(--border)',
                                }}
                              />
                            ))}
                          </span>
                        </div>
                      </div>
                      {bestWapeMetric?.wape != null && (
                        <div style={{ textAlign: 'right', flexShrink: 0 }}>
                          <div style={{ fontSize: 16, fontWeight: 700, color: wapeColor }}>
                            {Math.round((1 - bestWapeMetric.wape) * 100)}%
                          </div>
                          <div style={{ fontSize: 9, color: 'var(--dim)', marginTop: 1 }}>{t('skus.accuracy_label')}</div>
                        </div>
                      )}
                    </div>
                    <div style={{
                      fontSize: 11, color: 'var(--muted)', lineHeight: 1.6,
                      paddingTop: 10, borderTop: '1px solid var(--border)',
                    }}>
                      {confidenceMsg}
                    </div>
                    {skuQuality.n_outliers > 0 && (
                      <div style={{
                        marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)',
                        fontSize: 11, color: '#f59e0b',
                        display: 'flex', alignItems: 'center', gap: 6,
                      }}>
                        <AlertTriangle size={11} />
                        {skuQuality.n_outliers} {skuQuality.n_outliers !== 1 ? t('skus.outliers_in_history_plural') : t('skus.outliers_in_history_singular')}
                      </div>
                    )}
                  </div>
                )
              })()}

              <TabBar
                tabs={['Forecast', 'Metrics', 'Quality', 'Inventory']}
                active={tab}
                onChange={setTab}
                labelFor={tabKey => ({
                  Forecast: t('skus.tab_forecast'),
                  Metrics: t('skus.tab_metrics'),
                  Quality: t('skus.tab_quality'),
                  Inventory: t('skus.tab_inventory'),
                }[tabKey] ?? tabKey)}
              />

              <div style={{ flex: 1, overflow: tab === 'Forecast' ? 'hidden' : 'auto', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                {tab === 'Forecast' && sessionId && (
                  compareMode && cmpSessionId && cmpSku ? (
                    /* Split compare view */
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                      {/* Top: primary session */}
                      <div style={{ flex: 1, minHeight: 0, borderBottom: '2px solid var(--accent)', position: 'relative' }}>
                        <div style={{
                          position: 'absolute', top: 6, left: 12, zIndex: 5,
                          fontSize: 10, fontWeight: 600, color: 'var(--accent)',
                          background: 'rgba(129,140,248,0.12)', padding: '2px 7px', borderRadius: 4,
                        }}>
                          A · {sessions.find(s => s.session_id === sessionId)?.name ?? sessionId}
                        </div>
                        <ChartPanel key={`${sessionId}-${selectedSku}`} sessionId={sessionId} sku={selectedSku} isDark={isDark} />
                      </div>
                      {/* Bottom: compare session */}
                      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
                        <div style={{
                          position: 'absolute', top: 6, left: 12, zIndex: 5,
                          fontSize: 10, fontWeight: 600, color: '#22c55e',
                          background: 'rgba(34,197,94,0.12)', padding: '2px 7px', borderRadius: 4,
                        }}>
                          B · {sessions.find(s => s.session_id === cmpSessionId)?.name ?? cmpSessionId}
                        </div>
                        <ChartPanel key={`${cmpSessionId}-${cmpSku}`} sessionId={cmpSessionId} sku={cmpSku} isDark={isDark} />
                      </div>
                    </div>
                  ) : (
                    <ChartPanel key={`${sessionId}-${selectedSku}`} sessionId={sessionId} sku={selectedSku} isDark={isDark} />
                  )
                )}
                {tab === 'Metrics' && <MetricsTable rows={skuMetrics} sku={selectedSku} />}
                {tab === 'Quality' && (
                  skuQuality ? (
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      {/* Always-visible quality summary */}
                      <div style={{ padding: '16px 20px 0' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 12 }}>
                          {[
                            { label: t('skus.quality_records'),        value: skuQuality.n_rows },
                            { label: t('skus.quality_outliers'), value: skuQuality.n_outliers },
                            { label: t('skus.quality_missing_data'),  value: pct(skuQuality.missing_pct) },
                          ].map(({ label, value }) => (
                            <div key={label} style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '10px 12px', border: '1px solid var(--border)' }}>
                              <div style={{ fontSize: 18, fontWeight: 700 }}>{value}</div>
                              <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
                            </div>
                          ))}
                        </div>
                        {skuQuality.is_valid && !skuQuality.warnings?.length && (
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#22c55e', marginBottom: 12 }}>
                            <CheckCircle2 size={12} /> {t('skus.series_clean_no_warnings')}
                          </div>
                        )}
                        {skuQuality.warnings?.length > 0 && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
                            {skuQuality.warnings.map((w, i) => (
                              <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: '#f59e0b' }}>
                                <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />{w}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {/* Toggle for full statistical detail */}
                      <div style={{ padding: '0 20px 16px' }}>
                        {!advancedMode && (
                          <button
                            onClick={() => setShowSkuStats(v => !v)}
                            style={{
                              all: 'unset', cursor: 'pointer',
                              display: 'flex', alignItems: 'center', gap: 6,
                              fontSize: 12, color: 'var(--dim)', padding: '8px 0',
                              borderTop: '1px solid var(--border)', width: '100%',
                            }}
                          >
                            <span style={{ fontSize: 10 }}>{showSkuStats ? '▲' : '▼'}</span>
                            {showSkuStats ? t('skus.btn_hide') : t('skus.btn_view')} {t('skus.detailed_stat_analysis')}
                          </button>
                        )}
                        {(advancedMode || showSkuStats) && (
                          <div style={{ marginTop: advancedMode ? 0 : 4 }}>
                            <QualityPanel q={skuQuality} />
                          </div>
                        )}
                      </div>
                    </div>
                  ) : <EmptyState message={t('skus.empty_no_quality_data')} />
                )}
                {tab === 'Inventory' && (
                  skuInventory
                    ? <InventoryPanel inv={skuInventory} />
                    : <div style={{ padding: 20, color: 'var(--dim)', fontSize: 13 }}>
                        {t('skus.no_inventory_recommendations')}
                      </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

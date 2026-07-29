'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import dynamic from 'next/dynamic'
import {
  getSessions, getMetrics, getInventory, getQuality,
  getSkuIntelligence, getInventoryStatus,
} from '@/lib/api'
import type {
  SessionInfo, MetricRow, InventoryRecommendation, QualityReport,
  SkuIntelligenceData, ForecastPoint, InventorySignal,
  InventoryStatusItem, CoverageUnit,
} from '@/lib/types'
import { downloadWorkbook } from '@/lib/excel'
import Badge from '@/components/ui/Badge'
import SignalBadge, { signalColor } from '@/components/ui/SignalBadge'
import Spinner from '@/components/ui/Spinner'
import RunWarningsPanel from '@/components/ui/RunWarningsPanel'
import {
  EmptyState, ErrorState, InlineError, LoadingState, SkeletonTable,
} from '@/components/ui/States'
import Button from '@/components/ui/Button'
import Pagination, { usePage } from '@/components/table/Pagination'
import { usePlanning } from '@/contexts/PlanningContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { granularityLabel, seriesTypeLabel } from '@/lib/enumLabels'
import { coverageUnitShort } from '@/lib/period'
import { formatMoney } from '@/lib/currency'
import {
  Search, Package, ChevronDown, RefreshCw,
  AlertTriangle, CheckCircle2, TrendingUp, BarChart2,
  LineChart as LineChartIcon, Eye, EyeOff, Download,
  GitCompare, TableProperties, Grid3x3, FileSpreadsheet, Loader2,
  Maximize2, X,
} from 'lucide-react'

const ReactECharts = dynamic(() => import('echarts-for-react'), { ssr: false })

// ── Constants ─────────────────────────────────────────────────────────────────

const SERIES_COLOR: Record<string, string> = {
  stable:       '#22c55e',
  seasonal:     'var(--accent)',
  volatile:     '#f59e0b',
  intermittent: '#f97316',
  short:        '#06b6d4',
  unknown:      '#64748b',
}

// Maps the training-time inventory action to the shared semáforo signal, used
// as a fallback when a SKU has no live inventory_stock row. Live stock reports
// its signal directly (feature 2.8 / #7).
const ACTION_SIGNAL: Record<string, InventorySignal> = {
  REORDER:   'PEDIR_YA',
  OVERSTOCK: 'SOBRESTOCK',
  OK:        'OK',
}

// Stable identity, so a SKU with no metric rows does not hand SkuCard a fresh
// array on every render.
const EMPTY_METRICS: MetricRow[] = []

const GRANULARITY_LABELS: Record<string, string> = {
  daily:     'D',
  weekly:    'W',
  monthly:   'M',
  quarterly: 'Q',
  yearly:    'Y',
}

// Full granularity names are localized — see `granularityLabel`.

interface CIBand {
  key:    string
  lower:  string
  upper:  string
  label:  string
  color:  string
  opacity: number
}

// The single confidence band shown on the chart (one on/off toggle in the
// toolbar). Only rendered when exactly one model is selected.
const CI_BAND: CIBand = { key: 'p10p90', lower: 'q10', upper: 'q90', label: 'P10–P90', color: 'var(--accent)', opacity: 0.11 }

// Primary (first selected) model keeps the classic forecast green; additional
// overlaid models get a stable color from this palette, indexed by the model's
// position in available_models so colors don't shift as selection changes.
const PRIMARY_FORECAST_COLOR = '#22c55e'
const OVERLAY_COLORS = ['#f59e0b', '#06b6d4', '#f472b6', '#a78bfa', '#f97316', '#84cc16', '#e879f9', '#fbbf24']

// ── Helpers ───────────────────────────────────────────────────────────────────

// `t` returns the key itself when the catalog has no entry, so a build whose
// copy has not landed yet would print "skus.metrics_table_caption" at the user.
type Translate = (key: string, params?: Record<string, unknown>) => string
function tOr(t: Translate, key: string, fallback: string, params?: Record<string, unknown>): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

// ── Model labels ──────────────────────────────────────────────────────────────
//
// A distributor buys stock; nothing in that job is helped by learning that one
// of these series was fitted by a gradient-boosted tree. Every surface on this
// screen renders a model id through `modelLabel` — the single mapping — so
// "Modelo 3" is the same algorithm on every SKU, in every export and after
// every reload. A per-render or per-SKU numbering would be worse than the
// jargon: the same label would mean two different things on two rows.
//
// This array is the mapping. A model's POSITION here is the number the user
// sees, so entries must only ever be appended — reordering or removing one
// silently renumbers models the user has already learned.
const MODEL_ORDER = ['lightgbm', 'xgboost', 'prophet', 'arima', 'ets', 'croston', 'sarimax', 'lstm']

// Baselines are not one of the candidates: they are the "what if we didn't
// forecast at all" yardstick every trained model has to beat. Giving them a
// number would present them as an option worth picking; naming what they
// actually do explains why they are in the table at all.
// `ensemble` is the engine's per-SKU inverse-MAE blend of the models above
// (pipeline.py `_generate_forecast_df`). It is neither one of the candidates
// nor a yardstick, so a number would misfile it — it is what you get when the
// numbered models are combined, and the label says exactly that.
const NAMED_LABEL_KEYS: Record<string, string> = {
  ensemble:       'skus.model_combined',
  naive:          'skus.model_baseline_last_value',
  seasonal_naive: 'skus.model_baseline_season',
  historical_avg: 'skus.model_baseline_average',
}

function modelLabel(t: Translate, id: string | null | undefined): string {
  if (!id) return '—'
  const key = id.toLowerCase()
  const namedKey = NAMED_LABEL_KEYS[key]
  if (namedKey) return t(namedKey)
  const idx = MODEL_ORDER.indexOf(key)
  // An id outside the list means the engine gained a model this screen has not
  // been told about. A generic label keeps the jargon hidden and is a visible
  // signal to append the id to MODEL_ORDER; minting a number on the fly would
  // be worse, because such a number could not survive the next release.
  if (idx < 0) return t('skus.model_other')
  return t('skus.model_numbered', { n: idx + 1 })
}

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

// Exports carry the SAME neutral labels the screen shows, not the raw
// algorithm ids. The tempting alternative — real names in the file, numbers on
// screen — hands the user a sheet full of words the product never showed them
// and that nobody in support can reconcile with "Modelo 3". The labels are a
// bijection with the ids, so nothing analytical is lost: rows still join and
// group exactly as before. If a technical audience ever needs the algorithm,
// that belongs in its own diagnostic export, not mixed into the user's sheet.
function exportMetricsCSV(t: Translate, sku: string, rows: MetricRow[]) {
  downloadCSV(
    `metrics_${sku}.csv`,
    ['model', 'type', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
    rows.map(r => [modelLabel(t, r.model), r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
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

function exportMetricsExcel(t: Translate, sku: string, rows: MetricRow[]) {
  downloadWorkbook(`metrics_${sku}.xlsx`, [{
    name: 'Metrics',
    rows: [
      ['model', 'type', 'mae', 'rmse', 'wape', 'bias', 'n_folds'],
      ...rows.map(r => [modelLabel(t, r.model), r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null]),
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

function Sparkline({ values, color = 'var(--accent)', width = 80, height = 28 }: {
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

function SessionSelector({ sessions, selected, onSelect, selectId = 'skus-session-select', name = 'skus_session', compact = false, tourAnchor }: {
  sessions: SessionInfo[]; selected: string | null; onSelect: (id: string) => void
  selectId?: string; name?: string
  /** Drops the eyebrow and tightens the padding for the compare bar, where the
   *  surrounding copy already says what the control picks. */
  compact?: boolean
  /** Set on the primary selector only — a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
}) {
  const { t } = useLanguage()
  const [focused, setFocused] = useState(false)
  const trained = sessions.filter(s => s.status === 'COMPLETED')
  const current = trained.find(s => s.session_id === selected)

  // Granularity and run date answer "which one is this?" once the name is
  // ambiguous — they are context, so they sit under the name in the dim tone
  // rather than competing with it.
  const context = current
    ? [
        current.granularity ? granularityLabel(t, current.granularity) : null,
        current.updated_at ? new Date(current.updated_at).toLocaleDateString() : null,
      ].filter(Boolean).join(' · ')
    : ''

  return (
    <div data-tour={tourAnchor} style={{ position: 'relative', minWidth: compact ? 200 : 236 }}>
      {/* The native <select> stays the control: it is stretched invisibly over
          the card below, so the dropdown, the keyboard behaviour and the
          accessible name remain the browser's, while the visible layer is free
          to give the name and its context two different weights — something a
          styled <select> cannot do, since option text has one style. */}
      <select
        id={selectId}
        name={name}
        aria-label={t('skus.session_label')}
        value={selected ?? ''}
        onChange={e => onSelect(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%',
          margin: 0, padding: 0, border: 'none', appearance: 'none',
          opacity: 0, cursor: 'pointer', zIndex: 1,
        }}
      >
        <option value="" disabled>{t('skus.select_trained_session')}</option>
        {trained.map(s => <option key={s.session_id} value={s.session_id}>{s.name}</option>)}
      </select>
      <div
        aria-hidden
        style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: compact ? '5px 10px' : '6px 12px',
          background: 'var(--surface)',
          border: `1px solid ${focused ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 9,
          // The real focus ring lands on the transparent <select>, where nobody
          // could see it — so the visible layer redraws the very same ring the
          // global :focus-visible rule uses. Not a new treatment, just relocated.
          outline: focused ? '2px solid var(--accent)' : 'none',
          outlineOffset: 2,
          transition: 'border-color var(--dur-1) var(--ease-out)',
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          {!compact && (
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
              textTransform: 'uppercase', color: 'var(--dim)', lineHeight: 1.4,
            }}>
              {t('skus.session_eyebrow')}
            </div>
          )}
          <div style={{
            fontSize: 12, fontWeight: 600, lineHeight: 1.35,
            color: current ? 'var(--text)' : 'var(--dim)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {current?.name ?? t('skus.select_trained_session')}
          </div>
          {context && (
            <div style={{
              fontSize: 10, color: 'var(--dim)', lineHeight: 1.35,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {context}
            </div>
          )}
        </div>
        <ChevronDown size={13} style={{ flexShrink: 0, color: 'var(--dim)' }} />
      </div>
    </div>
  )
}

// ── SKU card ──────────────────────────────────────────────────────────────────

function SkuCard({ sku, quality, metrics, signal, selected, onClick, tourAnchor }: {
  sku: string
  quality?: QualityReport[string]
  metrics: MetricRow[]
  // Live semáforo (from inventory_stock), not the training-time recommendation.
  signal?: InventorySignal
  selected: boolean
  onClick: () => void
  /** Set on the first card only — a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
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
      data-tour={tourAnchor}
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
          {signal && <SignalBadge signal={signal} />}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5 }}>
        <span style={{ fontSize: 10, fontWeight: 500, color, background: color + '18', borderRadius: 4, padding: '1px 5px' }}>
          {seriesTypeLabel(t, seriesType)}
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

function ChipGroup<T extends string>({ options, value, onChange, label, tourAnchor }: {
  options: { value: T; label: string; icon?: React.ReactNode; title?: string }[]
  value: T; onChange: (v: T) => void; label?: string
  /** Set on the single-session panel only — a tour anchor has to be unique. */
  tourAnchor?: string
}) {
  return (
    <div data-tour={tourAnchor} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
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
    { label: t('skus.stat_best_model'), value: modelLabel(t, bestMetric?.model) },
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

// ── Confidence band toggle ────────────────────────────────────────────────────

function BandToggle({ active, onToggle, hasQuantiles, tourAnchor }: {
  active: boolean
  onToggle: () => void
  hasQuantiles: boolean
  /** Set on the single-session panel only — a tour anchor has to be unique. */
  tourAnchor?: string
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
          padding: '3px 8px', borderRadius: 6, fontSize: 11, fontWeight: 500,
          border: `1px solid ${on ? CI_BAND.color : 'var(--border)'}`,
          background: on ? CI_BAND.color + '22' : 'transparent',
          color: on ? CI_BAND.color : 'var(--dim)',
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

type ChartType = 'line' | 'bar'

// An extra model's forecast overlaid on the primary chart (multi-model compare).
interface ModelOverlay {
  model:    string
  color:    string
  forecast: ForecastPoint[]
}

// Shortens ISO date labels on the x-axis by series granularity so they don't
// overlap: daily/weekly show month-day (year only when it changes), monthly
// shows year-month, quarterly shows the quarter, yearly just the year.
// Falls back to the raw value for non-ISO dates. The tooltip keeps full dates.
function makeAxisDateFormatter(granularity: string, dates: string[]) {
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

function buildChartOption(
  data: SkuIntelligenceData,
  chartType: ChartType,
  showBand: boolean,
  isDark: boolean,
  gaps: { start: string; end: string }[],
  outlierIndices: number[] = [],
  t: Translate = (k) => k,
  overlays: ModelOverlay[] = [],
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
  const histColor   = 'var(--accent)'
  const fcastColor  = PRIMARY_FORECAST_COLOR
  // When several models are overlaid, name the primary series by its model so
  // the legend/tooltip distinguish it from the overlays.
  const primaryName = overlays.length > 0 && data.model
    ? modelLabel(t, data.model)
    : t('skus.series_forecast_p50')

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

  // ── Confidence band (rendered first so the P50 line sits on top) ───────────
  const renderedBandLabels: string[] = []

  if (showBand) {
    const band = CI_BAND

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

    if (fillVals.some(v => v !== null)) {
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
    name:       primaryName,
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
      s['lineStyle']  = { color: ov.color, width: 2, type: 'dashed' }
      s['itemStyle']  = { color: ov.color }
      s['symbol']     = 'circle'
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
        const lo = getQ(fp, CI_BAND.lower)
        const hi = getQ(fp, CI_BAND.upper)
        if (lo !== null || hi !== null) {
          const bandDot = `<span style="display:inline-block;width:12px;height:4px;border-radius:2px;background:${CI_BAND.color};opacity:0.7;flex-shrink:0"></span>`
          const loStr = lo !== null ? lo.toFixed(2) : '—'
          const hiStr = hi !== null ? hi.toFixed(2) : '—'
          lines.push(row(bandDot, CI_BAND.label, `${loStr} – ${hiStr}`))
        }
      }
      for (const ov of overlayByDate) {
        const v = ov.byDate.get(date)
        if (typeof v === 'number') lines.push(row(dot(ov.color), ov.name, v.toFixed(2)))
      }
    }

    return `<div style="font-size:11px;min-width:170px">
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
    },
    legend: {
      data:      legendData,
      textStyle: { color: dim, fontSize: 11 },
      top:       6,
      right:     16,
      itemWidth: 16,
      itemHeight: 8,
    },
    // containLabel keeps y-axis labels from clipping; bottom leaves room for
    // the x-axis labels (zoom/pan is gesture-only via the inside dataZoom).
    grid: { top: 40, bottom: 28, left: 12, right: 20, containLabel: true },
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
      { type: 'inside', xAxisIndex: 0, start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
    ],
    series,
  }
}

// ── Main chart panel ──────────────────────────────────────────────────────────

function ChartPanel({ sessionId, sku, isDark, tourAnchor }: {
  sessionId: string; sku: string; isDark: boolean
  /** Set on the single-session panel only — in compare mode two of these are
   *  on screen, and a tour anchor has to be unique in the DOM. */
  tourAnchor?: string
}) {
  const { t } = useLanguage()
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
          const vals = [modelLabel(t, r.model), r.type ?? '', fmt(r.mae), fmt(r.rmse), r.wape != null ? pct(r.wape) : '—', fmt(r.bias)]
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
      ...data.metrics.map(r => [modelLabel(t, r.model), r.type, r.mae, r.rmse, r.wape, r.bias, r.n_folds ?? null] as (string | number | null)[]),
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
    return buildChartOption(data, chartType, showBand && singleModel, isDark, gaps, outliers, t, overlayList)
  }, [data, chartType, showBand, singleModel, isDark, gaps, outliers, t, overlayList])

  if (loading && !data) return (
    <div style={{ flex: 1, padding: '16px', minHeight: 360 }} role="status" aria-busy="true">
      <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 10 }}>{t('skus.loading_label')}</div>
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
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
        flexWrap: 'wrap', borderBottom: '1px solid var(--border)', background: 'var(--surface)',
      }}>
        {/* Granularity */}
        <ChipGroup
          tourAnchor={tourAnchor ? 'skus.granularity' : undefined}
          label={t('skus.chip_granularity')}
          value={granularity ?? data.applied_granularity}
          onChange={g => setGranularity(g)}
          options={validGranularities.map(g => ({
            value: g,
            label: GRANULARITY_LABELS[g] ?? g,
            title: granularityLabel(t, g),
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
            { value: 'bar',  label: t('skus.chart_type_bar'),  icon: <BarChart2 size={10} /> },
          ]}
        />

        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />

        {/* Model selection — multi-select chips; each selected model renders
            its own colored series on the same axis. */}
        {data.available_models.length > 1 && (
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
                      padding: '3px 9px', borderRadius: 6, fontSize: 11, fontWeight: 500,
                      display: 'flex', alignItems: 'center', gap: 5,
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

          {/* Fullscreen toggle (Escape also exits) */}
          <button
            title={fullscreen ? t('skus.exit_fullscreen_title') : t('skus.fullscreen_title')}
            onClick={() => setFullscreen(v => !v)}
            style={{
              all: 'unset', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, color: 'var(--dim)',
              padding: '3px 8px', borderRadius: 6,
              border: '1px solid var(--border)',
            }}
          >
            {fullscreen ? <X size={11} /> : <Maximize2 size={11} />}
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <StatsStrip data={data} />

      {/* Chart */}
      <div data-tour={tourAnchor ? 'skus.plot' : undefined} style={{ flex: 1, minHeight: 0, padding: '8px 0 0' }}>
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
        {data.model && <span>{t('skus.footer_model')}: <strong>{modelLabel(t, data.model)}</strong></span>}
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
  const caption = tOr(t, 'skus.metrics_table_caption',
    `Model accuracy for ${sku}, ordered by WAPE, best first.`, { sku })

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
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsCSV(t, sku, sorted)}>{t('skus.export_csv_short')}</Button>
          <Button variant="ghost" size="sm" icon={<Download size={11} />} onClick={() => exportMetricsExcel(t, sku, sorted)}>{t('skus.export_excel_short')}</Button>
        </div>
      </div>

      <div style={{ overflow: 'auto', flex: 1 }}>
        {viewMode === 'heatmap' ? (
          <div style={{ padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
              {t('skus.color_scale_label')}: <span style={{ color: '#22c55e' }}>{t('skus.color_scale_green')}</span> → <span style={{ color: '#ef4444' }}>{t('skus.color_scale_red')}</span>
            </div>
            <table className="data-table" style={{ tableLayout: 'fixed' }}>
              <caption className="sr-only">{caption}</caption>
              <thead>
                <tr>
                  <th scope="col" style={{ width: '20%' }}>{t('skus.col_model')}</th>
                  <th scope="col" style={{ width: '10%' }}>{t('skus.col_type')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_mae')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_rmse')}</th>
                  {/* The table is always ordered by WAPE, best first. Saying so
                      is the only way a screen-reader user learns why the rows
                      are in this order. */}
                  <th scope="col" aria-sort="ascending" style={{ width: '15%' }}>{t('skus.col_wape')}</th>
                  <th scope="col" style={{ width: '15%' }}>{t('skus.col_bias')}</th>
                  <th scope="col" style={{ width: '10%' }}>{t('skus.col_folds')}</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={i}>
                    <th scope="row" style={{ fontWeight: r === best ? 600 : 400, textAlign: 'left' }}>
                      {modelLabel(t, r.model)}
                      {r === best && <span style={{ fontSize: 9, color: 'var(--accent)', marginLeft: 5 }}>{t('skus.badge_best')}</span>}
                    </th>
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
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">{t('skus.col_model')}</th>
                <th scope="col">{t('skus.col_type')}</th>
                <th scope="col">{t('skus.col_mae')}</th>
                <th scope="col">{t('skus.col_rmse')}</th>
                <th scope="col" aria-sort="ascending">{t('skus.col_wape')}</th>
                <th scope="col">{t('skus.col_bias')}</th>
                <th scope="col">{t('skus.col_folds')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i} style={{ background: r === best ? 'color-mix(in srgb, var(--accent) 6%, transparent)' : undefined }}>
                  <th scope="row" style={{ fontWeight: r === best ? 600 : 400, textAlign: 'left' }}>
                    {modelLabel(t, r.model)}{r === best && <span style={{ fontSize: 9, color: 'var(--accent)', marginLeft: 6 }}>{t('skus.badge_best')}</span>}
                  </th>
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

// Stock and coverage come from LIVE inventory_stock (`live`, from
// /inventory/status) — the same source the /inventory page uses — so the tab
// reflects post-reception stock, not the training-time snapshot. Forecast-derived
// figures (reorder point, safety stock, stockout risk) stay from the training
// recommendation `inv`, which does not change when stock moves.
function InventoryPanel({ inv, live, coverageUnit }: {
  inv: InventoryRecommendation
  live?: InventoryStatusItem
  coverageUnit?: CoverageUnit
}) {
  const { t } = useLanguage()
  const fmtNum = (n: number | null | undefined, d = 0) => n != null ? n.toFixed(d) : '—'

  // Prefer the live semáforo; fall back to the training-time action only when
  // no live stock row exists for this SKU.
  const signal: InventorySignal = live?.signal ?? ACTION_SIGNAL[inv.action] ?? 'SIN_DATOS'

  const cards: { label: string; value: string; color: string }[] = []
  if (live) {
    cards.push({ label: t('skus.inv_current_stock'), value: fmtNum(live.current_stock), color: 'var(--accent)' })
    cards.push({
      label: `${t('skus.inv_coverage')} (${coverageUnitShort(coverageUnit, t)})`,
      value: live.coverage_days != null ? `${fmtNum(live.coverage_days)} ${coverageUnitShort(coverageUnit, t)}` : '—',
      color: signalColor(signal),
    })
  }
  cards.push({ label: t('skus.inv_reorder_point'), value: fmtNum(inv.reorder_point), color: 'var(--accent)' })
  cards.push({ label: t('skus.inv_safety_stock'),  value: fmtNum(inv.safety_stock),  color: '#06b6d4' })
  cards.push({ label: t('skus.inv_stockout_risk'), value: pct(inv.stockout_risk),    color: (inv.stockout_risk ?? 0) > 0.2 ? '#ef4444' : '#22c55e' })
  if (inv.holding_cost != null) {
    cards.push({ label: t('skus.inv_holding_cost'), value: formatMoney(inv.holding_cost), color: '#f59e0b' })
  } else if (!live) {
    // No live stock row — keep the training-time coverage as the last resort.
    cards.push({ label: t('skus.inv_days_coverage'), value: fmtNum(inv.days_coverage), color: '#f59e0b' })
  }

  const variant: 'danger' | 'warning' | 'success' | 'neutral' =
      signal === 'PEDIR_YA'                                ? 'danger'
    : signal === 'PEDIR_PRONTO' || signal === 'SOBRESTOCK' ? 'warning'
    : signal === 'OK'                                       ? 'success'
    :                                                         'neutral'
  const bannerBg = variant === 'danger'  ? 'rgba(239,68,68,0.08)'
                 : variant === 'warning' ? 'rgba(245,158,11,0.08)'
                 : variant === 'success' ? 'rgba(34,197,94,0.08)'
                 :                         'rgba(100,116,139,0.08)'
  const bannerBorder = variant === 'danger'  ? 'rgba(239,68,68,0.2)'
                     : variant === 'warning' ? 'rgba(245,158,11,0.2)'
                     : variant === 'success' ? 'rgba(34,197,94,0.2)'
                     :                         'rgba(100,116,139,0.2)'
  const message = signal === 'PEDIR_YA'     ? t('skus.action_reorder_msg')
                : signal === 'PEDIR_PRONTO' ? t('skus.action_order_soon_msg')
                : signal === 'SOBRESTOCK'   ? t('skus.action_overstock_msg')
                : signal === 'OK'           ? t('skus.action_ok_msg')
                :                             t('skus.action_sin_datos_msg')

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
        background: bannerBg,
        border: `1px solid ${bannerBorder}`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <SignalBadge signal={signal} size="md" />
        <span style={{ fontSize: 12 }}>{message}</span>
      </div>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function PanelPlaceholder({ message }: { message: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--dim)', minHeight: 200 }}>
      <Package size={36} strokeWidth={1} style={{ opacity: 0.3 }} />
      <span style={{ fontSize: 13 }}>{message}</span>
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function TabBar({ tabs, active, onChange, labelFor, tourAnchor }: { tabs: string[]; active: string; onChange: (tab: string) => void; labelFor?: (tab: string) => string; tourAnchor?: string }) {
  return (
    <div data-tour={tourAnchor} style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--border)', padding: '0 16px', background: 'var(--surface)' }}>
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
  // Shared active-period session (multi-period) — same resolver /hoy and
  // /inventory follow. Drives the default-select on load (#6).
  const planningCtx = usePlanning()
  const activeSessionId = planningCtx?.planning?.active_session_id ?? ''
  const [sessions,       setSessions]       = useState<SessionInfo[]>([])
  const [sessionId,      setSessionId]      = useState<string | null>(null)
  const [metrics,        setMetrics]        = useState<MetricRow[]>([])
  const [inventory,      setInventory]      = useState<InventoryRecommendation[]>([])
  // Live inventory status (from inventory_stock via /inventory/status) —
  // reflects post-reception stock, unlike the training-time `inventory`.
  const [invStatus,      setInvStatus]      = useState<InventoryStatusItem[]>([])
  const [coverageUnit,   setCoverageUnit]   = useState<CoverageUnit | undefined>(undefined)
  const [quality,        setQuality]        = useState<QualityReport>({})
  const [loading,        setLoading]        = useState(false)
  const [search,         setSearch]         = useState('')
  const [skuListPage,    setSkuListPage]    = useState(1)
  const [selectedSku,    setSelectedSku]    = useState<string | null>(null)
  const [tab,            setTab]            = useState('Forecast')
  const [sessLoading,    setSessLoading]    = useState(true)
  const [sessError,      setSessError]      = useState<unknown>(null)
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

  // Extracted so the error banner's retry can re-run exactly this load.
  const reloadSessions = useCallback(() => {
    setSessLoading(true)
    setSessError(null)
    getSessions()
      .then(s => { setSessions(s); setSessLoading(false) })
      .catch((e: unknown) => {
        setSessError(e)
        setSessLoading(false)
      })
  }, [])

  useEffect(() => { reloadSessions() }, [reloadSessions])

  // Default-select the active session on load (#6) so the user lands on data
  // instead of an empty state that needs a manual pick. Reuses the shared
  // active-period resolution (PlanningContext) — the same one /hoy and
  // /inventory follow — and falls back to the latest-completed session for
  // tenants without a resolved active period.
  //
  // `sessions` and the planning state load in parallel, so we may land on
  // latest-completed before the active session resolves. `lastAutoRef` tracks
  // the session WE auto-applied: once planning resolves an active session and
  // the user hasn't switched since, we upgrade to it. A manual pick (which
  // never touches `lastAutoRef`) always wins and is never overridden.
  const lastAutoRef = useRef('')
  // /sessions history deep-link (/skus?session=<id>): honored once on load,
  // before the auto-pick. Treated like a manual pick, so the planning-resolved
  // active session never overrides it.
  const urlSessionConsumedRef = useRef(false)
  useEffect(() => {
    if (!urlSessionConsumedRef.current && !sessionId && sessions.length) {
      urlSessionConsumedRef.current = true
      const wanted = new URLSearchParams(window.location.search).get('session')
      if (wanted && sessions.some(s => s.session_id === wanted && s.status === 'COMPLETED')) {
        setSessionId(wanted)
        setTab('Forecast')
        return
      }
    }
    const trained = sessions
      .filter(s => s.status === 'COMPLETED')
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    if (!trained.length) return
    const active = activeSessionId
      ? trained.find(s => s.session_id === activeSessionId)?.session_id
      : undefined
    if (!sessionId) {
      const pick = active ?? trained[0].session_id
      lastAutoRef.current = pick
      setSessionId(pick)
      setTab('Forecast')
    } else if (active && sessionId === lastAutoRef.current && sessionId !== active) {
      // Planning resolved later — upgrade our auto-pick to the active session.
      lastAutoRef.current = active
      setSessionId(active)
    }
  }, [sessions, sessionId, activeSessionId])

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
      // Live stock/coverage/signal. Failing soft — the panels fall back to the
      // training recommendation if this is unavailable, so no error is surfaced.
      getInventoryStatus(sessionId, 0.95, { silent: true }).catch(() => ({ items: [], coverage_unit: undefined })),
    ]).then(([m, inv, q, status]) => {
      setMetrics(m.rows)
      setInventory(inv.recommendations)
      setInvStatus(status.items ?? [])
      setCoverageUnit(status.coverage_unit)
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

  // One pass instead of one full scan of `metrics` per card. With 2.000 SKUs
  // and ~4 rows each, the per-card `metrics.filter(...)` was 2.000 × 8.000
  // comparisons on every keystroke in the search box — before React had even
  // started rendering.
  const metricsBySku = useMemo(() => {
    const map = new Map<string, MetricRow[]>()
    for (const row of metrics) {
      if (!row.sku) continue
      const list = map.get(row.sku)
      if (list) list.push(row)
      else map.set(row.sku, [row])
    }
    return map
  }, [metrics])

  const recBySku = useMemo(
    () => new Map(inventory.map(r => [r.sku, r])),
    [inventory],
  )

  const skuPage = usePage(skus, skuListPage, setSkuListPage)
  // Any change to what is being listed sends you back to the first page.
  useEffect(() => { setSkuListPage(1) }, [search, sessionId])

  const cmpSkus = useMemo(() =>
    Array.from(new Set(cmpMetrics.map(r => r.sku).filter(Boolean) as string[]))
  , [cmpMetrics])

  const skuMetrics   = useMemo(() => metrics.filter(r => r.sku === selectedSku), [metrics, selectedSku])
  const skuInventory = useMemo(() => inventory.find(r => r.sku === selectedSku), [inventory, selectedSku])
  const skuQuality   = useMemo(() => selectedSku ? quality[selectedSku] : undefined, [quality, selectedSku])
  const statusBySku  = useMemo(() => new Map(invStatus.map(i => [i.sku, i])), [invStatus])
  const skuStatus    = useMemo(() => selectedSku ? statusBySku.get(selectedSku) : undefined, [statusBySku, selectedSku])
  // Resolve the semáforo for a SKU: prefer the live inventory_stock signal,
  // fall back to the training-time recommendation when no live row exists.
  const signalForSku = useCallback((sku: string): InventorySignal | undefined => {
    const live = statusBySku.get(sku)
    if (live) return live.signal
    const rec = recBySku.get(sku)
    return rec ? ACTION_SIGNAL[rec.action] : undefined
  }, [statusBySku, recBySku])
  const seriesType   = skuQuality?.series_type ?? 'unknown'
  const skuColor     = SERIES_COLOR[seriesType] ?? SERIES_COLOR.unknown
  // Best non-baseline model accuracy (1 − WAPE) for the selected SKU — the
  // single discreet accuracy figure shown next to the chart header.
  const skuAccuracy  = useMemo(() => {
    const best = skuMetrics
      .filter(r => r.type !== 'baseline' && r.wape !== null)
      .sort((a, b) => (a.wape ?? Infinity) - (b.wape ?? Infinity))[0]
    if (best?.wape == null) return null
    // WAPE divides by total real demand, so a SKU that never sold scores a
    // meaningless 0 error and would proudly report "100%" over a flat line of
    // zeros. Both errors landing on exactly 0 means there was no signal to be
    // accurate about — show nothing rather than false confidence.
    if (best.wape === 0 && (best.mae ?? 0) === 0) return null
    return Math.round((1 - best.wape) * 100)
  }, [skuMetrics])

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
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 56px)' }}>

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
              data-tour="skus.compare"
              onClick={() => { setCompareMode(v => !v); if (compareMode) setCmpSessionId(null) }}
              title={t('skus.compare_sessions_title')}
              style={{
                all: 'unset', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 7, fontSize: 11,
                border: `1px solid ${compareMode ? 'var(--accent)' : 'var(--border)'}`,
                color: compareMode ? 'var(--accent)' : 'var(--dim)',
                background: compareMode ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'var(--surface)',
              }}
            >
              <GitCompare size={11} /> {t('skus.btn_compare')}
            </button>
          )}

          {sessLoading ? <Spinner size={13} /> : (
            <SessionSelector tourAnchor="skus.session" sessions={sessions} selected={sessionId} onSelect={id => { setSessionId(id); setTab('Forecast'); setCompareMode(false) }} />
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
          background: 'color-mix(in srgb, var(--accent) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 20%, transparent)',
          borderRadius: 8, marginBottom: 12, flexWrap: 'wrap',
        }}>
          <GitCompare size={12} color="var(--accent)" />
          <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600 }}>{t('skus.comparing_with_label')}</span>
          <SessionSelector
            sessions={sessions}
            selected={cmpSessionId}
            onSelect={id => { setCmpSessionId(id); setCmpSku(null) }}
            selectId="skus-compare-session-select"
            name="skus_compare_session"
            compact
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
            <span style={{ fontSize: 11, color: '#f87171' }}>{cmpError}</span>
          )}
        </div>
      )}

      {/* Session list failed: retry reloads it. Partial-result failures carry
          their own composed sentence naming which parts are missing. */}
      {sessError != null && (
        <div style={{ marginBottom: 12 }}>
          <InlineError error={sessError} onRetry={reloadSessions} onDismiss={() => setSessError(null)} />
        </div>
      )}
      {loadError && (
        <div style={{ marginBottom: 12 }}>
          <InlineError error={new Error(loadError)} onDismiss={() => setLoadError(null)} />
        </div>
      )}

      {/* Data problems the engine found while training. They never abort a run,
          so this is the only place the user can learn the accuracy above is
          inflated by leakage. */}
      <RunWarningsPanel sessionId={sessionId} />

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
                data-tour="skus.search"
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
              /* Nothing trained yet: point at the action that creates the data. */
              <div style={{ padding: 14 }}>
                <EmptyState
                  compact
                  icon={<Package size={20} />}
                  title={t('skus.empty_title')}
                  body={t('skus.empty_body')}
                  actions={[{ label: t('skus.empty_cta'), href: '/quick-start' }]}
                />
              </div>
            ) : loading ? (
              <div style={{ padding: 12 }}>
                <LoadingState label={t('skus.loading_label')}>
                  <SkeletonTable rows={7} columns={1} />
                </LoadingState>
              </div>
            ) : skus.length === 0 ? (
              <PanelPlaceholder message={t('skus.empty_no_skus_found')} />
            ) : (
              skuPage.rows.map((sku, idx) => (
                <SkuCard
                  key={sku}
                  tourAnchor={idx === 0 ? 'skus.card' : undefined}
                  sku={sku}
                  quality={quality[sku]}
                  metrics={metricsBySku.get(sku) ?? EMPTY_METRICS}
                  signal={signalForSku(sku)}
                  selected={sku === selectedSku}
                  onClick={() => { setSelectedSku(sku); setTab('Forecast') }}
                />
              ))
            )}
          </div>
          {/* One page of cards at a time: a 2.000-SKU catalogue rendered every
              card at once, and each card carries an SVG sparkline and a badge. */}
          <Pagination
            page={skuPage.page}
            pageCount={skuPage.pageCount}
            offset={skuPage.offset}
            total={skuPage.total}
            rowsOnPage={skuPage.rows.length}
            onPage={setSkuListPage}
            label="SKU"
          />
        </div>

        {/* Detail panel */}
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column',
        }}>
          {!selectedSku ? (
            <PanelPlaceholder message={sessionId ? t('skus.empty_select_sku_from_list') : t('skus.empty_no_session_selected')} />
          ) : (
            <>
              {/* SKU header */}
              <div data-tour="skus.header" style={{
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
                          <span style={{ color: skuColor }}>{seriesTypeLabel(t, skuQuality.series_type)}</span>
                          {' · '}
                          {skuQuality.n_rows} {t('skus.rows_label')} · {pct(skuQuality.quality_score)} {t('skus.quality_label_lower')}
                        </>
                      ) : t('skus.no_quality_data')}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {skuAccuracy != null && (
                    <span style={{ fontSize: 11, color: 'var(--dim)' }}>
                      {t('skus.accuracy_label')}: <strong style={{ color: 'var(--fg)' }}>{skuAccuracy}%</strong>
                    </span>
                  )}
                  {selectedSku && signalForSku(selectedSku) && (
                    <SignalBadge signal={signalForSku(selectedSku)!} size="md" />
                  )}
                </div>
              </div>

              <TabBar
                tourAnchor="skus.tabs"
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
                          background: 'color-mix(in srgb, var(--accent) 12%, transparent)', padding: '2px 7px', borderRadius: 4,
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
                    <ChartPanel key={`${sessionId}-${selectedSku}`} tourAnchor="skus.chart" sessionId={sessionId} sku={selectedSku} isDark={isDark} />
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
                        {showSkuStats && (
                          <div style={{ marginTop: 4 }}>
                            <QualityPanel q={skuQuality} />
                          </div>
                        )}
                      </div>
                    </div>
                  ) : <PanelPlaceholder message={t('skus.empty_no_quality_data')} />
                )}
                {tab === 'Inventory' && (
                  skuInventory
                    ? <InventoryPanel inv={skuInventory} live={skuStatus} coverageUnit={coverageUnit} />
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

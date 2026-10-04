// Helpers shared by the /pronosticos panels. Moved out of
// app/pronosticos/page.tsx unchanged when the page was split into a buyer and a
// technical view (docs/stability.md section 18).
import { useEffect, useState } from 'react'
import type { MetricRow, InventorySignal } from '@/lib/types'
import type { Translate as TranslateFn } from '@/lib/modelLabel'

export const SERIES_COLOR: Record<string, string> = {
  stable:       '#2E8B62',
  seasonal:     'var(--accent)',
  volatile:     '#B7791F',
  intermittent: '#BF7440',
  short:        '#3E8E9B',
  unknown:      '#64748b',
}

// Maps the training-time inventory action to the shared semáforo signal, used
// as a fallback when a SKU has no live inventory_stock row. Live stock reports
// its signal directly (feature 2.8 / #7).
export const ACTION_SIGNAL: Record<string, InventorySignal> = {
  REORDER:   'PEDIR_YA',
  OVERSTOCK: 'SOBRESTOCK',
  OK:        'OK',
}

// Stable identity, so a SKU with no metric rows does not hand SkuCard a fresh
// array on every render.
export const EMPTY_METRICS: MetricRow[] = []

// The value the champion is actually chosen by — a unit short costs more than a
// unit spare, so the cheapest model is not always the most accurate one. Walks
// the same order as the engine's CHAMPION_METRIC_ORDER and the backend's
// _CHAMPION_METRICS; WAPE is the fallback for sessions trained before `cost`
// existed. One definition, because those two already drifted apart once and
// disagreed on 8 of 13 SKUs, and this screen is the third copy of the question.
export const CHAMPION_METRICS = ['cost_horizon', 'cost', 'wape', 'mae'] as const

// ONE metric for the whole set, then compare within it.
//
// This used to be `r.cost_horizon ?? r.cost ?? r.wape` evaluated PER ROW, which
// silently compared one model's cost_horizon against another model's cost — two
// different quantities on two different scales — and `test_horizon_comparability`
// says the second is systematically the smaller of the two. So a model the
// server had excluded could win the browser's comparison: the stats strip
// announced "Mejor modelo: XGBoost" with its WAPE while the curve drawn on the
// same screen, and the purchase order behind it, came from Prophet.
//
// The server picks the first metric ANY row carries and drops the rows that
// lack it (`service._champion_metric`); this mirrors that. One remaining
// difference, deliberately not papered over: the server chooses over the whole
// session's rows and this chooses over the rows in hand (one SKU's, on most of
// these surfaces). They only diverge for a session where some SKUs carry
// `cost_horizon` and others do not.
export const championMetric = (rows: MetricRow[]): (typeof CHAMPION_METRICS)[number] =>
  CHAMPION_METRICS.find(m => rows.some(r => r[m] !== null && r[m] !== undefined))
  ?? 'wape'

export const makeChampionRank = (rows: MetricRow[]) => {
  const metric = championMetric(rows)
  return (r: MetricRow): number | null | undefined =>
    r[metric] as number | null | undefined
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// `t` returns the key itself when the catalog has no entry, so a build whose
// copy has not landed yet would print "skus.metrics_table_caption" at the user.
export type Translate = TranslateFn
export function tOr(t: Translate, key: string, fallback: string, params?: Record<string, unknown>): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

export function pct(n: number | null | undefined) {
  if (n == null || isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}
export function fmt(n: number | null | undefined, d = 2) {
  if (n == null || isNaN(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
}
export function fmtK(n: number | null | undefined) {
  if (n == null || isNaN(n)) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
  return n.toFixed(1)
}

/** Same Alta/Media/Baja read of `quality_score` everywhere it appears —
 *  the SKU list pill and the Forecast tab's default stat tile — so the two
 *  never drift into disagreeing about the same SKU. */
export function reliabilityInfo(qs: number, t: Translate): { label: string; color: string } {
  return qs >= 0.7
    ? { label: t('skus.reliability_high'),   color: '#2E8B62' }
    : qs >= 0.45
    ? { label: t('skus.reliability_medium'), color: '#B7791F' }
    : { label: t('skus.reliability_low'),    color: '#C0504D' }
}

// ── CSV Export ────────────────────────────────────────────────────────────────

export function downloadCSV(filename: string, headers: string[], rows: (string | number | null)[][]) {
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

/** ECharts paints onto a canvas, where `var(--accent)` is not a colour the 2D
 *  context can resolve. The token has to be read off the document and handed
 *  over as a concrete value — re-read whenever the theme flips. */
export function useCssToken(name: string, fallback: string, themeKey: unknown): string {
  const [value, setValue] = useState(fallback)
  useEffect(() => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    setValue(v || fallback)
  }, [name, fallback, themeKey])
  return value
}

export function chartChrome(isDark: boolean) {
  return {
    dim:         '#64748b',
    gridLine:    isDark ? '#1e2030' : '#e2e8f0',
    tooltipBg:   isDark ? '#0f1015' : '#ffffff',
    tooltipBdr:  isDark ? '#1e2030' : '#e2e8f0',
    tooltipText: isDark ? '#e2e8f0' : '#1e293b',
  }
}

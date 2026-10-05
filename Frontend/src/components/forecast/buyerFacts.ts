// Pure helpers behind the buyer view of /pronosticos: the three answers a
// buyer reads at a glance. Nothing here recomputes a forecast or a signal —
// it only rearranges figures the API already returned (live stock status, the
// forecast points, the champion's metrics) into words and dates.
import type {
  CoverageUnit, ForecastPoint, InventoryStatusItem, MetricRow, SkuIntelligenceData,
} from '@/lib/types'
import { daysPerUnit } from '@/lib/period'
import { championError } from './shared'

const DAY_MS = 86_400_000

/** ISO date (`YYYY-MM-DD` or a full timestamp) to UTC milliseconds. */
export function dateToTs(date: string): number {
  return Date.parse(date.length === 10 ? `${date}T00:00:00Z` : date)
}

/** Today at UTC midnight, from the viewer's own calendar date. */
export function todayTs(): number {
  const d = new Date()
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())
}

export interface StockHorizon {
  /** Calendar days the current stock lasts at the expected demand. */
  coverageDays: number
  /** Supplier lead time in calendar days. */
  leadDays: number
  runoutTs: number
  arriveTs: number
  /** Days the shelf would be empty before an order placed today lands. 0 when
   *  the stock outlasts the supplier. */
  gapDays: number
}

/** When stock runs out against when an order placed today would arrive. Null
 *  when the live status has no stock or no coverage figure — the screen then
 *  says so instead of drawing a marker from nothing. */
export function stockHorizon(
  status: InventoryStatusItem | undefined,
  coverageUnit: CoverageUnit | undefined,
  now: number = todayTs(),
): StockHorizon | null {
  if (!status || !status.has_stock) return null
  const cov = status.coverage_days
  const lead = status.lead_time_days
  if (cov == null || !Number.isFinite(cov) || !Number.isFinite(lead) || lead <= 0) return null
  // `coverage_days` reads in the planning period's unit (see lib/period.ts).
  const coverageDays = Math.max(0, cov * daysPerUnit(coverageUnit))
  const gapDays = Math.max(0, Math.round(lead - coverageDays))
  return {
    coverageDays,
    leadDays: lead,
    runoutTs: now + Math.round(coverageDays) * DAY_MS,
    arriveTs: now + Math.round(lead) * DAY_MS,
    gapDays,
  }
}

/** The likely range of one forecast point: P10-P90, falling back to the
 *  lower/upper pair of a session stored before quantiles existed. */
export function likelyRange(p: ForecastPoint): [number | null, number | null] {
  const rec = p as unknown as Record<string, number | null | undefined>
  const lo = typeof rec['q10'] === 'number' ? rec['q10'] : (typeof p.lower === 'number' ? p.lower : null)
  const hi = typeof rec['q90'] === 'number' ? rec['q90'] : (typeof p.upper === 'number' ? p.upper : null)
  return [lo ?? null, hi ?? null]
}

export type Confidence = 'high' | 'medium' | 'low'

/** Plain-language confidence. Driven by the champion's measured error when it
 *  exists, otherwise by the series' quality score; the lower of the two wins,
 *  so a clean series with a poor backtest is never called reliable. */
export function confidenceOf(
  metrics: MetricRow[], qualityScore: number | undefined,
): Confidence | null {
  const { wape } = championError(metrics)
  const fromError: Confidence | null =
    wape == null ? null : wape <= 0.25 ? 'high' : wape <= 0.5 ? 'medium' : 'low'
  const fromQuality: Confidence | null =
    qualityScore == null ? null : qualityScore >= 0.7 ? 'high' : qualityScore >= 0.45 ? 'medium' : 'low'
  const rank = { high: 2, medium: 1, low: 0 } as const
  if (fromError && fromQuality) return rank[fromError] <= rank[fromQuality] ? fromError : fromQuality
  return fromError ?? fromQuality
}

// Fewer history points than this and a seasonal pattern cannot be told apart
// from noise. Per granularity, because 12 months is a year and 12 days is not.
const MIN_HISTORY_POINTS: Record<string, number> = {
  daily: 60, weekly: 26, monthly: 12, quarterly: 6, yearly: 3,
}

export function isShortHistory(data: SkuIntelligenceData): boolean {
  const need = MIN_HISTORY_POINTS[data.applied_granularity]
  return need != null && data.historical.length < need
}

/** The sales granularity the backtest error is measured in (the series' own
 *  frequency), as an i18n key for "per day / week / month". */
export function perPeriodKey(freq: string | undefined): string {
  const f = (freq ?? '').toLowerCase()
  if (f.startsWith('d')) return 'skus.err_per_day'
  if (f.startsWith('w')) return 'skus.err_per_week'
  if (f.startsWith('m')) return 'skus.err_per_month'
  return 'skus.err_per_period'
}

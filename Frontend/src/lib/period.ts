import type { CoverageUnit } from './types'

// Coverage in the active planning period reads in that period's unit
// (multi-period Phase C). day/week/month, singular when n === 1, via i18n so
// it follows the language toggle. Defaults to 'day' so pre-Phase-C / daily
// tenants read exactly as before.
export function coverageUnitLabel(
  unit: CoverageUnit | undefined,
  n: number,
  t: (k: string) => string,
): string {
  const u = unit ?? 'day'
  return t(n === 1 ? `period.${u}_singular` : `period.${u}_plural`)
}

// Calendar days in one bucket of the planning period — mirrors the backend's
// `_DAYS_PER_PERIOD` (daily 1, weekly 7, monthly 30). A per-period demand
// divided by this is a per-DAY demand, which is what a label saying "/día"
// promises; on a weekly tenant the raw figure is per WEEK.
export function daysPerUnit(unit: CoverageUnit | undefined): number {
  return unit === 'week' ? 7 : unit === 'month' ? 30 : 1
}

// Compact suffix for dense tables (d / sem / mes).
export function coverageUnitShort(
  unit: CoverageUnit | undefined,
  t: (k: string) => string,
): string {
  return t(`period.${unit ?? 'day'}_short`)
}

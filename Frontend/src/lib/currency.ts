/**
 * Single source of truth for money formatting.
 *
 * Faro's anchor market is Costa Rica, so amounts are colones (CRC, ₡) — every
 * screen used to hardcode `$` in its own local `fmtMoney`, which was both wrong
 * for the market and impossible to change in one place.
 *
 * Two deliberate rules, learned from a real bug:
 *  - `formatMoney` never abbreviates. Scaling to millions rendered ₡25,430 as
 *    "₡0.0M" — the product reporting zero for money the user actually had tied
 *    up in stock. SMB amounts are typically five figures, so abbreviating them
 *    destroys the number.
 *  - `formatMoneyCompact` exists only for dashboard tiles where space is tight,
 *    and it falls back to the full amount below 10,000 rather than showing 0.
 */

export const CURRENCY_CODE = 'CRC'
export const CURRENCY_SYMBOL = '₡'
const LOCALE = 'es-CR'

/** Full amount, no abbreviation. Use this everywhere by default. */
export function formatMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `${CURRENCY_SYMBOL}${Math.round(n).toLocaleString(LOCALE)}`
}

/**
 * Abbreviated form for KPI tiles only. Never abbreviates below 10,000, so a
 * real five-figure amount can never collapse to "₡0.0M".
 */
export function formatMoneyCompact(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${CURRENCY_SYMBOL}${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${CURRENCY_SYMBOL}${Math.round(n / 1_000)}K`
  return formatMoney(n)
}

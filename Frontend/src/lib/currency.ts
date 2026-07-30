/**
 * Single source of truth for money formatting.
 *
 * Two different kinds of money exist in this product and they do NOT share a
 * setting:
 *  - What Faro costs is priced in USD for everyone. That lives in Stripe and is
 *    rendered by the billing panel, never through here.
 *  - What the CUSTOMER's money is worth — inventory value, unit costs, a purchase
 *    order's total — is in whatever currency that business trades in. That is the
 *    tenant setting this module reads.
 *
 * The active currency is a module-level value rather than a hook return, and
 * `CurrencyProvider` sets it once on boot. That is deliberate: `formatMoney` is
 * called from ~200 places, many of them inside chart option builders and export
 * helpers that are not React components and cannot call a hook. Threading a
 * context through all of them would have meant touching every call site to change
 * one symbol.
 *
 * Two rules kept from a real bug:
 *  - `formatMoney` never abbreviates. Scaling to millions rendered ₡25,430 as
 *    "₡0.0M" — the product reporting near-zero for money the user actually had
 *    tied up in stock. SMB amounts are typically five figures.
 *  - `formatMoneyCompact` exists only for KPI tiles where space is tight, and it
 *    falls back to the full amount below 10,000 rather than showing 0.
 */

export interface CurrencyInfo {
  code:     string
  symbol:   string
  locale:   string
  decimals: number
}

/** Costa Rica is the anchor market, so a tenant that never chose keeps reading
 *  colones exactly as it did before this setting existed. Mirrors DEFAULT_CODE
 *  in backend/api/v1/currency.py. */
const DEFAULT: CurrencyInfo = {
  code: 'CRC', symbol: '₡', locale: 'es-CR', decimals: 0,
}

let active: CurrencyInfo = DEFAULT

/** Called by CurrencyProvider once the tenant's setting has loaded. */
export function setActiveCurrency(info: Partial<CurrencyInfo> | null | undefined) {
  if (!info?.code || !info.symbol) return
  active = {
    code: info.code,
    symbol: info.symbol,
    locale: info.locale || DEFAULT.locale,
    decimals: typeof info.decimals === 'number' ? info.decimals : 2,
  }
}

export function activeCurrency(): CurrencyInfo {
  return active
}

// Kept as live getters, not frozen constants: the old module exported
// `CURRENCY_CODE` / `CURRENCY_SYMBOL` as literals, and anything still importing
// them must follow the tenant's choice rather than a value captured at import.
export const getCurrencyCode   = () => active.code
export const getCurrencySymbol = () => active.symbol

/**
 * Full amount, no abbreviation. Use this everywhere by default.
 *
 * Honours the currency's declared `decimals`: it used to round unconditionally,
 * so a tenant on USD or MXN — both of which declare 2 — read "$8" for a unit cost
 * of $8.49. A 0-decimal currency (CRC, COP, CLP, ARS) still goes through the
 * exact same `Math.round(...).toLocaleString(locale)` as before, so the anchor
 * market renders byte-for-byte what it did.
 */
export function formatMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  const d = active.decimals
  if (!(d > 0)) return `${active.symbol}${Math.round(n).toLocaleString(active.locale)}`
  return `${active.symbol}${n.toLocaleString(active.locale, {
    minimumFractionDigits: d, maximumFractionDigits: d,
  })}`
}

/**
 * Abbreviated form for KPI tiles only. Never abbreviates below 10,000, so a real
 * five-figure amount can never collapse to "₡0.0M".
 */
export function formatMoneyCompact(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${active.symbol}${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${active.symbol}${Math.round(n / 1_000)}K`
  return formatMoney(n)
}

/**
 * What Faro itself costs. Always USD, never the tenant's currency: the plans are
 * the same price for every customer, and showing "₡649" for a 649-dollar plan
 * would understate it by roughly five hundred times.
 */
export function formatPlanPrice(usd: number): string {
  return `$${usd.toLocaleString('en-US')}`
}

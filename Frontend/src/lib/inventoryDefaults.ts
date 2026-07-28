// The planning values Faro assumes when the tenant has configured nothing, and
// the vocabulary that says WHERE a value came from.
//
// Mirror of `backend/inventory/defaults.py` — that module is the authority; the
// values are repeated here only because the browser cannot import Python.
// Keep the two in sync: `backend/tests/test_lead_time_provenance.py` guards the
// backend/ForecastingCore pair, and any change here must move that constant too.
//
// Why this file exists: the product shipped THREE lead-time defaults (7 in the
// canonical column defaults, 7 in the training runner, 15 in the DB schema, the
// Quick Start wizard and three `?? 15` fallbacks in /inventory). A number the
// buyer never chose was deciding how much money to spend, and no screen said so.

/** Days assumed when nobody configured a lead time. See the backend module for
 *  why 15 and not 7 (LatAm distributor, import leg; guessing long is the cheap
 *  mistake because a short guess is discovered as a stockout). */
export const DEFAULT_LEAD_TIME_DAYS = 15
export const DEFAULT_SERVICE_LEVEL = 0.95
export const DEFAULT_MOQ = 1
export const DEFAULT_HOLDING_COST_PCT = 0.2

/** The five real ways a planning value gets its number.
 *  - user          the buyer typed it on the SKU card
 *  - file          it came in on an uploaded file
 *  - supplier_rule it came from a supplier / category / global rule
 *  - learned       derived from the supplier's real recorded receptions
 *  - default       nobody configured anything; this is our assumption */
export type ValueSource = 'user' | 'file' | 'supplier_rule' | 'learned' | 'default'

/** Which level of the SKU > supplier > category > global cascade won. */
export type RuleScope = 'supplier' | 'category' | 'global'

/** True when the number on screen is Faro's assumption, not the tenant's data.
 *  Anything unknown is treated as assumed on purpose: claiming authorship we
 *  cannot prove is the exact failure this whole change exists to fix. */
export function isAssumed(source: ValueSource | null | undefined): boolean {
  return !source || source === 'default'
}

/** i18n key for a provenance badge: `inventory.source_<source>`. */
export function sourceLabelKey(source: ValueSource | null | undefined): string {
  return `inventory.source_${source || 'default'}`
}

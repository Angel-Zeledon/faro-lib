// Renders the recommendation explanation the backend now ships as a stable code
// plus parameters (`explanation_code` / `explanation_params`) into the user's
// language.
//
// It used to arrive as a hardcoded Spanish sentence assembled in
// `backend/inventory/service.py`, which broke CLAUDE.md twice over: Spanish
// string literals in backend logic, and copy that could only be changed with a
// backend deploy. The backend still owns the REASONING (which clause applies to
// which provenance); this module owns the wording.
//
// The clause that matters: when the lead time's source is 'default', the
// sentence must say we assumed it — not that the user configured it.

import type { RuleScope, ValueSource } from './inventoryDefaults'

type Translate = (key: string, params?: Record<string, unknown>) => string

export interface ExplanationParams {
  current_stock?: number
  daily_demand?: number
  /** null means coverage runs past the forecast horizon — a real state, not a
   *  missing value, and it has its own clause. */
  coverage_days?: number | null
  lead_time_days?: number
  lead_time_source?: ValueSource
  lead_time_rule_scope?: RuleScope | null
  reorder_point?: number
  signal?: string
}

/** Day count that agrees in number, via the i18n catalog (es: "1 día" / "N días"). */
function days(t: Translate, n: number): string {
  const rounded = Math.round(n)
  return rounded === 1
    ? t('explain.day_one')
    : t('explain.day_many', { n: rounded.toLocaleString() })
}

/** The clause describing where the lead time came from. One key per provenance
 *  value, so the five cases can be worded independently — 'default' in
 *  particular is an admission, not a variant of "configured". */
function leadClause(t: Translate, p: ExplanationParams): string {
  const source: ValueSource = p.lead_time_source || 'default'
  const d = days(t, p.lead_time_days ?? 0)
  if (source === 'supplier_rule') {
    // State the precedence explicitly: the buyer must be able to tell why this
    // SKU got this number (the lesson carried over from the event multipliers).
    const scope = t(`explain.scope_${p.lead_time_rule_scope || 'supplier'}`)
    return t('explain.lead_supplier_rule', { days: d, scope })
  }
  return t(`explain.lead_${source}`, { days: d })
}

/**
 * The full sentence. `code` is the backend's `explanation_code`; `fallback` is
 * its English `explanation` text, used only when the catalog has no mapping
 * (an older/newer backend shipping a code this build does not know).
 */
export function renderExplanation(
  t: Translate,
  code: string | null | undefined,
  params: ExplanationParams | null | undefined,
  fallback?: string | null,
): string {
  if (!code || !params) return fallback || ''

  // `t` returns the raw key when the catalog has no entry. Rendering
  // "explain.base" at the user is worse than rendering the backend's English
  // sentence, so probe one key and bail out to the fallback if the copy block
  // is not in this build's catalog yet.
  if (t('explain.base') === 'explain.base') return fallback || ''

  if (code === 'inventory_explain_no_demand') {
    return t('explain.no_demand', {
      stock: (params.current_stock ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 }),
    })
  }

  if (code !== 'inventory_explain_reorder') return fallback || ''

  const coverage = params.coverage_days != null
    ? t('explain.coverage_lasts', { days: days(t, params.coverage_days) })
    : t('explain.coverage_beyond_horizon')

  const base = t('explain.base', {
    stock: (params.current_stock ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 }),
    demand: (params.daily_demand ?? 0).toLocaleString(undefined, {
      minimumFractionDigits: 1, maximumFractionDigits: 1,
    }),
    coverage,
    lead: leadClause(t, params),
    reorder: (params.reorder_point ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 }),
  })

  if (params.signal === 'PEDIR_YA') return base + t('explain.suffix_order_now')
  if (params.signal === 'PEDIR_PRONTO') return base + t('explain.suffix_order_soon')
  return base + t('explain.suffix_end')
}

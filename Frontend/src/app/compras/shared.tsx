'use client'
/**
 * Pieces of `/hoy` that both the desktop table-style page and the narrow-screen
 * card list need.
 *
 * They were extracted from `page.tsx` verbatim when `HoyMobile` was added: the
 * mobile view must make exactly the same honesty promises as the desktop one —
 * the same "estimado" badge, the same provenance wording, the same assumptions
 * banner, the same "no lo podemos confirmar" when the semáforo has gone blind.
 * Two copies of those would be two chances to drift, and the phone is the
 * screen where a confident green over month-old stock does the most damage.
 */
import Link from 'next/link'
import { Info, ArrowRight } from 'lucide-react'
import { isAssumed, sourceLabelKey, type RuleScope, type ValueSource } from '@/lib/inventoryDefaults'
import type { MorningBriefing, InventoryStatusItem } from '@/lib/types'
import { StaleSignalChip } from '@/components/ui/StaleDataBanner'
import { useLanguage } from '@/contexts/LanguageContext'

// ── Colour palette ────────────────────────────────────────────────────────────
export const C = {
 bg: 'var(--bg)',
 surface: 'var(--surface)',
 card: 'var(--surface-2)',
 border: 'var(--border)',
 text: 'var(--text)',
 muted: 'var(--muted)',
 dim: 'var(--dim)',
 red: '#ef4444',
 amber: '#f59e0b',
 green: '#22c55e',
 blue: '#3b82f6',
 indigo: 'var(--accent)',
}

// ── Cart types ────────────────────────────────────────────────────────────────
export type ActionStatus = 'pending' | 'approved' | 'modified' | 'rejected'

export interface ActionItem {
 sku:            string
 name:           string
 supplier:      string | null
 // Set when the supplier came from the SKU's configured primary, or when the
 // buyer picked one in the cart. Free-text names from the SKU card have none.
 supplier_id:   string | null
 qty:            number
 recommended:    number   // original quantity Faro suggested (immutable)
 unit_cost:      number | null
 sale_price:   number | null   // sale price — for the margin-protected summary
 signal:         string
 days:           number | null
 lead_time:      number
 daily_demand: number | null   // forecasted daily demand — for the "why" panel
 current_stock:   number | null   // current stock — for the "why" panel
 // "Por qué" — every value below is computed by the backend, never here
 // user | file | supplier_rule | learned | default. 'default' is the case the
 // old 'learned' | 'configured' pair could not express, so this panel told the
 // buyer their lead time was "configurado por ti" when nobody had configured it.
 lead_time_source: ValueSource
 lead_time_rule_scope: RuleScope | null
 // Same question for the other three values that decide the order. The buyer
 // must be able to tell "I chose 95%" from "Faro assumed 95%" before approving.
 unit_cost_source: ValueSource
 service_level:        number | null
 service_level_source: ValueSource
 service_level_rule_scope: RuleScope | null
 moq:              number | null
 moq_source:       ValueSource
 moq_rule_scope:   RuleScope | null
 // State of the lead-time learning for this SKU's supplier: deliveries we have
 // recorded, and how many the planner needs before the learned average replaces
 // the configured value. The threshold comes from the payload
 // (MIN_LEAD_TIME_OBSERVATIONS), never from a literal here.
 lead_time_observations:        number | null
 lead_time_observations_needed: number | null
 lead_time_learned:             number | null
 reorder_point:    number | null
 // English fallback from the backend; the Spanish is rendered here from
 // explanation_code + explanation_params.
 explanation:      string | null
 explanation_code:   string | null
 explanation_params: Record<string, unknown> | null
 unit_margin:  number | null   // null = SKU sin price o sin cost
 reason:         string
 status:         ActionStatus
}

// `t` returns the key itself when the catalog has no entry for it; printing
// "hoy.assumptions_title" at the buyer is worse than plain English. Same guard
// `lib/explanationCopy.ts` uses before rendering a copy block.
export function tOr(
 t: (key: string, params?: Record<string, unknown>) => string,
 key: string, fallback: string, params?: Record<string, unknown>,
): string {
 const text = t(key, params)
 return text === key ? fallback : text
}

// ── Which of these numbers are ours, not yours? ──────────────────────────────
// Every planning value now carries a provenance, and 'default' is the one that
// means "nobody told us — this is Faro's assumption". The buyer approving an
// order deserves to know how much of it rests on our guesses before they spend
// the money, and to get one link to the screen that ranks those gaps by money.
//
// The count is derived from the recommendations actually on screen. It is never
// a fixed number and it is never shown when there is nothing to admit.
export const ASSUMPTION_FIELDS: { id: string; source: (i: InventoryStatusItem) => ValueSource | undefined }[] = [
 { id: 'lead_time',     source: i => i.lead_time_source },
 { id: 'service_level', source: i => i.service_level_source },
 { id: 'unit_cost',     source: i => i.unit_cost_source },
 { id: 'moq',           source: i => i.moq_source },
]

export interface AssumptionSummary {
 /** Which of the four planning fields we had to assume in at least one SKU. */
 fields: string[]
 /** SKUs on this screen resting on at least one assumption. */
 skus:   number
 /** SKUs examined, so the sentence can say "N of the M on this screen". Not the
  *  whole catalogue — the briefing caps each list — and the copy says so. */
 total:  number
}

export function summarizeAssumptions(briefing: MorningBriefing | null): AssumptionSummary {
 // Overstock counts too: "you already have enough" is a verdict built on the
 // same four numbers, and a tenant whose whole catalogue is overstocked would
 // otherwise never be told their configuration is missing.
 const items = [
  ...(briefing?.risks ?? []),
  ...(briefing?.warnings ?? []),
  ...(briefing?.overstocked ?? []),
 ]
 const fields = new Set<string>()
 let skus = 0
 for (const item of items) {
  let any = false
  for (const field of ASSUMPTION_FIELDS) {
   // No source at all means we cannot prove authorship, so it counts as ours —
   // claiming the buyer chose a number we cannot trace is the failure this
   // whole provenance change exists to remove. `isAssumed` treats it that way.
   if (isAssumed(field.source(item))) { fields.add(field.id); any = true }
  }
  if (any) skus++
 }
 return { fields: ASSUMPTION_FIELDS.map(f => f.id).filter(id => fields.has(id)), skus, total: items.length }
}

// English last-resort wording, one per provenance value. Spanish never appears
// here — it lives in the i18n catalog (CLAUDE.md).
const SOURCE_FALLBACK_EN: Record<ValueSource, string> = {
 user:          'you set it',
 file:          'from your file',
 supplier_rule: 'rule',
 learned:       'learned from your deliveries',
 default:       'estimated',
}

/** Names where a value came from, across all five sources. A rule hit also
 *  names the level that won, so the precedence is visible instead of being
 *  something the buyer has to reverse-engineer. */
export function provenanceText(
 t: (key: string, params?: Record<string, unknown>) => string,
 source?: ValueSource | null, scope?: RuleScope | null,
): string {
 const value: ValueSource = source || 'default'
 const label = tOr(t, sourceLabelKey(value), SOURCE_FALLBACK_EN[value])
 if (value === 'supplier_rule' && scope) {
  return `${label} · ${tOr(t, `explain.scope_${scope}`, scope)}`
 }
 return label
}

const ASSUMPTION_FIELD_FALLBACK_EN: Record<string, string> = {
 lead_time:     'the lead time',
 service_level: 'the service level',
 unit_cost:     'the unit cost',
 moq:           'the minimum order',
}

export function AssumptionsBanner({ summary, stacked = false }: {
 summary: AssumptionSummary
 /** Narrow screens put the CTA under the text instead of beside it; at 390px
  *  the side-by-side layout squeezes the sentence into a column of one word. */
 stacked?: boolean
}) {
 const { t } = useLanguage()
 // Nothing assumed: say nothing. A banner that reports zero is noise, and a
 // banner with an invented number is a lie.
 if (summary.fields.length === 0) return null

 const names = summary.fields.map(id => tOr(
  t, `hoy.assumption_field_${id}`, ASSUMPTION_FIELD_FALLBACK_EN[id] ?? id))
 const and = tOr(t, 'hoy.assumptions_list_and', 'and')
 const list = names.length === 1
  ? names[0]
  : `${names.slice(0, -1).join(', ')} ${and} ${names[names.length - 1]}`

 return (
  <Link
   href="/configurar-inventario"
   style={{
    display: 'flex',
    flexDirection: stacked ? 'column' : 'row',
    alignItems: 'flex-start', gap: 10, textDecoration: 'none',
    marginBottom: 20, padding: '12px 16px', borderRadius: 10,
    background: 'color-mix(in srgb, var(--accent) 6%, transparent)', border: '1px dashed color-mix(in srgb, var(--accent) 40%, transparent)',
   }}
  >
   <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flex: 1, minWidth: 0 }}>
    <Info size={15} color={C.indigo} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
    <div style={{ flex: 1, minWidth: 0 }}>
     <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>
      {summary.fields.length === 1
       ? tOr(t, 'hoy.assumptions_title_singular',
          'This recommendation uses 1 assumption of ours — review it')
       : tOr(t, 'hoy.assumptions_title_plural',
          `These recommendations use ${summary.fields.length} assumptions of ours — review them`,
          { n: summary.fields.length })}
     </div>
     <div style={{ fontSize: 12, color: C.muted, marginTop: 3, lineHeight: 1.5 }}>
      {tOr(t, 'hoy.assumptions_body',
       `You have not given us ${list} for ${summary.skus} of the ${summary.total} products below, so we used our own values.`,
       { fields: list, skus: summary.skus, total: summary.total })}
     </div>
    </div>
   </div>
   <span style={{
    display: 'inline-flex', alignItems: 'center', gap: 4, flexShrink: 0,
    fontSize: 12, fontWeight: 600, color: C.indigo, whiteSpace: 'nowrap',
    marginTop: stacked ? 0 : 2, marginLeft: stacked ? 25 : 0,
   }}>
    {tOr(t, 'hoy.assumptions_cta', 'See what to configure first')}
    <ArrowRight size={12} aria-hidden="true" />
   </span>
  </Link>
 )
}

// A muted dotted "estimado" badge on a value Faro assumed rather than received.
// Only assumed values are badged: no badge means the number is the buyer's own
// (typed, imported, ruled, or learned from their receptions). Mirrors the badge
// on /inventory so the two screens make the same promise.
export function SourceBadge({ source }: { source?: ValueSource | null }) {
 const { t } = useLanguage()
 if (!isAssumed(source)) return null
 return (
  <span
   title={tOr(t, 'inventory.source_assumed_tip',
    'Faro assumed this value — you have not given us one yet.')}
   style={{
    marginLeft: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
    textTransform: 'uppercase', padding: '1px 5px', borderRadius: 4,
    color: 'var(--dim)', border: '1px dashed var(--border)',
    background: 'transparent', whiteSpace: 'nowrap',
   }}
  >{tOr(t, 'inventory.source_assumed_badge', 'estimated')}</span>
 )
}

// ── "Nothing to do today" ─────────────────────────────────────────────────────
// The most confident thing this page ever says — and the exact claim stale data
// invalidates. Over a stock table nobody has touched in a month, the absence of
// a red signal is not evidence that nothing is wrong: it is evidence that we
// cannot see. So the green all-clear becomes an explicit "no lo podemos
// confirmar" instead of quietly reassuring the buyer.
//
// `unmeasured` is the harder version of the same problem: stale data is old
// data, but an uncounted catalogue is NO data. Every counter that would raise
// an alarm reads 0 because nothing was ever measured, so the all-clear here is
// not weak evidence — it is none at all, and it must not be shown as calm.
export function AllClear({ stale, unmeasured = false }: {
 stale: boolean
 unmeasured?: boolean
}) {
 const { t } = useLanguage()
 const doubtful = stale || unmeasured
 return (
  <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--muted)' }}>
   {stale && <div style={{ marginBottom: 10 }}><StaleSignalChip /></div>}
   <div style={{ fontSize: 15, marginBottom: 8 }}>
    {t(unmeasured ? 'hoy.no_pending_actions_unmeasured'
      : stale ? 'hoy.no_pending_actions_unverified'
      : 'hoy.no_pending_actions')}
   </div>
   <div style={{ fontSize: 13, color: 'var(--dim)' }}>
    {t(unmeasured ? 'hoy.inventory_unmeasured'
      : doubtful ? 'hoy.inventory_unverified'
      : 'hoy.inventory_under_control')}
   </div>
  </div>
 )
}

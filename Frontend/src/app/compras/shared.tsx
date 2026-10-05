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
import { Info, ArrowRight, Monitor, Truck, Check } from 'lucide-react'
import { isMobileReady } from '@/components/mobile/DesktopOnlyNotice'
import { isAssumed, sourceLabelKey, type RuleScope, type ValueSource } from '@/lib/inventoryDefaults'
import type { MorningBriefing, InventoryStatusItem, ServiceLevelCaveat, CoverageUnit, IncomingSource } from '@/lib/types'
import { incomingText } from '@/lib/incomingCopy'
import { formatMoney } from '@/lib/currency'
import { StaleSignalChip } from '@/components/ui/StaleDataBanner'
import { SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import type { InventorySignal } from '@/lib/types'
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
 red: '#C0504D',
 amber: '#B7791F',
 green: '#2E8B62',
 blue: '#4F7FB5',
 indigo: 'var(--accent)',
}

// ── The only place a semáforo colour appears on the Panel ─────────────────────
// A small dot beside a plain label. The colour never fills a card, a banner or
// a header: it marks the status and nothing else. The label is always there, so
// colour is never the only channel.
export function StatusMark({ signal, size = 13 }: { signal: string; size?: number }) {
 const { t } = useLanguage()
 const style = SIGNAL_STYLES[signal as InventorySignal]
 if (!style) return null
 return (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: size, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
   <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: style.fg, flexShrink: 0 }} />
   {t(style.labelKey)}
  </span>
 )
}

// ── Cart types ────────────────────────────────────────────────────────────────
// 'ordered' = this line is already on a purchase order generated from this
// screen. Terminal for the session: it leaves the cart, cannot be approved,
// edited or ordered again, and is never re-sent as a decision. Before it
// existed the line stayed 'approved' after "Descargar orden de compra", the
// cart bar stayed up, and a second tap wrote an identical second PO.
export type ActionStatus = 'pending' | 'approved' | 'modified' | 'rejected' | 'ordered'

export interface ActionItem {
 sku:            string
 name:           string
 supplier:      string | null
 // Set when the supplier came from the SKU's configured primary, or when the
 // buyer picked one in the cart. Free-text names from the SKU card have none.
 supplier_id:   string | null
 qty:            number
 recommended:    number   // original quantity StockAI suggested (immutable)
 unit_cost:      number | null
 sale_price:   number | null   // sale price — for the margin-protected summary
 signal:         string
 days:           number | null   // coverage, in `coverage_unit`
 coverage_unit?: CoverageUnit
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
 // must be able to tell "I chose 95%" from "StockAI assumed 95%" before approving.
 unit_cost_source: ValueSource
 service_level:        number | null
 service_level_source: ValueSource
 service_level_rule_scope: RuleScope | null
 // Measured unable to keep that service level (stability.md 17b): the panel
 // says so beside the percentage instead of printing it bare.
 service_level_caveat: ServiceLevelCaveat | null
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
 // Sales value a delay puts at risk (backend money_at_risk.py). null = value
 // unknown; absent on a backend older than the feature.
 money_at_risk?:       number | null
 money_at_risk_basis?: 'price' | 'cost' | 'unknown'
 // The line with the largest amount at risk in the whole list.
 order_first?:         boolean
 reason:         string
 status:         ActionStatus
 /** PO number (OC-000123) once the line was ordered from this screen. */
 ordered_ref?:   string | null
 /** The decision (approve/modify/reject) already went out on a PO, so the
  *  next order from this screen must not log it again. */
 decision_logged?: boolean
 /** Units already on open orders / transfers, and which ones — the reason
  *  the quantity is lower than the gap suggests (or 0). */
 incoming_qty?:     number
 incoming_sources?: IncomingSource[]
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
// means "nobody told us — this is StockAI's assumption". The buyer approving an
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

const SETUP_PATH = '/configurar-inventario'

export function AssumptionsBanner({ summary, stacked = false, flush = false }: {
 summary: AssumptionSummary
 /** Inside a container that owns the spacing (the Panel's review strip). */
 flush?: boolean
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
   href={SETUP_PATH}
   style={{
    display: 'flex',
    flexDirection: stacked ? 'column' : 'row',
    alignItems: 'flex-start', gap: 10, textDecoration: 'none',
    marginBottom: flush ? 0 : 20, padding: '12px 16px', borderRadius: 10,
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
   {/* On a phone this leads into a screen built for a computer. Say so before
       the tap, not after it (DesktopOnlyNotice decides which screens are). */}
   {stacked && !isMobileReady(SETUP_PATH) && (
    <span style={{
     display: 'inline-flex', alignItems: 'center', gap: 5,
     marginLeft: 25, marginTop: -4, fontSize: 11, color: C.dim,
    }}>
     <Monitor size={11} aria-hidden="true" />
     {t('mobile.opens_desktop_screen')}
    </span>
   )}
  </Link>
 )
}

// A muted dotted "estimado" badge on a value StockAI assumed rather than received.
// Only assumed values are badged: no badge means the number is the buyer's own
// (typed, imported, ruled, or learned from their receptions). Mirrors the badge
// on /inventory so the two screens make the same promise.
export function SourceBadge({ source }: { source?: ValueSource | null }) {
 const { t } = useLanguage()
 if (!isAssumed(source)) return null
 return (
  <span
   title={tOr(t, 'inventory.source_assumed_tip',
    'StockAI assumed this value — you have not given us one yet.')}
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
   {/* "Regístralo" used to be the whole call to action, with nothing to tap.
       Stock is recorded on /inventario. */}
   {unmeasured && (
    <Link
     href="/inventario"
     style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
      marginTop: 14, minHeight: 44, padding: '0 18px', borderRadius: 10, boxSizing: 'border-box',
      background: 'var(--accent)', color: '#fff', fontSize: 14, fontWeight: 700,
      textDecoration: 'none',
     }}
    >
     {t('hoy.inventory_unmeasured_cta')}
     <ArrowRight size={14} aria-hidden="true" />
    </Link>
   )}
  </div>
 )
}

// ── What is already on its way, and on which order ──────────────────────────
// The quantity on a card is NET of open purchase orders and transfers. Without
// this line a drop to 0 (or to less than the gap) reads as the app forgetting;
// with it the buyer sees exactly which order already covers the units.
export function IncomingNote({ item }: { item: ActionItem }) {
 const { t } = useLanguage()
 const text = incomingText(t, item.incoming_qty, item.incoming_sources)
 if (!text) return null
 return (
  <div style={{
   display: 'flex', alignItems: 'center', gap: 6, marginTop: 6,
   fontSize: 12, color: 'var(--accent)', overflowWrap: 'anywhere',
  }}>
   <Truck size={13} aria-hidden="true" style={{ flexShrink: 0 }} />
   <span>{text}</span>
  </div>
 )
}

// ── Money at risk: what waiting costs, and which line to order first ─────────
// The amount and its basis come from the backend (money_at_risk.py); this only
// words them. No value on file reads "value unknown" — never a zero.
export function MoneyAtRiskNote({ item }: { item: ActionItem }) {
 const { t } = useLanguage()
 if (item.status === 'ordered') return null
 const basis = item.money_at_risk_basis
 if (basis == null) return null // an older backend: say nothing rather than guess
 const amount = item.money_at_risk
 const known = amount != null && basis !== 'unknown'
 return (
  <div style={{
   display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 6,
   fontSize: 12, color: 'var(--muted)', overflowWrap: 'anywhere',
  }}>
   {item.order_first && (
    <span style={{
     display: 'inline-flex', alignItems: 'center', padding: '2px 8px', borderRadius: 20,
     fontSize: 10.5, fontWeight: 700, letterSpacing: '0.03em', textTransform: 'uppercase',
     color: 'var(--accent)', border: '1px solid var(--accent)',
    }}>
     {t('hoy.order_first_badge')}
    </span>
   )}
   <span>
    {known
     ? `${t('hoy.money_at_risk_label')}: ${formatMoney(amount)} (${t(basis === 'price' ? 'hoy.money_at_risk_basis_price' : 'hoy.money_at_risk_basis_cost')})`
     : t('hoy.money_at_risk_unknown')}
   </span>
  </div>
 )
}

/** Replaces the decision controls once the line is on a purchase order. */
export function OrderedNote({ item }: { item: ActionItem }) {
 const { t } = useLanguage()
 if (item.status !== 'ordered') return null
 const ref = item.ordered_ref ?? ''
 return (
  <div style={{ marginTop: 10 }}>
   <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
    <span style={{
     display: 'inline-flex', alignItems: 'center', gap: 5,
     fontSize: 12, fontWeight: 700, color: '#2E8B62',
     background: 'rgba(46,139,98,0.1)', padding: '3px 10px', borderRadius: 20,
    }}>
     <Check size={12} aria-hidden="true" />
     {tOr(t, 'hoy.line_ordered_badge', `Ordered on ${ref}`, { ref })}
    </span>
    <Link href="/pedidos" style={{
     display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: 32,
     fontSize: 12, color: 'var(--accent)', textDecoration: 'none',
    }}>
     {tOr(t, 'hoy.toast_view_orders', 'View orders')} <ArrowRight size={12} aria-hidden="true" />
    </Link>
   </div>
   <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 4, lineHeight: 1.45 }}>
    {tOr(t, 'hoy.line_ordered_hint',
     'It is already on a purchase order. It counts as on the way until you record the reception in Orders.')}
   </div>
  </div>
 )
}


'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { renderExplanation } from '@/lib/explanationCopy'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
 AlertTriangle, Clock, TrendingUp, TrendingDown, Archive,
 RefreshCw, ArrowRight, BarChart2, Package, Truck,
 ChevronDown, ChevronUp, Send, X, Upload, PlayCircle, Check,
} from 'lucide-react'
import {
 getMorningBriefing, getMorningNarrative, getPOHistory, optimizeInventory, logPOGeneration,
 getOverduePOs, sendPOToSuppliers, getSupplierContactHealth, getSupplierLeadTimeAlerts,
 evaluatePriceBreaks, getCashCalendar, checkCashFit, listSuppliers, ApiError,
} from '@/lib/api'
import type {
 MorningBriefing, BriefingRecommendation, MorningNarrative, NarrativeKeyPoint,
 DemandSpike, POLogEntry, CoverageUnit,
 OptimizationResponse, OptimizationOrder, POLineDecision, OverdueReception, SendPOResult,
 SupplierContactHealthRow, SupplierLeadTimeAlert, Supplier,
 PriceBreakEvaluation, CashCalendar, CashFitResult, InventoryStatusItem,
} from '@/lib/types'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { csvCell, csvNumber, buildCsv, downloadCsv } from '@/lib/csvWriter'
import {
 SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { TransferSuggestions } from '@/components/inventory/TransferSuggestions'
import { useWarehouses, defaultWarehouse } from '@/components/inventory/WarehouseControls'
import { coverageUnitLabel, daysPerUnit } from '@/lib/period'
import { PriceBreakPanel } from '@/components/inventory/PriceBreakPanel'
import { CashFitPanel } from '@/components/inventory/CashFitPanel'
import BudgetPanel, { BudgetChip, type BudgetNote } from '@/components/inventory/BudgetPanel'
import BudgetCartCheck from '@/components/inventory/BudgetCartCheck'
import { useAutoSession } from '@/hooks/useAutoSession'
import DataFreshness from '@/components/ui/DataFreshness'
import StaleDataBanner from '@/components/ui/StaleDataBanner'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import SignalBadge from '@/components/ui/SignalBadge'
import { getUser } from '@/lib/auth'
import Spinner from '@/components/ui/Spinner'
import {
  EmptyState, ErrorState, LoadingState, SkeletonCards, SkeletonTable, useErrorDetail,
} from '@/components/ui/States'
import NarrativeCard from '@/components/ui/NarrativeCard'
import HelpTip from '@/components/ui/HelpTip'
import { ReceptionModal } from '@/components/po/POHistory'
import { ForwardPOActions } from '@/components/po/ForwardPOActions'
import {
 RequestConfirmationCheckbox, useRequestConfirmationPref, confirmationNote,
 readRequestConfirmationPref,
} from '@/components/po/SupplierConfirmation'
import { RequestApprovalButton, usePOApproval } from '@/components/po/POApproval'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
// The honesty layer (assumptions banner, "estimado" badges, provenance wording,
// the unverified all-clear) and the cart's item shape live in ./shared so the
// narrow-screen card list below makes exactly the same promises as this table.
import {
  C, AllClear, StatusMark, SourceBadge, provenanceText, summarizeAssumptions,
  tOr, type ActionItem, type ActionStatus, IncomingNote, MoneyAtRiskNote, OrderedNote,
} from './shared'
import HoyMobile from './HoyMobile'
import { MoreAnalysis, StaleLine, analysisSections, useTabFold } from './folds'
import { StaleSignalChip } from '@/components/ui/StaleDataBanner'
import { fmtNum } from '@/lib/numberLocale'

// ── Formatters ────────────────────────────────────────────────────────────────
// Money formatting lives in lib/currency.ts — one source of truth for the whole
// app, in the anchor market's currency (CRC).
const fmtM = formatMoneyCompact
const fmtMoney = formatMoney

function timeSince(date: Date, t: (k: string) => string) {
 const mins = Math.floor((Date.now() - date.getTime()) / 60000)
 if (mins < 1) return t('hoy.time_just_now')
 if (mins === 1) return t('hoy.time_one_min_ago')
 return `${t('hoy.time_mins_ago_prefix')} ${mins} ${t('hoy.time_mins_ago_suffix')}`
}

// Day unit that agrees in number: "1 día" vs "N días". The risk cards read
// "1 días de stock" without it — the exact screen shown when stock is lowest.
function dayUnit(n: number, t: (k: string) => string) {
 return Math.round(n) === 1 ? t('hoy.reason_day_unit_singular') : t('hoy.reason_days_unit')
}

// The backend reports why a supplier was skipped as a stable code (English
// keys, localized here). Anything unrecognized — e.g. a transport error
// message — is shown as-is rather than swallowed.
function sendReason(reason: string, t: (k: string) => string) {
 const key = `roi.send_po_reason_${reason}`
 const label = t(key)
 return label === key ? reason : label
}

function formatDateES(isoDate: string, lang: string) {
 const d = new Date(isoDate + 'T12:00:00')
 return d.toLocaleDateString(lang, {
  weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
 })
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

// ── The morning briefing's recommendations, in the reader's language ──────────
//
// These sentences used to be composed in Spanish inside `inventory/service.py`
// and printed verbatim. Measured with the UI set to English: whole paragraphs
// of Spanish — "Emite la orden de Aceite de Oliva 1L HOY — tienes 2 días de
// stock…" — under the heading "Suggested action:". CLAUDE.md puts this the
// other way round: the backend sends a stable code plus params, the catalogue
// holds the wording.
//
// The backend's own `text`/`action` stay as the fallback, in English, for a
// deployment whose frontend is older than its API. A rough sentence beats a
// blank line.
//
// Coverage arrives as a raw number because its noun changes with the planning
// period (2 días vs 2 semanas) AND with the language; `coverageUnitLabel` owns
// that pairing already.
function recText(
  rec: BriefingRecommendation,
  coverageUnit: CoverageUnit | undefined,
  t: (k: string, p?: Record<string, unknown>) => string,
): string {
  // `text_code` lets one rec_type have two wordings — a named supplier and
  // no supplier need different sentences in both languages.
  const key = `hoyrec.${rec.text_code ?? rec.rec_type}`
  const params = { ...(rec.text_params ?? {}) } as Record<string, unknown>
  if (typeof params.days === 'number') {
    params.coverage = `${params.days} ${coverageUnitLabel(coverageUnit, params.days as number, t)}`
  }
  if (typeof params.excess === 'number') {
    params.excess_coverage =
      `${params.excess} ${coverageUnitLabel(coverageUnit, params.excess as number, t)}`
  }
  if (typeof params.lead_days === 'number') {
    params.lead = `${params.lead_days} ${dayUnit(params.lead_days as number, t)}`
  }
  const text = t(key, params)
  return text === key ? rec.text : text
}

// The one-line summary under the narrative card. It is composed by the backend
// even when the AI call SUCCEEDS — the narrative itself comes back in the
// reader's language, and these three sentences arrived in Spanish underneath it.
function keyPointText(
  point: NarrativeKeyPoint,
  t: (k: string, p?: Record<string, unknown>) => string,
): string {
  const key = `narrative.kp.${point.code}`
  const text = t(key, point.params ?? {})
  return text === key ? point.text : text
}

function recAction(
  rec: BriefingRecommendation,
  t: (k: string, p?: Record<string, unknown>) => string,
): string {
  if (!rec.action_code) return rec.action
  const key = `hoyrec.action.${rec.action_code}`
  const text = t(key, (rec.action_params ?? {}) as Record<string, unknown>)
  return text === key ? rec.action : text
}

// The "order X today" / "order X this week" recommendations are the action
// cards at the top of the page, said a second time further down: same SKU,
// same coverage, same lead time — and only the card can be acted on. Those are
// left out of the recommendations list whenever the SKU already has its card.
// A recommendation for a SKU without a card (and every other kind) still shows.
function recommendationsNotOnCards(b: MorningBriefing): BriefingRecommendation[] {
 const carded = new Set([...(b.risks ?? []), ...(b.warnings ?? [])].map(i => i.sku))
 return b.recommendations.filter(rec =>
  !((rec.rec_type === 'STOCKOUT_RISK' || rec.rec_type === 'REORDER_SOON') && carded.has(rec.sku)),
 )
}

function RecIcon({ rec_type }: { rec_type: BriefingRecommendation['rec_type'] }) {
 // One muted tone for all of them: the kind is in the icon's shape, not its colour.
 const color = 'var(--dim)'
 switch (rec_type) {
  case 'STOCKOUT_RISK': return <AlertTriangle size={16} color={color} />
  case 'REORDER_SOON': return <Clock size={16} color={color} />
  case 'DEMAND_UP': return <TrendingUp size={16} color={color} />
  case 'DEMAND_DOWN': return <TrendingDown size={16} color={color} />
  case 'OVERSTOCK': return <Archive size={16} color={color} />
 }
}

// ── Lead-time learning state ────────────────────────────────────────────────
// `resolve_lead_time` upgrades a supplier's lead time from their real
// receptions, but only past MIN_LEAD_TIME_OBSERVATIONS of them — a bar a new
// tenant never clears, so the fallback stayed silent and permanent-looking.
// Saying how many deliveries we have and how many we need turns it into a
// promise the buyer can wait for. The threshold arrives with the data; a
// literal here would be free to disagree with what the planner applies.
function LeadTimeLearning({ item }: { item: ActionItem }) {
 const { t } = useLanguage()
 const style: React.CSSProperties = {
  fontSize: 11, color: 'var(--dim)', lineHeight: 1.5,
  marginTop: 10, paddingTop: 8, borderTop: '1px dashed var(--border)',
 }

 if (!item.supplier) {
  return (
   <div style={style}>
    {tOr(t, 'inventory.lead_time_learning_no_supplier',
     'This product has no supplier, so we cannot learn its real lead time. Assign one and we start measuring.')}
   </div>
  )
 }

 const needed = item.lead_time_observations_needed
 // Older backend: say nothing rather than invent a threshold.
 if (needed == null) return null
 const seen = item.lead_time_observations ?? 0
 const supplier = item.supplier

 // 'learned' is the only state where the average is the number the planner
 // used; below the threshold it exists but is deliberately ignored.
 if (item.lead_time_source === 'learned' && item.lead_time_learned != null) {
  return (
   <div style={{ ...style, color: C.green }}>
    {tOr(t, 'inventory.lead_time_learning_active',
     `Learned from ${seen} of your deliveries from ${supplier}: ${item.lead_time_learned} days on average, and that is the number we use.`,
     { supplier, n: seen, days: item.lead_time_learned })}
   </div>
  )
 }
 if (seen === 0) {
  return (
   <div style={style}>
    {tOr(t, 'inventory.lead_time_learning_none',
     `We have no deliveries from ${supplier} yet. Once you record ${needed}, we adjust the lead time on our own.`,
     { supplier, needed })}
   </div>
  )
 }
 return (
  <div style={style}>
   {tOr(t, 'inventory.lead_time_learning_partial',
    `${seen} of ${needed} deliveries from ${supplier} recorded. ${needed - seen} more and we adjust the lead time on our own.`,
    { supplier, n: seen, needed, missing: needed - seen })}
  </div>
 )
}

// ── ActionCard component ──────────────────────────────────────────────────────
function ActionCard({ item, onApprove, onReject, onUndo, onChangeQty, suppliers, onChangeSupplier, canDecide, tourAnchor, tourAnchors, stale = false, noContact = false, lateAlert = null, budgetNote = null }: {
 item:        ActionItem
 /** Where the purchase budget puts this line: funded, partly, or deferred. */
 budgetNote?: BudgetNote | null
 /** This line's supplier has no email or WhatsApp: an order would skip it. */
 stale?:      boolean
 noContact?:  boolean
 /** This line's supplier is running later than its own history. */
 lateAlert?:  SupplierLeadTimeAlert | null
 onApprove:   () => void
 onReject:    () => void
 /** Take the line back OUT of the cart — NOT the same thing as rejecting it.
  *  The "Deshacer" button below was wired to `onReject`, so a buyer undoing
  *  their own mis-tap told StockAI the recommendation had been bad: that verdict
  *  reaches POST /inventory/log-po, is persisted on inventory_po_items, and
  *  is what /impacto counts as adoption feedback. The mobile card has had the
  *  correct handler all along (`unapproveItem`), so the two views recorded
  *  different things for the same gesture. */
 onUndo:      () => void
 onChangeQty: (qty: number) => void
 suppliers:   Supplier[]
 onChangeSupplier: (supplierId: string) => void
 /** A viewer can read every recommendation but cannot turn one into an order:
  *  the only exit from this cart is POST /inventory/log-po, which their role
  *  is refused. Approving would build a basket that can only fail at the end. */
 canDecide:   boolean
 /** Set on the first card only — a tour anchor has to be unique in the DOM,
  *  and this used to repeat once per recommendation. */
 tourAnchor?: string
 /** Same rule, for the three controls inside the card the tour explains one
  *  by one. Undefined on every card but the first. */
 tourAnchors?: { supplier?: string; qty?: string; decide?: string }
}) {
 const { t } = useLanguage()
 const [editing, setEditing] = useState(false)
 const [qtyInput, setQtyInput] = useState(String(item.qty))
 const [showWhy, setShowWhy] = useState(false)
 // Mounted closed, opened one painted frame later, so `.reveal-panel` has a
 // starting `grid-template-rows: 0fr` to animate away from. Mounting the panel
 // already open gives the browser nothing to interpolate and it snaps.
 const [whyOpen, setWhyOpen] = useState(false)

 // Keep qtyInput in sync when item.qty changes externally (e.g. after reset)
 useEffect(() => {
  setQtyInput(String(item.qty))
 }, [item.qty])

 useEffect(() => {
  if (!showWhy) { setWhyOpen(false); return }
  // Two frames: the first commits the closed state, the second flips it once
  // the browser has painted it.
  let inner = 0
  const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => setWhyOpen(true)) })
  return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner) }
 }, [showWhy])

 const isUrgent  = item.signal === 'PEDIR_YA'
 const accent    = isUrgent ? 'var(--signal-order-now-fg)' : 'var(--signal-order-soon-fg)'
 const isApproved = item.status === 'approved' || item.status === 'modified'
 const isRejected = item.status === 'rejected'
 const isOrdered  = item.status === 'ordered'

 const estimatedValue = item.qty * (item.unit_cost ?? 0)
 const canOrder = item.qty > 0
 const hasWhyData = item.daily_demand != null || item.current_stock != null
  || item.days != null || item.explanation != null

 return (
  <div style={{
   padding:    '18px 20px',
   background: 'var(--surface)',
   opacity:    isRejected ? 0.55 : 1,
   transition: 'opacity var(--dur-2) var(--ease-out)',
  }}>

   {/* Header */}
   <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10, gap: 12 }}>
    <div style={{ minWidth: 0 }}>
     <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{item.name}</span>
      <StatusMark signal={item.signal} size={12} />
      {stale && <StaleSignalChip title={t('freshness.banner_title')} />}
      {/* The code only when the name is not already the code — "SKU-005
          SKU-005" said the same thing twice. Same rule as the phone card. */}
      {item.name !== item.sku && (
       <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--dim)' }}>{item.sku}</span>
      )}
     </div>
     <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4, lineHeight: 1.5 }}>{item.reason}</div>
     <MoneyAtRiskNote item={item} />
     <IncomingNote item={item} />
     {/* Supplier is a decision, not a label: the buyer can send this line to
         whoever they want before the order is generated. Which is exactly why
         it is a picker only for a role that can generate one — re-pointing a
         line also flips it to `modified` and fills the cart. A viewer gets the
         same read-only label a tenant with no suppliers loaded already sees. */}
     <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginTop: 6 }}>
      {suppliers.length > 0 && canDecide && !isOrdered ? (
       <label data-tour={tourAnchors?.supplier} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.cart_supplier_label')}</span>
        <select
         value={item.supplier_id ?? (suppliers.find(s => s.name === item.supplier)?.id ?? '')}
         onChange={e => onChangeSupplier(e.target.value)}
         aria-label={t('hoy.cart_supplier_label')}
         style={{
          fontSize: 12, padding: '3px 6px', borderRadius: 6,
          border: '1px solid var(--border)', background: 'var(--surface)',
          color: 'var(--text)', maxWidth: 220,
         }}
        >
         <option value="">{t('hoy.cart_supplier_none')}</option>
         {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
       </label>
      ) : item.supplier ? (
       <span style={{ fontSize: 12, color: 'var(--dim)' }}>{item.supplier}</span>
      ) : null}
      {/* What used to be banners, now where it concerns: the supplier of THIS
          line. Plain text, no fill. */}
      {noContact && item.supplier && (
       <span style={{ fontSize: 12, color: 'var(--muted)' }}>
        {t('hoy.chip_no_contact')}{' · '}
        <Link href={`/proveedores?focus=${encodeURIComponent(item.supplier)}`} style={{ color: 'var(--accent)', fontWeight: 600, textDecoration: 'none' }}>
         {t('hoy.chip_complete')}
        </Link>
       </span>
      )}
      {budgetNote && <BudgetChip note={budgetNote} />}
      {lateAlert && (
       <span style={{ fontSize: 12, color: 'var(--muted)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        <Clock size={12} aria-hidden="true" />
        {t('hoy.chip_late_supplier', { recent: lateAlert.lead_time_recent, usual: lateAlert.lead_time_historical })}
       </span>
      )}
     </div>
    </div>
    {hasWhyData && (
     <button
      data-tour={tourAnchor}
      onClick={() => setShowWhy(v => !v)}
      style={{
       all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3,
       fontSize: 11, color: 'var(--dim)', flexShrink: 0, padding: '3px 6px',
      }}
     >
      {showWhy ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      {showWhy ? t('hoy.why_toggle_hide') : t('hoy.why_toggle_show')}
     </button>
    )}
   </div>

   {/* "Why" panel — plain-language breakdown behind the recommendation.
       The explanatory sentence and every number in it come from the backend
       (inventory/service.py); nothing here is computed client-side. */}
   {showWhy && (
    <div className="reveal-panel" data-open={whyOpen ? 'true' : 'false'}>
     <div>
    <div style={{
     background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8,
     padding: '10px 12px', marginBottom: 12, fontSize: 12,
    }}>
     {(() => {
      // The reasoning comes from the backend as a stable code + params; the
      // wording is rendered here so the Spanish lives in the i18n catalog.
      const sentence = renderExplanation(
       t, item.explanation_code, item.explanation_params, item.explanation)
      return sentence ? (
       <p style={{
        margin: '0 0 10px', fontSize: 13, lineHeight: 1.55, color: 'var(--text)',
       }}>
        {sentence}
       </p>
      ) : null
     })()}
     <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10,
     }}>
     {item.days != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_coverage_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {Math.round(item.days)} {coverageUnitLabel(item.coverage_unit, Math.round(item.days), t)}
       </div>
      </div>
     )}
     {item.daily_demand != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_demand_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {fmtNum(item.daily_demand, { maximumFractionDigits: 1 })} {t('hoy.why_units_day')}
       </div>
      </div>
     )}
     <div>
      <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
       {t('hoy.why_lead_time_label')}
      </div>
      <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
       {item.lead_time} {t('hoy.why_days')}
       <SourceBadge source={item.lead_time_source} />
      </div>
      <div style={{ color: 'var(--dim)', fontSize: 10, marginTop: 2 }}>
       {provenanceText(t, item.lead_time_source, item.lead_time_rule_scope)}
      </div>
     </div>
     {/* The other three values the order rests on. Each one the buyer never
         gave us is badged, so approving is a decision made with the guesses
         in plain sight instead of buried in the backend. */}
     {item.service_level != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {tOr(t, 'hoy.why_service_level_label', 'Service level')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {Math.round(item.service_level * 100)}%
        <SourceBadge source={item.service_level_source} />
       </div>
       <div style={{ color: 'var(--dim)', fontSize: 10, marginTop: 2 }}>
        {provenanceText(t, item.service_level_source, item.service_level_rule_scope)}
       </div>
       {item.service_level_caveat && (
        <div role="note" style={{ color: 'var(--warning)', fontSize: 10.5, marginTop: 4, lineHeight: 1.35 }}>
         {t(`hoy.service_level_caveat_${item.service_level_caveat}`, { pct: Math.round(item.service_level * 100) })}
        </div>
       )}
      </div>
     )}
     <div>
      <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
       {tOr(t, 'hoy.why_unit_cost_label', 'Unit cost')}
      </div>
      <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
       {item.unit_cost != null ? fmtMoney(item.unit_cost) : '—'}
       <SourceBadge source={item.unit_cost_source} />
      </div>
      <div style={{ color: 'var(--dim)', fontSize: 10, marginTop: 2 }}>
       {provenanceText(t, item.unit_cost_source)}
      </div>
     </div>
     {item.moq != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        MOQ
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {fmtNum(Math.round(item.moq))}
        <SourceBadge source={item.moq_source} />
       </div>
       <div style={{ color: 'var(--dim)', fontSize: 10, marginTop: 2 }}>
        {provenanceText(t, item.moq_source, item.moq_rule_scope)}
       </div>
      </div>
     )}
     {item.current_stock != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_stock_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {fmtNum(Math.round(item.current_stock))} {t('hoy.why_units')}
       </div>
      </div>
     )}
     {item.reorder_point != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_reorder_point_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {fmtNum(Math.round(item.reorder_point))} {t('hoy.why_units')}
       </div>
      </div>
     )}
     </div>
     {/* When the lead time is still ours, say what would make it theirs. */}
     <LeadTimeLearning item={item} />
    </div>
     </div>
    </div>
   )}

   {/* Once the line is on a PO it is done for this screen: no quantity to
       edit and no button that could put it on a second order. */}
   {isOrdered && <OrderedNote item={item} />}

   {/* Quantity + Value + Actions */}
   {!isRejected && !isOrdered && (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
     <div data-tour={tourAnchors?.qty} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.label_order_qty')}</span>
      {/* Not cosmetic for a viewer: changing the quantity marks the line
          `modified`, which is one of the two statuses that fill the cart — so
          leaving it editable would put the "Generate PO" bar back on screen
          for someone whose role can never generate one. */}
      {!canDecide ? (
       <span style={{ fontSize: 17, fontWeight: 600, color: 'var(--text)', lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
        {fmtNum(item.qty)}
       </span>
      ) : editing ? (
       <input
        type="number" min={0} value={qtyInput}
        name="order_qty" aria-label={t('hoy.label_order_qty')}
        onChange={e => setQtyInput(e.target.value)}
        onBlur={() => {
         const n = parseInt(qtyInput)
         if (!isNaN(n) && n > 0) { onChangeQty(n); setEditing(false) }
         else { setQtyInput(String(item.qty)); setEditing(false) }
        }}
        onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        autoFocus
        style={{
         width: 100, background: 'var(--surface-2)', border: '1px solid var(--accent)',
         borderRadius: 6, padding: '4px 8px', color: 'var(--text)',
         fontSize: 14, fontWeight: 700, outline: 'none',
        }}
       />
      ) : (
       <button onClick={() => setEditing(true)} style={{
        all: 'unset', cursor: 'pointer', fontSize: 17, fontWeight: 600, color: 'var(--text)',
        borderBottom: '1px dashed var(--border-strong)', lineHeight: 1.1, fontVariantNumeric: 'tabular-nums',
       }}>
        {fmtNum(item.qty)}
       </button>
      )}
      <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.label_units')}</span>
      {estimatedValue > 0 && (
       <span style={{ fontSize: 12, color: 'var(--dim)', fontVariantNumeric: 'tabular-nums' }}>
        ≈ {fmtMoney(estimatedValue)}
       </span>
      )}
     </div>

     {/* Action buttons */}
     {!canDecide ? (
      <span style={{ fontSize: 12, color: 'var(--dim)', fontStyle: 'italic', marginLeft: 'auto' }}>
       {t('hoy.decide_role_readonly')}
      </span>
     ) : !isApproved ? (
      <div data-tour={tourAnchors?.decide} style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
       {canOrder ? (
        <>
         <button onClick={onApprove} style={{
          all: 'unset', cursor: 'pointer', padding: '7px 16px', borderRadius: 8,
          background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 5,
         }}>
          {t('hoy.btn_approve')}
         </button>
         <button onClick={onReject} style={{
          all: 'unset', cursor: 'pointer', padding: '7px 12px', borderRadius: 8,
          border: '1px solid var(--border)', color: 'var(--dim)', fontSize: 13,
         }}>
          {t('hoy.btn_reject')}
         </button>
        </>
       ) : (
        <span style={{ fontSize: 12, color: 'var(--dim)', fontStyle: 'italic' }}>
         {(item.incoming_qty ?? 0) > 0 ? t('hoy.covered_by_incoming') : t('hoy.enough_stock')}
        </span>
       )}
      </div>
     ) : (
      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 12 }}>
       <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, fontWeight: 600, color: 'var(--accent)' }}>
        <Check size={14} aria-hidden="true" /> {t('hoy.badge_approved')}
       </span>
       <button onClick={onUndo} style={{
        all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--dim)', textDecoration: 'underline',
       }}>
        {t('hoy.btn_undo')}
       </button>
      </span>
     )}
    </div>
   )}

   {isRejected && canDecide && (
    <button onClick={onApprove} style={{
     all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--dim)', textDecoration: 'underline',
    }}>
     {t('hoy.btn_restore')}
    </button>
   )}
  </div>
 )
}

// ── SpikeCard: proactive future-peak alert ───────────────────────────────────
function shortDateES(iso: string | null, lang: string): string {
 if (!iso) return '—'
 const d = new Date(iso + 'T12:00:00')
 return d.toLocaleDateString(lang, { weekday: 'long', day: 'numeric', month: 'long' })
}

function SpikeCard({ s }: { s: DemandSpike }) {
 const { t, lang } = useLanguage()
 return (
  <div style={{ padding: '14px 0', borderTop: '1px solid var(--border)' }}>
   <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
    <span style={{ fontSize: 14, fontWeight: 600, color: C.text }}>{s.display_name}</span>
    <span style={{ fontSize: 12, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>+{s.uplift_pct}% {t('hoy.spike_projected')}</span>
   </div>
   <div style={{ fontSize: 13, color: C.muted, marginTop: 4, lineHeight: 1.5 }}>
    {t('hoy.spike_peak_expected_prefix')} <strong style={{ color: C.text, fontWeight: 600 }}>{shortDateES(s.peak_date, lang)}</strong>
    {' '}({t('hoy.spike_in_days_prefix')} {s.days_until_peak} {t('hoy.spike_days_unit')}{s.days_until_peak !== 1 ? 's' : ''}).
    {' '}{t('hoy.spike_supplier_lead_prefix')} {s.lead_time_days} {t('hoy.spike_days_unit')}.
   </div>
   <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 6, fontSize: 13, fontWeight: 600, color: C.text }}>
    <Clock size={13} aria-hidden="true" color="var(--dim)" />
    {s.already_late
     ? t('hoy.spike_already_late')
     : <>{t('hoy.spike_order_before_prefix')} {shortDateES(s.order_by_date, lang)} {t('hoy.spike_order_before_suffix')}</>}
   </div>
  </div>
 )
}

// ── Build ActionItems from briefing ──────────────────────────────────────────
// The status rows now also carry the lead-time learning counters. They are
// declared here rather than in lib/types.ts because that file belongs to
// another change in flight; both fields are optional, so an older backend
// simply reports no learning state instead of breaking.
type BriefingItem = InventoryStatusItem & {
 lead_time_observations?:        number
 lead_time_observations_needed?: number
 money_at_risk?:                 number | null
 money_at_risk_basis?:           'price' | 'cost' | 'unknown'
}

function buildActionItems(b: MorningBriefing, t: (k: string) => string): ActionItem[] {
 const items: ActionItem[] = []
 // Coverage figures are in the briefing's active period unit (weeks under a
 // weekly session); lead time is always real calendar days.
 const cu = b.coverage_unit

 for (const risk of ((b.risks ?? []) as BriefingItem[])) {
  const d = risk.coverage_days != null ? Math.round(risk.coverage_days) : null
  const reason = d != null
   ? `${t('hoy.reason_stock_left_prefix')} ${d} ${coverageUnitLabel(cu, d, t)} ${t('hoy.reason_stock_left_suffix')} ${risk.lead_time_days} ${dayUnit(risk.lead_time_days, t)}`
   : `${t('hoy.reason_immediate_risk')} — ${t('hoy.reason_lead_time_label')} ${risk.lead_time_days} ${dayUnit(risk.lead_time_days, t)}`
  items.push({
   sku:            risk.sku,
   name:           risk.display_name || risk.sku,
   supplier:      risk.supplier || null,
   supplier_id:   risk.supplier_id ?? null,
   qty:            risk.recommended_qty ?? 0,
   recommended:    risk.recommended_qty ?? 0,
   unit_cost:      risk.unit_cost ?? null,
   sale_price:   risk.sale_price ?? null,
   signal:         'PEDIR_YA',
   days:           risk.coverage_days ?? null,
   lead_time:      risk.lead_time_days,
   // Per DAY, as the panel labels it: the briefing's figure is per bucket of
   // the planning period (per week on a weekly tenant).
   daily_demand: risk.daily_demand != null ? risk.daily_demand / daysPerUnit(cu) : null,
   coverage_unit:  cu,
   current_stock:   risk.current_stock ?? null,
   // No source at all means we cannot prove authorship, so it reads as our
   // assumption — never as something the user configured.
   lead_time_source: risk.lead_time_source ?? 'default',
   lead_time_rule_scope: risk.lead_time_rule_scope ?? null,
   unit_cost_source: risk.unit_cost_source ?? 'default',
   service_level:        risk.service_level ?? null,
   service_level_source: risk.service_level_source ?? 'default',
   service_level_rule_scope: risk.service_level_rule_scope ?? null,
   service_level_caveat: risk.service_level_caveat ?? null,
   moq:              risk.moq ?? null,
   moq_source:       risk.moq_source ?? 'default',
   moq_rule_scope:   risk.moq_rule_scope ?? null,
   lead_time_observations:        risk.lead_time_observations ?? null,
   lead_time_observations_needed: risk.lead_time_observations_needed ?? null,
   lead_time_learned:             risk.lead_time_learned ?? null,
   reorder_point:    risk.reorder_point ?? null,
   explanation:      risk.explanation ?? null,
   explanation_code:   risk.explanation_code ?? null,
   explanation_params: risk.explanation_params ?? null,
   unit_margin:  risk.unit_margin ?? null,
   incoming_qty:     risk.incoming_qty ?? 0,
   incoming_sources: risk.incoming_sources ?? [],
   money_at_risk:       risk.money_at_risk ?? null,
   money_at_risk_basis: risk.money_at_risk_basis,
   reason,
   status:      'pending',
  })
 }

 for (const w of ((b.warnings ?? []) as BriefingItem[])) {
  const d = w.coverage_days != null ? Math.round(w.coverage_days) : null
  items.push({
   sku:            w.sku,
   name:           w.display_name || w.sku,
   supplier:      w.supplier || null,
   supplier_id:   w.supplier_id ?? null,
   qty:            w.recommended_qty ?? 0,
   recommended:    w.recommended_qty ?? 0,
   unit_cost:      w.unit_cost ?? null,
   sale_price:   w.sale_price ?? null,
   signal:         'PEDIR_PRONTO',
   days:           w.coverage_days ?? null,
   lead_time:      w.lead_time_days,
   daily_demand: w.daily_demand != null ? w.daily_demand / daysPerUnit(cu) : null,
   coverage_unit:  cu,
   current_stock:   w.current_stock ?? null,
   lead_time_source: w.lead_time_source ?? 'default',
   lead_time_rule_scope: w.lead_time_rule_scope ?? null,
   unit_cost_source: w.unit_cost_source ?? 'default',
   service_level:        w.service_level ?? null,
   service_level_source: w.service_level_source ?? 'default',
   service_level_rule_scope: w.service_level_rule_scope ?? null,
   service_level_caveat: w.service_level_caveat ?? null,
   moq:              w.moq ?? null,
   moq_source:       w.moq_source ?? 'default',
   moq_rule_scope:   w.moq_rule_scope ?? null,
   lead_time_observations:        w.lead_time_observations ?? null,
   lead_time_observations_needed: w.lead_time_observations_needed ?? null,
   lead_time_learned:             w.lead_time_learned ?? null,
   reorder_point:    w.reorder_point ?? null,
   explanation:      w.explanation ?? null,
   explanation_code:   w.explanation_code ?? null,
   explanation_params: w.explanation_params ?? null,
   unit_margin:  w.unit_margin ?? null,
   incoming_qty:     w.incoming_qty ?? 0,
   incoming_sources: w.incoming_sources ?? [],
   money_at_risk:       w.money_at_risk ?? null,
   money_at_risk_basis: w.money_at_risk_basis,
   reason:     `${d != null ? d + ' ' + coverageUnitLabel(cu, d, t) + ' ' + t('hoy.reason_coverage_suffix') : t('hoy.reason_next_order_recommended')} — ${t('hoy.reason_order_this_week')}`,
   status:      'pending',
  })
 }

 // "Order first": the line with the most money at risk, only when at least one
 // line has an amount (a tenant with no prices or costs sees no ranking).
 let top: ActionItem | null = null
 for (const it of items) {
  if ((it.money_at_risk ?? 0) > (top?.money_at_risk ?? 0)) top = it
 }
 if (top) top.order_first = true

 return items
}

// ── Designed empty state (feature 1.2) ────────────────────────────────────────
// `/hoy` is the post-login landing page, so for a brand-new tenant this screen
// IS the onboarding. Instead of "select a trained session", it states plainly
// that there's no data yet, shows what the page will contain once there is, and
// offers the two real ways forward: upload a sales file, or run the bundled
// demo (POST /demo/quickstart, driven by /quick-start?demo=1).
function HoyEmptyState({ variant }: { variant: 'no_session' | 'no_inventory' }) {
 const { t } = useLanguage()
 const isNoSession = variant === 'no_session'

 return (
  <div style={{
   display: 'flex', flexDirection: 'column', alignItems: 'center',
   justifyContent: 'center', flex: 1, padding: '56px 24px',
  }}>
   <EmptyState
    icon={isNoSession ? <BarChart2 size={24} /> : <Package size={24} />}
    title={isNoSession ? t('hoy.empty_title') : t('hoy.no_inventory_data')}
    body={isNoSession ? t('hoy.empty_body') : t('hoy.no_inventory_data_hint')}
    bullets={isNoSession ? [
     t('hoy.empty_bullet_1'),
     t('hoy.empty_bullet_2'),
     t('hoy.empty_bullet_3'),
    ] : undefined}
    actions={isNoSession ? [
     { label: t('hoy.empty_cta_primary'), href: '/ventas', icon: <Upload size={15} /> },
     { label: t('hoy.empty_cta_demo'), href: '/ventas?demo=1', icon: <PlayCircle size={15} />, variant: 'secondary' },
    ] : [
     { label: t('hoy.link_go_inventory'), href: '/inventario', icon: <Upload size={15} /> },
    ]}
   />

   {isNoSession && (
    <p style={{ fontSize: 12, color: C.dim, margin: '14px 0 0', lineHeight: 1.5, maxWidth: 560, textAlign: 'center' }}>
     {t('hoy.empty_demo_hint')}
    </p>
   )}
  </div>
 )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function HoyPage() {
 const { t, lang } = useLanguage()
 const { sessionId, setSessionId, currentSession, completedSessions, loading: sessionsLoading, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 // Translates an ApiError's `error_code` + `params` into the user's language.
 const errorDetail = useErrorDetail()
 const [briefing, setBriefing]             = useState<MorningBriefing | null>(null)
 const [loading, setLoading]               = useState(false)
 // Raw error so ErrorState can classify it by kind.
 const [error, setError]                   = useState<unknown>(null)
 const [loadedAt, setLoadedAt]             = useState<Date | null>(null)
 const [narrative, setNarrative]           = useState<MorningNarrative | null>(null)
 const [loadingNarrative, setLoadingNarrative] = useState(false)

 // Work-queue cart state
 const [cart, setCart] = useState<ActionItem[]>([])

 // Orders awaiting reception
 const [pendingPOs, setPendingPOs] = useState<POLogEntry[]>([])

 // Purchasing/transfers optimizer plan (MW-3)
 const [optimization, setOptimization] = useState<OptimizationResponse | null>(null)
 const [optimizationLoading, setOptimizationLoading] = useState(false)

 // Suppliers the PO-send path would skip (feature 2.5) and suppliers drifting
 // off their historical lead time (feature 3.3) — both computed server-side.
 const [contactHealth, setContactHealth] = useState<SupplierContactHealthRow[]>([])
 // Loaded once so every cart line can offer the same supplier picker without
 // one request per line.
 const [suppliers, setSuppliers] = useState<Supplier[]>([])
 const [leadTimeAlerts, setLeadTimeAlerts] = useState<SupplierLeadTimeAlert[]>([])

 // Price-break opportunities for the current cart (feature 3.5) and the cash
 // picture the cart has to fit into (feature 3.6).
 const [priceBreaks, setPriceBreaks] = useState<PriceBreakEvaluation | null>(null)
 const [cashCalendar, setCashCalendar] = useState<CashCalendar | null>(null)
 const [cashBudget, setCashBudget] = useState<number | null>(null)
 // Purchase budget: the per-SKU annotation of the plan, and the reason an
 // order that exceeds a budget goes out with (see BudgetCartCheck).
 const [budgetNotes, setBudgetNotes] = useState<Record<string, BudgetNote>>({})
 const [budgetReason, setBudgetReason] = useState('')
 const [budgetReload, setBudgetReload] = useState(0)
 const [cashFit, setCashFit] = useState<CashFitResult | null>(null)
 const [cashFitBusy, setCashFitBusy] = useState(false)

 // POs whose expected arrival (learned supplier lead time) has already passed
 const [overduePOs, setOverduePOs] = useState<OverdueReception[]>([])

 // PO currently being received via the reused ReceptionModal (feature: overdue nudge)
 const [receivingPO, setReceivingPO] = useState<string | null>(null)

 // Destination warehouse for the PO cart (5.4). Only meaningful when the
 // tenant has ≥2 warehouses; mono-warehouse tenants never send it.
 const { warehouses, multi } = useWarehouses()
 const [destWarehouse, setDestWarehouse] = useState<string>('')
 useEffect(() => {
  if (warehouses.length > 0 && destWarehouse === '') {
   // Shared with the manual-PO modal so both destination pickers agree on
   // which warehouse is the default (see defaultWarehouse).
   const def = defaultWarehouse(warehouses)
   if (def) setDestWarehouse(def.name)
  }
 }, [warehouses, destWarehouse])

 // Generate→send in one flow: the PO just logged, awaiting the "send now" decision
 const [generatedPO, setGeneratedPO]   = useState<POLogEntry | null>(null)
 // Opt-in approval workflow: `required` only for a tenant with an approval rule
 // the order matches. Until it is approved the order cannot be sent from here.
 const { data: poApproval, reload: reloadPoApproval } = usePOApproval(generatedPO?.id)
 const [generatedLines, setGeneratedLines] = useState<ActionItem[]>([])
 const [sendState, setSendState]       = useState<'idle' | 'sending' | 'done'>('idle')
 // A FAILED send, kept apart from `sendResult`. Stuffing the error into
 // `skipped` gave it the same amber shape as a legitimate "this supplier
 // has no email on file" line — and, for a dropped connection, the literal
 // text "HTTP 0" — while replacing the Send button with a results list, so
 // there was no way to retry. Nothing had been sent to anybody.
 const [sendError, setSendError]       = useState<unknown>(null)
 const [sendResult, setSendResult]     = useState<SendPOResult | null>(null)
 // "Ask the supplier to confirm": on by default, remembered across sessions.
 const [requestConfirmation, setRequestConfirmation] = useRequestConfirmationPref()

 const user    = getUser()
 // Every write this screen can start — logging a PO, converting an optimizer
 // line, recording a reception — is refused for a viewer by the backend. The
 // screen used to offer all of them anyway and only admit it at the end, after
 // the buyer had already decided twelve products and downloaded a CSV. Same
 // shape as /inventario, /escenarios and /historial.
 const canEdit = user?.role === 'admin' || user?.role === 'analyst'
 const { addToast } = useToast()
 const router = useRouter()

 // How old the two inputs behind the semáforo are. When either has gone blind
 // the page stops presenting the traffic light as trustworthy (see
 // StaleDataBanner) instead of showing a confident green over data nobody has
 // refreshed in a month.
 const { freshness } = useDataFreshness()
 const semaphoreStale = freshness?.semaphore === 'degraded'

 // How much of the catalogue nobody has counted. The semáforo already reports
 // these as SIN_DATOS per product; up here they were invisible, so a risk count
 // of 0 over an entirely uncounted catalogue read as "nothing to worry about".
 const uncounted = briefing?.kpis?.sin_datos ?? 0
 const nothingCounted = uncounted > 0 && uncounted >= (briefing?.kpis?.total_skus ?? 0)
 // No unit cost anywhere: the warehouse value is unknown, not zero. Guarded on
 // the field being present so a briefing from before it existed keeps its old
 // number rather than silently blanking.
 const noCostOnFile = briefing?.kpis?.valued_skus === 0 && (briefing?.kpis?.total_skus ?? 0) > 0

 // Phone or desktop. Declared with the other hooks so the hook order is stable
 // whichever tree ends up rendering (see the fork below the early returns).
 const isNarrow = useIsNarrow()

 // Load briefing when session changes
 const load = useCallback(async (sid: string) => {
  if (!sid) return
  setLoading(true)
  setError(null)
  try {
   // `silent: true` — the failure is rendered as a full ErrorState below, so
   // the interceptor's toast would say the same thing twice.
   const data = await getMorningBriefing(sid, 0.95, { silent: true })
   setBriefing(data)
   setLoadedAt(new Date())
  } catch (e: unknown) {
   setError(e)
  } finally {
   setLoading(false)
  }
 }, [])

 useEffect(() => {
  if (sessionId) load(sessionId)
 }, [sessionId, load])

 // Purchasing/transfers optimization plan — loads alongside the briefing
 useEffect(() => {
  if (!sessionId) return
  setOptimizationLoading(true)
  // No horizon: the endpoint uses the tenant's own planning window. Forcing 30
  // pushed every solve past the solver's ceiling and into the transfer-blind
  // fallback (see optimizeInventory).
  optimizeInventory(sessionId)
   .then(setOptimization)
   .catch(() => setOptimization(null))
   .finally(() => setOptimizationLoading(false))
 }, [sessionId])

 // Generates a fallback narrative from briefing data — no API required
 function buildFallbackNarrative(b: MorningBriefing): MorningNarrative {
  const k = b.kpis
  // 'ok' renders as "Situación controlada". An uncounted catalogue produces the
  // same two zeros as a healthy one, so that badge was the calm face of having
  // measured nothing — it degrades to a warning instead.
  const allUncounted = (k.sin_datos ?? 0) > 0 && (k.sin_datos ?? 0) >= k.total_skus
  const urgency = k.order_now > 0 ? 'critical'
    : (k.order_soon > 0 || allUncounted) ? 'warning' : 'ok'
  const parts: string[] = []
  if (k.order_now > 0) {
   const names = (b.risks ?? []).slice(0, 3).map(r => r.display_name || r.sku).join(', ')
   parts.push(`${k.order_now} ${t('hoy.narrative_products_immediate_risk')}: ${names}.`)
  }
  if (k.order_soon > 0)
   parts.push(`${k.order_soon} ${t('hoy.narrative_products_need_order_week')}`)
  if (k.overstock > 0 && k.capital_in_overstock > 0)
   // Full amount, never scaled to millions: an SMB's tied-up capital is usually
   // five figures, and `/1_000_000` rendered ₡25,430 as "₡0.0M" — the product
   // reporting zero for money the user actually has stuck on a shelf.
   parts.push(`${fmtMoney(k.capital_in_overstock)} ${t('hoy.narrative_capital_tied_overstock')}`)
  // "Everything is under control" is the strongest claim on this screen, and
  // it was made from two counters that are both 0 when nobody has counted
  // anything. A catalogue with no stock on file is not under control; it is
  // unmeasured, and saying which one it is costs one sentence.
  const uncountedHere = k.sin_datos ?? 0
  if (k.order_now === 0 && k.order_soon === 0) {
   if (uncountedHere >= k.total_skus && k.total_skus > 0)
    parts.push(t('hoy.narrative_nothing_counted').replace('{count}', String(uncountedHere)))
   else if (uncountedHere > 0)
    parts.push(`${t('hoy.narrative_inventory_under_control')} ${
     t('hoy.narrative_some_uncounted').replace('{count}', String(uncountedHere))}`)
   else
    parts.push(t('hoy.narrative_inventory_under_control'))
  }
  return { narrative: parts.join(' '), key_points: [], urgency, fallback: true }
 }

 useEffect(() => {
  if (!briefing || !sessionId) return
  setLoadingNarrative(true)
  const timeout = setTimeout(() => {
   setNarrative(buildFallbackNarrative(briefing))
   setLoadingNarrative(false)
  }, 11000)
  // `lang` reaches the model as the language to answer in. Measured before it
  // did: the whole narrative came back in Spanish under an English heading.
  // The timeout above used to be 8000ms — measured against the real
  // DeepSeek call, it answers in ~8.8s on the normal path, so the fallback
  // was firing before the real response most of the time: the screen showed
  // the rule-based sentence for a beat, then swapped it for the real one the
  // instant the request landed. 11000ms gives headroom over that measured
  // latency (docs/stability.md, section 7).
  getMorningNarrative(sessionId, 'distributor', lang)
   // `fallback: true` means the AI was unreachable and the backend answered with
   // its rule-based sentence. That one is written in English for API clients,
   // so this screen builds its own from the briefing instead — same facts,
   // through the catalogue, so it follows the language toggle.
   .then(data => {
    clearTimeout(timeout)
    setNarrative(data.fallback ? buildFallbackNarrative(briefing) : data)
   })
   .catch(() => { clearTimeout(timeout); setNarrative(buildFallbackNarrative(briefing)) })
   .finally(() => setLoadingNarrative(false))
  return () => clearTimeout(timeout)
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [briefing?.session_id, lang])

 // Build cart when briefing arrives
 useEffect(() => {
  if (briefing) {
   setCart(buildActionItems(briefing, t))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [briefing?.session_id])

 // Orders awaiting reception — confirming yesterday's arrivals is part of the morning routine
 useEffect(() => {
  getPOHistory(20)
   .then(list => setPendingPOs(list.filter(p =>
    ['pending', 'partial'].includes(p.reception_status ?? 'pending'),
   )))
   .catch(() => {})
 }, [])

 // Supplier health signals — the "we'd skip these on send" set and the
 // "this supplier drifted late" set.
 useEffect(() => {
  getSupplierContactHealth().then(setContactHealth).catch(() => {})
  getSupplierLeadTimeAlerts().then(setLeadTimeAlerts).catch(() => {})
  listSuppliers().then(setSuppliers).catch(() => {})
 }, [])

 const loadOverdue = useCallback(() => {
  getOverduePOs().then(setOverduePOs).catch(() => {})
 }, [])

 // POs whose expected arrival (learned supplier lead time) already passed
 useEffect(() => {
  loadOverdue()
 }, [loadOverdue])

 // ── Cart helpers ─────────────────────────────────────────────────────────
 function approveItem(sku: string) {
  setCart(prev => prev.map(i =>
   i.sku === sku && i.status !== 'ordered'
    ? { ...i, status: (i.status === 'approved' ? 'pending' : 'approved') as ActionStatus }
    : i,
  ))
 }

 // Take a line back OUT of the order without rejecting it.
 //
 // The mobile card has one button that toggles, and `approveItem` cannot serve
 // as its "remove": it only toggles 'approved' ⇄ 'pending', so a line the buyer
 // had put in the cart by editing its quantity ('modified') would stay in the
 // cart on the second tap. Rejecting it instead would be wrong too — the buyer
 // is undoing their own tap, not telling us the recommendation was bad, and
 // rejections are logged as adoption feedback.
 function unapproveItem(sku: string) {
  setCart(prev => prev.map(i => i.sku === sku && i.status !== 'ordered' ? { ...i, status: 'pending' as ActionStatus } : i))
 }

 function rejectItem(sku: string) {
  setCart(prev => prev.map(i => i.sku === sku && i.status !== 'ordered' ? { ...i, status: 'rejected' as ActionStatus } : i))
 }

 function changeQty(sku: string, qty: number) {
  setCart(prev => prev.map(i => i.sku === sku && i.status !== 'ordered' ? { ...i, qty, status: 'modified' as ActionStatus } : i))
 }

 // Re-pointing a line at a different supplier is a buyer decision, so the line
 // counts as modified for adoption tracking just like a quantity change.
 function changeSupplier(sku: string, supplierId: string) {
  const picked = suppliers.find(s => s.id === supplierId) || null
  setCart(prev => prev.map(i => i.sku === sku && i.status !== 'ordered'
   ? {
     ...i,
     supplier_id: picked?.id ?? null,
     supplier:    picked?.name ?? null,
     status: (i.status === 'pending' ? 'modified' : i.status) as ActionStatus,
    }
   : i))
 }

 const approved   = cart.filter(i => (i.status === 'approved' || i.status === 'modified') && i.qty > 0)
 const budgetCheckLines = approved.map(i => ({
  sku: i.sku, qty: i.qty, unit_cost: i.unit_cost ?? null,
  supplier: i.supplier, supplier_id: i.supplier_id,
 }))
 const totalValue = approved.reduce((s, i) => s + i.qty * (i.unit_cost ?? 0), 0)
 // Lines with no cost on file are NOT zero-cost lines; the total above leaves
 // them out and must say so, or ten priced units and fifty unpriced ones read
 // as a complete order of the first ten (math audit 2026-10-01).
 const uncostedLines = approved.filter(i => i.unit_cost == null).length

 // Feature 2.10 — margin visible in the cart. The per-unit margin is
 // computed by the backend (unit_margin = sale_price − unit_cost, null when
 // either one is missing); here we only multiply by the qty the
 // user approved and sum. Lines with no price or no cost stay OUT of
 // both totals and are reported separately, so the figure is neither
 // inflated nor deflated.
 const priced   = approved.filter(i => i.unit_margin != null && i.sale_price != null)
 const unpriced = approved.filter(i => i.unit_margin == null || i.sale_price == null)
 const salesProtected  = priced.reduce((s, i) => s + i.qty * (i.sale_price ?? 0), 0)
 const marginProtected = priced.reduce((s, i) => s + i.qty * (i.unit_margin ?? 0), 0)

 // Feature 2.5 — of the suppliers the backend flagged as un-sendable, show
 // only those actually in play right now: named on an approved cart line, or
 // already carrying an open order. A supplier with an incomplete ficha that
 // this buyer never orders from is housekeeping, not a warning worth
 // interrupting the morning routine.
 const cartSupplierNames = new Set(
  approved.map(i => i.supplier).filter((p): p is string => !!p)
         .map(p => p.toLowerCase()),
 )
 const relevantContactHealth = contactHealth.filter(
  r => cartSupplierNames.has(r.supplier.toLowerCase()) || r.has_open_pos,
 )

 // ── Price breaks (3.5) ───────────────────────────────────────────────────
 // Evaluated server-side against the quantities the buyer currently has, so
 // editing a line re-judges its scale. The "conviene o no" verdict, including
 // the holding-cost and overstock guardrails, belongs to the backend.
 // The SUPPLIER is part of the key, and travels with every line.
 //
 // This used to send `{sku, quantity}` only, so the ladder came from the status
 // row rather than from the cart — and because the key was `sku:qty`, switching
 // supplier did not even re-evaluate. The panel went on quoting the previous
 // supplier's scale: "Andina: order 500 and save ~1,400" about a price only
 // Norte ever quoted, which is the exact defect `evaluate_cart`'s docstring
 // says it fixed, reintroduced through the supplier-switch path
 // (stability 11.14).
 const approvedKey = approved.map(i => `${i.sku}:${i.qty}:${i.supplier_id ?? ''}`).join('|')
 useEffect(() => {
  if (!sessionId || approved.length === 0) { setPriceBreaks(null); return }
  let cancelled = false
  evaluatePriceBreaks(sessionId, approved.map(i => ({
   sku: i.sku, quantity: i.qty, supplier_id: i.supplier_id ?? undefined,
  })))
   .then(r => { if (!cancelled) setPriceBreaks(r) })
   .catch(() => { if (!cancelled) setPriceBreaks(null) })
  return () => { cancelled = true }
  // approvedKey collapses the cart to a primitive so this re-runs on a real
  // quantity or supplier change, not on every re-render that rebuilds the array.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [sessionId, approvedKey])

 function applyStepUp(sku: string, quantity: number, unitPrice: number) {
  // The quantity AND the price. This used to call `changeQty` alone, so the
  // line kept its old `unit_cost`: the panel said "order 500 instead of 100
  // and save ~1,400", the cart total went UP by the extra units at the old
  // price, and that old price was what the decisions payload wrote into
  // inventory_po_items.unit_cost — the single authority for the supplier PDF,
  // the cash-calendar payable, /impacto's managed value and the scorecard's
  // purchased_value. `effective_unit_price` existed and had no caller outside
  // its own evaluation, so the discount reached nothing StockAI stores or prints.
  setCart(prev => prev.map(i => i.sku === sku
   ? {
     ...i,
     qty: quantity,
     unit_cost: unitPrice,
     // The margin per unit moves with the cost it is derived from
     // (unit_margin = sale_price - unit_cost, computed by the backend), or
     // the cart would report the OLD margin on the NEW price.
     unit_margin: i.sale_price != null ? i.sale_price - unitPrice : i.unit_margin,
     status: 'modified' as ActionStatus,
    }
   : i))
 }

 // ── Cash calendar (3.6) ──────────────────────────────────────────────────
 useEffect(() => {
  getCashCalendar(30).then(setCashCalendar).catch(() => setCashCalendar(null))
 }, [])

 // Re-check the fit whenever the cart or the typed budget changes. Skipped
 // entirely until a budget exists — without one the backend returns fits:null
 // and there is nothing to show.
 useEffect(() => {
  if (cashBudget == null || approved.length === 0) { setCashFit(null); return }
  let cancelled = false
  setCashFitBusy(true)
  checkCashFit({
   budget: cashBudget,
   items: approved.map(i => ({
    sku: i.sku, supplier_name: i.supplier, quantity: i.qty, unit_cost: i.unit_cost,
   })),
  }, 30)
   .then(r => { if (!cancelled) setCashFit(r) })
   .catch(() => { if (!cancelled) setCashFit(null) })
   .finally(() => { if (!cancelled) setCashFitBusy(false) })
  return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [cashBudget, approvedKey])

 // One cart submission = one purchase order. `submittingRef` is the
 // synchronous guard (state updates land a render later, so a fast double
 // tap would read a stale `submitting`); `submitting` drives the button.
 // The idempotency key is the server-side half: it survives a FAILED attempt
 // as long as the cart is unchanged, so a retry after a dropped connection
 // — where the first request may have been written — returns that order
 // instead of creating a second one (OC-000003/OC-000004, mobile QA).
 const submittingRef = useRef(false)
 const [submitting, setSubmitting] = useState(false)
 const pendingSubmission = useRef<{ key: string; signature: string } | null>(null)

 function newIdempotencyKey(): string {
  try {
   if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch { /* fall through */ }
  return `po-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
 }

 async function downloadOC() {
  if (submittingRef.current || approved.length === 0) return
  submittingRef.current = true
  setSubmitting(true)
  try {
   await submitOrder()
  } finally {
   submittingRef.current = false
   setSubmitting(false)
  }
 }

 async function submitOrder() {
  // Written through lib/csvWriter: quotes escaped, formula prefixes
  // neutralised and a UTF-8 BOM — the same three things the backend writer of
  // this exact filename already did. An unpriced line exports an EMPTY value
  // cell instead of a confident 0 (see csvNumber).
  const header = ['SKU', t('hoy.csv_col_product'), t('hoy.csv_col_quantity'),
                  t('hoy.csv_col_supplier'), t('hoy.csv_col_estimated_value')]
  const rows = approved.map(item => [
   csvCell(item.sku),
   csvCell(item.name),
   csvNumber(item.qty),
   csvCell(item.supplier || ''),
   csvNumber(item.unit_cost == null ? null : item.qty * item.unit_cost),
  ])
  downloadCsv('purchase_order.csv', buildCsv(header, rows))

  if (!sessionId) return

  // Log the buyer's actual decisions (approved / modified / rejected) so we can
  // track adoption — "you followed N of M recommendations". Untouched 'pending'
  // items are excluded: the buyer never acted on them.
  // 'ordered' lines are already on a PO, and a decision that already went
  // out on one (a rejection logged with the previous order) must not be
  // counted twice in adoption.
  const decisions = cart
   .filter(i => i.status !== 'pending' && i.status !== 'ordered' && !i.decision_logged)
   .filter(i => i.status === 'rejected' || i.qty > 0)
   .map(i => ({
    sku:                  i.sku,
    display_name:         i.name,
    supplier:            i.supplier,
    supplier_id:         i.supplier_id,
    signal:               i.signal,
    recommended_qty: i.recommended,
    final_qty:       i.status === 'rejected' ? 0 : i.qty,
    status:               i.status as 'approved' | 'modified' | 'rejected',
    unit_cost:       i.unit_cost,
   }))

  // Feature: generate→send in one flow. Capture the logged PO so we can offer
  // "send to suppliers now" right here, instead of sending the buyer to /orders.
  const destination = multi ? destWarehouse || undefined : undefined
  const signature = JSON.stringify([sessionId, destination, decisions])
  if (!pendingSubmission.current || pendingSubmission.current.signature !== signature) {
   pendingSubmission.current = { key: newIdempotencyKey(), signature }
  }
  const orderedSkus = new Set(approved.map(i => i.sku))
  const loggedSkus  = new Set(decisions.map(d => d.sku))

  try {
   const entry = await logPOGeneration(
    sessionId, decisions, destination,
    { silent: true, headers: { 'Idempotency-Key': pendingSubmission.current.key } },
    budgetReason,
   )
   pendingSubmission.current = null
   setBudgetReason('')
   setBudgetReload(n => n + 1)
   if (entry.budget_warnings && entry.budget_warnings.length > 0) {
    addToast(t('budget.toast_over_title'), t('budget.toast_over_body'), 'info', { duration: 10000 })
   }
   const ref = entry.po_number ? `OC-${String(entry.po_number).padStart(6, '0')}` : entry.id
   // The lines just ordered leave the cart for good: the bar disappears with
   // them, and each card says which order it is on instead of offering the
   // same "add to order" again.
   setCart(prev => prev.map(i => {
    if (orderedSkus.has(i.sku)) {
     return { ...i, status: 'ordered' as ActionStatus, ordered_ref: ref, decision_logged: true }
    }
    if (loggedSkus.has(i.sku)) return { ...i, decision_logged: true }
    return i
   }))
   setGeneratedPO(entry)
   setGeneratedLines(approved)
   setSendState('idle')
   setSendResult(null)
   addToast(
    t('hoy.toast_po_saved_title', { ref }),
    entry.replayed
     ? t('hoy.toast_po_replayed_body')
     : orderedSkus.size === 1 ? t('hoy.toast_po_saved_body_one') : t('hoy.toast_po_saved_body', { count: orderedSkus.size }),
    'success',
    { duration: 8000, action: { label: t('hoy.toast_view_orders'), kind: 'link', onClick: () => router.push('/pedidos') } },
   )
   // Refresh the optimizer plan: its opening position now includes this order.
   optimizeInventory(sessionId).then(setOptimization).catch(() => {})
   // And the "N orders on the way" nudge, which now has one more.
   getPOHistory(20)
    .then(list => setPendingPOs(list.filter(p =>
     ['pending', 'partial'].includes(p.reception_status ?? 'pending'))))
    .catch(() => {})
  } catch (e) {
   // This call is not just the inline send panel — it is what makes the order
   // EXIST: /pedidos lists it, reception is tracked against it, and supplier
   // lead-time learning reads it. The old comment here reasoned the panel was
   // "a bonus" and stayed quiet, so a buyer who had just downloaded a CSV was
   // left believing the order was in the system. It is not, and the fix is to
   // generate it again, which they can only do if we say so.
   //
   // Unless the refusal was the ROLE, and then "generate it again" is an empty
   // promise: every retry 403s identically. Reachable even with the cart bar
   // gated — a cached role goes stale the moment an admin demotes the user in
   // another session.
   if (e instanceof ApiError && e.kind === 'permission') {
    addToast(t('states.err_permission_title'), t('states.err_permission_body'), 'error')
   } else if (e instanceof ApiError && (e.code === 'purchase_budget_hard_cap' || e.code === 'purchase_budget_override_requires_admin')) {
    // Refused by a hard-capped budget: the cart stays as it is, and the
    // reason box above it (admins) is where the way forward is.
    addToast(t('budget.toast_blocked_title'), errorDetail(e), 'error', { duration: 12000 })
   } else if (e instanceof ApiError && e.code === 'po_idempotency_key_reused') {
    // The key belongs to a different cart: a fresh one next time.
    pendingSubmission.current = null
    addToast(t('inventory.toast_po_not_logged_title'), errorDetail(e), 'error')
   } else {
    addToast(t('inventory.toast_po_not_logged_title'),
        t('inventory.toast_po_not_logged_body'), 'error')
   }
  }
 }

 async function sendGeneratedPONow() {
  if (!generatedPO) return
  setSendState('sending')
  setSendError(null)
  try {
   // Read at the moment of sending: the phone sheet has its own copy of the
   // checkbox, and the stored choice is what both of them write.
   const res = await sendPOToSuppliers(generatedPO.id, { requestConfirmation: readRequestConfirmationPref() })
   setSendResult(res)
  } catch (e: unknown) {
   setSendError(e)
  } finally {
   setSendState('done')
  }
 }

 function dismissGeneratedPO() {
  setGeneratedPO(null)
  setGeneratedLines([])
  setSendState('idle')
  setSendResult(null)
  setSendError(null)
 }

 // Converts a single optimizer-suggested order line straight into a logged PO,
 // without going through the manual approve/reject work-queue cart.
 // Same double-tap hole as the cart: one in-flight conversion per line, and
 // a key so a retry of the same line returns the order already written.
 const convertingRef = useRef<Map<string, string>>(new Map())
 async function convertOrderToPO(order: OptimizationOrder) {
  if (!sessionId) return
  const lineKey = `${order.sku}|${order.warehouse}|${order.qty}`
  if (convertingRef.current.has(lineKey)) return
  convertingRef.current.set(lineKey, newIdempotencyKey())
  try {
   await convertOrderToPOOnce(order, convertingRef.current.get(lineKey)!)
  } finally {
   convertingRef.current.delete(lineKey)
  }
 }

 async function convertOrderToPOOnce(order: OptimizationOrder, key: string) {
  if (!sessionId) return
  const decision: POLineDecision = {
   sku:                  order.sku,
   recommended_qty: order.qty,
   final_qty:       order.qty,
   status:               'approved',
   unit_cost:       order.unit_cost,
   supplier:            order.supplier,
   warehouse:               order.warehouse,
  }
  await logPOGeneration(sessionId, [decision], multi ? destWarehouse || undefined : undefined,
   { headers: { 'Idempotency-Key': key } })
  addToast(t('hoy.optimizer_po_created'), `${order.sku} — ${order.warehouse}`, 'success')
  setOptimization(prev => prev
   ? { ...prev, orders: prev.orders.filter(o => !(o.sku === order.sku && o.warehouse === order.warehouse)) }
   : prev)
 }

 // Shared by the desktop card and the phone one.
 function refreshNarrative() {
  if (!sessionId) return
  setLoadingNarrative(true)
  // The initial load has an 11s ceiling; this had none. `.finally`
  // cannot fire on a promise that never settles, so a DeepSeek that
  // hangs left the spinner turning with no way out — and this is a
  // button someone presses precisely when the answer looks stale.
  //
  // Same ceiling, different landing: the initial load has nothing on
  // screen and falls back to the rule-based sentence, while a
  // refresh already shows a good narrative. Replacing that with a
  // weaker one is a downgrade nobody asked for, so this stops the
  // spinner and keeps what is there.
  let timedOut = false
  const timeout = setTimeout(() => {
   timedOut = true
   setLoadingNarrative(false)
  }, 11000)
  getMorningNarrative(sessionId, 'distributor', lang)
   // Same rule as the initial load: the backend's rule-based sentence
   // is written for an API client, not for this screen, so "Refresh"
   // must not swap the local one back out for it.
   .then(data => {
    // A late answer after the ceiling must not repaint the card
    // under the reader — they have moved on by then.
    if (timedOut) return
    setNarrative(data.fallback && briefing ? buildFallbackNarrative(briefing) : data)
   })
   .catch(() => {})
   .finally(() => { clearTimeout(timeout); if (!timedOut) setLoadingNarrative(false) })
 }

 const kpis = briefing?.kpis

 // What today's advice rests on that nobody gave us. Computed here so the
 // banner and its tour anchor agree on when there is anything to admit.
 const assumptions = summarizeAssumptions(briefing)


 // What used to be banners is shown where it concerns: on the card of the
 // supplier it is about, and in the bell (see hooks/useAttention).
 const noContactNames = new Set(contactHealth.map(r => r.supplier.toLowerCase()))
 const lateBySupplier = new Map(leadTimeAlerts.map(a => [a.supplier.toLowerCase(), a] as const))
 const staleFold = useTabFold('compras.stale_dismissed')

 // What the closed "More analysis" fold holds, so its badge and its sections
 // use one set of guards.
 const moreSections = briefing
  ? analysisSections(briefing, optimization, recommendationsNotOnCards(briefing).length)
  : []

 // Total pending actions for greeting
 const totalPending = cart.filter(i => i.status === 'pending' && i.qty > 0).length

 // ── Session list failed to load ───────────────────────────────────────────
 if (!sessionsLoading && sessionsError) {
  return (
   <div style={{
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', flex: 1, gap: 16, padding: 40,
    color: C.muted, textAlign: 'center',
   }}>
    <AlertTriangle size={36} color={C.red} style={{ opacity: 0.7 }} />
    {/* Through useErrorDetail: this used to print the hook's pre-rendered
        string, which for a dropped connection was the literal "HTTP 0". */}
    <p style={{ fontSize: 15, color: C.text, margin: 0, maxWidth: 420 }}>{errorDetail(sessionsError)}</p>
    <button
     onClick={refreshSessions}
     style={{
      display: 'flex', alignItems: 'center', gap: 6,
      padding: '10px 20px', background: C.indigo, color: '#fff',
      border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 14, fontWeight: 600,
     }}
    >
     <RefreshCw size={13} /> {t('hoy.btn_retry')}
    </button>
   </div>
  )
 }

 // ── No trained session yet: this is the new tenant's first screen ─────────
 if (!loading && !sessionsLoading && !sessionId && completedSessions.length === 0) {
  return <HoyEmptyState variant="no_session" />
 }

 // ── Phone: a card list, not this table ────────────────────────────────────
 // The buyer checks what to order standing in the warehouse. Everything above
 // this line — the data loading, the cart state, the decisions logged on
 // download — is shared; only the presentation forks, so the two views cannot
 // disagree about what was approved or what the semáforo says.
 //
 // `isNarrow` is false on the first render (SSR has no viewport), so the
 // desktop tree is what hydrates and the swap happens one paint later.
 if (isNarrow) {
  const narrativeNode = (narrative || loadingNarrative) ? (
   <div style={{ marginBottom: 14 }}>
    <NarrativeCard
 plain
     title={t('hoy.narrative_card_title')}
     narrative={narrative?.narrative ?? null}
     keyPoints={(narrative?.key_points ?? []).map(p => keyPointText(p, t))}
     urgency={narrative?.urgency ?? 'ok'}
     loading={loadingNarrative}
     fallback={narrative?.fallback ?? false}
     analytistLink="/asistente"
     onRefresh={refreshNarrative}
    />
   </div>
  ) : null
  return (
   <>
    <HoyMobile
     loading={loading}
     error={error}
     onRetry={() => load(sessionId)}
     briefing={briefing}
     firstName={user?.full_name ? user.full_name.split(' ')[0] : null}
     freshness={freshness ?? null}
     freshnessChip={<DataFreshness currentSession={currentSession} loading={sessionsLoading} />}
     semaphoreStale={semaphoreStale}
     cart={cart}
     approved={approved}
     onApprove={approveItem}
     onRemove={unapproveItem}
     onReject={rejectItem}
     onChangeQty={changeQty}
     suppliers={suppliers}
     onChangeSupplier={changeSupplier}
     onClearCart={() => setCart(prev => prev.map(i =>
      i.status === 'approved' || i.status === 'modified'
       ? { ...i, status: 'pending' as ActionStatus }
       : i,
     ))}
     onGenerate={downloadOC}
     budgetNotes={budgetNotes}
     generating={submitting}
     canDecide={canEdit}
     multiWarehouse={multi}
     warehouses={warehouses}
     destWarehouse={destWarehouse}
     onDestWarehouse={setDestWarehouse}
     generatedPO={generatedPO}
     generatedLines={generatedLines}
     sendState={sendState}
     sendResult={sendResult}
     sendError={sendError}
     onSendNow={sendGeneratedPONow}
     sendReason={r => sendReason(r, t)}
     onDismissGenerated={dismissGeneratedPO}
     pendingReceptions={pendingPOs.length}
     overduePOs={overduePOs}
     onReceive={canEdit ? setReceivingPO : null}
     leadTimeAlerts={leadTimeAlerts}
     contactHealth={relevantContactHealth}
     noInventory={<HoyEmptyState variant="no_inventory" />}
     loadedAtText={loadedAt ? timeSince(loadedAt, t) : null}
     intro={<>
      {narrativeNode}
      <BudgetPanel sessionId={sessionId} onNotes={setBudgetNotes} reloadToken={budgetReload} />
      {(briefing?.transfer_suggestions?.length ?? 0) > 0 && (
       <div style={{ marginBottom: 14, minWidth: 0 }}>
        <TransferSuggestions suggestions={briefing?.transfer_suggestions ?? []} canApprove={canEdit} />
       </div>
      )}
     </>}
     cartPanels={<>
      <BudgetCartCheck lines={budgetCheckLines} destination={multi ? destWarehouse || undefined : undefined}
       reason={budgetReason} onReason={setBudgetReason} />
      {priceBreaks && (
       <PriceBreakPanel
        opportunities={priceBreaks.opportunities}
        totalNetSaving={priceBreaks.total_net_saving}
        currency={fmtMoney}
        onApplyStepUp={applyStepUp}
       />
      )}
      <CashFitPanel
       calendar={cashCalendar}
       fit={cashFit}
       currency={fmtMoney}
       onBudgetChange={setCashBudget}
       busy={cashFitBusy}
      />
     </>}
     extras={briefing ? (
      <HoyMobileExtras
       briefing={briefing}
       optimization={optimization}
       optimizationLoading={optimizationLoading}
       canEdit={canEdit}
       onConvert={convertOrderToPO}
      />
     ) : null}
    />
    {/* The same reception form as /pedidos (a bottom sheet on a phone),
        opened from the overdue-arrival rows. */}
    {receivingPO && (
     <ReceptionModal
      poId={receivingPO}
      onClose={() => setReceivingPO(null)}
      onSaved={() => {
       setReceivingPO(null)
       loadOverdue()
       getPOHistory(20)
        .then(list => setPendingPOs(list.filter(p =>
         ['pending', 'partial'].includes(p.reception_status ?? 'pending'),
        )))
        .catch(() => {})
      }}
     />
    )}
   </>
  )
 }

 return (
  <div style={{ background: C.bg, minHeight: '100vh', padding: '40px 48px 64px', position: 'relative' }}>

   <div style={{ maxWidth: 1000, margin: '0 auto' }}>

   {/* ── Header: one title, one quiet line under it ── */}
   <div style={{
    display: 'flex', alignItems: 'flex-start',
    justifyContent: 'space-between', gap: 24, marginBottom: 32,
   }}>
    <div style={{ minWidth: 0 }}>
     <h2 style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em', color: C.text, margin: '0 0 8px' }}>
      {t('hoy.greeting_good_morning')}{user?.full_name ? `, ${user.full_name.split(' ')[0]}` : ''}.
      {cart.length > 0 && totalPending > 0 && (
       <span style={{ fontSize: 14, fontWeight: 400, color: C.dim, marginLeft: 12 }}>
        {t('hoy.greeting_pending_actions_prefix')} {totalPending} {t('hoy.greeting_pending_actions_suffix')}
       </span>
      )}
     </h2>
     {briefing ? (
      <p data-tour={overduePOs.length > 0 ? 'hoy.receptions' : undefined} style={{ fontSize: 13, color: C.dim, margin: 0, lineHeight: 1.6 }}>
       {t('hoy.date_today_prefix')} {formatDateES(briefing.date, lang)}.{' '}
       {t('hoy.date_active_session')}: <span style={{ color: C.muted }}>{briefing.session_name}</span>
       {/* Deliveries waiting for a yes: plain text on the same line, not a
           box. The full list lives in the bell and on Orders. */}
       {overduePOs.length > 0 ? (
        <>
         {' · '}
         {t(overduePOs.length === 1 ? 'hoy.deliveries_to_confirm_one' : 'hoy.deliveries_to_confirm_other', { n: overduePOs.length })}
         {canEdit && (
          <>
           {' '}
           <button
            data-tour="hoy.receive"
            onClick={() => setReceivingPO(overduePOs[0].po_log_id)}
            style={{ all: 'unset', cursor: 'pointer', color: 'var(--accent)', fontWeight: 600 }}
           >
            {t('hoy.deliveries_confirm_cta')}
           </button>
          </>
         )}
        </>
       ) : pendingPOs.length > 0 ? (
        <>
         {' · '}
         <Link href="/pedidos" style={{ color: C.dim, textDecoration: 'underline' }}>
          {pendingPOs.length} {pendingPOs.length === 1 ? t('hoy.receptions_pending_singular') : t('hoy.receptions_pending_plural')}
         </Link>
        </>
       ) : null}
      </p>
     ) : error == null ? (
      <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
       {t('hoy.date_loading')}
      </p>
     ) : null}
    </div>

    {/* The tour anchor sits on this wrapper, not inside DataFreshness: that
        component is shared with /inventory and does not forward unknown props
        to the DOM. */}
    <div data-tour="hoy.freshness">
     <DataFreshness currentSession={currentSession} loading={sessionsLoading} />
    </div>
   </div>

   {/* ── Loading state: shaped like the briefing it replaces ── */}
   {loading && (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '8px 0 40px' }}>
     <LoadingState label={t('hoy.loading_label')}>
      <SkeletonCards count={4} height={88} />
     </LoadingState>
     <SkeletonTable rows={5} columns={4} />
    </div>
   )}

   {/* ── Error state ── */}
   {!loading && error != null && (
    <div style={{ padding: '24px 0 40px' }}>
     <ErrorState error={error} onRetry={() => load(sessionId)} />
    </div>
   )}

   {/* ── Main content ── */}
   {!loading && briefing && (
    <>
     {/* Session trained, but no stock loaded — nothing to put a semáforo on */}
     {!briefing.has_data ? (
      <HoyEmptyState variant="no_inventory" />
     ) : (
      <>
       {/* Above every other banner on purpose: it is not one more alert, it
           is the caveat that applies to all of them — the semáforo below was
           computed on stock (or sales) nobody has refreshed. */}
       {freshness && <StaleDataBanner freshness={freshness} />}

       {/* The one condition that makes every recommendation below unreliable
           stays in view, as a single slim neutral line that can be dismissed
           for this tab. Everything else that used to be a banner here lives in
           the bell and on the line it concerns. */}
       {freshness && staleFold.open !== true && <StaleLine freshness={freshness} onDismiss={() => staleFold.set(true)} />}

       {/* KPI row: figures, not boxes. */}
       <div data-tour="hoy.kpis" style={{
        display: 'flex', gap: 48, flexWrap: 'wrap', padding: '20px 0',
        borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}`,
        marginBottom: semaphoreStale ? 12 : 32,
       }}>
        <KpiCard label={t('hoy.kpi_total_skus')}        value={String(kpis!.total_skus)} />
        <KpiCard label={t('hoy.kpi_risk_today')}        value={nothingCounted ? '—' : String(kpis!.order_now)}
         dot={kpis!.order_now > 0 ? 'var(--signal-order-now-fg)' : undefined} />
        <KpiCard label={t('hoy.kpi_this_week')}         value={nothingCounted ? '—' : String(kpis!.order_soon)}
         dot={kpis!.order_soon > 0 ? 'var(--signal-order-soon-fg)' : undefined} />
        {/* "₡0 en bodega" reads as "your stock is worth nothing". With no unit
            cost on file the honest answer is that we were never told. */}
        <KpiCard label={t('hoy.kpi_inventory_value')}
         value={(nothingCounted || noCostOnFile) ? '—' : fmtM(kpis!.total_inventory_value)} />
       </div>
       {/* Every counter above divides by the same stale stock — say so once,
           right under them, instead of letting five confident numbers stand. */}
       {semaphoreStale && (
        <div style={{ fontSize: 12, color: C.dim, marginBottom: 24, lineHeight: 1.6 }}>
         {t('freshness.kpi_caveat')}
        </div>
       )}
       {/* A risk count of 0 over products nobody has counted is not "no risk",
           it is "no idea". The uncounted share of the catalogue decides how
           much of this row can be believed. */}
       {uncounted > 0 && (
        <div style={{ fontSize: 12, color: C.dim, marginBottom: 24, lineHeight: 1.6 }}>
         {(nothingCounted ? t('hoy.kpi_nothing_counted') : t('hoy.kpi_partially_counted'))
           .replace('{count}', String(uncounted))
           .replace('{total}', String(kpis!.total_skus))}{' '}
         <Link href="/inventario" style={{ fontWeight: 600, color: 'var(--accent)', textDecoration: 'none' }}>
          {t('hoy.needs_stock_cta')}
         </Link>
        </div>
       )}

       {/* Work queue */}
       <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

        {/* AI Narrative */}
        {(narrative || loadingNarrative) && (
         <div data-tour="hoy.narrative" style={{ marginBottom: 32 }}>
          <NarrativeCard
 plain
           title={t('hoy.narrative_card_title')}
           narrative={narrative?.narrative ?? null}
           keyPoints={(narrative?.key_points ?? []).map(p => keyPointText(p, t))}
           urgency={narrative?.urgency ?? 'ok'}
           loading={loadingNarrative}
           fallback={narrative?.fallback ?? false}
           analytistLink="/asistente"
           onRefresh={refreshNarrative}
          />
         </div>
        )}

        {/* Transfer suggestions (feature 5.4) — stock exists, wrong warehouse.
            Rendered before the urgent purchases: moving boxes is free. The
            suggestions arrive with the morning briefing (no extra request);
            the component renders null when there is nothing to transfer. */}
        {/* Wrapper carries the tour anchor (TransferSuggestions is shared and
            does not forward unknown props), and only when there is something to
            transfer — the component renders null otherwise. */}
        {(briefing?.transfer_suggestions?.length ?? 0) > 0 && (
         <div data-tour="hoy.transfers">
          <TransferSuggestions suggestions={briefing?.transfer_suggestions ?? []} canApprove={canEdit} />
         </div>
        )}

        {/* Purchase budget: burn against the calendar and what to fund first.
            Annotates the lines below; it never changes a quantity. */}
        <BudgetPanel sessionId={sessionId} onNotes={setBudgetNotes} reloadToken={budgetReload} />

        {/* The hero: what to order today, as one calm list. Rows share a
            container and are separated by hairlines, so they line up and have
            the same rhythm; the status colour is only the small dot in each. */}
        {cart.filter(i => i.signal === 'PEDIR_YA').length > 0 && (
         <div data-tour="hoy.actions" style={{ marginBottom: 32 }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, color: C.muted, margin: '0 0 12px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
           {t('hoy.section_urgent')}
           <span style={{ fontWeight: 400, color: C.dim, fontVariantNumeric: 'tabular-nums' }}>
            {cart.filter(i => i.signal === 'PEDIR_YA' && i.status !== 'rejected').length}
           </span>
          </h2>
          <div style={{ display: 'grid', gap: 1, background: C.border, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
           {cart.filter(i => i.signal === 'PEDIR_YA').map((item, idx) => (
            <ActionCard
             key={item.sku}
             item={item}
             stale={semaphoreStale}
             noContact={!!item.supplier && noContactNames.has(item.supplier.toLowerCase())}
             lateAlert={item.supplier ? lateBySupplier.get(item.supplier.toLowerCase()) ?? null : null}
             budgetNote={budgetNotes[item.sku] ?? null}
             tourAnchor={idx === 0 ? 'hoy.why' : undefined}
             tourAnchors={idx === 0 ? { supplier: 'hoy.supplier', qty: 'hoy.qty', decide: 'hoy.decide' } : undefined}
             onApprove={() => approveItem(item.sku)}
             onReject={() => rejectItem(item.sku)}
             onUndo={() => unapproveItem(item.sku)}
             onChangeQty={qty => changeQty(item.sku, qty)}
             suppliers={suppliers}
             onChangeSupplier={id => changeSupplier(item.sku, id)}
             canDecide={canEdit}
            />
           ))}
          </div>
         </div>
        )}

        {cart.filter(i => i.signal === 'PEDIR_PRONTO').length > 0 && (
         <div style={{ marginBottom: 32 }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, color: C.muted, margin: '0 0 12px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
           {t('hoy.section_this_week')}
           <span style={{ fontWeight: 400, color: C.dim, fontVariantNumeric: 'tabular-nums' }}>
            {cart.filter(i => i.signal === 'PEDIR_PRONTO' && i.status !== 'rejected').length}
           </span>
          </h2>
          <div style={{ display: 'grid', gap: 1, background: C.border, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
           {cart.filter(i => i.signal === 'PEDIR_PRONTO').map(item => (
            <ActionCard
             key={item.sku}
             item={item}
             stale={semaphoreStale}
             noContact={!!item.supplier && noContactNames.has(item.supplier.toLowerCase())}
             lateAlert={item.supplier ? lateBySupplier.get(item.supplier.toLowerCase()) ?? null : null}
             budgetNote={budgetNotes[item.sku] ?? null}
             onApprove={() => approveItem(item.sku)}
             onReject={() => rejectItem(item.sku)}
             onUndo={() => unapproveItem(item.sku)}
             onChangeQty={qty => changeQty(item.sku, qty)}
             suppliers={suppliers}
             onChangeSupplier={id => changeSupplier(item.sku, id)}
             canDecide={canEdit}
            />
           ))}
          </div>
         </div>
        )}

        {/* What we estimated ourselves, as a footnote under the list it is
            about: one plain sentence and a link, not a box. */}
        {assumptions.fields.length > 0 && cart.length > 0 && (
         <p data-tour="hoy.assumptions" style={{ fontSize: 12, color: C.dim, margin: '-12px 0 32px', lineHeight: 1.6 }}>
          {t(assumptions.skus === 1 ? 'hoy.assumed_note_one' : 'hoy.assumed_note_other', { n: assumptions.skus })}{' '}
          <Link href="/configurar-inventario" style={{ color: 'var(--accent)', fontWeight: 600, textDecoration: 'none' }}>
           {t('hoy.assumed_note_cta')}
          </Link>
         </p>
        )}

        {/* All-rejected empty state */}
        {cart.length > 0 && cart.every(i => i.status === 'rejected') && <AllClear stale={semaphoreStale} unmeasured={nothingCounted} />}

        {/* No risks / warnings at all */}
        {cart.length === 0 && <AllClear stale={semaphoreStale} unmeasured={nothingCounted} />}

        {/* Price breaks (3.5) and cash calendar (3.6) — both judged against
            the cart as it stands, so they sit right above it. */}
        {approved.length > 0 && (
         <BudgetCartCheck lines={budgetCheckLines} destination={multi ? destWarehouse || undefined : undefined}
          reason={budgetReason} onReason={setBudgetReason} />
        )}

        {approved.length > 0 && priceBreaks && (
         <PriceBreakPanel
          opportunities={priceBreaks.opportunities}
          totalNetSaving={priceBreaks.total_net_saving}
          currency={fmtMoney}
          onApplyStepUp={applyStepUp}
         />
        )}

        {approved.length > 0 && (
         <CashFitPanel
          calendar={cashCalendar}
          fit={cashFit}
          currency={fmtMoney}
          onBudgetChange={setCashBudget}
          busy={cashFitBusy}
         />
        )}

        {/* Sticky cart. It materialises far from the Approve button that
            summoned it — bottom of the screen, sometimes a full viewport away —
            so it arrives from its own edge: that short travel is what ties the
            approval to the total being committed. Enter only; clearing the cart
            must feel instant. */}
        {approved.length > 0 && (
         <div className="cart-bar-enter" data-tour="hoy.cart" style={{
          position: 'sticky', bottom: 16,
          background: 'var(--surface)', border: '1px solid var(--border-strong)',
          borderRadius: 12, padding: '14px 20px',
          boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
          display: 'flex', alignItems: 'center', gap: 16,
          marginTop: 8,
         }}>
          <div style={{ flex: 1 }}>
           <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
            {approved.length} {t('hoy.cart_products_approved')}
           </div>
           <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>
            {approved.map(i => `${i.name}: ${fmtNum(i.qty)} ${t('hoy.cart_unit_abbrev')}`).join(' · ')}
            {/* The eye is on the card that was just approved, not down here.
                A background flash points at the figure that changed; `key` is
                the value itself, so React remounts the span and the animation
                re-runs on every change. The digits are never animated — during
                a count-up the screen would show a total the buyer is not
                actually committing. */}
            {totalValue > 0 && (
             <span key={totalValue} className="value-changed" style={{ borderRadius: 4, padding: '0 3px' }}>
              {` · ${t('hoy.cart_total_label')}: ${fmtMoney(totalValue)}`}
             </span>
            )}
            {totalValue > 0 && uncostedLines > 0 && (
             <span style={{ color: C.muted }}>
              {` (${t('hoy.cart_total_uncosted', { count: uncostedLines })})`}
             </span>
            )}
           </div>
           {/* Margen visible en el carrito (2.10) */}
           {salesProtected > 0 && (
            <div style={{ fontSize: 12, color: C.muted, marginTop: 4 }}>
             {t('hoy.cart_protects_prefix')} {fmtMoney(salesProtected)} {t('hoy.cart_protects_sales_suffix')}{' '}
             {fmtMoney(marginProtected)} {t('hoy.cart_protects_margin_suffix')}
            </div>
           )}
           {/* Margin caveat (2.6/0.3 polish): tie the message to the MARGIN
               figure, never to the money total above it — the total uses cost
               and is complete. Two cases: partial (some approved SKUs priced)
               and none priced, where the margin row is absent entirely and the
               note becomes an invitation instead of a warning. */}
           {unpriced.length > 0 && priced.length > 0 && (
            <div style={{ fontSize: 11.5, color: C.dim, marginTop: 3 }}>
             {t('hoy.cart_margin_excludes_prefix')} {unpriced.length} {t('hoy.cart_margin_excludes_suffix')}
            </div>
           )}
           {unpriced.length > 0 && priced.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>
             {t('hoy.cart_margin_add_prices')}
            </div>
           )}
          </div>
          <button
           onClick={() => setCart(prev => prev.map(i =>
            i.status === 'approved' || i.status === 'modified' ? { ...i, status: 'pending' as ActionStatus } : i,
           ))}
           style={{
            all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--dim)',
            padding: '6px 12px', border: '1px solid var(--border)', borderRadius: 7,
           }}
          >
           {t('hoy.btn_clear')}
          </button>
          {/* Destination warehouse (5.4) — only rendered for multi-warehouse
              tenants; mono-warehouse tenants see the cart exactly as before. */}
          {multi && (
           <label data-tour="hoy.cart_warehouse" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--dim)' }}>
            {t('hoy.cart_destination')}
            <select
             name="cart_destination"
             value={destWarehouse}
             onChange={e => setDestWarehouse(e.target.value)}
             style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: 6, padding: '5px 8px', color: 'var(--text)',
              fontSize: 12, fontWeight: 600, outline: 'none', cursor: 'pointer',
             }}
            >
             {warehouses.map(w => (
              <option key={w.id} value={w.name}>{w.name}</option>
             ))}
            </select>
           </label>
          )}
          <button data-tour="hoy.download" onClick={downloadOC} disabled={submitting}
           aria-busy={submitting} style={{
           all: 'unset', cursor: submitting ? 'wait' : 'pointer', padding: '10px 20px', borderRadius: 8,
           background: 'var(--accent)', color: '#fff', fontSize: 14, fontWeight: 600,
           display: 'flex', alignItems: 'center', gap: 8, opacity: submitting ? 0.6 : 1,
          }}>
           {submitting ? t('hoy.btn_download_po_busy') : t('hoy.btn_download_po')}
          </button>
         </div>
        )}

        {/* Generate→send in one flow: right after a PO is logged, offer to
            send it to its suppliers without navigating away to /orders. */}
        {generatedPO && (
         <div style={{
          marginTop: 12, background: 'var(--surface)', border: '1px solid var(--border-strong)',
          borderRadius: 12, padding: '16px 20px',
         }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
           <Send size={14} color="var(--muted)" />
           <span style={{ fontSize: 14, fontWeight: 700, color: C.text, flex: 1 }}>
            {t('hoy.generate_send_title')}
           </span>
           <button onClick={dismissGeneratedPO} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex' }}>
            <X size={15} aria-hidden="true" />
           </button>
          </div>
          <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 12px' }}>
           {t('hoy.generate_send_subtitle')}
          </p>

          {/* Summary of which supplier gets which lines, before confirming */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
           {Object.entries(
            generatedLines.reduce<Record<string, ActionItem[]>>((acc, i) => {
             const key = i.supplier || ''
             ;(acc[key] = acc[key] || []).push(i)
             return acc
            }, {}),
           ).map(([supplier, lines]) => (
            <div key={supplier || '__none__'} style={{
             display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
             padding: '8px 10px', borderRadius: 8, background: 'var(--surface-2)',
            }}>
             <span style={{ fontWeight: 600, color: C.text, flexShrink: 0 }}>
              {supplier || t('hoy.generate_send_no_supplier')}
             </span>
             <span style={{ color: 'var(--dim)' }}>
              {lines.map(l => `${l.name} (${fmtNum(l.qty)} ${t('hoy.generate_send_units_abbrev')})`).join(' · ')}
             </span>
            </div>
           ))}
          </div>

          {sendState === 'done' && sendResult ? (
           <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {sendResult.sent.map(s => (
             <div key={s.supplier} style={{ fontSize: 12, color: C.muted }}>
              {s.supplier}{s.email ? ' · email' : ''}{s.whatsapp ? ' · WhatsApp' : ''}
             </div>
            ))}
            {sendResult.skipped.map((s, idx) => (
             <div key={`${s.supplier}-${idx}`} style={{ fontSize: 12, color: C.muted }}>
              {s.supplier || '—'}: {sendReason(s.reason, t)}
             </div>
            ))}
            {(sendResult.unresolved ?? []).length > 0 && (
             <div style={{ fontSize: 12, color: C.muted }}>
              {t('roi.send_po_unresolved')}{' '}
              {(sendResult.unresolved ?? []).map(u => u.sku).join(', ')}
             </div>
            )}
            {confirmationNote(sendResult, t) && (
             <div style={{ fontSize: 12, color: C.muted }}>{confirmationNote(sendResult, t)}</div>
            )}
           </div>
          ) : (
           <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {sendError != null && (
             <div style={{ fontSize: 12, color: C.red }}>
              {t('hoy.generate_send_failed')} {errorDetail(sendError)}
             </div>
            )}
            {poApproval?.required && (
             <p style={{ margin: 0, fontSize: 12, color: C.muted }}>{t('po_approval.generated_hint')}</p>
            )}
            {!poApproval?.required && (
             <RequestConfirmationCheckbox checked={requestConfirmation} onChange={setRequestConfirmation} />
            )}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {poApproval?.required ? (
             <RequestApprovalButton poLogId={generatedPO.id} approval={poApproval} onChanged={reloadPoApproval} />
            ) : (
            <button
             onClick={sendGeneratedPONow}
             disabled={sendState === 'sending'}
             style={{
              all: 'unset', cursor: sendState === 'sending' ? 'not-allowed' : 'pointer',
              padding: '9px 18px', borderRadius: 8, background: 'var(--accent)', color: '#fff',
              fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6,
              opacity: sendState === 'sending' ? 0.7 : 1,
             }}
            >
             <Send size={13} />
             {sendState === 'sending' ? t('roi.send_po_sending') : t('hoy.generate_send_btn')}
            </button>
            )}
            <Link href="/pedidos" style={{
             fontSize: 13, color: 'var(--dim)', textDecoration: 'none',
             display: 'flex', alignItems: 'center', padding: '9px 4px',
            }}>
             {t('hoy.generate_send_go_orders')}
            </Link>
            </div>
           </div>
          )}

          {/* Forward-it-yourself path: no StockAI↔supplier integration needed. */}
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
           <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 8px' }}>
            {t('po.forward_hint')}
           </p>
           <ForwardPOActions poLogId={generatedPO.id} approval={poApproval} />
          </div>
         </div>
        )}
       </div>

       {/* Compras y transferencias suggested — optimizer plan (MW-3) */}
       {optimizationLoading && !optimization && (
        <p style={{ fontSize: 12, color: C.dim, marginTop: 24 }}>
         {t('hoy.optimizer_loading')}
        </p>
       )}
       {/* Products the optimizer refused to decide for. Rendered even when there
          is nothing else to show: "no suggestions" and "no suggestions BECAUSE
          nobody recorded the stock" look identical on screen, and only one of
          them is the user's to fix. */}
      {optimization && (optimization.needs_stock?.length ?? 0) > 0
        && optimization.needs_stock!.length !== uncounted && (
       <section style={{
        marginTop: 32, padding: '14px 16px', borderRadius: 12,
        border: '1px solid var(--border)', background: 'var(--surface)',
       }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
         {t('hoy.needs_stock_title').replace('{count}', String(optimization.needs_stock!.length))}
        </h3>
        <p style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.5, marginBottom: 8 }}>
         {t('hoy.needs_stock_body')}
        </p>
        <p style={{ fontSize: 12, color: 'var(--text)', marginBottom: 8 }}>
         {optimization.needs_stock!.slice(0, 12).join(', ')}
         {optimization.needs_stock!.length > 12 &&
          ` … +${optimization.needs_stock!.length - 12}`}
        </p>
        <Link href="/inventario" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>
         {t('hoy.needs_stock_cta')}
        </Link>
       </section>
      )}

       <MoreAnalysis sections={moreSections}>
      {/* `status === 'fallback'` is part of the condition, not just of the notice
          inside it. The whole section used to render only when there were lines
          to show — so a greedy fallback that produced an EMPTY plan drew nothing
          at all, and the buyer read that silence as "nothing to order". The
          truth was "the optimiser gave up and we do not know", which is a
          different sentence and the more expensive one to get wrong. */}
      {optimization && (optimization.orders.length > 0 || optimization.transfers.length > 0
                        || optimization.status === 'fallback') && (
        <section style={{ marginTop: 16, marginBottom: 20 }}>
         <h2 style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>
          {t('hoy.optimizer_title')}
         </h2>
         {(optimization.orders.length > 0 || optimization.transfers.length > 0) && (
          <>
         <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 6 }}>
          {t('hoy.optimizer_subtitle').replace('{horizon}', String(optimization.horizon_days))}
         </p>
         {/* Why this panel's numbers are bigger than the semáforo's, said before
             the buyer has to wonder. The two answer different questions — "order
             today" vs "cover the horizon" — and standing next to each other with
             no explanation they read as a contradiction: measured on one tenant,
             the semáforo said SKU-002 needed nothing while this said buy 1966. */}
         <p style={{ fontSize: 11.5, color: 'var(--dim)', marginBottom: 14, lineHeight: 1.6 }}>
          {t('hoy.optimizer_vs_semaforo').replace('{horizon}', String(optimization.horizon_days))}
         </p>
          </>
         )}
         {/* Math audit O1: a SKU whose supplier takes as long as the horizon
             used to be planned at 0 under a heading promising {horizon} days.
             Its plan now reaches the next order's arrival, and the panel says
             how many lines that is before the buyer reads them. */}
         {(optimization.extended_lines ?? 0) > 0 && (
          <p style={{ fontSize: 11.5, color: 'var(--text)', marginBottom: 14, lineHeight: 1.6 }}>
           {t('hoy.optimizer_extended_note', {
            count: optimization.extended_lines, horizon: optimization.horizon_days,
           })}
          </p>
         )}

         {/* The cost optimiser could not finish, so these lines come from the
             greedy fallback — which ignores transfers entirely and buys each
             day's shortfall. Measured: the same tenant got 22 transfers and 2
             purchase lines when the solve completed, and 5 purchase lines with
             no transfers when it did not. Presenting the second as "the
             optimisation plan" without a word is how a buyer ends up ordering
             stock they already own in another warehouse. */}
         {optimization.status === 'fallback' && (
          <p style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 14, padding: '8px 10px',
                      borderRadius: 8, color: 'var(--text)',
                      background: 'var(--surface-2)',
                      border: '1px solid var(--border)' }}>
           {/* An empty fallback needs its own sentence. The standard notice says
               "this list was built by a simpler rule" — about a list that is not
               there, which reads as reassurance instead of a warning. */}
           {optimization.orders.length === 0 && optimization.transfers.length === 0
             ? t('hoy.optimizer_fallback_empty')
             : t('hoy.optimizer_fallback_notice')}
          </p>
         )}

         {optimization.orders.length > 0 && (
          <div style={{ marginBottom: 16 }}>
           <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            {t('hoy.optimizer_orders_title')}
           </h3>
           <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 8 }}>
           {optimization.orders.map(order => (
            <div key={`${order.sku}-${order.warehouse}`} style={{
             display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
             padding: '8px 12px', border: '1px solid var(--border)', borderRadius: 8, minWidth: 0,
            }}>
             <span style={{ fontSize: 13 }}>
              {order.sku} — {order.warehouse}: <strong>{order.qty}</strong>
              {order.sized_like_panel && order.effective_horizon_days != null && (
               <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>
                {t('hoy.optimizer_line_extended', { days: order.effective_horizon_days })}
               </span>
              )}
             </span>
             {/* Writes a PO in one click, so it is refused for a viewer — and
                 it has no catch of its own: the failure surfaced only as the
                 shared 403 toast with the line still sitting there. */}
             {canEdit && (
              <button onClick={() => convertOrderToPO(order)} style={{
               all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600,
               color: 'var(--accent)', padding: '4px 10px', borderRadius: 6,
              }}>
               {t('hoy.optimizer_convert_to_po')}
              </button>
             )}
            </div>
           ))}
           </div>
          </div>
         )}

         {optimization.transfers.length > 0 && (
          <div>
           <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            {t('hoy.optimizer_transfers_title')}
           </h3>
           <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 8 }}>
           {optimization.transfers.map(tr => (
            <div key={`${tr.sku}-${tr.from_warehouse}-${tr.to_warehouse}`} style={{
             fontSize: 13, padding: '8px 12px', border: '1px solid var(--border)',
             borderRadius: 8,
            }}>
             {t('hoy.optimizer_transfer_line')
              .replace('{qty}', String(tr.qty))
              .replace('{sku}', tr.sku)
              .replace('{from}', tr.from_warehouse)
              .replace('{to}', tr.to_warehouse)}
            </div>
           ))}
           </div>
          </div>
         )}
        </section>
       )}

       {/* Anticípate — proactive future demand peaks */}
       {(briefing.demand_spikes?.length ?? 0) > 0 && (
        <section style={{ marginTop: 16, marginBottom: 20 }}>
         <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <h2 style={{ fontSize: 14, fontWeight: 600, color: C.text, margin: 0 }}>
           {t('hoy.section_anticipate_title')}
          </h2>
         </div>
         <p style={{ fontSize: 12, color: C.dim, margin: '0 0 14px' }}>
          {t('hoy.section_anticipate_desc')}
         </p>
         {(briefing.demand_spikes ?? []).map(s => (
          <SpikeCard key={s.sku} s={s} />
         ))}
        </section>
       )}

       {/* Demand changes & other sections below the queue */}
       {briefing.demand_changes.length > 0 && (
        <section style={{ marginTop: 16, marginBottom: 20 }}>
         <h2 style={{ fontSize: 14, fontWeight: 600, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_demand_changes')}
         </h2>
         <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 10, overflow: 'hidden',
         }}>
          {briefing.demand_changes.map((item, idx) => {
           const pct = item.demand_trend_pct
           const up  = pct > 0
           const pctColor = C.text
           const sign     = up ? '+' : ''
           return (
            <div
             key={item.sku}
             style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '12px 16px',
              borderBottom: idx < briefing.demand_changes.length - 1
               ? `1px solid ${C.border}` : 'none',
             }}
            >
             {up ? <TrendingUp size={16} color="var(--dim)" /> : <TrendingDown size={16} color="var(--dim)" />}
             <span style={{ fontSize: 13, fontWeight: 600, color: pctColor, minWidth: 52, fontVariantNumeric: 'tabular-nums' }}>
              {sign}{pct.toFixed(0)}%
             </span>
             <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{item.sku}</span>
             {item.display_name && (
              <span style={{ fontSize: 13, color: C.muted }}>— {item.display_name}</span>
             )}
             <span style={{ fontSize: 12, color: C.dim, marginLeft: 'auto' }}>
              {up
               ? t('hoy.demand_running_above_forecast')
               : t('hoy.demand_running_below_forecast')
              }
             </span>
            </div>
           )
          })}
         </div>
        </section>
       )}

       {recommendationsNotOnCards(briefing).length > 0 && (
        <section style={{ marginBottom: 20 }}>
         <h2 style={{ fontSize: 14, fontWeight: 600, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_system_recommendations')}
         </h2>
         <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {recommendationsNotOnCards(briefing).slice(0, 8).map((rec, idx) => (
           <div
            key={idx}
            style={{
             background: C.surface, border: `1px solid ${C.border}`,
             borderRadius: 10, padding: '14px 16px',
            }}
           >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
             <div style={{ flexShrink: 0, marginTop: 1 }}>
              <RecIcon rec_type={rec.rec_type} />
             </div>
             <span style={{ fontSize: 13, color: C.text, lineHeight: 1.5 }}>
              {recText(rec, briefing?.coverage_unit, t)}
             </span>
            </div>
            <p style={{ fontSize: 12, color: C.muted, margin: 0, paddingLeft: 26 }}>
             {t('hoy.suggested_action_label')}: {recAction(rec, t)}
            </p>
           </div>
          ))}
         </div>
        </section>
       )}

       {briefing.overstocked.length > 0 && kpis!.capital_in_overstock > 0 && (
        <section style={{ marginBottom: 20 }}>
         <h2 style={{ fontSize: 14, fontWeight: 600, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_capital_opportunities')}
         </h2>
         <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 12, padding: '16px 20px',
         }}>
          <p style={{ fontSize: 14, color: C.text, margin: '0 0 16px' }}>
           {t('hoy.capital_overstock_prefix')} {fmtM(kpis!.capital_in_overstock)} {t('hoy.capital_overstock_suffix')}
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
           {briefing.overstocked.slice(0, 3).map(item => (
            <div
             key={item.sku}
             style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              background: C.surface, border: `1px solid ${C.border}`,
              borderRadius: 8, padding: '10px 14px',
             }}
            >
             <div>
              <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{item.sku}</span>
              {item.display_name && (
               <span style={{ fontSize: 13, color: C.muted }}> — {item.display_name}</span>
              )}
             </div>
             <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
              {item.coverage_days != null && (
               <span style={{ fontSize: 12, color: C.dim }}>
                {Math.round(item.coverage_days)} {coverageUnitLabel(briefing?.coverage_unit, Math.round(item.coverage_days), t)} {t('hoy.reason_coverage_suffix')}
               </span>
              )}
              {item.inventory_value != null && (
               <span style={{ fontSize: 13, fontWeight: 600, color: C.text, fontVariantNumeric: 'tabular-nums' }}>
                {fmtM(item.inventory_value)}
               </span>
              )}
             </div>
            </div>
           ))}
          </div>
         </div>
        </section>
       )}

       </MoreAnalysis>

       {/* Footer */}
       <div style={{
        marginTop: 24, paddingTop: 20, borderTop: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 12,
       }}>
        <div style={{ fontSize: 12, color: C.dim }}>
         {/* The session name is in the header ("Actualización en uso"); it is
             not repeated here. */}
         {loadedAt && <>{t('hoy.footer_last_update')}: {timeSince(loadedAt, t)}</>}
        </div>
        <button
         onClick={() => load(sessionId)}
         style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '7px 16px', background: C.surface,
          border: `1px solid ${C.border}`, borderRadius: 7,
          cursor: 'pointer', fontSize: 13, color: C.text,
         }}
        >
         <RefreshCw size={13} /> {t('hoy.btn_refresh_data')}
        </button>
       </div>

       {/* No "Ver todos en Inventario" link here: Inventario is in the sidebar
           beside it, and the uncounted-products caption above links there too. */}
      </>
     )}
    </>
   )}

   </div>

   {/* Reception modal — reused from /orders, opened from the overdue-reception nudge */}
   {receivingPO && (
    <ReceptionModal
     poId={receivingPO}
     onClose={() => setReceivingPO(null)}
     onSaved={() => {
      setReceivingPO(null)
      loadOverdue()
      getPOHistory(20)
       .then(list => setPendingPOs(list.filter(p =>
        ['pending', 'partial'].includes(p.reception_status ?? 'pending'),
       )))
       .catch(() => {})
     }}
    />
   )}
  </div>
 )
}

// ── The rest of the briefing, phone-sized ────────────────────────────────────
// Everything the desktop shows below the work queue, as stacked sections a
// thumb can scroll: the optimizer plan (with "convert to PO"), the products the
// optimizer refused to decide for, demand peaks, demand changes, the system's
// recommendations and the capital tied up in overstock. Same data, same copy
// and the same guards as the desktop sections — only the layout differs.
function HoyMobileExtras({ briefing, optimization, optimizationLoading, canEdit, onConvert }: {
 briefing: MorningBriefing
 optimization: OptimizationResponse | null
 optimizationLoading: boolean
 canEdit: boolean
 onConvert: (order: OptimizationOrder) => void
}) {
 const { t } = useLanguage()
 const kpis = briefing.kpis
 const sectionTitle: React.CSSProperties = { fontSize: 14, fontWeight: 600, color: C.text, margin: '0 0 4px' }
 const sectionDesc: React.CSSProperties = { fontSize: 12.5, color: C.dim, margin: '0 0 10px', lineHeight: 1.5 }
 const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', minHeight: 52,
  boxSizing: 'border-box', borderTop: `1px solid ${C.border}`,
 }
 const first = (idx: number): React.CSSProperties => (idx === 0 ? { ...row, borderTop: 'none' } : row)
 const group: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, background: C.surface,
  border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden',
 }
 const showPlan = optimization && (optimization.orders.length > 0 || optimization.transfers.length > 0
  || optimization.status === 'fallback')
 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 22, marginTop: 10, minWidth: 0 }}>
   {optimizationLoading && !optimization && (
    <p style={{ fontSize: 12.5, color: C.dim, margin: 0 }}>{t('hoy.optimizer_loading')}</p>
   )}

   {/* Same rule as the desktop: the KPI caption already names this count and
       carries the link, so the card only appears when its number differs. */}
   {optimization && (optimization.needs_stock?.length ?? 0) > 0
     && optimization.needs_stock!.length !== (briefing.kpis?.sin_datos ?? 0) && (
    <section style={{ padding: '12px 14px', borderRadius: 12, border: `1px solid ${C.border}`, background: C.surface }}>
     <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 4px', color: C.text }}>
      {t('hoy.needs_stock_title').replace('{count}', String(optimization.needs_stock!.length))}
     </h3>
     <p style={{ ...sectionDesc, margin: '0 0 6px' }}>{t('hoy.needs_stock_body')}</p>
     <p style={{ fontSize: 13, color: C.text, margin: '0 0 6px', overflowWrap: 'anywhere' }}>
      {optimization.needs_stock!.slice(0, 12).join(', ')}
      {optimization.needs_stock!.length > 12 && ` … +${optimization.needs_stock!.length - 12}`}
     </p>
     <Link href="/inventario" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: 44, fontSize: 13.5, fontWeight: 600, color: 'var(--accent)' }}>
      {t('hoy.needs_stock_cta')} <ArrowRight size={14} />
     </Link>
    </section>
   )}

   <MoreAnalysis compact sections={analysisSections(briefing, optimization, recommendationsNotOnCards(briefing).length)}>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22, minWidth: 0 }}>
   {showPlan && optimization && (
    <section>
     <h2 style={sectionTitle}>{t('hoy.optimizer_title')}</h2>
     {(optimization.orders.length > 0 || optimization.transfers.length > 0) && (
      <>
     <p style={sectionDesc}>{t('hoy.optimizer_subtitle').replace('{horizon}', String(optimization.horizon_days))}</p>
     <p style={{ ...sectionDesc, fontSize: 12 }}>{t('hoy.optimizer_vs_semaforo').replace('{horizon}', String(optimization.horizon_days))}</p>
      </>
     )}
     {optimization.status === 'fallback' && (
      <p style={{ fontSize: 12.5, lineHeight: 1.5, margin: '0 0 10px', padding: '8px 10px', borderRadius: 8, color: 'var(--text)',
             background: 'var(--surface-2)',
             border: '1px solid var(--border)' }}>
       {optimization.orders.length === 0 && optimization.transfers.length === 0
        ? t('hoy.optimizer_fallback_empty')
        : t('hoy.optimizer_fallback_notice')}
      </p>
     )}
     {optimization.orders.length > 0 && (
      <>
       <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 6px', color: C.text }}>{t('hoy.optimizer_orders_title')}</h3>
       <ul style={{ ...group, marginBottom: 12 }}>
        {optimization.orders.map((order, idx) => (
         <li key={`${order.sku}-${order.warehouse}`} style={first(idx)}>
          <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, color: C.text, overflowWrap: 'anywhere' }}>
           <span style={{ fontFamily: 'monospace' }}>{order.sku}</span> — {order.warehouse}
          </span>
          <strong style={{ fontSize: 15, fontVariantNumeric: 'tabular-nums', color: C.text }}>{fmtNum(order.qty)}</strong>
          {canEdit && (
           <button onClick={() => onConvert(order)} style={{
            all: 'unset', boxSizing: 'border-box', cursor: 'pointer', flexShrink: 0, minHeight: 44,
            padding: '0 10px', borderRadius: 10, fontSize: 13, fontWeight: 700, color: 'var(--accent)',
            border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)', display: 'flex', alignItems: 'center',
           }}>
            {t('hoy.optimizer_convert_to_po')}
           </button>
          )}
         </li>
        ))}
       </ul>
      </>
     )}
     {optimization.transfers.length > 0 && (
      <>
       <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 6px', color: C.text }}>{t('hoy.optimizer_transfers_title')}</h3>
       <ul style={group}>
        {optimization.transfers.map((tr, idx) => (
         <li key={`${tr.sku}-${tr.from_warehouse}-${tr.to_warehouse}`} style={{ ...first(idx), fontSize: 13.5, color: C.text }}>
          {t('hoy.optimizer_transfer_line')
           .replace('{qty}', String(tr.qty))
           .replace('{sku}', tr.sku)
           .replace('{from}', tr.from_warehouse)
           .replace('{to}', tr.to_warehouse)}
         </li>
        ))}
       </ul>
      </>
     )}
    </section>
   )}

   {(briefing.demand_spikes?.length ?? 0) > 0 && (
    <section>
     <h2 style={{ ...sectionTitle, display: 'flex', alignItems: 'center', gap: 6 }}>
      {t('hoy.section_anticipate_title')}
     </h2>
     <p style={sectionDesc}>{t('hoy.section_anticipate_desc')}</p>
     {(briefing.demand_spikes ?? []).map(sp => <SpikeCard key={sp.sku} s={sp} />)}
    </section>
   )}

   {briefing.demand_changes.length > 0 && (
    <section>
     <h2 style={{ ...sectionTitle, marginBottom: 10 }}>{t('hoy.section_demand_changes')}</h2>
     <ul style={group}>
      {briefing.demand_changes.map((item, idx) => {
       const pct = item.demand_trend_pct
       const up = pct > 0
       return (
        <li key={item.sku} style={first(idx)}>
         {up ? <TrendingUp size={17} color="var(--dim)" aria-hidden="true" /> : <TrendingDown size={17} color="var(--dim)" aria-hidden="true" />}
         <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: C.text, overflow: 'hidden', overflowWrap: 'anywhere', }}>
           {item.display_name || item.sku}
          </span>
          <span style={{ display: 'block', fontSize: 12, color: C.dim }}>
           {up ? t('hoy.demand_running_above_forecast') : t('hoy.demand_running_below_forecast')}
          </span>
         </span>
         <strong style={{ fontSize: 14, fontWeight: 600, color: C.text, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{up ? '+' : ''}{pct.toFixed(0)}%</strong>
        </li>
       )
      })}
     </ul>
    </section>
   )}

   {recommendationsNotOnCards(briefing).length > 0 && (
    <section>
     <h2 style={{ ...sectionTitle, marginBottom: 10 }}>{t('hoy.section_system_recommendations')}</h2>
     <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {recommendationsNotOnCards(briefing).slice(0, 8).map((rec, idx) => (
       <div key={idx} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
         <div style={{ flexShrink: 0, marginTop: 2 }}><RecIcon rec_type={rec.rec_type} /></div>
         <span style={{ fontSize: 13.5, color: C.text, lineHeight: 1.5, minWidth: 0 }}>{recText(rec, briefing.coverage_unit, t)}</span>
        </div>
        <p style={{ fontSize: 12.5, color: C.muted, margin: '6px 0 0', paddingLeft: 26, lineHeight: 1.5 }}>
         {t('hoy.suggested_action_label')}: {recAction(rec, t)}
        </p>
       </div>
      ))}
     </div>
    </section>
   )}

   {briefing.overstocked.length > 0 && kpis.capital_in_overstock > 0 && (
    <section>
     <h2 style={{ ...sectionTitle, marginBottom: 6 }}>{t('hoy.section_capital_opportunities')}</h2>
     <p style={{ fontSize: 13.5, color: C.text, margin: '0 0 10px', lineHeight: 1.5 }}>
      {t('hoy.capital_overstock_prefix')} {fmtM(kpis.capital_in_overstock)} {t('hoy.capital_overstock_suffix')}
     </p>
     <ul style={group}>
      {briefing.overstocked.slice(0, 3).map((item, idx) => (
       <li key={item.sku} style={first(idx)}>
        <span style={{ flex: 1, minWidth: 0 }}>
         <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: C.text, overflow: 'hidden', overflowWrap: 'anywhere', }}>
          {item.display_name || item.sku}
         </span>
         {item.coverage_days != null && (
          <span style={{ display: 'block', fontSize: 12, color: C.dim }}>
           {Math.round(item.coverage_days)} {coverageUnitLabel(briefing.coverage_unit, Math.round(item.coverage_days), t)} {t('hoy.reason_coverage_suffix')}
          </span>
         )}
        </span>
        {item.inventory_value != null && (
         <strong style={{ fontSize: 14, fontWeight: 600, color: C.text, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{fmtM(item.inventory_value)}</strong>
        )}
       </li>
      ))}
     </ul>
    </section>
   )}
    </div>
   </MoreAnalysis>
  </div>
 )
}

// ── KPI figure helper ─────────────────────────────────────────────────────────
// A quiet label over a large tabular number. No box; the optional dot is the
// only colour and it is the status marker, not decoration.
function KpiCard({ label, value, dot, help }: { label: string; value: string; dot?: string; help?: string }) {
 return (
  <div style={{ minWidth: 120 }}>
   <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
    {dot && <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0 }} />}
    {label}
    {help && <HelpTip text={help} size={13} />}
   </div>
   <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1.1, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
    {value}
   </div>
  </div>
 )
}

'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import {
 AlertTriangle, Clock, TrendingUp, TrendingDown, Archive,
 RefreshCw, ArrowRight, BarChart2, Package, Zap, Truck,
 ChevronDown, ChevronUp, Send, UserX, X, Upload, PlayCircle,
} from 'lucide-react'
import {
 getMorningBriefing, getMorningNarrative, getPOHistory, optimizeInventory, logPOGeneration,
 getOverduePOs, sendPOToSuppliers, getSupplierContactHealth, getSupplierLeadTimeAlerts,
 evaluatePriceBreaks, getCashCalendar, checkCashFit,
} from '@/lib/api'
import type {
 MorningBriefing, BriefingRecommendation, MorningNarrative, DemandSpike, POLogEntry,
 OptimizationResponse, OptimizationOrder, POLineDecision, OverdueReception, SendPOResult,
 SupplierContactHealthRow, SupplierLeadTimeAlert,
 PriceBreakEvaluation, CashCalendar, CashFitResult,
} from '@/lib/types'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import {
 SupplierContactHealthBanner, SupplierLeadTimeAlertBanner,
} from '@/components/suppliers/SupplierHealthBanners'
import { TransferSuggestions } from '@/components/inventory/TransferSuggestions'
import { useWarehouses } from '@/components/inventory/WarehouseControls'
import { coverageUnitLabel } from '@/lib/period'
import { PriceBreakPanel } from '@/components/inventory/PriceBreakPanel'
import { CashFitPanel } from '@/components/inventory/CashFitPanel'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import DataFreshness from '@/components/ui/DataFreshness'
import SignalBadge from '@/components/ui/SignalBadge'
import { getUser } from '@/lib/auth'
import Spinner from '@/components/ui/Spinner'
import {
  EmptyState, ErrorState, LoadingState, SkeletonCards, SkeletonTable,
} from '@/components/ui/States'
import NarrativeCard from '@/components/ui/NarrativeCard'
import HelpTip from '@/components/ui/HelpTip'
import { ReceptionModal } from '@/components/po/POHistory'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
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
 indigo: '#818cf8',
}

// ── Formatters ────────────────────────────────────────────────────────────────
// Money formatting lives in lib/currency.ts — one source of truth for the whole
// app, in the anchor market's currency (CRC).
const fmtM = formatMoneyCompact
const fmtMoney = formatMoney

function fmtPct(n: number | null) {
 if (n == null) return '—'
 return `${(n * 100).toFixed(1)}%`
}

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

function formatDateES(isoDate: string, lang: string) {
 const d = new Date(isoDate + 'T12:00:00')
 return d.toLocaleDateString(lang, {
  weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
 })
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

function RecIcon({ rec_type }: { rec_type: BriefingRecommendation['rec_type'] }) {
 switch (rec_type) {
  case 'STOCKOUT_RISK': return <AlertTriangle size={16} color={C.red} />
  case 'REORDER_SOON': return <Clock size={16} color={C.amber} />
  case 'DEMAND_UP': return <TrendingUp size={16} color={C.green} />
  case 'DEMAND_DOWN': return <TrendingDown size={16} color={C.red} />
  case 'OVERSTOCK': return <Archive size={16} color={C.blue} />
 }
}

// ── Cart types ────────────────────────────────────────────────────────────────
type ActionStatus = 'pending' | 'approved' | 'modified' | 'rejected'

interface ActionItem {
 sku:            string
 name:           string
 supplier:      string | null
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
 lead_time_source: 'learned' | 'configured'
 reorder_point:    number | null
 explanation:      string | null
 unit_margin:  number | null   // null = SKU sin price o sin cost
 reason:         string
 status:         ActionStatus
}

// ── ActionCard component ──────────────────────────────────────────────────────
function ActionCard({ item, onApprove, onReject, onChangeQty }: {
 item:        ActionItem
 onApprove:   () => void
 onReject:    () => void
 onChangeQty: (qty: number) => void
}) {
 const { t } = useLanguage()
 const [editing, setEditing] = useState(false)
 const [qtyInput, setQtyInput] = useState(String(item.qty))
 const [showWhy, setShowWhy] = useState(false)

 // Keep qtyInput in sync when item.qty changes externally (e.g. after reset)
 useEffect(() => {
  setQtyInput(String(item.qty))
 }, [item.qty])

 const isUrgent  = item.signal === 'PEDIR_YA'
 const accent    = isUrgent ? 'var(--signal-order-now-fg)' : 'var(--signal-order-soon-fg)'
 const isApproved = item.status === 'approved' || item.status === 'modified'
 const isRejected = item.status === 'rejected'

 const estimatedValue = item.qty * (item.unit_cost ?? 0)
 const canOrder = item.qty > 0
 const hasWhyData = item.daily_demand != null || item.current_stock != null
  || item.days != null || item.explanation != null

 return (
  <div style={{
   border:       `1px solid ${isApproved ? '#22c55e40' : isRejected ? 'var(--border)' : accent + '30'}`,
   borderLeft:   `4px solid ${isApproved ? '#22c55e'  : isRejected ? 'var(--border)' : accent}`,
   borderRadius: 10,
   padding:      '16px 18px',
   marginBottom: 10,
   background:   isApproved ? 'rgba(34,197,94,0.03)' : isRejected ? 'var(--surface-2)' : 'var(--surface)',
   opacity:      isRejected ? 0.5 : 1,
   transition:   'all 0.2s',
  }}>

   {/* Header */}
   <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 8 }}>
    <div>
     <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>{item.name}</span>
      {/* El borde de color solo no dice en qué estado está el SKU: el badge
          añade icono + etiqueta (WCAG 1.4.1). */}
      <SignalBadge signal={item.signal} />
      <span style={{
       fontSize: 10, fontFamily: 'monospace', color: 'var(--dim)',
       background: 'var(--surface-2)', padding: '2px 6px', borderRadius: 4,
      }}>{item.sku}</span>
      {isApproved && (
       <span style={{
        fontSize: 10, fontWeight: 700, color: '#22c55e',
        background: 'rgba(34,197,94,0.1)', padding: '2px 8px', borderRadius: 20,
       }}>{t('hoy.badge_approved')}</span>
      )}
     </div>
     <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 3 }}>{item.reason}</div>
     {item.supplier && (
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{item.supplier}</div>
     )}
    </div>
    {hasWhyData && (
     <button
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
    <div style={{
     background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8,
     padding: '10px 12px', marginBottom: 12, fontSize: 12,
    }}>
     {item.explanation && (
      <p style={{
       margin: '0 0 10px', fontSize: 13, lineHeight: 1.55, color: 'var(--text)',
      }}>
       {item.explanation}
      </p>
     )}
     <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10,
     }}>
     {item.days != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_coverage_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {Math.round(item.days)} {t('hoy.why_days')}
       </div>
      </div>
     )}
     {item.daily_demand != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_demand_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {item.daily_demand.toLocaleString('es', { maximumFractionDigits: 1 })} {t('hoy.why_units_day')}
       </div>
      </div>
     )}
     <div>
      <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
       {t('hoy.why_lead_time_label')}
      </div>
      <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
       {item.lead_time} {t('hoy.why_days')}
      </div>
      <div style={{ color: 'var(--dim)', fontSize: 10, marginTop: 2 }}>
       {item.lead_time_source === 'learned'
        ? t('hoy.why_lead_time_learned')
        : t('hoy.why_lead_time_configured')}
      </div>
     </div>
     {item.current_stock != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_stock_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {Math.round(item.current_stock).toLocaleString('es')} {t('hoy.why_units')}
       </div>
      </div>
     )}
     {item.reorder_point != null && (
      <div>
       <div style={{ color: 'var(--dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {t('hoy.why_reorder_point_label')}
       </div>
       <div style={{ color: 'var(--text)', fontWeight: 700, marginTop: 2 }}>
        {Math.round(item.reorder_point).toLocaleString('es')} {t('hoy.why_units')}
       </div>
      </div>
     )}
     </div>
    </div>
   )}

   {/* Quantity + Value + Actions */}
   {!isRejected && (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
     <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.label_order_qty')}</span>
      {editing ? (
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
        all: 'unset', cursor: 'pointer', fontSize: 18, fontWeight: 800, color: accent,
        borderBottom: '2px dashed ' + accent + '60', lineHeight: 1,
       }}>
        {item.qty.toLocaleString('es')}
       </button>
      )}
      <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.label_units')}</span>
      {estimatedValue > 0 && (
       <span style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'monospace' }}>
        ≈ {fmtMoney(estimatedValue)}
       </span>
      )}
     </div>

     {/* Action buttons */}
     {!isApproved ? (
      <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
       {canOrder ? (
        <>
         <button onClick={onApprove} style={{
          all: 'unset', cursor: 'pointer', padding: '7px 16px', borderRadius: 8,
          background: '#22c55e', color: '#fff', fontSize: 13, fontWeight: 700,
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
         {t('hoy.enough_stock')}
        </span>
       )}
      </div>
     ) : (
      <button onClick={onReject} style={{
       all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--dim)',
       marginLeft: 'auto', textDecoration: 'underline',
      }}>
       {t('hoy.btn_undo')}
      </button>
     )}
    </div>
   )}

   {isRejected && (
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
 const accent = s.already_late ? C.red : C.amber
 return (
  <div style={{
   border:       `1px solid ${accent}30`,
   borderLeft:   `4px solid ${accent}`,
   borderRadius: 10, padding: '14px 18px', marginBottom: 10,
   background:   'var(--surface)',
  }}>
   <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
    <Zap size={16} color={accent} style={{ flexShrink: 0, marginTop: 2 }} />
    <div style={{ flex: 1, minWidth: 0 }}>
     <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{s.display_name}</span>
      <span style={{
       fontSize: 11, fontWeight: 700, color: C.green,
       background: 'rgba(34,197,94,0.1)', padding: '2px 8px', borderRadius: 20,
      }}>+{s.uplift_pct}% {t('hoy.spike_projected')}</span>
     </div>
     <div style={{ fontSize: 13, color: C.muted, marginTop: 4, lineHeight: 1.5 }}>
      {t('hoy.spike_peak_expected_prefix')} <strong style={{ color: C.text }}>{shortDateES(s.peak_date, lang)}</strong>
      {' '}({t('hoy.spike_in_days_prefix')} {s.days_until_peak} {t('hoy.spike_days_unit')}{s.days_until_peak !== 1 ? 's' : ''}).
      {' '}{t('hoy.spike_supplier_lead_prefix')} {s.lead_time_days} {t('hoy.spike_days_unit')}.
     </div>
     <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8,
      fontSize: 13, fontWeight: 700, color: accent,
     }}>
      <Clock size={13} />
      {s.already_late
       ? t('hoy.spike_already_late')
       : <>{t('hoy.spike_order_before_prefix')} {shortDateES(s.order_by_date, lang)} {t('hoy.spike_order_before_suffix')}</>}
     </div>
    </div>
   </div>
  </div>
 )
}

// ── Build ActionItems from briefing ──────────────────────────────────────────
function buildActionItems(b: MorningBriefing, t: (k: string) => string): ActionItem[] {
 const items: ActionItem[] = []
 // Coverage figures are in the briefing's active period unit (weeks under a
 // weekly session); lead time is always real calendar days.
 const cu = b.coverage_unit

 for (const risk of (b.risks ?? [])) {
  const d = risk.coverage_days != null ? Math.round(risk.coverage_days) : null
  const reason = d != null
   ? `${t('hoy.reason_stock_left_prefix')} ${d} ${coverageUnitLabel(cu, d, t)} ${t('hoy.reason_stock_left_suffix')} ${risk.lead_time_days} ${dayUnit(risk.lead_time_days, t)}`
   : `${t('hoy.reason_immediate_risk')} — ${t('hoy.reason_lead_time_label')} ${risk.lead_time_days} ${dayUnit(risk.lead_time_days, t)}`
  items.push({
   sku:            risk.sku,
   name:           risk.display_name || risk.sku,
   supplier:      risk.supplier || null,
   qty:            risk.recommended_qty ?? 0,
   recommended:    risk.recommended_qty ?? 0,
   unit_cost:      risk.unit_cost ?? null,
   sale_price:   risk.sale_price ?? null,
   signal:         'PEDIR_YA',
   days:           risk.coverage_days ?? null,
   lead_time:      risk.lead_time_days,
   daily_demand: risk.daily_demand ?? null,
   current_stock:   risk.current_stock ?? null,
   lead_time_source: risk.lead_time_source ?? 'configured',
   reorder_point:    risk.reorder_point ?? null,
   explanation:      risk.explanation ?? null,
   unit_margin:  risk.unit_margin ?? null,
   reason,
   status:      'pending',
  })
 }

 for (const w of (b.warnings ?? [])) {
  const d = w.coverage_days != null ? Math.round(w.coverage_days) : null
  items.push({
   sku:            w.sku,
   name:           w.display_name || w.sku,
   supplier:      w.supplier || null,
   qty:            w.recommended_qty ?? 0,
   recommended:    w.recommended_qty ?? 0,
   unit_cost:      w.unit_cost ?? null,
   sale_price:   w.sale_price ?? null,
   signal:         'PEDIR_PRONTO',
   days:           w.coverage_days ?? null,
   lead_time:      w.lead_time_days,
   daily_demand: w.daily_demand ?? null,
   current_stock:   w.current_stock ?? null,
   lead_time_source: w.lead_time_source ?? 'configured',
   reorder_point:    w.reorder_point ?? null,
   explanation:      w.explanation ?? null,
   unit_margin:  w.unit_margin ?? null,
   reason:      `${d != null ? d + ' ' + coverageUnitLabel(cu, d, t) + ' ' + t('hoy.reason_coverage_suffix') : t('hoy.reason_next_order_recommended')} — ${t('hoy.reason_order_this_week')}`,
   status:      'pending',
  })
 }

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
     { label: t('hoy.empty_cta_primary'), href: '/quick-start', icon: <Upload size={15} /> },
     { label: t('hoy.empty_cta_demo'), href: '/quick-start?demo=1', icon: <PlayCircle size={15} />, variant: 'secondary' },
    ] : [
     { label: t('hoy.link_go_inventory'), href: '/inventory', icon: <Upload size={15} /> },
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
 const [leadTimeAlerts, setLeadTimeAlerts] = useState<SupplierLeadTimeAlert[]>([])

 // Price-break opportunities for the current cart (feature 3.5) and the cash
 // picture the cart has to fit into (feature 3.6).
 const [priceBreaks, setPriceBreaks] = useState<PriceBreakEvaluation | null>(null)
 const [cashCalendar, setCashCalendar] = useState<CashCalendar | null>(null)
 const [cashBudget, setCashBudget] = useState<number | null>(null)
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
   // Precedence mirrors the backend default-warehouse rule: is_default flag,
   // then 'principal' (auto-created rows carry is_default=false, so without
   // this the destination fell to the alphabetically-first warehouse — an
   // emoji/capitalized name would win over 'principal').
   const def = warehouses.find(w => w.is_default)
    ?? warehouses.find(w => w.name === 'principal')
    ?? warehouses[0]
   setDestWarehouse(def.name)
  }
 }, [warehouses, destWarehouse])

 // Generate→send in one flow: the PO just logged, awaiting the "send now" decision
 const [generatedPO, setGeneratedPO]   = useState<POLogEntry | null>(null)
 const [generatedLines, setGeneratedLines] = useState<ActionItem[]>([])
 const [sendState, setSendState]       = useState<'idle' | 'sending' | 'done'>('idle')
 const [sendResult, setSendResult]     = useState<SendPOResult | null>(null)

 const user    = getUser()
 const { advancedMode } = useBusinessProfile()
 const { addToast } = useToast()

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
  optimizeInventory(sessionId, 30)
   .then(setOptimization)
   .catch(() => setOptimization(null))
   .finally(() => setOptimizationLoading(false))
 }, [sessionId])

 // Generates a fallback narrative from briefing data — no API required
 function buildFallbackNarrative(b: MorningBriefing): MorningNarrative {
  const k = b.kpis
  const urgency = k.order_now > 0 ? 'critical' : k.order_soon > 0 ? 'warning' : 'ok'
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
  if (k.order_now === 0 && k.order_soon === 0)
   parts.push(t('hoy.narrative_inventory_under_control'))
  if (k.avg_accuracy)
   parts.push(`${t('hoy.narrative_forecast_accuracy')}: ${(k.avg_accuracy * 100).toFixed(1)}%.`)
  return { narrative: parts.join(' '), key_points: [], urgency, fallback: true }
 }

 useEffect(() => {
  if (!briefing || !sessionId) return
  setLoadingNarrative(true)
  const timeout = setTimeout(() => {
   setNarrative(buildFallbackNarrative(briefing))
   setLoadingNarrative(false)
  }, 8000)
  getMorningNarrative(sessionId)
   .then(data => { clearTimeout(timeout); setNarrative(data) })
   .catch(() => { clearTimeout(timeout); setNarrative(buildFallbackNarrative(briefing)) })
   .finally(() => setLoadingNarrative(false))
  return () => clearTimeout(timeout)
 }, [briefing?.session_id])

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
   i.sku === sku
    ? { ...i, status: (i.status === 'approved' ? 'pending' : 'approved') as ActionStatus }
    : i,
  ))
 }

 function rejectItem(sku: string) {
  setCart(prev => prev.map(i => i.sku === sku ? { ...i, status: 'rejected' as ActionStatus } : i))
 }

 function changeQty(sku: string, qty: number) {
  setCart(prev => prev.map(i => i.sku === sku ? { ...i, qty, status: 'modified' as ActionStatus } : i))
 }

 const approved   = cart.filter(i => (i.status === 'approved' || i.status === 'modified') && i.qty > 0)
 const totalValue = approved.reduce((s, i) => s + i.qty * (i.unit_cost ?? 0), 0)

 // Feature 2.10 — margen visible en el carrito. El margen por unit lo calcula
 // el backend (unit_margin = sale_price − unit_cost, null cuando
 // either one is missing); here we only multiply by the qty the
 // user approved and sum. Lines with no price or no cost stay OUT of
 // ambos totals y se reportan aparte, para no inflar ni desinflar la cifra.
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
  r => cartSupplierNames.has(r.supplier.toLowerCase()) || r.en_ordenes_pendientes,
 )

 // ── Price breaks (3.5) ───────────────────────────────────────────────────
 // Evaluated server-side against the quantities the buyer currently has, so
 // editing a line re-judges its scale. The "conviene o no" verdict, including
 // the holding-cost and overstock guardrails, belongs to the backend.
 const approvedKey = approved.map(i => `${i.sku}:${i.qty}`).join('|')
 useEffect(() => {
  if (!sessionId || approved.length === 0) { setPriceBreaks(null); return }
  let cancelled = false
  evaluatePriceBreaks(sessionId, approved.map(i => ({ sku: i.sku, quantity: i.qty })))
   .then(r => { if (!cancelled) setPriceBreaks(r) })
   .catch(() => { if (!cancelled) setPriceBreaks(null) })
  return () => { cancelled = true }
  // approvedKey collapses the cart to a primitive so this re-runs on a real
  // quantity change, not on every re-render that rebuilds the array.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [sessionId, approvedKey])

 function applyStepUp(sku: string, quantity: number) {
  changeQty(sku, quantity)
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

 async function downloadOC() {
  const rows = [`SKU,${t('hoy.csv_col_product')},${t('hoy.csv_col_quantity')},${t('hoy.csv_col_supplier')},${t('hoy.csv_col_estimated_value')}`]
  for (const item of approved) {
   const val = item.qty * (item.unit_cost ?? 0)
   rows.push(`${item.sku},"${item.name}",${item.qty},"${item.supplier || ''}",${val}`)
  }
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = 'purchase_order.csv'
  a.click()
  URL.revokeObjectURL(url)

  if (!sessionId) return

  // Log the buyer's actual decisions (approved / modified / rejected) so we can
  // track adoption — "you followed N of M recommendations". Untouched 'pending'
  // items are excluded: the buyer never acted on them.
  const decisions = cart
   .filter(i => i.status !== 'pending')
   .filter(i => i.status === 'rejected' || i.qty > 0)
   .map(i => ({
    sku:                  i.sku,
    display_name:         i.name,
    supplier:            i.supplier,
    signal:               i.signal,
    recommended_qty: i.recommended,
    final_qty:       i.status === 'rejected' ? 0 : i.qty,
    status:               i.status as 'approved' | 'modified' | 'rejected',
    unit_cost:       i.unit_cost,
   }))

  // Feature: generate→send in one flow. Capture the logged PO so we can offer
  // "send to suppliers now" right here, instead of sending the buyer to /orders.
  try {
   const entry = await logPOGeneration(sessionId, decisions, multi ? destWarehouse || undefined : undefined)
   setGeneratedPO(entry)
   setGeneratedLines(approved)
   setSendState('idle')
   setSendResult(null)
  } catch {
   // The CSV already downloaded successfully; the inline send panel is a bonus,
   // so a logging failure here shouldn't block or alarm the buyer.
  }
 }

 async function sendGeneratedPONow() {
  if (!generatedPO) return
  setSendState('sending')
  try {
   const res = await sendPOToSuppliers(generatedPO.id)
   setSendResult(res)
  } catch (e: unknown) {
   setSendResult({
    sent: [],
    skipped: [{ supplier: null, reason: e instanceof Error ? e.message : t('roi.send_po_error') }],
   })
  } finally {
   setSendState('done')
  }
 }

 function dismissGeneratedPO() {
  setGeneratedPO(null)
  setGeneratedLines([])
  setSendState('idle')
  setSendResult(null)
 }

 // Converts a single optimizer-suggested order line straight into a logged PO,
 // without going through the manual approve/reject work-queue cart.
 async function convertOrderToPO(order: OptimizationOrder) {
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
  await logPOGeneration(sessionId, [decision], multi ? destWarehouse || undefined : undefined)
  addToast(t('hoy.optimizer_po_created'), `${order.sku} — ${order.warehouse}`, 'success')
  setOptimization(prev => prev
   ? { ...prev, orders: prev.orders.filter(o => !(o.sku === order.sku && o.warehouse === order.warehouse)) }
   : prev)
 }

 // ── Accuracy colour ───────────────────────────────────────────────────────
 function accuracyColor(v: number | null | undefined): string {
  if (v == null) return C.muted
  if (v >= 0.85) return C.green
  if (v >= 0.70) return C.amber
  return C.red
 }

 const kpis = briefing?.kpis

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
    <p style={{ fontSize: 15, color: C.text, margin: 0, maxWidth: 420 }}>{sessionsError}</p>
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

 return (
  <div style={{ background: C.bg, minHeight: '100vh', padding: '32px 40px', position: 'relative' }}>

   {/* ── Tagline ── */}
   <div style={{ marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
    <span style={{
     fontSize: 10, fontWeight: 700, color: C.indigo,
     padding: '2px 8px', borderRadius: 20,
     background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.2)',
     textTransform: 'uppercase' as const, letterSpacing: '0.07em',
    }}>
     {t('hoy.tagline_badge')}
    </span>
    <span style={{ fontSize: 11, color: C.dim }}>{t('hoy.tagline_subtitle')}</span>
   </div>

   {/* ── Header ── */}
   <div style={{
    display: 'flex', alignItems: 'flex-start',
    justifyContent: 'space-between', marginBottom: 28,
   }}>
    <div>
     <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, margin: '0 0 6px' }}>
      {t('hoy.greeting_good_morning')}{user?.full_name ? `, ${user.full_name.split(' ')[0]}` : ''}.
      {cart.length > 0 && totalPending > 0 && (
       <span style={{ fontSize: 14, fontWeight: 400, color: C.dim, marginLeft: 10 }}>
        {t('hoy.greeting_pending_actions_prefix')} {totalPending} {t('hoy.greeting_pending_actions_suffix')}
       </span>
      )}
     </h1>
     {briefing ? (
      <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
       {t('hoy.date_today_prefix')} {formatDateES(briefing.date, lang)}.{' '}
       {t('hoy.date_active_session')}: <span style={{ color: C.muted }}>{briefing.session_name}</span>
      </p>
     ) : (
      <p style={{ fontSize: 13, color: C.dim, margin: 0 }}>
       {t('hoy.date_loading')}
      </p>
     )}
    </div>

    {/* Session selector (advanced) or data freshness (normal) */}
    {advancedMode ? (
     <SessionBar
      currentSession={currentSession}
      completedSessions={completedSessions}
      sessionId={sessionId}
      onSelect={setSessionId}
      loading={sessionsLoading}
      onRefresh={() => load(sessionId)}
     />
    ) : (
     <DataFreshness currentSession={currentSession} loading={sessionsLoading} />
    )}
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
       {/* Feature 3.3 — a supplier's recent lead time drifted significantly
           off its own history. Sits above the overdue nudge because it is the
           earlier signal: it explains WHY orders are starting to run late. */}
       {leadTimeAlerts.length > 0 && (
        <div style={{ marginBottom: 20 }}>
         <SupplierLeadTimeAlertBanner alerts={leadTimeAlerts} />
        </div>
       )}

       {/* Overdue-reception alerts: expected arrival (learned supplier lead
           time) has passed with no reception recorded. */}
       {overduePOs.length > 0 && (
        <div style={{ marginBottom: 20, display: 'flex', flexDirection: 'column', gap: 8 }}>
         <div style={{
          fontSize: 11, fontWeight: 700, color: C.red,
          textTransform: 'uppercase', letterSpacing: '0.08em',
         }}>
          {t('hoy.overdue_section_title')}
         </div>
         {overduePOs.map(o => (
          <div key={`${o.po_log_id}-${o.supplier}`} style={{
           display: 'flex', alignItems: 'center', gap: 10,
           padding: '12px 16px', borderRadius: 10,
           background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.25)',
          }}>
           <AlertTriangle size={15} color={C.red} style={{ flexShrink: 0 }} />
           <span style={{ fontSize: 13, color: C.text, flex: 1 }}>
            {t('hoy.overdue_line_prefix')} <strong>{o.supplier}</strong>{' '}
            {t('hoy.overdue_line_suffix')} <strong>{o.days_overdue}</strong> {t('hoy.overdue_days_ago_suffix')}
           </span>
           <button
            onClick={() => setReceivingPO(o.po_log_id)}
            style={{
             all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
             fontSize: 12, fontWeight: 700, color: C.red, padding: '6px 12px', borderRadius: 7,
             border: `1px solid ${C.red}55`, flexShrink: 0,
            }}
           >
            <Truck size={12} /> {t('hoy.overdue_cta')}
           </button>
          </div>
         ))}
        </div>
       )}

       {/* Pending receptions banner */}
       {pendingPOs.length > 0 && (
        <Link href="/pedidos" style={{ textDecoration: 'none' }}>
         <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20,
          padding: '12px 16px', borderRadius: 10,
          background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.25)',
         }}>
          <Truck size={15} color={C.indigo} style={{ flexShrink: 0 }} />
          <span style={{ fontSize: 13, color: C.text, flex: 1 }}>
           <strong>{pendingPOs.length}</strong>{' '}
           {pendingPOs.length === 1 ? t('hoy.receptions_pending_singular') : t('hoy.receptions_pending_plural')}
          </span>
          <span style={{
           fontSize: 12, fontWeight: 700, color: C.indigo,
           display: 'inline-flex', alignItems: 'center', gap: 4,
          }}>
           {t('hoy.receptions_cta')} <ArrowRight size={12} />
          </span>
         </div>
        </Link>
       )}

       {/* KPI row */}
       <div style={{ display: 'flex', gap: 12, marginBottom: 28, flexWrap: 'wrap' }}>
        <KpiCard label={t('hoy.kpi_total_skus')}        value={String(kpis!.total_skus)}       color={C.text} />
        <KpiCard label={t('hoy.kpi_risk_today')}        value={String(kpis!.order_now)}         color={kpis!.order_now > 0 ? C.red : C.text} />
        <KpiCard label={t('hoy.kpi_this_week')}         value={String(kpis!.order_soon)}      color={kpis!.order_soon > 0 ? C.amber : C.text} />
        <KpiCard label={t('hoy.kpi_avg_accuracy')}      value={fmtPct(kpis!.avg_accuracy)}     color={accuracyColor(kpis!.avg_accuracy)}
         help={t('hoy.kpi_avg_accuracy_help')} />
        <KpiCard label={t('hoy.kpi_inventory_value')}   value={fmtM(kpis!.total_inventory_value)} color={C.text} />
       </div>

       {/* Work queue */}
       <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

        {/* AI Narrative */}
        {(narrative || loadingNarrative) && (
         <div style={{ marginBottom: 20 }}>
          <NarrativeCard
           title={t('hoy.narrative_card_title')}
           narrative={narrative?.narrative ?? null}
           keyPoints={narrative?.key_points ?? []}
           urgency={narrative?.urgency ?? 'ok'}
           loading={loadingNarrative}
           fallback={narrative?.fallback ?? false}
           analytistLink="/analyst"
           onRefresh={() => {
            if (!sessionId) return
            setLoadingNarrative(true)
            getMorningNarrative(sessionId)
             .then(setNarrative).catch(() => {}).finally(() => setLoadingNarrative(false))
           }}
          />
         </div>
        )}

        {/* Transfer suggestions (feature 5.4) — stock exists, wrong warehouse.
            Rendered before the urgent purchases: moving boxes is free. The
            suggestions arrive with the morning briefing (no extra request);
            the component renders null when there is nothing to transfer. */}
        <TransferSuggestions suggestions={briefing?.transfer_suggestions ?? []} />

        {/* URGENTE section */}
        {cart.filter(i => i.signal === 'PEDIR_YA').length > 0 && (
         <div style={{ marginBottom: 24 }}>
          <div style={{
           fontSize: 11, fontWeight: 700, color: 'var(--signal-order-now-fg)',
           textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
           display: 'flex', alignItems: 'center', gap: 6,
          }}>
           <AlertTriangle size={12} aria-hidden="true" />
           {t('hoy.section_urgent')}
          </div>
          {cart.filter(i => i.signal === 'PEDIR_YA').map(item => (
           <ActionCard
            key={item.sku}
            item={item}
            onApprove={() => approveItem(item.sku)}
            onReject={() => rejectItem(item.sku)}
            onChangeQty={qty => changeQty(item.sku, qty)}
           />
          ))}
         </div>
        )}

        {/* ESTA SEMANA section */}
        {cart.filter(i => i.signal === 'PEDIR_PRONTO').length > 0 && (
         <div style={{ marginBottom: 24 }}>
          <div style={{
           fontSize: 11, fontWeight: 700, color: 'var(--signal-order-soon-fg)',
           textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
           display: 'flex', alignItems: 'center', gap: 6,
          }}>
           <Clock size={12} aria-hidden="true" />
           {t('hoy.section_this_week')}
          </div>
          {cart.filter(i => i.signal === 'PEDIR_PRONTO').map(item => (
           <ActionCard
            key={item.sku}
            item={item}
            onApprove={() => approveItem(item.sku)}
            onReject={() => rejectItem(item.sku)}
            onChangeQty={qty => changeQty(item.sku, qty)}
           />
          ))}
         </div>
        )}

        {/* All-rejected empty state */}
        {cart.length > 0 && cart.every(i => i.status === 'rejected') && (
         <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--muted)' }}>
          <div style={{ fontSize: 15, marginBottom: 8 }}>{t('hoy.no_pending_actions')}</div>
          <div style={{ fontSize: 13, color: 'var(--dim)' }}>{t('hoy.inventory_under_control')}</div>
         </div>
        )}

        {/* No risks / warnings at all */}
        {cart.length === 0 && (
         <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--muted)' }}>
          <div style={{ fontSize: 15, marginBottom: 8 }}>{t('hoy.no_pending_actions')}</div>
          <div style={{ fontSize: 13, color: 'var(--dim)' }}>{t('hoy.inventory_under_control')}</div>
         </div>
        )}

        {/* Feature 2.5 — suppliers the send path would silently skip. */}
        {relevantContactHealth.length > 0 && (
         <div style={{ marginBottom: 10 }}>
          <SupplierContactHealthBanner rows={relevantContactHealth} />
         </div>
        )}

        {/* Price breaks (3.5) and cash calendar (3.6) — both judged against
            the cart as it stands, so they sit right above it. */}
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

        {/* Sticky cart */}
        {approved.length > 0 && (
         <div style={{
          position: 'sticky', bottom: 16,
          background: 'var(--surface)', border: '1px solid rgba(34,197,94,0.4)',
          borderRadius: 12, padding: '14px 20px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', gap: 16,
          marginTop: 8,
         }}>
          <div style={{ flex: 1 }}>
           <div style={{ fontSize: 13, fontWeight: 700, color: '#22c55e' }}>
            {approved.length} {t('hoy.cart_products_approved')}
           </div>
           <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>
            {approved.map(i => `${i.name}: ${i.qty.toLocaleString('es')} ${t('hoy.cart_unit_abbrev')}`).join(' · ')}
            {totalValue > 0 && ` · ${t('hoy.cart_total_label')}: ${fmtMoney(totalValue)}`}
           </div>
           {/* Margen visible en el carrito (2.10) */}
           {salesProtected > 0 && (
            <div style={{ fontSize: 12, color: C.green, marginTop: 4, fontWeight: 600 }}>
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
            <div style={{ fontSize: 11, color: C.amber, marginTop: 3 }}>
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
           <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--dim)' }}>
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
          <button onClick={downloadOC} style={{
           all: 'unset', cursor: 'pointer', padding: '10px 20px', borderRadius: 8,
           background: '#22c55e', color: '#fff', fontSize: 14, fontWeight: 700,
           display: 'flex', alignItems: 'center', gap: 8,
          }}>
           {t('hoy.btn_download_po')}
          </button>
         </div>
        )}

        {/* Generate→send in one flow: right after a PO is logged, offer to
            send it to its suppliers without navigating away to /orders. */}
        {generatedPO && (
         <div style={{
          marginTop: 12, background: 'var(--surface)', border: '1px solid rgba(129,140,248,0.4)',
          borderRadius: 12, padding: '16px 20px',
         }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
           <Send size={14} color={C.indigo} />
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
             <span style={{ fontWeight: 700, color: supplier ? C.text : C.amber, flexShrink: 0 }}>
              {supplier || t('hoy.generate_send_no_supplier')}
             </span>
             <span style={{ color: 'var(--dim)' }}>
              {lines.map(l => `${l.name} (${l.qty.toLocaleString('es')} ${t('hoy.generate_send_units_abbrev')})`).join(' · ')}
             </span>
            </div>
           ))}
          </div>

          {sendState === 'done' && sendResult ? (
           <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {sendResult.sent.map(s => (
             <div key={s.supplier} style={{ fontSize: 12, color: C.green }}>
              ✓ {s.supplier}{s.email ? ' · email' : ''}{s.whatsapp ? ' · WhatsApp' : ''}
             </div>
            ))}
            {sendResult.skipped.map((s, idx) => (
             <div key={`${s.supplier}-${idx}`} style={{ fontSize: 12, color: C.amber }}>
              ⚠ {s.supplier || '—'}: {s.reason}
             </div>
            ))}
           </div>
          ) : (
           <div style={{ display: 'flex', gap: 10 }}>
            <button
             onClick={sendGeneratedPONow}
             disabled={sendState === 'sending'}
             style={{
              all: 'unset', cursor: sendState === 'sending' ? 'not-allowed' : 'pointer',
              padding: '9px 18px', borderRadius: 8, background: C.indigo, color: '#fff',
              fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6,
              opacity: sendState === 'sending' ? 0.7 : 1,
             }}
            >
             <Send size={13} />
             {sendState === 'sending' ? t('roi.send_po_sending') : t('hoy.generate_send_btn')}
            </button>
            <Link href="/pedidos" style={{
             fontSize: 13, color: 'var(--dim)', textDecoration: 'none',
             display: 'flex', alignItems: 'center', padding: '9px 4px',
            }}>
             {t('hoy.generate_send_go_orders')}
            </Link>
           </div>
          )}
         </div>
        )}
       </div>

       {/* Compras y transferencias suggested — optimizer plan (MW-3) */}
       {optimizationLoading && !optimization && (
        <p style={{ fontSize: 12, color: C.dim, marginTop: 24 }}>
         {t('hoy.optimizer_loading')}
        </p>
       )}
       {optimization && (optimization.orders.length > 0 || optimization.transfers.length > 0) && (
        <section style={{ marginTop: 32, marginBottom: 28 }}>
         <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
          {t('hoy.optimizer_title')}
         </h2>
         <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 14 }}>
          {t('hoy.optimizer_subtitle').replace('{horizon}', String(optimization.horizon_days))}
         </p>

         {optimization.orders.length > 0 && (
          <div style={{ marginBottom: 16 }}>
           <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            {t('hoy.optimizer_orders_title')}
           </h3>
           {optimization.orders.map(order => (
            <div key={`${order.sku}-${order.warehouse}`} style={{
             display: 'flex', alignItems: 'center', justifyContent: 'space-between',
             padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 6,
            }}>
             <span style={{ fontSize: 13 }}>
              {order.sku} — {order.warehouse}: <strong>{order.qty}</strong>
             </span>
             <button onClick={() => convertOrderToPO(order)} style={{
              all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              color: 'var(--accent)', padding: '4px 10px', borderRadius: 6,
             }}>
              {t('hoy.optimizer_convert_to_po')}
             </button>
            </div>
           ))}
          </div>
         )}

         {optimization.transfers.length > 0 && (
          <div>
           <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            {t('hoy.optimizer_transfers_title')}
           </h3>
           {optimization.transfers.map(tr => (
            <div key={`${tr.sku}-${tr.from_warehouse}-${tr.to_warehouse}`} style={{
             fontSize: 13, padding: '10px 12px', border: '1px solid var(--border)',
             borderRadius: 8, marginBottom: 6,
            }}>
             {t('hoy.optimizer_transfer_line')
              .replace('{qty}', String(tr.qty))
              .replace('{sku}', tr.sku)
              .replace('{from}', tr.from_warehouse)
              .replace('{to}', tr.to_warehouse)}
            </div>
           ))}
          </div>
         )}
        </section>
       )}

       {/* Anticípate — proactive future demand peaks */}
       {(briefing.demand_spikes?.length ?? 0) > 0 && (
        <section style={{ marginTop: 32, marginBottom: 28 }}>
         <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Zap size={16} color={C.amber} />
          <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: 0 }}>
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
        <section style={{ marginTop: 32, marginBottom: 28 }}>
         <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_demand_changes')}
         </h2>
         <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 10, overflow: 'hidden',
         }}>
          {briefing.demand_changes.map((item, idx) => {
           const pct = item.demand_trend_pct
           const up  = pct > 0
           const pctColor = up ? C.green : C.red
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
             {up ? <TrendingUp size={16} color={C.green} /> : <TrendingDown size={16} color={C.red} />}
             <span style={{ fontSize: 13, fontWeight: 700, color: pctColor, minWidth: 52 }}>
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

       {briefing.recommendations.length > 0 && (
        <section style={{ marginBottom: 28 }}>
         <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_system_recommendations')}
         </h2>
         <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {briefing.recommendations.slice(0, 8).map((rec, idx) => (
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
             <span style={{ fontSize: 13, color: C.text, lineHeight: 1.5 }}>{rec.text}</span>
            </div>
            <p style={{ fontSize: 12, color: C.muted, margin: 0, paddingLeft: 26 }}>
             {t('hoy.suggested_action_label')}: {rec.action}
            </p>
           </div>
          ))}
         </div>
        </section>
       )}

       {briefing.overstocked.length > 0 && kpis!.capital_in_overstock > 0 && (
        <section style={{ marginBottom: 28 }}>
         <h2 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: '0 0 14px' }}>
          {t('hoy.section_capital_opportunities')}
         </h2>
         <div style={{
          background: 'rgba(59,130,246,0.05)', border: `1px solid ${C.blue}33`,
          borderRadius: 10, padding: '16px 20px',
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
               <span style={{ fontSize: 13, fontWeight: 600, color: C.blue }}>
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

       {/* Footer */}
       <div style={{
        marginTop: 24, paddingTop: 20, borderTop: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 12,
       }}>
        <div style={{ fontSize: 12, color: C.dim }}>
         {loadedAt && <>{t('hoy.footer_last_update')}: {timeSince(loadedAt, t)}</>}
         {briefing.session_name && <> &nbsp;|&nbsp; {t('hoy.footer_session')}: {briefing.session_name}</>}
         {kpis?.avg_accuracy != null && <> &nbsp;|&nbsp; {t('hoy.footer_model_accuracy')}: {fmtPct(kpis.avg_accuracy)}</>}
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

       {/* Inventory link */}
       <div style={{ marginTop: 8 }}>
        <Link
         href="/inventory"
         style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          fontSize: 13, color: C.indigo, textDecoration: 'none',
         }}
        >
         {t('hoy.link_view_all_inventory')} <ArrowRight size={13} />
        </Link>
       </div>
      </>
     )}
    </>
   )}

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

// ── KPI card helper ───────────────────────────────────────────────────────────
function KpiCard({ label, value, color, help }: { label: string; value: string; color: string; help?: string }) {
 return (
  <div style={{
   flex: '1 1 160px',
   background: 'var(--surface)',
   border: '1px solid var(--border)',
   borderRadius: 10,
   padding: '14px 18px',
  }}>
   <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 6, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 4 }}>
    {label}
    {help && <HelpTip text={help} size={13} />}
   </div>
   <div style={{ fontSize: 22, fontWeight: 700, color }}>
    {value}
   </div>
  </div>
 )
}

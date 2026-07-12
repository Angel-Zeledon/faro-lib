'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import {
 AlertTriangle, Clock, TrendingUp, TrendingDown, Archive,
 RefreshCw, ArrowRight, BarChart2, Package, Zap, Truck,
} from 'lucide-react'
import { getMorningBriefing, getMorningNarrative, getPOHistory } from '@/lib/api'
import type { MorningBriefing, BriefingRecommendation, MorningNarrative, DemandSpike, POLogEntry } from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import { getUser } from '@/lib/auth'
import Spinner from '@/components/ui/Spinner'
import NarrativeCard from '@/components/ui/NarrativeCard'
import HelpTip from '@/components/ui/HelpTip'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import { useLanguage } from '@/contexts/LanguageContext'

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
function fmtM(n: number) {
 if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
 if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
 return `$${n.toFixed(0)}`
}

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
 sku:         string
 name:        string
 proveedor:   string | null
 qty:         number
 recommended: number   // original quantity Faro suggested (immutable)
 unit_cost:   number | null
 signal:      string
 dias:        number | null
 lead_time:   number
 reason:      string
 status:      ActionStatus
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

 // Keep qtyInput in sync when item.qty changes externally (e.g. after reset)
 useEffect(() => {
  setQtyInput(String(item.qty))
 }, [item.qty])

 const isUrgent  = item.signal === 'PEDIR_YA'
 const accent    = isUrgent ? '#ef4444' : '#f59e0b'
 const isApproved = item.status === 'approved' || item.status === 'modified'
 const isRejected = item.status === 'rejected'

 const estimatedValue = item.qty * (item.unit_cost ?? 0)
 const canOrder = item.qty > 0

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
     {item.proveedor && (
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{item.proveedor}</div>
     )}
    </div>
   </div>

   {/* Quantity + Value + Actions */}
   {!isRejected && (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
     <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('hoy.label_order_qty')}</span>
      {editing ? (
       <input
        type="number" min={0} value={qtyInput}
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
        ≈ ${(estimatedValue / 1_000_000).toFixed(1)}M
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
      {' '}{t('hoy.spike_supplier_lead_prefix')} {s.lead_time_dias} {t('hoy.spike_days_unit')}.
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

 for (const risk of (b.risks ?? [])) {
  const d = risk.dias_cobertura != null ? Math.round(risk.dias_cobertura) : null
  const reason = d != null
   ? `${t('hoy.reason_stock_left_prefix')} ${d} ${t('hoy.reason_days_unit')} ${t('hoy.reason_stock_left_suffix')} ${risk.lead_time_dias} ${t('hoy.reason_days_unit')}`
   : `${t('hoy.reason_immediate_risk')} — ${t('hoy.reason_lead_time_label')} ${risk.lead_time_dias} ${t('hoy.reason_days_unit')}`
  items.push({
   sku:         risk.sku,
   name:        risk.display_name || risk.sku,
   proveedor:   risk.proveedor || null,
   qty:         risk.cantidad_recomendada ?? 0,
   recommended: risk.cantidad_recomendada ?? 0,
   unit_cost:   risk.costo_unitario ?? null,
   signal:      'PEDIR_YA',
   dias:        risk.dias_cobertura ?? null,
   lead_time:   risk.lead_time_dias,
   reason,
   status:      'pending',
  })
 }

 for (const w of (b.warnings ?? [])) {
  const d = w.dias_cobertura != null ? Math.round(w.dias_cobertura) : null
  items.push({
   sku:         w.sku,
   name:        w.display_name || w.sku,
   proveedor:   w.proveedor || null,
   qty:         w.cantidad_recomendada ?? 0,
   recommended: w.cantidad_recomendada ?? 0,
   unit_cost:   w.costo_unitario ?? null,
   signal:      'PEDIR_PRONTO',
   dias:        w.dias_cobertura ?? null,
   lead_time:   w.lead_time_dias,
   reason:      `${d != null ? d + ' ' + t('hoy.reason_days_coverage') : t('hoy.reason_next_order_recommended')} — ${t('hoy.reason_order_this_week')}`,
   status:      'pending',
  })
 }

 return items
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function HoyPage() {
 const { t, lang } = useLanguage()
 const { sessionId, setSessionId, currentSession, completedSessions, loading: sessionsLoading, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 const [briefing, setBriefing]             = useState<MorningBriefing | null>(null)
 const [loading, setLoading]               = useState(false)
 const [error, setError]                   = useState<string | null>(null)
 const [loadedAt, setLoadedAt]             = useState<Date | null>(null)
 const [narrative, setNarrative]           = useState<MorningNarrative | null>(null)
 const [loadingNarrative, setLoadingNarrative] = useState(false)

 // Work-queue cart state
 const [cart, setCart] = useState<ActionItem[]>([])

 // Orders awaiting reception
 const [pendingPOs, setPendingPOs] = useState<POLogEntry[]>([])

 const user    = getUser()
 const { profile } = useBusinessProfile()

 // Load briefing when session changes
 const load = useCallback(async (sid: string) => {
  if (!sid) return
  setLoading(true)
  setError(null)
  try {
   const data = await getMorningBriefing(sid)
   setBriefing(data)
   setLoadedAt(new Date())
  } catch (e: unknown) {
   setError(e instanceof Error ? e.message : t('hoy.error_loading_briefing'))
  } finally {
   setLoading(false)
  }
 }, [])

 useEffect(() => {
  if (sessionId) load(sessionId)
 }, [sessionId, load])

 // Generates a fallback narrative from briefing data — no API required
 function buildFallbackNarrative(b: MorningBriefing): MorningNarrative {
  const k = b.kpis
  const urgency = k.pedir_ya > 0 ? 'critical' : k.pedir_pronto > 0 ? 'warning' : 'ok'
  const parts: string[] = []
  if (k.pedir_ya > 0) {
   const names = (b.risks ?? []).slice(0, 3).map(r => r.display_name || r.sku).join(', ')
   parts.push(`${k.pedir_ya} ${t('hoy.narrative_products_immediate_risk')}: ${names}.`)
  }
  if (k.pedir_pronto > 0)
   parts.push(`${k.pedir_pronto} ${t('hoy.narrative_products_need_order_week')}`)
  if (k.sobrestock > 0 && k.capital_in_overstock > 0)
   parts.push(`$${(k.capital_in_overstock / 1_000_000).toFixed(1)}M ${t('hoy.narrative_capital_tied_overstock')}`)
  if (k.pedir_ya === 0 && k.pedir_pronto === 0)
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
  getMorningNarrative(sessionId, profile || 'distributor')
   .then(data => { clearTimeout(timeout); setNarrative(data) })
   .catch(() => { clearTimeout(timeout); setNarrative(buildFallbackNarrative(briefing)) })
   .finally(() => setLoadingNarrative(false))
  return () => clearTimeout(timeout)
 }, [briefing?.session_id, profile])

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

 function downloadOC() {
  const rows = [`SKU,${t('hoy.csv_col_product')},${t('hoy.csv_col_quantity')},${t('hoy.csv_col_supplier')},${t('hoy.csv_col_estimated_value')}`]
  for (const item of approved) {
   const val = item.qty * (item.unit_cost ?? 0)
   rows.push(`${item.sku},"${item.name}",${item.qty},"${item.proveedor || ''}",${val}`)
  }
  const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = 'orden_de_compra.csv'
  a.click()
  URL.revokeObjectURL(url)

  if (sessionId) {
   // Log the buyer's actual decisions (approved / modified / rejected) so we can
   // track adoption — "you followed N of M recommendations". Untouched 'pending'
   // items are excluded: the buyer never acted on them.
   const decisions = cart
    .filter(i => i.status !== 'pending')
    .filter(i => i.status === 'rejected' || i.qty > 0)
    .map(i => ({
     sku:                  i.sku,
     display_name:         i.name,
     proveedor:            i.proveedor,
     signal:               i.signal,
     cantidad_recomendada: i.recommended,
     cantidad_final:       i.status === 'rejected' ? 0 : i.qty,
     status:               i.status as 'approved' | 'modified' | 'rejected',
     costo_unitario:       i.unit_cost,
    }))
   import('@/lib/api').then(({ logPOGeneration }) => {
    logPOGeneration(sessionId, decisions).catch(() => {})
   })
  }
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

 // ── No session state ──────────────────────────────────────────────────────
 if (!loading && !sessionsLoading && !sessionId && completedSessions.length === 0) {
  return (
   <div style={{
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', flex: 1, gap: 20, padding: 40,
    color: C.muted, textAlign: 'center',
   }}>
    <BarChart2 size={40} color={C.dim} style={{ opacity: 0.4 }} />
    <p style={{ fontSize: 16, color: C.text, margin: 0 }}>
     {t('hoy.no_session_message')}
    </p>
    <div style={{ display: 'flex', gap: 12 }}>
     <Link
      href="/quick-start"
      style={{
       padding: '10px 20px', background: C.indigo, color: '#fff',
       borderRadius: 8, textDecoration: 'none', fontSize: 14, fontWeight: 600,
      }}
     >
      {t('hoy.link_go_quick_start')}
     </Link>
     <Link
      href="/inventory"
      style={{
       padding: '10px 20px', background: C.surface, color: C.text,
       border: `1px solid ${C.border}`, borderRadius: 8,
       textDecoration: 'none', fontSize: 14, fontWeight: 600,
      }}
     >
      {t('hoy.link_go_inventory')}
     </Link>
    </div>
   </div>
  )
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

    {/* Session selector */}
    <SessionBar
     currentSession={currentSession}
     completedSessions={completedSessions}
     sessionId={sessionId}
     onSelect={setSessionId}
     loading={sessionsLoading}
     onRefresh={() => load(sessionId)}
    />
   </div>

   {/* ── Loading state ── */}
   {loading && (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
     <Spinner size={32} color={C.indigo} />
    </div>
   )}

   {/* ── Error state ── */}
   {!loading && error && (
    <div style={{
     background: 'rgba(239,68,68,0.08)', border: `1px solid ${C.red}33`,
     borderRadius: 10, padding: '16px 20px', marginBottom: 24,
     display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    }}>
     <span style={{ fontSize: 14, color: C.text }}>{error}</span>
     <button
      onClick={() => load(sessionId)}
      style={{
       display: 'flex', alignItems: 'center', gap: 6,
       padding: '6px 14px', background: C.surface,
       border: `1px solid ${C.border}`, borderRadius: 6,
       cursor: 'pointer', fontSize: 13, color: C.text,
      }}
     >
      <RefreshCw size={13} /> {t('hoy.btn_retry')}
     </button>
    </div>
   )}

   {/* ── Main content ── */}
   {!loading && briefing && (
    <>
     {/* No-data empty state */}
     {!briefing.has_data ? (
      <div style={{
       display: 'flex', flexDirection: 'column', alignItems: 'center',
       justifyContent: 'center', padding: '60px 0', gap: 16, textAlign: 'center',
      }}>
       <Package size={36} color={C.dim} style={{ opacity: 0.4 }} />
       <p style={{ fontSize: 16, color: C.text, margin: 0 }}>
        {t('hoy.no_inventory_data')}
       </p>
       <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>
        {t('hoy.no_inventory_data_hint')}
       </p>
       <Link
        href="/inventory"
        style={{
         padding: '10px 20px', background: C.indigo, color: '#fff',
         borderRadius: 8, textDecoration: 'none', fontSize: 14, fontWeight: 600,
        }}
       >
        {t('hoy.link_go_inventory')}
       </Link>
      </div>
     ) : (
      <>
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
        <KpiCard label={t('hoy.kpi_risk_today')}        value={String(kpis!.pedir_ya)}         color={kpis!.pedir_ya > 0 ? C.red : C.text} />
        <KpiCard label={t('hoy.kpi_this_week')}         value={String(kpis!.pedir_pronto)}      color={kpis!.pedir_pronto > 0 ? C.amber : C.text} />
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
            if (!sessionId || !profile) return
            setLoadingNarrative(true)
            getMorningNarrative(sessionId, profile || 'distributor')
             .then(setNarrative).catch(() => {}).finally(() => setLoadingNarrative(false))
           }}
          />
         </div>
        )}

        {/* URGENTE section */}
        {cart.filter(i => i.signal === 'PEDIR_YA').length > 0 && (
         <div style={{ marginBottom: 24 }}>
          <div style={{
           fontSize: 11, fontWeight: 700, color: '#ef4444',
           textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
          }}>
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
           fontSize: 11, fontWeight: 700, color: '#f59e0b',
           textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
          }}>
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
            {totalValue > 0 && ` · ${t('hoy.cart_total_label')}: $${(totalValue / 1_000_000).toFixed(1)}M`}
           </div>
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
          <button onClick={downloadOC} style={{
           all: 'unset', cursor: 'pointer', padding: '10px 20px', borderRadius: 8,
           background: '#22c55e', color: '#fff', fontSize: 14, fontWeight: 700,
           display: 'flex', alignItems: 'center', gap: 8,
          }}>
           {t('hoy.btn_download_po')}
          </button>
         </div>
        )}
       </div>

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
              {item.dias_cobertura != null && (
               <span style={{ fontSize: 12, color: C.dim }}>
                {Math.round(item.dias_cobertura)} {t('hoy.reason_days_coverage')}
               </span>
              )}
              {item.valor_inventario != null && (
               <span style={{ fontSize: 13, fontWeight: 600, color: C.blue }}>
                {fmtM(item.valor_inventario)}
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

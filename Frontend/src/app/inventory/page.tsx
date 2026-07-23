'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Link from 'next/link'
import {
 getInventoryStatus, upsertInventoryStock, deleteInventoryStock,
 importInventoryCSV, exportInventoryPO, downloadInventoryPDF,
 listInventoryEvents, createInventoryEvent, updateInventoryEvent, deleteInventoryEvent,
 listSuppliers, getDeadStock, simulateEvent, logPOGeneration, downloadInventoryTemplate,
 createShrinkage,
 getCalendarCatalog, seedCalendarCatalog, toggleCalendarEntry,
 listEventMultipliers, setEventMultiplier, deleteEventMultiplier,
} from '@/lib/api'
import type {
 InventoryStatusItem, InventorySignal,
 InventoryCalcExplanation, InventoryEvent, Supplier, DeadStockResponse, ExcludedSku,
 EventSimulationResult, POLineDecision, ShrinkageReason, CalendarCatalogEntry, CoverageUnit,
 EventMultiplier,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import { useWarehouses, WarehouseSelector } from '@/components/inventory/WarehouseControls'
import { WarehouseStatusTable } from '@/components/inventory/WarehouseStatusTable'
import SessionBar from '@/components/ui/SessionBar'
import DataFreshness from '@/components/ui/DataFreshness'
import Spinner from '@/components/ui/Spinner'
import { EmptyState, ErrorState, InlineError, LoadingState, SkeletonCards, SkeletonTable } from '@/components/ui/States'
import HelpTip from '@/components/ui/HelpTip'
import SharedSignalBadge, { signalColor } from '@/components/ui/SignalBadge'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import { formatMoney, formatMoneyCompact } from '@/lib/currency'
import { coverageUnitShort } from '@/lib/period'
import {
 ShoppingCart, AlertTriangle, CheckCircle2, TrendingDown, TrendingUp,
 ChevronDown, ChevronRight, RefreshCw, Upload, Download, Edit2, Trash2,
 X, Save, Package, Info, Layers, List, FileText, Calendar, Plus, PencilLine, Truck, Sliders,
 Zap, PackageMinus, Search, PackagePlus,
} from 'lucide-react'

// Maps the active UI language to a concrete BCP-47 locale for date formatting,
// so dates follow the language toggle instead of always rendering in Spanish.
function localeFor(lang: string): string {
 return lang === 'en' ? 'en-US' : 'es-CR'
}

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#ef4444', amber: '#f59e0b', green: '#22c55e', blue: '#3b82f6', indigo: '#818cf8',
}

// ── Signal config ─────────────────────────────────────────────────────────────
// The signal presentation (icon + label + accessible colour) lives in
// components/ui/SignalBadge — antes estaba duplicada aquí y en
// SkuSearchOverlay, con colores que fallaban contraste en tema claro.
function SignalBadge({ s }: { s: InventorySignal }) {
 return <SharedSignalBadge signal={s} />
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
 const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

 return (
 <span
 style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
 onMouseEnter={e => {
 const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
 setPos({ x: rect.left + rect.width / 2, y: rect.top })
 }}
 onMouseLeave={() => setPos(null)}
 >
 {children}
 {pos && (
 <span style={{
 position: 'fixed',
 left: pos.x, top: pos.y - 8,
 transform: 'translate(-50%, -100%)',
 background: '#1e293b', color: '#e2e8f0', fontSize: 11, lineHeight: 1.55,
 padding: '8px 11px', borderRadius: 7, width: 230, zIndex: 9999,
 border: '1px solid #334155', boxShadow: '0 6px 18px rgba(0,0,0,0.5)',
 pointerEvents: 'none', whiteSpace: 'normal',
 }}>
 {text}
 <span style={{
 position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
 borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
 borderTop: '5px solid #1e293b',
 }} />
 </span>
 )}
 </span>
 )
}

function ThTip({ label, tip }: { label: string; tip: string }) {
 return (
 <th style={{ padding: '9px 12px', textAlign: 'left', whiteSpace: 'nowrap', color: C.dim, fontWeight: 600, fontSize: 10, borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>
 <Tooltip text={tip}><span>{label}</span><Info size={9} color={C.dim} style={{ opacity: 0.5, flexShrink: 0 }} /></Tooltip>
 </th>
 )
}

// ── ABC-XYZ badge ─────────────────────────────────────────────────────────────
const ABC_COLOR: Record<string, string> = { A: '#22c55e', B: '#f59e0b', C: '#64748b', '?': '#334155' }
const XYZ_COLOR: Record<string, string> = { X: '#818cf8', Y: '#f59e0b', Z: '#ef4444', '?': '#334155' }
function AbcXyzBadge({ value }: { value: string }) {
 if (!value || value === '?') return <span style={{ color: C.dim }}>—</span>
 const abc = value[0], xyz = value[1] || ''
 return (
 <span style={{ display: 'inline-flex', gap: 2, fontFamily: 'monospace', fontWeight: 700, fontSize: 11 }}>
 <span style={{ color: ABC_COLOR[abc] ?? C.dim }}>{abc}</span>
 <span style={{ color: XYZ_COLOR[xyz] ?? C.dim }}>{xyz}</span>
 </span>
 )
}

// ── Sparkline ─────────────────────────────────────────────────────────────────
function Sparkline({ data }: { data: { stock: number }[] }) {
 if (data.length < 2) return <span style={{ color: C.dim, fontSize: 10 }}>—</span>
 const W = 60, H = 20
 const vals = data.map(d => d.stock)
 const lo = Math.min(...vals), hi = Math.max(...vals), rng = hi - lo || 1
 const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * W},${H - 2 - ((v - lo) / rng) * (H - 4)}`).join(' ')
 const trend = vals[vals.length - 1] - vals[0]
 const tColor = trend < -rng * 0.1 ? C.red : trend > rng * 0.1 ? C.green : C.indigo
 return (
 <svg width={W} height={H} style={{ display: 'block' }}>
 <polyline points={pts} fill="none" stroke={tColor} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// ── Why is this being recommended? ──────────────────────────────────────────
function CalcExplainer({ exp, moq }: { exp: InventoryCalcExplanation; moq: number }) {
 const { t } = useLanguage()

 if (exp.suficiente) {
 return (
 <div style={{ fontSize: 11, color: 'var(--dim)', padding: '4px 0' }}>
 {t('inventory.calc_enough_stock')}
 </div>
 )
 }

 const unitWord = t('inventory.calc_unit_units')
 // Label where the lead time came from, so the buyer knows whether the number
 // is learned from real receptions or the one typed on the SKU card.
 const leadOrigin = exp.lead_time_source === 'learned'
 ? t('inventory.lead_origin_learned')
 : exp.lead_time_source === 'configured'
 ? t('inventory.lead_origin_configured')
 : null
 const steps = [
 { label: t('inventory.calc_step_avg_daily_sales'), value: `${exp.daily_demand!.toFixed(1)} ${t('inventory.calc_unit_per_day')}`, op: null },
 { label: `× ${t('inventory.calc_step_lead_days')} (${exp.lead_time_days}d${leadOrigin ? ` · ${leadOrigin}` : ''})`, value: `= ${exp.lead_time_demand!.toFixed(0)} ${unitWord}`, op: '×' },
 { label: `+ ${t('inventory.calc_step_safety_stock')}`, value: `+ ${exp.safety_stock!.toFixed(0)} ${unitWord}`, op: '+' },
 { label: `− ${t('inventory.calc_step_current_stock')}`, value: `− ${exp.current_stock!.toFixed(0)} ${unitWord}`, op: '−' },
 { label: `= ${t('inventory.calc_step_before_rounding')}`, value: `${exp.antes_moq!.toFixed(0)} ${unitWord}`, op: '=' },
 ...(moq > 1
 ? [{ label: `↑ ${t('inventory.calc_step_rounded_moq')} (${moq})`, value: `→ ${exp.final_qty!.toFixed(0)} ${unitWord}`, op: '↑' }]
 : []),
 ]

 return (
 <div style={{
 background: 'rgba(129,140,248,0.04)', border: '1px solid rgba(129,140,248,0.15)',
 borderRadius: 8, padding: '12px 16px', marginTop: 2,
 }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: C.indigo, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
 {t('inventory.calc_title')}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
 {steps.map(({ label, value }, i) => (
 <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
 <span style={{ fontSize: 12, color: C.muted }}>{label}</span>
 <span style={{
 fontSize: 12, fontFamily: 'monospace', fontWeight: i === steps.length - 1 ? 700 : 500,
 color: i === steps.length - 1 ? C.green : C.text,
 }}>{value}</span>
 </div>
 ))}
 </div>
 <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid rgba(129,140,248,0.15)`, fontSize: 11, color: C.dim }}>
 {t('inventory.calc_footer_safety_stock')}
 </div>
 </div>
 )
}

// ── Plain-language situation ────────────────────────────────────────────────
function ContextMessage({ summary }: { summary: Record<string, number> }) {
 const { t } = useLanguage()
 const lines: { text: string; color: string }[] = []
 if (summary.order_now > 0)
 lines.push({ text: `${summary.order_now} ${t('inventory.ctx_order_now_suffix')}`, color: '#ef4444' })
 if (summary.order_soon > 0)
 lines.push({ text: `${summary.order_soon} ${t('inventory.ctx_order_soon_suffix')}`, color: '#f59e0b' })
 if (summary.overstock > 0)
 lines.push({ text: `${summary.overstock} ${t('inventory.ctx_overstock_suffix')}`, color: '#3b82f6' })
 if (!lines.length && summary.ok > 0)
 lines.push({ text: t('inventory.ctx_all_covered'), color: '#22c55e' })
 if (!lines.length) return null
 return (
 <div style={{ padding: '10px 16px', borderRadius: 9, background: C.surface, border: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 4 }}>
 {lines.map(({ text, color }) => <span key={text} style={{ fontSize: 12, color }}>{text}</span>)}
 </div>
 )
}

// ── KPI card ──────────────────────────────────────────────────────────────────
function KPICard({ label, value, color, sub, onClick, active }: {
 label: string; value: React.ReactNode; color: string; sub?: string; onClick?: () => void; active?: boolean
}) {
 return (
 <div onClick={onClick} style={{ background: C.surface, border: `1px solid ${active ? color + '60' : C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${color}`, cursor: onClick ? 'pointer' : 'default', transition: 'border-color 0.15s' }}>
 <div style={{ fontSize: 22, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{label}</div>
 {sub && <div style={{ fontSize: 10, color: C.dim, marginTop: 2, opacity: 0.7 }}>{sub}</div>}
 </div>
 )
}

// ── Multiplier: explanation and per-product editing (feature 3.4) ───────────
// An event multiplier cannot be a number with no provenance: if Faro
// says "order 3x of this on Black Friday", the buyer has to be able to see
// where that 3x comes from and change it. And it is not a single number —
// electronics and milk behave differently — so it resolves SKU > category > event.

function MultiplierChip({ value, origin }: { value: number; origin: string }) {
 const { t } = useLanguage()
 const label = origin === 'sku' ? t('inventory.mult_origin_sku')
  : origin === 'category' ? t('inventory.mult_origin_category')
  : t('inventory.mult_origin_event')
 const custom = origin !== 'event'
 return (
  <span
   title={label}
   style={{
    display: 'inline-flex', alignItems: 'center', gap: 4,
    fontFamily: 'monospace', fontWeight: 700, fontSize: 11,
    padding: '2px 7px', borderRadius: 5,
    background: custom ? 'rgba(129,140,248,0.12)' : 'transparent',
    color: custom ? C.indigo : C.muted,
   }}
  >
   x{value.toFixed(1)}
   <span style={{ fontWeight: 500, fontSize: 9, opacity: 0.85 }}>{label}</span>
  </span>
 )
}

function MultiplierExplainer({ result, eventId, onEdited }: {
 result: EventSimulationResult
 eventId: string
 onEdited: () => void
}) {
 const { t } = useLanguage()
 const [open, setOpen] = useState(false)
 const [rows, setRows] = useState<EventMultiplier[] | null>(null)
 const [form, setForm] = useState({ scope: 'category' as 'sku' | 'category', value: '', mult: '2.0' })
 const [busy, setBusy] = useState(false)
 const [err, setErr] = useState('')
 const exp = result.explanation

 const load = useCallback(() => {
  listEventMultipliers(eventId).then(setRows).catch(() => setRows([]))
 }, [eventId])

 useEffect(() => { if (open && rows === null) load() }, [open, rows, load])

 async function save() {
  if (!form.value.trim()) return
  setBusy(true); setErr('')
  try {
   await setEventMultiplier(eventId, form.scope, form.value.trim(), parseFloat(form.mult) || 1)
   setForm(f => ({ ...f, value: '' }))
   load(); onEdited()
  } catch (e) { setErr(e instanceof Error ? e.message : t('inventory.mult_err_save')) }
  finally { setBusy(false) }
 }

 async function remove(id: string) {
  setBusy(true); setErr('')
  try { await deleteEventMultiplier(eventId, id); load(); onEdited() }
  catch (e) { setErr(e instanceof Error ? e.message : t('inventory.mult_err_delete')) }
  finally { setBusy(false) }
 }

 const inputS3: React.CSSProperties = {
  background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
  color: C.text, fontSize: 11, outline: 'none', padding: '5px 8px',
 }

 return (
  <div style={{ marginBottom: 16, padding: '12px 14px', borderRadius: 9, background: C.card, border: `1px solid ${C.border}` }}>
   <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
    <Info size={13} color={C.indigo} style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
    <div style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: C.muted, lineHeight: 1.55 }}>
     <strong style={{ color: C.text }}>
      {t('inventory.mult_base_label')} x{exp.base_multiplier.toFixed(1)}
     </strong>
     {' — '}
     {exp.es_estimacion ? t('inventory.mult_from_catalog') : t('inventory.mult_from_user')}
     {exp.reason && <div style={{ marginTop: 3 }}>{exp.reason}</div>}
     {exp.es_estimacion && (
      <div style={{ marginTop: 3, color: C.dim }}>{t('inventory.mult_estimate_hint')}</div>
     )}
     {result.multipliers_applied.length > 0 && (
      <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
       {result.multipliers_applied.map(m => (
        <span key={`${m.multiplier}-${m.source}`} style={{ fontSize: 10, color: C.dim }}>
         <MultiplierChip value={m.multiplier} origin={m.source} /> {m.skus} SKU
        </span>
       ))}
      </div>
     )}
    </div>
    <button
     onClick={() => setOpen(v => !v)}
     aria-expanded={open}
     style={{ all: 'unset', cursor: 'pointer', flexShrink: 0, fontSize: 11, fontWeight: 600, color: C.indigo, padding: '2px 4px' }}
    >
     {open ? t('inventory.mult_btn_hide') : t('inventory.mult_btn_adjust')}
    </button>
   </div>

   {open && (
    <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${C.border}` }}>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 8 }}>
      {t('inventory.mult_edit_hint')}
     </div>

     {rows && rows.length > 0 && (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 10 }}>
       {rows.map(o => (
        <div key={o.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
         <span style={{ color: C.dim, width: 68 }}>
          {o.scope === 'sku' ? t('inventory.mult_origin_sku') : t('inventory.mult_origin_category')}
         </span>
         <span style={{ flex: 1, color: C.text, fontFamily: 'monospace' }}>{o.scope_value}</span>
         <span style={{ fontFamily: 'monospace', fontWeight: 700, color: C.indigo }}>x{o.multiplier.toFixed(1)}</span>
         <button
          onClick={() => remove(o.id)}
          disabled={busy}
          aria-label={`${t('inventory.mult_btn_remove')}: ${o.scope_value}`}
          title={t('inventory.mult_btn_remove')}
          style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 3 }}
         >
          <Trash2 size={11} aria-hidden="true" />
         </button>
        </div>
       ))}
      </div>
     )}

     <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <select
       value={form.scope}
       onChange={e => setForm(f => ({ ...f, scope: e.target.value as 'sku' | 'category' }))}
       aria-label={t('inventory.mult_scope_label')}
       style={{ ...inputS3, width: 110 }}
      >
       <option value="category">{t('inventory.mult_origin_category')}</option>
       <option value="sku">{t('inventory.mult_origin_sku')}</option>
      </select>
      <input
       value={form.value}
       onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
       placeholder={form.scope === 'sku' ? t('inventory.mult_ph_sku') : t('inventory.mult_ph_category')}
       aria-label={t('inventory.mult_value_label')}
       style={{ ...inputS3, flex: 1, minWidth: 130 }}
      />
      <input
       type="number" step="0.1" min="0.1" max="10"
       value={form.mult}
       onChange={e => setForm(f => ({ ...f, mult: e.target.value }))}
       aria-label={t('inventory.mult_value_multiplier')}
       style={{ ...inputS3, width: 70 }}
      />
      <button
       onClick={save}
       disabled={busy || !form.value.trim()}
       style={{ all: 'unset', cursor: busy || !form.value.trim() ? 'not-allowed' : 'pointer', padding: '5px 12px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 11, fontWeight: 600, opacity: busy || !form.value.trim() ? 0.5 : 1 }}
      >
       {t('inventory.mult_btn_apply')}
      </button>
     </div>
     {err && <div style={{ marginTop: 6, fontSize: 11, color: C.red }}>{err}</div>}
    </div>
   )}
  </div>
 )
}

// ── Event impact simulator modal (feature 2.3) ───────────────────────────────
function EventSimModal({ ev, sessionId, onClose, onReload }: {
 ev: InventoryEvent
 sessionId: string
 onClose: () => void
 onReload: () => void
}) {
 const { t, lang } = useLanguage()
 const [result, setResult] = useState<EventSimulationResult | null>(null)
 const [error, setError] = useState<string | null>(null)

 useEffect(() => {
 simulateEvent({ session_id: sessionId, event_id: ev.id })
 .then(setResult)
 .catch(e => setError(e instanceof Error ? e.message : 'Error al simular'))
 }, [ev.id, sessionId])

 const fmtD = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'long' })
 const pctExtra = Math.round((ev.multiplier - 1) * 100)

 return (
 <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
 <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 640, maxHeight: '85vh', overflowY: 'auto', background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
 <Zap size={16} color={C.amber} />
 <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>Simulación: {ev.name}</span>
 <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} aria-hidden="true" /></button>
 </div>
 <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim }}>
 {fmtD(ev.start_date)} → {fmtD(ev.end_date)} · demanda estimada +{pctExtra}%
 </p>

 {!result && !error && <div style={{ padding: 24, textAlign: 'center' }}><Spinner size={16} /></div>}
 {error && <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 13, color: C.red }}>{error}</div>}

 {result && (
 <>
 {/* Headline — the actionable sentence */}
 <div style={{
 padding: '14px 18px', borderRadius: 10, marginBottom: 16,
 background: result.summary.skus_at_risk > 0 ? 'rgba(245,158,11,0.07)' : 'rgba(34,197,94,0.07)',
 border: `1px solid ${result.summary.skus_at_risk > 0 ? 'rgba(245,158,11,0.3)' : 'rgba(34,197,94,0.3)'}`,
 fontSize: 13, color: C.text, lineHeight: 1.6,
 }}>
 {result.summary.skus_at_risk > 0 ? (
 <>
 Con <strong>{ev.name}</strong> (+{pctExtra}% de demanda), necesitarías pedir{' '}
 <strong>{result.summary.total_to_order.toLocaleString()} unidades extra</strong> en{' '}
 <strong>{result.summary.skus_at_risk} producto{result.summary.skus_at_risk !== 1 ? 's' : ''}</strong>
 {result.summary.order_before && <> antes del <strong>{fmtD(result.summary.order_before)}</strong></>}
 {result.summary.total_order_value > 0 && <> (≈ {formatMoney(result.summary.total_order_value)})</>}.
 {result.summary.any_order_late && (
 <div style={{ color: C.red, fontWeight: 600, marginTop: 6 }}>
 ⚠ Para algunos productos ya es tarde: si pides hoy, la orden llegaría con el evento en curso.
 </div>
 )}
 </>
 ) : (
 <>✓ Tu stock actual resiste <strong>{ev.name}</strong> (+{pctExtra}% de demanda) sin pedidos adicionales.</>
 )}
 </div>

 {/* Why this multiplier — never show a x2.2 without justifying it */}
 <MultiplierExplainer result={result} eventId={ev.id} onEdited={onReload} />

 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr>
 {[t('inventory.sim_col_product'), t('inventory.sim_col_multiplier'), t('inventory.sim_col_event_demand'), t('inventory.sim_col_stock_at_start'), t('inventory.sim_col_shortfall'), t('inventory.sim_col_order'), t('inventory.sim_col_order_before')].map(h => (
 <th key={h} style={{ textAlign: 'left', padding: '6px 8px', color: C.dim, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: `1px solid ${C.border}` }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {result.items.map(r => (
 <tr key={r.sku} style={{ borderBottom: `1px solid ${C.border}`, background: r.en_risk ? 'rgba(245,158,11,0.04)' : undefined }}>
 <td style={{ padding: '8px' }}>
 <div style={{ fontWeight: 600, color: C.text }}>{r.display_name || r.sku}</div>
 <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{r.sku}</div>
 </td>
 <td style={{ padding: '8px' }}>
 <MultiplierChip value={r.multiplier} origin={r.multiplier_source} />
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.text }}>
 {r.event_units.toLocaleString()}
 <span style={{ color: C.dim, fontSize: 10 }}> (+{r.extra_units.toLocaleString()})</span>
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.muted }}>
 {r.stock_al_inicio != null ? r.stock_al_inicio.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.en_risk ? C.red : C.green }}>
 {r.deficit != null ? (r.deficit > 0 ? r.deficit.toLocaleString() : '✓') : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.qty_to_order ? C.text : C.dim }}>
 {r.qty_to_order ? r.qty_to_order.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontSize: 11, color: r.llega_tarde && r.en_risk ? C.red : C.muted, whiteSpace: 'nowrap' }}>
 {r.en_risk ? (r.llega_tarde ? '¡hoy mismo!' : fmtD(r.order_by)) : '—'}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 <p style={{ margin: '14px 0 0', fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
 Cálculo: demanda diaria del pronóstico × {result.event_days} día{result.event_days !== 1 ? 's' : ''} × {ev.multiplier.toFixed(1)},
 contra el stock proyectado al inicio del evento. Las cantidades respetan el MOQ de cada producto. Nada se guarda — es solo una simulación.
 </p>
 </>
 )}
 </div>
 </div>
 )
}

// ── Shrinkage modal (record a non-sale stock-out: breakage/expiry/self-consumption/gift) ──
const SHRINKAGE_REASONS: ShrinkageReason[] = ['breakage', 'expiry', 'self_consumption', 'gift']

function ShrinkageModal({ items, onClose, onSaved }: {
 items: InventoryStatusItem[]
 onClose: () => void
 onSaved: () => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const [sku, setSku] = useState('')
 const [quantity, setQuantity] = useState('')
 const [reason, setReason] = useState<ShrinkageReason>('breakage')
 const [notes, setNotes] = useState('')
 const [saving, setSaving] = useState(false)
 const [error, setError] = useState<string | null>(null)

 const selected = items.find(i => i.sku === sku) || null
 const qtyNum = parseFloat(quantity)
 const estCost = selected?.unit_cost != null && !isNaN(qtyNum) && qtyNum > 0
  ? qtyNum * selected.unit_cost
  : null

 const inputM: React.CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 12, outline: 'none', padding: '7px 9px', width: '100%', boxSizing: 'border-box' }

 async function handleSubmit() {
  setError(null)
  if (!selected) { setError(t('inventory.shrinkage_err_select_sku')); return }
  if (!qtyNum || qtyNum <= 0) { setError(t('inventory.shrinkage_err_quantity')); return }
  setSaving(true)
  try {
   await createShrinkage({ sku: selected.sku, quantity: qtyNum, reason, notes: notes || undefined })
   addToast(t('inventory.shrinkage_toast_title'), `${qtyNum} ${t('inventory.calc_unit_units')} ${t('inventory.shrinkage_toast_body')} ${selected.sku}`, 'success')
   onSaved()
   onClose()
  } catch (e) {
   setError(e instanceof Error ? e.message : String(e))
  } finally {
   setSaving(false)
  }
 }

 return (
  <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
   <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 460, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
     <PackageMinus size={16} color={C.red} />
     <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{t('inventory.shrinkage_title_register')}</span>
     <button onClick={onClose} aria-label={t('common.close')} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} aria-hidden="true" /></button>
    </div>
    <p style={{ margin: '0 0 16px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>{t('inventory.shrinkage_subtitle')}</p>

    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
     <div>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_sku')}</div>
      <input
       list="shrinkage-sku-options"
       style={inputM}
       placeholder={t('inventory.shrinkage_sku_placeholder')}
       value={sku}
       onChange={e => setSku(e.target.value)}
       autoFocus
      />
      <datalist id="shrinkage-sku-options">
       {items.map(i => (
        <option key={i.sku} value={i.sku}>{i.display_name || i.sku}</option>
       ))}
      </datalist>
      {selected && (
       <div style={{ marginTop: 4, fontSize: 11, color: C.dim }}>
        {t('inventory.shrinkage_current_stock_prefix')} <strong style={{ color: C.text }}>{fmt(selected.current_stock, 0)}</strong>
       </div>
      )}
     </div>

     <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
      <div>
       <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_quantity')}</div>
       <input type="number" min={0} step="any" style={inputM} value={quantity} onChange={e => setQuantity(e.target.value)} />
      </div>
      <div>
       <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_reason')}</div>
       <select style={inputM} value={reason} onChange={e => setReason(e.target.value as ShrinkageReason)}>
        {SHRINKAGE_REASONS.map(r => <option key={r} value={r}>{t(`inventory.shrinkage_reason_${r}`)}</option>)}
       </select>
      </div>
     </div>

     <div>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>{t('inventory.shrinkage_field_notes')}</div>
      <input style={inputM} placeholder={t('inventory.shrinkage_notes_placeholder')} value={notes} onChange={e => setNotes(e.target.value)} />
     </div>

     {estCost != null && (
      <div style={{ fontSize: 11, color: C.dim }}>
       {t('inventory.shrinkage_estimated_cost_prefix')} <strong style={{ color: C.red }}>{fmtCurrency(estCost)}</strong>
      </div>
     )}

     {error && (
      <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 12, color: C.red }}>{error}</div>
     )}

     <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
      <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', padding: '7px 14px', borderRadius: 8, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>{t('common.cancel')}</button>
      <button onClick={handleSubmit} disabled={saving} style={{ all: 'unset', cursor: saving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 16px', borderRadius: 8, background: C.red, color: '#fff', fontSize: 12, fontWeight: 600, opacity: saving ? 0.6 : 1 }}>
       {saving && <Spinner size={12} />} {saving ? t('inventory.shrinkage_btn_submitting') : t('inventory.shrinkage_btn_submit')}
      </button>
     </div>
    </div>
   </div>
  </div>
 )
}

// ── LatAm calendar catalog (feature 3.4) ─────────────────────────────────────
const COUNTRY_NAMES: Record<string, string> = { CR: 'Costa Rica', CO: 'Colombia' }
// The catalog is seeded into the DB (not a frontend array): this only
// shows its state and switches each event on/off.
function CalendarCatalogPanel({ onSeeded }: { onSeeded: () => void }) {
 const { t, lang } = useLanguage()
 const [entries, setEntries] = useState<CalendarCatalogEntry[] | null>(null)
 const [countries, setCountries] = useState<string[]>([])
 const [country, setCountry] = useState('')   // '' = default del backend (CR)
 const [busy, setBusy] = useState<string | null>(null)
 const [err, setErr] = useState('')

 const load = useCallback(() => {
  getCalendarCatalog(country || undefined)
   .then(r => { setEntries(r.entries); setCountries(r.countries); setCountry(c => c || r.country) })
   .catch(e => setErr(e instanceof Error ? e.message : String(e)))
 }, [country])

 useEffect(() => { load() }, [load])

 async function handleSeed() {
  setBusy('__seed__'); setErr('')
  try {
   await seedCalendarCatalog(country || undefined)
   load(); onSeeded()
  } catch (e) { setErr(e instanceof Error ? e.message : t('inventory.calendar_err_seed')) }
  finally { setBusy(null) }
 }

 async function handleToggle(entry: CalendarCatalogEntry) {
  setBusy(entry.key); setErr('')
  const next = !entry.active
  // Optimista: el toggle debe sentirse inmediato.
  setEntries(prev => prev?.map(e => e.key === entry.key ? { ...e, active: next } : e) ?? null)
  try {
   await toggleCalendarEntry(entry.key, next)
   onSeeded()
  } catch (e) {
   setEntries(prev => prev?.map(x => x.key === entry.key ? { ...x, active: entry.active } : x) ?? null)
   setErr(e instanceof Error ? e.message : t('inventory.calendar_err_toggle'))
  } finally { setBusy(null) }
 }

 if (!entries) return <div style={{ fontSize: 12, color: C.dim, padding: '10px 0' }}>{t('common.loading')}</div>

 const anySeeded = entries.some(e => e.seeded)

 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
   <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
    <div style={{ fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
     {t('inventory.calendar_intro')}
     {countries.length > 1 && (
      <>
       {' '}
       <label htmlFor="cal-country" style={{ marginLeft: 4 }}>{t('inventory.calendar_country')}</label>{' '}
       <select
        id="cal-country"
        value={country}
        onChange={e => { setEntries(null); setCountry(e.target.value) }}
        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text, fontSize: 11, padding: '2px 5px' }}
       >
        {countries.map(c => <option key={c} value={c}>{COUNTRY_NAMES[c] ?? c}</option>)}
       </select>
      </>
     )}
   </div>
    {!anySeeded && (
     <button
      onClick={handleSeed}
      disabled={busy === '__seed__'}
      style={{ all: 'unset', cursor: busy ? 'wait' : 'pointer', flexShrink: 0, padding: '6px 12px', borderRadius: 7, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: busy === '__seed__' ? 0.6 : 1 }}
     >
      {busy === '__seed__' ? t('inventory.calendar_seeding') : t('inventory.calendar_btn_load')}
     </button>
    )}
   </div>

   {err && <div style={{ fontSize: 11, color: C.red }}>{err}</div>}

   {entries.map(entry => {
    const on = entry.seeded && entry.active
    return (
     <div key={entry.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderRadius: 8, background: C.card, border: `1px solid ${C.border}`, opacity: entry.seeded ? 1 : 0.55 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
       <div style={{ fontSize: 12.5, fontWeight: 600, color: C.text }}>{entry.name}</div>
       <div style={{ fontSize: 10.5, color: C.dim, marginTop: 2, lineHeight: 1.45 }}>{entry.notes}</div>
       <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>
        {entry.seeded
         ? <>×{entry.multiplier.toFixed(1)} · {entry.occurrences} {t('inventory.calendar_occurrences')}
            {entry.next_start && <> · {t('inventory.calendar_next')} {new Date(entry.next_start + 'T00:00:00').toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short', year: 'numeric' })}</>}
           </>
         : t('inventory.calendar_not_loaded')}
       </div>
      </div>
      <button
       role="switch"
       aria-checked={on}
       aria-label={`${on ? t('inventory.calendar_toggle_off_aria') : t('inventory.calendar_toggle_on_aria')}: ${entry.name}`}
       disabled={!entry.seeded || busy === entry.key}
       onClick={() => handleToggle(entry)}
       style={{
        all: 'unset', flexShrink: 0,
        cursor: !entry.seeded ? 'not-allowed' : busy === entry.key ? 'wait' : 'pointer',
        width: 38, height: 21, borderRadius: 11, position: 'relative',
        background: on ? C.indigo : C.border,
        transition: 'background 0.15s',
       }}
      >
       <span style={{
        position: 'absolute', top: 3, left: on ? 20 : 3,
        width: 15, height: 15, borderRadius: '50%', background: '#fff',
        transition: 'left 0.15s',
       }} />
      </button>
      {/* Estado en texto: el toggle no puede comunicarse sólo por posición/color. */}
      <span style={{ fontSize: 10, fontWeight: 600, width: 52, textAlign: 'right', flexShrink: 0, color: on ? C.indigo : C.dim }}>
       {entry.seeded ? (on ? t('inventory.calendar_state_on') : t('inventory.calendar_state_off')) : '—'}
      </span>
     </div>
    )
   })}

   {anySeeded && (
    <button
     onClick={handleSeed}
     disabled={busy === '__seed__'}
     style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: C.dim, padding: '5px 0', textAlign: 'center' }}
    >
     {t('inventory.calendar_btn_refresh')}
    </button>
   )}
  </div>
 )
}

// ── Events panel ─────────────────────────────────────────────────────────────
function EventsPanel({ events, onAdd, onDelete, onSimulate, onCatalogChange }: {
 events: InventoryEvent[]
 onAdd: (ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) => void
 onDelete: (id: string) => void
 onSimulate: (ev: InventoryEvent) => void
 onCatalogChange: () => void
}) {
 const { t, lang } = useLanguage()
 const [adding, setAdding] = useState(false)
 const [tab, setTab] = useState<'mine' | 'catalog'>('mine')
 const [form, setForm] = useState({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })

 const visible = events.filter(e => e.active !== false)
 const upcoming = visible.filter(e => new Date(e.end_date) >= new Date())
 const past = visible.filter(e => new Date(e.end_date) < new Date())

 function handleAdd() {
 if (!form.name || !form.start_date || !form.end_date) return
 onAdd({ name: form.name, start_date: form.start_date, end_date: form.end_date, multiplier: parseFloat(form.multiplier) || 1.5, notes: form.notes || null })
 setForm({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })
 setAdding(false)
 }

 const inputS2: React.CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 12, outline: 'none', padding: '6px 9px', width: '100%', boxSizing: 'border-box' }

 const TODAY_LABEL = t('inventory.day_today')
 const TOMORROW_LABEL = t('inventory.day_tomorrow')
 const daysUntil = (date: string) => {
 const d = Math.round((new Date(date).getTime() - Date.now()) / 86400000)
 if (d < 0) return null
 if (d === 0) return TODAY_LABEL
 if (d === 1) return TOMORROW_LABEL
 return `${t('inventory.day_in_prefix')} ${d} ${t('inventory.day_in_suffix')}`
 }

 const tabS = (active: boolean): React.CSSProperties => ({
  all: 'unset', cursor: 'pointer', padding: '5px 12px', borderRadius: 7,
  fontSize: 11.5, fontWeight: 600,
  background: active ? 'rgba(129,140,248,0.12)' : 'transparent',
  color: active ? C.indigo : C.dim,
 })

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {/* Mis events vs. calendar LatAm precargado */}
 <div role="tablist" aria-label={t('inventory.events_tablist_aria')} style={{ display: 'flex', gap: 4, marginBottom: 2 }}>
  <button role="tab" aria-selected={tab === 'mine'} onClick={() => setTab('mine')} style={tabS(tab === 'mine')}>
   {t('inventory.events_tab_mine')}
  </button>
  <button role="tab" aria-selected={tab === 'catalog'} onClick={() => setTab('catalog')} style={tabS(tab === 'catalog')}>
   {t('inventory.events_tab_calendar')}
  </button>
 </div>

 {tab === 'catalog' ? <CalendarCatalogPanel onSeeded={onCatalogChange} /> : <>
 {/* Upcoming */}
 {upcoming.map(ev => {
 const until = daysUntil(ev.start_date)
 const isClose = until && ![TODAY_LABEL, TOMORROW_LABEL].includes(until) ? parseInt(until) <= 14 : !!until
 return (
 <div key={ev.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 8, background: isClose ? 'rgba(245,158,11,0.06)' : C.card, border: `1px solid ${isClose ? 'rgba(245,158,11,0.25)' : C.border}` }}>
 <Calendar size={14} color={isClose ? C.amber : C.dim} style={{ flexShrink: 0 }} />
 <div style={{ flex: 1, minWidth: 0 }}>
 <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{ev.name}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 1 }}>
 {new Date(ev.start_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}
 {ev.end_date !== ev.start_date && ` → ${new Date(ev.end_date).toLocaleDateString(localeFor(lang), { day: 'numeric', month: 'short' })}`}
 {until && <span style={{ marginLeft: 8, color: isClose ? C.amber : C.dim }}>({until})</span>}
 </div>
 </div>
 <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 20, background: 'rgba(129,140,248,0.1)', color: C.indigo, flexShrink: 0 }}>
 ×{ev.multiplier.toFixed(1)}
 </span>
 <button
 onClick={() => onSimulate(ev)}
 title={t('inventory.events_simulate_tooltip')}
 aria-label={`${t('inventory.events_btn_simulate')}: ${ev.name}`}
 style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: `1px solid rgba(245,158,11,0.4)`, color: 'var(--signal-order-soon-fg)', fontSize: 11, fontWeight: 600, flexShrink: 0 }}
 >
 <Zap size={11} aria-hidden="true" /> {t('inventory.events_btn_simulate')}
 </button>
 <button
 onClick={() => onDelete(ev.id)}
 aria-label={`${t('inventory.events_btn_delete')}: ${ev.name}`}
 title={t('inventory.events_btn_delete')}
 style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }}
 onMouseEnter={e => (e.currentTarget.style.color = C.red)}
 onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
 >
 <Trash2 size={12} aria-hidden="true" />
 </button>
 </div>
 )
 })}

 {upcoming.length === 0 && !adding && (
 <div style={{ fontSize: 12, color: C.dim, textAlign: 'center', padding: '12px 0' }}>
 {t('inventory.events_empty_state')}
 </div>
 )}

 {/* Add form */}
 {adding ? (
 <div style={{ padding: '12px 14px', borderRadius: 8, background: C.card, border: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
 <input style={inputS2} placeholder={t('inventory.events_name_placeholder')} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} autoFocus />
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_start_date')}</div>
 <input style={inputS2} type="date" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_end_date')}</div>
 <input style={inputS2} type="date" value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>{t('inventory.events_multiplier')}</div>
 <select style={inputS2} value={form.multiplier} onChange={e => setForm(f => ({ ...f, multiplier: e.target.value }))}>
 <option value="1.2">×1.2 — {t('inventory.events_mult_mild')} (+20%)</option>
 <option value="1.5">×1.5 — {t('inventory.events_mult_moderate')} (+50%)</option>
 <option value="2.0">×2.0 — {t('inventory.events_mult_high')} (+100%)</option>
 <option value="2.5">×2.5 — {t('inventory.events_mult_very_high')} (+150%)</option>
 <option value="3.0">×3.0 — {t('inventory.events_mult_peak')} (+200%)</option>
 </select>
 </div>
 </div>
 <input style={inputS2} placeholder={t('inventory.events_notes_placeholder')} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
 <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
 <button onClick={() => setAdding(false)} style={{ all: 'unset', cursor: 'pointer', padding: '6px 12px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>{t('common.cancel')}</button>
 <button onClick={handleAdd} disabled={!form.name || !form.start_date || !form.end_date} style={{ all: 'unset', cursor: 'pointer', padding: '6px 14px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: !form.name || !form.start_date || !form.end_date ? 0.5 : 1 }}>{t('inventory.events_btn_save')}</button>
 </div>
 </div>
 ) : (
 <button onClick={() => setAdding(true)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, border: `1px dashed ${C.border}`, color: C.dim, fontSize: 12, justifyContent: 'center' }}>
 <Plus size={12} /> {t('inventory.events_btn_add')}
 </button>
 )}

 {past.length > 0 && (
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>
 {past.length} {past.length > 1 ? t('inventory.events_past_count_suffix_plural') : t('inventory.events_past_count_suffix_singular')}
 </div>
 )}
 </>}
 </div>
 )
}

// ── Inline edit state ─────────────────────────────────────────────────────────
interface EditState { current_stock: string; lead_time_days: string; unit_cost: string; moq: string; supplier: string; display_name: string; service_level: string; sale_price: string; category: string; brand: string; unit_of_measure: string; barcode: string }
function rowToEdit(item: InventoryStatusItem): EditState {
 return { current_stock: String(item.current_stock ?? ''), lead_time_days: String(item.lead_time_days ?? 15), unit_cost: String(item.unit_cost ?? ''), moq: String(item.moq ?? 1), supplier: item.supplier ?? '', display_name: item.display_name ?? '', service_level: String(item.service_level ?? 0.95), sale_price: String(item.sale_price ?? ''), category: item.category ?? '', brand: item.brand ?? '', unit_of_measure: item.unit_of_measure ?? '', barcode: item.barcode ?? '' }
}
const inputS: React.CSSProperties = { background: 'var(--surface-2)', border: `1px solid var(--border)`, borderRadius: 5, color: 'var(--text)', fontSize: 12, outline: 'none', padding: '3px 7px', width: '100%', boxSizing: 'border-box' }

// ── Provider group ────────────────────────────────────────────────────────────
function ProviderGroup({ name, items, onEdit, editedQty, editingQtySku, setEditedQty, setEditingQtySku, effectiveQty, coverageUnit }: {
 name: string; items: InventoryStatusItem[]; onEdit: (item: InventoryStatusItem) => void
 editedQty: Record<string, number>
 editingQtySku: string | null
 setEditedQty: React.Dispatch<React.SetStateAction<Record<string, number>>>
 setEditingQtySku: React.Dispatch<React.SetStateAction<string | null>>
 effectiveQty: (item: InventoryStatusItem) => number
 coverageUnit?: CoverageUnit
}) {
 const { t } = useLanguage()
 const [open, setOpen] = useState(true)
 const critical = items.filter(i => i.signal === 'PEDIR_YA').length
 const warning = items.filter(i => i.signal === 'PEDIR_PRONTO').length
 return (
 <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden', marginBottom: 10 }}>
 <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', background: C.card, cursor: 'pointer', borderBottom: open ? `1px solid ${C.border}` : 'none' }}>
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{name || t('inventory.no_provider')}</span>
 <span style={{ fontSize: 11, color: C.dim }}>{items.length} SKUs</span>
 {critical > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red }}>{critical} {critical !== 1 ? t('inventory.urgent_plural') : t('inventory.urgent_singular')}</span>}
 {warning > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{warning} {t('inventory.soon_suffix')}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </div>
 {open && items.map((item, idx) => (
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '160px 100px 90px 90px 80px 60px auto', gap: 12, padding: '10px 16px', alignItems: 'center', fontSize: 12, background: idx % 2 === 0 ? C.surface : C.card, borderBottom: idx < items.length - 1 ? `1px solid ${C.border}` : 'none' }}>
 <div><div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{item.sku}</div>{item.display_name && <div style={{ color: C.dim, fontSize: 10 }}>{item.display_name}</div>}</div>
 <SignalBadge s={item.signal} />
 <span style={{ color: signalColor(item.signal), fontWeight: 600 }}>{item.coverage_days != null ? `${item.coverage_days.toFixed(0)} ${coverageUnitShort(coverageUnit, t)}` : '—'}</span>
 <span>
 {item.recommended_qty != null && item.recommended_qty > 0 ? (
 editingQtySku === item.sku ? (
 <input
 type="number" min={1} autoFocus
 defaultValue={effectiveQty(item)}
 onClick={e => e.stopPropagation()}
 onBlur={e => {
 const n = parseInt(e.target.value, 10)
 setEditedQty(prev => (!isNaN(n) && n > 0 ? { ...prev, [item.sku]: n } : prev))
 setEditingQtySku(null)
 }}
 onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
 style={{ width: 70, background: C.card, border: `1px solid ${C.indigo}`, borderRadius: 5, color: C.text, fontSize: 13, fontWeight: 700, padding: '3px 6px', outline: 'none' }}
 />
 ) : (
 <button
 onClick={e => { e.stopPropagation(); setEditingQtySku(item.sku) }}
 title={t('inventory.edit_qty_title')}
 style={{ all: 'unset', cursor: 'pointer', fontWeight: 700, fontSize: 12,
 color: editedQty[item.sku] != null ? C.indigo : C.green,
 borderBottom: `2px dashed ${(editedQty[item.sku] != null ? C.indigo : C.green)}60`, lineHeight: 1 }}
 >
 {fmt(effectiveQty(item), 0)}
 </button>
 )
 ) : item.recommended_qty === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : <span style={{ color: C.dim }}>—</span>}
 </span>
 <span style={{ color: C.dim }}>{item.current_stock?.toFixed(0) ?? '—'}</span>
 <AbcXyzBadge value={item.abc_xyz} />
 <button onClick={() => onEdit(item)} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={12} /></button>
 </div>
 ))}
 </div>
 )
}

function fmt(n: number | null | undefined, d = 1) { if (n == null) return '—'; return n.toLocaleString(undefined, { maximumFractionDigits: d }) }
function fmtCurrency(n: number | null | undefined) { return formatMoney(n) }

// ── Simulator helpers ─────────────────────────────────────────────────────────
function simulateRecommendation(
 currentStock: number,
 dailyDemand: number,
 avgStd: number,
 leadTime: number,
 moq: number,
 serviceLevel = 0.95,
): number {
 const Z_MAP: Record<number, number> = { 0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326 }
 const z = Z_MAP[serviceLevel] ?? 1.645
 const demandLT = dailyDemand * leadTime
 const safetyStock = z * avgStd * Math.sqrt(leadTime)
 const raw = Math.max(0, demandLT + safetyStock - currentStock)
 if (moq > 0) return Math.ceil(raw / moq) * moq
 return Math.round(raw)
}

function SimulatorPanel({ item }: { item: InventoryStatusItem }) {
 const { t } = useLanguage()
 const exp = item.calc_explanation
 if (!exp || !item.daily_demand || item.daily_demand <= 0) return null

 const [ltDelta,    setLtDelta]    = useState(0)
 const [demandMult, setDemandMult] = useState(100)
 const [stockDelta, setStockDelta] = useState(0)

 const simLeadTime = Math.max(1, (item.lead_time_days ?? 15) + ltDelta)
 const simDemand   = (item.daily_demand ?? 0) * demandMult / 100
 const simStock    = Math.max(0, (item.current_stock ?? 0) + stockDelta)

 // Approximate avgStd from safety stock and original lead_time
 // safety_stock = z * avgStd * sqrt(lead_time) → avgStd ≈ safety_stock / (z * sqrt(lead_time))
 const origLT = item.lead_time_days ?? 15
 const origSS = exp.safety_stock ?? 0
 const z      = 1.645
 const avgStd = origLT > 0 ? origSS / (z * Math.sqrt(origLT)) : 0

 const simRecommended = simulateRecommendation(simStock, simDemand, avgStd, simLeadTime, item.moq ?? 1)
 const originalRec    = item.recommended_qty ?? 0
 const delta          = simRecommended - originalRec
 const deltaColor     = delta > 0 ? '#ef4444' : delta < 0 ? '#22c55e' : C.muted

 const sliderS: React.CSSProperties = { width: '100%', cursor: 'pointer', accentColor: '#818cf8' }

 return (
  <div style={{
   background: 'rgba(129,140,248,0.04)', border: '1px solid rgba(129,140,248,0.15)',
   borderRadius: 8, padding: '16px 18px', marginTop: 8,
  }}>
   <div style={{ fontSize: 11, fontWeight: 700, color: '#818cf8', marginBottom: 14,
    textTransform: 'uppercase', letterSpacing: '0.07em' }}>
    {t('inventory.sim_title')}
   </div>

   <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
    {/* Lead time slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_lead_time')}</span>
      <span style={{ fontWeight: 700, color: ltDelta !== 0 ? '#f59e0b' : C.text }}>
       {simLeadTime}d {ltDelta > 0 ? `(+${ltDelta})` : ltDelta < 0 ? `(${ltDelta})` : ''}
      </span>
     </div>
     <input type="range" min={-10} max={30} value={ltDelta}
      onChange={e => setLtDelta(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-10d</span><span>+30d</span>
     </div>
    </div>

    {/* Demand slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_demand_variation')}</span>
      <span style={{ fontWeight: 700, color: demandMult !== 100 ? '#f59e0b' : C.text }}>
       {demandMult}% {demandMult !== 100 ? `(${simDemand.toFixed(1)} ${t('inventory.calc_unit_per_day')})` : ''}
      </span>
     </div>
     <input type="range" min={50} max={200} step={5} value={demandMult}
      onChange={e => setDemandMult(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-50%</span><span>+100%</span>
     </div>
    </div>

    {/* Stock slider */}
    <div>
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: C.muted, marginBottom: 6 }}>
      <span>{t('inventory.sim_extra_stock')}</span>
      <span style={{ fontWeight: 700, color: stockDelta !== 0 ? '#f59e0b' : C.text }}>
       {stockDelta > 0 ? `+${stockDelta}` : stockDelta} {t('inventory.unit_und')}
      </span>
     </div>
     <input type="range" min={-(item.current_stock ?? 0)} max={(item.current_stock ?? 0) * 2}
      step={Math.max(1, Math.floor((item.current_stock ?? 50) / 10))}
      value={stockDelta}
      onChange={e => setStockDelta(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-{item.current_stock ?? 0}</span><span>+{(item.current_stock ?? 0) * 2}</span>
     </div>
    </div>
   </div>

   {/* Result */}
   <div style={{
    marginTop: 16, padding: '12px 16px', borderRadius: 8,
    background: 'var(--surface)', border: `1px solid ${C.border}`,
    display: 'flex', alignItems: 'center', gap: 16,
   }}>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_original_rec')}</div>
     <div style={{ fontSize: 20, fontWeight: 800, color: C.muted }}>{originalRec.toLocaleString('es')} {t('inventory.unit_und')}</div>
    </div>
    <div style={{ fontSize: 20, color: C.dim }}>→</div>
    <div>
     <div style={{ fontSize: 11, color: C.dim, marginBottom: 2 }}>{t('inventory.sim_with_changes')}</div>
     <div style={{ fontSize: 24, fontWeight: 900, color: delta > 0 ? '#ef4444' : delta < 0 ? '#22c55e' : C.text }}>
      {simRecommended.toLocaleString('es')} {t('inventory.unit_und')}
     </div>
    </div>
    {delta !== 0 && (
     <div style={{ fontSize: 13, color: deltaColor, fontWeight: 600 }}>
      {delta > 0 ? `+${delta.toLocaleString('es')} ${t('inventory.sim_units_more')}` : `${Math.abs(delta).toLocaleString('es')} ${t('inventory.sim_units_less')}`}
     </div>
    )}
    <button onClick={() => { setLtDelta(0); setDemandMult(100); setStockDelta(0) }}
     style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', fontSize: 11,
      color: C.dim, padding: '4px 10px', border: `1px solid ${C.border}`, borderRadius: 6 }}>
     {t('inventory.btn_reset')}
    </button>
   </div>
  </div>
 )
}

// ── Main ─────────────────────────────────────────────────────────────────────
export default function InventoryPage() {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const { advancedMode } = useBusinessProfile()
 const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
 const { sessionId, setSessionId, currentSession, completedSessions, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 const [data, setData] = useState<{ items: InventoryStatusItem[]; summary: Record<string, number>; excluded_skus?: ExcludedSku[]; coverage_unit?: CoverageUnit } | null>(null)
 const [loading, setLoading] = useState(false)
 // Raw error, so ErrorState can classify by kind instead of showing a
 // pre-flattened string.
 const [error, setError] = useState<unknown>(null)
 const [signalFilter, setSignalFilter] = useState<InventorySignal | ''>('')
 const [search, setSearch] = useState('')
 const [viewMode, setViewMode] = useState<'table' | 'simple' | 'provider' | 'update' | 'dead'>(() =>
 typeof window !== 'undefined' && localStorage.getItem('adv') === '1' ? 'table' : 'simple'
 )
 const [expandedSku, setExpandedSku] = useState<string | null>(null)
 const [editId, setEditId] = useState<string | null>(null)
 const [editState, setEditState] = useState<EditState | null>(null)
 const [saving, setSaving] = useState(false)
 const [importing, setImporting] = useState(false)
 const [exporting, setExporting] = useState(false)
 const [pdfLoading, setPdfLoading] = useState(false)
 const [events, setEvents] = useState<InventoryEvent[]>([])
 const [showEvents, setShowEvents] = useState(false)
 const [simEvent, setSimEvent] = useState<InventoryEvent | null>(null)
 const [updateDraft, setUpdateDraft] = useState<Record<string, { current_stock: string; lead_time_days: string; supplier: string }>>({})
 const [updateSaving, setUpdateSaving] = useState(false)
 const [updatedSkus, setUpdatedSkus] = useState<Set<string>>(new Set())
 const [suppliers, setSuppliers] = useState<Supplier[]>([])
 const importRef = useRef<HTMLInputElement>(null)
 const savingRef = useRef(false)
 const [deadStock,   setDeadStock]   = useState<DeadStockResponse | null>(null)
 const [loadingDead, setLoadingDead] = useState(false)
 const [editedQty, setEditedQty] = useState<Record<string, number>>({})
 const [editingQtySku, setEditingQtySku] = useState<string | null>(null)
 const [showShrinkageModal, setShowShrinkageModal] = useState(false)
 // Multi-warehouse (feature 5.4): selector + per-warehouse view. null = all.
 const { warehouses, multi: multiWarehouse } = useWarehouses()
 const [selectedWarehouse, setSelectedWarehouse] = useState<string | null>(null)

 // Effective order quantity for an item: the buyer's edit if present, else the recommendation.
 const effectiveQty = useCallback((item: InventoryStatusItem): number =>
  editedQty[item.sku] ?? item.recommended_qty ?? 0, [editedQty])

 const reloadEvents = useCallback(() => {
 listInventoryEvents().then(setEvents).catch(() => {})
 }, [])

 useEffect(() => {
 reloadEvents()
 listSuppliers().then(setSuppliers).catch(() => {})
 }, [reloadEvents])

 const load = useCallback(async (sid: string) => {
 if (!sid) return
 setLoading(true); setError(null)
 setEditedQty({})
 setEditingQtySku(null)
 // `silent: true` — the failure is rendered as a full ErrorState below, so the
 // interceptor's toast would say the same thing twice.
 try { setData(await getInventoryStatus(sid, 0.95, { silent: true })) }
 catch (e: unknown) { setError(e) }
 finally { setLoading(false) }
 }, [])

 useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

 // ── Dead stock load ────────────────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'dead' || !sessionId) return
 setLoadingDead(true)
 getDeadStock(sessionId)
  .then(setDeadStock)
  .catch((e: unknown) => setError(e))
  .finally(() => setLoadingDead(false))
 }, [viewMode, sessionId])

 // ── Update-draft initialization ────────────────────────────────────────────
 useEffect(() => {
 if (viewMode === 'update' && data) {
 const draft: Record<string, { current_stock: string; lead_time_days: string; supplier: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = {
 current_stock: String(item.current_stock ?? ''),
 lead_time_days: String(item.lead_time_days ?? 15),
 supplier: item.supplier ?? '',
 }
 })
 setUpdateDraft(draft)
 setUpdatedSkus(new Set())
 }
 }, [viewMode, data])

 function handleDraftChange(sku: string, field: string, value: string) {
 setUpdateDraft(prev => ({ ...prev, [sku]: { ...prev[sku], [field]: value } }))
 setUpdatedSkus(prev => { const next = new Set(prev); next.add(sku); return next })
 }

 async function handleSaveAll() {
 if (!sessionId) return
 setUpdateSaving(true)
 const toSave = Object.entries(updateDraft).filter(([sku, draft]) => {
 const original = data?.items.find(i => i.sku === sku)
 if (!original) return true
 return (
 parseFloat(draft.current_stock) !== (original.current_stock ?? 0) ||
 parseInt(draft.lead_time_days) !== original.lead_time_days ||
 draft.supplier !== (original.supplier ?? '')
 )
 })
 let saved = 0
 const failed: string[] = []
 for (const [sku, draft] of toSave) {
 try {
 await upsertInventoryStock(sku, {
 current_stock: parseFloat(draft.current_stock) || 0,
 lead_time_days: parseInt(draft.lead_time_days) || 15,
 supplier: draft.supplier || undefined,
 })
 saved++
 } catch (e) {
 console.error(`Error saving ${sku}:`, e)
 failed.push(sku)
 }
 }
 setUpdateSaving(false)
 if (failed.length === 0) {
 addToast(t('inventory.toast_saved_title'), `${saved} SKUs`, 'success')
 setUpdateDraft({})
 setUpdatedSkus(new Set())
 setViewMode('table')
 } else {
 addToast(t('inventory.toast_save_partial'), `${failed.join(', ')} ${t('inventory.toast_save_failed_sufx')}`, 'error')
 setUpdatedSkus(new Set(failed))
 }
 await load(sessionId)
 }

 const items = useMemo(() => (data?.items ?? []).filter(item => {
 if (signalFilter && item.signal !== signalFilter) return false
 if (search) { const q = search.toLowerCase(); return item.sku.toLowerCase().includes(q) || (item.display_name ?? '').toLowerCase().includes(q) || (item.supplier ?? '').toLowerCase().includes(q) }
 return true
 }), [data, signalFilter, search])

 const byProvider = useMemo(() => {
 const groups: Record<string, InventoryStatusItem[]> = {}
 for (const item of items) { const k = item.supplier || ''; if (!groups[k]) groups[k] = []; groups[k].push(item) }
 const PRIO = ['PEDIR_YA', 'PEDIR_PRONTO', 'OK', 'SOBRESTOCK', 'SIN_DATOS']
 return Object.entries(groups).sort((a, b) => {
 const sa = Math.min(...a[1].map(i => PRIO.indexOf(i.signal)))
 const sb = Math.min(...b[1].map(i => PRIO.indexOf(i.signal)))
 return sa - sb || a[0].localeCompare(b[0])
 })
 }, [items])

 // Upcoming events within 30 days
 const upcomingAlerts = useMemo(() => events.filter(e => {
 if (e.active === false) return false   // un event apagado no debe alertar
 const d = Math.round((new Date(e.start_date).getTime() - Date.now()) / 86400000)
 return d >= 0 && d <= 30 && new Date(e.end_date) >= new Date()
 }), [events])

 function startEdit(item: InventoryStatusItem) { setEditId(item.sku); setEditState(rowToEdit(item)) }
 function cancelEdit() { setEditId(null); setEditState(null) }

 async function commitEdit(sku: string) {
 // Guard with a ref, not just the `saving` state: a fast double-click fires
 // this handler twice before React re-renders the disabled button, and both
 // calls would otherwise close over the same stale saving=false and both
 // PUT to the backend.
 if (!editState || savingRef.current) return
 savingRef.current = true; setSaving(true)
 try {
 await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, current_stock: parseFloat(editState.current_stock) || 0, lead_time_days: parseInt(editState.lead_time_days) || 15, unit_cost: editState.unit_cost ? parseFloat(editState.unit_cost) : undefined, moq: parseFloat(editState.moq) || 1, supplier: editState.supplier || undefined, service_level: parseFloat(editState.service_level) || 0.95, sale_price: editState.sale_price ? parseFloat(editState.sale_price) : undefined, category: editState.category || undefined, brand: editState.brand || undefined, unit_of_measure: editState.unit_of_measure || undefined, barcode: editState.barcode || undefined })
 setEditId(null); setEditState(null); await load(sessionId)
 } catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_saving')) }
 finally { savingRef.current = false; setSaving(false) }
 }

 function handleDelete(sku: string) { setDeleteTarget(sku) }

 async function confirmDelete() {
 if (!deleteTarget) return
 const sku = deleteTarget
 setDeleteTarget(null)
 try { await deleteInventoryStock(sku); await load(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_deleting')) }
 }

 async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
 const file = e.target.files?.[0]; if (!file) return; e.target.value = ''; setImporting(true)
 try {
 const res = await importInventoryCSV(file)
 await load(sessionId)
 addToast(t('inventory.toast_import_title'), `${t('inventory.alert_imported_prefix')} ${res.imported} ${t('inventory.alert_imported_of')} ${res.total_rows} SKUs`, 'success')
 }
 catch (err: unknown) { setError(err instanceof Error ? err.message : t('inventory.err_importing')) }
 finally { setImporting(false) }
 }

 async function handleExport() {
 if (!sessionId) return; setExporting(true)
 try { await exportInventoryPO(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_exporting')) }
 finally { setExporting(false) }
 }

 // Exports a PO CSV built from the buyer's edited quantities (instead of the
 // server re-deriving them) and logs the decisions via logPOGeneration so
 // edited amounts are reflected in adoption tracking.
 function exportEditedPO() {
 if (!sessionId || !data) return
 const orderItems = data.items
  .filter(i => (i.signal === 'PEDIR_YA' || i.signal === 'PEDIR_PRONTO') && effectiveQty(i) > 0)
 if (orderItems.length === 0) return
 // CSV
 const rows = ['SKU,Producto,Cantidad,Proveedor,Valor estimado']
 for (const i of orderItems) {
  const qty = effectiveQty(i)
  const val = qty * (i.unit_cost ?? 0)
  rows.push(`${i.sku},"${i.display_name ?? ''}",${qty},"${i.supplier ?? ''}",${val}`)
 }
 const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
 const url = URL.createObjectURL(blob)
 const a = document.createElement('a'); a.href = url; a.download = 'purchase_order.csv'; a.click()
 URL.revokeObjectURL(url)
 // Log decisions (edited => 'modified', otherwise 'approved')
 const decisions: POLineDecision[] = orderItems.map(i => ({
  sku: i.sku,
  display_name: i.display_name ?? undefined,
  supplier: i.supplier ?? undefined,
  signal: i.signal,
  recommended_qty: i.recommended_qty ?? 0,
  final_qty: effectiveQty(i),
  status: (editedQty[i.sku] != null ? 'modified' : 'approved') as 'approved' | 'modified',
  unit_cost: i.unit_cost ?? undefined,
 }))
 logPOGeneration(sessionId, decisions).catch(() => {})
 }

 async function handlePDF() {
 if (!sessionId) return; setPdfLoading(true)
 try { await downloadInventoryPDF(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_generating_pdf')) }
 finally { setPdfLoading(false) }
 }

 async function handleAddEvent(ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) {
 try { const created = await createInventoryEvent(ev); setEvents(prev => [...prev, created]) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_saving_event')) }
 }

 async function handleDeleteEvent(id: string) {
 try { await deleteInventoryEvent(id); setEvents(prev => prev.filter(e => e.id !== id)) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_deleting_event')) }
 }

 const summary = data?.summary
 const skusSinStock = data ? data.items.filter(i => !i.has_stock).length : 0
 const skusConForecast = data ? data.items.filter(i => i.has_forecast).length : 0

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

 {/* Header */}
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <div style={{ width: 36, height: 36, borderRadius: 9, background: 'linear-gradient(135deg, #22c55e, #16a34a)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
 <ShoppingCart size={17} color="#fff" strokeWidth={2.5} />
 </div>
 <div>
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>{t('inventory.title')}</h1>
 <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('inventory.subtitle')}</p>
 </div>
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
 {/* Session */}
 {advancedMode ? (
 <SessionBar
 currentSession={currentSession}
 completedSessions={completedSessions}
 sessionId={sessionId}
 onSelect={id => { setSessionId(id); load(id) }}
 onRefresh={() => sessionId ? load(sessionId) : undefined}
 />
 ) : (
 <DataFreshness currentSession={currentSession} />
 )}

 {/* View toggle */}
 <div style={{ display: 'flex', border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
 {([
 ['table', <List size={13} />, t('inventory.view_table')],
 ['simple', <Package size={13} />, t('inventory.view_simple')],
 ['provider', <Layers size={13} />, t('inventory.view_provider')],
 ['update', <PencilLine size={13} />, t('inventory.view_update')],
 ['dead', <Package size={13} />, t('inventory.view_dead')],
 ] as [string, React.ReactNode, string][]).map(([mode, icon, label]) => (
 <button key={mode} onClick={() => setViewMode(mode as 'table' | 'simple' | 'provider' | 'update' | 'dead')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, padding: '6px 11px', fontSize: 11, fontWeight: 500, background: viewMode === mode ? 'var(--accent-dim)' : 'transparent', color: viewMode === mode ? 'var(--accent)' : C.dim }}>
 {icon}{label}
 </button>
 ))}
 </div>

 <button onClick={() => sessionId && load(sessionId)} disabled={loading} title={t('inventory.btn_refresh')} style={{ all: 'unset', cursor: loading ? 'default' : 'pointer', display: 'flex', alignItems: 'center', padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 8, color: C.dim, opacity: loading ? 0.5 : 1 }}><RefreshCw size={13} /></button>
 <input ref={importRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={handleImport} />
 <button onClick={() => importRef.current?.click()} disabled={importing} style={{ all: 'unset', cursor: importing ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted, opacity: importing ? 0.6 : 1 }}>
 {importing ? <Spinner size={12} /> : <Upload size={12} />} CSV
 </button>
 <button onClick={() => downloadInventoryTemplate().catch(err => setError(err instanceof Error ? err.message : String(err)))} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
 <Download size={12} /> {t('inventory.btn_template')}
 </button>
 <button onClick={handleExport} disabled={exporting || !sessionId} style={{ all: 'unset', cursor: exporting || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: C.green, opacity: exporting || !sessionId ? 0.5 : 1 }}>
 {exporting ? <Spinner size={12} /> : <Download size={12} />} {t('inventory.btn_export_po')}
 </button>
 <button onClick={exportEditedPO} disabled={!sessionId} style={{ all: 'unset', cursor: !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, opacity: !sessionId ? 0.5 : 1 }}>
 <Download size={12} /> {t('inventory.btn_export_edited')}
 </button>
 <button onClick={handlePDF} disabled={pdfLoading || !sessionId} title={t('inventory.title_download_pdf')} style={{ all: 'unset', cursor: pdfLoading || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, opacity: pdfLoading || !sessionId ? 0.5 : 1 }}>
 {pdfLoading ? <Spinner size={12} /> : <FileText size={12} />} PDF
 </button>
 <Link href="/inventory/roi" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title={t('inventory.title_view_impact')}>
 <TrendingUp size={12} /> {t('inventory.btn_impact')}
 </Link>
 <Link href="/inventory/suppliers" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title={t('inventory.title_manage_suppliers')}>
 <Truck size={12} /> {t('inventory.btn_suppliers')}
 </Link>
 <button onClick={() => setShowShrinkageModal(true)} title={t('inventory.shrinkage_title_register')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: C.red }}>
 <PackageMinus size={12} /> {t('inventory.shrinkage_btn_register')}
 </button>
 </div>
 </div>

 {/* Secondary failure over an already-rendered screen (e.g. dead-stock view). */}
 {error != null && data != null && (
 <InlineError error={error} onRetry={() => sessionId && load(sessionId)} onDismiss={() => setError(null)} />
 )}

 {/* Excluded SKUs notice — products uploaded but left out of the forecast */}
 {(data?.excluded_skus?.length ?? 0) > 0 && (
 <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Info size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12.5, color: C.text }}>
 <span style={{ fontWeight: 700, color: C.amber }}>
 {data!.excluded_skus!.length} {data!.excluded_skus!.length !== 1 ? t('inventory.excluded_skus_suffix_plural') : t('inventory.excluded_skus_suffix_singular')}
 </span>
 <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
 {data!.excluded_skus!.map(e => (
 <div key={e.sku} style={{ color: C.muted }}>
 <span style={{ fontFamily: 'monospace', fontWeight: 600, color: C.text }}>{e.sku}</span>
 {' — '}{e.detail}
 </div>
 ))}
 </div>
 <div style={{ marginTop: 6, fontSize: 11, color: C.dim }}>
 {t('inventory.excluded_skus_hint')}
 </div>
 </div>
 </div>
 )}

 {/* Upcoming events alert */}
 {upcomingAlerts.length > 0 && (
 <div style={{ padding: '10px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Calendar size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12 }}>
 <span style={{ fontWeight: 600, color: C.amber }}>{t('inventory.upcoming_events_prefix')}</span>
 {upcomingAlerts.map(ev => {
 const d = Math.round((new Date(ev.start_date).getTime() - Date.now()) / 86400000)
 // Clickable: the multiplier must not stay a bare number — opening the
 // simulator shows where it comes from and lets you tune it per product.
 return (
 <button
 key={ev.id}
 onClick={() => sessionId && setSimEvent(ev)}
 disabled={!sessionId}
 title={ev.notes || t('inventory.events_simulate_tooltip')}
 aria-label={`${t('inventory.events_btn_simulate')}: ${ev.name}`}
 style={{ all: 'unset', cursor: sessionId ? 'pointer' : 'default', marginLeft: 8, color: C.text, borderBottom: sessionId ? `1px dotted ${C.dim}` : 'none' }}
 >
 {ev.name} <span style={{ color: C.dim }}>({d === 0 ? t('inventory.day_today') : `${t('inventory.day_in_prefix')} ${d}d`}, ×{ev.multiplier.toFixed(1)})</span>
 </button>
 )
 })}
 <span style={{ marginLeft: 8, color: C.dim, fontSize: 11 }}>{t('inventory.events_multiplier_hint')}</span>
 </div>
 </div>
 )}

 {/* Situación */}
 {summary && <ContextMessage summary={summary} />}

 {/* SKUs sin stock banner */}
 {!loading && sessionId && skusSinStock > 0 && (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 8, background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.2)', color: C.indigo, fontSize: 13 }}>
 <Info size={14} style={{ flexShrink: 0 }} />
 <span><strong>{skusSinStock} {t('inventory.skus_of_label')} {skusConForecast} SKUs</strong> {t('inventory.skus_no_stock_hint')} <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, current_stock, lead_time_days</code></span>
 </div>
 )}

 {/* KPIs — skeleton first so the row does not pop in. */}
 {loading && !summary && <SkeletonCards count={6} height={74} />}
 {summary && (
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
 <KPICard label={t('inventory.kpi_total_skus')} value={summary.total_skus} color={C.indigo} onClick={() => setSignalFilter('')} active={!signalFilter} />
 <KPICard label={t('inventory.signal_order_now')} value={summary.order_now} color={C.red} onClick={() => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA')} active={signalFilter === 'PEDIR_YA'} sub={summary.order_now > 0 ? t('inventory.kpi_immediate_risk') : undefined} />
 <KPICard label={t('inventory.signal_order_soon')} value={summary.order_soon} color={C.amber} onClick={() => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO')} active={signalFilter === 'PEDIR_PRONTO'} />
 <KPICard label={t('inventory.signal_ok')} value={summary.ok} color={C.green} onClick={() => setSignalFilter(signalFilter === 'OK' ? '' : 'OK')} active={signalFilter === 'OK'} />
 <KPICard label={t('inventory.signal_overstock')} value={summary.overstock} color={C.blue} onClick={() => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK')} active={signalFilter === 'SOBRESTOCK'} />
 <KPICard label={t('inventory.kpi_inventory_value')} value={summary.total_inventory_value > 0 ? fmtCurrency(summary.total_inventory_value) : '—'} color={C.indigo} sub={t('inventory.kpi_skus_with_cost')} />
 </div>
 )}

 {/* Warehouse selector (feature 5.4). With 2+ warehouses: full selector;
     mono-warehouse: only the discreet add-warehouse entry point. */}
 {sessionId && (
 <WarehouseSelector
 value={selectedWarehouse}
 onChange={setSelectedWarehouse}
 warehouses={warehouses}
 onSharesChanged={() => { if (sessionId) load(sessionId) }}
 />
 )}

 {/* Per-warehouse semáforo replaces the main table while a warehouse is selected */}
 {selectedWarehouse && sessionId ? (
 <WarehouseStatusTable
 sessionId={sessionId}
 warehouse={selectedWarehouse}
 onTransferCreated={() => load(sessionId)}
 />
 ) : (
 <>
 {/* Main table / view */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>

 {/* Toolbar */}
 <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10, background: C.card }}>
 <input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('inventory.search_placeholder')} style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 7, padding: '6px 12px', fontSize: 12, color: C.text, outline: 'none' }} />
 {search && <button onClick={() => setSearch('')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }}><X size={13} /></button>}
 <span style={{ fontSize: 11, color: C.dim, whiteSpace: 'nowrap' }}>{items.length} SKU{items.length !== 1 ? 's' : ''}</span>
 </div>

 {loading ? (
 <LoadingState label={t('inventory.loading_label')}>
 <SkeletonTable rows={8} columns={6} />
 </LoadingState>
 ) : error && !data ? (
 /* ── Status request failed outright ───────────────────────── */
 <div style={{ padding: '32px 24px' }}>
 <ErrorState error={error} onRetry={() => sessionId && load(sessionId)} />
 </div>
 ) : sessionsError ? (
 /* ── Session list failed to load ──────────────────────────── */
 <div style={{ padding: '40px 32px', textAlign: 'center' }}>
 <AlertTriangle size={32} color={C.red} style={{ margin: '0 auto 12px', opacity: 0.7 }} />
 <div style={{ fontSize: 14, color: C.text, marginBottom: 16, maxWidth: 420, margin: '0 auto 16px' }}>{sessionsError}</div>
 <button onClick={refreshSessions} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', borderRadius: 8, background: C.indigo, color: '#fff', fontSize: 13, fontWeight: 600 }}>
 <RefreshCw size={12} /> {t('inventory.btn_retry')}
 </button>
 </div>
 ) : !sessionId ? (
 /* ── Onboarding empty state ───────────────────────────────── */
 <div style={{ padding: '40px 48px', maxWidth: 520, margin: '0 auto', textAlign: 'center' }}>
 <ShoppingCart size={36} strokeWidth={1} color={C.indigo} style={{ margin: '0 auto 16px', opacity: 0.4 }} />
 <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>{t('inventory.onboarding_welcome')}</div>
 <div style={{ fontSize: 13, color: C.dim, marginBottom: 24, lineHeight: 1.7 }}>{t('inventory.onboarding_desc')}</div>
 <div style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 14 }}>
 {[
 { n: '1', title: t('inventory.onboarding_step1_title'), desc: t('inventory.onboarding_step1_desc') },
 { n: '2', title: t('inventory.onboarding_step2_title'), desc: t('inventory.onboarding_step2_desc') },
 { n: '3', title: t('inventory.onboarding_step3_title'), desc: t('inventory.onboarding_step3_desc') },
 ].map(({ n, title, desc }) => (
 <div key={n} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
 <span style={{ width: 24, height: 24, borderRadius: '50%', flexShrink: 0, background: 'rgba(129,140,248,0.12)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{n}</span>
 <div><div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div><div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.6 }}>{desc}</div></div>
 </div>
 ))}
 </div>
 </div>
 ) : items.length === 0 ? (
 /* Two very different emptinesses: a filter that matched nothing (clear it)
    versus a session with no stock loaded (go load it). */
 <div style={{ padding: '32px 24px' }}>
 {signalFilter || search ? (
 <EmptyState
 compact
 icon={<Search size={20} />}
 title={t('inventory.empty_filtered_title')}
 body={t('inventory.empty_no_filtered_skus')}
 actions={[{
 label: t('inventory.empty_filtered_cta'),
 variant: 'secondary',
 onClick: () => { setSignalFilter(''); setSearch('') },
 }]}
 />
 ) : (
 <EmptyState
 icon={<PackagePlus size={22} />}
 title={t('inventory.empty_stock_title')}
 body={t('inventory.empty_stock_body')}
 actions={[{
 label: t('inventory.empty_stock_cta'),
 icon: <Upload size={14} />,
 onClick: () => setViewMode('update'),
 }]}
 />
 )}
 </div>
 ) : viewMode === 'provider' ? (
 <div style={{ padding: 16 }}>{byProvider.map(([provider, provItems]) => <ProviderGroup key={provider || '__none__'} name={provider} items={provItems} onEdit={startEdit} editedQty={editedQty} editingQtySku={editingQtySku} setEditedQty={setEditedQty} setEditingQtySku={setEditingQtySku} effectiveQty={effectiveQty} coverageUnit={data?.coverage_unit} />)}</div>

 ) : viewMode === 'update' ? (
 /* ── Vista actualización rápida ───────────────────────────── */
 <div>
 {/* CSV import hint */}
 <div style={{ padding: '10px 16px', background: 'rgba(129,140,248,0.04)', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 12, color: C.dim, flex: 1 }}>
 {t('inventory.csv_hint_prefix')} <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, current_stock, lead_time_days</code>
 </span>
 <button onClick={() => importRef.current?.click()} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 7, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
 <Upload size={11} /> {t('inventory.btn_import_csv_arrow')}
 </button>
 </div>
 {/* Action bar */}
 <div style={{ padding: '10px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10 }}>
 <span style={{ fontSize: 12, color: updatedSkus.size > 0 ? C.amber : C.dim, fontWeight: updatedSkus.size > 0 ? 600 : 400, flex: 1 }}>
 {updatedSkus.size === 0 ? t('inventory.no_changes') : `${updatedSkus.size} ${updatedSkus.size !== 1 ? t('inventory.rows_modified_plural') : t('inventory.rows_modified_singular')}`}
 </span>
 <button
 onClick={() => {
 if (data) {
 const draft: Record<string, { current_stock: string; lead_time_days: string; supplier: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = { current_stock: String(item.current_stock ?? ''), lead_time_days: String(item.lead_time_days ?? 15), supplier: item.supplier ?? '' }
 })
 setUpdateDraft(draft)
 setUpdatedSkus(new Set())
 }
 }}
 disabled={updatedSkus.size === 0}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 ? 'default' : 'pointer', padding: '6px 14px', borderRadius: 7, border: `1px solid ${C.border}`, fontSize: 12, color: C.dim, opacity: updatedSkus.size === 0 ? 0.4 : 1 }}
 >
 {t('inventory.btn_discard')}
 </button>
 <button
 onClick={handleSaveAll}
 disabled={updatedSkus.size === 0 || updateSaving}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 || updateSaving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7, fontSize: 12, fontWeight: 600, background: updatedSkus.size > 0 ? C.green : 'rgba(34,197,94,0.2)', color: '#fff', opacity: updatedSkus.size === 0 || updateSaving ? 0.5 : 1 }}
 >
 {updateSaving ? <Spinner size={11} /> : <Save size={11} />}
 {updateSaving ? t('inventory.saving_ellipsis') : `${t('inventory.btn_save_prefix')} ${updatedSkus.size > 0 ? updatedSkus.size : ''} ${updatedSkus.size !== 1 ? t('inventory.changes_plural') : t('inventory.changes_singular')}`}
 </button>
 </div>
 {/* Table */}
 <div style={{ overflowY: 'auto', maxHeight: 500 }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
 <tr style={{ background: C.card }}>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em', width: 8 }} />
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>SKU</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_name')}</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_current_stock')}</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_lead_time_days')}</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>{t('inventory.col_provider')}</th>
 </tr>
 </thead>
 <tbody>
 {items.map((item, idx) => {
 const draft = updateDraft[item.sku]
 const isModified = updatedSkus.has(item.sku)
 const rowBg = idx % 2 === 0 ? C.surface : C.card
 const inputUpd: React.CSSProperties = {
 background: C.surface, border: `1px solid ${C.border}`, borderRadius: 5,
 color: C.text, fontSize: 12, outline: 'none',
 padding: '5px 8px', width: '100%', boxSizing: 'border-box' as const,
 transition: 'border-color 0.15s',
 }
 return (
 <tr key={item.sku} style={{ background: isModified ? 'rgba(245,158,11,0.04)' : rowBg }}>
 {/* Modified indicator */}
 <td style={{ padding: '0 0 0 8px', borderBottom: `1px solid ${C.border}`, width: 8 }}>
 {isModified && <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: C.amber }} />}
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, fontFamily: 'monospace', fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap' }}>
 {item.sku}
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, color: C.muted, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {item.display_name || <span style={{ color: C.dim }}>—</span>}
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 110 }}>
 <input
 style={inputUpd}
 type="number" min={0}
 value={draft?.current_stock ?? ''}
 onChange={e => handleDraftChange(item.sku, 'current_stock', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 onKeyDown={e => {
 if (e.key === 'Enter') {
 e.preventDefault()
 const rows = document.querySelectorAll<HTMLInputElement>('[data-stock-input]')
 const idx2 = Array.from(rows).indexOf(e.currentTarget)
 if (idx2 >= 0 && idx2 < rows.length - 1) rows[idx2 + 1].focus()
 }
 }}
 data-stock-input=""
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 130 }}>
 <input
 style={inputUpd}
 type="number" min={1} max={365}
 value={draft?.lead_time_days ?? ''}
 onChange={e => handleDraftChange(item.sku, 'lead_time_days', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 150 }}>
 <input
 style={inputUpd}
 type="text"
 value={draft?.supplier ?? ''}
 onChange={e => handleDraftChange(item.sku, 'supplier', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 />
 </td>
 </tr>
 )
 })}
 </tbody>
 </table>
 </div>
 </div>

 ) : viewMode === 'simple' ? (
 /* ── Vista simple ─────────────────────────────────────────── */
 <div>
 <div style={{ padding: '10px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'grid', gridTemplateColumns: '1fr 160px 120px 160px', gap: 16, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 <span>{t('inventory.col_sku_product')}</span><span>{t('inventory.col_signal')}</span><span style={{ textAlign: 'right' }}>{t('inventory.col_qty_to_order')}</span><span>{t('inventory.col_provider')}</span>
 </div>
 {items.filter(i => i.signal !== 'OK' && i.signal !== 'SIN_DATOS').concat(items.filter(i => i.signal === 'OK' || i.signal === 'SIN_DATOS')).map((item, idx) => (
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '1fr 160px 120px 160px', gap: 16, padding: '14px 16px', alignItems: 'center', borderBottom: `1px solid ${C.border}`, background: idx % 2 === 0 ? C.surface : C.card, borderLeft: `3px solid ${item.signal === 'PEDIR_YA' || item.signal === 'PEDIR_PRONTO' ? signalColor(item.signal) : 'transparent'}` }}>
 <div>
 <div style={{ fontWeight: 600, fontSize: 13 }}>{item.display_name || item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>}
 </div>
 <SignalBadge s={item.signal} />
 <div style={{ textAlign: 'right' }}>
 {item.recommended_qty != null && item.recommended_qty > 0
 ? <span style={{ fontSize: 18, fontWeight: 800, color: signalColor(item.signal) }}>{fmt(item.recommended_qty, 0)}</span>
 : <span style={{ fontSize: 13, color: C.dim }}>—</span>}
 </div>
 <span style={{ fontSize: 12, color: C.muted }}>{item.supplier || '—'}</span>
 </div>
 ))}
 </div>

 ) : viewMode === 'dead' ? (
 /* ── Inventario inmovilizado ──────────────────────────────── */
 <div style={{ padding: 16 }}>
  {loadingDead ? (
   <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
  ) : !deadStock ? (
   <div style={{ padding: 48, textAlign: 'center', color: C.dim, fontSize: 13 }}>
    {t('inventory.dead_select_session')}
   </div>
  ) : deadStock.sku_count === 0 ? (
   <div style={{ padding: '40px 0', textAlign: 'center' }}>
    <div style={{ fontSize: 14, fontWeight: 600, color: C.green, marginBottom: 8 }}>
     {t('inventory.dead_none_detected')}
    </div>
    <div style={{ fontSize: 13, color: C.dim }}>
     {t('inventory.dead_none_detected_desc')}
    </div>
   </div>
  ) : (
   <>
    {/* Summary bar */}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
     {[
      { label: t('inventory.dead_kpi_skus'), value: deadStock.sku_count, color: C.amber },
      { label: t('inventory.dead_kpi_capital_trapped'),
       value: formatMoneyCompact(deadStock.total_capital_trapped),
       color: C.red },
      { label: t('inventory.dead_kpi_holding_cost'),
       value: formatMoneyCompact(deadStock.total_holding_cost_monthly),
       color: C.amber },
     ].map(({ label, value, color }) => (
      <div key={label} style={{
       background: C.surface, border: `1px solid ${C.border}`,
       borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${color}`,
      }}>
       <div style={{ fontSize: 20, fontWeight: 800, color }}>{value}</div>
       <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{label}</div>
      </div>
     ))}
    </div>

    {/* Items table */}
    <div style={{ borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
     <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
       <tr style={{ background: C.card }}>
        {[t('inventory.dead_col_product'), t('inventory.dead_col_days_stalled'), t('inventory.dead_col_stock'), t('inventory.dead_col_capital_trapped'), t('inventory.dead_col_cost_per_month'), t('inventory.dead_col_category'), t('inventory.dead_col_suggested_action')].map(h => (
         <th key={h} style={{
          padding: '9px 12px', textAlign: 'left',
          color: C.dim, fontWeight: 600, fontSize: 10,
          borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const,
          letterSpacing: '0.06em',
         }}>{h}</th>
        ))}
       </tr>
      </thead>
      <tbody>
       {deadStock.items.map((item, i: number) => (
        <tr key={item.sku} style={{
         background: i % 2 === 0 ? C.surface : C.card,
         borderBottom: `1px solid ${C.border}`,
        }}>
         <td style={{ padding: '10px 12px' }}>
          <div style={{ fontWeight: 600 }}>{item.display_name || item.sku}</div>
          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>
          {item.supplier && <div style={{ fontSize: 10, color: C.muted }}>{item.supplier}</div>}
         </td>
         <td style={{ padding: '10px 12px', color: C.red, fontWeight: 700 }}>
          {item.days_without_movement}d
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.current_stock?.toLocaleString()}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: C.red }}>
          {formatMoneyCompact(item.capital_trapped)}
         </td>
         <td style={{ padding: '10px 12px', color: C.amber, fontSize: 11 }}>
          {formatMoneyCompact(item.holding_cost_monthly)}{t('inventory.unit_per_month_suffix')}
         </td>
         <td style={{ padding: '10px 12px' }}>
          <span style={{
           fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20,
           background: item.abc === 'A' ? 'rgba(34,197,94,0.1)' : item.abc === 'B' ? 'rgba(245,158,11,0.1)' : 'rgba(100,116,139,0.1)',
           color: item.abc === 'A' ? '#22c55e' : item.abc === 'B' ? '#f59e0b' : '#64748b',
          }}>{item.abc}</span>
         </td>
         <td style={{ padding: '10px 12px', fontSize: 11, color: C.muted }}>
          {item.action_suggested}
         </td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>

    <div style={{ marginTop: 12, fontSize: 11, color: C.dim }}>
     {t('inventory.dead_footer_note_1')}
     {t('inventory.dead_footer_note_2')}
    </div>
   </>
  )}
 </div>

 ) : (
 /* ── Tabla completa ───────────────────────────────────────── */
 <div style={{ overflowX: 'auto' }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 <th style={{ padding: '9px 12px', width: 28, borderBottom: `1px solid ${C.border}` }} />
 <ThTip label={t('inventory.col_signal')} tip={t('inventory.tip_signal')} />
 <ThTip label={t('inventory.col_sku_name')} tip={t('inventory.tip_sku_name')} />
 <ThTip label={t('inventory.col_stock')} tip={t('inventory.tip_stock')} />
 <ThTip label={t('inventory.col_trend')} tip={t('inventory.tip_trend')} />
 <ThTip label={t('inventory.col_days_coverage')} tip={t('inventory.tip_days_coverage')} />
 <ThTip label={t('inventory.col_demand_lt')} tip={t('inventory.tip_demand_lt')} />
 <ThTip label={t('inventory.col_qty_to_order')} tip={t('inventory.tip_qty_to_order')} />
 <ThTip label={t('inventory.col_lead_time')} tip={t('inventory.tip_lead_time')} />
 <ThTip label="MOQ" tip={t('inventory.tip_moq')} />
 <ThTip label="ABC-XYZ" tip={t('inventory.tip_abc_xyz')} />
 <ThTip label={t('inventory.col_warehouse_value')} tip={t('inventory.tip_warehouse_value')} />
 <th style={{ padding: '9px 12px', borderBottom: `1px solid ${C.border}` }} />
 </tr>
 </thead>
 <tbody>
 {items.map((item, idx) => {
 const isEditing = editId === item.sku
 const isExpanded = expandedSku === item.sku && !isEditing
 const rowBg = idx % 2 === 0 ? C.surface : C.card
 const crit = item.signal === 'PEDIR_YA'

 if (isEditing && editState) return (
 <tr key={item.sku} style={{ background: 'rgba(129,140,248,0.04)' }}>
 <td style={{ padding: '8px 6px', borderBottom: `1px solid ${C.border}` }} />
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}><SignalBadge s={item.signal} /></td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ fontWeight: 600, fontFamily: 'monospace', marginBottom: 4, fontSize: 11 }}>{item.sku}</div>
 <input style={inputS} placeholder={t('inventory.edit_display_name_placeholder')} value={editState.display_name} onChange={e => setEditState(s => s ? { ...s, display_name: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_display_name_hint')}</div>
 <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
 <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_category')} value={editState.category} onChange={e => setEditState(s => s ? { ...s, category: e.target.value } : s)} />
 <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_brand')} value={editState.brand} onChange={e => setEditState(s => s ? { ...s, brand: e.target.value } : s)} />
 <input style={{ ...inputS, width: 70 }} placeholder={t('inventory.edit_unit')} value={editState.unit_of_measure} onChange={e => setEditState(s => s ? { ...s, unit_of_measure: e.target.value } : s)} />
 <input style={{ ...inputS, width: 120 }} placeholder={t('inventory.edit_barcode')} value={editState.barcode} onChange={e => setEditState(s => s ? { ...s, barcode: e.target.value } : s)} />
 </div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} value={editState.current_stock} onChange={e => setEditState(s => s ? { ...s, current_stock: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_stock_hint')}</div>
 </td>
 <td colSpan={2} style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}>{t('inventory.edit_recalculated_on_save')}</td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <select
 style={{ ...inputS, width: 160 }}
 value={editState.supplier}
 onChange={e => setEditState(s => s ? { ...s, supplier: e.target.value } : s)}
 >
 <option value="">{t('inventory.edit_no_provider_option')}</option>
 {suppliers.map(s => (
 <option key={s.id} value={s.name}>{s.name}</option>
 ))}
 </select>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 60 }} type="number" min={1} max={365} value={editState.lead_time_days} onChange={e => setEditState(s => s ? { ...s, lead_time_days: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_provider_days_hint')}</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 70 }} type="number" min={0} value={editState.moq} onChange={e => setEditState(s => s ? { ...s, moq: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
 {t('inventory.edit_min_per_order')}
 <HelpTip text={t('inventory.help_moq')} width={240} />
 </div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder="0" value={editState.unit_cost} onChange={e => setEditState(s => s ? { ...s, unit_cost: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_provider_price_hint')}</div>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 4 }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder={t('inventory.edit_sale_price')} value={editState.sale_price} onChange={e => setEditState(s => s ? { ...s, sale_price: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 4, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
 {t('inventory.edit_service_level_label')}
 <HelpTip text={t('inventory.help_service_level')} width={260} />
 </div>
 <select style={{ ...inputS, width: 90 }} value={editState.service_level}
 onChange={e => setEditState(s => s ? { ...s, service_level: e.target.value } : s)}>
 <option value="0.90">90% — {t('inventory.service_level_low')}</option>
 <option value="0.95">95% — {t('inventory.service_level_normal')}</option>
 <option value="0.97">97% — {t('inventory.service_level_high')}</option>
 <option value="0.99">99% — {t('inventory.service_level_max')}</option>
 </select>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 <button onClick={() => commitEdit(item.sku)} disabled={saving} style={{ all: 'unset', cursor: saving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: C.green, color: '#fff', opacity: saving ? 0.6 : 1 }}>
 {saving ? <Spinner size={10} /> : <Save size={10} />} {t('inventory.btn_save')}
 </button>
 <button onClick={cancelEdit} aria-label={t('common.cancel')} title={t('common.cancel')} style={{ all: 'unset', cursor: 'pointer', padding: '5px 8px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}><X size={11} aria-hidden="true" /></button>
 </div>
 </td>
 </tr>
 )

 return (
 <>
 <tr key={item.sku}
 style={{ background: crit ? 'rgba(239,68,68,0.02)' : rowBg, borderLeft: `3px solid ${crit ? C.red : 'transparent'}`, transition: 'background 0.1s' }}
 onMouseEnter={e => (e.currentTarget.style.background = 'rgba(129,140,248,0.04)')}
 onMouseLeave={e => (e.currentTarget.style.background = crit ? 'rgba(239,68,68,0.02)' : rowBg)}
 >
 {/* Expand button */}
 <td style={{ padding: '10px 6px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.calc_explanation && (
 <button
 onClick={() => setExpandedSku(isExpanded ? null : item.sku)}
 title={t('inventory.title_see_calculation')}
 style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }}
 onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
 onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
 >
 <ChevronRight size={12} style={{ transform: isExpanded ? 'rotate(90deg)' : undefined, transition: 'transform 0.15s' }} />
 </button>
 )}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><SignalBadge s={item.signal} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: 11 }}>{item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.muted, marginTop: 1 }}>{item.display_name}</div>}
 {item.supplier && <div style={{ fontSize: 10, color: C.dim, marginTop: 1 }}>{item.supplier}</div>}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.has_stock ? <span style={{ fontWeight: 600 }}>{fmt(item.current_stock, 0)}</span> : <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.no_record')}</span>}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <Sparkline data={item.stock_history} />
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.coverage_days != null ? (
 <span style={{ fontWeight: 600, color: signalColor(item.signal) }}>
 {fmt(item.coverage_days, 0)} {coverageUnitShort(data?.coverage_unit, t)}
 </span>
 ) : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted, fontFamily: 'monospace', fontSize: 11 }}>{fmt(item.lead_time_demand, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.recommended_qty != null && item.recommended_qty > 0 ? (
 editingQtySku === item.sku ? (
 <input
 type="number" min={1} autoFocus
 defaultValue={effectiveQty(item)}
 onBlur={e => {
 const n = parseInt(e.target.value, 10)
 setEditedQty(prev => (!isNaN(n) && n > 0 ? { ...prev, [item.sku]: n } : prev))
 setEditingQtySku(null)
 }}
 onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
 style={{ width: 70, background: C.card, border: `1px solid ${C.indigo}`, borderRadius: 5, color: C.text, fontSize: 13, fontWeight: 700, padding: '3px 6px', outline: 'none' }}
 />
 ) : (
 <button
 onClick={() => setEditingQtySku(item.sku)}
 title={t('inventory.edit_qty_title')}
 style={{ all: 'unset', cursor: 'pointer', fontWeight: 700, fontSize: 13,
 color: editedQty[item.sku] != null ? C.indigo : C.green,
 borderBottom: `2px dashed ${(editedQty[item.sku] != null ? C.indigo : C.green)}60`, lineHeight: 1 }}
 >
 {fmt(effectiveQty(item), 0)}
 </button>
 )
 ) : item.recommended_qty === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{item.lead_time_days}d</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{fmt(item.moq, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, fontFamily: 'monospace', fontSize: 11, color: C.muted }}>{item.inventory_value != null ? fmtCurrency(item.inventory_value) : '—'}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 {item.calc_explanation && item.daily_demand && (
  <button onClick={() => setExpandedSku(isExpanded ? null : item.sku)} title={t('inventory.title_simulate_scenarios')}
   style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: isExpanded ? C.indigo : C.dim, display: 'flex' }}
   onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
   onMouseLeave={e => (e.currentTarget.style.color = isExpanded ? C.indigo : C.dim)}>
   <Sliders size={13} />
  </button>
 )}
 <button onClick={() => startEdit(item)} title={t('inventory.title_edit')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={13} /></button>
 {item.has_stock && <button onClick={() => handleDelete(item.sku)} title={t('inventory.title_delete')} style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Trash2 size={13} /></button>}
 </div>
 </td>
 </tr>
 {/* Expanded explanation row */}
 {isExpanded && item.calc_explanation && (
 <tr key={`${item.sku}-exp`}>
 <td colSpan={13} style={{ padding: '0 16px 12px 48px', borderBottom: `1px solid ${C.border}`, background: crit ? 'rgba(239,68,68,0.01)' : rowBg }}>
 <CalcExplainer exp={item.calc_explanation} moq={item.moq} />
 <SimulatorPanel item={item} />
 </td>
 </tr>
 )}
 </>
 )
 })}
 </tbody>
 </table>
 </div>
 )}
 </div>
 </>
 )}

 {/* Events panel */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
 <button
 onClick={() => setShowEvents(v => !v)}
 style={{ all: 'unset', cursor: 'pointer', width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '14px 20px', boxSizing: 'border-box' }}
 >
 <Calendar size={14} color={C.indigo} />
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{t('inventory.events_section_title')}</span>
 {upcomingAlerts.length > 0 && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{upcomingAlerts.length} {upcomingAlerts.length > 1 ? t('inventory.upcoming_plural') : t('inventory.upcoming_singular')}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: showEvents ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </button>
 {showEvents && (
 <div style={{ padding: '0 20px 20px', borderTop: `1px solid ${C.border}` }}>
 <div style={{ fontSize: 12, color: C.dim, marginBottom: 14, marginTop: 12, lineHeight: 1.6 }}>
 {t('inventory.events_section_desc')}
 </div>
 <EventsPanel events={events} onAdd={handleAddEvent} onDelete={handleDeleteEvent} onSimulate={setSimEvent} onCatalogChange={reloadEvents} />
 </div>
 )}
 </div>

 {simEvent && sessionId && (
 <EventSimModal ev={simEvent} sessionId={sessionId} onClose={() => setSimEvent(null)} onReload={reloadEvents} />
 )}

 {showShrinkageModal && (
 <ShrinkageModal
  items={data?.items ?? []}
  onClose={() => setShowShrinkageModal(false)}
  onSaved={() => { if (sessionId) load(sessionId) }}
 />
 )}

 {deleteTarget && (
 <div onClick={() => setDeleteTarget(null)} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
 <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 400, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
 <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>
 {t('inventory.confirm_delete_prefix')} {deleteTarget}?
 </div>
 <p style={{ fontSize: 12, color: C.dim, margin: '0 0 18px', lineHeight: 1.5 }}>{t('inventory.confirm_delete_hint')}</p>
 <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
 <button onClick={() => setDeleteTarget(null)} style={{ all: 'unset', cursor: 'pointer', padding: '8px 16px', borderRadius: 8, border: `1px solid ${C.border}`, color: C.dim, fontSize: 13 }}>{t('common.cancel')}</button>
 <button onClick={confirmDelete} style={{ all: 'unset', cursor: 'pointer', padding: '8px 16px', borderRadius: 8, background: C.red, color: '#fff', fontSize: 13, fontWeight: 700 }}>{t('inventory.btn_delete_confirm')}</button>
 </div>
 </div>
 </div>
 )}

 {/* Legend */}
 {!loading && sessionId && (
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 20, fontSize: 11, color: C.dim, paddingBottom: 8, flexWrap: 'wrap' }}>
 {[
 { signal: t('inventory.signal_order_now'), desc: t('inventory.legend_order_now') },
 { signal: t('inventory.signal_order_soon'), desc: t('inventory.legend_order_soon') },
 { signal: t('inventory.signal_ok'), desc: t('inventory.legend_ok') },
 { signal: t('inventory.signal_overstock'), desc: t('inventory.legend_overstock') },
 { signal: t('inventory.signal_sin_datos'), desc: t('inventory.legend_sin_datos') },
 ].map(({ signal, desc }) => <span key={signal}><strong>{signal}</strong> — {desc}</span>)}
 </div>
 )}
 </div>
 )
}

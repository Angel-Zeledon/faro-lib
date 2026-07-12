'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Link from 'next/link'
import {
 getInventoryStatus, upsertInventoryStock, deleteInventoryStock,
 importInventoryCSV, exportInventoryPO, downloadInventoryPDF,
 listInventoryEvents, createInventoryEvent, updateInventoryEvent, deleteInventoryEvent,
 listSuppliers, getDeadStock, simulateEvent, logPOGeneration, downloadInventoryTemplate,
} from '@/lib/api'
import type {
 InventoryStatusItem, InventorySignal,
 InventoryCalcExplanation, InventoryEvent, Supplier, DeadStockResponse, ExcludedSku,
 EventSimulationResult, POLineDecision,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import Spinner from '@/components/ui/Spinner'
import HelpTip from '@/components/ui/HelpTip'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'
import {
 ShoppingCart, AlertTriangle, CheckCircle2, TrendingDown, TrendingUp,
 ChevronDown, ChevronRight, RefreshCw, Upload, Download, Edit2, Trash2,
 X, Save, Package, Info, Layers, List, FileText, Calendar, Plus, PencilLine, Truck, Sliders,
 Zap,
} from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#ef4444', amber: '#f59e0b', green: '#22c55e', blue: '#3b82f6', indigo: '#818cf8',
}

// ── Signal config ─────────────────────────────────────────────────────────────
const SIGNAL_CFG: Record<InventorySignal, { labelKey: string; color: string; bg: string; icon: React.ElementType }> = {
 PEDIR_YA: { labelKey: 'inventory.signal_pedir_ya', color: '#ef4444', bg: 'rgba(239,68,68,0.1)', icon: AlertTriangle },
 PEDIR_PRONTO: { labelKey: 'inventory.signal_pedir_pronto', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', icon: AlertTriangle },
 OK: { labelKey: 'inventory.signal_ok', color: '#22c55e', bg: 'rgba(34,197,94,0.1)', icon: CheckCircle2 },
 SOBRESTOCK: { labelKey: 'inventory.signal_sobrestock', color: '#3b82f6', bg: 'rgba(59,130,246,0.1)', icon: TrendingDown },
 SIN_DATOS: { labelKey: 'inventory.signal_sin_datos', color: '#64748b', bg: 'rgba(100,116,139,0.1)', icon: Info },
}

function SignalBadge({ s }: { s: InventorySignal }) {
 const { t } = useLanguage()
 const cfg = SIGNAL_CFG[s]; const Icon = cfg.icon
 return (
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: cfg.bg, color: cfg.color }}>
 <Icon size={10} /> {t(cfg.labelKey)}
 </span>
 )
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

// ── ¿Por qué me recomienda esto? ─────────────────────────────────────────────
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
 const steps = [
 { label: t('inventory.calc_step_avg_daily_sales'), value: `${exp.demanda_diaria!.toFixed(1)} ${t('inventory.calc_unit_per_day')}`, op: null },
 { label: `× ${t('inventory.calc_step_lead_days')} (${exp.lead_time_dias}d)`, value: `= ${exp.demanda_lead_time!.toFixed(0)} ${unitWord}`, op: '×' },
 { label: `+ ${t('inventory.calc_step_safety_stock')}`, value: `+ ${exp.safety_stock!.toFixed(0)} ${unitWord}`, op: '+' },
 { label: `− ${t('inventory.calc_step_current_stock')}`, value: `− ${exp.stock_actual!.toFixed(0)} ${unitWord}`, op: '−' },
 { label: `= ${t('inventory.calc_step_before_rounding')}`, value: `${exp.antes_moq!.toFixed(0)} ${unitWord}`, op: '=' },
 ...(moq > 1
 ? [{ label: `↑ ${t('inventory.calc_step_rounded_moq')} (${moq})`, value: `→ ${exp.cantidad_final!.toFixed(0)} ${unitWord}`, op: '↑' }]
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

// ── Situación en lenguaje natural ─────────────────────────────────────────────
function ContextMessage({ summary }: { summary: Record<string, number> }) {
 const { t } = useLanguage()
 const lines: { text: string; color: string }[] = []
 if (summary.pedir_ya > 0)
 lines.push({ text: `${summary.pedir_ya} ${t('inventory.ctx_pedir_ya_suffix')}`, color: '#ef4444' })
 if (summary.pedir_pronto > 0)
 lines.push({ text: `${summary.pedir_pronto} ${t('inventory.ctx_pedir_pronto_suffix')}`, color: '#f59e0b' })
 if (summary.sobrestock > 0)
 lines.push({ text: `${summary.sobrestock} ${t('inventory.ctx_sobrestock_suffix')}`, color: '#3b82f6' })
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

// ── Event impact simulator modal (feature 2.3) ───────────────────────────────
function EventSimModal({ ev, sessionId, onClose }: {
 ev: InventoryEvent
 sessionId: string
 onClose: () => void
}) {
 const [result, setResult] = useState<EventSimulationResult | null>(null)
 const [error, setError] = useState<string | null>(null)

 useEffect(() => {
 simulateEvent({ session_id: sessionId, event_id: ev.id })
 .then(setResult)
 .catch(e => setError(e instanceof Error ? e.message : 'Error al simular'))
 }, [ev.id, sessionId])

 const fmtD = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString('es', { day: 'numeric', month: 'long' })
 const pctExtra = Math.round((ev.multiplier - 1) * 100)

 return (
 <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
 <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxWidth: 640, maxHeight: '85vh', overflowY: 'auto', background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: 24 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
 <Zap size={16} color={C.amber} />
 <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>Simulación: {ev.name}</span>
 <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={16} /></button>
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
 background: result.summary.skus_en_riesgo > 0 ? 'rgba(245,158,11,0.07)' : 'rgba(34,197,94,0.07)',
 border: `1px solid ${result.summary.skus_en_riesgo > 0 ? 'rgba(245,158,11,0.3)' : 'rgba(34,197,94,0.3)'}`,
 fontSize: 13, color: C.text, lineHeight: 1.6,
 }}>
 {result.summary.skus_en_riesgo > 0 ? (
 <>
 Con <strong>{ev.name}</strong> (+{pctExtra}% demanda), necesitarías pedir{' '}
 <strong>{result.summary.total_pedir.toLocaleString()} unidades extra</strong> en{' '}
 <strong>{result.summary.skus_en_riesgo} producto{result.summary.skus_en_riesgo !== 1 ? 's' : ''}</strong>
 {result.summary.pedir_antes_de && <> antes del <strong>{fmtD(result.summary.pedir_antes_de)}</strong></>}
 {result.summary.valor_total_pedido > 0 && <> (≈ ${result.summary.valor_total_pedido.toLocaleString(undefined, { maximumFractionDigits: 0 })})</>}.
 {result.summary.algun_pedido_tarde && (
 <div style={{ color: C.red, fontWeight: 600, marginTop: 6 }}>
 ⚠ Para algunos productos ya es tarde: pidiendo hoy, el pedido llegaría con el evento en curso.
 </div>
 )}
 </>
 ) : (
 <>✓ Tu stock actual resiste <strong>{ev.name}</strong> (+{pctExtra}% demanda) sin pedidos adicionales.</>
 )}
 </div>

 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr>
 {['Producto', 'Demanda evento', 'Stock al inicio', 'Faltante', 'Pedir', 'Pedir antes de'].map(h => (
 <th key={h} style={{ textAlign: 'left', padding: '6px 8px', color: C.dim, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: `1px solid ${C.border}` }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {result.items.map(r => (
 <tr key={r.sku} style={{ borderBottom: `1px solid ${C.border}`, background: r.en_riesgo ? 'rgba(245,158,11,0.04)' : undefined }}>
 <td style={{ padding: '8px' }}>
 <div style={{ fontWeight: 600, color: C.text }}>{r.display_name || r.sku}</div>
 <div style={{ fontSize: 10, color: C.dim, fontFamily: 'monospace' }}>{r.sku}</div>
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.text }}>
 {r.event_units.toLocaleString()}
 <span style={{ color: C.dim, fontSize: 10 }}> (+{r.extra_units.toLocaleString()})</span>
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', color: C.muted }}>
 {r.stock_al_inicio != null ? r.stock_al_inicio.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.en_riesgo ? C.red : C.green }}>
 {r.deficit != null ? (r.deficit > 0 ? r.deficit.toLocaleString() : '✓') : '—'}
 </td>
 <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 700, color: r.cantidad_pedir ? C.text : C.dim }}>
 {r.cantidad_pedir ? r.cantidad_pedir.toLocaleString() : '—'}
 </td>
 <td style={{ padding: '8px', fontSize: 11, color: r.llega_tarde && r.en_riesgo ? C.red : C.muted, whiteSpace: 'nowrap' }}>
 {r.en_riesgo ? (r.llega_tarde ? '¡hoy mismo!' : fmtD(r.order_by)) : '—'}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 <p style={{ margin: '14px 0 0', fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
 Cálculo: demanda diaria del forecast × {result.event_days} día{result.event_days !== 1 ? 's' : ''} × {ev.multiplier.toFixed(1)},
 contra el stock proyectado al inicio del evento. Las cantidades respetan el MOQ de cada producto. Nada se guarda — es solo una simulación.
 </p>
 </>
 )}
 </div>
 </div>
 )
}

// ── Events panel ─────────────────────────────────────────────────────────────
function EventsPanel({ events, onAdd, onDelete, onSimulate }: {
 events: InventoryEvent[]
 onAdd: (ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) => void
 onDelete: (id: string) => void
 onSimulate: (ev: InventoryEvent) => void
}) {
 const { t } = useLanguage()
 const [adding, setAdding] = useState(false)
 const [form, setForm] = useState({ name: '', start_date: '', end_date: '', multiplier: '1.5', notes: '' })

 const upcoming = events.filter(e => new Date(e.end_date) >= new Date())
 const past = events.filter(e => new Date(e.end_date) < new Date())

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

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
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
 {new Date(ev.start_date).toLocaleDateString('es', { day: 'numeric', month: 'short' })}
 {ev.end_date !== ev.start_date && ` → ${new Date(ev.end_date).toLocaleDateString('es', { day: 'numeric', month: 'short' })}`}
 {until && <span style={{ marginLeft: 8, color: isClose ? C.amber : C.dim }}>({until})</span>}
 </div>
 </div>
 <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 20, background: 'rgba(129,140,248,0.1)', color: C.indigo, flexShrink: 0 }}>
 ×{ev.multiplier.toFixed(1)}
 </span>
 <button
 onClick={() => onSimulate(ev)}
 title="Simular impacto en el inventario"
 style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: `1px solid rgba(245,158,11,0.4)`, color: C.amber, fontSize: 11, fontWeight: 600, flexShrink: 0 }}
 >
 <Zap size={11} /> Simular
 </button>
 <button onClick={() => onDelete(ev.id)} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}>
 <Trash2 size={12} />
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
 </div>
 )
}

// ── Inline edit state ─────────────────────────────────────────────────────────
interface EditState { stock_actual: string; lead_time_dias: string; costo_unitario: string; moq: string; proveedor: string; display_name: string; service_level: string; precio_venta: string; categoria: string; marca: string; unidad_medida: string; codigo_barras: string }
function rowToEdit(item: InventoryStatusItem): EditState {
 return { stock_actual: String(item.stock_actual ?? ''), lead_time_dias: String(item.lead_time_dias ?? 15), costo_unitario: String(item.costo_unitario ?? ''), moq: String(item.moq ?? 1), proveedor: item.proveedor ?? '', display_name: item.display_name ?? '', service_level: String(item.service_level ?? 0.95), precio_venta: String(item.precio_venta ?? ''), categoria: item.categoria ?? '', marca: item.marca ?? '', unidad_medida: item.unidad_medida ?? '', codigo_barras: item.codigo_barras ?? '' }
}
const inputS: React.CSSProperties = { background: 'var(--surface-2)', border: `1px solid var(--border)`, borderRadius: 5, color: 'var(--text)', fontSize: 12, outline: 'none', padding: '3px 7px', width: '100%', boxSizing: 'border-box' }

// ── Provider group ────────────────────────────────────────────────────────────
function ProviderGroup({ name, items, onEdit, editedQty, editingQtySku, setEditedQty, setEditingQtySku, effectiveQty }: {
 name: string; items: InventoryStatusItem[]; onEdit: (item: InventoryStatusItem) => void
 editedQty: Record<string, number>
 editingQtySku: string | null
 setEditedQty: React.Dispatch<React.SetStateAction<Record<string, number>>>
 setEditingQtySku: React.Dispatch<React.SetStateAction<string | null>>
 effectiveQty: (item: InventoryStatusItem) => number
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
 <span style={{ color: item.signal === 'PEDIR_YA' ? C.red : item.signal === 'PEDIR_PRONTO' ? C.amber : C.green, fontWeight: 600 }}>{item.dias_cobertura != null ? `${item.dias_cobertura.toFixed(0)}d` : '—'}</span>
 <span>
 {item.cantidad_recomendada != null && item.cantidad_recomendada > 0 ? (
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
 ) : item.cantidad_recomendada === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : <span style={{ color: C.dim }}>—</span>}
 </span>
 <span style={{ color: C.dim }}>{item.stock_actual?.toFixed(0) ?? '—'}</span>
 <AbcXyzBadge value={item.abc_xyz} />
 <button onClick={() => onEdit(item)} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={12} /></button>
 </div>
 ))}
 </div>
 )
}

function fmt(n: number | null | undefined, d = 1) { if (n == null) return '—'; return n.toLocaleString(undefined, { maximumFractionDigits: d }) }
function fmtCurrency(n: number | null | undefined) { if (n == null) return '—'; return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 }) }

// ── Simulator helpers ─────────────────────────────────────────────────────────
function simulateRecommendation(
 stockActual: number,
 demandaDiaria: number,
 avgStd: number,
 leadTime: number,
 moq: number,
 serviceLevel = 0.95,
): number {
 const Z_MAP: Record<number, number> = { 0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326 }
 const z = Z_MAP[serviceLevel] ?? 1.645
 const demandaLT = demandaDiaria * leadTime
 const safetyStock = z * avgStd * Math.sqrt(leadTime)
 const raw = Math.max(0, demandaLT + safetyStock - stockActual)
 if (moq > 0) return Math.ceil(raw / moq) * moq
 return Math.round(raw)
}

function SimulatorPanel({ item }: { item: InventoryStatusItem }) {
 const { t } = useLanguage()
 const exp = item.calc_explanation
 if (!exp || !item.demanda_diaria || item.demanda_diaria <= 0) return null

 const [ltDelta,    setLtDelta]    = useState(0)
 const [demandMult, setDemandMult] = useState(100)
 const [stockDelta, setStockDelta] = useState(0)

 const simLeadTime = Math.max(1, (item.lead_time_dias ?? 15) + ltDelta)
 const simDemand   = (item.demanda_diaria ?? 0) * demandMult / 100
 const simStock    = Math.max(0, (item.stock_actual ?? 0) + stockDelta)

 // Approximate avgStd from safety stock and original lead_time
 // safety_stock = z * avgStd * sqrt(lead_time) → avgStd ≈ safety_stock / (z * sqrt(lead_time))
 const origLT = item.lead_time_dias ?? 15
 const origSS = exp.safety_stock ?? 0
 const z      = 1.645
 const avgStd = origLT > 0 ? origSS / (z * Math.sqrt(origLT)) : 0

 const simRecommended = simulateRecommendation(simStock, simDemand, avgStd, simLeadTime, item.moq ?? 1)
 const originalRec    = item.cantidad_recomendada ?? 0
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
     <input type="range" min={-(item.stock_actual ?? 0)} max={(item.stock_actual ?? 0) * 2}
      step={Math.max(1, Math.floor((item.stock_actual ?? 50) / 10))}
      value={stockDelta}
      onChange={e => setStockDelta(Number(e.target.value))} style={sliderS} />
     <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim }}>
      <span>-{item.stock_actual ?? 0}</span><span>+{(item.stock_actual ?? 0) * 2}</span>
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
 const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
 const { sessionId, setSessionId, currentSession, completedSessions, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 const [data, setData] = useState<{ items: InventoryStatusItem[]; summary: Record<string, number>; excluded_skus?: ExcludedSku[] } | null>(null)
 const [loading, setLoading] = useState(false)
 const [error, setError] = useState<string | null>(null)
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
 const [updateDraft, setUpdateDraft] = useState<Record<string, { stock_actual: string; lead_time_dias: string; proveedor: string }>>({})
 const [updateSaving, setUpdateSaving] = useState(false)
 const [updatedSkus, setUpdatedSkus] = useState<Set<string>>(new Set())
 const [suppliers, setSuppliers] = useState<Supplier[]>([])
 const importRef = useRef<HTMLInputElement>(null)
 const savingRef = useRef(false)
 const [deadStock,   setDeadStock]   = useState<DeadStockResponse | null>(null)
 const [loadingDead, setLoadingDead] = useState(false)
 const [editedQty, setEditedQty] = useState<Record<string, number>>({})
 const [editingQtySku, setEditingQtySku] = useState<string | null>(null)

 // Effective order quantity for an item: the buyer's edit if present, else the recommendation.
 const effectiveQty = useCallback((item: InventoryStatusItem): number =>
  editedQty[item.sku] ?? item.cantidad_recomendada ?? 0, [editedQty])

 useEffect(() => {
 listInventoryEvents().then(setEvents).catch(() => {})
 listSuppliers().then(setSuppliers).catch(() => {})
 }, [])

 const load = useCallback(async (sid: string) => {
 if (!sid) return
 setLoading(true); setError(null)
 setEditedQty({})
 setEditingQtySku(null)
 try { setData(await getInventoryStatus(sid)) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('inventory.err_loading')) }
 finally { setLoading(false) }
 }, [t])

 useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

 // ── Dead stock load ────────────────────────────────────────────────────────
 useEffect(() => {
 if (viewMode !== 'dead' || !sessionId) return
 setLoadingDead(true)
 getDeadStock(sessionId)
  .then(setDeadStock)
  .catch((e: unknown) => { setError(e instanceof Error ? e.message : t('inventory.err_loading_dead_stock')) })
  .finally(() => setLoadingDead(false))
 }, [viewMode, sessionId])

 // ── Update-draft initialization ────────────────────────────────────────────
 useEffect(() => {
 if (viewMode === 'update' && data) {
 const draft: Record<string, { stock_actual: string; lead_time_dias: string; proveedor: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = {
 stock_actual: String(item.stock_actual ?? ''),
 lead_time_dias: String(item.lead_time_dias ?? 15),
 proveedor: item.proveedor ?? '',
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
 parseFloat(draft.stock_actual) !== (original.stock_actual ?? 0) ||
 parseInt(draft.lead_time_dias) !== original.lead_time_dias ||
 draft.proveedor !== (original.proveedor ?? '')
 )
 })
 let saved = 0
 const failed: string[] = []
 for (const [sku, draft] of toSave) {
 try {
 await upsertInventoryStock(sku, {
 stock_actual: parseFloat(draft.stock_actual) || 0,
 lead_time_dias: parseInt(draft.lead_time_dias) || 15,
 proveedor: draft.proveedor || undefined,
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
 if (search) { const q = search.toLowerCase(); return item.sku.toLowerCase().includes(q) || (item.display_name ?? '').toLowerCase().includes(q) || (item.proveedor ?? '').toLowerCase().includes(q) }
 return true
 }), [data, signalFilter, search])

 const byProvider = useMemo(() => {
 const groups: Record<string, InventoryStatusItem[]> = {}
 for (const item of items) { const k = item.proveedor || ''; if (!groups[k]) groups[k] = []; groups[k].push(item) }
 const PRIO = ['PEDIR_YA', 'PEDIR_PRONTO', 'OK', 'SOBRESTOCK', 'SIN_DATOS']
 return Object.entries(groups).sort((a, b) => {
 const sa = Math.min(...a[1].map(i => PRIO.indexOf(i.signal)))
 const sb = Math.min(...b[1].map(i => PRIO.indexOf(i.signal)))
 return sa - sb || a[0].localeCompare(b[0])
 })
 }, [items])

 // Upcoming events within 30 days
 const upcomingAlerts = useMemo(() => events.filter(e => {
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
 await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, stock_actual: parseFloat(editState.stock_actual) || 0, lead_time_dias: parseInt(editState.lead_time_dias) || 15, costo_unitario: editState.costo_unitario ? parseFloat(editState.costo_unitario) : undefined, moq: parseFloat(editState.moq) || 1, proveedor: editState.proveedor || undefined, service_level: parseFloat(editState.service_level) || 0.95, precio_venta: editState.precio_venta ? parseFloat(editState.precio_venta) : undefined, categoria: editState.categoria || undefined, marca: editState.marca || undefined, unidad_medida: editState.unidad_medida || undefined, codigo_barras: editState.codigo_barras || undefined })
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
  const val = qty * (i.costo_unitario ?? 0)
  rows.push(`${i.sku},"${i.display_name ?? ''}",${qty},"${i.proveedor ?? ''}",${val}`)
 }
 const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
 const url = URL.createObjectURL(blob)
 const a = document.createElement('a'); a.href = url; a.download = 'orden_de_compra.csv'; a.click()
 URL.revokeObjectURL(url)
 // Log decisions (edited => 'modified', otherwise 'approved')
 const decisions: POLineDecision[] = orderItems.map(i => ({
  sku: i.sku,
  display_name: i.display_name ?? undefined,
  proveedor: i.proveedor ?? undefined,
  signal: i.signal,
  cantidad_recomendada: i.cantidad_recomendada ?? 0,
  cantidad_final: effectiveQty(i),
  status: (editedQty[i.sku] != null ? 'modified' : 'approved') as 'approved' | 'modified',
  costo_unitario: i.costo_unitario ?? undefined,
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
 <SessionBar
 currentSession={currentSession}
 completedSessions={completedSessions}
 sessionId={sessionId}
 onSelect={id => { setSessionId(id); load(id) }}
 onRefresh={() => sessionId ? load(sessionId) : undefined}
 />

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
 </div>
 </div>

 {/* Error */}
 {error && (
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', fontSize: 13, color: C.red }}>
 <AlertTriangle size={13} style={{ flexShrink: 0 }} />{error}
 <button onClick={() => setError(null)} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={13} /></button>
 </div>
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
 return (
 <span key={ev.id} style={{ marginLeft: 8, color: C.text }}>
 {ev.name} <span style={{ color: C.dim }}>({d === 0 ? t('inventory.day_today') : `${t('inventory.day_in_prefix')} ${d}d`}, ×{ev.multiplier.toFixed(1)})</span>
 </span>
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
 <span><strong>{skusSinStock} {t('inventory.skus_of_label')} {skusConForecast} SKUs</strong> {t('inventory.skus_no_stock_hint')} <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, stock_actual, lead_time_dias</code></span>
 </div>
 )}

 {/* KPIs */}
 {summary && (
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
 <KPICard label={t('inventory.kpi_total_skus')} value={summary.total_skus} color={C.indigo} onClick={() => setSignalFilter('')} active={!signalFilter} />
 <KPICard label={t('inventory.signal_pedir_ya')} value={summary.pedir_ya} color={C.red} onClick={() => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA')} active={signalFilter === 'PEDIR_YA'} sub={summary.pedir_ya > 0 ? t('inventory.kpi_immediate_risk') : undefined} />
 <KPICard label={t('inventory.signal_pedir_pronto')} value={summary.pedir_pronto} color={C.amber} onClick={() => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO')} active={signalFilter === 'PEDIR_PRONTO'} />
 <KPICard label={t('inventory.signal_ok')} value={summary.ok} color={C.green} onClick={() => setSignalFilter(signalFilter === 'OK' ? '' : 'OK')} active={signalFilter === 'OK'} />
 <KPICard label={t('inventory.signal_sobrestock')} value={summary.sobrestock} color={C.blue} onClick={() => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK')} active={signalFilter === 'SOBRESTOCK'} />
 <KPICard label={t('inventory.kpi_inventory_value')} value={summary.valor_total_inventario > 0 ? fmtCurrency(summary.valor_total_inventario) : '—'} color={C.indigo} sub={t('inventory.kpi_skus_with_cost')} />
 </div>
 )}

 {/* Main table / view */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>

 {/* Toolbar */}
 <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10, background: C.card }}>
 <input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('inventory.search_placeholder')} style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 7, padding: '6px 12px', fontSize: 12, color: C.text, outline: 'none' }} />
 {search && <button onClick={() => setSearch('')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }}><X size={13} /></button>}
 <span style={{ fontSize: 11, color: C.dim, whiteSpace: 'nowrap' }}>{items.length} SKU{items.length !== 1 ? 's' : ''}</span>
 </div>

 {loading ? (
 <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
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
 <div style={{ padding: 48, textAlign: 'center', color: C.dim, fontSize: 13 }}>{signalFilter || search ? t('inventory.empty_no_filtered_skus') : t('inventory.empty_no_session_data')}</div>
 ) : viewMode === 'provider' ? (
 <div style={{ padding: 16 }}>{byProvider.map(([provider, provItems]) => <ProviderGroup key={provider || '__none__'} name={provider} items={provItems} onEdit={startEdit} editedQty={editedQty} editingQtySku={editingQtySku} setEditedQty={setEditedQty} setEditingQtySku={setEditingQtySku} effectiveQty={effectiveQty} />)}</div>

 ) : viewMode === 'update' ? (
 /* ── Vista actualización rápida ───────────────────────────── */
 <div>
 {/* CSV import hint */}
 <div style={{ padding: '10px 16px', background: 'rgba(129,140,248,0.04)', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 12, color: C.dim, flex: 1 }}>
 {t('inventory.csv_hint_prefix')} <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, stock_actual, lead_time_dias</code>
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
 const draft: Record<string, { stock_actual: string; lead_time_dias: string; proveedor: string }> = {}
 data.items.forEach(item => {
 draft[item.sku] = { stock_actual: String(item.stock_actual ?? ''), lead_time_dias: String(item.lead_time_dias ?? 15), proveedor: item.proveedor ?? '' }
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
 value={draft?.stock_actual ?? ''}
 onChange={e => handleDraftChange(item.sku, 'stock_actual', e.target.value)}
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
 value={draft?.lead_time_dias ?? ''}
 onChange={e => handleDraftChange(item.sku, 'lead_time_dias', e.target.value)}
 onFocus={e => { e.target.style.borderColor = 'var(--accent)' }}
 onBlur={e => { e.target.style.borderColor = C.border }}
 />
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, minWidth: 150 }}>
 <input
 style={inputUpd}
 type="text"
 value={draft?.proveedor ?? ''}
 onChange={e => handleDraftChange(item.sku, 'proveedor', e.target.value)}
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
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '1fr 160px 120px 160px', gap: 16, padding: '14px 16px', alignItems: 'center', borderBottom: `1px solid ${C.border}`, background: idx % 2 === 0 ? C.surface : C.card, borderLeft: `3px solid ${item.signal === 'PEDIR_YA' ? C.red : item.signal === 'PEDIR_PRONTO' ? C.amber : 'transparent'}` }}>
 <div>
 <div style={{ fontWeight: 600, fontSize: 13 }}>{item.display_name || item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.dim, fontFamily: 'monospace' }}>{item.sku}</div>}
 </div>
 <SignalBadge s={item.signal} />
 <div style={{ textAlign: 'right' }}>
 {item.cantidad_recomendada != null && item.cantidad_recomendada > 0
 ? <span style={{ fontSize: 18, fontWeight: 800, color: item.signal === 'PEDIR_YA' ? C.red : item.signal === 'PEDIR_PRONTO' ? C.amber : C.green }}>{fmt(item.cantidad_recomendada, 0)}</span>
 : <span style={{ fontSize: 13, color: C.dim }}>—</span>}
 </div>
 <span style={{ fontSize: 12, color: C.muted }}>{item.proveedor || '—'}</span>
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
       value: deadStock.total_capital_trapped >= 1_000_000
        ? `$${(deadStock.total_capital_trapped / 1_000_000).toFixed(1)}M`
        : `$${(deadStock.total_capital_trapped / 1_000).toFixed(0)}K`,
       color: C.red },
      { label: t('inventory.dead_kpi_holding_cost'),
       value: `$${(deadStock.total_holding_cost_monthly / 1_000).toFixed(0)}K`,
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
          {item.proveedor && <div style={{ fontSize: 10, color: C.muted }}>{item.proveedor}</div>}
         </td>
         <td style={{ padding: '10px 12px', color: C.red, fontWeight: 700 }}>
          {item.days_without_movement}d
         </td>
         <td style={{ padding: '10px 12px', color: C.text }}>
          {item.stock_actual?.toLocaleString()}
         </td>
         <td style={{ padding: '10px 12px', fontWeight: 700, color: C.red }}>
          {item.capital_trapped >= 1_000_000
           ? `$${(item.capital_trapped / 1_000_000).toFixed(1)}M`
           : `$${(item.capital_trapped / 1_000).toFixed(0)}K`}
         </td>
         <td style={{ padding: '10px 12px', color: C.amber, fontSize: 11 }}>
          ${(item.holding_cost_monthly / 1_000).toFixed(0)}K{t('inventory.unit_per_month_suffix')}
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
 <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_categoria')} value={editState.categoria} onChange={e => setEditState(s => s ? { ...s, categoria: e.target.value } : s)} />
 <input style={{ ...inputS, width: 90 }} placeholder={t('inventory.edit_marca')} value={editState.marca} onChange={e => setEditState(s => s ? { ...s, marca: e.target.value } : s)} />
 <input style={{ ...inputS, width: 70 }} placeholder={t('inventory.edit_unidad')} value={editState.unidad_medida} onChange={e => setEditState(s => s ? { ...s, unidad_medida: e.target.value } : s)} />
 <input style={{ ...inputS, width: 120 }} placeholder={t('inventory.edit_codigo_barras')} value={editState.codigo_barras} onChange={e => setEditState(s => s ? { ...s, codigo_barras: e.target.value } : s)} />
 </div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} value={editState.stock_actual} onChange={e => setEditState(s => s ? { ...s, stock_actual: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_stock_hint')}</div>
 </td>
 <td colSpan={2} style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}>{t('inventory.edit_recalculated_on_save')}</td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <select
 style={{ ...inputS, width: 160 }}
 value={editState.proveedor}
 onChange={e => setEditState(s => s ? { ...s, proveedor: e.target.value } : s)}
 >
 <option value="">{t('inventory.edit_no_provider_option')}</option>
 {suppliers.map(s => (
 <option key={s.id} value={s.name}>{s.name}</option>
 ))}
 </select>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 60 }} type="number" min={1} max={365} value={editState.lead_time_dias} onChange={e => setEditState(s => s ? { ...s, lead_time_dias: e.target.value } : s)} />
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
 <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder="0" value={editState.costo_unitario} onChange={e => setEditState(s => s ? { ...s, costo_unitario: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{t('inventory.edit_provider_price_hint')}</div>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 4 }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder={t('inventory.edit_precio_venta')} value={editState.precio_venta} onChange={e => setEditState(s => s ? { ...s, precio_venta: e.target.value } : s)} />
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
 <button onClick={cancelEdit} style={{ all: 'unset', cursor: 'pointer', padding: '5px 8px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}><X size={11} /></button>
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
 {item.proveedor && <div style={{ fontSize: 10, color: C.dim, marginTop: 1 }}>{item.proveedor}</div>}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.has_stock ? <span style={{ fontWeight: 600 }}>{fmt(item.stock_actual, 0)}</span> : <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.no_record')}</span>}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <Sparkline data={item.stock_history} />
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.dias_cobertura != null ? (
 <span style={{ fontWeight: 600, color: item.signal === 'PEDIR_YA' ? C.red : item.signal === 'PEDIR_PRONTO' ? C.amber : item.signal === 'SOBRESTOCK' ? C.blue : C.green }}>
 {fmt(item.dias_cobertura, 0)}d
 </span>
 ) : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted, fontFamily: 'monospace', fontSize: 11 }}>{fmt(item.demanda_lead_time, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 {item.cantidad_recomendada != null && item.cantidad_recomendada > 0 ? (
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
 ) : item.cantidad_recomendada === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>{t('inventory.dont_order')}</span>
 : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{item.lead_time_dias}d</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{fmt(item.moq, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, fontFamily: 'monospace', fontSize: 11, color: C.muted }}>{item.valor_inventario != null ? fmtCurrency(item.valor_inventario) : '—'}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 {item.calc_explanation && item.demanda_diaria && (
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
 <EventsPanel events={events} onAdd={handleAddEvent} onDelete={handleDeleteEvent} onSimulate={setSimEvent} />
 </div>
 )}
 </div>

 {simEvent && sessionId && (
 <EventSimModal ev={simEvent} sessionId={sessionId} onClose={() => setSimEvent(null)} />
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
 { signal: t('inventory.signal_pedir_ya'), desc: t('inventory.legend_pedir_ya') },
 { signal: t('inventory.signal_pedir_pronto'), desc: t('inventory.legend_pedir_pronto') },
 { signal: t('inventory.signal_ok'), desc: t('inventory.legend_ok') },
 { signal: t('inventory.signal_sobrestock'), desc: t('inventory.legend_sobrestock') },
 { signal: t('inventory.signal_sin_datos'), desc: t('inventory.legend_sin_datos') },
 ].map(({ signal, desc }) => <span key={signal}><strong>{signal}</strong> — {desc}</span>)}
 </div>
 )}
 </div>
 )
}

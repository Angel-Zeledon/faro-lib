'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Link from 'next/link'
import {
 getInventoryStatus, upsertInventoryStock, deleteInventoryStock,
 importInventoryCSV, exportInventoryPO, downloadInventoryPDF,
 listInventoryEvents, createInventoryEvent, updateInventoryEvent, deleteInventoryEvent,
 listSuppliers,
} from '@/lib/api'
import type {
 InventoryStatusItem, InventorySignal,
 InventoryCalcExplanation, InventoryEvent, Supplier,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import Spinner from '@/components/ui/Spinner'
import {
 ShoppingCart, AlertTriangle, CheckCircle2, TrendingDown, TrendingUp,
 ChevronDown, ChevronRight, RefreshCw, Upload, Download, Edit2, Trash2,
 X, Save, Package, Info, Layers, List, FileText, Calendar, Plus, PencilLine, Truck,
} from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#ef4444', amber: '#f59e0b', green: '#22c55e', blue: '#3b82f6', indigo: '#818cf8',
}

// ── Signal config ─────────────────────────────────────────────────────────────
const SIGNAL_CFG: Record<InventorySignal, { label: string; color: string; bg: string; icon: React.ElementType }> = {
 PEDIR_YA: { label: 'Pedir YA', color: '#ef4444', bg: 'rgba(239,68,68,0.1)', icon: AlertTriangle },
 PEDIR_PRONTO: { label: 'Pedir pronto', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', icon: AlertTriangle },
 OK: { label: 'OK', color: '#22c55e', bg: 'rgba(34,197,94,0.1)', icon: CheckCircle2 },
 SOBRESTOCK: { label: 'Sobrestock', color: '#3b82f6', bg: 'rgba(59,130,246,0.1)', icon: TrendingDown },
 SIN_DATOS: { label: 'Sin datos', color: '#64748b', bg: 'rgba(100,116,139,0.1)', icon: Info },
}

function SignalBadge({ s }: { s: InventorySignal }) {
 const cfg = SIGNAL_CFG[s]; const Icon = cfg.icon
 return (
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: cfg.bg, color: cfg.color }}>
 <Icon size={10} /> {cfg.label}
 </span>
 )
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
 const [show, setShow] = useState(false)
 return (
 <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
 onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
 {children}
 {show && (
 <span style={{
 position: 'absolute', bottom: 'calc(100% + 6px)', left: '50%', transform: 'translateX(-50%)',
 background: '#1e293b', color: '#e2e8f0', fontSize: 11, lineHeight: 1.55,
 padding: '8px 11px', borderRadius: 7, width: 230, zIndex: 200,
 border: '1px solid #334155', boxShadow: '0 6px 18px rgba(0,0,0,0.5)',
 pointerEvents: 'none', whiteSpace: 'normal',
 }}>
 {text}
 <span style={{ position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)', borderLeft: '5px solid transparent', borderRight: '5px solid transparent', borderTop: '5px solid #1e293b' }} />
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
 const steps = [
 { label: 'Ventas diarias promedio', value: `${exp.demanda_diaria.toFixed(1)} unid/día`, op: null },
 { label: `× Días de entrega (${exp.lead_time_dias}d)`, value: `= ${exp.demanda_lead_time.toFixed(0)} unidades`, op: '×' },
 { label: '+ Colchón de seguridad', value: `+ ${exp.safety_stock.toFixed(0)} unidades`, op: '+' },
 { label: '− Stock actual en bodega', value: `− ${exp.stock_actual.toFixed(0)} unidades`, op: '−' },
 { label: '= Antes de redondear', value: `${exp.antes_moq.toFixed(0)} unidades`, op: '=' },
 ...(moq > 1
 ? [{ label: `↑ Redondeado al MOQ (${moq})`, value: `→ ${exp.cantidad_final.toFixed(0)} unidades`, op: '↑' }]
 : []),
 ]

 return (
 <div style={{
 background: 'rgba(129,140,248,0.04)', border: '1px solid rgba(129,140,248,0.15)',
 borderRadius: 8, padding: '12px 16px', marginTop: 2,
 }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: C.indigo, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
 Cómo se calculó esta recomendación
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
 El colchón de seguridad cubre la variabilidad histórica de tus ventas para el nivel de servicio configurado (95%).
 </div>
 </div>
 )
}

// ── Situación en lenguaje natural ─────────────────────────────────────────────
function ContextMessage({ summary }: { summary: Record<string, number> }) {
 const lines: { text: string; color: string }[] = []
 if (summary.pedir_ya > 0)
 lines.push({ text: `${summary.pedir_ya} producto${summary.pedir_ya > 1 ? 's' : ''} se ${summary.pedir_ya > 1 ? 'agotan' : 'agota'} antes de que llegue tu próximo pedido — actúa hoy`, color: '#ef4444' })
 if (summary.pedir_pronto > 0)
 lines.push({ text: `${summary.pedir_pronto} producto${summary.pedir_pronto > 1 ? 's' : ''} ${summary.pedir_pronto > 1 ? 'necesitan' : 'necesita'} pedido esta semana`, color: '#f59e0b' })
 if (summary.sobrestock > 0)
 lines.push({ text: `${summary.sobrestock} producto${summary.sobrestock > 1 ? 's' : ''} con exceso de stock — considera pausar el pedido`, color: '#3b82f6' })
 if (!lines.length && summary.ok > 0)
 lines.push({ text: 'Todo el inventario está bien cubierto', color: '#22c55e' })
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

// ── Events panel ─────────────────────────────────────────────────────────────
function EventsPanel({ events, onAdd, onDelete }: {
 events: InventoryEvent[]
 onAdd: (ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) => void
 onDelete: (id: string) => void
}) {
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

 const daysUntil = (date: string) => {
 const d = Math.round((new Date(date).getTime() - Date.now()) / 86400000)
 if (d < 0) return null
 if (d === 0) return 'hoy'
 if (d === 1) return 'mañana'
 return `en ${d} días`
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {/* Upcoming */}
 {upcoming.map(ev => {
 const until = daysUntil(ev.start_date)
 const isClose = until && !['hoy', 'mañana'].includes(until) ? parseInt(until) <= 14 : !!until
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
 <button onClick={() => onDelete(ev.id)} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex', padding: 4 }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}>
 <Trash2 size={12} />
 </button>
 </div>
 )
 })}

 {upcoming.length === 0 && !adding && (
 <div style={{ fontSize: 12, color: C.dim, textAlign: 'center', padding: '12px 0' }}>
 No hay eventos próximos. Agrega Black Friday, Semana Santa, temporada de fin de año…
 </div>
 )}

 {/* Add form */}
 {adding ? (
 <div style={{ padding: '12px 14px', borderRadius: 8, background: C.card, border: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
 <input style={inputS2} placeholder="Nombre del evento (ej. Black Friday, Semana Santa)" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} autoFocus />
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>Fecha inicio</div>
 <input style={inputS2} type="date" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>Fecha fin</div>
 <input style={inputS2} type="date" value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
 </div>
 <div>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 3 }}>Multiplicador de demanda</div>
 <select style={inputS2} value={form.multiplier} onChange={e => setForm(f => ({ ...f, multiplier: e.target.value }))}>
 <option value="1.2">×1.2 — leve (+20%)</option>
 <option value="1.5">×1.5 — moderado (+50%)</option>
 <option value="2.0">×2.0 — alto (+100%)</option>
 <option value="2.5">×2.5 — muy alto (+150%)</option>
 <option value="3.0">×3.0 — pico (+200%)</option>
 </select>
 </div>
 </div>
 <input style={inputS2} placeholder="Notas opcionales (ej. aplica solo a lácteos)" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
 <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
 <button onClick={() => setAdding(false)} style={{ all: 'unset', cursor: 'pointer', padding: '6px 12px', borderRadius: 6, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12 }}>Cancelar</button>
 <button onClick={handleAdd} disabled={!form.name || !form.start_date || !form.end_date} style={{ all: 'unset', cursor: 'pointer', padding: '6px 14px', borderRadius: 6, background: C.indigo, color: '#fff', fontSize: 12, fontWeight: 600, opacity: !form.name || !form.start_date || !form.end_date ? 0.5 : 1 }}>Guardar evento</button>
 </div>
 </div>
 ) : (
 <button onClick={() => setAdding(true)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, border: `1px dashed ${C.border}`, color: C.dim, fontSize: 12, justifyContent: 'center' }}>
 <Plus size={12} /> Agregar evento o temporada
 </button>
 )}

 {past.length > 0 && (
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>
 {past.length} evento{past.length > 1 ? 's' : ''} pasado{past.length > 1 ? 's' : ''} (oculto{past.length > 1 ? 's' : ''})
 </div>
 )}
 </div>
 )
}

// ── Inline edit state ─────────────────────────────────────────────────────────
interface EditState { stock_actual: string; lead_time_dias: string; costo_unitario: string; moq: string; proveedor: string; display_name: string }
function rowToEdit(item: InventoryStatusItem): EditState {
 return { stock_actual: String(item.stock_actual ?? ''), lead_time_dias: String(item.lead_time_dias ?? 15), costo_unitario: String(item.costo_unitario ?? ''), moq: String(item.moq ?? 1), proveedor: item.proveedor ?? '', display_name: item.display_name ?? '' }
}
const inputS: React.CSSProperties = { background: 'var(--surface-2)', border: `1px solid var(--border)`, borderRadius: 5, color: 'var(--text)', fontSize: 12, outline: 'none', padding: '3px 7px', width: '100%', boxSizing: 'border-box' }

// ── Provider group ────────────────────────────────────────────────────────────
function ProviderGroup({ name, items, onEdit }: { name: string; items: InventoryStatusItem[]; onEdit: (item: InventoryStatusItem) => void }) {
 const [open, setOpen] = useState(true)
 const critical = items.filter(i => i.signal === 'PEDIR_YA').length
 const warning = items.filter(i => i.signal === 'PEDIR_PRONTO').length
 return (
 <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden', marginBottom: 10 }}>
 <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', background: C.card, cursor: 'pointer', borderBottom: open ? `1px solid ${C.border}` : 'none' }}>
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{name || '(sin proveedor)'}</span>
 <span style={{ fontSize: 11, color: C.dim }}>{items.length} SKUs</span>
 {critical > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red }}>{critical} urgente{critical !== 1 ? 's' : ''}</span>}
 {warning > 0 && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{warning} pronto</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </div>
 {open && items.map((item, idx) => (
 <div key={item.sku} style={{ display: 'grid', gridTemplateColumns: '160px 100px 90px 90px 80px 60px auto', gap: 12, padding: '10px 16px', alignItems: 'center', fontSize: 12, background: idx % 2 === 0 ? C.surface : C.card, borderBottom: idx < items.length - 1 ? `1px solid ${C.border}` : 'none' }}>
 <div><div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{item.sku}</div>{item.display_name && <div style={{ color: C.dim, fontSize: 10 }}>{item.display_name}</div>}</div>
 <SignalBadge s={item.signal} />
 <span style={{ color: item.signal === 'PEDIR_YA' ? C.red : item.signal === 'PEDIR_PRONTO' ? C.amber : C.green, fontWeight: 600 }}>{item.dias_cobertura != null ? `${item.dias_cobertura.toFixed(0)}d` : '—'}</span>
 <span style={{ fontWeight: 700, color: item.cantidad_recomendada ? C.green : C.dim }}>{item.cantidad_recomendada ? item.cantidad_recomendada.toFixed(0) : '—'}</span>
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

// ── Main ─────────────────────────────────────────────────────────────────────
export default function InventoryPage() {
 const { sessionId, setSessionId, currentSession, completedSessions } = useAutoSession()
 const [data, setData] = useState<{ items: InventoryStatusItem[]; summary: Record<string, number> } | null>(null)
 const [loading, setLoading] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [signalFilter, setSignalFilter] = useState<InventorySignal | ''>('')
 const [search, setSearch] = useState('')
 const [viewMode, setViewMode] = useState<'table' | 'simple' | 'provider' | 'update'>(() =>
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
 const [updateDraft, setUpdateDraft] = useState<Record<string, { stock_actual: string; lead_time_dias: string; proveedor: string }>>({})
 const [updateSaving, setUpdateSaving] = useState(false)
 const [updatedSkus, setUpdatedSkus] = useState<Set<string>>(new Set())
 const [suppliers, setSuppliers] = useState<Supplier[]>([])
 const importRef = useRef<HTMLInputElement>(null)

 useEffect(() => {
 listInventoryEvents().then(setEvents).catch(() => {})
 listSuppliers().then(setSuppliers).catch(() => {})
 }, [])

 const load = useCallback(async (sid: string) => {
 if (!sid) return
 setLoading(true); setError(null)
 try { setData(await getInventoryStatus(sid) as any) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error cargando inventario') }
 finally { setLoading(false) }
 }, [])

 useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])

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
 }
 }
 setUpdateSaving(false)
 setUpdateDraft({})
 setUpdatedSkus(new Set())
 setViewMode('table')
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
 if (!editState) return; setSaving(true)
 try {
 await upsertInventoryStock(sku, { display_name: editState.display_name || undefined, stock_actual: parseFloat(editState.stock_actual) || 0, lead_time_dias: parseInt(editState.lead_time_dias) || 15, costo_unitario: editState.costo_unitario ? parseFloat(editState.costo_unitario) : undefined, moq: parseFloat(editState.moq) || 1, proveedor: editState.proveedor || undefined })
 setEditId(null); setEditState(null); await load(sessionId)
 } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error guardando') }
 finally { setSaving(false) }
 }

 async function handleDelete(sku: string) {
 if (!confirm(`¿Eliminar el registro de inventario para ${sku}?`)) return
 try { await deleteInventoryStock(sku); await load(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error eliminando') }
 }

 async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
 const file = e.target.files?.[0]; if (!file) return; e.target.value = ''; setImporting(true)
 try { const res = await importInventoryCSV(file); await load(sessionId); alert(`Importados ${res.imported} de ${res.total_rows} SKUs`) }
 catch (err: unknown) { setError(err instanceof Error ? err.message : 'Error importando') }
 finally { setImporting(false) }
 }

 async function handleExport() {
 if (!sessionId) return; setExporting(true)
 try { await exportInventoryPO(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error exportando') }
 finally { setExporting(false) }
 }

 async function handlePDF() {
 if (!sessionId) return; setPdfLoading(true)
 try { await downloadInventoryPDF(sessionId) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error generando PDF') }
 finally { setPdfLoading(false) }
 }

 async function handleAddEvent(ev: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) {
 try { const created = await createInventoryEvent(ev); setEvents(prev => [...prev, created]) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error guardando evento') }
 }

 async function handleDeleteEvent(id: string) {
 try { await deleteInventoryEvent(id); setEvents(prev => prev.filter(e => e.id !== id)) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error eliminando evento') }
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
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>Inventario</h1>
 <p style={{ margin: 0, fontSize: 11, color: C.dim }}>Semáforo de stock · Recomendaciones de compra</p>
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
 ['table', <List size={13} />, 'Tabla'],
 ['simple', <Package size={13} />, 'Simple'],
 ['provider', <Layers size={13} />, 'Proveedor'],
 ['update', <PencilLine size={13} />, 'Actualizar stock'],
 ] as [string, React.ReactNode, string][]).map(([mode, icon, label]) => (
 <button key={mode} onClick={() => setViewMode(mode as 'table' | 'simple' | 'provider' | 'update')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, padding: '6px 11px', fontSize: 11, fontWeight: 500, background: viewMode === mode ? 'var(--accent-dim)' : 'transparent', color: viewMode === mode ? 'var(--accent)' : C.dim }}>
 {icon}{label}
 </button>
 ))}
 </div>

 <button onClick={() => sessionId && load(sessionId)} disabled={loading} title="Actualizar" style={{ all: 'unset', cursor: loading ? 'default' : 'pointer', display: 'flex', alignItems: 'center', padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 8, color: C.dim, opacity: loading ? 0.5 : 1 }}><RefreshCw size={13} /></button>
 <input ref={importRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={handleImport} />
 <button onClick={() => importRef.current?.click()} disabled={importing} style={{ all: 'unset', cursor: importing ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted, opacity: importing ? 0.6 : 1 }}>
 {importing ? <Spinner size={12} /> : <Upload size={12} />} CSV
 </button>
 <button onClick={handleExport} disabled={exporting || !sessionId} style={{ all: 'unset', cursor: exporting || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: C.green, opacity: exporting || !sessionId ? 0.5 : 1 }}>
 {exporting ? <Spinner size={12} /> : <Download size={12} />} Exportar OC
 </button>
 <button onClick={handlePDF} disabled={pdfLoading || !sessionId} title="Descargar resumen ejecutivo en PDF" style={{ all: 'unset', cursor: pdfLoading || !sessionId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, background: 'rgba(129,140,248,0.1)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, opacity: pdfLoading || !sessionId ? 0.5 : 1 }}>
 {pdfLoading ? <Spinner size={12} /> : <FileText size={12} />} PDF
 </button>
 <Link href="/inventory/roi" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title="Ver impacto acumulado">
 <TrendingUp size={12} /> Impacto
 </Link>
 <Link href="/inventory/suppliers" style={{
 display: 'flex', alignItems: 'center', gap: 5,
 fontSize: 11, color: C.dim, textDecoration: 'none',
 padding: '7px 10px', border: `1px solid ${C.border}`,
 borderRadius: 8,
 }} title="Gestionar proveedores">
 <Truck size={12} /> Proveedores
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

 {/* Upcoming events alert */}
 {upcomingAlerts.length > 0 && (
 <div style={{ padding: '10px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <Calendar size={14} color={C.amber} style={{ flexShrink: 0, marginTop: 1 }} />
 <div style={{ flex: 1, fontSize: 12 }}>
 <span style={{ fontWeight: 600, color: C.amber }}>Eventos próximos:</span>
 {upcomingAlerts.map(ev => {
 const d = Math.round((new Date(ev.start_date).getTime() - Date.now()) / 86400000)
 return (
 <span key={ev.id} style={{ marginLeft: 8, color: C.text }}>
 {ev.name} <span style={{ color: C.dim }}>({d === 0 ? 'hoy' : `en ${d}d`}, ×{ev.multiplier.toFixed(1)})</span>
 </span>
 )
 })}
 <span style={{ marginLeft: 8, color: C.dim, fontSize: 11 }}>— Las recomendaciones de stock no incluyen aún el multiplicador de evento.</span>
 </div>
 </div>
 )}

 {/* Situación */}
 {summary && <ContextMessage summary={summary} />}

 {/* SKUs sin stock banner */}
 {!loading && sessionId && skusSinStock > 0 && (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 8, background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.2)', color: C.indigo, fontSize: 13 }}>
 <Info size={14} style={{ flexShrink: 0 }} />
 <span><strong>{skusSinStock} de {skusConForecast} SKUs</strong> del forecast no tienen stock registrado. Edítalos con el boton de edicion o importa un CSV con columnas: <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, stock_actual, lead_time_dias</code></span>
 </div>
 )}

 {/* KPIs */}
 {summary && (
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
 <KPICard label="Total SKUs" value={summary.total_skus} color={C.indigo} onClick={() => setSignalFilter('')} active={!signalFilter} />
 <KPICard label="Pedir YA" value={summary.pedir_ya} color={C.red} onClick={() => setSignalFilter(signalFilter === 'PEDIR_YA' ? '' : 'PEDIR_YA')} active={signalFilter === 'PEDIR_YA'} sub={summary.pedir_ya > 0 ? 'Riesgo inmediato' : undefined} />
 <KPICard label="Pedir pronto" value={summary.pedir_pronto} color={C.amber} onClick={() => setSignalFilter(signalFilter === 'PEDIR_PRONTO' ? '' : 'PEDIR_PRONTO')} active={signalFilter === 'PEDIR_PRONTO'} />
 <KPICard label="OK" value={summary.ok} color={C.green} onClick={() => setSignalFilter(signalFilter === 'OK' ? '' : 'OK')} active={signalFilter === 'OK'} />
 <KPICard label="Sobrestock" value={summary.sobrestock} color={C.blue} onClick={() => setSignalFilter(signalFilter === 'SOBRESTOCK' ? '' : 'SOBRESTOCK')} active={signalFilter === 'SOBRESTOCK'} />
 <KPICard label="Valor inventario" value={summary.valor_total_inventario > 0 ? fmtCurrency(summary.valor_total_inventario) : '—'} color={C.indigo} sub="SKUs con costo registrado" />
 </div>
 )}

 {/* Main table / view */}
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>

 {/* Toolbar */}
 <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10, background: C.card }}>
 <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Buscar SKU, nombre, proveedor…" style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 7, padding: '6px 12px', fontSize: 12, color: C.text, outline: 'none' }} />
 {search && <button onClick={() => setSearch('')} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }}><X size={13} /></button>}
 <span style={{ fontSize: 11, color: C.dim, whiteSpace: 'nowrap' }}>{items.length} SKU{items.length !== 1 ? 's' : ''}</span>
 </div>

 {loading ? (
 <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
 ) : !sessionId ? (
 /* ── Onboarding empty state ───────────────────────────────── */
 <div style={{ padding: '40px 48px', maxWidth: 520, margin: '0 auto', textAlign: 'center' }}>
 <ShoppingCart size={36} strokeWidth={1} color={C.indigo} style={{ margin: '0 auto 16px', opacity: 0.4 }} />
 <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>Bienvenido al módulo de inventario</div>
 <div style={{ fontSize: 13, color: C.dim, marginBottom: 24, lineHeight: 1.7 }}>Aquí verás en tiempo real qué productos necesitas pedir, cuánto pedir, y cuándo.</div>
 <div style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 14 }}>
 {[
 { n: '1', title: 'Selecciona una sesión entrenada', desc: 'Usa el selector de arriba. La sesión contiene el historial de ventas y los modelos de forecast.' },
 { n: '2', title: 'Agrega tu stock actual por SKU', desc: 'Haz clic en ✏️ en cualquier fila e ingresa cuántas unidades tienes en bodega hoy. O importa un CSV con columnas: sku, stock_actual, lead_time_dias.' },
 { n: '3', title: 'El semáforo se calcula automáticamente', desc: ' Pedir YA · Pedir esta semana · Cubierto · Sobrestock. Exporta la orden de compra con un clic.' },
 ].map(({ n, title, desc }) => (
 <div key={n} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
 <span style={{ width: 24, height: 24, borderRadius: '50%', flexShrink: 0, background: 'rgba(129,140,248,0.12)', border: '1px solid rgba(129,140,248,0.3)', color: C.indigo, fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{n}</span>
 <div><div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div><div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.6 }}>{desc}</div></div>
 </div>
 ))}
 </div>
 </div>
 ) : items.length === 0 ? (
 <div style={{ padding: 48, textAlign: 'center', color: C.dim, fontSize: 13 }}>{signalFilter || search ? 'No hay SKUs con esos filtros' : 'No hay datos para esta sesión'}</div>
 ) : viewMode === 'provider' ? (
 <div style={{ padding: 16 }}>{byProvider.map(([provider, provItems]) => <ProviderGroup key={provider || '__none__'} name={provider} items={provItems} onEdit={startEdit} />)}</div>

 ) : viewMode === 'update' ? (
 /* ── Vista actualización rápida ───────────────────────────── */
 <div>
 {/* CSV import hint */}
 <div style={{ padding: '10px 16px', background: 'rgba(129,140,248,0.04)', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 12, color: C.dim, flex: 1 }}>
 ¿Tienes el stock en un archivo? Importa un CSV con columnas: <code style={{ fontSize: 11, background: 'rgba(129,140,248,0.1)', padding: '1px 5px', borderRadius: 4 }}>sku, stock_actual, lead_time_dias</code>
 </span>
 <button onClick={() => importRef.current?.click()} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 7, fontSize: 12, fontWeight: 600, border: `1px solid ${C.border}`, color: C.muted }}>
 <Upload size={11} /> Importar CSV →
 </button>
 </div>
 {/* Action bar */}
 <div style={{ padding: '10px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 10 }}>
 <span style={{ fontSize: 12, color: updatedSkus.size > 0 ? C.amber : C.dim, fontWeight: updatedSkus.size > 0 ? 600 : 400, flex: 1 }}>
 {updatedSkus.size === 0 ? 'Sin cambios' : `${updatedSkus.size} fila${updatedSkus.size !== 1 ? 's' : ''} modificada${updatedSkus.size !== 1 ? 's' : ''}`}
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
 Descartar
 </button>
 <button
 onClick={handleSaveAll}
 disabled={updatedSkus.size === 0 || updateSaving}
 style={{ all: 'unset', cursor: updatedSkus.size === 0 || updateSaving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7, fontSize: 12, fontWeight: 600, background: updatedSkus.size > 0 ? C.green : 'rgba(34,197,94,0.2)', color: '#fff', opacity: updatedSkus.size === 0 || updateSaving ? 0.5 : 1 }}
 >
 {updateSaving ? <Spinner size={11} /> : <Save size={11} />}
 {updateSaving ? `Guardando…` : `Guardar ${updatedSkus.size > 0 ? updatedSkus.size : ''} cambio${updatedSkus.size !== 1 ? 's' : ''}`}
 </button>
 </div>
 {/* Table */}
 <div style={{ overflowY: 'auto', maxHeight: 500 }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
 <tr style={{ background: C.card }}>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em', width: 8 }} />
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>SKU</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>Nombre</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>Stock actual</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>Lead time (días)</th>
 <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.06em' }}>Proveedor</th>
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
 <span>SKU / Producto</span><span>Señal</span><span style={{ textAlign: 'right' }}>Cantidad a pedir</span><span>Proveedor</span>
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

 ) : (
 /* ── Tabla completa ───────────────────────────────────────── */
 <div style={{ overflowX: 'auto' }}>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 <th style={{ padding: '9px 12px', width: 28, borderBottom: `1px solid ${C.border}` }} />
 <ThTip label="Señal" tip="Se agota antes de recibir el pedido · Pedir esta semana · Cubierto · Exceso · Sin datos: agrega stock actual para calcular" />
 <ThTip label="SKU / Nombre" tip="Código del producto. El proveedor aparece en letra pequeña debajo." />
 <ThTip label="Stock" tip="Unidades en bodega hoy. Haz clic en ✏️ para actualizar." />
 <ThTip label="Tendencia" tip="Evolución de tu stock en los últimos 14 días. Rojo = bajando, verde = estable o subiendo." />
 <ThTip label="Días cobertura" tip="Con tu stock actual, ¿cuántos días aguanta tu bodega sin pedir? Si es menor que tu lead time, estás en riesgo." />
 <ThTip label="Dem. (LT)" tip="Cuánto esperas vender mientras esperas que llegue tu pedido." />
 <ThTip label="Cantidad a pedir" tip="Lo que deberías pedir HOY. Haz clic en ▶ para ver el desglose del cálculo." />
 <ThTip label="Lead time" tip="Días que tarda tu proveedor desde que haces el pedido hasta que llega a tu bodega." />
 <ThTip label="MOQ" tip="Mínimo de unidades por pedido. La recomendación siempre es múltiplo de este número." />
 <ThTip label="ABC-XYZ" tip="A/B/C = importancia en ingresos. X/Y/Z = qué tan predecible es la demanda. AZ = valor alto + demanda errática — el más delicado." />
 <ThTip label="Valor bodega" tip="Stock actual × costo unitario. Solo aparece si registraste el costo." />
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
 <input style={inputS} placeholder="Nombre visible (ej. Arroz 1kg)" value={editState.display_name} onChange={e => setEditState(s => s ? { ...s, display_name: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>Nombre descriptivo (opcional)</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} value={editState.stock_actual} onChange={e => setEditState(s => s ? { ...s, stock_actual: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>Unidades en bodega hoy</div>
 </td>
 <td colSpan={2} style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}`, color: C.dim, fontSize: 11 }}>Se recalcula al guardar</td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 {(() => {
 const matched = suppliers.find(s => s.name.toLowerCase() === (editState.proveedor ?? '').trim().toLowerCase())
 return (
 <>
 <input
 style={{ ...inputS, width: 120 }}
 placeholder="Proveedor"
 value={editState.proveedor}
 onChange={e => setEditState(s => s ? { ...s, proveedor: e.target.value } : s)}
 list="suppliers-datalist"
 />
 <datalist id="suppliers-datalist">
 {suppliers.map(s => <option key={s.id} value={s.name} />)}
 </datalist>
 {editState.proveedor && (
 <div style={{ fontSize: 10, marginTop: 2, color: matched ? C.green : C.amber }}>
 {matched
 ? `Vinculado a ${matched.name}`
 : 'Escribe el nombre exacto de un proveedor o créalo en Proveedores'}
 </div>
 )}
 </>
 )
 })()}
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 60 }} type="number" min={1} max={365} value={editState.lead_time_dias} onChange={e => setEditState(s => s ? { ...s, lead_time_dias: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>Días del proveedor</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <input style={{ ...inputS, width: 70 }} type="number" min={0} value={editState.moq} onChange={e => setEditState(s => s ? { ...s, moq: e.target.value } : s)} />
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>Mínimo por pedido</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
 <span style={{ fontSize: 11, color: C.dim }}>$</span>
 <input style={{ ...inputS, width: 80 }} type="number" min={0} placeholder="0" value={editState.costo_unitario} onChange={e => setEditState(s => s ? { ...s, costo_unitario: e.target.value } : s)} />
 </div>
 <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>Precio proveedor / und</div>
 </td>
 <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 <button onClick={() => commitEdit(item.sku)} disabled={saving} style={{ all: 'unset', cursor: saving ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: C.green, color: '#fff', opacity: saving ? 0.6 : 1 }}>
 {saving ? <Spinner size={10} /> : <Save size={10} />} Guardar
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
 title="Ver cómo se calculó"
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
 {item.has_stock ? <span style={{ fontWeight: 600 }}>{fmt(item.stock_actual, 0)}</span> : <span style={{ color: C.dim, fontSize: 11 }}>Sin registro</span>}
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
 {item.cantidad_recomendada != null && item.cantidad_recomendada > 0
 ? <span style={{ fontWeight: 700, color: C.green, fontSize: 13 }}>{fmt(item.cantidad_recomendada, 0)}</span>
 : item.cantidad_recomendada === 0
 ? <span style={{ color: C.dim, fontSize: 11 }}>No pedir</span>
 : '—'}
 </td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{item.lead_time_dias}d</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, color: C.muted }}>{fmt(item.moq, 0)}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}><AbcXyzBadge value={item.abc_xyz} /></td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`, fontFamily: 'monospace', fontSize: 11, color: C.muted }}>{item.valor_inventario != null ? fmtCurrency(item.valor_inventario) : '—'}</td>
 <td style={{ padding: '10px 12px', borderBottom: isExpanded ? 'none' : `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 4 }}>
 <button onClick={() => startEdit(item)} title="Editar" style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.indigo)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Edit2 size={13} /></button>
 {item.has_stock && <button onClick={() => handleDelete(item.sku)} title="Eliminar" style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}><Trash2 size={13} /></button>}
 </div>
 </td>
 </tr>
 {/* Expanded explanation row */}
 {isExpanded && item.calc_explanation && (
 <tr key={`${item.sku}-exp`}>
 <td colSpan={13} style={{ padding: '0 16px 12px 48px', borderBottom: `1px solid ${C.border}`, background: crit ? 'rgba(239,68,68,0.01)' : rowBg }}>
 <CalcExplainer exp={item.calc_explanation} moq={item.moq} />
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
 <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>Eventos y temporadas</span>
 {upcomingAlerts.length > 0 && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(245,158,11,0.1)', color: C.amber }}>{upcomingAlerts.length} próximo{upcomingAlerts.length > 1 ? 's' : ''}</span>}
 <ChevronDown size={13} color={C.dim} style={{ transform: showEvents ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }} />
 </button>
 {showEvents && (
 <div style={{ padding: '0 20px 20px', borderTop: `1px solid ${C.border}` }}>
 <div style={{ fontSize: 12, color: C.dim, marginBottom: 14, marginTop: 12, lineHeight: 1.6 }}>
 Registra temporadas altas (Black Friday, Semana Santa, fin de año) para que el sistema te avise con anticipación que necesitas más stock del habitual. El multiplicador indica cuánto más alto que lo normal suele ser la demanda.
 </div>
 <EventsPanel events={events} onAdd={handleAddEvent} onDelete={handleDeleteEvent} />
 </div>
 )}
 </div>

 {/* Legend */}
 {!loading && sessionId && (
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 20, fontSize: 11, color: C.dim, paddingBottom: 8, flexWrap: 'wrap' }}>
 {[
 { signal: 'Pedir YA', desc: 'se agota antes de recibir el pedido' },
 { signal: 'Pedir pronto', desc: 'colchón mínimo — pide esta semana' },
 { signal: 'OK', desc: 'stock saludable' },
 { signal: 'Sobrestock', desc: 'considera pausar el pedido' },
 { signal: 'Sin datos', desc: 'agrega stock actual para ver la señal' },
 ].map(({ signal, desc }) => <span key={signal}><strong>{signal}</strong> — {desc}</span>)}
 </div>
 )}
 </div>
 )
}

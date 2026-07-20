'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import {
 listInventoryStock,
 getBOM, upsertBOMItem, deleteBOMItem, getProductionRequirements, setProductType,
} from '@/lib/api'
import type {
 InventoryStock, BomItem, ProductionPlan, ProductionRequirement,
} from '@/lib/types'
import { useAutoSession } from '@/hooks/useAutoSession'
import SessionBar from '@/components/ui/SessionBar'
import Spinner from '@/components/ui/Spinner'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'
import {
 Cog, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight,
 RefreshCw, Plus, Trash2, X, Save, Package, ArrowRight, Info, Layers,
} from 'lucide-react'

const C = {
 surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
 text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
 red: '#ef4444', amber: '#f59e0b', green: '#22c55e',
 blue: '#3b82f6', indigo: '#818cf8', orange: '#f97316',
}

const PRODUCT_TYPE_LABEL_KEYS: Record<string, string> = {
 finished_good: 'produccion.type_finished_good', semi_finished: 'produccion.type_semi_finished',
 component: 'produccion.type_component', raw_material: 'produccion.type_raw_material',
 packaging: 'produccion.type_packaging', service: 'produccion.type_service',
}

const PRODUCT_TYPE_COLOR: Record<string, string> = {
 finished_good: C.indigo, semi_finished: C.orange, component: C.blue,
 raw_material: C.green, packaging: C.amber, service: C.dim,
}

function TypeBadge({ type }: { type: string }) {
 const { t } = useLanguage()
 const labelKey = PRODUCT_TYPE_LABEL_KEYS[type]
 const label = labelKey ? t(labelKey) : type
 const color = PRODUCT_TYPE_COLOR[type] || C.dim
 return (
 <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: color + '18', color }}>
 {label}
 </span>
 )
}

function fmt(n: number | null | undefined, d = 1) {
 if (n == null) return '—'
 return n.toLocaleString(undefined, { maximumFractionDigits: d })
}

// ── Finished Good card (collapsible) ─────────────────────────────────────────
function FinishedGoodCard({ item }: { item: ProductionPlan['finished_goods'][number] }) {
 const { t } = useLanguage()
 const [open, setOpen] = useState(item.requirements.some(r => r.status === 'SHORTAGE'))
 const hasShortage = item.requirements.some(r => r.status === 'SHORTAGE')

 return (
 <div style={{
 border: `1px solid ${hasShortage ? C.red + '40' : C.border}`,
 borderRadius: 10, overflow: 'hidden', marginBottom: 10,
 background: hasShortage ? 'rgba(239,68,68,0.02)' : C.surface,
 }}>
 <div onClick={() => setOpen(o => !o)} style={{
 display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
 cursor: 'pointer', background: C.card,
 borderBottom: open ? `1px solid ${C.border}` : 'none',
 }}>
 <ChevronRight size={13} color={C.dim} style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 0.2s', flexShrink: 0 }} />
 <div style={{ flex: 1, minWidth: 0 }}>
 <div style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 12, color: C.text }}>{item.sku}</div>
 {item.display_name && <div style={{ fontSize: 11, color: C.muted }}>{item.display_name}</div>}
 </div>
 <div style={{ display: 'flex', gap: 20, fontSize: 12, color: C.muted }}>
 <span>{t('produccion.label_demand')} <strong style={{ color: C.text }}>{fmt(item.forecast_demand, 0)}</strong></span>
 <span>{t('produccion.label_in_stock')} <strong style={{ color: C.text }}>{fmt(item.current_stock, 0)}</strong></span>
 <span>{t('produccion.label_to_produce')} <strong style={{ color: C.indigo }}>{fmt(item.to_produce, 0)}</strong></span>
 </div>
 {hasShortage && (
 <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: 'rgba(239,68,68,0.1)', color: C.red, flexShrink: 0 }}>
 {item.requirements.filter(r => r.status === 'SHORTAGE').length} {t('produccion.shortage_suffix')}
 </span>
 )}
 </div>
 {open && (
 <div>
 {item.requirements.length === 0 ? (
 <div style={{ padding: '12px 16px', fontSize: 12, color: C.dim }}>{t('produccion.no_bom_materials')}</div>
 ) : (
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 {[t('produccion.col_material'), t('produccion.col_type'), t('produccion.col_qty_per_unit'), t('produccion.col_required'), t('produccion.col_in_stock'), t('produccion.col_shortage'), t('produccion.col_status')].map(h => (
 <th key={h} style={{ padding: '7px 12px', textAlign: 'left', color: C.dim, fontWeight: 600, fontSize: 10, borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {item.requirements.map((req, i) => (
 <tr key={req.child_sku} style={{ background: i % 2 === 0 ? C.surface : C.card, borderBottom: `1px solid ${C.border}` }}>
 <td style={{ padding: '8px 12px' }}>
 <div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{req.child_sku}</div>
 {req.display_name !== req.child_sku && <div style={{ fontSize: 11, color: C.muted }}>{req.display_name}</div>}
 </td>
 <td style={{ padding: '8px 12px' }}><TypeBadge type={req.product_type} /></td>
 <td style={{ padding: '8px 12px', color: C.muted }}>{fmt(req.quantity_per_unit)}{req.unit ? ` ${req.unit}` : ''}</td>
 <td style={{ padding: '8px 12px', fontWeight: 600 }}>{fmt(req.required_quantity)}{req.unit ? ` ${req.unit}` : ''}</td>
 <td style={{ padding: '8px 12px', color: C.muted }}>{fmt(req.current_stock)}</td>
 <td style={{ padding: '8px 12px', fontWeight: 700, color: req.shortage > 0 ? C.red : C.green }}>{req.shortage > 0 ? fmt(req.shortage) : '—'}</td>
 <td style={{ padding: '8px 12px' }}>
 {req.status === 'SHORTAGE'
 ? <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.red, fontWeight: 700 }}><AlertTriangle size={11} /> {t('produccion.status_shortage')}</span>
 : <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.green }}><CheckCircle2 size={11} /> {t('produccion.status_ok')}</span>}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 </div>
 )}
 </div>
 )
}

// ── BOM Editor ────────────────────────────────────────────────────────────────
function BomEditor({ allSkus }: { allSkus: InventoryStock[] }) {
 const { t } = useLanguage()
 const [parentSku, setParentSku] = useState('')
 const [bomItems, setBomItems] = useState<BomItem[]>([])
 const [loading, setLoading] = useState(false)
 const [addMode, setAddMode] = useState(false)
 const [newChild, setNewChild] = useState('')
 const [newQty, setNewQty] = useState('1')
 const [newUnit, setNewUnit] = useState('')
 const [saving, setSaving] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [loadError, setLoadError] = useState<string | null>(null)

 const parents = allSkus.filter(s =>
 !s.product_type || ['finished_good', 'semi_finished'].includes(s.product_type)
 )
 const children = allSkus.filter(s =>
 !s.product_type || ['component', 'raw_material', 'packaging', 'semi_finished'].includes(s.product_type)
 )

 const loadBom = useCallback((sku: string) => {
 if (!sku) { setBomItems([]); setLoadError(null); return }
 setLoading(true)
 setLoadError(null)
 getBOM(sku)
 .then(setBomItems)
 .catch((e: unknown) => {
 setBomItems([])
 setLoadError(e instanceof Error ? e.message : `${t('produccion.err_load_bom_prefix')} ${sku}. ${t('produccion.err_check_connection')}`)
 })
 .finally(() => setLoading(false))
 }, [])

 useEffect(() => { loadBom(parentSku) }, [parentSku, loadBom])

 async function handleAdd() {
 if (!parentSku || !newChild || !newQty) return
 setSaving(true); setError(null)
 try {
 const item = await upsertBOMItem(parentSku, newChild, { quantity: parseFloat(newQty) || 1, unit: newUnit || undefined })
 setBomItems(prev => {
 const idx = prev.findIndex(b => b.child_sku === newChild)
 return idx >= 0 ? prev.map((b, i) => i === idx ? item : b) : [...prev, item]
 })
 setAddMode(false); setNewChild(''); setNewQty('1'); setNewUnit('')
 } catch (e: unknown) { setError(e instanceof Error ? e.message : t('produccion.err_generic')) }
 finally { setSaving(false) }
 }

 async function handleDelete(childSku: string) {
 if (!confirm(`${t('produccion.confirm_delete_bom_prefix')} ${childSku} ${t('produccion.confirm_delete_bom_mid')} ${parentSku}?`)) return
 try {
  await deleteBOMItem(parentSku, childSku)
  setBomItems(prev => prev.filter(b => b.child_sku !== childSku))
 } catch (e: unknown) {
  setError(e instanceof Error ? e.message : t('produccion.err_delete_material'))
 }
 }

 const inputS: React.CSSProperties = {
 background: C.card, border: `1px solid ${C.border}`, borderRadius: 6,
 color: C.text, fontSize: 12, outline: 'none', padding: '6px 9px', boxSizing: 'border-box',
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
 {/* Parent selector */}
 <div>
 <label style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase' as const, letterSpacing: '0.07em', display: 'block', marginBottom: 6 }}>
 {t('produccion.label_parent_select')}
 </label>
 <div style={{ position: 'relative', maxWidth: 380 }}>
 <select value={parentSku} onChange={e => setParentSku(e.target.value)} style={{ ...inputS, width: '100%', paddingRight: 28, appearance: 'none' as const }}>
 <option value="">{t('produccion.option_select_product')}</option>
 {parents.map(s => (
 <option key={s.sku} value={s.sku}>{s.sku}{s.display_name ? ` — ${s.display_name}` : ''}</option>
 ))}
 </select>
 <ChevronDown size={12} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: C.dim }} />
 </div>
 {allSkus.length === 0 && (
 <p style={{ fontSize: 12, color: C.amber, marginTop: 8 }}>
 {t('produccion.no_skus_in_inventory')} <Link href="/inventory" style={{ color: C.indigo }}>{t('produccion.link_add_products_first')}</Link>
 </p>
 )}
 </div>

 {/* BOM table */}
 {parentSku && (
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: 'hidden' }}>
 <div style={{ padding: '12px 16px', background: C.card, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
 <div style={{ fontSize: 13, fontWeight: 600 }}>{t('produccion.bom_list_title_prefix')} {parentSku}</div>
 <button onClick={() => setAddMode(true)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, color: C.green, padding: '5px 12px', border: `1px solid ${C.green}40`, borderRadius: 7, background: C.green + '10' }}>
 <Plus size={12} /> {t('produccion.btn_add_material')}
 </button>
 </div>

 {loading ? (
 <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
 ) : loadError ? (
 <div style={{ padding: '16px', display: 'flex', alignItems: 'center', gap: 10, color: C.red, fontSize: 12 }}>
 <AlertTriangle size={14} style={{ flexShrink: 0 }} />
 <span>{loadError}</span>
 <button onClick={() => loadBom(parentSku)} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', padding: '5px 10px', border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 11 }}>
 {t('produccion.btn_retry')}
 </button>
 </div>
 ) : (
 <>
 {error && <div style={{ padding: '8px 16px', color: C.red, fontSize: 12 }}>{error}</div>}
 {addMode && (
 <div style={{ padding: '12px 16px', background: 'rgba(129,140,248,0.04)', borderBottom: `1px solid ${C.border}`, display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
 <div style={{ flex: 2, minWidth: 200 }}>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 4 }}>{t('produccion.field_material_component')}</div>
 <select value={newChild} onChange={e => setNewChild(e.target.value)} style={{ ...inputS, width: '100%' }}>
 <option value="">{t('produccion.option_select')}</option>
 {allSkus.filter(s => s.sku !== parentSku).map(s => (
 <option key={s.sku} value={s.sku}>{s.sku}{s.display_name ? ` — ${s.display_name}` : ''}</option>
 ))}
 </select>
 </div>
 <div style={{ width: 90 }}>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 4 }}>{t('produccion.field_quantity')}</div>
 <input type="number" min={0.001} step={0.001} value={newQty} onChange={e => setNewQty(e.target.value)} style={{ ...inputS, width: '100%' }} />
 </div>
 <div style={{ width: 100 }}>
 <div style={{ fontSize: 10, color: C.dim, marginBottom: 4 }}>{t('produccion.field_unit')}</div>
 <input type="text" placeholder={t('produccion.placeholder_unit')} value={newUnit} onChange={e => setNewUnit(e.target.value)} style={{ ...inputS, width: '100%' }} />
 </div>
 <div style={{ display: 'flex', gap: 6 }}>
 <button onClick={handleAdd} disabled={saving || !newChild || !newQty} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '7px 14px', borderRadius: 7, background: C.green, color: '#fff', fontSize: 12, fontWeight: 600, opacity: saving || !newChild ? 0.6 : 1 }}>
 {saving ? <Spinner size={11} /> : <Save size={11} />} {t('produccion.btn_save')}
 </button>
 <button onClick={() => { setAddMode(false); setError(null) }} style={{ all: 'unset', cursor: 'pointer', padding: '7px 10px', borderRadius: 7, border: `1px solid ${C.border}`, color: C.dim }}>
 <X size={12} />
 </button>
 </div>
 </div>
 )}
 {bomItems.length === 0 && !addMode ? (
 <div style={{ padding: '28px 16px', textAlign: 'center', color: C.dim, fontSize: 13 }}>
 {t('produccion.empty_bom_line1')}
 {t('produccion.empty_bom_line2')}
 </div>
 ) : (
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 {[t('produccion.col_component_material'), t('produccion.col_type'), t('produccion.col_qty_per_unit_full'), t('produccion.col_current_stock'), ''].map(h => (
 <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: C.dim, fontWeight: 600, fontSize: 10, borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {bomItems.map((item, i) => (
 <tr key={item.child_sku} style={{ background: i % 2 === 0 ? C.surface : C.card, borderBottom: `1px solid ${C.border}` }}>
 <td style={{ padding: '10px 12px' }}>
 <div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{item.child_sku}</div>
 {item.child_name && <div style={{ fontSize: 11, color: C.muted }}>{item.child_name}</div>}
 </td>
 <td style={{ padding: '10px 12px' }}><TypeBadge type={item.child_type || 'component'} /></td>
 <td style={{ padding: '10px 12px', color: C.text }}>{fmt(item.quantity)}{item.unit ? ` ${item.unit}` : ` ${t('produccion.unit_fallback')}`}</td>
 <td style={{ padding: '10px 12px', color: C.muted }}>{fmt(item.child_stock, 0)}</td>
 <td style={{ padding: '10px 12px' }}>
 <button onClick={() => handleDelete(item.child_sku)} style={{ all: 'unset', cursor: 'pointer', color: C.dim, display: 'flex' }} onMouseEnter={e => (e.currentTarget.style.color = C.red)} onMouseLeave={e => (e.currentTarget.style.color = C.dim)}>
 <Trash2 size={13} />
 </button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 </>
 )}
 </div>
 )}

 {/* Hint */}
 <div style={{ display: 'flex', gap: 10, padding: '12px 16px', background: 'rgba(129,140,248,0.04)', border: `1px solid rgba(129,140,248,0.15)`, borderRadius: 8, fontSize: 12, color: C.muted }}>
 <Info size={14} color={C.indigo} style={{ flexShrink: 0, marginTop: 1 }} />
 <span>{t('produccion.hint_bom_part1')} <Link href="/inventory" style={{ color: C.indigo }}>{t('produccion.hint_bom_link')}</Link> {t('produccion.hint_bom_part2')}</span>
 </div>
 </div>
 )
}

// ── Main ─────────────────────────────────────────────────────────────────────
export default function ProduccionPage() {
 const { t } = useLanguage()
 const { sessionId, setSessionId, currentSession, completedSessions, error: sessionsError, refresh: refreshSessions } = useAutoSession()
 const [activeTab, setActiveTab] = useState<'plan' | 'bom'>('plan')
 const [plan, setPlan] = useState<ProductionPlan | null>(null)
 const [loading, setLoading] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [horizonDays, setHorizonDays] = useState(30)
 const [allSkus, setAllSkus] = useState<InventoryStock[]>([])
 const [skusError, setSkusError] = useState<string | null>(null)
 const [expandedFGs, setExpandedFGs] = useState<Set<string>>(new Set())

 const loadSkus = useCallback(() => {
 setSkusError(null)
 listInventoryStock().then(setAllSkus).catch((e: unknown) => {
 setSkusError(e instanceof Error ? e.message : t('produccion.err_load_skus'))
 })
 }, [t])

 useEffect(() => { loadSkus() }, [loadSkus])

 const loadPlan = useCallback(async (sid: string, days: number) => {
 if (!sid) return
 setLoading(true); setError(null)
 try { setPlan(await getProductionRequirements(sid, days)) }
 catch (e: unknown) { setError(e instanceof Error ? e.message : t('produccion.err_loading_plan')) }
 finally { setLoading(false) }
 }, [t])

 useEffect(() => { if (sessionId && activeTab === 'plan') loadPlan(sessionId, horizonDays) }, [sessionId, horizonDays, activeTab])

 const totalToProduceUnits = useMemo(() =>
 plan?.finished_goods.reduce((s, fg) => s + fg.to_produce, 0) ?? 0, [plan])
 const shortageCount = useMemo(() =>
 plan?.raw_material_summary.filter(r => r.status === 'SHORTAGE').length ?? 0, [plan])

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

 {/* Header */}
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <div style={{ width: 36, height: 36, borderRadius: 9, background: 'linear-gradient(135deg, #f97316, #ea580c)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
 <Cog size={18} color="#fff" strokeWidth={2.5} />
 </div>
 <div>
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>{t('produccion.title')}</h1>
 <p style={{ margin: 0, fontSize: 11, color: C.dim }}>{t('produccion.subtitle')}</p>
 </div>
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
 {/* Session */}
 <SessionBar
 currentSession={currentSession}
 completedSessions={completedSessions}
 sessionId={sessionId}
 onSelect={id => { setSessionId(id); if (activeTab === 'plan') loadPlan(id, horizonDays) }}
 />

 {/* Horizon */}
 <div style={{ position: 'relative' }}>
 <select value={horizonDays} onChange={e => setHorizonDays(Number(e.target.value))} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, color: C.text, fontSize: 12, padding: '7px 28px 7px 10px', outline: 'none', appearance: 'none' as const }}>
 {[7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d} {t('produccion.unit_days')}</option>)}
 </select>
 <ChevronDown size={11} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: C.dim }} />
 </div>

 <button onClick={() => sessionId && loadPlan(sessionId, horizonDays)} disabled={loading} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 8, color: C.dim, opacity: loading ? 0.5 : 1 }}>
 <RefreshCw size={13} />
 </button>
 </div>
 </div>

 {/* Error */}
 {error && (
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', fontSize: 13, color: C.red }}>
 <AlertTriangle size={13} />{error}
 <button onClick={() => setError(null)} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', color: C.dim }}><X size={13} /></button>
 </div>
 )}

 {/* Tabs */}
 <div style={{ display: 'flex', gap: 0, borderBottom: `1px solid ${C.border}` }}>
 {([['plan', <Layers size={13} />, t('produccion.tab_requirements')], ['bom', <Cog size={13} />, t('produccion.tab_bom_editor')]] as const).map(([tab, icon, label]) => (
 <button key={tab} onClick={() => setActiveTab(tab)} style={{
 all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7,
 padding: '10px 18px', fontSize: 12, fontWeight: activeTab === tab ? 600 : 400,
 color: activeTab === tab ? C.orange : C.dim,
 borderBottom: `2px solid ${activeTab === tab ? C.orange : 'transparent'}`,
 marginBottom: -1, transition: 'all 0.15s',
 }}>
 {icon}{label}
 </button>
 ))}
 </div>

 {/* Tab: Production Plan */}
 {activeTab === 'plan' && (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
 {loading ? (
 <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
 ) : sessionsError ? (
 <div style={{ padding: '48px 32px', textAlign: 'center' }}>
 <AlertTriangle size={32} color={C.red} style={{ margin: '0 auto 12px', opacity: 0.7 }} />
 <div style={{ fontSize: 13, color: C.text, marginBottom: 16, maxWidth: 420, margin: '0 auto 16px' }}>{sessionsError}</div>
 <button onClick={refreshSessions} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', borderRadius: 8, background: C.orange, color: '#fff', fontSize: 13, fontWeight: 600 }}>
 <RefreshCw size={12} /> {t('produccion.btn_retry')}
 </button>
 </div>
 ) : !sessionId ? (
 <div style={{ padding: '48px 32px', textAlign: 'center', color: C.dim }}>
 <Cog size={36} strokeWidth={1} style={{ margin: '0 auto 12px', opacity: 0.3 }} />
 <div style={{ fontSize: 13, marginBottom: 16 }}>{t('produccion.empty_select_session')}</div>
 <Link href="/quick-start" style={{ fontSize: 13, color: C.indigo, textDecoration: 'none' }}>{t('produccion.link_create_session')}</Link>
 </div>
 ) : !plan || plan.finished_goods_count === 0 ? (
 <div style={{ padding: '40px 32px', textAlign: 'center' }}>
 <Package size={36} strokeWidth={1} color={C.dim} style={{ margin: '0 auto 16px', opacity: 0.3 }} />
 <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 8 }}>{t('produccion.empty_no_fg_title')}</div>
 <div style={{ fontSize: 13, color: C.muted, marginBottom: 24, lineHeight: 1.7, maxWidth: 480, margin: '0 auto 24px' }}>
 {t('produccion.empty_no_fg_intro')}<br />
 {t('produccion.empty_no_fg_step1')}<br />
 {t('produccion.empty_no_fg_step2')}
 </div>
 <button onClick={() => setActiveTab('bom')} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '10px 20px', borderRadius: 8, background: C.orange, color: '#fff', fontSize: 13, fontWeight: 600 }}>
 {t('produccion.btn_go_to_bom_editor')} <ArrowRight size={13} />
 </button>
 </div>
 ) : (
 <>
 {/* Shortage banner */}
 {plan.has_shortages && (
 <div style={{ padding: '12px 16px', borderRadius: 9, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.25)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
 <AlertTriangle size={15} color={C.red} style={{ flexShrink: 0, marginTop: 1 }} />
 <div>
 <div style={{ fontSize: 13, fontWeight: 700, color: C.red }}>
 {shortageCount} {shortageCount > 1 ? t('produccion.materials_shortage_plural') : t('produccion.materials_shortage_singular')} {t('produccion.shortage_for_plan_of')} {horizonDays} {t('produccion.unit_days')}
 </div>
 <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
 {t('produccion.label_estimated_purchase_value')}{' '}
 <strong style={{ color: C.text }}>{formatMoney(plan.total_shortage_value)}</strong>
 </div>
 </div>
 </div>
 )}

 {/* KPIs */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
 {[
 { label: t('produccion.kpi_products_to_plan'), value: plan.finished_goods_count, color: C.orange },
 { label: t('produccion.kpi_total_to_produce'), value: fmt(totalToProduceUnits, 0), color: C.indigo },
 { label: t('produccion.kpi_materials_in_shortage'), value: shortageCount, color: shortageCount > 0 ? C.red : C.green },
 { label: t('produccion.kpi_value_to_purchase'), value: plan.total_shortage_value > 0 ? formatMoney(plan.total_shortage_value) : '—', color: plan.total_shortage_value > 0 ? C.red : C.green },
 ].map(({ label, value, color }) => (
 <div key={label} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: '14px 18px', borderTop: `3px solid ${color}` }}>
 <div style={{ fontSize: 20, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 4 }}>{label}</div>
 </div>
 ))}
 </div>

 {/* Raw material summary */}
 {plan.raw_material_summary.length > 0 && (
 <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
 <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, background: C.card }}>
 <div style={{ fontSize: 13, fontWeight: 600 }}>{t('produccion.materials_summary_title')}</div>
 <div style={{ fontSize: 11, color: C.dim, marginTop: 2 }}>{t('produccion.materials_summary_subtitle')} {t('produccion.label_horizon')} {horizonDays} {t('produccion.unit_days')}</div>
 </div>
 <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
 <thead>
 <tr style={{ background: C.card }}>
 {[t('produccion.col_material'), t('produccion.col_type'), t('produccion.col_unit'), t('produccion.col_total_required'), t('produccion.col_in_stock'), t('produccion.col_shortage'), t('produccion.col_status'), t('produccion.col_estimated_cost')].map(h => (
 <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: C.dim, fontWeight: 600, fontSize: 10, borderBottom: `1px solid ${C.border}`, textTransform: 'uppercase' as const }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {plan.raw_material_summary.map((mat, i) => (
 <tr key={mat.sku} style={{ background: i % 2 === 0 ? C.surface : C.card, borderBottom: `1px solid ${C.border}`, borderLeft: `3px solid ${mat.status === 'SHORTAGE' ? C.red : 'transparent'}` }}>
 <td style={{ padding: '10px 12px' }}>
 <div style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11 }}>{mat.sku}</div>
 {mat.display_name !== mat.sku && <div style={{ fontSize: 11, color: C.muted }}>{mat.display_name}</div>}
 </td>
 <td style={{ padding: '10px 12px' }}><TypeBadge type={mat.product_type} /></td>
 <td style={{ padding: '10px 12px', color: C.muted, fontSize: 11 }}>{mat.unit || '—'}</td>
 <td style={{ padding: '10px 12px', fontWeight: 600 }}>{fmt(mat.total_required)}</td>
 <td style={{ padding: '10px 12px', color: C.muted }}>{fmt(mat.current_stock)}</td>
 <td style={{ padding: '10px 12px', fontWeight: 700, color: mat.shortage > 0 ? C.red : C.green }}>{mat.shortage > 0 ? fmt(mat.shortage) : '—'}</td>
 <td style={{ padding: '10px 12px' }}>
 {mat.status === 'SHORTAGE'
 ? <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: C.red, fontSize: 11, fontWeight: 700 }}><AlertTriangle size={11} /> {t('produccion.status_shortage')}</span>
 : <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: C.green, fontSize: 11 }}><CheckCircle2 size={11} /> {t('produccion.status_ok')}</span>}
 </td>
 <td style={{ padding: '10px 12px', color: mat.estimated_cost ? C.red : C.dim, fontFamily: 'monospace', fontSize: 11 }}>
 {formatMoney(mat.estimated_cost)}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )}

 {/* Per finished good */}
 <div>
 <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>{t('produccion.detail_per_fg_title')}</div>
 {plan.finished_goods.map(fg => <FinishedGoodCard key={fg.sku} item={fg} />)}
 </div>
 </>
 )}
 </div>
 )}

 {/* Tab: BOM Editor */}
 {activeTab === 'bom' && (
 skusError ? (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px', borderRadius: 8, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', fontSize: 13, color: C.red }}>
 <AlertTriangle size={14} style={{ flexShrink: 0 }} />
 <span>{skusError}</span>
 <button onClick={loadSkus} style={{ all: 'unset', cursor: 'pointer', marginLeft: 'auto', padding: '5px 10px', border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, fontSize: 11 }}>
 {t('produccion.btn_retry')}
 </button>
 </div>
 ) : <BomEditor allSkus={allSkus} />
 )}
 </div>
 )
}

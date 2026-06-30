'use client'
import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import {
 getSessions, listDataSources, getActivityLogs,
 deleteSession, patchSession, getInventoryDashboardSummary,
} from '@/lib/api'
import type { SessionInfo, ActivityLog, InventoryDashboardSummary } from '@/lib/types'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import {
 Activity, AlertTriangle, CheckCircle2, Clock,
 Database, TrendingUp, Package, ArrowRight, Play, BarChart2,
 ChevronLeft, ChevronRight, Search, X, SlidersHorizontal,
 MessageSquare, Pencil, Trash2, RefreshCw, Bell, Plus, Trash,
 ShoppingCart,
} from 'lucide-react'

const PAGE_SIZE = 10

// ── Inventory Widget ──────────────────────────────────────────────────────────
function InventoryWidget({ sessionId }: { sessionId: string }) {
 const { t } = useLanguage()
 const [data, setData] = useState<InventoryDashboardSummary | null>(null)
 const [loading, setLoading] = useState(true)

 useEffect(() => {
 setLoading(true)
 getInventoryDashboardSummary(sessionId)
 .then(setData)
 .catch(() => {})
 .finally(() => setLoading(false))
 }, [sessionId])

 const hasCritical = data && (data.pedir_ya > 0 || data.pedir_pronto > 0)

 return (
 <div style={{
 background: 'var(--surface)', border: `1px solid ${hasCritical ? 'rgba(239,68,68,0.3)' : 'var(--border)'}`,
 borderRadius: 12, padding: '16px 20px',
 borderTop: hasCritical ? '3px solid #ef4444' : '3px solid #22c55e',
 }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
 <ShoppingCart size={13} color={hasCritical ? '#ef4444' : '#22c55e'} />
 <span style={{ fontSize: 13, fontWeight: 600 }}>{t('dashboard.inventory_title')}</span>
 {data && data.pedir_ya > 0 && (
 <span style={{
 fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20,
 background: 'rgba(239,68,68,0.1)', color: '#ef4444',
 }}>
 {data.pedir_ya} {t('dashboard.inventory_at_risk')}
 </span>
 )}
 </div>
 <Link href="/inventory" style={{ fontSize: 11, color: 'var(--accent)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 3 }}>
 {t('dashboard.inventory_view_board')} <ArrowRight size={10} />
 </Link>
 </div>

 {loading ? (
 <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}><Spinner size={16} /></div>
 ) : !data ? (
 <div style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'center', padding: '8px 0' }}>
 {t('dashboard.inventory_no_data')}
 </div>
 ) : (
 <>
 {/* Signal pills */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginBottom: 12 }}>
 {[
 { label: ` ${t('dashboard.inventory_order_now')}`, value: data.pedir_ya, color: '#ef4444' },
 { label: ` ${t('dashboard.inventory_order_soon')}`, value: data.pedir_pronto, color: '#f59e0b' },
 { label: ` ${t('dashboard.inventory_ok')}`, value: data.ok, color: '#22c55e' },
 { label: ` ${t('dashboard.inventory_overstock')}`, value: data.sobrestock, color: '#3b82f6' },
 ].map(({ label, value, color }) => (
 <div key={label} style={{
 textAlign: 'center', padding: '8px 4px', borderRadius: 8,
 background: 'var(--surface-2)', border: '1px solid var(--border)',
 }}>
 <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
 <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
 </div>
 ))}
 </div>

 {/* Top critical */}
 {data.top_critical.length > 0 && (
 <div>
 <div style={{ fontSize: 10, fontWeight: 600, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
 {t('dashboard.inventory_urgent')}
 </div>
 {data.top_critical.map(item => (
 <div key={item.sku} style={{
 display: 'flex', alignItems: 'center', gap: 8,
 padding: '5px 0', borderBottom: '1px solid var(--border)',
 fontSize: 12,
 }}>
 <span style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 11, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {item.display_name || item.sku}
 </span>
 <span style={{ color: '#ef4444', fontWeight: 600, flexShrink: 0 }}>
 {item.dias_cobertura != null ? `${item.dias_cobertura.toFixed(0)}d` : '—'}
 </span>
 </div>
 ))}
 </div>
 )}
 </>
 )}
 </div>
 )
}

// ── Alert Rules ───────────────────────────────────────────────────────────────

interface AlertRule {
 id: string
 label: string
 metric: 'failed_count' | 'draft_days' | 'no_training_days'
 threshold: number
}

function getRuleLabels(t: (k: string) => string): Record<AlertRule['metric'], string> {
 return {
 failed_count: t('dashboard.rule_failed_sessions'),
 draft_days: t('dashboard.rule_draft_days'),
 no_training_days: t('dashboard.rule_no_training_days'),
 }
}

const STORAGE_KEY = 'fp_alert_rules'

function loadRules(): AlertRule[] {
 try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') } catch { return [] }
}
function saveRules(rules: AlertRule[]) {
 localStorage.setItem(STORAGE_KEY, JSON.stringify(rules))
}

function AlertRulesPanel({ sessions }: { sessions: SessionInfo[] }) {
 const { t } = useLanguage()
 const ruleLabels = getRuleLabels(t)
 const [rules, setRules] = useState<AlertRule[]>([])
 const [addMetric, setAddMetric] = useState<AlertRule['metric']>('failed_count')
 const [addThresh, setAddThresh] = useState(3)
 const [addLabel, setAddLabel] = useState('')
 const [showAdd, setShowAdd] = useState(false)

 useEffect(() => { setRules(loadRules()) }, [])

 function addRule() {
 const rule: AlertRule = {
 id: Date.now().toString(),
 label: addLabel.trim() || ruleLabels[addMetric],
 metric: addMetric,
 threshold: addThresh,
 }
 const next = [...rules, rule]
 setRules(next); saveRules(next)
 setShowAdd(false); setAddLabel('')
 }

 function deleteRule(id: string) {
 const next = rules.filter(r => r.id !== id)
 setRules(next); saveRules(next)
 }

 // Evaluate rules against current sessions
 const now = Date.now()
 const failedCount = sessions.filter(s => s.status === 'FAILED').length
 const lastComplete = sessions.filter(s => s.status === 'COMPLETED')
 .map(s => new Date(s.created_at).getTime()).sort((a, b) => b - a)[0] ?? 0
 const daysSinceComplete = (now - lastComplete) / 86_400_000

 function evalRule(rule: AlertRule): boolean {
 if (rule.metric === 'failed_count') return failedCount > rule.threshold
 if (rule.metric === 'no_training_days') return daysSinceComplete > rule.threshold
 if (rule.metric === 'draft_days') {
 return sessions.some(s =>
 s.status === 'DRAFT' &&
 (now - new Date(s.created_at).getTime()) / 86_400_000 >= rule.threshold
 )
 }
 return false
 }

 const triggered = rules.filter(evalRule)

 return (
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, padding: '16px 20px',
 }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
 <div style={{ fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}>
 <Bell size={13} color={triggered.length > 0 ? '#ef4444' : 'var(--dim)'} />
 {t('dashboard.alert_rules_title')}
 {triggered.length > 0 && (
 <Badge variant="danger">{triggered.length} {t('dashboard.alert_rules_active')}</Badge>
 )}
 </div>
 <button
 onClick={() => setShowAdd(v => !v)}
 title={t('dashboard.alert_rules_add_title')}
 style={{
 all: 'unset', cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 4,
 fontSize: 11, color: 'var(--accent)',
 padding: '3px 8px', borderRadius: 6,
 border: '1px solid rgba(129,140,248,0.3)',
 background: 'rgba(129,140,248,0.08)',
 }}
 >
 <Plus size={11} /> {t('dashboard.alert_rules_add_btn')}
 </button>
 </div>

 {/* Triggered alerts */}
 {triggered.map(rule => (
 <div key={rule.id} style={{
 display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0',
 borderBottom: '1px solid var(--border)',
 }}>
 <AlertTriangle size={12} color="#ef4444" />
 <span style={{ fontSize: 12, flex: 1, color: '#ef4444', fontWeight: 500 }}>
 {rule.label}
 </span>
 <Badge variant="danger">{t('dashboard.alert_rules_triggered')}</Badge>
 </div>
 ))}

 {/* Inactive rules */}
 {rules.filter(r => !evalRule(r)).map(rule => (
 <div key={rule.id} style={{
 display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0',
 borderBottom: '1px solid var(--border)', opacity: 0.6,
 }}>
 <CheckCircle2 size={12} color="#22c55e" />
 <span style={{ fontSize: 12, flex: 1 }}>{rule.label}</span>
 <span style={{ fontSize: 10, color: 'var(--dim)' }}>
 {t('dashboard.alert_rules_threshold')}: {rule.threshold}
 </span>
 <button
 onClick={() => deleteRule(rule.id)}
 style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex', padding: 3 }}
 onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')}
 onMouseLeave={e => (e.currentTarget.style.color = 'var(--dim)')}
 >
 <Trash size={11} />
 </button>
 </div>
 ))}

 {rules.length === 0 && !showAdd && (
 <div style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'center', padding: '8px 0' }}>
 {t('dashboard.alert_rules_empty')}
 </div>
 )}

 {/* Add form */}
 {showAdd && (
 <div style={{
 marginTop: 10, padding: 12,
 background: 'var(--surface-2)', borderRadius: 8,
 border: '1px solid var(--border)',
 display: 'flex', flexDirection: 'column', gap: 8,
 }}>
 <select
 value={addMetric}
 onChange={e => setAddMetric(e.target.value as AlertRule['metric'])}
 style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 color: 'var(--text)', borderRadius: 6, fontSize: 11,
 padding: '5px 8px', outline: 'none',
 }}
 >
 {(Object.keys(ruleLabels) as AlertRule['metric'][]).map(m => (
 <option key={m} value={m}>{ruleLabels[m]}</option>
 ))}
 </select>
 <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
 <span style={{ fontSize: 11, color: 'var(--dim)', whiteSpace: 'nowrap' }}>{t('dashboard.alert_rules_threshold_label')}:</span>
 <input
 type="number" min={1} value={addThresh}
 onChange={e => setAddThresh(Number(e.target.value))}
 style={{
 width: 60, background: 'var(--surface)', border: '1px solid var(--border)',
 color: 'var(--text)', borderRadius: 6, fontSize: 11, padding: '4px 8px', outline: 'none',
 }}
 />
 <input
 type="text" placeholder={t('dashboard.alert_rules_label_placeholder')}
 value={addLabel}
 onChange={e => setAddLabel(e.target.value)}
 style={{
 flex: 1, background: 'var(--surface)', border: '1px solid var(--border)',
 color: 'var(--text)', borderRadius: 6, fontSize: 11, padding: '4px 8px', outline: 'none',
 }}
 />
 </div>
 <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
 <button
 onClick={() => setShowAdd(false)}
 style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)', padding: '4px 10px', border: '1px solid var(--border)', borderRadius: 5 }}
 >
 {t('common.cancel')}
 </button>
 <button
 onClick={addRule}
 style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: '#fff', background: 'var(--accent)', padding: '4px 10px', borderRadius: 5, fontWeight: 600 }}
 >
 {t('dashboard.alert_rules_save')}
 </button>
 </div>
 </div>
 )}
 </div>
 )
}

function getStatusOpts(t: (k: string) => string): Array<{ value: string; label: string }> {
 return [
 { value: '', label: t('dashboard.status_all') },
 { value: 'COMPLETED', label: t('dashboard.status_completed') },
 { value: 'RUNNING', label: t('dashboard.status_running') },
 { value: 'QUEUED', label: t('dashboard.status_queued') },
 { value: 'FAILED', label: t('dashboard.status_failed') },
 { value: 'CANCELLED', label: t('dashboard.status_cancelled') },
 { value: 'DRAFT', label: t('dashboard.status_draft') },
 ]
}

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KPICard({
 label, value, sub, icon: Icon, accent, trend,
}: {
 label: string; value: string | number; sub?: string
 icon: React.ElementType; accent: string; trend?: string
}) {
 return (
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, padding: '18px 20px',
 display: 'flex', flexDirection: 'column', gap: 12,
 }}>
 <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
 <div style={{
 width: 36, height: 36, borderRadius: 9,
 background: accent + '18', display: 'flex',
 alignItems: 'center', justifyContent: 'center',
 }}>
 <Icon size={17} color={accent} strokeWidth={2} />
 </div>
 {trend && (
 <span style={{
 fontSize: 11, fontWeight: 600,
 color: trend.startsWith('+') ? '#22c55e' : '#64748b',
 }}>
 {trend}
 </span>
 )}
 </div>
 <div>
 <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.03em', lineHeight: 1 }}>
 {value}
 </div>
 <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>{label}</div>
 {sub && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{sub}</div>}
 </div>
 </div>
 )
}

// ── Alert Row ─────────────────────────────────────────────────────────────────
function AlertRow({ session }: { session: SessionInfo }) {
 const { t } = useLanguage()
 return (
 <div style={{
 display: 'flex', alignItems: 'center', gap: 12,
 padding: '10px 0', borderBottom: '1px solid var(--border)',
 }}>
 <AlertTriangle size={14} color="#f59e0b" strokeWidth={2} />
 <div style={{ flex: 1 }}>
 <div style={{ fontSize: 13, fontWeight: 500 }}>{session.name}</div>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>
 {session.error || t('dashboard.training_failed_unexpectedly')}
 </div>
 </div>
 <Badge variant="danger">{t('dashboard.error_badge')}</Badge>
 </div>
 )
}

// ── Session continue button ───────────────────────────────────────────────────
function ContinueBtn({ session }: { session: SessionInfo }) {
 const { t } = useLanguage()
 const { status, session_id } = session

 const label =
 status === 'COMPLETED' ? t('dashboard.btn_view_results') :
 status === 'RUNNING' || status === 'QUEUED' ? t('dashboard.btn_view_training') :
 status === 'FAILED' || status === 'CANCELLED' ? t('dashboard.btn_retry') : t('dashboard.btn_continue')

 const icon =
 status === 'COMPLETED' ? <BarChart2 size={11} /> :
 status === 'RUNNING' || status === 'QUEUED' ? <Play size={11} /> :
 <ArrowRight size={11} />

 return (
 <Link href={`/forecast?session=${session_id}`} style={{ textDecoration: 'none' }}>
 <Button variant={status === 'COMPLETED' ? 'primary' : 'ghost'} size="sm" icon={icon}>
 {label}
 </Button>
 </Link>
 )
}

// ── Status dot ────────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: SessionInfo['status'] }) {
 const { t } = useLanguage()
 const color =
 status === 'COMPLETED' ? '#22c55e' :
 status === 'RUNNING' ? '#f59e0b' :
 status === 'QUEUED' ? '#f59e0b' :
 status === 'FAILED' ? '#ef4444' :
 status === 'CANCELLED' ? '#94a3b8' :
 status === 'MODELS_CONFIGURED' ? '#818cf8' :
 status === 'FEATURES_CONFIGURED' ? '#818cf8' :
 status === 'COLUMNS_CONFIGURED' ? '#818cf8' :
 status === 'INSPECTED' ? '#0ea5e9' :
 status === 'DATASET_LOADED' ? '#0ea5e9' : '#64748b'
 const isActive = status === 'RUNNING' || status === 'QUEUED'
 const statusKey = `dashboard.status_dot_${status.toLowerCase()}`
 const label = t(statusKey) !== statusKey ? t(statusKey) : status.replace(/_/g, ' ').toLowerCase()
 return (
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 500, color }}>
 <span style={{
 width: 6, height: 6, borderRadius: '50%', background: color,
 animation: isActive ? 'pulse-dot 1.4s ease-in-out infinite' : undefined,
 }} />
 {label}
 </span>
 )
}

// ── Activity feed ─────────────────────────────────────────────────────────────
function ActivityFeed({ logs }: { logs: ActivityLog[] }) {
 const { t } = useLanguage()
 if (!logs.length) return (
 <div style={{ padding: '12px 0', textAlign: 'center', fontSize: 12, color: 'var(--dim)' }}>
 {t('dashboard.no_recent_activity')}
 </div>
 )
 return (
 <div style={{ display: 'flex', flexDirection: 'column' }}>
 {logs.map((log, i) => (
 <div key={log.id} style={{
 display: 'flex', alignItems: 'flex-start', gap: 9,
 padding: '7px 0',
 borderBottom: i < logs.length - 1 ? '1px solid var(--border)' : 'none',
 }}>
 <span style={{
 width: 6, height: 6, borderRadius: '50%', flexShrink: 0, marginTop: 4,
 background: log.status === 'success' ? '#22c55e' : '#ef4444',
 }} />
 <div style={{ flex: 1, minWidth: 0 }}>
 <div style={{ fontSize: 12, fontWeight: 500 }}>
 {log.action.replace(/[._]/g, ' ')}
 </div>
 {log.resource && (
 <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {log.resource}
 </div>
 )}
 </div>
 <span style={{ fontSize: 10, color: 'var(--dim)', flexShrink: 0 }}>
 {new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
 </span>
 </div>
 ))}
 </div>
 )
}

// ── All Sessions Panel ────────────────────────────────────────────────────────
function AllSessionsPanel({
 sessions, onDelete, onRename,
}: {
 sessions: SessionInfo[]
 onDelete: (id: string) => void
 onRename: (id: string, name: string) => void
}) {
 const { t } = useLanguage()
 const statusOpts = getStatusOpts(t)
 const [search, setSearch] = useState('')
 const [statusFilter, setStatus] = useState('')
 const [page, setPage] = useState(0)
 const [deleteId, setDeleteId] = useState<string | null>(null)
 const [renameId, setRenameId] = useState<string | null>(null)
 const [renameVal, setRenameVal] = useState('')

 const filtered = useMemo(() => {
 const q = search.toLowerCase()
 return sessions
 .filter(s => !statusFilter || s.status === statusFilter)
 .filter(s => !q || s.name.toLowerCase().includes(q) || s.session_id.toLowerCase().includes(q))
 .sort((a, b) => b.created_at.localeCompare(a.created_at))
 }, [sessions, search, statusFilter])

 const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
 const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

 function handleSearch(v: string) { setSearch(v); setPage(0) }
 function handleStatus(v: string) { setStatus(v); setPage(0) }

 function startRename(s: SessionInfo) {
 setRenameId(s.session_id); setRenameVal(s.name); setDeleteId(null)
 }
 function commitRename() {
 if (renameId && renameVal.trim()) onRename(renameId, renameVal.trim())
 setRenameId(null)
 }

 return (
 <div style={{ borderTop: '1px solid var(--border)' }}>
 {/* Toolbar */}
 <div style={{
 display: 'flex', alignItems: 'center', gap: 8,
 padding: '10px 16px', borderBottom: '1px solid var(--border)',
 background: 'var(--surface-2)',
 }}>
 <div style={{
 flex: 1, display: 'flex', alignItems: 'center', gap: 6,
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 6, padding: '5px 10px',
 }}>
 <Search size={12} color="var(--dim)" />
 <input
 value={search}
 onChange={e => handleSearch(e.target.value)}
 placeholder={t('dashboard.search_sessions_placeholder')}
 style={{ flex: 1, background: 'none', border: 'none', outline: 'none', color: 'var(--text)', fontSize: 12 }}
 />
 {search && (
 <button onClick={() => handleSearch('')} style={{ all: 'unset', cursor: 'pointer', display: 'flex', color: 'var(--dim)' }}>
 <X size={11} />
 </button>
 )}
 </div>
 <SlidersHorizontal size={12} color="var(--dim)" />
 <select
 value={statusFilter}
 onChange={e => handleStatus(e.target.value)}
 style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 color: 'var(--text)', borderRadius: 6, fontSize: 12,
 padding: '5px 8px', outline: 'none', cursor: 'pointer',
 }}
 >
 {statusOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
 </select>
 <span style={{ fontSize: 11, color: 'var(--dim)', whiteSpace: 'nowrap' }}>
 {filtered.length} {filtered.length !== 1 ? t('dashboard.sessions_count_plural') : t('dashboard.sessions_count_singular')}
 </span>
 </div>

 {/* Table */}
 {visible.length === 0 ? (
 <div style={{ padding: 32, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
 {t('dashboard.no_sessions_match_filters')}
 </div>
 ) : (
 <table className="data-table">
 <thead>
 <tr><th>{t('dashboard.col_session')}</th><th>{t('dashboard.col_status')}</th><th>{t('dashboard.col_created')}</th><th></th></tr>
 </thead>
 <tbody>
 {visible.map(s => (
 <tr key={s.session_id}>
 <td>
 {renameId === s.session_id ? (
 <input
 autoFocus
 value={renameVal}
 onChange={e => setRenameVal(e.target.value)}
 onKeyDown={e => {
 if (e.key === 'Enter') commitRename()
 if (e.key === 'Escape') setRenameId(null)
 }}
 onBlur={commitRename}
 style={{
 background: 'var(--surface-2)', border: '1px solid var(--accent)',
 borderRadius: 5, padding: '2px 7px', fontSize: 12,
 color: 'var(--text)', outline: 'none', width: 200,
 }}
 />
 ) : (
 <>
 <div style={{ fontWeight: 500 }}>{s.name}</div>
 <div style={{ fontSize: 10, color: 'var(--dim)', fontFamily: 'monospace' }}>
 {s.session_id.slice(0, 12)}…
 </div>
 </>
 )}
 </td>
 <td><StatusDot status={s.status} /></td>
 <td style={{ color: 'var(--dim)' }}>
 {new Date(s.created_at).toLocaleDateString()}
 </td>
 <td>
 {deleteId === s.session_id ? (
 <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
 <span style={{ fontSize: 11, color: '#ef4444', fontWeight: 500 }}>{t('dashboard.delete_confirm_question')}</span>
 <button
 onClick={() => { onDelete(s.session_id); setDeleteId(null) }}
 style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: '#ef4444', fontWeight: 600, padding: '2px 8px', border: '1px solid #ef4444', borderRadius: 5 }}
 >
 {t('common.yes')}
 </button>
 <button
 onClick={() => setDeleteId(null)}
 style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)', padding: '2px 8px', border: '1px solid var(--border)', borderRadius: 5 }}
 >
 {t('common.no')}
 </button>
 </div>
 ) : (
 <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
 <ContinueBtn session={s} />
 <button
 title={t('dashboard.rename_title')}
 onClick={() => startRename(s)}
 style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: '4px', borderRadius: 4, display: 'flex' }}
 onMouseEnter={e => (e.currentTarget.style.color = 'var(--accent)')}
 onMouseLeave={e => (e.currentTarget.style.color = 'var(--dim)')}
 >
 <Pencil size={11} />
 </button>
 <button
 title={t('dashboard.delete_title')}
 onClick={() => { setDeleteId(s.session_id); setRenameId(null) }}
 style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: '4px', borderRadius: 4, display: 'flex' }}
 onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')}
 onMouseLeave={e => (e.currentTarget.style.color = 'var(--dim)')}
 >
 <Trash2 size={11} />
 </button>
 </div>
 )}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 )}

 {/* Pagination */}
 {totalPages > 1 && (
 <div style={{
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 padding: '10px 16px', borderTop: '1px solid var(--border)',
 }}>
 <span style={{ fontSize: 11, color: 'var(--dim)' }}>
 {t('dashboard.page_label')} {page + 1} {t('dashboard.page_of')} {totalPages}
 </span>
 <div style={{ display: 'flex', gap: 4 }}>
 <button
 disabled={page === 0}
 onClick={() => setPage(p => p - 1)}
 style={{
 all: 'unset', cursor: page === 0 ? 'default' : 'pointer',
 padding: '4px 8px', borderRadius: 6, fontSize: 12,
 display: 'flex', alignItems: 'center',
 color: page === 0 ? 'var(--dim)' : 'var(--text)',
 background: 'var(--surface-2)', border: '1px solid var(--border)',
 opacity: page === 0 ? 0.4 : 1,
 }}
 >
 <ChevronLeft size={13} />
 </button>
 {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
 const n = totalPages <= 5 ? i : Math.max(0, Math.min(page - 2, totalPages - 5)) + i
 return (
 <button
 key={n}
 onClick={() => setPage(n)}
 style={{
 all: 'unset', cursor: 'pointer',
 padding: '4px 9px', borderRadius: 6, fontSize: 12,
 background: n === page ? 'var(--accent)' : 'var(--surface-2)',
 color: n === page ? '#fff' : 'var(--text)',
 border: '1px solid var(--border)',
 }}
 >
 {n + 1}
 </button>
 )
 })}
 <button
 disabled={page >= totalPages - 1}
 onClick={() => setPage(p => p + 1)}
 style={{
 all: 'unset', cursor: page >= totalPages - 1 ? 'default' : 'pointer',
 padding: '4px 8px', borderRadius: 6, fontSize: 12,
 display: 'flex', alignItems: 'center',
 color: page >= totalPages - 1 ? 'var(--dim)' : 'var(--text)',
 background: 'var(--surface-2)', border: '1px solid var(--border)',
 opacity: page >= totalPages - 1 ? 0.4 : 1,
 }}
 >
 <ChevronRight size={13} />
 </button>
 </div>
 </div>
 )}
 </div>
 )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function DashboardPage() {
 const { t } = useLanguage()
 const [sessions, setSessions] = useState<SessionInfo[]>([])
 const [loading, setLoading] = useState(true)
 const [error, setError] = useState<string | null>(null)
 const [showAll, setShowAll] = useState(false)
 const [dsCount, setDsCount] = useState<number | null>(null)
 const [activityLogs, setActivityLogs] = useState<ActivityLog[]>([])
 const [refreshing, setRefreshing] = useState(false)
 const [fetchErrors, setFetchErrors] = useState<string[]>([])
 const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

 const loadSessions = useCallback((quiet = false) => {
 if (quiet) setRefreshing(true); else setLoading(true)
 return getSessions()
 .then(setSessions)
 .catch(e => { if (!quiet) setError(e.message) })
 .finally(() => { setLoading(false); setRefreshing(false) })
 }, [])

 useEffect(() => {
 loadSessions()
 listDataSources(0, 1).then(r => setDsCount(r.total)).catch(() => setFetchErrors(e => [...e, t('dashboard.err_load_datasources')]))
 getActivityLogs({ limit: 8 }).then(r => setActivityLogs(r.items)).catch(() => setFetchErrors(e => [...e, t('dashboard.err_load_activity')]))
 }, [])

 // Auto-refresh every 5 s when jobs are active
 const training = sessions.filter(s => s.status === 'RUNNING' || s.status === 'QUEUED').length
 useEffect(() => {
 if (pollRef.current) clearInterval(pollRef.current)
 if (training > 0) {
 pollRef.current = setInterval(() => {
 getSessions().then(setSessions).catch(() => {})
 }, 5000)
 }
 return () => { if (pollRef.current) clearInterval(pollRef.current) }
 }, [training])

 const handleDelete = useCallback((id: string) => {
 deleteSession(id)
 .then(() => setSessions(prev => prev.filter(s => s.session_id !== id)))
 .catch((e: unknown) => {
 setFetchErrors(prev => [...prev, e instanceof Error ? e.message : t('dashboard.err_delete_session')])
 })
 }, [t])

 const handleRename = useCallback((id: string, name: string) => {
 patchSession(id, { name })
 .then(updated => setSessions(prev => prev.map(s => s.session_id === id ? updated : s)))
 .catch((e: unknown) => {
 setFetchErrors(prev => [...prev, e instanceof Error ? e.message : t('dashboard.err_rename_session')])
 })
 }, [t])

 const trained = sessions.filter(s => s.status === 'COMPLETED').length
 const errors = sessions.filter(s => s.status === 'FAILED')
 const loaded = sessions.filter(s => s.status === 'DATASET_LOADED' || s.status === 'INSPECTED').length
 const recent = [...sessions].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8)

 // Sessions created this week vs last week for trend chip
 const now = Date.now()
 const thisWeek = sessions.filter(s => now - new Date(s.created_at).getTime() < 7 * 86_400_000).length
 const sessionTrend = thisWeek > 0 ? `+${thisWeek} ${t('dashboard.this_week')}` : undefined

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 24, animation: 'fadeIn 0.3s ease-out' }}>

 {fetchErrors.length > 0 && (
 <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.25)', fontSize: 12, color: '#f87171' }}>
 <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
 <span style={{ flex: 1 }}>{fetchErrors.join(' ')}</span>
 <button onClick={() => setFetchErrors([])} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#f87171', padding: 0, lineHeight: 1 }}>✕</button>
 </div>
 )}

 {/* KPI grid */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16 }}>
 <KPICard
 label={t('dashboard.kpi_total_sessions')}
 value={loading ? '–' : sessions.length}
 icon={Activity}
 accent="#818cf8"
 sub={t('dashboard.kpi_across_datasets')}
 trend={sessionTrend}
 />
 <KPICard
 label={t('dashboard.kpi_trained_models')}
 value={loading ? '–' : trained}
 icon={CheckCircle2}
 accent="#22c55e"
 sub={`${sessions.length ? Math.round(trained / sessions.length * 100) : 0}% ${t('dashboard.kpi_of_sessions')}`}
 />
 <KPICard
 label={t('dashboard.kpi_in_training')}
 value={loading ? '–' : training}
 icon={Clock}
 accent="#f59e0b"
 sub={training > 0 ? t('dashboard.kpi_auto_refreshing') : t('dashboard.kpi_no_active_jobs')}
 />
 <KPICard
 label={t('dashboard.kpi_awaiting_config')}
 value={loading ? '–' : loaded}
 icon={Database}
 accent="#0ea5e9"
 sub={t('dashboard.kpi_data_loaded_not_trained')}
 />
 <KPICard
 label={t('dashboard.kpi_datasets')}
 value={dsCount === null ? '–' : dsCount}
 icon={Package}
 accent="#a78bfa"
 sub={t('dashboard.kpi_connected_sources')}
 />
 </div>

 {/* Body grid */}
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 16 }}>

 {/* Sessions card */}
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, overflow: 'hidden',
 }}>
 <div style={{
 padding: '16px 20px', borderBottom: '1px solid var(--border)',
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 }}>
 <div>
 <div style={{ fontSize: 13, fontWeight: 600 }}>
 {showAll ? t('dashboard.all_sessions_title') : t('dashboard.recent_sessions_title')}
 </div>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>
 {showAll ? `${sessions.length} ${t('dashboard.total_label')}` : t('dashboard.last_8_sessions')}
 </div>
 </div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
 <button
 onClick={() => loadSessions(true)}
 title={t('dashboard.refresh_title')}
 style={{
 all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center',
 color: refreshing ? 'var(--accent)' : 'var(--dim)', padding: 4, borderRadius: 6,
 opacity: refreshing ? 0.6 : 1,
 }}
 >
 <RefreshCw size={13} />
 </button>
 <Button
 variant="ghost"
 size="sm"
 icon={showAll ? <X size={12} /> : <ArrowRight size={12} />}
 onClick={() => setShowAll(v => !v)}
 >
 {showAll ? t('dashboard.btn_collapse') : t('dashboard.btn_view_all')}
 </Button>
 </div>
 </div>

 {loading ? (
 <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spinner /></div>
 ) : error && !sessions.length ? (
 <div style={{ padding: 24, color: '#ef4444', fontSize: 13 }}>
 {t('dashboard.err_could_not_connect')}: {error}
 </div>
 ) : sessions.length === 0 ? (
 <div style={{ padding: 40, textAlign: 'center', color: 'var(--dim)' }}>
 <Database size={32} strokeWidth={1} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
 <div style={{ fontSize: 13 }}>{t('dashboard.no_sessions_yet')}</div>
 <div style={{ fontSize: 11, marginTop: 4 }}>{t('dashboard.upload_dataset_to_start')}</div>
 </div>
 ) : showAll ? (
 <AllSessionsPanel
 sessions={sessions}
 onDelete={handleDelete}
 onRename={handleRename}
 />
 ) : (
 <table className="data-table">
 <thead>
 <tr><th>{t('dashboard.col_session')}</th><th>{t('dashboard.col_status')}</th><th>{t('dashboard.col_created')}</th><th></th></tr>
 </thead>
 <tbody>
 {recent.map(s => (
 <tr key={s.session_id}>
 <td>
 <div style={{ fontWeight: 500 }}>{s.name}</div>
 <div style={{ fontSize: 10, color: 'var(--dim)', fontFamily: 'monospace' }}>
 {s.session_id.slice(0, 12)}…
 </div>
 </td>
 <td><StatusDot status={s.status} /></td>
 <td style={{ color: 'var(--dim)' }}>
 {new Date(s.created_at).toLocaleDateString()}
 </td>
 <td><ContinueBtn session={s} /></td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 </div>

 {/* Right column */}
 <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

 {/* Alerts */}
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, padding: '16px 20px',
 }}>
 <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
 <AlertTriangle size={13} color="#f59e0b" />
 {t('dashboard.alerts_title')}
 {errors.length > 0 && <Badge variant="warning">{errors.length}</Badge>}
 </div>
 {errors.length === 0 ? (
 <div style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'center', padding: '12px 0' }}>
 <CheckCircle2 size={20} color="#22c55e" style={{ margin: '0 auto 6px' }} />
 {t('dashboard.no_active_alerts')}
 </div>
 ) : (
 errors.map(s => <AlertRow key={s.session_id} session={s} />)
 )}
 </div>

 {/* Inventory widget — shows when there's a completed session */}
 {sessions.find(s => s.status === 'COMPLETED') && (
 <InventoryWidget sessionId={sessions.find(s => s.status === 'COMPLETED')!.session_id} />
 )}

 {/* Quick actions */}
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, padding: '16px 20px',
 }}>
 <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>{t('dashboard.quick_actions_title')}</div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
 {[
 { href: '/data', label: t('dashboard.qa_upload_dataset'), Icon: Database, color: '#0ea5e9' },
 { href: '/forecast', label: t('dashboard.qa_new_forecast_run'), Icon: TrendingUp, color: '#818cf8' },
 { href: '/inventory', label: t('dashboard.qa_inventory'), Icon: ShoppingCart, color: '#22c55e' },
 { href: '/analyst', label: t('dashboard.qa_ai_analyst'), Icon: MessageSquare, color: '#a78bfa' },
 ].map(({ href, label, Icon, color }) => (
 <Link key={href} href={href} style={{ textDecoration: 'none' }}>
 <div style={{
 display: 'flex', alignItems: 'center', gap: 10,
 padding: '10px 12px', borderRadius: 8,
 background: 'var(--surface-2)', border: '1px solid var(--border)',
 cursor: 'pointer', transition: 'all 0.15s',
 }}>
 <div style={{
 width: 28, height: 28, borderRadius: 7,
 background: color + '18', display: 'flex', alignItems: 'center', justifyContent: 'center',
 }}>
 <Icon size={14} color={color} />
 </div>
 <span style={{ fontSize: 13, fontWeight: 500 }}>{label}</span>
 <ArrowRight size={12} color="var(--dim)" style={{ marginLeft: 'auto' }} />
 </div>
 </Link>
 ))}
 </div>
 </div>

 {/* Activity feed */}
 <div style={{
 background: 'var(--surface)', border: '1px solid var(--border)',
 borderRadius: 12, padding: '16px 20px',
 }}>
 <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
 <Activity size={13} color="var(--accent)" />
 {t('dashboard.recent_activity_title')}
 </div>
 <ActivityFeed logs={activityLogs} />
 </div>

 {/* Alert Rules */}
 <AlertRulesPanel sessions={sessions} />

 </div>
 </div>
 </div>
 )
}

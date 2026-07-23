'use client'
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
 listDataSources, createFileSource, createSqlSource, replaceFileSource,
 updateSqlConfig, testSqlConnection, executeSqlQuery, saveSqlQuery,
 getDataSourcePreview, getDataSource, renameDataSource, deleteDataSource,
 analyzeDataSource, analyzeSkuDetail, getEditableTable, saveDatasetAsNew,
} from '@/lib/api'
import type {
 DataSource, DataPreview, EditableTable, SqlQueryResult, SqlEngine,
 AnalysisResult, AnalysisSummaryRow, SkuDetailResult, OutlierPoint,
} from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import {
 Database, FileSpreadsheet, Upload, Plus, Trash2, RefreshCw,
 CheckCircle2, XCircle, Clock, Edit2, X,
 Play, Save, Link2, Table2, AlertTriangle, Eye,
 Layers, ArrowLeft, BarChart2, ChevronUp, ChevronDown,
} from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/contexts/ToastContext'

// ── Palette ──────────────────────────────────────────────────────────────────
const C = {
 bg: 'var(--bg)',
 surface: 'var(--surface)',
 card: 'var(--surface-2)',
 border: 'var(--border)',
 border2: 'var(--border-strong)',
 green: '#10b981',
 greenDim: '#10b98120',
 greenHov: '#059669',
 blue: '#3b82f6',
 blueDim: '#3b82f615',
 amber: '#f59e0b',
 red: '#ef4444',
 redDim: '#ef444418',
 text: 'var(--text)',
 muted: 'var(--muted)',
 muted2: 'var(--surface-3)',
}

// ── Column auto-detection heuristic ──────────────────────────────────────────
function guessColumns(cols: string[]): { dateCol: string; targetCol: string; skuCol: string } {
 const lower = cols.map(c => c.toLowerCase())
 const find = (hints: string[]) => {
 for (const h of hints) {
 const i = lower.findIndex(c => c.includes(h))
 if (i >= 0) return cols[i]
 }
 return ''
 }
 return {
 dateCol: find(['date', 'fecha', 'dt', 'time', 'period', 'week', 'month', 'year', 'dia', 'semana', 'mes']),
 targetCol: find(['sales', 'ventas', 'demand', 'qty', 'quantity', 'units', 'target', 'value', 'amount', 'revenue', 'uds', 'venta']),
 skuCol: find(['sku', 'product', 'item', 'grupo', 'group', 'category', 'categoria', 'codigo', 'code']),
 }
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
 const { t } = useLanguage()
 const cfg = {
 connected: { icon: CheckCircle2, color: C.green, label: t('data.status_connected') },
 pending: { icon: Clock, color: C.amber, label: t('data.status_pending') },
 error: { icon: XCircle, color: C.red, label: t('data.status_error') },
 }[status] ?? { icon: Clock, color: C.muted, label: status }
 const Icon = cfg.icon
 return (
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4,
 padding: '2px 8px', borderRadius: 20, background: cfg.color + '18',
 color: cfg.color, fontSize: 11, fontWeight: 600 }}>
 <Icon size={10} /> {cfg.label}
 </span>
 )
}

// ── Source icon ───────────────────────────────────────────────────────────────
function SourceIcon({ type, size = 16 }: { type: string; size?: number }) {
 return type === 'sql'
 ? <Database size={size} color={C.blue} />
 : <FileSpreadsheet size={size} color={C.green} />
}

// ── Format bytes ──────────────────────────────────────────────────────────────
function fmt(bytes: number | null) {
 if (!bytes) return '—'
 if (bytes < 1024) return `${bytes} B`
 if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
 return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ── Data grid ─────────────────────────────────────────────────────────────────
function DataGrid({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
 const { t } = useLanguage()
 if (!columns.length) return <p style={{ color: C.muted, padding: 20 }}>{t('common.no_data')}</p>
 return (
 <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 380, borderRadius: 8,
 border: `1px solid ${C.border}` }}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 {columns.map(c => (
 <th key={c} style={{ padding: '8px 12px', textAlign: 'left', whiteSpace: 'nowrap',
 background: 'var(--surface-2)', color: C.muted, fontWeight: 600, fontSize: 11,
 borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0 }}>
 {c}
 </th>
 ))}
 </tr>
 </thead>
 <tbody>
 {rows.map((row, i) => (
 <tr key={i} style={{ background: i % 2 === 0 ? C.card : C.surface }}>
 {columns.map(c => (
 <td key={c} style={{ padding: '6px 12px', color: C.text, whiteSpace: 'nowrap',
 borderBottom: `1px solid ${C.border}`, maxWidth: 220,
 overflow: 'hidden', textOverflow: 'ellipsis' }}>
 {row[c] == null ? <span style={{ color: C.muted }}>null</span> : String(row[c])}
 </td>
 ))}
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )
}

// ── Sparkline (mini SVG chart) ────────────────────────────────────────────────
function Sparkline({ values, color = C.green, w = 160, h = 36 }: {
 values: number[]; color?: string; w?: number; h?: number
}) {
 if (values.length < 2) return <span style={{ color: C.muted, fontSize: 11 }}>—</span>
 const min = Math.min(...values), max = Math.max(...values)
 const rng = max - min || 1
 const pts = values.map((v, i) =>
 `${(i / (values.length - 1)) * w},${h - 2 - ((v - min) / rng) * (h - 4)}`
 ).join(' ')
 return (
 <svg width={w} height={h} style={{ display: 'block', overflow: 'visible' }}>
 <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// ── Line chart (full-width SVG) ───────────────────────────────────────────────
function LineChart({ data, color = C.green, height = 130, outliers, showOutliers }: {
 data: { date: string; value: number | null }[]
 color?: string; height?: number
 outliers?: OutlierPoint[]
 showOutliers?: boolean
}) {
 const { t } = useLanguage()
 const valid = data.filter(d => d.value != null) as { date: string; value: number }[]
 if (valid.length < 2) return <p style={{ color: C.muted, fontSize: 12, textAlign: 'center', padding: 20 }}>{t('data.not_enough_data')}</p>
 const VW = 720, VH = height
 const P = { t: 10, b: 22, l: 46, r: 8 }
 const CW = VW - P.l - P.r, CH = VH - P.t - P.b
 const vals = valid.map(d => d.value)
 const minV = Math.min(...vals), maxV = Math.max(...vals)
 const rng = maxV - minV || 1
 const X = (i: number) => P.l + (i / (valid.length - 1)) * CW
 const Y = (v: number) => P.t + CH - ((v - minV) / rng) * CH
 const pts = valid.map((d, i) => `${X(i)},${Y(d.value)}`).join(' ')
 const yTicks = [0, 0.5, 1].map(f => ({ y: P.t + CH * (1 - f), label: (minV + rng * f).toFixed(1) }))
 const xLabels = [0, Math.floor(valid.length / 2), valid.length - 1].map(i => ({ x: X(i), label: valid[i].date.slice(0, 7) }))
 const outlierMap = (showOutliers && outliers?.length)
 ? new Map(outliers.map(o => [o.date, o]))
 : null
 const fences = outlierMap && outliers?.length ? { lo: outliers[0].lower_bound, hi: outliers[0].upper_bound } : null
 return (
 <svg width="100%" viewBox={`0 0 ${VW} ${VH}`} style={{ overflow: 'visible', display: 'block' }}>
 {yTicks.map(t => (
 <g key={t.label}>
 <line x1={P.l} y1={t.y} x2={VW - P.r} y2={t.y} stroke={C.border} strokeWidth={1} />
 <text x={P.l - 4} y={t.y + 4} textAnchor="end" fontSize={9} fill={C.muted}>{t.label}</text>
 </g>
 ))}
 {/* IQR fence lines — dashed red horizontals at Q1−1.5IQR and Q3+1.5IQR */}
 {fences && fences.lo > minV && fences.lo < maxV && (
 <line x1={P.l} y1={Y(fences.lo)} x2={VW - P.r} y2={Y(fences.lo)}
 stroke={C.red} strokeWidth={1} strokeDasharray="4 3" opacity={0.45} />
 )}
 {fences && fences.hi > minV && fences.hi < maxV && (
 <line x1={P.l} y1={Y(fences.hi)} x2={VW - P.r} y2={Y(fences.hi)}
 stroke={C.red} strokeWidth={1} strokeDasharray="4 3" opacity={0.45} />
 )}
 <polygon
 points={`${X(0)},${P.t + CH} ${pts} ${X(valid.length - 1)},${P.t + CH}`}
 fill={color} opacity={0.07}
 />
 <polyline points={pts} fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
 {/* Outlier markers — halo + filled dot */}
 {outlierMap && valid.map((d, i) => {
 if (!outlierMap.has(d.date)) return null
 return (
 <g key={`o-${d.date}`}>
 <circle cx={X(i)} cy={Y(d.value)} r={7} fill={`${C.red}20`} />
 <circle cx={X(i)} cy={Y(d.value)} r={3.5} fill={C.red} opacity={0.9} />
 </g>
 )
 })}
 {xLabels.map(l => (
 <text key={l.label} x={l.x} y={VH - 4} textAnchor="middle" fontSize={9} fill={C.muted}>{l.label}</text>
 ))}
 </svg>
 )
}

// ── Drag-drop upload zone ─────────────────────────────────────────────────────
function DropZone({ onFile, compact }: { onFile: (f: File) => void; compact?: boolean }) {
 const { t } = useLanguage()
 const [drag, setDrag] = useState(false)
 const ref = useRef<HTMLInputElement>(null)
 const onDrop = (e: React.DragEvent) => {
 e.preventDefault(); setDrag(false)
 const f = e.dataTransfer.files[0]
 if (f) onFile(f)
 }
 return (
 <div
 onDragOver={e => { e.preventDefault(); setDrag(true) }}
 onDragLeave={() => setDrag(false)}
 onDrop={onDrop}
 onClick={() => ref.current?.click()}
 style={{
 border: `2px dashed ${drag ? C.green : C.border2}`,
 borderRadius: 12, padding: compact ? '24px 20px' : '40px 20px',
 textAlign: 'center', cursor: 'pointer', transition: 'all 0.2s',
 background: drag ? C.greenDim : 'transparent',
 }}
 >
 <input ref={ref} type="file" accept=".csv,.xlsx,.xls,.parquet,.json"
 style={{ display: 'none' }}
 onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
 <Upload size={compact ? 24 : 32} color={drag ? C.green : C.muted} style={{ margin: '0 auto 8px' }} />
 <p style={{ color: drag ? C.green : C.text, fontWeight: 600, margin: '0 0 4px' }}>
 {drag ? t('data.drop_to_upload') : t('data.drag_or_browse')}
 </p>
 <p style={{ color: C.muted, fontSize: 12, margin: 0 }}>{t('data.file_types_hint')}</p>
 </div>
 )
}

// ── SQL Connection Form ───────────────────────────────────────────────────────
interface SqlFormData {
 name: string; description: string; engine: SqlEngine
 host: string; port: string; database: string; username: string; password: string
}
const SQL_DEFAULTS: SqlFormData = {
 name: '', description: '', engine: 'postgresql',
 host: 'localhost', port: '5432', database: '', username: '', password: '',
}
const ENGINE_PORTS: Record<string, string> = {
 postgresql: '5432', mysql: '3306', mssql: '1433', oracle: '1521',
}

function SqlForm({ initial, onSave, onCancel, saving, isEdit }:
 { initial?: Partial<SqlFormData>; onSave: (d: SqlFormData) => void; onCancel?: () => void; saving?: boolean; isEdit?: boolean }
) {
 const { t } = useLanguage()
 const [form, setForm] = useState<SqlFormData>({ ...SQL_DEFAULTS, ...initial })
 const set = (k: keyof SqlFormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
 setForm(f => ({ ...f, [k]: e.target.value }))
 const inputStyle: React.CSSProperties = {
 width: '100%', background: C.surface, border: `1px solid ${C.border2}`,
 borderRadius: 8, padding: '9px 12px', color: C.text, fontSize: 13,
 outline: 'none', boxSizing: 'border-box',
 }
 const labelStyle: React.CSSProperties = { color: C.muted, fontSize: 12, fontWeight: 600, marginBottom: 4, display: 'block' }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
 {!isEdit && (
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
 <div>
 <label style={labelStyle}>{t('data.field_source_name')} *</label>
 <input style={inputStyle} value={form.name} onChange={set('name')} placeholder={t('data.field_source_name_ph')} />
 </div>
 <div>
 <label style={labelStyle}>{t('data.field_description')}</label>
 <input style={inputStyle} value={form.description} onChange={set('description')} placeholder={t('data.field_optional_ph')} />
 </div>
 </div>
 )}
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
 <div>
 <label style={labelStyle}>{t('data.field_engine')} *</label>
 <select style={inputStyle} value={form.engine} onChange={e => {
 const eng = e.target.value as SqlEngine
 setForm(f => ({ ...f, engine: eng, port: ENGINE_PORTS[eng] || f.port }))
 }}>
 {['postgresql', 'mysql', 'mssql', 'oracle'].map(e => (
 <option key={e} value={e}>{e}</option>
 ))}
 </select>
 </div>
 <div>
 <label style={labelStyle}>{t('data.field_host')} *</label>
 <input style={inputStyle} value={form.host} onChange={set('host')} placeholder="localhost" />
 </div>
 <div>
 <label style={labelStyle}>{t('data.field_port')} *</label>
 <input style={inputStyle} value={form.port} onChange={set('port')} type="number" />
 </div>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
 <div>
 <label style={labelStyle}>{t('data.field_database')} *</label>
 <input style={inputStyle} value={form.database} onChange={set('database')} placeholder={t('data.field_database_ph')} />
 </div>
 <div>
 <label style={labelStyle}>{t('data.field_username')} *</label>
 <input style={inputStyle} value={form.username} onChange={set('username')} placeholder={t('data.field_username_ph')} />
 </div>
 <div>
 <label style={labelStyle}>{t('data.field_password')} {isEdit && <span style={{ fontWeight: 400 }}>{t('data.field_password_keep')}</span>}</label>
 <input style={inputStyle} type="password" value={form.password} onChange={set('password')} placeholder="••••••••" />
 </div>
 </div>
 <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
 {onCancel && (
 <button onClick={onCancel} style={{ padding: '9px 18px', borderRadius: 8,
 background: 'transparent', border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer' }}>
 {t('common.cancel')}
 </button>
 )}
 <button onClick={() => onSave(form)} disabled={saving}
 style={{ padding: '9px 20px', borderRadius: 8, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer',
 opacity: saving ? 0.7 : 1, display: 'flex', alignItems: 'center', gap: 6 }}>
 {saving ? <Spinner size={14} /> : <Save size={14} />}
 {isEdit ? t('data.btn_save_changes') : t('data.btn_create_connection')}
 </button>
 </div>
 </div>
 )
}

// ── SQL Editor Panel ──────────────────────────────────────────────────────────
function SqlEditorPanel({ source, onSaved }: { source: DataSource; onSaved: (s: DataSource) => void }) {
 const { t } = useLanguage()
 const [sql, setSql] = useState(source.saved_query || '')
 const [result, setResult] = useState<SqlQueryResult | null>(null)
 const [running, setRunning] = useState(false)
 const [saving, setSaving] = useState(false)
 const [err, setErr] = useState<string | null>(null)

 const run = async () => {
 if (!sql.trim()) return
 setRunning(true); setErr(null)
 try {
 const r = await executeSqlQuery(source.id, sql)
 setResult(r)
 } catch (e: any) { setErr(e.message) }
 finally { setRunning(false) }
 }

 const save = async () => {
 if (!sql.trim()) return
 setSaving(true)
 try {
 const updated = await saveSqlQuery(source.id, sql)
 onSaved(updated)
 } catch (e: any) { setErr(e.message) }
 finally { setSaving(false) }
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
 <span style={{ color: C.muted, fontSize: 12, fontWeight: 600 }}>{t('data.sql_editor_label')}</span>
 <div style={{ display: 'flex', gap: 8 }}>
 <button onClick={save} disabled={saving || !sql.trim()}
 style={{ padding: '6px 14px', borderRadius: 7, background: 'transparent',
 border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
 {saving ? <Spinner size={12} /> : <Save size={12} />} {t('data.btn_save_query')}
 </button>
 <button onClick={run} disabled={running || !sql.trim()}
 style={{ padding: '6px 16px', borderRadius: 7, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
 opacity: running || !sql.trim() ? 0.6 : 1 }}>
 {running ? <Spinner size={12} /> : <Play size={12} />} {t('data.btn_run')}
 </button>
 </div>
 </div>
 <textarea
 value={sql}
 onChange={e => setSql(e.target.value)}
 onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run() } }}
 spellCheck={false}
 placeholder={t('data.query_placeholder')}
 style={{
 width: '100%', minHeight: 140, background: 'var(--surface-2)', border: `1px solid ${C.border2}`,
 borderRadius: 8, padding: '12px 14px',
 fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 13, color: C.text,
 lineHeight: 1.6, resize: 'vertical', outline: 'none', boxSizing: 'border-box',
 }}
 />
 {err && (
 <div style={{ background: C.redDim, border: `1px solid ${C.red}30`,
 borderRadius: 8, padding: '10px 14px', color: C.red, fontSize: 13 }}>
 {err}
 </div>
 )}
 {result && (
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
 <Table2 size={14} color={C.green} />
 <span style={{ color: C.green, fontSize: 12, fontWeight: 600 }}>
 {result.row_count} {result.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}{result.truncated ? ` ${t('data.truncated_suffix')}` : ''}
 </span>
 </div>
 <DataGrid columns={result.columns} rows={result.rows} />
 </div>
 )}
 </div>
 )
}

// ── Analysis: SKU Summary Table ───────────────────────────────────────────────
const TD: React.CSSProperties = {
 padding: '7px 12px', color: 'var(--text)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
}

function AnalysisSummaryTable({ rows, sortCol, sortDir, onSort, onSelect }: {
 rows: AnalysisSummaryRow[]
 sortCol: string; sortDir: 'asc' | 'desc'
 onSort: (col: string) => void
 onSelect: (sku: string) => void
}) {
 const { t } = useLanguage()
 if (!rows.length) return <p style={{ color: C.muted, padding: 20 }}>{t('data.no_skus_analysed')}</p>

 const Hdr = ({ label, col }: { label: string; col: string }) => {
 const active = sortCol === col
 return (
 <th onClick={() => onSort(col)}
 style={{ padding: '9px 12px', textAlign: 'left', whiteSpace: 'nowrap',
 background: 'var(--surface-2)', color: active ? C.green : C.muted, fontWeight: 600,
 fontSize: 11, borderBottom: `1px solid ${C.border}`, cursor: 'pointer',
 position: 'sticky', top: 0, userSelect: 'none' }}>
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
 {label}
 {active ? (sortDir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />) : null}
 </span>
 </th>
 )
 }

 return (
 <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 460,
 borderRadius: 8, border: `1px solid ${C.border}` }}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 <Hdr label={t('data.col_sku')} col="sku" />
 <Hdr label={t('data.col_n')} col="n" />
 <Hdr label={t('data.col_mean')} col="mean" />
 <Hdr label={t('data.col_cv')} col="cv" />
 <Hdr label={t('data.col_seasonality')} col="seasonality_class" />
 <Hdr label={t('data.col_period')} col="dominant_period" />
 <Hdr label={t('data.col_trend')} col="trend_direction" />
 <Hdr label={t('data.col_stationarity')} col="stationarity" />
 <Hdr label={t('data.col_demand_type')} col="croston_class" />
 </tr>
 </thead>
 <tbody>
 {rows.map((row, i) => {
 const bg = i % 2 === 0 ? C.card : C.surface
 const trendColor = row.trend_direction === 'increasing' ? C.green
 : row.trend_direction === 'decreasing' ? C.red : C.muted
 const seasColor = row.seasonality_class === 'strong' ? C.green
 : row.seasonality_class === 'moderate' ? C.amber : C.muted
 const statColor = row.stationarity === 'stationary' ? C.green
 : row.stationarity ? C.amber : C.muted
 return (
 <tr key={row.sku ?? i}
 onClick={() => row.sku && !row.error && onSelect(row.sku)}
 onMouseEnter={e => (e.currentTarget.style.background = C.greenDim)}
 onMouseLeave={e => (e.currentTarget.style.background = bg)}
 style={{ background: bg, cursor: row.error ? 'default' : 'pointer', transition: 'background 0.1s' }}>
 <td style={{ ...TD, color: C.green, fontWeight: 600 }}>
 {row.sku ?? '__all__'}
 {row.error && <span style={{ color: C.red, fontSize: 10, marginLeft: 6 }}>⚠ {t('data.error_short')}</span>}
 </td>
 <td style={TD}>{row.n?.toLocaleString() ?? '—'}</td>
 <td style={TD}>{row.mean != null ? row.mean.toFixed(1) : '—'}</td>
 <td style={{ ...TD, color: row.cv != null && row.cv > 1 ? C.amber : C.text }}>
 {row.cv != null ? row.cv.toFixed(2) : '—'}
 </td>
 <td style={{ ...TD, color: seasColor }}>
 {row.seasonality_class ?? '—'}
 </td>
 <td style={TD}>{row.dominant_period ?? '—'}</td>
 <td style={{ ...TD, color: trendColor }}>
 {row.trend_direction === 'increasing' ? '↑ ' : row.trend_direction === 'decreasing' ? '↓ ' : row.trend_direction ? '→ ' : ''}
 {row.trend_direction ?? '—'}
 </td>
 <td style={{ ...TD, color: statColor }}>{row.stationarity ?? '—'}</td>
 <td style={{ ...TD, color: C.muted, fontSize: 11 }}>{row.croston_class ?? '—'}</td>
 </tr>
 )
 })}
 </tbody>
 </table>
 </div>
 )
}

// ── Analysis: SKU Detail View ─────────────────────────────────────────────────
function SkuDetailView({ sku, detail, loading, onBack }: {
 sku: string; detail: SkuDetailResult | null; loading: boolean; onBack: () => void
}) {
 const { t } = useLanguage()
 const [showOutliers, setShowOutliers] = useState(true)
 if (loading) return (
 <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '80px 0', justifyContent: 'center' }}>
 <Spinner size={22} /> <span style={{ color: C.muted }}>{t('data.running_deep_analysis')} {sku}…</span>
 </div>
 )
 if (!detail) return null

 const r = detail.report
 const dist = (r.distribution as Record<string, unknown>) ?? {}
 const seas = (r.seasonality as Record<string, unknown>) ?? {}
 const trend = (r.trend as Record<string, unknown>) ?? {}
 const stat = (r.stationarity as Record<string, unknown>) ?? {}
 const acf = (r.autocorrelation as Record<string, unknown>) ?? {}
 const dec = (r.decomposition as Record<string, unknown>) ?? {}
 const dr = (r.date_range as Record<string, unknown>) ?? {}
 const mk = (trend.mann_kendall as Record<string, unknown>) ?? {}
 const lin = (trend.linear as Record<string, unknown>) ?? {}
 const croston = (dist.croston as Record<string, unknown>) ?? {}
 const norm = (dist.normality as Record<string, unknown>) ?? {}
 const adf = (stat.adf as Record<string, unknown>) ?? {}
 const kpss = (stat.kpss as Record<string, unknown>) ?? {}
 const lb = (acf.ljung_box as Record<string, unknown>) ?? {}

 const n = (v: unknown, d = 2) => v != null ? Number(v).toFixed(d) : '—'
 const pf = (v: unknown) => v == null ? '—' : Number(v) < 0.001 ? '<0.001' : Number(v).toFixed(3)
 const pct = (v: unknown) => v != null ? `${Number(v).toFixed(1)}%` : '—'

 const panels = [
 {
 title: t('data.panel_distribution'), color: C.blue,
 rows: [
 [t('data.stat_mean'), n(dist.mean, 1)],
 [t('data.stat_median'), n(dist.median, 1)],
 [t('data.stat_std'), n(dist.std, 1)],
 [t('data.stat_cv'), n(dist.cv)],
 [t('data.stat_skewness'), n(dist.skewness)],
 [t('data.stat_zero_pct'), pct(dist.zero_pct)],
 [t('data.stat_outliers'), pct(dist.outlier_pct)],
 [t('data.stat_best_dist'), String(dist.best_distribution ?? '—')],
 ],
 },
 {
 title: t('data.panel_seasonality'), color: C.green,
 rows: [
 [t('data.stat_class'), String(seas.classification ?? '—')],
 [t('data.stat_period'), String(seas.dominant_period ?? '—')],
 [t('data.stat_strength'), n(seas.seasonal_strength)],
 [t('data.stat_stl_seasonal'), n(dec.seasonal_strength)],
 [t('data.stat_stl_trend'), n(dec.trend_strength)],
 ],
 },
 {
 title: t('data.panel_trend'), color: C.amber,
 rows: [
 [t('data.stat_direction'), String(mk.direction ?? '—')],
 [t('data.stat_mk_pvalue'), pf(mk.pvalue)],
 [t('data.stat_sens_slope'), n(trend.sens_slope, 4)],
 [t('data.stat_linear_r2'), n(lin.r2)],
 [t('data.stat_change_points'), String((trend.change_points as unknown[] | undefined)?.length ?? '—')],
 ],
 },
 {
 title: t('data.panel_stationarity'), color: C.muted,
 rows: [
 [t('data.stat_verdict'), String(stat.verdict ?? '—')],
 [t('data.stat_diff_order'), String(stat.diff_order ?? '—')],
 [t('data.stat_adf_pvalue'), pf(adf.pvalue)],
 [t('data.stat_kpss_pvalue'), pf(kpss.pvalue)],
 ],
 },
 ]

 const decTrend = Array.isArray(dec.trend) ? (dec.trend as (number | null)[]).filter(v => v != null) as number[] : []
 const decSeasonal = Array.isArray(dec.seasonal) ? (dec.seasonal as (number | null)[]).filter(v => v != null) as number[] : []
 const decResidual = Array.isArray(dec.residual) ? (dec.residual as (number | null)[]).filter(v => v != null) as number[] : []

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
 {/* Header */}
 <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
 <button onClick={onBack}
 style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px',
 background: 'transparent', border: `1px solid ${C.border2}`, borderRadius: 8,
 color: C.muted, cursor: 'pointer', fontSize: 12, flexShrink: 0 }}>
 <ArrowLeft size={13} /> {t('common.back')}
 </button>
 <div>
 <h3 style={{ margin: 0, color: C.text, fontSize: 15, fontWeight: 700 }}>{sku === '__all__' ? t('data.full_dataset') : sku}</h3>
 <span style={{ color: C.muted, fontSize: 12 }}>
 {dr.start ? `${dr.start} → ${dr.end}` : ''}
 {dr.n_days != null ? ` · ${dr.n_days} ${Number(dr.n_days) === 1 ? t('data.days_singular') : t('data.days_plural')}` : ''}
 {dr.freq_detected ? ` · ${dr.freq_detected}` : ''}
 {r.n_observations != null ? ` · ${r.n_observations} ${t('data.observations_suffix')}` : ''}
 </span>
 </div>
 </div>

 {/* Series chart */}
 {detail.series.length > 1 && (
 <div style={{ background: C.surface, borderRadius: 10, padding: '14px 14px 10px',
 border: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>
 {t('data.section_time_series')}
 </div>
 {(detail.outliers?.length ?? 0) > 0 && (
 <button
 onClick={() => setShowOutliers(v => !v)}
 style={{
 display: 'flex', alignItems: 'center', gap: 5,
 padding: '3px 10px', borderRadius: 6,
 background: showOutliers ? `${C.red}15` : 'transparent',
 border: `1px solid ${showOutliers ? C.red + '40' : C.border2}`,
 color: showOutliers ? C.red : C.muted,
 fontSize: 11, cursor: 'pointer', transition: 'all 0.15s',
 }}
 >
 <AlertTriangle size={10} />
 {showOutliers ? t('data.btn_hide') : t('data.btn_show')} {t('data.outliers_word')} ({detail.outliers!.length})
 </button>
 )}
 </div>
 <LineChart data={detail.series} outliers={detail.outliers} showOutliers={showOutliers} />
 </div>
 )}

 {/* Outlier panel */}
 {(detail.outliers?.length ?? 0) > 0 && (
 <div style={{
 background: C.surface, borderRadius: 10, padding: '14px 16px',
 border: `1px solid ${C.red}30`,
 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
 <AlertTriangle size={13} color={C.red} />
 <span style={{ color: C.red, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
 {detail.outliers!.length} {detail.outliers!.length !== 1 ? t('data.outliers_detected_plural') : t('data.outliers_detected_singular')}
 </span>
 <span style={{ color: C.muted, fontSize: 11 }}>{t('data.iqr_method')}</span>
 </div>
 <div style={{ overflowX: 'auto' }}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 {[t('data.col_date'), t('data.col_value'), t('data.col_zscore'), t('data.col_direction'), t('data.col_reason')].map(h => (
 <th key={h} style={{
 padding: '6px 12px', textAlign: 'left', background: 'var(--surface-2)',
 color: C.muted, fontWeight: 600, fontSize: 11,
 borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap',
 }}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {detail.outliers!.map((o, i) => (
 <tr key={o.date} style={{ background: i % 2 === 0 ? C.card : C.surface }}>
 <td style={{ padding: '6px 12px', color: C.muted, borderBottom: `1px solid ${C.border}`, fontFamily: 'monospace', fontSize: 11 }}>
 {o.date}
 </td>
 <td style={{ padding: '6px 12px', fontWeight: 700, borderBottom: `1px solid ${C.border}`, color: o.value > o.upper_bound ? C.red : C.amber }}>
 {o.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, color: Math.abs(o.z_score) > 3 ? C.red : C.amber }}>
 {o.z_score > 0 ? '+' : ''}{o.z_score}σ
 </td>
 <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.border}`, color: o.value > o.upper_bound ? C.red : C.amber, whiteSpace: 'nowrap' }}>
 {o.value > o.upper_bound ? t('data.direction_high') : t('data.direction_low')}
 </td>
 <td style={{ padding: '6px 12px', color: C.muted, fontSize: 11, borderBottom: `1px solid ${C.border}` }}>
 {o.reason}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Stat panels */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
 {panels.map(p => (
 <div key={p.title} style={{ background: C.surface, borderRadius: 10,
 border: `1px solid ${C.border}`, padding: '12px 14px' }}>
 <div style={{ color: p.color, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', marginBottom: 10, letterSpacing: '0.05em' }}>
 {p.title}
 </div>
 {p.rows.map(([label, val]) => (
 <div key={label as string} style={{ display: 'flex', justifyContent: 'space-between',
 alignItems: 'center', marginBottom: 5, gap: 8 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{label}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600,
 maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' }}>
 {String(val)}
 </span>
 </div>
 ))}
 </div>
 ))}
 </div>

 {/* STL Decomposition sparklines */}
 {decTrend.length > 2 && (
 <div style={{ background: C.surface, borderRadius: 10, padding: '14px 16px',
 border: `1px solid ${C.border}` }}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', marginBottom: 14, letterSpacing: '0.05em' }}>
 {t('data.section_stl')}
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 20 }}>
 {[
 { label: t('data.stl_trend'), values: decTrend, color: C.green },
 { label: t('data.stl_seasonal'), values: decSeasonal, color: C.blue },
 { label: t('data.stl_residual'), values: decResidual, color: C.amber },
 ].map(({ label, values, color }) => (
 <div key={label}>
 <div style={{ color: C.muted, fontSize: 11, marginBottom: 6 }}>{label}</div>
 {values.length > 2
 ? <Sparkline values={values} color={color} w={200} h={44} />
 : <span style={{ color: C.muted, fontSize: 11 }}>—</span>}
 </div>
 ))}
 </div>
 </div>
 )}

 {/* Autocorrelation + Demand classification */}
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
 <div style={{ background: C.surface, borderRadius: 10, padding: '12px 14px',
 border: `1px solid ${C.border}` }}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', marginBottom: 10 }}>{t('data.section_autocorrelation')}</div>
 {[
 [t('data.ac_suggested_ar'), String(acf.suggested_ar_order ?? '—')],
 [t('data.ac_suggested_ma'), String(acf.suggested_ma_order ?? '—')],
 [t('data.ac_white_noise'), lb.is_white_noise != null ? (lb.is_white_noise ? t('common.yes') : t('common.no')) : '—'],
 [t('data.ac_ljung_box_p'), pf(lb.pvalue)],
 ].map(([l, v]) => (
 <div key={l} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{l}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600 }}>{v}</span>
 </div>
 ))}
 </div>

 <div style={{ background: C.surface, borderRadius: 10, padding: '12px 14px',
 border: `1px solid ${C.border}` }}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', marginBottom: 10 }}>{t('data.section_demand_class')}</div>
 {[
 [t('data.dc_croston_class'), String(croston.classification ?? '—')],
 [t('data.dc_adi'), n(croston.adi)],
 [t('data.dc_cv2'), n(croston.cv2)],
 [t('data.dc_best_fit_dist'), String(dist.best_distribution ?? '—')],
 [t('data.dc_is_normal'), norm.is_normal != null ? (norm.is_normal ? t('common.yes') : t('common.no')) : '—'],
 ].map(([l, v]) => (
 <div key={l} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{l}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600 }}>{String(v)}</span>
 </div>
 ))}
 </div>
 </div>
 </div>
 )
}

// ── Analysis Tab ─────────────────────────────────────────────────────────────
function AnalysisTab({ source, columns, activeSheet }: {
 source: DataSource; columns: string[]; activeSheet?: string
}) {
 const { t } = useLanguage()
 const guessed = useMemo(() => guessColumns(columns), [columns.join(',')]) // eslint-disable-line

 const [dateCol, setDateCol] = useState('')
 const [targetCol, setTargetCol] = useState('')
 const [skuCol, setSkuCol] = useState('')
 const [dateFrom, setDateFrom] = useState('')
 const [dateTo, setDateTo] = useState('')
 const [result, setResult] = useState<AnalysisResult | null>(null)
 const [loading, setLoading] = useState(false)
 const [err, setErr] = useState<string | null>(null)
 const [selSku, setSelSku] = useState<string | null>(null)
 const [skuDetail, setSkuDetail] = useState<SkuDetailResult | null>(null)
 const [skuLoading,setSkuLoading]= useState(false)
 const [sortCol, setSortCol] = useState('sku')
 const [sortDir, setSortDir] = useState<'asc'|'desc'>('asc')

 // Populate column selectors once column list arrives
 useEffect(() => {
 if (guessed.dateCol && !dateCol) setDateCol(guessed.dateCol)
 if (guessed.targetCol && !targetCol) setTargetCol(guessed.targetCol)
 if (guessed.skuCol && !skuCol) setSkuCol(guessed.skuCol)
 }, [guessed.dateCol, guessed.targetCol, guessed.skuCol]) // eslint-disable-line

 const runAnalysis = async () => {
 if (!dateCol || !targetCol) { setErr(t('data.err_select_columns')); return }
 setLoading(true); setErr(null); setResult(null); setSelSku(null); setSkuDetail(null)
 try {
 const r = await analyzeDataSource(source.id, {
 date_col: dateCol, target_col: targetCol,
 sku_col: skuCol || undefined, sheet: activeSheet,
 date_from: dateFrom || undefined, date_to: dateTo || undefined,
 })
 setResult(r)
 } catch (e: any) { setErr(e.message) }
 finally { setLoading(false) }
 }

 const loadSku = async (sku: string) => {
 setSelSku(sku); setSkuDetail(null); setSkuLoading(true)
 try {
 const r = await analyzeSkuDetail(source.id, sku, {
 date_col: dateCol, target_col: targetCol,
 sku_col: skuCol || undefined, sheet: activeSheet,
 date_from: dateFrom || undefined, date_to: dateTo || undefined,
 })
 setSkuDetail(r)
 } catch (e: any) { setErr(e.message); setSelSku(null) }
 finally { setSkuLoading(false) }
 }

 const toggleSort = (col: string) => {
 if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
 else { setSortCol(col); setSortDir('asc') }
 }

 const sortedRows = useMemo(() => {
 if (!result?.summary) return []
 return [...result.summary].sort((a, b) => {
 const av = a[sortCol as keyof AnalysisSummaryRow], bv = b[sortCol as keyof AnalysisSummaryRow]
 if (av == null && bv == null) return 0
 if (av == null) return 1
 if (bv == null) return -1
 const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true })
 return sortDir === 'asc' ? cmp : -cmp
 })
 }, [result?.summary, sortCol, sortDir])

 // Show detail view when a SKU is selected
 if (selSku) {
 return (
 <SkuDetailView
 sku={selSku}
 detail={skuDetail}
 loading={skuLoading}
 onBack={() => { setSelSku(null); setSkuDetail(null) }}
 />
 )
 }

 const selStyle: React.CSSProperties = {
 width: '100%', background: C.surface, border: `1px solid ${C.border2}`,
 borderRadius: 8, padding: '8px 10px', color: C.text, fontSize: 13,
 outline: 'none', boxSizing: 'border-box',
 }
 const lblStyle: React.CSSProperties = {
 color: C.muted, fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
 marginBottom: 4, display: 'block', letterSpacing: '0.05em',
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
 {/* Column mapping */}
 <div style={{ background: C.surface, borderRadius: 10, padding: '14px 16px',
 border: `1px solid ${C.border}` }}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', marginBottom: 14, letterSpacing: '0.05em' }}>
 {t('data.section_column_mapping')}
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
 <div>
 <label style={lblStyle}>{t('data.field_date_column')} *</label>
 <select style={selStyle} value={dateCol} onChange={e => setDateCol(e.target.value)}>
 <option value="">{t('data.select_placeholder')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </select>
 </div>
 <div>
 <label style={lblStyle}>{t('data.field_target_column')} *</label>
 <select style={selStyle} value={targetCol} onChange={e => setTargetCol(e.target.value)}>
 <option value="">{t('data.select_placeholder')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </select>
 </div>
 <div>
 <label style={lblStyle}>{t('data.field_group_sku_column')}</label>
 <select style={selStyle} value={skuCol} onChange={e => setSkuCol(e.target.value)}>
 <option value="">{t('data.option_none_single_series')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </select>
 </div>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'end' }}>
 <div>
 <label style={lblStyle}>{t('data.field_date_from')}</label>
 <input type="date" style={selStyle} value={dateFrom}
 onChange={e => setDateFrom(e.target.value)} />
 </div>
 <div>
 <label style={lblStyle}>{t('data.field_date_to')}</label>
 <input type="date" style={selStyle} value={dateTo}
 onChange={e => setDateTo(e.target.value)} />
 </div>
 <button onClick={runAnalysis} disabled={loading || !dateCol || !targetCol}
 style={{ padding: '9px 18px', borderRadius: 8, background: C.green, border: 'none',
 color: '#fff', fontWeight: 700, fontSize: 13,
 cursor: loading || !dateCol || !targetCol ? 'not-allowed' : 'pointer',
 opacity: loading || !dateCol || !targetCol ? 0.55 : 1,
 display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
 {loading ? <Spinner size={14} /> : <BarChart2 size={14} />}
 {loading ? t('data.btn_analyzing') : t('data.btn_analyze')}
 </button>
 </div>
 {columns.length === 0 && (
 <p style={{ margin: '10px 0 0', color: C.amber, fontSize: 12 }}>
 {t('data.warn_preview_not_loaded')}
 </p>
 )}
 </div>

 {err && (
 <div style={{ background: C.redDim, border: `1px solid ${C.red}30`,
 borderRadius: 8, padding: '10px 14px', color: C.red, fontSize: 13 }}>
 {err}
 </div>
 )}

 {loading && (
 <div style={{ display: 'flex', gap: 12, alignItems: 'center',
 padding: '40px 0', justifyContent: 'center' }}>
 <Spinner size={22} />
 <span style={{ color: C.muted }}>{t('data.running_statistical_analysis')}</span>
 </div>
 )}

 {result && !loading && (
 <>
 {/* Summary info bar */}
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 padding: '10px 14px', background: C.surface, borderRadius: 8, border: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 20 }}>
 {[
 [t('data.summary_skus'), String(result.summary.length)],
 [t('data.summary_date_col'), result.date_col],
 [t('data.summary_target_col'), result.target_col],
 ...(result.sku_col ? [[t('data.summary_group_col'), result.sku_col]] : []),
 ].map(([label, val]) => (
 <div key={label}>
 <span style={{ color: C.muted, fontSize: 10, fontWeight: 600, textTransform: 'uppercase' }}>{label} </span>
 <span style={{ color: C.green, fontSize: 12, fontWeight: 600 }}>{val}</span>
 </div>
 ))}
 </div>
 <span style={{ color: C.muted, fontSize: 11 }}>{t('data.click_row_for_detail')}</span>
 </div>

 <AnalysisSummaryTable
 rows={sortedRows}
 sortCol={sortCol}
 sortDir={sortDir}
 onSort={toggleSort}
 onSelect={loadSku}
 />
 </>
 )}
 </div>
 )
}

// ── Dataset editor (edit as spreadsheet, save as new) ─────────────────────────
function DatasetEditorPanel({ source, onCreated }: {
 source: DataSource; onCreated: (s: DataSource) => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const [loading, setLoading] = useState(true)
 const [loadErr, setLoadErr] = useState<string | null>(null)
 const [columns, setColumns] = useState<string[]>([])
 const [rows, setRows] = useState<Record<string, unknown>[]>([])
 const [name, setName] = useState(`${source.name} (editado)`)
 const [saving, setSaving] = useState(false)

 useEffect(() => {
  let alive = true
  setLoading(true); setLoadErr(null)
  getEditableTable(source.id)
   .then((tbl: EditableTable) => { if (alive) { setColumns(tbl.columns); setRows(tbl.rows) } })
   .catch((e: any) => { if (alive) setLoadErr(e.message || t('data.editor_too_large')) })
   .finally(() => { if (alive) setLoading(false) })
  return () => { alive = false }
 }, [source.id]) // eslint-disable-line

 const setCell = (ri: number, col: string, val: string) =>
  setRows(rs => rs.map((r, i) => i === ri ? { ...r, [col]: val } : r))
 const addRow = () =>
  setRows(rs => [...rs, Object.fromEntries(columns.map(c => [c, '']))])
 const deleteRow = (ri: number) =>
  setRows(rs => rs.filter((_, i) => i !== ri))
 const dropColumn = (col: string) => {
  setColumns(cs => cs.filter(c => c !== col))
  setRows(rs => rs.map(r => { const { [col]: _drop, ...rest } = r; return rest }))
 }
 const renameColumn = (col: string) => {
  const next = window.prompt(t('data.editor_rename_column'), col)
  if (!next || next === col || columns.includes(next)) return
  setColumns(cs => cs.map(c => c === col ? next : c))
  setRows(rs => rs.map(r => { const { [col]: v, ...rest } = r; return { ...rest, [next]: v } }))
 }
 const addColumn = () => {
  const nm = window.prompt(t('data.editor_new_column'), '')
  if (!nm || columns.includes(nm)) return
  setColumns(cs => [...cs, nm])
  setRows(rs => rs.map(r => ({ ...r, [nm]: '' })))
 }

 const save = async () => {
  setSaving(true)
  try {
   const created = await saveDatasetAsNew(source.id, { name: name.trim() || undefined, columns, rows })
   addToast(t('data.editor_saved'), created.name, 'success')
   onCreated(created)
  } catch (e: any) {
   addToast(t('data.editor_save_failed'), e.message, 'error')
  } finally { setSaving(false) }
 }

 if (loading) return (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '40px 0', justifyContent: 'center' }}>
   <Spinner size={20} /> <span style={{ color: C.muted }}>{t('data.editor_loading')}</span>
  </div>
 )
 if (loadErr) return (
  <div style={{ background: C.redDim, border: `1px solid ${C.red}30`, borderRadius: 8,
   padding: '14px 16px', color: C.red, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
   <AlertTriangle size={14} /> {loadErr}
  </div>
 )

 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
   <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
    <input value={name} onChange={e => setName(e.target.value)}
     placeholder={t('data.editor_new_name')}
     style={{ flex: 1, minWidth: 200, background: C.surface, border: `1px solid ${C.border2}`,
      borderRadius: 8, padding: '8px 12px', color: C.text, fontSize: 13, outline: 'none' }} />
    <button onClick={addRow}
     style={{ padding: '8px 14px', borderRadius: 8, background: 'transparent',
      border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
     <Plus size={12} /> {t('data.editor_add_row')}
    </button>
    <button onClick={addColumn}
     style={{ padding: '8px 14px', borderRadius: 8, background: 'transparent',
      border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
     <Plus size={12} /> {t('data.editor_new_column')}
    </button>
    <button onClick={save} disabled={saving || !columns.length}
     style={{ padding: '8px 18px', borderRadius: 8, background: C.green, border: 'none',
      color: '#fff', fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer',
      opacity: saving || !columns.length ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
     {saving ? <Spinner size={12} /> : <Save size={12} />} {saving ? t('data.editor_saving') : t('data.editor_save_as_new')}
    </button>
   </div>
   <div style={{ color: C.muted, fontSize: 12 }}>{rows.length} {t('data.editor_rows_count')}</div>
   <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 420, borderRadius: 8, border: `1px solid ${C.border}` }}>
    <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
     <thead>
      <tr>
       <th style={{ padding: '6px 8px', background: 'var(--surface-2)', borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0 }} />
       {columns.map(c => (
        <th key={c} style={{ padding: '6px 10px', textAlign: 'left', whiteSpace: 'nowrap',
         background: 'var(--surface-2)', color: C.muted, fontWeight: 600, fontSize: 11,
         borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0 }}>
         <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {c}
          <button onClick={() => renameColumn(c)} title={t('data.editor_rename_column')}
           style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer', padding: 0 }}>
           <Edit2 size={11} />
          </button>
          <button onClick={() => dropColumn(c)} title={t('data.editor_drop_column')}
           style={{ background: 'transparent', border: 'none', color: C.red, cursor: 'pointer', padding: 0 }}>
           <X size={11} />
          </button>
         </span>
        </th>
       ))}
      </tr>
     </thead>
     <tbody>
      {rows.map((row, ri) => (
       <tr key={ri} style={{ background: ri % 2 === 0 ? C.card : C.surface }}>
        <td style={{ padding: '2px 6px', borderBottom: `1px solid ${C.border}` }}>
         <button onClick={() => deleteRow(ri)} title={t('data.editor_delete_row')}
          style={{ background: 'transparent', border: 'none', color: C.red, cursor: 'pointer', padding: 2 }}>
          <Trash2 size={12} />
         </button>
        </td>
        {columns.map(c => (
         <td key={c} style={{ padding: 0, borderBottom: `1px solid ${C.border}` }}>
          <input value={row[c] == null ? '' : String(row[c])}
           onChange={e => setCell(ri, c, e.target.value)}
           style={{ width: '100%', minWidth: 90, background: 'transparent', border: 'none',
            padding: '6px 10px', color: C.text, fontSize: 12, outline: 'none', boxSizing: 'border-box' }} />
         </td>
        ))}
       </tr>
      ))}
     </tbody>
    </table>
   </div>
  </div>
 )
}

// ── Right panel: detail for a selected source ─────────────────────────────────
function SourceDetail({ source, onUpdated, onDeleted, onBack }:
 { source: DataSource; onUpdated: (s: DataSource) => void; onDeleted: () => void; onBack?: () => void }
) {
 const { t } = useLanguage()
 const confirm = useConfirm()
 const { addToast } = useToast()
 const [tab, setTab] = useState<'preview' | 'analysis' | 'edit' | 'sql-editor' | 'connection'>('preview')
 const [preview, setPreview] = useState<DataPreview | null>(null)
 const [loadingPreview, setLoadingPreview] = useState(false)
 const [previewErr, setPreviewErr] = useState<string | null>(null)
 const [activeSheet, setActiveSheet] = useState<string | undefined>()
 const [testing, setTesting] = useState(false)
 const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null)
 const [editName, setEditName] = useState(false)
 const [newName, setNewName] = useState(source.name)
 const [savingName, setSavingName] = useState(false)
 const [deletingId, setDeletingId] = useState(false)
 const [deleteErr, setDeleteErr] = useState<string | null>(null)
 const [editSql, setEditSql] = useState(false)
 const replaceRef = useRef<HTMLInputElement>(null)
 const [replacing, setReplacing] = useState(false)
 const [replaceErr, setReplaceErr] = useState<string | null>(null)

 const isSql = source.source_type === 'sql'

 useEffect(() => {
 setTab(isSql ? 'sql-editor' : 'preview')
 setPreview(null)
 setTestResult(null)
 setEditSql(false)
 setNewName(source.name)
 }, [source.id])

 const loadPreview = useCallback(async (sheet?: string): Promise<boolean> => {
 setLoadingPreview(true); setPreviewErr(null)
 try {
 const p = await getDataSourcePreview(source.id, 100, sheet)
 setPreview(p)
 if (p.active_sheet) setActiveSheet(p.active_sheet)
 return true
 } catch (e: any) { setPreviewErr(e.message); return false }
 finally { setLoadingPreview(false) }
 }, [source.id])

 useEffect(() => {
 if (!isSql && (tab === 'preview' || tab === 'analysis')) loadPreview()
 }, [tab, isSql, loadPreview])

 const testConn = async () => {
 setTesting(true); setTestResult(null)
 try {
 const r = await testSqlConnection(source.id)
 setTestResult(r)
 if (r.ok) onUpdated({ ...source, connection_status: 'connected' })
 } catch (e: any) { setTestResult({ ok: false, error: e.message }) }
 finally { setTesting(false) }
 }

 const saveName = async () => {
 if (!newName.trim()) return
 setSavingName(true)
 try { onUpdated(await renameDataSource(source.id, newName.trim())) }
 catch {}
 finally { setSavingName(false); setEditName(false) }
 }

 const doDelete = async () => {
 if (!(await confirm({
 title: `${t('data.confirm_delete_prefix')} "${source.name}"?`,
 message: t('data.confirm_delete_suffix'),
 danger: true,
 }))) return
 setDeletingId(true); setDeleteErr(null)
 try { await deleteDataSource(source.id); onDeleted() }
 catch (e) { setDeleteErr(e instanceof Error ? e.message : t('data.delete_failed')) }
 finally { setDeletingId(false) }
 }

 const replaceFile = async (file: File) => {
 setReplacing(true); setReplaceErr(null)
 try {
 const fd = new FormData(); fd.append('file', file)
 await replaceFileSource(source.id, fd)
 onUpdated(await getDataSource(source.id))
 setPreview(null)
 loadPreview()
 } catch (e: any) { setReplaceErr(e.message) }
 finally { setReplacing(false) }
 }

 const tabs = isSql
 ? [{ id: 'sql-editor', label: t('data.tab_query_editor') }, { id: 'connection', label: t('data.tab_connection') }]
 : [{ id: 'preview', label: t('data.tab_data_preview') }, { id: 'edit', label: t('data.tab_edit') }, { id: 'analysis', label: t('data.tab_analysis') }, { id: 'connection', label: t('data.tab_replace_file') }]

 return (
 <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
 {/* Header */}
 <div style={{ padding: '20px 24px 0', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16 }}>
 {onBack && (
 <button onClick={onBack} aria-label={t('common.back')} style={{ background: 'transparent', border: 'none',
 color: C.muted, cursor: 'pointer', padding: 4, marginTop: 2 }}>
 <ArrowLeft size={16} aria-hidden="true" />
 </button>
 )}
 <SourceIcon type={source.source_type} size={24} />
 <div style={{ flex: 1, minWidth: 0 }}>
 {editName ? (
 <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
 <input value={newName} onChange={e => setNewName(e.target.value)}
 onKeyDown={e => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') setEditName(false) }}
 autoFocus
 style={{ background: C.surface, border: `1px solid ${C.green}`,
 borderRadius: 6, padding: '4px 10px', color: C.text, fontSize: 16, fontWeight: 700, outline: 'none' }} />
 <button onClick={saveName} disabled={savingName}
 style={{ background: C.green, border: 'none', borderRadius: 6,
 padding: '4px 12px', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
 {savingName ? '…' : t('common.save')}
 </button>
 <button onClick={() => setEditName(false)}
 style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer' }}>
 <X size={14} />
 </button>
 </div>
 ) : (
 <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
 <h2 style={{ margin: 0, color: C.text, fontSize: 18, fontWeight: 700,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {source.name}
 </h2>
 <button onClick={() => setEditName(true)}
 style={{ background: 'transparent', border: 'none', color: C.muted,
 cursor: 'pointer', padding: 2, opacity: 0.6 }}>
 <Edit2 size={13} />
 </button>
 </div>
 )}
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
 <StatusBadge status={source.connection_status} />
 {source.original_filename && (
 <span style={{ color: C.muted, fontSize: 12 }}>{source.original_filename}</span>
 )}
 {source.sql_config && (
 <span style={{ color: C.muted, fontSize: 12 }}>
 {source.sql_config.host}:{source.sql_config.port}/{source.sql_config.database}
 </span>
 )}
 </div>
 </div>
 <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
 {isSql && (
 <button onClick={testConn} disabled={testing}
 style={{ padding: '7px 14px', borderRadius: 8, background: C.blueDim,
 border: `1px solid ${C.blue}40`, color: C.blue, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600 }}>
 {testing ? <Spinner size={12} /> : <Link2 size={12} />}
 {t('data.btn_test_connection')}
 </button>
 )}
 <button onClick={doDelete} disabled={deletingId}
 aria-label={t('common.delete')} title={t('common.delete')}
 style={{ padding: '7px 12px', borderRadius: 8, background: C.redDim,
 border: `1px solid ${C.red}30`, color: C.red, cursor: 'pointer' }}>
 <Trash2 size={14} aria-hidden="true" />
 </button>
 </div>
 </div>

 {deleteErr && (
 <div style={{ margin: '0 0 8px', padding: '8px 12px', borderRadius: 7,
 background: C.redDim, border: `1px solid ${C.red}40`, color: C.red, fontSize: 12,
 display: 'flex', alignItems: 'center', gap: 6 }}>
 <AlertTriangle size={12} /> {deleteErr}
 </div>
 )}

 {/* Stats strip */}
 <div style={{ display: 'flex', gap: 24, paddingBottom: 12 }}>
 {[
 { label: t('data.stat_size'), value: fmt(source.size_bytes) },
 { label: t('data.stat_rows'), value: source.row_count?.toLocaleString() ?? '—' },
 { label: t('data.stat_columns'), value: source.column_count?.toLocaleString() ?? '—' },
 { label: t('data.stat_type'), value: source.file_type || source.sql_config?.engine || '—' },
 ].map(s => (
 <div key={s.label}>
 <div style={{ color: C.muted, fontSize: 10, fontWeight: 600, textTransform: 'uppercase' }}>{s.label}</div>
 <div style={{ color: C.text, fontSize: 14, fontWeight: 600 }}>{s.value}</div>
 </div>
 ))}
 </div>

 {testResult && (
 <div style={{
 marginBottom: 12, padding: '8px 14px', borderRadius: 8,
 background: testResult.ok ? C.greenDim : C.redDim,
 border: `1px solid ${testResult.ok ? C.green : C.red}30`,
 color: testResult.ok ? C.green : C.red, fontSize: 13,
 display: 'flex', alignItems: 'center', gap: 6,
 }}>
 {testResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
 {testResult.ok ? t('data.connection_successful') : `${t('data.connection_failed')}: ${testResult.error}`}
 </div>
 )}

 {/* Tabs */}
 <div style={{ display: 'flex', gap: 2 }}>
 {tabs.map(t => (
 <button key={t.id} onClick={() => setTab(t.id as any)}
 style={{
 padding: '8px 16px', border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600,
 background: 'transparent', borderBottom: `2px solid ${tab === t.id ? C.green : 'transparent'}`,
 color: tab === t.id ? C.green : C.muted, transition: 'all 0.15s',
 display: 'flex', alignItems: 'center', gap: 6,
 }}>
 {t.id === 'analysis' && <BarChart2 size={12} />}
 {t.label}
 </button>
 ))}
 </div>
 </div>

 {/* Tab content */}
 <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>

 {/* File preview tab */}
 {tab === 'preview' && !isSql && (
 <div>
 {preview?.sheets && preview.sheets.length > 1 && (
 <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
 {preview.sheets.map(s => (
 <button key={s} onClick={async () => {
 const prev = activeSheet
 setActiveSheet(s)
 const ok = await loadPreview(s)
 if (!ok) setActiveSheet(prev)
 }}
 style={{ padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 600,
 cursor: 'pointer', border: `1px solid ${activeSheet === s ? C.green : C.border2}`,
 background: activeSheet === s ? C.greenDim : 'transparent',
 color: activeSheet === s ? C.green : C.muted }}>
 {s}
 </button>
 ))}
 </div>
 )}
 {loadingPreview ? (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '40px 0', justifyContent: 'center' }}>
 <Spinner size={20} /> <span style={{ color: C.muted }}>{t('data.loading_preview')}</span>
 </div>
 ) : previewErr ? (
 <div style={{ color: C.red, padding: 20 }}>{previewErr}</div>
 ) : preview ? (
 <>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
 <Eye size={14} color={C.muted} />
 <span style={{ color: C.muted, fontSize: 12 }}>
 {t('data.showing')} {preview.row_count} {preview.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}{preview.truncated ? ` ${t('data.first_100')}` : ''}
 {preview.active_sheet ? ` — ${t('data.sheet_label')}: ${preview.active_sheet}` : ''}
 </span>
 <button
 onClick={() => { if (!loadingPreview) loadPreview(activeSheet) }}
 disabled={loadingPreview}
 style={{ marginLeft: 'auto', background: 'transparent', border: 'none',
 color: C.muted, cursor: loadingPreview ? 'default' : 'pointer',
 display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
 opacity: loadingPreview ? 0.5 : 1 }}>
 {loadingPreview ? <Spinner size={12} /> : <RefreshCw size={12} />} {t('common.refresh')}
 </button>
 </div>
 <DataGrid columns={preview.columns} rows={preview.rows} />
 </>
 ) : null}
 </div>
 )}

 {/* Analysis tab */}
 {tab === 'analysis' && !isSql && (
 loadingPreview && !preview ? (
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '40px 0', justifyContent: 'center' }}>
 <Spinner size={20} /> <span style={{ color: C.muted }}>{t('data.loading_column_list')}</span>
 </div>
 ) : (
 <AnalysisTab
 source={source}
 columns={preview?.columns ?? []}
 activeSheet={activeSheet}
 />
 )
 )}

 {/* Edit tab */}
 {tab === 'edit' && !isSql && (
 <DatasetEditorPanel
 source={source}
 onCreated={(created) => { onUpdated(created) }}
 />
 )}

 {/* SQL editor tab */}
 {tab === 'sql-editor' && isSql && (
 source.connection_status !== 'connected' ? (
 <div style={{ textAlign: 'center', padding: '40px 0' }}>
 <AlertTriangle size={32} color={C.amber} style={{ marginBottom: 12 }} />
 <p style={{ color: C.amber, fontWeight: 600 }}>{t('data.connection_not_established')}</p>
 <p style={{ color: C.muted, fontSize: 13 }}>{t('data.test_connection_first')}</p>
 <button onClick={testConn} disabled={testing}
 style={{ marginTop: 12, padding: '9px 20px', borderRadius: 8, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer',
 display: 'inline-flex', alignItems: 'center', gap: 6 }}>
 {testing ? <Spinner size={14} /> : <Link2 size={14} />} {t('data.btn_test_connection')}
 </button>
 </div>
 ) : (
 <SqlEditorPanel source={source} onSaved={onUpdated} />
 )
 )}

 {/* Replace file tab */}
 {tab === 'connection' && !isSql && (
 <div>
 <p style={{ color: C.muted, fontSize: 13, marginBottom: 16 }}>
 {t('data.replace_file_hint')}
 </p>
 {replaceErr && (
 <div style={{ background: C.redDim, border: `1px solid ${C.red}30`,
 borderRadius: 8, padding: '10px 14px', color: C.red, fontSize: 13, marginBottom: 12 }}>
 {replaceErr}
 </div>
 )}
 {replacing ? (
 <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '20px 0' }}>
 <Spinner size={18} /> <span style={{ color: C.muted }}>{t('data.replacing_file')}</span>
 </div>
 ) : (
 <DropZone onFile={replaceFile} compact />
 )}
 </div>
 )}

 {/* Edit SQL config tab */}
 {tab === 'connection' && isSql && (
 <div>
 <p style={{ color: C.muted, fontSize: 13, marginBottom: 16 }}>
 {t('data.update_connection_hint')}
 </p>
 <SqlForm
 isEdit
 initial={{
 engine: source.sql_config?.engine,
 host: source.sql_config?.host,
 port: source.sql_config?.port?.toString(),
 database: source.sql_config?.database,
 username: source.sql_config?.username,
 }}
 onSave={async form => {
 try {
 const updated = await updateSqlConfig(source.id, {
 engine: form.engine, host: form.host, port: Number(form.port),
 database: form.database, username: form.username,
 password: form.password || undefined,
 })
 onUpdated(updated)
 setTestResult(null)
 } catch (e: any) { addToast(t('data.sql_config_save_failed'), e.message, 'error') }
 }}
 />
 </div>
 )}
 </div>
 </div>
 )
}

// ── New source panel (right panel when creating) ───────────────────────────────
function NewSourcePanel({ onCreated, onCancel }:
 { onCreated: (s: DataSource) => void; onCancel: () => void }
) {
 const { t } = useLanguage()
 const [mode, setMode] = useState<'file' | 'sql'>('file')
 const [busy, setBusy] = useState(false)
 const [err, setErr] = useState<string | null>(null)
 const [file, setFile] = useState<File | null>(null)
 const [name, setName] = useState('')
 const [desc, setDesc] = useState('')

 const uploadFile = async () => {
 if (!file) return
 setBusy(true); setErr(null)
 try {
 const fd = new FormData()
 fd.append('file', file)
 if (name.trim()) fd.append('name', name.trim())
 if (desc.trim()) fd.append('description', desc.trim())
 onCreated(await createFileSource(fd))
 } catch (e: any) { setErr(e.message) }
 finally { setBusy(false) }
 }

 const typeTabStyle = (active: boolean, color: string): React.CSSProperties => ({
 flex: 1, padding: '8px 12px', borderRadius: 7, cursor: 'pointer',
 background: active ? color + '18' : 'transparent',
 border: `1px solid ${active ? color + '50' : C.border}`,
 color: active ? color : C.muted,
 fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center',
 justifyContent: 'center', gap: 6, transition: 'all 0.15s',
 })

 if (mode === 'file') {
 return (
 <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <h3 style={{ margin: 0, color: C.text, fontSize: 16, fontWeight: 700, flex: 1 }}>{t('data.new_data_source')}</h3>
 <button onClick={onCancel} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer' }}>
 <X size={16} aria-hidden="true" />
 </button>
 </div>
 <div style={{ display: 'flex', gap: 8 }}>
 <button style={typeTabStyle(true, C.green)} onClick={() => setMode('file')}>
 <FileSpreadsheet size={13} /> {t('data.file_upload')}
 </button>
 <button style={typeTabStyle(false, C.blue)} onClick={() => { setMode('sql'); setErr(null) }}>
 <Database size={13} /> {t('data.sql_database')}
 </button>
 </div>
 {err && (
 <div style={{ background: C.redDim, border: `1px solid ${C.red}30`,
 borderRadius: 8, padding: '10px 14px', color: C.red, fontSize: 13 }}>
 {err}
 </div>
 )}
 {file ? (
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px',
 background: C.greenDim, border: `1px solid ${C.green}30`, borderRadius: 8, marginBottom: 14 }}>
 <FileSpreadsheet size={16} color={C.green} />
 <span style={{ color: C.green, fontSize: 13, fontWeight: 600 }}>{file.name}</span>
 <span style={{ color: C.muted, fontSize: 12 }}>({fmt(file.size)})</span>
 <button onClick={() => setFile(null)} style={{ marginLeft: 'auto', background: 'transparent',
 border: 'none', color: C.muted, cursor: 'pointer' }}>
 <X size={12} />
 </button>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
 <div>
 <label style={{ color: C.muted, fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 4 }}>
 {t('data.field_name_optional')}
 </label>
 <input value={name} onChange={e => setName(e.target.value)}
 placeholder={file.name.replace(/\.[^.]+$/, '')}
 style={{ width: '100%', background: C.surface, border: `1px solid ${C.border2}`,
 borderRadius: 8, padding: '9px 12px', color: C.text, fontSize: 13,
 outline: 'none', boxSizing: 'border-box' }} />
 </div>
 <div>
 <label style={{ color: C.muted, fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 4 }}>
 {t('data.field_description_optional')}
 </label>
 <input value={desc} onChange={e => setDesc(e.target.value)} placeholder={t('data.desc_example_ph')}
 style={{ width: '100%', background: C.surface, border: `1px solid ${C.border2}`,
 borderRadius: 8, padding: '9px 12px', color: C.text, fontSize: 13,
 outline: 'none', boxSizing: 'border-box' }} />
 </div>
 </div>
 <button onClick={uploadFile} disabled={busy}
 style={{ width: '100%', padding: '11px', borderRadius: 8, background: C.green,
 border: 'none', color: '#fff', fontWeight: 700, fontSize: 14, cursor: busy ? 'not-allowed' : 'pointer',
 opacity: busy ? 0.7 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
 {busy ? <Spinner size={16} /> : <Upload size={16} />}
 {busy ? t('data.btn_uploading') : t('data.btn_upload_file')}
 </button>
 </div>
 ) : (
 <DropZone onFile={setFile} />
 )}
 </div>
 )
 }

 return (
 <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
 <h3 style={{ margin: 0, color: C.text, fontSize: 16, fontWeight: 700, flex: 1 }}>{t('data.new_data_source')}</h3>
 <button onClick={onCancel} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer' }}>
 <X size={16} aria-hidden="true" />
 </button>
 </div>
 <div style={{ display: 'flex', gap: 8 }}>
 <button style={typeTabStyle(false, C.green)} onClick={() => { setMode('file'); setErr(null) }}>
 <FileSpreadsheet size={13} /> {t('data.file_upload')}
 </button>
 <button style={typeTabStyle(true, C.blue)} onClick={() => setMode('sql')}>
 <Database size={13} /> {t('data.sql_database')}
 </button>
 </div>
 {err && (
 <div style={{ background: C.redDim, border: `1px solid ${C.red}30`,
 borderRadius: 8, padding: '10px 14px', color: C.red, fontSize: 13 }}>
 {err}
 </div>
 )}
 <SqlForm
 saving={busy}
 onCancel={onCancel}
 onSave={async form => {
 setBusy(true); setErr(null)
 try {
 onCreated(await createSqlSource({
 name: form.name, description: form.description || undefined,
 host: form.host, port: Number(form.port), database: form.database,
 username: form.username, password: form.password, engine: form.engine,
 }))
 } catch (e: any) { setErr(e.message) }
 finally { setBusy(false) }
 }}
 />
 </div>
 )
}

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyRight({ onCreate }: { onCreate: () => void }) {
 const { t } = useLanguage()
 return (
 <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
 justifyContent: 'center', height: '100%', padding: 40, textAlign: 'center' }}>
 <Layers size={48} color={C.border2} style={{ marginBottom: 20 }} />
 <h2 style={{ color: C.text, fontSize: 20, fontWeight: 700, margin: '0 0 8px' }}>{t('data.no_source_selected')}</h2>
 <p style={{ color: C.muted, fontSize: 14, margin: '0 0 32px', maxWidth: 340 }}>
 {t('data.no_source_selected_hint')}
 </p>
 <button onClick={onCreate}
 style={{ padding: '12px 24px', borderRadius: 10, background: C.greenDim,
 border: `1px solid ${C.green}40`, color: C.green, fontWeight: 700, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
 <Plus size={16} /> {t('data.new_data_source')}
 </button>
 </div>
 )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function DataPage() {
 const { t } = useLanguage()
 const [sources, setSources] = useState<DataSource[]>([])
 const [loading, setLoading] = useState(true)
 const [loadErr, setLoadErr] = useState<string | null>(null)
 const [selected, setSelected] = useState<DataSource | null>(null)
 const [creating, setCreating] = useState<'file' | 'sql' | 'new' | null>(null)
 const [search, setSearch] = useState('')

 const load = useCallback(async () => {
 setLoading(true); setLoadErr(null)
 try {
 const res = await listDataSources(0, 200)
 setSources(res.items)
 } catch (e: any) { setLoadErr(e.message) }
 finally { setLoading(false) }
 }, [])

 useEffect(() => { load() }, [load])

 const filtered = sources.filter(s =>
 s.name.toLowerCase().includes(search.toLowerCase()) ||
 (s.description || '').toLowerCase().includes(search.toLowerCase())
 )

 const handleCreated = (src: DataSource) => {
 setSources(prev => [src, ...prev])
 setCreating(null)
 setSelected(src)
 }

 const handleUpdated = (src: DataSource) => {
 setSources(prev => prev.map(s => s.id === src.id ? src : s))
 setSelected(src)
 }

 const handleDeleted = () => {
 if (!selected) return
 setSources(prev => prev.filter(s => s.id !== selected.id))
 setSelected(null)
 }

 return (
 <div style={{ display: 'flex', height: 'calc(100vh - 52px)', margin: '-24px',
 background: C.bg, color: C.text, overflow: 'hidden' }}>

 {/* ── Left sidebar ─────────────────────────────────────────────────── */}
 <div style={{ width: 280, flexShrink: 0, borderRight: `1px solid ${C.border}`,
 display: 'flex', flexDirection: 'column', background: C.surface }}>

 {/* Sidebar header */}
 <div style={{ padding: '20px 16px 14px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
 <div>
 <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800, color: C.text }}>{t('data.page_title')}</h1>
 <p style={{ margin: '2px 0 0', fontSize: 12, color: C.muted }}>
 {sources.length} {sources.length !== 1 ? t('data.source_plural') : t('data.source_singular')}
 </p>
 </div>
 <button onClick={load} title={t('data.refresh_title')} aria-label={t('data.refresh_title')}
 style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer', padding: 4 }}>
 <RefreshCw size={14} aria-hidden="true" />
 </button>
 </div>
 <input
 value={search}
 onChange={e => setSearch(e.target.value)}
 placeholder={t('data.search_sources_ph')}
 style={{ width: '100%', background: C.card, border: `1px solid ${C.border}`,
 borderRadius: 8, padding: '8px 12px', color: C.text, fontSize: 13,
 outline: 'none', boxSizing: 'border-box' }}
 />
 </div>

 {/* New item button */}
 <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.border}` }}>
 <button
 onClick={() => { setCreating(creating ? null : 'new'); setSelected(null) }}
 style={{
 width: '100%', padding: '8px 12px', borderRadius: 8,
 background: creating ? C.greenDim : 'transparent',
 border: `1px solid ${creating ? C.green : C.border}`,
 color: creating ? C.green : C.muted,
 cursor: 'pointer', display: 'flex', alignItems: 'center',
 justifyContent: 'center', gap: 6,
 fontSize: 12, fontWeight: 600, transition: 'all 0.15s',
 }}
 >
 <Plus size={13} /> {t('data.btn_new_item')}
 </button>
 </div>

 {/* Source list */}
 <div style={{ flex: 1, overflowY: 'auto' }}>
 {loading ? (
 <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}>
 <Spinner size={20} />
 </div>
 ) : loadErr ? (
 <div style={{ padding: 16, color: C.red, fontSize: 13 }}>{loadErr}</div>
 ) : filtered.length === 0 ? (
 <div style={{ padding: '24px 16px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
 {search ? t('data.no_matches') : t('data.no_sources_yet')}
 </div>
 ) : (
 filtered.map(src => {
 const isActive = selected?.id === src.id && !creating
 return (
 <button
 key={src.id}
 onClick={() => { setSelected(src); setCreating(null) }}
 style={{
 width: '100%', textAlign: 'left', padding: '12px 14px',
 background: isActive ? C.greenDim : 'transparent',
 border: 'none', borderLeft: `3px solid ${isActive ? C.green : 'transparent'}`,
 cursor: 'pointer', transition: 'all 0.15s',
 borderBottom: `1px solid ${C.border}`,
 }}
 >
 <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
 <SourceIcon type={src.source_type} size={15} />
 <span style={{ flex: 1, color: isActive ? C.green : C.text,
 fontSize: 13, fontWeight: 600, overflow: 'hidden',
 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {src.name}
 </span>
 <StatusBadge status={src.connection_status} />
 </div>
 {src.description && (
 <p style={{ margin: '4px 0 0 23px', color: C.muted, fontSize: 11,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {src.description}
 </p>
 )}
 <div style={{ margin: '4px 0 0 23px', display: 'flex', gap: 10 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>
 {src.source_type === 'sql'
 ? `${src.sql_config?.engine} · ${src.sql_config?.host}`
 : fmt(src.size_bytes)}
 </span>
 {src.row_count && (
 <span style={{ color: C.muted, fontSize: 11 }}>
 {src.row_count.toLocaleString()} {src.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}
 </span>
 )}
 </div>
 </button>
 )
 })
 )}
 </div>
 </div>

 {/* ── Right panel ──────────────────────────────────────────────────── */}
 <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
 background: C.card, overflow: 'hidden' }}>
 {creating ? (
 <NewSourcePanel
 onCreated={handleCreated}
 onCancel={() => setCreating(null)}
 />
 ) : selected ? (
 <SourceDetail
 key={selected.id}
 source={selected}
 onUpdated={handleUpdated}
 onDeleted={handleDeleted}
 />
 ) : (
 <EmptyRight onCreate={() => { setCreating('new'); setSelected(null) }} />
 )}
 </div>
 </div>
 )
}

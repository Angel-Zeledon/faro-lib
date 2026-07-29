'use client'
import { useState, useEffect, useRef, useCallback, useMemo, useId } from 'react'
import {
 listDataSources, createFileSource, createSqlSource, replaceFileSource,
 updateSqlConfig, testSqlConnection, executeSqlQuery, saveSqlQuery, materializeSqlSource,
 exportSqlQueryXlsx,
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
 CheckCircle2, XCircle, Edit2, X,
 Play, Save, Link2, Table2, AlertTriangle, Eye,
 Layers, ArrowLeft, BarChart2, ChevronUp, ChevronDown,
 Terminal, Search,
} from 'lucide-react'
import Input, { FieldLabel, Select } from '@/components/ui/Input'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  seasonalityClassLabel, trendDirectionLabel, stationarityLabel,
  crostonClassLabel, distributionLabel,
} from '@/lib/enumLabels'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useErrorDetail } from '@/components/ui/States'
import { useToast } from '@/contexts/ToastContext'
import DataTabs from '@/components/layout/DataTabs'

// ── Palette ──────────────────────────────────────────────────────────────────
/**
 * A token at partial strength. `var(--x)` cannot take the 8-digit hex suffix a
 * literal can — `var(--accent)18` is not a colour, it is silently dropped — so
 * every tint on this screen goes through color-mix instead.
 */
const alpha = (c: string, pct: number) => `color-mix(in srgb, ${c} ${pct}%, transparent)`

/**
 * Tokens only.
 *
 * This screen used to carry its own hex set — #10b981 green, #3b82f6 blue,
 * #ef4444 red, #f59e0b amber — which did two bad things at once: it read as a
 * different product from the rest of Faro (the app's accent is the petrol teal
 * `--accent`, not an emerald), and it failed WCAG AA as text. #10b981 on the
 * white surface is 2.5:1, and it was the colour of the active tab label, the
 * "connected" badge and the SKU column. Everything now points at globals.css,
 * so the screen follows the theme instead of fighting it.
 */
const C = {
 bg: 'var(--bg)',
 surface: 'var(--surface)',
 card: 'var(--surface-2)',
 inset: 'var(--surface-3)',
 border: 'var(--border)',
 border2: 'var(--border-strong)',
 green: 'var(--accent)',
 greenDim: 'var(--accent-dim)',
 blue: 'var(--info)',
 blueDim: alpha('var(--info)', 12),
 amber: 'var(--warning)',
 red: 'var(--danger)',
 redDim: alpha('var(--danger)', 10),
 text: 'var(--text)',
 muted: 'var(--muted)',
 dim: 'var(--dim)',
}

/**
 * One monospace stack for the whole screen — the SQL surface and every value in
 * a result grid. This is the only new font family the visual pass introduces,
 * and it is what makes a query read as code and a column of figures read as
 * data rather than as prose.
 */
const MONO = "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', 'Fira Code', Consolas, 'Liberation Mono', monospace"

/**
 * The error block, shared by every failure this screen can show.
 *
 * The fill is `--surface`, not a danger wash. `--danger` text on its own 10%
 * tint measures 4.1:1 on the light theme and 4.2:1 on the dark one — both short
 * of AA, and an error message is the last text in the app that should be hard
 * to read. On the plain surface it is 4.8:1 / 4.6:1, and the 3px left rule
 * carries the alarm the fill used to.
 */
const errorBlock: React.CSSProperties = {
 background: 'var(--surface)',
 border: `1px solid ${alpha('var(--danger)', 38)}`,
 borderLeft: '3px solid var(--danger)',
 borderRadius: 8, padding: '11px 14px',
 color: 'var(--danger)', fontSize: 12.5, lineHeight: 1.55,
}

/**
 * Whether a grid value should sit against the right edge.
 *
 * A database client aligns numerics right so the decimal points stack and the
 * eye can compare magnitudes down a column. Dates stay left: they are fixed
 * width, and right-aligning them only detaches them from their header.
 */
function isNumeric(v: unknown): boolean {
 if (typeof v === 'number') return Number.isFinite(v)
 if (typeof v !== 'string') return false
 const s = v.trim()
 if (!s) return false
 // Reject anything date-shaped (2026-06-01, 01/06/2026) before the number test.
 if (/[-/:]/.test(s.slice(1))) return false
 return !Number.isNaN(Number(s.replace(/,/g, '')))
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
/**
 * A quiet connection-status marker, the way a database client shows it: the
 * colour lives in a small dot, the label stays neutral text.
 *
 * The label used to be tinted the same colour as the badge fill, which is where
 * the contrast went — amber "pendiente" text on an amber wash is 2.9:1 on the
 * light theme. A dot is a non-text UI component (3:1 threshold, which all three
 * states clear comfortably) and the label now rides `--muted`, 6.5:1 or better
 * on every surface this badge lands on.
 */
function StatusBadge({ status }: { status: string }) {
 const { t } = useLanguage()
 const cfg = {
 connected: { color: C.green, label: t('data.status_connected') },
 pending: { color: C.amber, label: t('data.status_pending') },
 error: { color: C.red, label: t('data.status_error') },
 }[status] ?? { color: C.dim, label: status }
 return (
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
 padding: '2px 8px 2px 7px', borderRadius: 20,
 background: C.card, border: `1px solid ${C.border}`,
 color: C.muted, fontSize: 10.5, fontWeight: 600,
 letterSpacing: '0.01em', whiteSpace: 'nowrap', flexShrink: 0 }}>
 <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%',
 background: cfg.color, flexShrink: 0 }} />
 {cfg.label}
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
/**
 * Shared chrome for every result grid on this screen (query results, file
 * preview, spreadsheet editor). A result set should read as data, not as a
 * document: monospace values so glyph widths line up, a header that is visibly
 * a header rather than a first row, tight rows, and hairlines instead of a
 * boxed border on every cell.
 */
const GRID_SHELL: React.CSSProperties = {
 overflow: 'auto', maxHeight: 380, borderRadius: 10,
 border: `1px solid ${C.border}`, background: C.surface,
}

/** Header cell: uppercase, tracked out, and sticky so it survives the scroll. */
const gridTh = (align: 'left' | 'right' = 'left'): React.CSSProperties => ({
 padding: '9px 12px', textAlign: align, whiteSpace: 'nowrap',
 background: C.card, color: C.muted, fontWeight: 600, fontSize: 10,
 textTransform: 'uppercase', letterSpacing: '0.06em',
 borderBottom: `1px solid ${C.border2}`, position: 'sticky', top: 0, zIndex: 1,
})

/** The zebra: `--surface` base, `--surface-2` on odd rows. Barely there on
 *  purpose — it should guide the eye across a wide row, not stripe the panel. */
const gridRowBg = (i: number) => (i % 2 === 1 ? C.card : C.surface)

function DataGrid({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
 const { t } = useLanguage()
 if (!columns.length) return <p style={{ color: C.muted, padding: 20 }}>{t('common.no_data')}</p>

 // Align a column by what it actually holds, decided once from the first
 // non-null value rather than per cell, so one stray string cannot make a
 // numeric column jump sides halfway down.
 const alignOf = new Map(columns.map(c => {
  const sample = rows.find(r => r[c] != null)?.[c]
  return [c, isNumeric(sample) ? 'right' as const : 'left' as const]
 }))

 return (
 <div style={GRID_SHELL}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 {columns.map(c => (
 <th key={c} style={gridTh(alignOf.get(c))}>{c}</th>
 ))}
 {/* Slack column. Without it a three-column result stretches each column
     to a third of the panel and the values drift apart; a database client
     sizes columns to their content and leaves the remainder empty. */}
 <th aria-hidden="true" style={{ ...gridTh(), width: '100%' }} />
 </tr>
 </thead>
 <tbody>
 {rows.map((row, i) => (
 <tr key={i} style={{ background: gridRowBg(i), transition: `background var(--dur-1) var(--ease-out)` }}
 onMouseEnter={e => (e.currentTarget.style.background = C.inset)}
 onMouseLeave={e => (e.currentTarget.style.background = gridRowBg(i))}>
 {columns.map(c => (
 <td key={c} style={{ padding: '5px 12px', color: C.text, whiteSpace: 'nowrap',
 fontFamily: MONO, fontSize: 11.5, lineHeight: 1.7,
 textAlign: alignOf.get(c), borderBottom: `1px solid ${C.border}`, maxWidth: 260,
 overflow: 'hidden', textOverflow: 'ellipsis' }}>
 {/* NULL is a value, not missing text — italic dim is the convention every
     database client uses, and it keeps it from reading as the literal string
     "null". Italic carries the distinction; the colour stays `--muted` rather
     than `--dim`, which is only 3.5:1 on the light surface. */}
 {row[c] == null
 ? <span style={{ color: C.muted, fontStyle: 'italic' }}>null</span>
 : String(row[c])}
 </td>
 ))}
 <td aria-hidden="true" style={{ borderBottom: `1px solid ${C.border}` }} />
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
 <circle cx={X(i)} cy={Y(d.value)} r={7} fill={alpha(C.red, 20)} />
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
 border: `1.5px dashed ${drag ? C.green : C.border2}`,
 borderRadius: 12, padding: compact ? '26px 20px' : '44px 20px',
 textAlign: 'center', cursor: 'pointer',
 transition: `border-color var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out)`,
                 // `--surface`, a step above the panel behind it, so the drop target reads
 // as a place to put something rather than as a hole in the page.
 background: drag ? C.greenDim : C.surface,
 }}
 >
 <input ref={ref} type="file" name="dataset_file" aria-label={t('data.drag_or_browse')} accept=".csv,.xlsx,.xls,.parquet,.json"
 style={{ display: 'none' }}
 onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
 <Upload size={compact ? 22 : 28} color={drag ? C.green : C.dim} style={{ margin: '0 auto 10px' }} aria-hidden="true" />
 <p style={{ color: drag ? C.green : C.text, fontWeight: 600, fontSize: 13.5, margin: '0 0 5px' }}>
 {drag ? t('data.drop_to_upload') : t('data.drag_or_browse')}
 </p>
 {/* A sentence telling you which formats are accepted is prose, not code. */}
 <p style={{ color: C.muted, fontSize: 11.5, margin: 0 }}>{t('data.file_types_hint')}</p>
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
 const uid = useId()
 const fid = (k: keyof SqlFormData) => `sql-${k}-${uid}`
 const [form, setForm] = useState<SqlFormData>({ ...SQL_DEFAULTS, ...initial })
 const set = (k: keyof SqlFormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
 setForm(f => ({ ...f, [k]: e.target.value }))

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
 {!isEdit && (
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
 <div>
 <FieldLabel htmlFor={fid('name')}>{t('data.field_source_name')} *</FieldLabel>
 <Input id={fid('name')} name="name" size="lg" tone="surface" border="strong" value={form.name} onChange={set('name')} placeholder={t('data.field_source_name_ph')} />
 </div>
 <div>
 <FieldLabel htmlFor={fid('description')}>{t('data.field_description')}</FieldLabel>
 <Input id={fid('description')} name="description" size="lg" tone="surface" border="strong" value={form.description} onChange={set('description')} placeholder={t('data.field_optional_ph')} />
 </div>
 </div>
 )}
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
 <div>
 <FieldLabel htmlFor={fid('engine')}>{t('data.field_engine')} *</FieldLabel>
 <Select id={fid('engine')} name="engine" size="lg" tone="surface" border="strong" value={form.engine} onChange={e => {
 const eng = e.target.value as SqlEngine
 setForm(f => ({ ...f, engine: eng, port: ENGINE_PORTS[eng] || f.port }))
 }}>
 {['postgresql', 'mysql', 'mssql', 'oracle'].map(e => (
 <option key={e} value={e}>{e}</option>
 ))}
 </Select>
 </div>
 <div>
 <FieldLabel htmlFor={fid('host')}>{t('data.field_host')} *</FieldLabel>
 <Input id={fid('host')} name="host" size="lg" tone="surface" border="strong" value={form.host} onChange={set('host')} placeholder="localhost" />
 </div>
 <div>
 <FieldLabel htmlFor={fid('port')}>{t('data.field_port')} *</FieldLabel>
 <Input id={fid('port')} name="port" size="lg" tone="surface" border="strong" value={form.port} onChange={set('port')} type="number" />
 </div>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
 <div>
 <FieldLabel htmlFor={fid('database')}>{t('data.field_database')} *</FieldLabel>
 <Input id={fid('database')} name="database" size="lg" tone="surface" border="strong" value={form.database} onChange={set('database')} placeholder={t('data.field_database_ph')} />
 </div>
 <div>
 <FieldLabel htmlFor={fid('username')}>{t('data.field_username')} *</FieldLabel>
 <Input id={fid('username')} name="username" size="lg" tone="surface" border="strong" value={form.username} onChange={set('username')} placeholder={t('data.field_username_ph')} />
 </div>
 <div>
 <FieldLabel htmlFor={fid('password')}>{t('data.field_password')} {isEdit && <span style={{ fontWeight: 400 }}>{t('data.field_password_keep')}</span>}</FieldLabel>
 <Input id={fid('password')} name="password" size="lg" tone="surface" border="strong" type="password" value={form.password} onChange={set('password')} placeholder="••••••••" />
 </div>
 </div>
 <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 2 }}>
 {onCancel && (
 <button className="btn" onClick={onCancel} style={{ padding: '9px 18px', borderRadius: 8,
 background: 'transparent', border: `1px solid ${C.border2}`, color: C.muted,
 fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>
 {t('common.cancel')}
 </button>
 )}
 <button className="btn" onClick={() => onSave(form)} disabled={saving}
 style={{ padding: '9px 20px', borderRadius: 8, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, fontSize: 13, cursor: saving ? 'not-allowed' : 'pointer',
 opacity: saving ? 0.7 : 1, display: 'flex', alignItems: 'center', gap: 6 }}>
 {saving ? <Spinner size={14} /> : <Save size={14} />}
 {isEdit ? t('data.btn_save_changes') : t('data.btn_create_connection')}
 </button>
 </div>
 </div>
 )
}

// ── SQL Editor Panel ──────────────────────────────────────────────────────────
function SqlEditorPanel({ source, onSaved, onDatasetCreated }: {
 source: DataSource
 onSaved: (s: DataSource) => void
 onDatasetCreated?: (ds: DataSource) => void
}) {
 const { t } = useLanguage()
 const { addToast } = useToast()
 const errorDetail = useErrorDetail()
 const [sql, setSql] = useState(source.saved_query || '')
 const [result, setResult] = useState<SqlQueryResult | null>(null)
 const [running, setRunning] = useState(false)
 const [saving, setSaving] = useState(false)
 const [materializing, setMaterializing] = useState(false)
 const [exporting, setExporting] = useState(false)
 const [err, setErr] = useState<string | null>(null)

 const run = async () => {
 if (!sql.trim()) return
 setRunning(true); setErr(null)
 try {
 const r = await executeSqlQuery(source.id, sql)
 setResult(r)
 } catch (e: unknown) { setErr(errorDetail(e)) }
 finally { setRunning(false) }
 }

 const save = async () => {
 if (!sql.trim()) return
 setSaving(true)
 try {
 const updated = await saveSqlQuery(source.id, sql)
 onSaved(updated)
 } catch (e: unknown) { setErr(errorDetail(e)) }
 finally { setSaving(false) }
 }

 // Download the FULL query result (not the preview) as an .xlsx workbook.
 const exportXlsx = async () => {
 if (!sql.trim() || exporting) return
 setExporting(true); setErr(null)
 try {
 await exportSqlQueryXlsx(source.id, sql, `${source.name}.xlsx`)
 } catch (e: unknown) { setErr(errorDetail(e)) }
 finally { setExporting(false) }
 }

 // Snapshot the FULL query result (not the preview) as a CSV dataset; from
 // there the wizard treats it exactly like an uploaded file.
 const materialize = async () => {
 if (!sql.trim() || materializing) return
 setMaterializing(true); setErr(null)
 try {
 const ds = await materializeSqlSource(source.id, { sql, name: `${source.name} (SQL)` })
 addToast(
 t('data.materialize_done_title'),
 `"${ds.name}" — ${t('data.materialize_done_body')}`,
 'success',
 )
 onDatasetCreated?.(ds)
 // The snapshot's shape lands on the source row too — refresh its card.
 try { onSaved(await getDataSource(source.id)) } catch { /* card refresh only */ }
 } catch (e: unknown) { setErr(errorDetail(e)) }
 finally { setMaterializing(false) }
 }

 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
 {/* The editor is one framed object — toolbar welded to the code surface by a
     shared border — instead of a floating label, a floating button row and a
     boxed textarea. That frame is what makes it read as a query pane rather
     than as a form field that happens to be monospace. */}
 <div style={{ border: `1px solid ${C.border2}`, borderRadius: 10, overflow: 'hidden',
 background: C.inset }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 gap: 12, padding: '8px 10px 8px 14px', background: C.surface,
 borderBottom: `1px solid ${C.border}` }}>
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7,
 color: C.muted, fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', letterSpacing: '0.06em' }}>
 <Terminal size={12} aria-hidden="true" /> {t('data.sql_editor_label')}
 </span>
 <div style={{ display: 'flex', gap: 8 }}>
 <button className="btn" onClick={save} disabled={saving || !sql.trim()}
 style={{ padding: '6px 14px', borderRadius: 7, background: 'transparent',
 border: `1px solid ${C.border2}`, color: C.muted, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600 }}>
 {saving ? <Spinner size={12} /> : <Save size={12} />} {t('data.btn_save_query')}
 </button>
 <button className="btn" onClick={run} disabled={running || !sql.trim()}
 style={{ padding: '6px 16px', borderRadius: 7, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
 opacity: running || !sql.trim() ? 0.6 : 1 }}>
 {running ? <Spinner size={12} /> : <Play size={12} />} {t('data.btn_run')}
 </button>
 </div>
 </div>
 <textarea
 name="sql_query"
 aria-label={t('data.sql_editor_label')}
 value={sql}
 onChange={e => setSql(e.target.value)}
 onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run() } }}
 spellCheck={false}
 placeholder={t('data.query_placeholder')}
 style={{
 // `--surface-3`, one step below the panel it sits on, so the code
 // surface reads as inset rather than as more page. No border of its
 // own (the frame draws one), and the roomier line height a query needs.
 display: 'block', width: '100%', minHeight: 180,
 background: C.inset, border: 'none', borderRadius: 0,
 padding: '14px 16px',
 fontFamily: MONO, fontSize: 12.5, color: C.text,
 lineHeight: 1.75, tabSize: 2,
 resize: 'vertical', outline: 'none', boxSizing: 'border-box',
 }}
 />
 </div>
                 {/* A database error is the server talking, so it keeps the code voice. */}
 {err && (
 <div style={{ ...errorBlock, fontFamily: MONO, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
 {err}
 </div>
 )}
 {result && (
 <div>
 {/* Result-set status line: the row count is the fact, so it reads as a
     figure in the code voice; the two exports stay where they were. */}
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
 <Table2 size={13} color={C.muted} aria-hidden="true" />
 <span style={{ color: C.text, fontSize: 12, fontWeight: 600, fontFamily: MONO }}>
 {result.row_count}
 </span>
 <span style={{ color: C.muted, fontSize: 12 }}>
 {result.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}{result.truncated ? ` ${t('data.truncated_suffix')}` : ''}
 </span>
 <button className="btn" onClick={exportXlsx} disabled={exporting}
 title={t('data.btn_export_xlsx_hint')}
 style={{ marginLeft: 'auto', padding: '6px 14px', borderRadius: 7,
 background: 'transparent', border: `1px solid ${C.border2}`, color: C.muted,
 fontWeight: 600, cursor: exporting ? 'default' : 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
 opacity: exporting ? 0.6 : 1 }}>
 {exporting ? <Spinner size={12} /> : <FileSpreadsheet size={12} />} {t('data.btn_export_xlsx')}
 </button>
 <button className="btn" onClick={materialize} disabled={materializing}
 title={t('data.btn_materialize_hint')}
 style={{ padding: '6px 14px', borderRadius: 7,
 background: C.greenDim, border: `1px solid ${alpha(C.green, 45)}`, color: C.green,
 fontWeight: 600, cursor: materializing ? 'default' : 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
 opacity: materializing ? 0.6 : 1 }}>
 {materializing ? <Spinner size={12} /> : <Database size={12} />} {t('data.btn_materialize')}
 </button>
 </div>
 <DataGrid columns={result.columns} rows={result.rows} />
 </div>
 )}
 </div>
 )
}

// ── Analysis: SKU Summary Table ───────────────────────────────────────────────
const TD: React.CSSProperties = {
 padding: '6px 12px', color: 'var(--text)', borderBottom: '1px solid var(--border)',
 whiteSpace: 'nowrap', lineHeight: 1.7,
}

/**
 * The section caption. Six near-identical copies of this used to sit inline and
 * disagreed on tracking (0.05em, 0.06em, none at all); one declaration is what
 * "one type scale" actually costs.
 */
const EYEBROW: React.CSSProperties = {
 color: 'var(--muted)', fontSize: 10, fontWeight: 700,
 textTransform: 'uppercase', letterSpacing: '0.06em',
}

/** The panel every statistic on this screen sits in. */
const PANEL: React.CSSProperties = {
 background: 'var(--surface)', borderRadius: 10,
 border: '1px solid var(--border)', padding: '13px 15px',
}

/** Figures in a table are display type: monospace so the columns line up. */
const TD_NUM: React.CSSProperties = { ...TD, fontFamily: MONO, fontSize: 11.5, textAlign: 'right' }

/**
 * A classification's colour, carried by a dot instead of by the word itself.
 *
 * The words used to be tinted directly, which is where the contrast went: amber
 * on the light surface is 3.2:1 and the old #f59e0b was 2.2:1. A 6px dot is a
 * non-text UI component (3:1), the label rides `--text`, and the signal is
 * identical — this is the same move the status badge makes.
 */
function Dot({ color }: { color: string }) {
 return <span aria-hidden="true" style={{ display: 'inline-block', width: 6, height: 6,
  borderRadius: '50%', background: color, marginRight: 6, verticalAlign: 'middle',
  position: 'relative', top: -1 }} />
}

function AnalysisSummaryTable({ rows, sortCol, sortDir, onSort, onSelect }: {
 rows: AnalysisSummaryRow[]
 sortCol: string; sortDir: 'asc' | 'desc'
 onSort: (col: string) => void
 onSelect: (sku: string) => void
}) {
 const { t } = useLanguage()
 if (!rows.length) return <p style={{ color: C.muted, padding: 20 }}>{t('data.no_skus_analysed')}</p>

 const Hdr = ({ label, col, align = 'left' }: { label: string; col: string; align?: 'left' | 'right' }) => {
 const active = sortCol === col
 return (
 <th onClick={() => onSort(col)}
 style={{ ...gridTh(align), color: active ? C.green : C.muted,
 cursor: 'pointer', userSelect: 'none' }}>
 <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
 {label}
 {active ? (sortDir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />) : null}
 </span>
 </th>
 )
 }

 return (
 <div style={{ ...GRID_SHELL, maxHeight: 460 }}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 <Hdr label={t('data.col_sku')} col="sku" />
 <Hdr label={t('data.col_n')} col="n" align="right" />
 <Hdr label={t('data.col_mean')} col="mean" align="right" />
 <Hdr label={t('data.col_cv')} col="cv" align="right" />
 <Hdr label={t('data.col_seasonality')} col="seasonality_class" />
 <Hdr label={t('data.col_period')} col="dominant_period" align="right" />
 <Hdr label={t('data.col_trend')} col="trend_direction" />
 <Hdr label={t('data.col_stationarity')} col="stationarity" />
 <Hdr label={t('data.col_demand_type')} col="croston_class" />
 </tr>
 </thead>
 <tbody>
 {rows.map((row, i) => {
 const bg = gridRowBg(i)
 const trendColor = row.trend_direction === 'increasing' ? C.green
 : row.trend_direction === 'decreasing' ? C.red : C.dim
 const seasColor = row.seasonality_class === 'strong' ? C.green
 : row.seasonality_class === 'moderate' ? C.amber : C.dim
 const statColor = row.stationarity === 'stationary' ? C.green
 : row.stationarity ? C.amber : C.dim
 return (
 <tr key={row.sku ?? i}
 onClick={() => row.sku && !row.error && onSelect(row.sku)}
 onMouseEnter={e => (e.currentTarget.style.background = C.greenDim)}
 onMouseLeave={e => (e.currentTarget.style.background = bg)}
 style={{ background: bg, cursor: row.error ? 'default' : 'pointer',
 transition: `background var(--dur-1) var(--ease-out)` }}>
 <td style={{ ...TD, color: C.green, fontWeight: 600, fontFamily: MONO, fontSize: 11.5 }}>
 {row.sku ?? '__all__'}
 {row.error && <span style={{ color: C.red, fontSize: 10, marginLeft: 6 }}>⚠ {t('data.error_short')}</span>}
 </td>
 <td style={TD_NUM}>{row.n?.toLocaleString() ?? '—'}</td>
 <td style={TD_NUM}>{row.mean != null ? row.mean.toFixed(1) : '—'}</td>
 {/* A CV above 1 is the one flag in this row whose colour is not also
     spelled out in words, so it keeps its marker — as a dot, next to a
     figure that stays readable. */}
 <td style={{ ...TD_NUM, fontWeight: row.cv != null && row.cv > 1 ? 700 : 400 }}>
 {row.cv != null && row.cv > 1 && <Dot color={C.amber} />}
 {row.cv != null ? row.cv.toFixed(2) : '—'}
 </td>
 <td style={TD}>
 <Dot color={seasColor} />{seasonalityClassLabel(t, row.seasonality_class)}
 </td>
 <td style={TD_NUM}>{row.dominant_period ?? '—'}</td>
 <td style={TD}>
 <Dot color={trendColor} />
 {row.trend_direction === 'increasing' ? '↑ ' : row.trend_direction === 'decreasing' ? '↓ ' : row.trend_direction ? '→ ' : ''}
 {trendDirectionLabel(t, row.trend_direction)}
 </td>
 <td style={TD}><Dot color={statColor} />{stationarityLabel(t, row.stationarity)}</td>
 <td style={{ ...TD, color: C.muted, fontSize: 11 }}>{crostonClassLabel(t, row.croston_class)}</td>
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
 [t('data.stat_best_dist'), distributionLabel(t, dist.best_distribution as string | null)],
 ],
 },
 {
 title: t('data.panel_seasonality'), color: C.green,
 rows: [
 [t('data.stat_class'), seasonalityClassLabel(t, seas.classification as string | null)],
 [t('data.stat_period'), String(seas.dominant_period ?? '—')],
 [t('data.stat_strength'), n(seas.seasonal_strength)],
 [t('data.stat_stl_seasonal'), n(dec.seasonal_strength)],
 [t('data.stat_stl_trend'), n(dec.trend_strength)],
 ],
 },
 {
 title: t('data.panel_trend'), color: C.amber,
 rows: [
 [t('data.stat_direction'), trendDirectionLabel(t, mk.direction as string | null)],
 [t('data.stat_mk_pvalue'), pf(mk.pvalue)],
 [t('data.stat_sens_slope'), n(trend.sens_slope, 4)],
 [t('data.stat_linear_r2'), n(lin.r2)],
 [t('data.stat_change_points'), String((trend.change_points as unknown[] | undefined)?.length ?? '—')],
 ],
 },
 {
 title: t('data.panel_stationarity'), color: C.muted,
 rows: [
 [t('data.stat_verdict'), stationarityLabel(t, stat.verdict as string | null)],
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
 <h3 style={{ margin: 0, color: C.text, fontSize: 15, fontWeight: 700,
 fontFamily: sku === '__all__' ? undefined : MONO, letterSpacing: '-0.01em' }}>
 {sku === '__all__' ? t('data.full_dataset') : sku}
 </h3>
 {/* Range and cadence are machine facts about the series, so they keep the
     code voice the identifier above them already speaks. */}
 <span style={{ color: C.muted, fontSize: 11.5, fontFamily: MONO }}>
 {dr.start ? `${dr.start} → ${dr.end}` : ''}
 {dr.n_days != null ? ` · ${dr.n_days} ${Number(dr.n_days) === 1 ? t('data.days_singular') : t('data.days_plural')}` : ''}
 {dr.freq_detected ? ` · ${dr.freq_detected}` : ''}
 {r.n_observations != null ? ` · ${r.n_observations} ${t('data.observations_suffix')}` : ''}
 </span>
 </div>
 </div>

 {/* Series chart */}
 {detail.series.length > 1 && (
 <div style={{ ...PANEL, padding: '14px 14px 10px' }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
 <div style={EYEBROW}>
 {t('data.section_time_series')}
 </div>
 {(detail.outliers?.length ?? 0) > 0 && (
 <button
 onClick={() => setShowOutliers(v => !v)}
 style={{
 display: 'flex', alignItems: 'center', gap: 5,
 padding: '3px 10px', borderRadius: 6,
                 // The toggle reads as "on" through its border and dot, not through a
 // wash behind its own label: `--danger` over a danger tint is 4.1:1.
 background: C.surface,
 border: `1px solid ${showOutliers ? alpha(C.red, 45) : C.border2}`,
 color: showOutliers ? C.red : C.muted,
 fontSize: 11, cursor: 'pointer',
 transition: `border-color var(--dur-1) var(--ease-out), color var(--dur-1) var(--ease-out)`,
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
 border: `1px solid ${alpha(C.red, 30)}`,
 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
 <AlertTriangle size={13} color={C.red} aria-hidden="true" />
 <span style={{ color: C.red, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
 {detail.outliers!.length} {detail.outliers!.length !== 1 ? t('data.outliers_detected_plural') : t('data.outliers_detected_singular')}
 </span>
 <span style={{ color: C.muted, fontSize: 11 }}>{t('data.iqr_method')}</span>
 </div>
 <div style={{ ...GRID_SHELL, maxHeight: 300 }}>
 <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
 <thead>
 <tr>
 {[
 [t('data.col_date'), 'left'], [t('data.col_value'), 'right'],
 [t('data.col_zscore'), 'right'], [t('data.col_direction'), 'left'],
 [t('data.col_reason'), 'left'],
 ].map(([h, a]) => (
 <th key={h} style={gridTh(a as 'left' | 'right')}>{h}</th>
 ))}
 </tr>
 </thead>
 <tbody>
 {detail.outliers!.map((o, i) => {
 // High vs low is spelled out in the direction column, so the severity
 // colour rides a dot and the figures stay at full contrast.
 const sev = o.value > o.upper_bound ? C.red : C.amber
 return (
 <tr key={o.date} style={{ background: gridRowBg(i) }}>
 <td style={{ ...TD, color: C.muted, fontFamily: MONO, fontSize: 11 }}>
 {o.date}
 </td>
 <td style={{ ...TD_NUM, fontWeight: 700 }}>
 {o.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
 </td>
 <td style={{ ...TD_NUM, fontWeight: Math.abs(o.z_score) > 3 ? 700 : 400 }}>
 {o.z_score > 0 ? '+' : ''}{o.z_score}σ
 </td>
 <td style={{ ...TD, whiteSpace: 'nowrap' }}>
 <Dot color={sev} />
 {o.value > o.upper_bound ? t('data.direction_high') : t('data.direction_low')}
 </td>
 <td style={{ ...TD, color: C.muted, fontSize: 11 }}>
 {o.reason}
 </td>
 </tr>
 )
 })}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Stat panels */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                 {/* Four panels, four different title colours — one of which (#f59e0b) was
     2.2:1 on white. The colour moves to a 2px rule under the caption, where
     it still tells the panels apart but no longer has to be legible type. */}
 {panels.map(p => (
 <div key={p.title} style={PANEL}>
 <div style={{ ...EYEBROW, paddingBottom: 7, marginBottom: 9,
 borderBottom: `2px solid ${alpha(p.color, 55)}` }}>
 {p.title}
 </div>
 {p.rows.map(([label, val]) => (
 <div key={label as string} style={{ display: 'flex', justifyContent: 'space-between',
 alignItems: 'baseline', marginBottom: 5, gap: 8 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{label}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600, fontFamily: MONO,
 maxWidth: 96, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' }}>
 {String(val)}
 </span>
 </div>
 ))}
 </div>
 ))}
 </div>

 {/* STL Decomposition sparklines */}
 {decTrend.length > 2 && (
 <div style={PANEL}>
 <div style={{ ...EYEBROW, marginBottom: 14 }}>
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
 <div style={PANEL}>
 <div style={{ ...EYEBROW, marginBottom: 10 }}>{t('data.section_autocorrelation')}</div>
 {[
 [t('data.ac_suggested_ar'), String(acf.suggested_ar_order ?? '—')],
 [t('data.ac_suggested_ma'), String(acf.suggested_ma_order ?? '—')],
 [t('data.ac_white_noise'), lb.is_white_noise != null ? (lb.is_white_noise ? t('common.yes') : t('common.no')) : '—'],
 [t('data.ac_ljung_box_p'), pf(lb.pvalue)],
 ].map(([l, v]) => (
 <div key={l} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{l}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600, fontFamily: MONO }}>{v}</span>
 </div>
 ))}
 </div>

 <div style={PANEL}>
 <div style={{ ...EYEBROW, marginBottom: 10 }}>{t('data.section_demand_class')}</div>
 {[
 [t('data.dc_croston_class'), crostonClassLabel(t, croston.classification as string | null)],
 [t('data.dc_adi'), n(croston.adi)],
 [t('data.dc_cv2'), n(croston.cv2)],
 [t('data.dc_best_fit_dist'), distributionLabel(t, dist.best_distribution as string | null)],
 [t('data.dc_is_normal'), norm.is_normal != null ? (norm.is_normal ? t('common.yes') : t('common.no')) : '—'],
 ].map(([l, v]) => (
 <div key={l} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
 <span style={{ color: C.muted, fontSize: 11 }}>{l}</span>
 <span style={{ color: C.text, fontSize: 11, fontWeight: 600, fontFamily: MONO }}>{String(v)}</span>
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


 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
 {/* Column mapping */}
 <div style={{ ...PANEL, padding: '14px 16px' }}>
 <div style={{ ...EYEBROW, marginBottom: 14 }}>
 {t('data.section_column_mapping')}
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
 <div>
 <FieldLabel htmlFor="analysis-date-col" variant="eyebrow" style={{ color: C.muted }}>{t('data.field_date_column')} *</FieldLabel>
 <Select id="analysis-date-col" name="date_column" size="lg" tone="surface" border="strong" value={dateCol} onChange={e => setDateCol(e.target.value)}>
 <option value="">{t('data.select_placeholder')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </Select>
 </div>
 <div>
 <FieldLabel htmlFor="analysis-target-col" variant="eyebrow" style={{ color: C.muted }}>{t('data.field_target_column')} *</FieldLabel>
 <Select id="analysis-target-col" name="target_column" size="lg" tone="surface" border="strong" value={targetCol} onChange={e => setTargetCol(e.target.value)}>
 <option value="">{t('data.select_placeholder')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </Select>
 </div>
 <div>
 <FieldLabel htmlFor="analysis-sku-col" variant="eyebrow" style={{ color: C.muted }}>{t('data.field_group_sku_column')}</FieldLabel>
 <Select id="analysis-sku-col" name="sku_column" size="lg" tone="surface" border="strong" value={skuCol} onChange={e => setSkuCol(e.target.value)}>
 <option value="">{t('data.option_none_single_series')}</option>
 {columns.map(c => <option key={c} value={c}>{c}</option>)}
 </Select>
 </div>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'end' }}>
 <div>
 <FieldLabel htmlFor="analysis-date-from" variant="eyebrow" style={{ color: C.muted }}>{t('data.field_date_from')}</FieldLabel>
 <Input id="analysis-date-from" name="date_from" type="date" size="lg" tone="surface" border="strong" value={dateFrom}
 onChange={e => setDateFrom(e.target.value)} />
 </div>
 <div>
 <FieldLabel htmlFor="analysis-date-to" variant="eyebrow" style={{ color: C.muted }}>{t('data.field_date_to')}</FieldLabel>
 <Input id="analysis-date-to" name="date_to" type="date" size="lg" tone="surface" border="strong" value={dateTo}
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
                 {/* Warning copy stays on `--text` with an amber marker: `--warning` as
     small type is 3.2:1 on the light surface. */}
 {columns.length === 0 && (
 <p style={{ margin: '10px 0 0', color: C.text, fontSize: 12,
 display: 'flex', alignItems: 'center', gap: 7 }}>
 <AlertTriangle size={13} color={C.amber} aria-hidden="true" style={{ flexShrink: 0 }} />
 {t('data.warn_preview_not_loaded')}
 </p>
 )}
 </div>

 {err && <div style={errorBlock}>{err}</div>}

 {loading && (
 <div style={{ display: 'flex', gap: 12, alignItems: 'center',
 padding: '40px 0', justifyContent: 'center' }}>
 <Spinner size={22} />
 <span style={{ color: C.muted }}>{t('data.running_statistical_analysis')}</span>
 </div>
 )}

 {result && !loading && (
 <>
                 {/* What the run was actually bound to — the caption above, the bound
     column name below it in the code voice, hairline-separated like a
     database client's object properties. */}
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 gap: 16, padding: '9px 14px', background: C.surface, borderRadius: 8,
 border: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', gap: 0, flexWrap: 'wrap' }}>
 {[
 [t('data.summary_skus'), String(result.summary.length)],
 [t('data.summary_date_col'), result.date_col],
 [t('data.summary_target_col'), result.target_col],
 ...(result.sku_col ? [[t('data.summary_group_col'), result.sku_col]] : []),
 ].map(([label, val], i) => (
 <div key={label} style={{ paddingLeft: i === 0 ? 0 : 16, paddingRight: 16,
 borderLeft: i === 0 ? 'none' : `1px solid ${C.border}` }}>
 <div style={EYEBROW}>{label}</div>
 <div style={{ color: C.text, fontSize: 12, fontWeight: 600, fontFamily: MONO, marginTop: 2 }}>{val}</div>
 </div>
 ))}
 </div>
 <span style={{ color: C.muted, fontSize: 11, flexShrink: 0 }}>{t('data.click_row_for_detail')}</span>
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
 // Through i18n: this becomes the dataset's persisted NAME, so an English user
 // should not end up with a file called "… (editado)" forever. The backend has
 // the same fallback for direct API callers, in English — the UI always sends
 // this one.
 const [name, setName] = useState(t('data.editor_copy_suffix', { name: source.name }))
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
  <div style={{ ...errorBlock, display: 'flex', alignItems: 'center', gap: 8 }}>
   <AlertTriangle size={14} aria-hidden="true" style={{ flexShrink: 0 }} /> {loadErr}
  </div>
 )

 return (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
   <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
    <input value={name} onChange={e => setName(e.target.value)}
     name="dataset_new_name" aria-label={t('data.editor_new_name')}
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
   <div style={{ color: C.muted, fontSize: 12 }}>
    {/* Inside a sentence, so it stays in the sentence's typeface. */}
    <span style={{ color: C.text, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{rows.length}</span> {t('data.editor_rows_count')}
   </div>
   {/* Same grid chrome as the read-only ones, so switching to the Edit tab does
       not feel like switching to a different application. */}
   <div style={{ ...GRID_SHELL, maxHeight: 420 }}>
    <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
     <thead>
      <tr>
       <th style={{ ...gridTh(), padding: '9px 8px', width: 1 }} />
       {columns.map(c => (
        <th key={c} style={gridTh()}>
         <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontFamily: MONO, textTransform: 'none', letterSpacing: 0,
           fontSize: 11, color: C.text }}>{c}</span>
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
       <tr key={ri} style={{ background: gridRowBg(ri) }}>
        <td style={{ padding: '2px 6px', borderBottom: `1px solid ${C.border}` }}>
         <button onClick={() => deleteRow(ri)} title={t('data.editor_delete_row')}
          style={{ background: 'transparent', border: 'none', color: C.red, cursor: 'pointer', padding: 2, display: 'flex' }}>
          <Trash2 size={12} />
         </button>
        </td>
        {columns.map(c => (
         <td key={c} style={{ padding: 0, borderBottom: `1px solid ${C.border}` }}>
          <input value={row[c] == null ? '' : String(row[c])}
           name={`cell-${ri}-${c}`} aria-label={c}
           onChange={e => setCell(ri, c, e.target.value)}
           style={{ width: '100%', minWidth: 90, background: 'transparent', border: 'none',
            padding: '6px 12px', color: C.text, fontSize: 11.5, fontFamily: MONO, lineHeight: 1.7,
            outline: 'none', boxSizing: 'border-box' }} />
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
function SourceDetail({ source, onUpdated, onDeleted, onBack, onDatasetCreated }:
 { source: DataSource; onUpdated: (s: DataSource) => void; onDeleted: () => void; onBack?: () => void
   onDatasetCreated?: (ds: DataSource) => void }
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
 <div data-tour="data.header" style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16 }}>
 {onBack && (
 <button onClick={onBack} aria-label={t('common.back')} style={{ background: 'transparent', border: 'none',
 color: C.muted, cursor: 'pointer', padding: 4, marginTop: 2 }}>
 <ArrowLeft size={16} aria-hidden="true" />
 </button>
 )}
                 {/* The icon sits in a tile rather than floating next to the title: it is
     the object's type marker, the way a database client shows a connection
     node, and the tile is what keeps it from reading as a stray glyph. */}
 <div style={{ width: 36, height: 36, borderRadius: 9, flexShrink: 0,
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 background: C.surface, border: `1px solid ${C.border}` }}>
 <SourceIcon type={source.source_type} size={18} />
 </div>
 <div style={{ flex: 1, minWidth: 0 }}>
 {editName ? (
 <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
 <input value={newName} onChange={e => setNewName(e.target.value)}
 name="source_name" aria-label={t('data.field_source_name')}
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
 letterSpacing: '-0.015em',
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {source.name}
 </h2>
 <button onClick={() => setEditName(true)}
 style={{ background: 'transparent', border: 'none', color: C.muted,
 cursor: 'pointer', padding: 2, opacity: 0.6, display: 'flex' }}>
 <Edit2 size={13} />
 </button>
 </div>
 )}
 {/* Filename and DSN are machine identifiers, not prose — monospace. */}
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 7, minWidth: 0 }}>
 <StatusBadge status={source.connection_status} />
 {source.original_filename && (
 <span style={{ color: C.muted, fontSize: 11.5, fontFamily: MONO,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {source.original_filename}
 </span>
 )}
 {source.sql_config && (
 <span style={{ color: C.muted, fontSize: 11.5, fontFamily: MONO,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {source.sql_config.host}:{source.sql_config.port}/{source.sql_config.database}
 </span>
 )}
 </div>
 </div>
 <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
 {isSql && (
 <button className="btn" onClick={testConn} disabled={testing}
 style={{ padding: '7px 14px', borderRadius: 8, background: C.blueDim,
 border: `1px solid ${alpha(C.blue, 40)}`, color: C.blue, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600 }}>
 {testing ? <Spinner size={12} /> : <Link2 size={12} />}
 {t('data.btn_test_connection')}
 </button>
 )}
 <button className="btn" onClick={doDelete} disabled={deletingId}
 aria-label={t('common.delete')} title={t('common.delete')}
 style={{ padding: '7px 12px', borderRadius: 8, background: C.redDim,
 border: `1px solid ${alpha(C.red, 30)}`, color: C.red, cursor: 'pointer', display: 'flex' }}>
 <Trash2 size={14} aria-hidden="true" />
 </button>
 </div>
 </div>

 {deleteErr && (
 <div style={{ ...errorBlock, margin: '0 0 8px', padding: '8px 12px', fontSize: 12,
 display: 'flex', alignItems: 'center', gap: 6 }}>
 <AlertTriangle size={12} aria-hidden="true" style={{ flexShrink: 0 }} /> {deleteErr}
 </div>
 )}

 {/* Stats strip — the object's properties, hairline-separated, figures in the
     code voice so size / rows / columns line up as a column of facts. */}
 <div data-tour="data.stats" style={{ display: 'flex', paddingBottom: 14 }}>
 {[
 { label: t('data.stat_size'), value: fmt(source.size_bytes) },
 { label: t('data.stat_rows'), value: source.row_count?.toLocaleString() ?? '—' },
 { label: t('data.stat_columns'), value: source.column_count?.toLocaleString() ?? '—' },
 { label: t('data.stat_type'), value: source.file_type || source.sql_config?.engine || '—' },
 ].map((s, i) => (
 <div key={s.label} style={{ paddingLeft: i === 0 ? 0 : 18, paddingRight: 18,
 borderLeft: i === 0 ? 'none' : `1px solid ${C.border}` }}>
 <div style={EYEBROW}>{s.label}</div>
 <div style={{ color: C.text, fontSize: 13.5, fontWeight: 600, fontFamily: MONO, marginTop: 3 }}>{s.value}</div>
 </div>
 ))}
 </div>

 {/* Test result. Both outcomes sit on `--surface` with a coloured left rule:
     `--danger` on its own tint is 4.1:1, which an outcome message cannot
     afford. Success is 4.8:1 on the tint and would have passed, but the two
     states share a shape so they read as the same control answering. */}
 {testResult && (
 <div style={{
 marginBottom: 12, padding: '9px 14px', borderRadius: 8,
 background: C.surface,
 border: `1px solid ${alpha(testResult.ok ? C.green : C.red, 35)}`,
 borderLeft: `3px solid ${testResult.ok ? C.green : C.red}`,
 color: testResult.ok ? C.green : C.red, fontSize: 12.5, lineHeight: 1.55,
 display: 'flex', alignItems: 'center', gap: 7,
 }}>
 {testResult.ok
 ? <CheckCircle2 size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
 : <XCircle size={14} aria-hidden="true" style={{ flexShrink: 0 }} />}
 {testResult.ok ? t('data.connection_successful') : `${t('data.connection_failed')}: ${testResult.error}`}
 </div>
 )}

 {/* Tabs */}
 <div data-tour="data.tabs" style={{ display: 'flex', gap: 2 }}>
 {tabs.map(t => (
 <button key={t.id} className="btn" onClick={() => setTab(t.id as any)}
 style={{
 padding: '9px 14px', border: 'none', cursor: 'pointer', fontSize: 12.5, fontWeight: 600,
 background: 'transparent', borderBottom: `2px solid ${tab === t.id ? C.green : 'transparent'}`,
 color: tab === t.id ? C.green : C.muted,
 display: 'flex', alignItems: 'center', gap: 6,
 }}>
 {t.id === 'analysis' && <BarChart2 size={12} />}
 {t.label}
 </button>
 ))}
 </div>
 </div>

 {/* Tab content */}
 <div data-tour="data.content" style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>

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
                 className="btn"
 style={{ padding: '4px 12px', borderRadius: 20, fontSize: 11.5, fontWeight: 600,
 fontFamily: MONO,
 cursor: 'pointer', border: `1px solid ${activeSheet === s ? alpha(C.green, 55) : C.border2}`,
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
 <div style={errorBlock}>{previewErr}</div>
 ) : preview ? (
 <>
 <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10 }}>
 <Eye size={13} color={C.muted} aria-hidden="true" />
 <span style={{ color: C.muted, fontSize: 12 }}>
 {t('data.showing')} <span style={{ color: C.text, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{preview.row_count}</span> {preview.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}{preview.truncated ? ` ${t('data.first_100')}` : ''}
 {preview.active_sheet ? ` — ${t('data.sheet_label')}: ${preview.active_sheet}` : ''}
 </span>
 <button className="btn"
 onClick={() => { if (!loadingPreview) loadPreview(activeSheet) }}
 disabled={loadingPreview}
 style={{ marginLeft: 'auto', background: 'transparent',
 border: `1px solid ${C.border}`, borderRadius: 7, padding: '5px 11px',
 color: C.muted, cursor: loadingPreview ? 'default' : 'pointer',
 display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600,
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
                 // The headline moves to `--text` with the amber left in the icon:
 // `--warning` at 14px is 3.2:1 on the light surface.
 <div style={{ textAlign: 'center', padding: '48px 0' }}>
 <AlertTriangle size={30} color={C.amber} style={{ marginBottom: 14 }} aria-hidden="true" />
 <p style={{ color: C.text, fontWeight: 700, fontSize: 14 }}>{t('data.connection_not_established')}</p>
 <p style={{ color: C.muted, fontSize: 13, marginTop: 4 }}>{t('data.test_connection_first')}</p>
 <button className="btn" onClick={testConn} disabled={testing}
 style={{ marginTop: 16, padding: '9px 20px', borderRadius: 8, background: C.green,
 border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer',
 display: 'inline-flex', alignItems: 'center', gap: 6 }}>
 {testing ? <Spinner size={14} /> : <Link2 size={14} />} {t('data.btn_test_connection')}
 </button>
 </div>
 ) : (
 <SqlEditorPanel source={source} onSaved={onUpdated} onDatasetCreated={onDatasetCreated} />
 )
 )}

 {/* Replace file tab */}
 {tab === 'connection' && !isSql && (
 <div>
 <p style={{ color: C.muted, fontSize: 13, marginBottom: 16 }}>
 {t('data.replace_file_hint')}
 </p>
 {replaceErr && (
 <div style={{ ...errorBlock, marginBottom: 12 }}>{replaceErr}</div>
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

                 // A segmented control: the chosen half is filled and outlined, the other
 // half is bare. `--info` as a label is 4.1:1 on white, so the selected
 // label rides `--text` and the tint plus the icon carry which one is on.
 const typeTabStyle = (active: boolean, color: string): React.CSSProperties => ({
 flex: 1, padding: '9px 12px', borderRadius: 8, cursor: 'pointer',
 background: active ? alpha(color, 12) : 'transparent',
 border: `1px solid ${active ? alpha(color, 50) : C.border}`,
 color: active ? C.text : C.muted,
 fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center',
 justifyContent: 'center', gap: 6,
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
 {err && <div style={errorBlock}>{err}</div>}
 {file ? (
 <div>
                 {/* The staged file, as a chip: name in the code voice because it is a
     filename, size beside it as secondary metadata. */}
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px',
 background: C.greenDim, border: `1px solid ${alpha(C.green, 35)}`, borderRadius: 9, marginBottom: 14 }}>
 <FileSpreadsheet size={15} color={C.green} aria-hidden="true" style={{ flexShrink: 0 }} />
 <span style={{ color: C.text, fontSize: 12.5, fontWeight: 600, fontFamily: MONO,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
 {/* The filename above keeps the code voice; its size does not — "28.9 KB"
     is a number and a unit, and the unit is a word. */}
 <span style={{ color: C.muted, fontSize: 11.5, flexShrink: 0,
 fontVariantNumeric: 'tabular-nums' }}>({fmt(file.size)})</span>
 <button onClick={() => setFile(null)} style={{ marginLeft: 'auto', background: 'transparent',
 border: 'none', color: C.muted, cursor: 'pointer', display: 'flex', flexShrink: 0 }}>
 <X size={13} />
 </button>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
 <div>
 <FieldLabel htmlFor="upload-source-name">
 {t('data.field_name_optional')}
 </FieldLabel>
 <Input id="upload-source-name" name="name" value={name} onChange={e => setName(e.target.value)}
 placeholder={file.name.replace(/\.[^.]+$/, '')}
 size="lg" tone="surface" border="strong" />
 </div>
 <div>
 <FieldLabel htmlFor="upload-source-description">
 {t('data.field_description_optional')}
 </FieldLabel>
 <Input id="upload-source-description" name="description" value={desc} onChange={e => setDesc(e.target.value)} placeholder={t('data.desc_example_ph')}
 size="lg" tone="surface" border="strong" />
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
 {err && <div style={errorBlock}>{err}</div>}
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
                 {/* The glyph sits in the same tile the selected source's icon gets, so the
     empty panel and the filled one share a silhouette. */}
 <div style={{ width: 64, height: 64, borderRadius: 16, marginBottom: 22,
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 background: C.surface, border: `1px solid ${C.border}` }}>
 <Layers size={28} color={C.dim} aria-hidden="true" />
 </div>
 <h2 style={{ color: C.text, fontSize: 18, fontWeight: 700, margin: '0 0 8px',
 letterSpacing: '-0.015em' }}>{t('data.no_source_selected')}</h2>
 <p style={{ color: C.muted, fontSize: 13.5, margin: '0 0 28px', maxWidth: 340, lineHeight: 1.6 }}>
 {t('data.no_source_selected_hint')}
 </p>
 <button className="btn" onClick={onCreate}
 style={{ padding: '11px 22px', borderRadius: 10, background: C.greenDim,
 border: `1px solid ${alpha(C.green, 45)}`, color: C.green, fontWeight: 700, cursor: 'pointer',
 display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5 }}>
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
 <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 52px)',
 margin: '-24px', background: C.bg, color: C.text, overflow: 'hidden' }}>

 {/* Same nav entry as /quick-start — the two routes are tabs of each other. */}
 <DataTabs style={{ background: C.surface, padding: '0 12px', flexShrink: 0 }} />

 <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>

 {/* ── Left sidebar ─────────────────────────────────────────────────── */}
 <div style={{ width: 280, flexShrink: 0, borderRight: `1px solid ${C.border}`,
 display: 'flex', flexDirection: 'column', background: C.surface }}>

 {/* Sidebar header */}
 <div style={{ padding: '20px 16px 14px', borderBottom: `1px solid ${C.border}` }}>
 <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
 {/* No page title here: the tab strip above already names this view, and
     repeating it under the top bar's own title said the same thing three
     times in the top 90px of the screen. */}
                 {/* "5 FUENTES" is one caption, so it is set in one typeface. The
     figure used to be monospace while the noun beside it was not, and
     two families inside three characters reads as a rendering fault
     rather than as emphasis. Weight carries the figure instead. */}
 <p style={{ margin: 0, ...EYEBROW, display: 'flex', alignItems: 'baseline', gap: 5 }}>
 <span style={{ fontSize: 12, fontWeight: 700, color: C.text, letterSpacing: 0,
 fontVariantNumeric: 'tabular-nums' }}>{sources.length}</span>
 {sources.length !== 1 ? t('data.source_plural') : t('data.source_singular')}
 </p>
 <button className="btn" onClick={load} title={t('data.refresh_title')} aria-label={t('data.refresh_title')}
 style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer', padding: 4, display: 'flex' }}>
 <RefreshCw size={14} aria-hidden="true" />
 </button>
 </div>
 {/* The magnifier is what makes a bare rounded box read as a filter rather
     than as one more text field; the input keeps its own tour anchor. */}
 <div style={{ position: 'relative' }}>
 <Search size={13} aria-hidden="true" style={{ position: 'absolute', left: 10,
 top: '50%', transform: 'translateY(-50%)', color: C.dim, pointerEvents: 'none' }} />
 <input
 data-tour="data.search"
 type="search"
 name="source_search"
 aria-label={t('data.search_sources_ph')}
 value={search}
 onChange={e => setSearch(e.target.value)}
 placeholder={t('data.search_sources_ph')}
 className="form-input"
 style={{ width: '100%', background: C.card, border: `1px solid ${C.border}`,
 borderRadius: 8, padding: '8px 12px 8px 30px', color: C.text, fontSize: 12.5,
 outline: 'none', boxSizing: 'border-box' }}
 />
 </div>
 </div>

 {/* New item button */}
 <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.border}` }}>
 <button
 data-tour="data.new"
 className="btn"
 onClick={() => { setCreating(creating ? null : 'new'); setSelected(null) }}
 style={{
 width: '100%', padding: '8px 12px', borderRadius: 8,
 background: creating ? C.greenDim : 'transparent',
 border: `1px solid ${creating ? alpha(C.green, 55) : C.border}`,
 color: creating ? C.green : C.muted,
 cursor: 'pointer', display: 'flex', alignItems: 'center',
 justifyContent: 'center', gap: 6,
 fontSize: 12, fontWeight: 600,
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
 <div style={{ ...errorBlock, margin: 12 }}>{loadErr}</div>
 ) : filtered.length === 0 ? (
 <div style={{ padding: '28px 16px', textAlign: 'center', color: C.muted, fontSize: 12.5 }}>
 {search ? t('data.no_matches') : t('data.no_sources_yet')}
 </div>
 ) : (
 filtered.map((src, idx) => {
 const isActive = selected?.id === src.id && !creating
 return (
 /* One connection entry: type icon, name, quiet status, then the host or
    the file size as secondary metadata — the shape a database client's
    object browser uses. */
 <button
 key={src.id}
 /* First row only: a tour anchor has to resolve to a single element. */
 data-tour={idx === 0 ? 'data.item' : undefined}
 onClick={() => { setSelected(src); setCreating(null) }}
 onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = C.card }}
 onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
 style={{
 width: '100%', textAlign: 'left', padding: '11px 14px 11px 12px',
 background: isActive ? C.greenDim : 'transparent',
 border: 'none', borderLeft: `3px solid ${isActive ? C.green : 'transparent'}`,
 cursor: 'pointer',
 transition: `background var(--dur-1) var(--ease-out), border-color var(--dur-1) var(--ease-out)`,
 borderBottom: `1px solid ${C.border}`,
 }}
 >
 <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
 <SourceIcon type={src.source_type} size={15} />
 <span style={{ flex: 1, minWidth: 0, color: isActive ? C.green : C.text,
 fontSize: 12.5, fontWeight: 600, overflow: 'hidden',
 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {src.name}
 </span>
 <StatusBadge status={src.connection_status} />
 </div>
 {src.description && (
 <p style={{ margin: '5px 0 0 24px', color: C.muted, fontSize: 11, lineHeight: 1.45,
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
 {src.description}
 </p>
 )}
 {/* The whole line used to be monospace, which put "123 filas" and
     "28.9 KB" — a number followed by a WORD — into the code voice. A
     unit is prose, and at 10.5px monospace it read cramped and
     technical for something that is just a caption. Only the engine
     and host stay in code voice, because those are identifiers you
     might have to copy; the rest is the app's own type with tabular
     figures, so the digits still line up down the column. */}
 <div style={{ margin: '5px 0 0 24px', display: 'flex', gap: 9, alignItems: 'baseline',
 fontSize: 11, color: C.muted, minWidth: 0, fontVariantNumeric: 'tabular-nums' }}>
 <span style={{
 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
 ...(src.source_type === 'sql' ? { fontFamily: MONO, fontSize: 10.5 } : null),
 }}>
 {src.source_type === 'sql'
 ? `${src.sql_config?.engine} · ${src.sql_config?.host}`
 : fmt(src.size_bytes)}
 </span>
 {/* `? :`, not `&&`: a row_count of 0 makes `count && …` evaluate to the
     NUMBER 0, which React happily renders as a bare "0" next to the file
     size. An empty file should show nothing, not a stray zero. */}
 {src.row_count ? (
 <span style={{ flexShrink: 0 }}>
 {src.row_count.toLocaleString()} {src.row_count === 1 ? t('data.rows_singular') : t('data.rows_plural')}
 </span>
 ) : null}
 </div>
 </button>
 )
 })
 )}
 </div>
 </div>

 {/* ── Right panel ──────────────────────────────────────────────────── */}
 <div data-tour="data.panel" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
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
 onDatasetCreated={ds => setSources(prev => [ds, ...prev])}
 />
 ) : (
 <EmptyRight onCreate={() => { setCreating('new'); setSelected(null) }} />
 )}
 </div>
 </div>
 </div>
 )
}

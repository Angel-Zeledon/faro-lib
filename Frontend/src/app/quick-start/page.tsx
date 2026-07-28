'use client'
import { Suspense, useState, useRef, useCallback, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
 createSession, uploadDataset, attachDataset, inspectSession,
 chooseColumnsCanonical, setFeatures, setModels, setValidationConfig,
 setBusinessConfig, startTraining, getJob,
 startDemoQuickstart, listDatasets, getSessionSummaries, getColumnsConfig,
} from '@/lib/api'
import type { TrainingFamily } from '@/lib/api'
import {
  DEFAULT_HOLDING_COST_PCT, DEFAULT_LEAD_TIME_DAYS, DEFAULT_SERVICE_LEVEL,
} from '@/lib/inventoryDefaults'
import { validateSalesCsv } from '@/lib/csvCheck'
import type { CsvIssueGroup } from '@/lib/csvCheck'
import CsvIssueReport, { CsvTemplateButton } from '@/components/ui/CsvIssueReport'
import DataIssuesPanel from '@/components/ui/DataIssuesPanel'
import type {
 InspectionResult, CanonicalMapping, DatasetMeta, SessionSummary,
} from '@/lib/types'
import HelpTip from '@/components/ui/HelpTip'
import DataTabs from '@/components/layout/DataTabs'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePlanning } from '@/contexts/PlanningContext'

// The worker reports data problems as a stable code (see runner.py's
// TrainingDataError) so the user reads an actionable sentence instead of a raw
// Python error — "El entrenamiento falló: 'model'" was a real thing users saw.
// Anything unrecognized is shown as-is: hiding an unexpected error is worse.
function trainingErrorText(raw: string | null | undefined, t: (k: string) => string): string {
 if (!raw) return t('qs.err_unknown')
 const key = `errors.training.${raw.trim()}`
 const localized = t(key)
 return localized === key ? raw : localized
}

// ── Step indicator ─────────────────────────────────────────────────────────────
function StepBubble({ n, label, active, done }: { n: number; label: string; active: boolean; done: boolean }) {
 return (
 <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
 <div style={{
 width: 36, height: 36, borderRadius: '50%',
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 background: done ? '#22c55e' : active ? 'var(--accent)' : 'var(--surface-2, #f1f5f9)',
 border: `2px solid ${done ? '#22c55e' : active ? 'var(--accent)' : 'var(--border)'}`,
 color: done || active ? '#fff' : 'var(--dim)',
 fontWeight: 700, fontSize: 15,
 transition: 'all 0.25s',
 }}>
 {done ? '✓' : n}
 </div>
 <span style={{
 fontSize: 12, fontWeight: active ? 600 : 400,
 color: active ? 'var(--accent)' : done ? '#22c55e' : 'var(--dim)',
 whiteSpace: 'nowrap',
 }}>
 {label}
 </span>
 </div>
 )
}

function StepBar({ step }: { step: number }) {
 const { t } = useLanguage()
 const steps = [
 { n: 1, label: t('qs.step1') },
 { n: 2, label: t('qs.step2') },
 { n: 3, label: t('qs.step3') },
 ]
 return (
 <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', gap: 0, marginBottom: 40 }}>
 {steps.map((s, i) => (
 <div key={s.n} style={{ display: 'flex', alignItems: 'center' }}>
 <StepBubble n={s.n} label={s.label} active={step === s.n} done={step > s.n} />
 {i < steps.length - 1 && (
 <div style={{
 width: 80, height: 2, margin: '0 8px', marginBottom: 24,
 background: step > s.n ? '#22c55e44' : 'var(--border)',
 transition: 'all 0.25s',
 }} />
 )}
 </div>
 ))}
 </div>
 )
}

// ── CSV example ────────────────────────────────────────────────────────────────
function CsvExample() {
 const { t } = useLanguage()
 // Header names mirror the downloadable template (lib/csvCheck.ts) — the
 // canonical aliases the backend profiler auto-detects.
 const rows = [
 { sku: 'SKU-001', date: '2026-01-01', demand: '32' },
 { sku: 'SKU-001', date: '2026-01-02', demand: '28' },
 { sku: 'SKU-002', date: '2026-01-01', demand: '15' },
 ]
 return (
 <div style={{ marginTop: 20, overflowX: 'auto' }}>
 <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 8 }}>
 {t('qs.csv_example')}
 </p>
 <table style={{
 borderCollapse: 'collapse', fontSize: 12, width: '100%',
 border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden',
 }}>
 <thead>
 <tr style={{ background: 'var(--surface-2, #f8fafc)' }}>
 {['sku', 'date', 'demand'].map(h => (
 <th key={h} style={{
 padding: '6px 12px', textAlign: 'left',
 borderBottom: '1px solid var(--border)',
 color: 'var(--dim)', fontWeight: 600,
 }}>
 {h}
 </th>
 ))}
 </tr>
 </thead>
 <tbody>
 {rows.map((r, i) => (
 <tr key={i} style={{ borderBottom: i < rows.length - 1 ? '1px solid var(--border)' : 'none' }}>
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.sku}</td>
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.date}</td>
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.demand}</td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )
}

// ── Drop zone ──────────────────────────────────────────────────────────────────
function DropZone({ onFile, busy }: { onFile: (f: File) => void; busy: boolean }) {
 const { t } = useLanguage()
 const [dragging, setDragging] = useState(false)
 const inputRef = useRef<HTMLInputElement>(null)

 const handleDrop = useCallback((e: React.DragEvent) => {
 e.preventDefault()
 setDragging(false)
 const file = e.dataTransfer.files[0]
 if (file) onFile(file)
 }, [onFile])

 const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
 const file = e.target.files?.[0]
 if (file) onFile(file)
 }, [onFile])

 return (
 <div
 onDragOver={e => { e.preventDefault(); setDragging(true) }}
 onDragLeave={() => setDragging(false)}
 onDrop={handleDrop}
 onClick={() => !busy && inputRef.current?.click()}
 style={{
 border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
 borderRadius: 12,
 padding: '48px 32px',
 textAlign: 'center',
 cursor: busy ? 'not-allowed' : 'pointer',
 background: dragging ? 'var(--accent-dim, #eef2ff)' : 'var(--surface-2, #f8fafc)',
 transition: 'all 0.2s',
 opacity: busy ? 0.7 : 1,
 }}
 >
 <input
 ref={inputRef}
 type="file"
 accept=".csv,.xlsx,.xls"
 style={{ display: 'none' }}
 onChange={handleChange}
 disabled={busy}
 />
 <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'center' }}><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{color:'var(--dim)'}}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg></div>
 <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
 {busy ? t('qs.uploading') : t('qs.dropzone')}
 </div>
 <div style={{ fontSize: 13, color: 'var(--dim)' }}>
 {t('qs.formats')}
 </div>
 </div>
 )
}


// ── Existing-dataset picker (step 1, reuse tab) ────────────────────────────────
function formatBytes(bytes: number): string {
 if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
 return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

// ── Previous-session picker (step 1, clone tab) ────────────────────────────────
// Cloning goes one step further than reusing a dataset: it also carries over the
// column mapping, so a repeat run needs no confirmation step at all.
function SessionClonePicker({ sessions, onPick, busy }: {
 sessions: SessionSummary[]; onPick: (s: SessionSummary) => void; busy: boolean
}) {
 const { t } = useLanguage()
 if (sessions.length === 0) {
 return <p style={{ fontSize: 13, color: 'var(--dim)', margin: 0 }}>{t('qs.clone_empty')}</p>
 }
 return (
 <div>
 <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 12px' }}>
 {t('qs.clone_desc')}
 </p>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 320, overflowY: 'auto' }}>
 {sessions.map(s => (
 <div key={s.session_id} style={{
 display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
 padding: '10px 14px', border: '1px solid var(--border)', borderRadius: 10,
 background: 'var(--surface-2, #f8fafc)',
 }}>
 <div style={{ minWidth: 0 }}>
 <div style={{
  fontSize: 13, fontWeight: 600, color: 'var(--text)',
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
 }}>
  {s.name}
 </div>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
  {new Date(s.created_at).toLocaleDateString()}
  {s.dataset_filename && <>{' · '}{s.dataset_filename}</>}
  {s.sku_count != null && <>{' · '}{s.sku_count} SKUs</>}
 </div>
 </div>
 <button
 type="button"
 onClick={() => onPick(s)}
 disabled={busy}
 style={{
  padding: '7px 18px', borderRadius: 8, fontSize: 13, fontWeight: 700,
  border: '1px solid var(--accent)', background: 'transparent',
  color: 'var(--accent)', flexShrink: 0,
  cursor: busy ? 'not-allowed' : 'pointer',
  opacity: busy ? 0.6 : 1,
 }}
 >
 {t('qs.clone_use_btn')}
 </button>
 </div>
 ))}
 </div>
 </div>
 )
}


function DatasetPicker({ datasets, onPick, busy }: {
 datasets: DatasetMeta[]; onPick: (id: string) => void; busy: boolean
}) {
 const { t } = useLanguage()
 if (datasets.length === 0) {
 return <p style={{ fontSize: 13, color: 'var(--dim)', margin: 0 }}>{t('qs.reuse_empty')}</p>
 }
 return (
 <div>
 <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 12px' }}>
 {t('qs.reuse_desc')}
 </p>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 320, overflowY: 'auto' }}>
 {datasets.map(d => (
 <div key={d.id} style={{
 display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
 padding: '10px 14px', border: '1px solid var(--border)', borderRadius: 10,
 background: 'var(--surface-2, #f8fafc)',
 }}>
 <div style={{ minWidth: 0 }}>
 <div style={{
  fontSize: 13, fontWeight: 600, color: 'var(--text)',
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
 }}>
  {d.original_filename}
 </div>
 <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
  {t('qs.reuse_uploaded_prefix')} {new Date(d.uploaded_at).toLocaleDateString()}
  {' · '}{formatBytes(d.size_bytes)}
  {d.row_count != null && <>{' · '}{d.row_count.toLocaleString()} {t('qs.reuse_rows')}</>}
 </div>
 </div>
 <button
 type="button"
 onClick={() => onPick(d.id)}
 disabled={busy}
 style={{
  padding: '7px 18px', borderRadius: 8, fontSize: 13, fontWeight: 700,
  border: '1px solid var(--accent)', background: 'transparent',
  color: 'var(--accent)', flexShrink: 0,
  cursor: busy ? 'not-allowed' : 'pointer',
  opacity: busy ? 0.6 : 1,
 }}
 >
 {t('qs.reuse_use_btn')}
 </button>
 </div>
 ))}
 </div>
 </div>
 )
}

function TrainingLoader({ message, pct, multiPeriod }: { message: string; pct: number | null; multiPeriod: boolean }) {
 const { t } = useLanguage()
 return (
 <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 24, paddingTop: 20 }}>
 {/* Spinner */}
 <div style={{ position: 'relative', width: 72, height: 72 }}>
 <div style={{
 position: 'absolute', inset: 0,
 border: '4px solid var(--border)',
 borderTopColor: 'var(--accent)',
 borderRadius: '50%',
 animation: 'qs-spin 0.9s linear infinite',
 }} />
 {pct != null && (
 <div style={{
 position: 'absolute', inset: 0, display: 'flex',
 alignItems: 'center', justifyContent: 'center',
 fontSize: 16, fontWeight: 700, color: 'var(--text)',
 }}>
 {pct}%
 </div>
 )}
 </div>
 <div
 key={message}
 style={{
 fontSize: 16, fontWeight: 500, color: 'var(--text)',
 textAlign: 'center', minHeight: 28,
 animation: 'qs-fade 0.5s ease-out',
 }}
 >
 {message || t('qs.msg_analyzing')}
 </div>
 {/* Real progress bar (driven by the worker's emitted percent) */}
 {pct != null && (
 <div style={{ width: '100%', maxWidth: 340, height: 6, borderRadius: 6, background: 'var(--border)', overflow: 'hidden' }}>
 <div style={{ width: `${pct}%`, height: '100%', background: 'var(--accent)', transition: 'width 0.5s ease-out' }} />
 </div>
 )}
 <div style={{ fontSize: 13, color: 'var(--dim)', textAlign: 'center', maxWidth: 340 }}>
 {t('qs.may_take')}
 <br />{t('qs.dont_close')}
 </div>
 {multiPeriod && (
 <div style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'center', maxWidth: 340, opacity: 0.85 }}>
 {t('qs.family_note')}
 </div>
 )}
 </div>
 )
}

// ── Canonical field definitions ────────────────────────────────────────────────
const CANONICAL_FIELDS = [
 { name: 'sku',           label: 'SKU / Producto',     required: true  },
 { name: 'date',          label: 'Fecha',              required: true  },
 { name: 'demand',        label: 'Demanda',            required: true  },
 { name: 'store',         label: 'Tienda',             required: false, default: 'Tienda única' },
 { name: 'region',        label: 'Región',             required: false, default: 'Sin región' },
 { name: 'inventory',     label: 'Inventario',         required: false, default: '0' },
 // The default shown here is what the engine actually broadcasts into an
 // unmapped lead_time column. It said 7 while the DB, this wizard's business
 // config and /inventory all said 15 — the mapping step was promising the user
 // a number no other screen would honour.
 { name: 'lead_time',     label: 'Lead Time (días)',   required: false, default: String(DEFAULT_LEAD_TIME_DAYS) },
 { name: 'price',         label: 'Precio',             required: false, default: 'Desconocido' },
 { name: 'cost',          label: 'Costo',              required: false, default: 'Desconocido' },
 { name: 'regular_price', label: 'Precio Regular',     required: false, default: 'Desconocido' },
 { name: 'promo_price',   label: 'Precio Promocional', required: false, default: '= Precio Regular' },
 { name: 'promo',         label: 'Promoción',          required: false, default: 'false' },
 { name: 'promo_type',    label: 'Tipo de Promoción',  required: false, default: 'Sin promoción' },
 { name: 'discount',      label: 'Descuento',          required: false, default: '0%' },
] as const

// ── Plan settings (name + horizon + granularity, step 1) ───────────────────────
type Granularity = 'auto' | 'daily' | 'weekly' | 'monthly'

// Horizon presets in calendar days; the backend converts to per-grain steps.
const HORIZON_PRESETS = [
 { days: 28,  labelKey: 'qs.plan_horizon_4w' },
 { days: 56,  labelKey: 'qs.plan_horizon_8w' },
 { days: 180, labelKey: 'qs.plan_horizon_6m' },
] as const

const GRANULARITY_OPTIONS: { value: Granularity; labelKey: string }[] = [
 { value: 'auto',    labelKey: 'qs.plan_granularity_auto' },
 { value: 'daily',   labelKey: 'qs.plan_granularity_daily' },
 { value: 'weekly',  labelKey: 'qs.plan_granularity_weekly' },
 { value: 'monthly', labelKey: 'qs.plan_granularity_monthly' },
]

function Chip({ label, selected, disabled, onClick }: {
 label: string; selected: boolean; disabled: boolean; onClick: () => void
}) {
 return (
 <button
 type="button"
 onClick={onClick}
 disabled={disabled}
 aria-pressed={selected}
 style={{
 padding: '6px 14px', borderRadius: 999, fontSize: 13,
 fontWeight: selected ? 700 : 400,
 border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
 background: selected ? 'var(--accent-dim, #eef2ff)' : 'var(--surface)',
 color: selected ? 'var(--accent)' : 'var(--text)',
 cursor: disabled ? 'not-allowed' : 'pointer',
 opacity: disabled ? 0.6 : 1,
 transition: 'all 0.15s',
 }}
 >
 {label}
 </button>
 )
}

function PlanSettings({ name, onName, horizonDays, onHorizonDays, granularity, onGranularity, busy }: {
 name: string; onName: (v: string) => void
 horizonDays: number; onHorizonDays: (v: number) => void
 granularity: Granularity; onGranularity: (v: Granularity) => void
 busy: boolean
}) {
 const { t } = useLanguage()
 const labelStyle: React.CSSProperties = {
 fontSize: 13, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6,
 }
 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 20 }}>
 <div>
 <label htmlFor="qs-session-name" style={labelStyle}>
 {t('qs.plan_name_label')}
 </label>
 <input
 id="qs-session-name"
 type="text"
 value={name}
 disabled={busy}
 maxLength={200}
 placeholder={t('qs.plan_name_placeholder')}
 onChange={e => onName(e.target.value)}
 style={{
 width: '100%', padding: '9px 12px', borderRadius: 8,
 border: '1px solid var(--border)', background: 'var(--surface)',
 color: 'var(--text)', fontSize: 13,
 }}
 />
 </div>
 <div>
 <span style={labelStyle}>{t('qs.plan_horizon_label')}</span>
 <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
 {HORIZON_PRESETS.map(p => (
 <Chip
  key={p.days}
  label={t(p.labelKey)}
  selected={horizonDays === p.days}
  disabled={busy}
  onClick={() => onHorizonDays(p.days)}
 />
 ))}
 </div>
 </div>
 <div>
 <span style={labelStyle}>{t('qs.plan_granularity_label')}</span>
 <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
 {GRANULARITY_OPTIONS.map(o => (
 <Chip
  key={o.value}
  label={t(o.labelKey)}
  selected={granularity === o.value}
  disabled={busy}
  onClick={() => onGranularity(o.value)}
 />
 ))}
 </div>
 </div>
 </div>
 )
}

// ── Quick-start page ───────────────────────────────────────────────────────────
function QuickStartPageContent() {
 const router = useRouter()
 const searchParams = useSearchParams()
 const { t } = useLanguage()
 // The wizard runs inside the AppShell, so the planning context that resolves
 // the active session was loaded BEFORE this training existed — see the
 // redirect in pollFamily for why it has to be refreshed there.
 const planningCtx = usePlanning()

 const [step, setStep] = useState(1)
 const [busy, setBusy] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [fileName, setFileName] = useState<string | null>(null)

 // Session / dataset IDs
 const [sessionId, setSessionId] = useState<string | null>(null)

 // Uploaded/selected dataset id. Kept across retries: the file already lives
 // on the server, so a failed run must never force a re-upload.
 const [datasetId, setDatasetId] = useState<string | null>(null)

 // Previously uploaded datasets (reuse tab). Loaded once on mount; the tab
 // only renders when at least one exists.
 const [datasets, setDatasets] = useState<DatasetMeta[]>([])
 // Completed sessions available to clone (dataset + column mapping reused).
 const [clonableSessions, setClonableSessions] = useState<SessionSummary[]>([])
 const [source, setSource] = useState<'upload' | 'existing' | 'clone'>('upload')

 // True once training launched for the current session — decides whether a
 // retry can keep the session (config-stage failure: still configurable) or
 // needs a fresh one (QUEUED/RUNNING/FAILED can't re-enter the wizard).
 const trainLaunchedRef = useRef(false)

 // Shown on the mapping step after a retry that skipped the re-upload.
 const [retryNote, setRetryNote] = useState(false)

 // Set when the mapping came from the previous session instead of from the
 // profiler's suggestions. `missing` non-empty means it could NOT be reused —
 // the user is told which columns disappeared rather than left to spot it.
 const [reusedMapping, setReusedMapping] =
  useState<{ from: string; missing: string[] } | null>(null)

 // Plan settings (step 1): optional session name, forecast horizon in calendar
 // days and planning grain. The backend derives each grain's horizon from the
 // days value — no hardcoded forecast_cfg horizon is posted anymore.
 const [sessionName, setSessionName] = useState('')
 const [horizonDays, setHorizonDays] = useState<number>(28)
 const [granularity, setGranularity] = useState<Granularity>('auto')

 // Inspection result
 const [inspection, setInspection] = useState<InspectionResult | null>(null)

 // Column mapping (14-field canonical schema)
 const [mapping, setMapping] = useState<Record<string, string | null>>(
   Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
 )

 // Training progress
 const [trainMsg, setTrainMsg] = useState('')
 const [trainPct, setTrainPct] = useState<number | null>(null)
 // True when the launch fanned out into >1 planning period (daily + weekly/…):
 // drives the "we're preparing several views, you'll land as soon as the daily
 // one is ready" note so the aggregated bar isn't mistaken for a stall.
 const [multiPeriod, setMultiPeriod] = useState(false)
 const msgIdxRef = useRef(0)

 // Non-fatal pre-upload observations (row counts, ignored rows, short history)
 const [csvWarnings, setCsvWarnings] = useState<string[]>([])

 // Per-row findings of the pre-upload validator, grouped by problem kind.
 // Kept whether or not the file was rejected: a file that passes can still
 // carry rows the backend will silently skip, and the user should see them.
 const [csvIssues, setCsvIssues] = useState<CsvIssueGroup[]>([])

 // ── Demo: seed everything server-side and jump straight to training ─────────
 const handleDemo = async () => {
 setError(null)
 setBusy(true)
 setStep(3)
 try {
 const demo = await startDemoQuickstart({
 name: sessionName.trim() || undefined,
 user_horizon_days: horizonDays,
 user_granularity: granularity,
 })
 setSessionId(demo.session_id)
 trainLaunchedRef.current = true
 await pollFamily(demo.job_id, demo.family)
 } catch (e: unknown) {
 setError(e instanceof Error ? e.message : t('qs.err_demo'))
 setBusy(false)
 }
 }

 // Arriving via /quick-start?demo=1 (e.g. from the landing page's "empezar
 // gratis" CTA, carried through signup + login) auto-starts the demo instead
 // of waiting on a click — the whole point of that path is zero extra taps
 // between "create account" and "see the semáforo working".
 const autoDemoRanRef = useRef(false)
 useEffect(() => {
 if (autoDemoRanRef.current) return
 if (searchParams.get('demo') !== '1') return
 autoDemoRanRef.current = true
 handleDemo()
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [searchParams])

 // Load previously uploaded datasets once — a failure just keeps the reuse
 // tab hidden, the upload path is unaffected.
 useEffect(() => {
 listDatasets(0, 50)
 .then(r => setDatasets(r.items ?? []))
 .catch(() => { /* reuse tab simply stays hidden */ })
 // Only completed sessions that still have their dataset can be cloned.
 getSessionSummaries(0, 50)
 .then(r => setClonableSessions(
 (r.items ?? []).filter(s => s.status === 'COMPLETED' && s.dataset_id),
 ))
 .catch(() => { /* clone tab simply stays hidden */ })
 }, [])

 // The column mapping the user last confirmed, from the newest COMPLETED
 // session that still has one. Returns null when this is their first upload,
 // when the older session predates canonical mapping, or on any read failure —
 // in every one of those cases the wizard simply behaves as it always did.
 const lastConfirmedMapping = async (): Promise<
  { name: string; mapping: Record<string, string | null> } | null
 > => {
  const candidates = [...clonableSessions].sort(
   (a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at),
  )
  for (const s of candidates.slice(0, 3)) {
   try {
    const cfg = await getColumnsConfig(s.session_id)
    const mapped = (cfg?.canonical_mapping ?? null) as Record<string, string | null> | null
    if (mapped && Object.values(mapped).some(Boolean)) {
     return { name: s.name, mapping: mapped }
    }
   } catch { /* try the next one */ }
  }
  return null
 }

 // ── Step 1: session over an already-stored dataset ──────────────────────────
 // Create a fresh session, attach the dataset, inspect it and enter the
 // column-mapping step. Shared by the upload path (right after the file
 // upload), the "use an existing dataset" tab and retry-after-failure.
 // A fresh session is created every time: POST /sessions/{id}/dataset attaches
 // in any state, but once a training launch happened the session sits in
 // QUEUED/RUNNING/FAILED where /train rejects (409) until the state machine
 // reaches MODELS_CONFIGURED again — a clean DRAFT session avoids all of that.
 const startFromDataset = async (dsId: string, keepMapping = false) => {
 setError(null)
 setBusy(true)
 trainLaunchedRef.current = false
 try {
 const session = await createSession(sessionName.trim() || undefined)
 setSessionId(session.session_id)

 await attachDataset(session.session_id, dsId)

 const insp = await inspectSession(session.session_id)
 setInspection(insp)
 setDatasetId(dsId)

 if (!keepMapping) {
  // The monthly upload is last month's file with new rows, so the mapping
  // the user already confirmed almost always still fits. Redoing the whole
  // wizard every month is a recurring cost that buys nothing, and it is
  // what makes people stop updating after the second month.
  //
  // Reused only when EVERY column it names is still present: otherwise the
  // run would train on a silently different set of fields, which is worse
  // than asking. A partial match falls back to the suggestions and reports
  // which columns went missing.
  const available = new Set(insp.profile.columns.map(c => c.name))
  const previous = await lastConfirmedMapping()
  const named = previous
   ? (CANONICAL_FIELDS.map(f => previous.mapping[f.name]).filter(Boolean) as string[])
   : []
  const missing = named.filter(col => !available.has(col))

  if (previous && named.length > 0 && missing.length === 0) {
   setMapping(Object.fromEntries(
    CANONICAL_FIELDS.map(f => [f.name, previous.mapping[f.name] ?? null]),
   ))
   setReusedMapping({ from: previous.name, missing: [] })
  } else {
   const suggestions: CanonicalMapping = insp.canonical_suggestions ?? {}
   const next: Record<string, string | null> =
    Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
   for (const field of CANONICAL_FIELDS) {
    const sug = suggestions[field.name]
    if (sug?.top && sug.confidence >= 0.7) next[field.name] = sug.top
   }
   setMapping(next)
   setReusedMapping(
    previous && missing.length > 0 ? { from: previous.name, missing } : null,
   )
  }
 }

 setStep(2)
 } catch (e: unknown) {
 setError(e instanceof Error ? e.message : t('qs.reuse_err_attach'))
 setStep(1)
 } finally {
 setBusy(false)
 }
 }

 // Reuse tab: pick a previously uploaded dataset and jump to column mapping.
 const handlePickExisting = (dsId: string) => {
 if (busy) return
 setFileName(null)
 setCsvWarnings([])
 setCsvIssues([])
 setRetryNote(false)
 void startFromDataset(dsId)
 }

 // Clone tab: same dataset AND the same column mapping as a previous run, so
 // re-forecasting the same file at a different horizon/grain needs no
 // re-mapping. Falls back to the normal mapping step if the old configuration
 // can't be read or doesn't fit this dataset.
 const handleCloneSession = async (src: SessionSummary) => {
 if (busy || !src.dataset_id) return
 setFileName(null)
 setCsvWarnings([])
 setCsvIssues([])
 setRetryNote(false)
 // Cloning states its own reuse in the tab copy; the upload banner would be
 // a second, contradictory explanation of where the mapping came from.
 setReusedMapping(null)
 setError(null)
 setBusy(true)
 trainLaunchedRef.current = false
 try {
 const previous = await getColumnsConfig(src.session_id)
 const previousMapping =
 (previous?.canonical_mapping ?? null) as Record<string, string | null> | null

 // Through i18n: this becomes the run's persisted NAME, so an English user
 // should not be left with "… (copia)" in their history forever. Same reason
 // as the dataset editor's copy suffix.
 const session = await createSession(
 sessionName.trim() || t('qs.clone_copy_suffix', { name: src.name }),
 )
 setSessionId(session.session_id)
 await attachDataset(session.session_id, src.dataset_id)
 const insp = await inspectSession(session.session_id)
 setInspection(insp)
 setDatasetId(src.dataset_id)

 // Only keep columns the cloned dataset actually still has.
 const available = new Set(insp.profile.columns.map(c => c.name))
 const next: Record<string, string | null> =
 Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
 let reused = 0
 for (const field of CANONICAL_FIELDS) {
 const col = previousMapping?.[field.name]
 if (col && available.has(col)) { next[field.name] = col; reused++ }
 }
 if (reused === 0) {
 // Nothing survived — behave exactly like a fresh reuse.
 const suggestions: CanonicalMapping = insp.canonical_suggestions ?? {}
 for (const field of CANONICAL_FIELDS) {
  const sug = suggestions[field.name]
  if (sug?.top && sug.confidence >= 0.7) next[field.name] = sug.top
 }
 }
 setMapping(next)
 setStep(2)
 } catch (e: unknown) {
 setError(e instanceof Error ? e.message : t('qs.clone_err'))
 setStep(1)
 } finally {
 setBusy(false)
 }
 }

 // ── Step 1: Upload ───────────────────────────────────────────────────────────
 const handleFile = async (file: File) => {
 setError(null)
 setCsvWarnings([])
 setCsvIssues([])
 setBusy(true)
 setFileName(file.name)

 // Pre-upload validation for CSVs: report broken rows with their line number
 // BEFORE uploading (Excel files are profiled server-side instead).
 if (file.name.toLowerCase().endsWith('.csv')) {
 try {
 const check = validateSalesCsv(await file.text())
 setCsvIssues(check.issueGroups)
 if (!check.ok) {
  // The grouped report carries the row-level detail; `error` keeps the
  // one-line summary for the cases with no per-row issues at all
  // (empty file, single-column file — issueGroups is empty there).
  if (check.issueGroups.length === 0) setError(check.errors.join('\n'))
  setFileName(null)
  setBusy(false)
  return
 }
 if (check.warnings.length) setCsvWarnings(check.warnings)
 } catch { /* unreadable as text — let the backend decide */ }
 }

 try {
 // Upload the file, then hand off to the shared session/attach/inspect
 // path (it owns busy/error handling from here on).
 const fd = new FormData()
 fd.append('file', file)
 const dataset = await uploadDataset(fd)

 // Keep the reuse tab in sync without a refetch
 setDatasets(prev => [dataset, ...prev.filter(d => d.id !== dataset.id)])
 setRetryNote(false)

 await startFromDataset(dataset.id)
 } catch (e: unknown) {
 // Only the upload itself can throw here — startFromDataset handles its own.
 const msg = e instanceof Error ? e.message : t('qs.err_upload')
 setError(msg)
 setFileName(null)
 setBusy(false)
 }
 }

 // ── Step 2: Confirm columns → trigger training ───────────────────────────────
 const handleConfirm = async () => {
 if (!sessionId || !inspection) return

 setError(null)
 setBusy(true)
 setStep(3)

 try {
 // POST canonical columns mapping
 await chooseColumnsCanonical(sessionId, {
  canonical_mapping: mapping,
  defaults_override: {},
 })

 // POST features config
 await setFeatures(sessionId, {
 lags: [1, 7, 14, 28],
 rolling: [7, 14, 28],
 diffs: [1],
 calendar: true,
 ewm_spans: [7, 14],
 })

 // POST models config
 await setModels(sessionId, ['lightgbm', 'prophet', 'croston', 'xgboost'])

 // POST validation config
 await setValidationConfig(sessionId, {
 train_ratio: 0.8,
 walk_forward: true,
 wfv_splits: 3,
 min_history: 20,
 seasonal_period: 7,
 })

 // Forecast horizon is NOT posted here: the backend derives each grain's
 // horizon from user_horizon_days at launch (see startTraining below).

 // POST business config
 // One source of truth for what Faro assumes (src/lib/inventoryDefaults.ts,
 // mirroring backend/inventory/defaults.py) — this used to be a literal 15
 // sitting next to a literal 7 in the mapping step above.
 await setBusinessConfig(sessionId, {
 service_level: DEFAULT_SERVICE_LEVEL,
 lead_time_days: DEFAULT_LEAD_TIME_DAYS,
 holding_cost_pct: DEFAULT_HOLDING_COST_PCT,
 stockout_cost_multiplier: 3.0,
 })

 // Start training — fans out into a granularity family (daily/weekly/…),
 // narrowed and sized by the user's step-1 plan settings.
 const res = await startTraining(sessionId, {
 user_horizon_days: horizonDays,
 user_granularity: granularity,
 })
 trainLaunchedRef.current = true

 // Poll the whole family
 await pollFamily(res.job_id, res.family)
 } catch (e: unknown) {
 const msg = e instanceof Error ? e.message : t('qs.err_config')
 setError(msg)
 setBusy(false)
 }
 }

 // ── Polling ──────────────────────────────────────────────────────────────────
 // A training launch fans out into a granularity family: a base (finest-grain)
 // session plus coarser siblings, each its own job trained on the shared worker.
 // The progress bar must reflect ALL members — polling only the base made it look
 // stuck (a single job's 40% "training" plateau) while siblings were still queued.
 // We therefore average every member's percent (100% only when all finish), but
 // redirect as soon as the BASE session's results are ready: the semáforo runs off
 // the finest grain, and coarser periods keep computing in the background. The
 // destination resolves the active session itself (planning resolver), matching
 // every other screen — no session id needs to be threaded through the URL.
 const pollFamily = async (baseJobId: string, family?: TrainingFamily) => {
 // Member job ids to poll for progress. Fall back to the base job alone when
 // no family came back (family-less/legacy response, or an empty sessions list).
 const memberJobIds = family?.sessions?.length
 ? family.sessions.map(m => m.job_id)
 : [baseJobId]
 setMultiPeriod(memberJobIds.length > 1)

 // Cap polling so a job stuck in RUNNING (dead/orphaned worker) can't spin
 // this tab forever. 3s/poll × 600 ≈ 30 min, well above normal training.
 const MAX_POLLS = 600
 let attempts = 0

 const poll = async (): Promise<void> => {
 try {
 const jobs = await Promise.all(memberJobIds.map(id => getJob(id)))
 const baseJob = jobs.find(j => j.id === baseJobId) ?? jobs[0]

 // Aggregate progress across the family — a settled (done/failed/cancelled)
 // member counts as 100 so a fast sibling finishing pulls the bar forward
 // instead of leaving it pinned to the slowest member's plateau.
 const pcts = jobs.map(j => {
 if (j.status === 'COMPLETED' || j.status === 'FAILED' || j.status === 'CANCELLED') return 100
 return typeof j.progress?.percent === 'number' ? j.progress.percent : 0
 })
 setTrainPct(Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length))

 // The step message tracks the BASE job (finest grain — what the user lands on).
 // The worker emits its message in English (backend code is English-only).
 // `step` is the stable key, so the Spanish copy lives here; the raw message
 // is the fallback for any stage this map does not know yet.
 if (baseJob?.progress) {
 const stepKey = baseJob.progress.step ? `qs.stage_${baseJob.progress.step}` : null
 const translated = stepKey ? t(stepKey as never) : null
 if (translated && translated !== stepKey) setTrainMsg(translated)
 else if (baseJob.progress.message) setTrainMsg(baseJob.progress.message)
 }

 // Redirect the moment the base session's results are ready — don't wait on
 // coarser siblings. If ONLY the base failed, surface it; a sibling failing
 // is non-fatal to onboarding (the daily semáforo still works).
 if (baseJob?.status === 'COMPLETED') {
 setTrainPct(100)
 // Make the run the user just waited for the ACTIVE session before landing
 // on /hoy. The backend resolver already prefers the newest family, but the
 // planning context lives in the AppShell — which stays mounted across this
 // client-side navigation — so it still holds the active_session_id resolved
 // before this training existed. useAutoSession applies that cached id on
 // mount and then skips its own fetch, which is exactly how /hoy ended up
 // showing the PREVIOUS session's briefing, KPIs and cart. Awaited, so /hoy
 // mounts with the new value. Deliberately scoped to the user's OWN
 // just-finished run: the app is never re-pointed at a session that finished
 // in the background while the user was mid-task somewhere else.
 await planningCtx?.reload()
 router.push('/hoy')
 return
 }
 if (baseJob?.status === 'FAILED') {
 setError(`${t('qs.err_failed')} ${trainingErrorText(baseJob.error, t)}`)
 setBusy(false)
 return
 }
 if (++attempts >= MAX_POLLS) {
 setError(t('qs.err_timeout'))
 setBusy(false)
 return
 }
 // Still running, poll again
 await new Promise(res => setTimeout(res, 3000))
 return poll()
 } catch (e: unknown) {
 const msg = e instanceof Error ? e.message : t('qs.err_status')
 setError(msg)
 setBusy(false)
 }
 }

 return poll()
 }

 const handleRetry = () => {
 setError(null)
 setTrainMsg('')
 setTrainPct(null)
 setMultiPeriod(false)
 msgIdxRef.current = 0

 if (datasetId) {
 // The file already lives on the server — never force a re-upload.
 setRetryNote(true)
 if (!trainLaunchedRef.current && sessionId && inspection) {
  // The failure happened while posting configs, before any training
  // launch: the session is still in a configurable state (the configure
  // endpoints are re-callable there), so just return to the mapping step
  // with the user's column choices intact.
  setBusy(false)
  setStep(2)
  return
 }
 // Training already launched: the old session is QUEUED/RUNNING/FAILED and
 // /train would reject it (409) mid-run — attach the same dataset to a
 // fresh session and re-enter mapping keeping the user's column choices.
 setStep(1)
 void startFromDataset(datasetId, true)
 return
 }

 // No reusable dataset (demo path or nothing uploaded yet): full reset.
 setStep(1)
 setBusy(false)
 setFileName(null)
 setSessionId(null)
 setInspection(null)
 setRetryNote(false)
 setMapping(Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null])))
 }

 // ── Preview table (first 3 rows sample) ─────────────────────────────────────
 function PreviewTable() {
 if (!inspection) return null
 const profile = inspection.profile
 const cols = profile.columns.slice(0, 5)
 const maxRows = 3
 return (
 <div style={{ marginTop: 16, overflowX: 'auto' }}>
 <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 8 }}>
 {t('qs.preview')}
 </p>
 <table style={{
 borderCollapse: 'collapse', fontSize: 12, width: '100%',
 border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden',
 }}>
 <thead>
 <tr style={{ background: 'var(--surface-2, #f8fafc)' }}>
 {cols.map(c => (
 <th key={c.name} style={{
 padding: '6px 12px', textAlign: 'left',
 borderBottom: '1px solid var(--border)',
 color: 'var(--dim)', fontWeight: 600,
 }}>
 {c.name}
 </th>
 ))}
 </tr>
 </thead>
 <tbody>
 {Array.from({ length: maxRows }).map((_, i) => (
 <tr key={i} style={{ borderBottom: i < maxRows - 1 ? '1px solid var(--border)' : 'none' }}>
 {cols.map(c => (
 <td key={c.name} style={{ padding: '6px 12px', color: 'var(--text)' }}>
 {String(c.sample?.[i] ?? '—')}
 </td>
 ))}
 </tr>
 ))}
 </tbody>
 </table>
 {profile.columns.length > 5 && (
 <p style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6 }}>
 + {profile.columns.length - 5} {t('qs.more_columns')}
 </p>
 )}
 </div>
 )
 }

 return (
 <>
 {/* Keyframes */}
 <style>{`
 @keyframes qs-spin {
 to { transform: rotate(360deg); }
 }
 @keyframes qs-fade {
 from { opacity: 0; transform: translateY(4px); }
 to { opacity: 1; transform: translateY(0); }
 }
 `}</style>

 <div style={{
 minHeight: '100vh',
 background: 'var(--bg)',
 display: 'flex',
 flexDirection: 'column',
 alignItems: 'center',
 padding: '20px 20px 48px',
 }}>
 <div style={{ width: '100%', maxWidth: 580 }}>

 {/* Same nav entry as /data — the two routes are tabs of each other. */}
 <DataTabs style={{ marginBottom: 32 }} />

 {/* Header */}
 <div style={{ textAlign: 'center', marginBottom: 40 }}>
 <h1 style={{
 fontSize: 26, fontWeight: 700,
 color: 'var(--text)', margin: 0, marginBottom: 8,
 letterSpacing: '-0.02em',
 }}>
 {t('qs.title')}
 </h1>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: 0 }}>
 {t('qs.subtitle')}
 </p>
 </div>

 {/* Step bar */}
 <StepBar step={step} />

 {/* Card */}
 <div style={{
 background: 'var(--surface)',
 border: '1px solid var(--border)',
 borderRadius: 16,
 padding: 32,
 }}>

 {/* ── Step 1 ──────────────────────────────────────────────────────── */}
 {step === 1 && (
 <div>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 {t('qs.upload_title')}
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
 {t('qs.upload_desc')}
 {' '}<strong style={{ color: 'var(--text)' }}>{t('qs.upload_desc_bold')}</strong>
 </p>

 {/* Plan settings: name + horizon + granularity. Applied to both the
 file-upload path and the one-click demo below. */}
 <PlanSettings
 name={sessionName} onName={setSessionName}
 horizonDays={horizonDays} onHorizonDays={setHorizonDays}
 granularity={granularity} onGranularity={setGranularity}
 busy={busy}
 />

 {/* Source selector: upload a new file vs reuse a previously uploaded
 dataset. The reuse tab only exists once the tenant has datasets. */}
 {(datasets.length > 0 || clonableSessions.length > 0) && (
 <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
 {([
 { value: 'upload',   labelKey: 'qs.reuse_tab_upload' },
 ...(datasets.length > 0
 ? [{ value: 'existing' as const, labelKey: 'qs.reuse_tab_existing' }] : []),
 ...(clonableSessions.length > 0
 ? [{ value: 'clone' as const, labelKey: 'qs.clone_tab' }] : []),
 ] as const).map(tab => (
 <button
  key={tab.value}
  type="button"
  onClick={() => setSource(tab.value)}
  disabled={busy}
  aria-pressed={source === tab.value}
  style={{
  flex: 1, padding: '9px 0', borderRadius: 8, fontSize: 13,
  fontWeight: source === tab.value ? 700 : 400,
  border: `1px solid ${source === tab.value ? 'var(--accent)' : 'var(--border)'}`,
  background: source === tab.value ? 'var(--accent-dim, #eef2ff)' : 'var(--surface)',
  color: source === tab.value ? 'var(--accent)' : 'var(--text)',
  cursor: busy ? 'not-allowed' : 'pointer',
  transition: 'all 0.15s',
  }}
 >
  {t(tab.labelKey)}
 </button>
 ))}
 </div>
 )}

 {source === 'clone' && clonableSessions.length > 0 ? (
 <SessionClonePicker
 sessions={clonableSessions}
 onPick={s => void handleCloneSession(s)}
 busy={busy}
 />
 ) : source === 'existing' && datasets.length > 0 ? (
 <DatasetPicker datasets={datasets} onPick={handlePickExisting} busy={busy} />
 ) : (
 <>
 <DropZone onFile={handleFile} busy={busy} />

 {fileName && !error && (
 <div style={{
 marginTop: 12, padding: '8px 14px',
 background: 'var(--accent-dim, #eef2ff)',
 borderRadius: 8, fontSize: 13,
 color: 'var(--accent)',
 }}>
 ✓ {t('qs.file_selected')} {fileName}
 </div>
 )}
 </>
 )}

 {/* Shared between both tabs: upload errors AND attach/inspect errors
 from the reuse path land here. */}
 {error && (
 <div style={{
 marginTop: 12, padding: '10px 14px',
 background: '#fee2e2', borderRadius: 8,
 fontSize: 13, color: '#dc2626',
 whiteSpace: 'pre-line',
 }}>
 {error}
 </div>
 )}

 {source !== 'existing' && csvWarnings.length > 0 && (
 <div style={{
 marginTop: 12, padding: '10px 14px',
 background: '#fef3c7', borderRadius: 8,
 fontSize: 13, color: '#92400e',
 whiteSpace: 'pre-line',
 }}>
 {csvWarnings.join('\n')}
 </div>
 )}

 {source !== 'existing' && <>

 {/* Row-level findings: "fila 214: fecha inválida — se esperaba AAAA-MM-DD" */}
 <CsvIssueReport groups={csvIssues} fileName={fileName} />

 {/* The report embeds its own template button — don't show two */}
 {csvIssues.length === 0 && (
 <div style={{ marginTop: 14 }}>
 <CsvTemplateButton />
 </div>
 )}

 {/* Demo de un clic: ver el semáforo sin preparar ningún archivo */}
 <div style={{
 marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border)',
 textAlign: 'center',
 }}>
 <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 10px' }}>
 {t('qs.demo_prompt')}
 </p>
 <button
 onClick={handleDemo}
 disabled={busy}
 style={{
 padding: '11px 24px',
 background: 'transparent',
 color: 'var(--accent)',
 border: '1px solid var(--accent)',
 borderRadius: 10, fontSize: 14, fontWeight: 700,
 cursor: busy ? 'not-allowed' : 'pointer',
 opacity: busy ? 0.6 : 1,
 }}
 >
 {t('qs.demo_btn')}
 </button>
 <p style={{ fontSize: 11, color: 'var(--dim)', margin: '8px 0 0', opacity: 0.8 }}>
 {t('qs.demo_hint')}
 </p>
 </div>

 <CsvExample />
 </>}
 </div>
 )}

 {/* ── Step 2 ──────────────────────────────────────────────────────── */}
 {step === 2 && inspection && (
 <div>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 {t('qs.confirm_title')}
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
 {t('qs.confirm_desc')}
 </p>

 {/* After a retry the dataset is reused server-side — tell the user
 no re-upload happened so the jump back here isn't confusing. */}
 {retryNote && (
 <div style={{
 marginBottom: 16, padding: '8px 14px',
 background: 'var(--accent-dim, #eef2ff)', borderRadius: 8,
 fontSize: 13, color: 'var(--accent)',
 }}>
 {t('qs.reuse_retry_note')}
 </div>
 )}

 {/* The monthly re-upload: last month's file with new rows. Naming the run
 the mapping came from is what makes it safe to just confirm — and when it
 could NOT be carried over, naming the columns that disappeared is the
 difference between a considered choice and a silent change of what gets
 trained on. */}
 {reusedMapping && (
 <div style={{
 marginBottom: 16, padding: '10px 14px', borderRadius: 8,
 border: `1px solid ${reusedMapping.missing.length ? '#d9770655' : 'var(--border)'}`,
 background: reusedMapping.missing.length ? 'rgba(217,119,6,0.07)' : 'var(--surface-2)',
 fontSize: 13, color: 'var(--text)', lineHeight: 1.55,
 }}>
 {reusedMapping.missing.length === 0
 ? t('qs.mapping_reused', { session: reusedMapping.from })
 : t('qs.mapping_reuse_failed', {
  session: reusedMapping.from,
  columns: reusedMapping.missing.join(', '),
 })}
 </div>
 )}

 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {CANONICAL_FIELDS.map(field => {
  const allCols = inspection.profile.columns.map(c => c.name)
  const val     = mapping[field.name]
  const isNone  = val === null

  return (
  <div key={field.name} style={{
   display: 'grid', gridTemplateColumns: '1fr 1fr',
   alignItems: 'center', gap: 12,
   padding: '10px 0',
   borderBottom: '1px solid var(--border)',
  }}>
   <div>
   <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
    {field.required && <span style={{ color: '#ef4444', marginRight: 4 }}>★</span>}
    {field.label}
   </span>
   {!field.required && isNone && (
    <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
    {t('qs.default_prefix')} {(field as { default?: string }).default}
    </div>
   )}
   </div>
   <select
   value={val ?? '__none__'}
   onChange={e => {
    const v = e.target.value
    setMapping(prev => ({ ...prev, [field.name]: v === '__none__' ? null : v }))
   }}
   style={{
    padding: '8px 10px', borderRadius: 8,
    border: `1px solid ${field.required && !val ? '#ef4444' : 'var(--border)'}`,
    background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
    cursor: 'pointer',
   }}
   >
   {!field.required && (
    <option value="__none__">{t('qs.not_in_file')}</option>
   )}
   {field.required && !val && (
    <option value="__none__">{t('qs.select_column')}</option>
   )}
   {allCols.map(c => (
    <option key={c} value={c}>{c}</option>
   ))}
   </select>
  </div>
  )
 })}
 </div>

 <PreviewTable />

 {/* The profiler has always found these; nothing used to show them. This is
     the last screen where the user can still go fix the file. */}
 <DataIssuesPanel
  issues={inspection.profile.data_quality?.issues ?? []}
  granularity={inspection.granularity}
 />

 {error && (
 <div style={{ marginTop: 16, padding: '10px 14px', background: '#fee2e2',
  borderRadius: 8, fontSize: 13, color: '#dc2626' }}>
  {error}
 </div>
 )}

 <button
 onClick={handleConfirm}
 disabled={busy || CANONICAL_FIELDS.filter(f => f.required).some(f => !mapping[f.name])}
 style={{
  marginTop: 28, width: '100%', padding: '14px 0',
  background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 700,
  cursor: (busy || CANONICAL_FIELDS.filter(f => f.required).some(f => !mapping[f.name]))
   ? 'not-allowed' : 'pointer',
  opacity: busy ? 0.7 : 1,
  transition: 'opacity 0.15s',
 }}
 >
 {busy ? t('qs.processing') : t('qs.looks_good')}
 </button>
 </div>
 )}

 {/* ── Step 3 ──────────────────────────────────────────────────────── */}
 {step === 3 && (
 <div style={{ textAlign: 'center' }}>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 {t('qs.learning_title')}
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 32px', lineHeight: 1.6 }}>
 {t('qs.learning_desc')}
 <br />
 {t('qs.learning_desc2')}
 </p>

 {!error && <TrainingLoader message={trainMsg} pct={trainPct} multiPeriod={multiPeriod} />}

 {error && (
 <div style={{ marginTop: 20 }}>
 <div style={{
 padding: '14px 18px',
 background: '#fee2e2', borderRadius: 10,
 fontSize: 14, color: '#dc2626',
 marginBottom: 20,
 }}>
 {error}
 </div>
 <button
 onClick={handleRetry}
 style={{
 padding: '12px 32px',
 background: 'var(--accent)',
 color: '#fff',
 border: 'none', borderRadius: 10,
 fontSize: 14, fontWeight: 700,
 cursor: 'pointer',
 }}
 >
 {t('qs.try_again')}
 </button>
 </div>
 )}
 </div>
 )}
 </div>
 </div>
 </div>
 </>
 )
}

export default function QuickStartPage() {
 return (
 <Suspense fallback={null}>
 <QuickStartPageContent />
 </Suspense>
 )
}

'use client'
import { useState, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
 createSession, uploadDataset, attachDataset, inspectSession,
 chooseColumnsCanonical, setFeatures, setModels, setValidationConfig,
 setForecastConfig, setBusinessConfig, startTraining, getJob,
 startDemoQuickstart,
} from '@/lib/api'
import { validateSalesCsv } from '@/lib/csvCheck'
import type { InspectionResult, CanonicalMapping } from '@/lib/types'
import HelpTip from '@/components/ui/HelpTip'
import { useLanguage } from '@/contexts/LanguageContext'

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
 const rows = [
 { fecha: '2024-01-01', producto: 'SKU-001', cantidad: '32' },
 { fecha: '2024-01-02', producto: 'SKU-001', cantidad: '28' },
 { fecha: '2024-01-03', producto: 'SKU-002', cantidad: '15' },
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
 {['fecha', 'producto', 'cantidad'].map(h => (
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
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.fecha}</td>
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.producto}</td>
 <td style={{ padding: '6px 12px', color: 'var(--text)' }}>{r.cantidad}</td>
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


function TrainingLoader({ message, pct }: { message: string; pct: number | null }) {
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
 { name: 'lead_time',     label: 'Lead Time (días)',   required: false, default: '7' },
 { name: 'price',         label: 'Precio',             required: false, default: 'Desconocido' },
 { name: 'cost',          label: 'Costo',              required: false, default: 'Desconocido' },
 { name: 'regular_price', label: 'Precio Regular',     required: false, default: 'Desconocido' },
 { name: 'promo_price',   label: 'Precio Promocional', required: false, default: '= Precio Regular' },
 { name: 'promo',         label: 'Promoción',          required: false, default: 'false' },
 { name: 'promo_type',    label: 'Tipo de Promoción',  required: false, default: 'Sin promoción' },
 { name: 'discount',      label: 'Descuento',          required: false, default: '0%' },
] as const

// ── Quick-start page ───────────────────────────────────────────────────────────
export default function QuickStartPage() {
 const router = useRouter()
 const { t } = useLanguage()

 const [step, setStep] = useState(1)
 const [busy, setBusy] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [fileName, setFileName] = useState<string | null>(null)

 // Session / dataset IDs
 const [sessionId, setSessionId] = useState<string | null>(null)

 // Inspection result
 const [inspection, setInspection] = useState<InspectionResult | null>(null)

 // Column mapping (14-field canonical schema)
 const [mapping, setMapping] = useState<Record<string, string | null>>(
   Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
 )

 // Training progress
 const [trainMsg, setTrainMsg] = useState('')
 const [trainPct, setTrainPct] = useState<number | null>(null)
 const msgIdxRef = useRef(0)

 // Non-fatal pre-upload observations (row counts, ignored rows, short history)
 const [csvWarnings, setCsvWarnings] = useState<string[]>([])

 // ── Demo: seed everything server-side and jump straight to training ─────────
 const handleDemo = async () => {
 setError(null)
 setBusy(true)
 setStep(3)
 try {
 const demo = await startDemoQuickstart()
 setSessionId(demo.session_id)
 await pollJob(demo.job_id, demo.session_id)
 } catch (e: unknown) {
 setError(e instanceof Error ? e.message : t('qs.err_demo'))
 setBusy(false)
 }
 }

 // ── Step 1: Upload ───────────────────────────────────────────────────────────
 const handleFile = async (file: File) => {
 setError(null)
 setCsvWarnings([])
 setBusy(true)
 setFileName(file.name)

 // Pre-upload validation for CSVs: report broken rows with their line number
 // BEFORE uploading (Excel files are profiled server-side instead).
 if (file.name.toLowerCase().endsWith('.csv')) {
 try {
 const check = validateSalesCsv(await file.text())
 if (!check.ok) {
  setError(check.errors.join('\n'))
  setFileName(null)
  setBusy(false)
  return
 }
 if (check.warnings.length) setCsvWarnings(check.warnings)
 } catch { /* unreadable as text — let the backend decide */ }
 }

 try {
 // 1. Create session
 const session = await createSession()
 setSessionId(session.session_id)

 // 2. Upload dataset
 const fd = new FormData()
 fd.append('file', file)
 const dataset = await uploadDataset(fd)

 // 3. Attach dataset to session
 await attachDataset(session.session_id, dataset.id)

 // 4. Inspect session to get column candidates
 const insp = await inspectSession(session.session_id)
 setInspection(insp)

 // Pre-select from canonical suggestions
 const suggestions: CanonicalMapping = insp.canonical_suggestions ?? {}
 setMapping(prev => {
  const next = { ...prev }
  for (const field of CANONICAL_FIELDS) {
   const sug = suggestions[field.name]
   if (sug?.top && sug.confidence >= 0.7) {
    next[field.name] = sug.top
   }
  }
  return next
 })

 setStep(2)
 } catch (e: unknown) {
 const msg = e instanceof Error ? e.message : t('qs.err_upload')
 setError(msg)
 setFileName(null)
 } finally {
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

 // POST forecast config. Horizon must exceed the lead time (15d) so the
 // forecast can "see" past the reorder point — otherwise proactive peak
 // alerts can never give a future order-by date. 30 = lead_time + ~2 weeks.
 await setForecastConfig(sessionId, { horizon: 30 })

 // POST business config
 await setBusinessConfig(sessionId, {
 service_level: 0.95,
 lead_time_days: 15,
 holding_cost_pct: 0.20,
 stockout_cost_multiplier: 3.0,
 })

 // Start training
 const { job_id } = await startTraining(sessionId)

 // Poll job
 await pollJob(job_id, sessionId)
 } catch (e: unknown) {
 const msg = e instanceof Error ? e.message : t('qs.err_config')
 setError(msg)
 setBusy(false)
 }
 }

 // ── Polling ──────────────────────────────────────────────────────────────────
 const pollJob = async (jobId: string, sid: string) => {
 // Cap polling so a job stuck in RUNNING (dead/orphaned worker) can't spin
 // this tab forever. 3s/poll × 600 ≈ 30 min, well above normal training.
 const MAX_POLLS = 600
 let attempts = 0

 const poll = async (): Promise<void> => {
 try {
 const job = await getJob(jobId)

 // Show the worker's REAL progress (percent + step message) instead of
 // a fake cycling animation — the backend emits this on every stage.
 if (job.progress) {
 if (typeof job.progress.percent === 'number') setTrainPct(job.progress.percent)
 if (job.progress.message) setTrainMsg(job.progress.message)
 }

 if (job.status === 'COMPLETED') {
 setTrainPct(100)
 router.push(`/inventory?session=${sid}`)
 return
 }
 if (job.status === 'FAILED') {
 setError(`${t('qs.err_failed')} ${job.error ?? t('qs.err_unknown')}`)
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
 setStep(1)
 setError(null)
 setBusy(false)
 setFileName(null)
 setSessionId(null)
 setInspection(null)
 setMapping(Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null])))
 setTrainMsg('')
 setTrainPct(null)
 msgIdxRef.current = 0
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
 padding: '48px 20px',
 }}>
 <div style={{ width: '100%', maxWidth: 580 }}>

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
 <p style={{ fontSize: 12, color: 'var(--dim)', margin: '10px 0 0', opacity: 0.7 }}>
 {t('qs.advanced_hint')}{' '}
 <a href="/forecast" style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 }}>
 {t('qs.use_studio')}
 </a>
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

 {csvWarnings.length > 0 && (
 <div style={{
 marginTop: 12, padding: '10px 14px',
 background: '#fef3c7', borderRadius: 8,
 fontSize: 13, color: '#92400e',
 whiteSpace: 'pre-line',
 }}>
 {csvWarnings.join('\n')}
 </div>
 )}

 <div style={{ marginTop: 10, fontSize: 12 }}>
 <a href="/plantilla_faro.csv" download
 style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 }}>
 {t('qs.download_template')}
 </a>
 </div>

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

 {!error && <TrainingLoader message={trainMsg} pct={trainPct} />}

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

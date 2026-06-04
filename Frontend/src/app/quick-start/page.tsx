'use client'
import { useState, useRef, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import {
 createSession, uploadDataset, attachDataset, inspectSession,
 chooseColumns, setFeatures, setModels, setValidationConfig,
 setForecastConfig, setBusinessConfig, startTraining, getJob,
} from '@/lib/api'
import type { InspectionResult } from '@/lib/types'

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
 const steps = [
 { n: 1, label: 'Sube tus ventas' },
 { n: 2, label: 'Confirma columnas' },
 { n: 3, label: 'El sistema aprende' },
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
 const rows = [
 { fecha: '2024-01-01', producto: 'SKU-001', cantidad: '32' },
 { fecha: '2024-01-02', producto: 'SKU-001', cantidad: '28' },
 { fecha: '2024-01-03', producto: 'SKU-002', cantidad: '15' },
 ]
 return (
 <div style={{ marginTop: 20, overflowX: 'auto' }}>
 <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 8 }}>
 Ejemplo de cómo debe verse tu archivo:
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
 {busy ? 'Subiendo archivo…' : 'Arrastra tu archivo aquí o haz clic para seleccionarlo'}
 </div>
 <div style={{ fontSize: 13, color: 'var(--dim)' }}>
 Formatos aceptados: CSV, Excel (.xlsx, .xls)
 </div>
 </div>
 )
}

// ── Loading messages animation ─────────────────────────────────────────────────
const MESSAGES = [
 'Analizando tus patrones de venta...',
 'Identificando estacionalidad...',
 'Entrenando modelos de predicción...',
 'Calculando recomendaciones de inventario...',
]

function TrainingLoader({ message }: { message: string }) {
 return (
 <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 28, paddingTop: 20 }}>
 {/* Spinner */}
 <div style={{ position: 'relative', width: 72, height: 72 }}>
 <div style={{
 position: 'absolute', inset: 0,
 border: '4px solid var(--border)',
 borderTopColor: 'var(--accent)',
 borderRadius: '50%',
 animation: 'qs-spin 0.9s linear infinite',
 }} />
 </div>
 <div
 key={message}
 style={{
 fontSize: 16, fontWeight: 500, color: 'var(--text)',
 textAlign: 'center', minHeight: 28,
 animation: 'qs-fade 0.5s ease-out',
 }}
 >
 {message}
 </div>
 <div style={{ fontSize: 13, color: 'var(--dim)', textAlign: 'center', maxWidth: 340 }}>
 Esto puede tomar algunos minutos dependiendo del tamaño de tu archivo.
 <br />No cierres esta pestaña.
 </div>
 </div>
 )
}

// ── Quick-start page ───────────────────────────────────────────────────────────
export default function QuickStartPage() {
 const router = useRouter()

 const [step, setStep] = useState(1)
 const [busy, setBusy] = useState(false)
 const [error, setError] = useState<string | null>(null)
 const [fileName, setFileName] = useState<string | null>(null)

 // Session / dataset IDs
 const [sessionId, setSessionId] = useState<string | null>(null)

 // Inspection result
 const [inspection, setInspection] = useState<InspectionResult | null>(null)

 // Column selections
 const [dateCol, setDateCol] = useState('')
 const [targetCol, setTargetCol] = useState('')
 const [skuCol, setSkuCol] = useState<string>('__none__')

 // Training progress
 const [trainMsg, setTrainMsg] = useState(MESSAGES[0])
 const msgIdxRef = useRef(0)

 // ── Step 1: Upload ───────────────────────────────────────────────────────────
 const handleFile = async (file: File) => {
 setError(null)
 setBusy(true)
 setFileName(file.name)
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

 // Pre-select first candidates as defaults
 const opts = insp.column_options
 setDateCol(opts.date_candidates[0] ?? '')
 setTargetCol(opts.target_candidates[0] ?? '')
 setSkuCol(opts.group_candidates[0] ?? '__none__')

 setStep(2)
 } catch (e: unknown) {
 const msg = e instanceof Error ? e.message : 'Error al subir el archivo'
 setError(msg)
 setFileName(null)
 } finally {
 setBusy(false)
 }
 }

 // ── Step 2: Confirm columns → trigger training ───────────────────────────────
 const handleConfirm = async () => {
 if (!sessionId || !inspection) return
 if (!dateCol) { setError('Por favor selecciona la columna de fecha.'); return }
 if (!targetCol) { setError('Por favor selecciona la columna de cantidad vendida.'); return }

 setError(null)
 setBusy(true)
 setStep(3)

 try {
 // POST columns config with defaults
 await chooseColumns(sessionId, {
 date_column: dateCol,
 target_column: targetCol,
 sku_column: skuCol === '__none__' ? null : skuCol,
 gap_fill: 'forward',
 outlier_config: {
 strategy: 'winsorize_sigma', n_sigma: 3,
 percentile: 0.05, iqr_k: 1.5,
 per_sku_overrides: {}, per_sku_n_sigma: {},
 per_sku_percentile: {}, per_sku_iqr_k: {},
 },
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
 await setModels(sessionId, ['lightgbm', 'prophet', 'croston'])

 // POST validation config
 await setValidationConfig(sessionId, {
 train_ratio: 0.8,
 walk_forward: true,
 wfv_splits: 3,
 min_history: 20,
 seasonal_period: 7,
 })

 // POST forecast config
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
 const msg = e instanceof Error ? e.message : 'Error al configurar el entrenamiento'
 setError(msg)
 setBusy(false)
 }
 }

 // ── Polling ──────────────────────────────────────────────────────────────────
 const pollJob = async (jobId: string, sid: string) => {
 let msgIdx = 0
 const advance = () => {
 msgIdx = (msgIdx + 1) % MESSAGES.length
 setTrainMsg(MESSAGES[msgIdx])
 }
 const interval = setInterval(advance, 5000)

 const poll = async (): Promise<void> => {
 try {
 const job = await getJob(jobId)
 if (job.status === 'COMPLETED') {
 clearInterval(interval)
 router.push(`/inventory?session=${sid}`)
 return
 }
 if (job.status === 'FAILED') {
 clearInterval(interval)
 setError(`El entrenamiento falló: ${job.error ?? 'error desconocido'}`)
 setBusy(false)
 return
 }
 // Still running, poll again
 await new Promise(res => setTimeout(res, 3000))
 return poll()
 } catch (e: unknown) {
 clearInterval(interval)
 const msg = e instanceof Error ? e.message : 'Error al verificar el estado'
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
 setDateCol('')
 setTargetCol('')
 setSkuCol('__none__')
 setTrainMsg(MESSAGES[0])
 msgIdxRef.current = 0
 }

 // Keep trainMsg cycling only on step 3
 useEffect(() => {
 if (step !== 3 || !busy) return
 const id = setInterval(() => {
 msgIdxRef.current = (msgIdxRef.current + 1) % MESSAGES.length
 setTrainMsg(MESSAGES[msgIdxRef.current])
 }, 5000)
 return () => clearInterval(id)
 }, [step, busy])

 const opts = inspection?.column_options

 // ── Preview table (first 3 rows sample) ─────────────────────────────────────
 function PreviewTable() {
 if (!inspection) return null
 const profile = inspection.profile
 const cols = profile.columns.slice(0, 5)
 const maxRows = 3
 return (
 <div style={{ marginTop: 16, overflowX: 'auto' }}>
 <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 8 }}>
 Vista previa de tus datos:
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
 + {profile.columns.length - 5} columnas más
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
 Inicio Rápido
 </h1>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: 0 }}>
 De cero a semáforo de inventario en menos de 5 minutos
 </p>
 <p style={{ fontSize: 12, color: 'var(--dim)', margin: '10px 0 0', opacity: 0.7 }}>
 ¿Necesitas configuración avanzada de modelos, columnas o validación?{' '}
 <a href="/forecast" style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 }}>
 Usar Forecast Studio →
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
 Sube tus ventas
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
 Necesitamos tu historial de ventas. El archivo debe tener al menos:
 {' '}<strong style={{ color: 'var(--text)' }}>fecha, producto y cantidad vendida.</strong>
 </p>

 <DropZone onFile={handleFile} busy={busy} />

 {fileName && !error && (
 <div style={{
 marginTop: 12, padding: '8px 14px',
 background: 'var(--accent-dim, #eef2ff)',
 borderRadius: 8, fontSize: 13,
 color: 'var(--accent)',
 }}>
 ✓ Archivo seleccionado: {fileName}
 </div>
 )}

 {error && (
 <div style={{
 marginTop: 12, padding: '10px 14px',
 background: '#fee2e2', borderRadius: 8,
 fontSize: 13, color: '#dc2626',
 }}>
 {error}
 </div>
 )}

 <CsvExample />
 </div>
 )}

 {/* ── Step 2 ──────────────────────────────────────────────────────── */}
 {step === 2 && opts && (
 <div>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 Confirma tus columnas
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 24px', lineHeight: 1.6 }}>
 Detectamos las siguientes columnas en tu archivo. Confirma cuál es cuál.
 </p>

 <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
 {/* Date column */}
 <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
 <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
 ¿Cuál columna es la fecha?
 </span>
 <select
 value={dateCol}
 onChange={e => setDateCol(e.target.value)}
 style={{
 padding: '10px 12px', borderRadius: 8,
 border: '1px solid var(--border)',
 background: 'var(--surface)',
 color: 'var(--text)', fontSize: 14,
 cursor: 'pointer',
 }}
 >
 <option value="">— Selecciona una columna —</option>
 {opts.date_candidates.map(c => (
 <option key={c} value={c}>{c}</option>
 ))}
 </select>
 </label>

 {/* Target column */}
 <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
 <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
 ¿Cuál columna es la cantidad vendida?
 </span>
 <select
 value={targetCol}
 onChange={e => setTargetCol(e.target.value)}
 style={{
 padding: '10px 12px', borderRadius: 8,
 border: '1px solid var(--border)',
 background: 'var(--surface)',
 color: 'var(--text)', fontSize: 14,
 cursor: 'pointer',
 }}
 >
 <option value="">— Selecciona una columna —</option>
 {opts.target_candidates.map(c => (
 <option key={c} value={c}>{c}</option>
 ))}
 </select>
 </label>

 {/* SKU column */}
 <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
 <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
 ¿Tienes una columna de producto / SKU?
 </span>
 <select
 value={skuCol}
 onChange={e => setSkuCol(e.target.value)}
 style={{
 padding: '10px 12px', borderRadius: 8,
 border: '1px solid var(--border)',
 background: 'var(--surface)',
 color: 'var(--text)', fontSize: 14,
 cursor: 'pointer',
 }}
 >
 <option value="__none__">No, es un solo producto</option>
 {opts.group_candidates.map(c => (
 <option key={c} value={c}>{c}</option>
 ))}
 </select>
 </label>
 </div>

 {/* Preview */}
 <PreviewTable />

 {error && (
 <div style={{
 marginTop: 16, padding: '10px 14px',
 background: '#fee2e2', borderRadius: 8,
 fontSize: 13, color: '#dc2626',
 }}>
 {error}
 </div>
 )}

 <button
 onClick={handleConfirm}
 disabled={busy}
 style={{
 marginTop: 28,
 width: '100%', padding: '14px 0',
 background: 'var(--accent)',
 color: '#fff',
 border: 'none', borderRadius: 10,
 fontSize: 15, fontWeight: 700,
 cursor: busy ? 'not-allowed' : 'pointer',
 opacity: busy ? 0.7 : 1,
 transition: 'opacity 0.15s',
 }}
 >
 {busy ? 'Procesando…' : 'Esto se ve bien, continuar →'}
 </button>
 </div>
 )}

 {/* ── Step 3 ──────────────────────────────────────────────────────── */}
 {step === 3 && (
 <div style={{ textAlign: 'center' }}>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 El sistema está aprendiendo
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 32px', lineHeight: 1.6 }}>
 Configuramos automáticamente los mejores parámetros para tu negocio.
 <br />
 Al terminar, verás el semáforo de inventario de todos tus productos.
 </p>

 {!error && <TrainingLoader message={trainMsg} />}

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
 Intentar de nuevo
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

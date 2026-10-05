'use client'
import { Suspense, useState, useRef, useCallback, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
 createSession, uploadDataset, attachDataset, inspectSession,
 chooseColumnsCanonical, setFeatures, setModels, setValidationConfig,
 setBusinessConfig, startTraining, getJob,
 startDemoQuickstart, listDatasets, getSessionSummaries, getColumnsConfig,
 getDataGate, setRemediations, getActiveTraining, getTenantTimezone,
 previewGuidedReading, applyGuidedReading, getHorizonNeed,
} from '@/lib/api'
import type { TrainingFamily } from '@/lib/api'
import {
  DEFAULT_HOLDING_COST_PCT, DEFAULT_LEAD_TIME_DAYS, DEFAULT_SERVICE_LEVEL,
} from '@/lib/inventoryDefaults'
import { validateSalesCsv } from '@/lib/csvCheck'
import type { CsvIssueGroup } from '@/lib/csvCheck'
import UploadGuide from '@/components/upload/UploadGuide'
import GuidedReading from '@/components/upload/GuidedReading'
import CsvIssueReport, { CsvTemplateButton } from '@/components/ui/CsvIssueReport'
import DataIssuesPanel from '@/components/ui/DataIssuesPanel'
import RemediationChoices from '@/components/ui/RemediationChoices'
import type {
 InspectionResult, CanonicalMapping, DatasetMeta, SessionSummary, DataGate,
 GuidanceReport, GuidedRecord, HorizonPreview,
} from '@/lib/types'
import HelpTip from '@/components/ui/HelpTip'
import { useErrorDetail } from '@/components/ui/States'
import DataTabs from '@/components/layout/DataTabs'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import StickyActionBar from '@/components/mobile/StickyActionBar'
import TrainingBudgetNote from '@/components/limits/TrainingBudgetNote'
import { useLanguage } from '@/contexts/LanguageContext'
import { usePlanning } from '@/contexts/PlanningContext'
import { useTraining } from '@/contexts/TrainingContext'
import { useSmoothedPercent } from '@/hooks/useSmoothedPercent'

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
function StepBubble({ n, label, active, done, narrow }: { n: number; label: string; active: boolean; done: boolean; narrow?: boolean }) {
 return (
 <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, ...(narrow ? { flexShrink: 0, maxWidth: 92 } : {}) }}>
 <div style={{
 width: 36, height: 36, borderRadius: '50%',
 display: 'flex', alignItems: 'center', justifyContent: 'center',
 background: done ? '#2E8B62' : active ? 'var(--accent)' : 'var(--surface-2, #f1f5f9)',
 border: `2px solid ${done ? '#2E8B62' : active ? 'var(--accent)' : 'var(--border)'}`,
 color: done || active ? '#fff' : 'var(--dim)',
 fontWeight: 700, fontSize: 15,
 transition: 'all 0.25s',
 }}>
 {done ? '✓' : n}
 </div>
 <span style={{
 fontSize: 12, fontWeight: active ? 600 : 400,
 color: active ? 'var(--accent)' : done ? '#2E8B62' : 'var(--dim)',
 whiteSpace: narrow ? 'normal' : 'nowrap',
 ...(narrow ? { textAlign: 'center', lineHeight: 1.25 } : {}),
 }}>
 {label}
 </span>
 </div>
 )
}

function StepBar({ step }: { step: number }) {
 const { t } = useLanguage()
 // Phone: the connectors flex instead of a fixed 80px, and the labels wrap —
 // three bubbles with nowrap labels measured 420px at 360.
 const narrow = useIsNarrow()
 const steps = [
 { n: 1, label: t('qs.step1') },
 { n: 2, label: t('qs.step2') },
 { n: 3, label: t('qs.step3') },
 ]
 return (
 <div data-tour="qs.steps" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', gap: 0, marginBottom: narrow ? 20 : 40 }}>
 {steps.map((s, i) => (
 <div key={s.n} style={{ display: 'flex', alignItems: 'center', ...(narrow && i < steps.length - 1 ? { flex: 1, minWidth: 0 } : {}) }}>
 <StepBubble n={s.n} label={s.label} active={step === s.n} done={step > s.n} narrow={narrow} />
 {i < steps.length - 1 && (
 <div style={{
 width: narrow ? 'auto' : 80, flex: narrow ? 1 : undefined, minWidth: narrow ? 12 : undefined, height: 2, margin: narrow ? '0 4px' : '0 8px', marginBottom: 24,
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
 <div data-tour="qs.example" style={{ marginTop: 20, overflowX: 'auto' }}>
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
 const narrow = useIsNarrow()
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
 data-tour="qs.upload"
 onDragOver={e => { e.preventDefault(); setDragging(true) }}
 onDragLeave={() => setDragging(false)}
 onDrop={handleDrop}
 onClick={() => !busy && inputRef.current?.click()}
 style={{
 border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
 borderRadius: 12,
 padding: narrow ? '28px 16px' : '48px 32px',
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
 const narrow = useIsNarrow()
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
  overflow: 'hidden', overflowWrap: 'anywhere',
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
  ...(narrow ? { minHeight: 44, padding: '0 16px' } : {}),
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
 const narrow = useIsNarrow()
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
  overflow: 'hidden', overflowWrap: 'anywhere',
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
  ...(narrow ? { minHeight: 44, padding: '0 16px' } : {}),
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

function TrainingLoader({ message, pct: realPct, multiPeriod }: { message: string; pct: number | null; multiPeriod: boolean }) {
 const { t } = useLanguage()
 // Eased toward the last REAL value from the server — never ahead of it.
 const pct = useSmoothedPercent(realPct)
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
// Labels and prose defaults go through i18n: they were hardcoded Spanish, so
// the mapping step of the wizard stayed in Spanish for an English user — on the
// one screen where getting a column wrong costs a whole training run.
// `defaultLiteral` is for the values that are not prose (a number, `false`,
// `0%`), which read the same in both languages.
const CANONICAL_FIELDS = [
 { name: 'sku',           labelKey: 'qs.field_sku',           required: true  },
 { name: 'date',          labelKey: 'qs.field_date',          required: true  },
 { name: 'demand',        labelKey: 'qs.field_demand',        required: true  },
 { name: 'store',         labelKey: 'qs.field_store',         required: false, defaultKey: 'qs.default_single_store' },
 { name: 'region',        labelKey: 'qs.field_region',        required: false, defaultKey: 'qs.default_no_region' },
 { name: 'inventory',     labelKey: 'qs.field_inventory',     required: false, defaultLiteral: '0' },
 // The default shown here is what the engine actually broadcasts into an
 // unmapped lead_time column. It said 7 while the DB, this wizard's business
 // config and /inventory all said 15 — the mapping step was promising the user
 // a number no other screen would honour.
 { name: 'lead_time',     labelKey: 'qs.field_lead_time',     required: false, defaultLiteral: String(DEFAULT_LEAD_TIME_DAYS) },
 { name: 'price',         labelKey: 'qs.field_price',         required: false, defaultKey: 'qs.default_unknown' },
 { name: 'cost',          labelKey: 'qs.field_cost',          required: false, defaultKey: 'qs.default_unknown' },
 { name: 'regular_price', labelKey: 'qs.field_regular_price', required: false, defaultKey: 'qs.default_unknown' },
 { name: 'promo_price',   labelKey: 'qs.field_promo_price',   required: false, defaultKey: 'qs.default_same_as_regular' },
 { name: 'promo',         labelKey: 'qs.field_promo',         required: false, defaultLiteral: 'false' },
 { name: 'promo_type',    labelKey: 'qs.field_promo_type',    required: false, defaultKey: 'qs.default_no_promo' },
 { name: 'discount',      labelKey: 'qs.field_discount',      required: false, defaultLiteral: '0%' },
] as const

// ── Plan settings (name + horizon + granularity, step 1) ───────────────────────
type Granularity = 'auto' | 'daily' | 'weekly' | 'monthly'

// Horizon presets in calendar days; the backend converts to per-grain steps.
const HORIZON_PRESETS = [
 { days: 28,  labelKey: 'qs.plan_horizon_4w' },
 { days: 56,  labelKey: 'qs.plan_horizon_8w' },
 { days: 180, labelKey: 'qs.plan_horizon_6m' },
] as const

// What the engine really accepts, read from the backend (not guessed):
//  · the API takes 1..365 days (`user_horizon_days`, ge=1 le=365);
//  · each grain forecasts at most GENEROUS_REACH steps (daily 90, weekly 26,
//    monthly 12) and at least 2 steps, whatever was asked — so a longer request
//    is silently shortened, which is why the options say so out loud;
//  · a week is 7 days and a month 30 (DAYS_PER_PERIOD).
const HORIZON_MAX_DAYS = 365
const HORIZON_UNIT_DAYS = { days: 1, weeks: 7, months: 30 } as const
type HorizonUnit = keyof typeof HORIZON_UNIT_DAYS
// The grain's reach in days, and the shortest span it can forecast (2 steps).
const GRAIN_REACH_DAYS: Record<Exclude<Granularity, 'auto'>, number> = { daily: 90, weekly: 182, monthly: 360 }
const GRAIN_MIN_DAYS: Record<Exclude<Granularity, 'auto'>, number> = { daily: 2, weekly: 14, monthly: 60 }
// Units that make sense for each detail level: months for a daily forecast and
// days for a monthly one would both be a span the grain cannot express.
const UNITS_FOR_GRAIN: Record<Granularity, HorizonUnit[]> = {
 auto: ['days', 'weeks', 'months'],
 daily: ['days', 'weeks', 'months'],
 weekly: ['weeks', 'months'],
 monthly: ['months'],
}
// Beyond a third of the history, a forecast reads as precise and is not.
const HISTORY_FRACTION = 3

const GRANULARITY_OPTIONS: { value: Granularity; labelKey: string }[] = [
 { value: 'auto',    labelKey: 'qs.plan_granularity_auto' },
 { value: 'daily',   labelKey: 'qs.plan_granularity_daily' },
 { value: 'weekly',  labelKey: 'qs.plan_granularity_weekly' },
 { value: 'monthly', labelKey: 'qs.plan_granularity_monthly' },
]

// Which country's public holidays the engine learns from.
//
// The engine has always accepted this and the wizard never asked, so every
// tenant trained on COLOMBIAN holidays — including the Mexican and Costa Rican
// ones. Holidays are, per the engine's own comment, "among the strongest
// signals a daily retail series carries": a distributor whose December 12th is
// dead and whose Semana Santa is frantic was being modelled on someone else's
// calendar, and nothing on any screen said so.
//
// A curated list rather than the ~150 the `holidays` package supports: this is
// a LatAm product and a 150-row dropdown is a worse answer than a short one.
// The backend validates against the full package list, so a country missing
// here is a one-line addition, not a redesign.
const HOLIDAY_COUNTRIES = [
 { code: 'CR', labelKey: 'qs.country_CR' },
 { code: 'CO', labelKey: 'qs.country_CO' },
 { code: 'MX', labelKey: 'qs.country_MX' },
 { code: 'PE', labelKey: 'qs.country_PE' },
 { code: 'CL', labelKey: 'qs.country_CL' },
 { code: 'AR', labelKey: 'qs.country_AR' },
 { code: 'EC', labelKey: 'qs.country_EC' },
 { code: 'GT', labelKey: 'qs.country_GT' },
 { code: 'PA', labelKey: 'qs.country_PA' },
 { code: 'DO', labelKey: 'qs.country_DO' },
 { code: 'ES', labelKey: 'qs.country_ES' },
 { code: 'US', labelKey: 'qs.country_US' },
] as const

// Decides whether the profiler's reading of the file is certain enough to skip
// the "confirm columns" step. Anything short of certain returns null and the
// step stays on screen: a wrongly guessed date or quantity column does not make
// the training fail, it makes it learn a history that never happened.
//
// Certain means ALL of: the three required fields each have exactly one
// candidate column that is an exact name match; no two fields claim the same
// column; and no optional field has only a weak (partial-name) guess, because
// that is a column the person may want to map and would never be asked about.
const CERTAIN_CONFIDENCE = 0.95
const REQUIRED_FIELDS = ['sku', 'date', 'demand'] as const

function detectedWithCertainty(
 suggestions: CanonicalMapping,
): Record<string, string | null> | null {
 const out: Record<string, string | null> = Object.fromEntries(
  CANONICAL_FIELDS.map(f => [f.name, null]),
 )
 const used = new Set<string>()
 for (const field of CANONICAL_FIELDS) {
  const sug = suggestions[field.name]
  const required = (REQUIRED_FIELDS as readonly string[]).includes(field.name)
  if (required) {
   if (!sug?.top || sug.confidence < CERTAIN_CONFIDENCE || sug.candidates.length !== 1) return null
  } else if (sug?.top && sug.confidence < 0.7) {
   return null
  } else if (!sug?.top) {
   continue
  }
  const col = sug.top as string
  if (used.has(col)) return null
  used.add(col)
  out[field.name] = col
 }
 return out
}

// Days between the first and last date of the uploaded file, or null when the
// profile does not say.
function historyDaysOf(insp: InspectionResult): number | null {
 const a = Date.parse(insp.profile?.stats?.date_min ?? '')
 const b = Date.parse(insp.profile?.stats?.date_max ?? '')
 if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null
 return Math.round((b - a) / 86400000) + 1
}

function Chip({ label, selected, disabled, onClick }: {
 label: string; selected: boolean; disabled: boolean; onClick: () => void
}) {
 const narrow = useIsNarrow()
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
 ...(narrow ? { minHeight: 44, fontSize: 14, padding: '0 16px' } : {}),
 }}
 >
 {label}
 </button>
 )
}

// "45 days" / "12 weeks" / "9 months": the largest whole unit, so the same
// number reads the same wherever it is shown.
function spanText(days: number, t: (k: string, p?: Record<string, string | number>) => string): string {
 if (days >= 60 && days % 30 === 0) { const n = days / 30; return t(n === 1 ? 'qs.span_month' : 'qs.span_months', { n }) }
 if (days >= 14 && days % 7 === 0) { const n = days / 7; return t(n === 1 ? 'qs.span_week' : 'qs.span_weeks', { n }) }
 return t(days === 1 ? 'qs.span_day' : 'qs.span_days', { n: days })
}

function PlanSettings({ name, onName, horizonDays, onHorizonDays, granularity, onGranularity,
                        country, onCountry, busy }: {
 name: string; onName: (v: string) => void
 horizonDays: number; onHorizonDays: (v: number) => void
 granularity: Granularity; onGranularity: (v: Granularity) => void
 country: string; onCountry: (v: string) => void
 busy: boolean
}) {
 const { t } = useLanguage()
 const narrow = useIsNarrow()
 // Custom horizon: a number and a unit. Presets stay as one-click shortcuts;
 // typing here replaces them, picking one clears this.
 const [rawN, setRawN] = useState('')
 const [unit, setUnit] = useState<HorizonUnit>('weeks')
 const allowedUnits = UNITS_FOR_GRAIN[granularity]
 const unitNow: HorizonUnit = allowedUnits.includes(unit) ? unit : allowedUnits[0]
 const typedN = parseInt(rawN, 10)
 const typedDays = Number.isFinite(typedN) && typedN >= 1 ? typedN * HORIZON_UNIT_DAYS[unitNow] : null
 const applyCustom = (n: string, u: HorizonUnit) => {
  const v = parseInt(n, 10)
  if (!Number.isFinite(v) || v < 1) return
  onHorizonDays(Math.min(HORIZON_MAX_DAYS, v * HORIZON_UNIT_DAYS[u]))
 }
 const grainKey = granularity === 'auto' ? null : granularity
 const horizonNote: string | null =
  typedDays !== null && typedDays > HORIZON_MAX_DAYS
   ? t('qs.horizon_max_note', { max: spanText(HORIZON_MAX_DAYS, t) })
   : grainKey && horizonDays > GRAIN_REACH_DAYS[grainKey]
    ? t('qs.horizon_cap_note', {
        grain: t(`qs.plan_granularity_${grainKey}`).toLowerCase(), max: spanText(GRAIN_REACH_DAYS[grainKey], t),
      })
    : grainKey && horizonDays < GRAIN_MIN_DAYS[grainKey]
     ? t('qs.horizon_min_note', { min: spanText(GRAIN_MIN_DAYS[grainKey], t) })
     : !grainKey && horizonDays > GRAIN_REACH_DAYS.daily
      ? t('qs.horizon_cap_auto', { daily: spanText(GRAIN_REACH_DAYS.daily, t), weekly: spanText(GRAIN_REACH_DAYS.weekly, t) })
      : null
 // What the tenant's own suppliers need (lead time + review period). Advisory:
 // when the lookup fails the note is simply absent and the launch decides
 // on the server regardless.
 const [needPreview, setNeedPreview] = useState<HorizonPreview | null>(null)
 useEffect(() => {
  let live = true
  const timer = setTimeout(() => {
   getHorizonNeed(horizonDays)
    .then(p => { if (live) setNeedPreview(p) })
    .catch(() => { if (live) setNeedPreview(null) })
  }, 300)
  return () => { live = false; clearTimeout(timer) }
 }, [horizonDays])
 const needGrain = grainKey ?? 'daily'
 const needNote: string | null = (() => {
  const preview = needPreview
  const need = preview?.need
  if (!preview || !need || !preview.by_grain[needGrain]?.extended) return null
  const params = {
   who: need.supplier ?? need.sku, required: need.required_days,
   lead: need.lead_time_days, review: need.review_period_days,
   span: spanText(need.need_days, t),
  }
  const base = t(need.supplier ? 'qs.horizon_extended' : 'qs.horizon_extended_sku', params)
  return need.capped ? `${base} ${t('qs.horizon_extended_capped', { max: spanText(preview.ceiling_days, t) })}` : base
 })()
 const fieldN: React.CSSProperties = narrow ? { fontSize: 16, minHeight: 44, boxSizing: 'border-box', borderRadius: 10 } : {}
 const labelStyle: React.CSSProperties = {
 fontSize: 13, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6,
 }
 return (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 20 }}>
 <div data-tour="qs.name">
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
 ...fieldN,
 }}
 />
 </div>
 <div data-tour="qs.horizon">
 <span style={labelStyle}>{t('qs.plan_horizon_label')}</span>
 <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
 {HORIZON_PRESETS.map(p => (
 <Chip
  key={p.days}
  label={t(p.labelKey)}
  selected={horizonDays === p.days}
  disabled={busy}
  onClick={() => { setRawN(''); onHorizonDays(p.days) }}
 />
 ))}
 </div>
 <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 10 }}>
 <label htmlFor="qs-horizon-n" style={{ fontSize: 12, color: 'var(--dim)' }}>{t('qs.horizon_custom_label')}</label>
 <input
  id="qs-horizon-n"
  type="number"
  inputMode="numeric"
  min={1}
  value={rawN}
  disabled={busy}
  placeholder={t('qs.horizon_custom_placeholder')}
  onChange={e => { setRawN(e.target.value); applyCustom(e.target.value, unitNow) }}
  style={{
  width: 84, padding: '7px 10px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--surface)',
  color: 'var(--text)', fontSize: 13,
  ...fieldN,
  }}
 />
 <select
  aria-label={t('qs.horizon_custom_label')}
  value={unitNow}
  disabled={busy}
  onChange={e => { const u = e.target.value as HorizonUnit; setUnit(u); applyCustom(rawN, u) }}
  style={{
  padding: '7px 10px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--surface)',
  color: 'var(--text)', fontSize: 13,
  ...fieldN,
  }}
 >
  {allowedUnits.map(u => <option key={u} value={u}>{t(`qs.horizon_unit_${u}`)}</option>)}
 </select>
 </div>
 <div style={{ fontSize: 12, color: horizonNote ? '#92400e' : 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>
 {horizonNote ?? t('qs.horizon_history_tip')}
 </div>
 {needNote && (
 <div role="status" data-testid="qs-horizon-extended" style={{ fontSize: 12, color: '#92400e', marginTop: 6, lineHeight: 1.5 }}>
 {needNote}
 </div>
 )}
 </div>
 <div data-tour="qs.granularity">
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
 <div data-tour="qs.country">
 <label htmlFor="qs-holiday-country" style={labelStyle}>
 {t('qs.plan_country_label')}
 </label>
 <select
 id="qs-holiday-country"
 value={country}
 disabled={busy}
 onChange={e => onCountry(e.target.value)}
 style={{
 padding: '9px 12px', borderRadius: 8,
 border: '1px solid var(--border)', background: 'var(--surface)',
 color: 'var(--text)', fontSize: 13, minWidth: 220,
 ...(narrow ? { ...fieldN, minWidth: 0, width: '100%' } : {}),
 }}
 >
 {HOLIDAY_COUNTRIES.map(c => (
 <option key={c.code} value={c.code}>{t(c.labelKey)}</option>
 ))}
 </select>
 <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>
 {t('qs.plan_country_help')}
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
 // Phone: no second gutter inside the shell's, a 16px card, the mapping as
 // stacked label/select pairs, and the confirm pinned above the tab bar.
 const narrow = useIsNarrow()
 // Backend failures arrive with a stable `error_code`; without this the wizard
 // printed the English `detail` instead — a viewer who picked a file read
 // "Role 'viewer' not permitted. Required: ['admin', 'analyst']".
 const errorDetail = useErrorDetail()
 // The wizard runs inside the AppShell, so the planning context that resolves
 // the active session was loaded BEFORE this training existed — see the
 // redirect in pollFamily for why it has to be refreshed there.
 const planningCtx = usePlanning()
 const trainingCtx = useTraining()

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
 // Defaults to Costa Rica, the anchor market (owner's decision, 2026-09-30).
 // It was Colombia before, only because the first calendar was Colombian.
 // A default for a NEW run; a session that already stored a country keeps it.
 const [holidayCountry, setHolidayCountry] = useState('CR')
 // The account's own country replaces the default above, but never over a
 // choice the person already made in the options.
 const countryTouchedRef = useRef(false)
 const chooseCountry = (code: string) => {
  countryTouchedRef.current = true
  setHolidayCountry(code)
 }
 // Set when the columns were detected with certainty and the mapping step was
 // skipped: the training screen then names them and offers "Change columns".
 // Days of history in the uploaded file, once inspected. Drives the "your
 // horizon is long for this much history" note and keeps the column step open.
 const [historyDays, setHistoryDays] = useState<number | null>(null)
 const [autoMapped, setAutoMapped] = useState<Record<string, string | null> | null>(null)
 // Bumped to retire a running poll loop (see pollFamily and handleChangeColumns).
 const pollGenRef = useRef(0)

 // Inspection result
 const [inspection, setInspection] = useState<InspectionResult | null>(null)
 // The pre-training gate, evaluated against the CONFIRMED mapping. Null until
 // the first confirm attempt: before a mapping exists the profiler's own
 // reading is all there is, and that is what DataIssuesPanel already shows.
 const [gate, setGate] = useState<DataGate | null>(null)
 // {issue_type: option_code} — what the user decided about each fixable finding.
 const [remediationChoices, setRemediationChoices] = useState<Record<string, string>>({})

 // Column mapping (14-field canonical schema)
 const [mapping, setMapping] = useState<Record<string, string | null>>(
   Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
 )

 // ── Guided upload ───────────────────────────────────────────────────────────
 // What the guide makes of the file (read in ForecastingCore), the answers the
 // person has given, and what was already done to the file for them. The report
 // is the single source for the conversation on the mapping step.
 const [guided, setGuided] = useState<GuidanceReport | null>(null)
 const [guidedApplied, setGuidedApplied] = useState<GuidedRecord | null>(null)
 const [guidedDecisions, setGuidedDecisions] = useState<Record<string, unknown>>({})
 const [guidedBusy, setGuidedBusy] = useState(false)
 const [showAdvanced, setShowAdvanced] = useState(false)
 // The person chose to use their file as it is: never re-apply fixes on their behalf.
 const guidedOffRef = useRef(false)
 // Set once the person picks a column themselves: from then on the guide JUDGES
 // that mapping instead of proposing its own.
 const mappingTouchedRef = useRef(false)
 // Which fixes are already applied to the file under the session.
 const appliedKeyRef = useRef('')

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
 setError(errorDetail(e) || t('qs.err_demo'))
 setBusy(false)
 }
 }

 // Arriving via /quick-start?demo=1 (e.g. from the landing page's "empezar
 // gratis" CTA, carried through signup + login) auto-starts the demo instead
 // of waiting on a click — the whole point of that path is zero extra taps
 // between "create account" and "see the semáforo working".
 // Set when this screen unmounts. `pollFamily` below is a self-recursive
 // async closure with no AbortController, so without this it kept running
 // after the user navigated away — and on completion it called
 // `router.push('/compras')`, yanking them off whatever screen they had
 // moved on to, minutes later, discarding anything unsaved there. Every
 // other effect on this page already has a `cancelled` flag; this one, the
 // longest-lived of them (MAX_POLLS ≈ 30 min), did not.
 //
 // Reset on (re)mount: React's development mode mounts, unmounts and mounts
 // again, and a flag that is only ever set to true left the poll exiting on
 // its first lap — the demo trained and the screen never moved on.
 const unmountedRef = useRef(false)
 useEffect(() => {
 unmountedRef.current = false
 return () => { unmountedRef.current = true }
 }, [])

 const autoDemoRanRef = useRef(false)
 useEffect(() => {
 if (autoDemoRanRef.current) return
 if (searchParams.get('demo') !== '1') return
 autoDemoRanRef.current = true
 handleDemo()
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [searchParams])

 // Resume from SERVER state: if a training is queued/running for this tenant
 // (the user left this screen and came back, reloaded, or opened it from the
 // top-bar pill) show its live progress instead of an empty upload step. Local
 // state is never the source: it is gone the moment the user navigates away.
 const resumeCheckedRef = useRef(false)
 useEffect(() => {
 if (resumeCheckedRef.current) return
 resumeCheckedRef.current = true
 if (searchParams.get('demo') === '1') return
 getActiveTraining()
 .then(r => {
 const fam = r.families?.[0]
 if (!fam || trainLaunchedRef.current) return
 trainLaunchedRef.current = true
 setSessionId(fam.base_session_id)
 setBusy(true)
 setStep(3)
 void pollFamily(fam.base_job_id, {
 family_id: fam.family_id,
 base_job_id: fam.base_job_id,
 sessions: fam.members.map(m => ({
 session_id: m.session_id, granularity: m.granularity ?? '', job_id: m.job_id,
 })),
 })
 })
 .catch(() => { /* nothing to resume: the normal upload step stays */ })
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [])

 // Ask the gate as soon as the mapping screen opens, not only when the user
 // presses confirm.
 //
 // The profiler's own `blocking` flag cannot tell a dead end from a question:
 // it marks duplicated rows as blocking, so the screen said "este archivo no
 // puede generar un pronóstico" and offered nothing but another file — for a
 // problem the gate has two documented answers to. Fetching here puts the
 // questions on screen the moment the user can act on them; `handleConfirm`
 // re-asks against the confirmed mapping, which is the authoritative verdict.
 useEffect(() => {
 if (step !== 2 || !sessionId) return
 let cancelled = false
 getDataGate(sessionId, { silent: true })
 .then(g => { if (!cancelled) setGate(g) })
 .catch(() => { /* the profiler's own panel is still rendered */ })
 return () => { cancelled = true }
 }, [step, sessionId])

 // Country for the holiday calendar: the account already names one (its
 // timezone's country). A failed read keeps the default — the options still
 // show which country will be used, so nothing is hidden.
 useEffect(() => {
 getTenantTimezone()
 .then(r => {
 const code = r.current?.country
 if (code && !countryTouchedRef.current && HOLIDAY_COUNTRIES.some(c => c.code === code)) {
  setHolidayCountry(code)
 }
 })
 .catch(() => { /* default country stays */ })
 }, [])

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

 // ── Guided upload: conversation helpers ─────────────────────────────────────
 // The guide's own reading of which column is which. Only the three required
 // fields (and the store) are taken from it, and only once it is complete: a
 // half-proposed mapping would look like a decision.
 const guidedMapping = (g: GuidanceReport | null): Record<string, string> | null => {
  if (!g || g.verdict !== 'ready') return null
  const m = g.mapping
  return m.sku && m.date && m.demand ? (m as Record<string, string>) : null
 }
 const adoptGuidedMapping = (g: GuidanceReport | null) => {
  if (!g || mappingTouchedRef.current) return
  const m = guidedMapping(g)
  if (!m) return
  setMapping(prev => ({
   ...prev,
   sku: m.sku, date: m.date, demand: m.demand, ...(m.store ? { store: m.store } : {}),
  }))
 }

 // Applies the fixes of a READY report into a cleaned copy (the original is
 // never touched) and re-reads the file. Returns the new inspection, or null
 // when there was nothing to do or the same fixes are already in place.
 const settleGuided = async (
  sid: string, rep: GuidanceReport, decisions: Record<string, unknown>,
  userMapping: Record<string, string | null> | null,
 ): Promise<InspectionResult | null> => {
  if (guidedOffRef.current || rep.verdict !== 'ready' || rep.fixes.length === 0) return null
  const key = JSON.stringify([rep.fixes, rep.mapping])
  if (key === appliedKeyRef.current) return null
  const res = await applyGuidedReading(sid, { decisions, mapping: userMapping })
  appliedKeyRef.current = key
  setGuidedApplied(res.applied)
  const fresh = await inspectSession(sid)
  setInspection(fresh)
  return fresh
 }

 // One answer: merge it, ask the guide again, apply the fixes once nothing is
 // left to ask. Exactly one question is ever on screen.
 const handleGuidedAnswer = async (decision: Record<string, unknown>) => {
  if (!sessionId) return
  const merged: Record<string, unknown> = { ...guidedDecisions }
  for (const [k, v] of Object.entries(decision)) {
   merged[k] = (v && typeof v === 'object' && !Array.isArray(v))
    ? { ...((merged[k] as Record<string, unknown>) ?? {}), ...(v as Record<string, unknown>) }
    : v
  }
  setError(null)
  setGuidedBusy(true)
  try {
   const touched = mappingTouchedRef.current ? mapping : null
   const { report } = await previewGuidedReading(sessionId, merged, touched)
   setGuidedDecisions(merged)
   setGuided(report)
   adoptGuidedMapping(report)
   await settleGuided(sessionId, report, merged, touched)
  } catch (e: unknown) {
   setError(errorDetail(e) || t('guide.err_apply'))
  } finally {
   setGuidedBusy(false)
  }
 }

 // "Use my file exactly as it is": back to the original, and no more fixes on
 // the person's behalf for this session.
 const handleGuidedUndo = async () => {
  if (!sessionId) return
  setError(null)
  setGuidedBusy(true)
  try {
   guidedOffRef.current = true
   await applyGuidedReading(sessionId, { decisions: guidedDecisions, revert: true })
   appliedKeyRef.current = ''
   setGuidedApplied(null)
   setInspection(await inspectSession(sessionId))
  } catch (e: unknown) {
   guidedOffRef.current = false
   setError(errorDetail(e) || t('guide.err_apply'))
  } finally {
   setGuidedBusy(false)
  }
 }

 // The person picked a column in the advanced view: judge THEIR mapping.
 useEffect(() => {
  if (step !== 2 || !sessionId || !mappingTouchedRef.current) return
  let cancelled = false
  const timer = setTimeout(async () => {
   try {
    const { report } = await previewGuidedReading(sessionId, guidedDecisions, mapping)
    if (cancelled) return
    setGuided(report)
    await settleGuided(sessionId, report, guidedDecisions, mapping)
   } catch { /* the previous verdict stays on screen; confirm re-checks server-side */ }
  }, 350)
  return () => { cancelled = true; clearTimeout(timer) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [mapping, step, sessionId])

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
 setAutoMapped(null)
 trainLaunchedRef.current = false
 // True once training was handed off: the training screen owns `busy` then.
 let handedOff = false
 try {
 const session = await createSession(sessionName.trim() || undefined)
 setSessionId(session.session_id)

 await attachDataset(session.session_id, dsId)

 let insp = await inspectSession(session.session_id)

 // Guided upload: the guide has already read the file. A file that needs only
 // lossless fixes is fixed here and the wizard goes on; one with a real
 // question or an unusable one stops on the mapping step, where the question is.
 guidedOffRef.current = false
 mappingTouchedRef.current = false
 appliedKeyRef.current = ''
 setGuidedDecisions({})
 setShowAdvanced(false)
 setGuidedApplied(insp.guided_reading ?? null)
 let g: GuidanceReport | null = insp.guidance ?? null
 setGuided(g)
 if (g && (g.verdict === 'ask' || g.verdict === 'unusable')) {
  setInspection(insp)
  setDatasetId(dsId)
  setHistoryDays(historyDaysOf(insp))
  adoptGuidedMapping(g)
  setStep(2)
  return
 }
 if (g && g.verdict === 'ready' && g.fixes.length > 0) {
  try {
   const next = await settleGuided(session.session_id, g, {}, null)
   if (next) { insp = next; g = next.guidance ?? null; setGuided(g) }
  } catch (e: unknown) {
   setError(errorDetail(e) || t('guide.err_apply'))
   setInspection(insp)
   setDatasetId(dsId)
   setStep(2)
   return
  }
 }
 setInspection(insp)
 setDatasetId(dsId)
 const hist = historyDaysOf(insp)
 setHistoryDays(hist)
 // A horizon longer than a third of the history is unreliable: do not skip the
 // column step, so the person sees the note before anything is trained.
 const horizonTooLong = hist !== null && horizonDays > Math.floor(hist / HISTORY_FRACTION)

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
   const reusedMap: Record<string, string | null> = Object.fromEntries(
    CANONICAL_FIELDS.map(f => [f.name, previous.mapping[f.name] ?? null]),
   )
   setMapping(reusedMap)
   setReusedMapping({ from: previous.name, missing: [] })
   // The person confirmed exactly these columns last time and the file still
   // has every one of them: nothing is left to ask.
   if (!horizonTooLong && REQUIRED_FIELDS.every(f => reusedMap[f])) {
    handedOff = await autoConfirm(session.session_id, reusedMap)
    if (handedOff) return
   }
  } else {
   const suggestions: CanonicalMapping = insp.canonical_suggestions ?? {}
   const next: Record<string, string | null> =
    Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
   for (const field of CANONICAL_FIELDS) {
    const sug = suggestions[field.name]
    if (sug?.top && sug.confidence >= 0.7) next[field.name] = sug.top
   }
   // What the guide read from the CONTENT of the columns beats what their
   // names suggest: a file whose "fecha" holds quantities must not train.
   const gm = guidedMapping(g)
   if (gm) {
    for (const f of ['sku', 'date', 'demand', 'store'] as const) if (gm[f]) next[f] = gm[f]
   }
   setMapping(next)
   setReusedMapping(
    previous && missing.length > 0 ? { from: previous.name, missing } : null,
   )
   let certain = !horizonTooLong && (!previous || missing.length === 0) ? detectedWithCertainty(suggestions) : null
   // Names are certain but the content disagrees: ask, do not skip the step.
   if (certain && gm && REQUIRED_FIELDS.some(f => certain![f] !== gm[f])) certain = null
   if (certain) {
    handedOff = await autoConfirm(session.session_id, certain)
    if (handedOff) return
   }
  }
 }

 setStep(2)
 } catch (e: unknown) {
 setError(errorDetail(e) || t('qs.reuse_err_attach'))
 setStep(1)
 } finally {
 if (!handedOff) setBusy(false)
 }
 }

 // Skips the mapping step: confirms the mapping and launches training. When the
 // gate still has questions or blocks the file, nothing launches and the person
 // lands on the mapping step, where those answers live.
 const autoConfirm = async (
  sid: string, map: Record<string, string | null>,
 ): Promise<boolean> => {
  const launched = await confirmMapping(sid, map, true)
  if (launched) {
   setAutoMapped(map)
   setMapping(map)
  } else {
   setStep(2)
  }
  return launched
 }

 // "Change columns" on the training screen: the file is already on the server,
 // so open a fresh session over it with the mapping the person is looking at,
 // and retire the poll loop of the run that was started on the skipped step.
 const handleChangeColumns = () => {
  if (!datasetId) return
  pollGenRef.current++
  setError(null)
  setTrainMsg('')
  setTrainPct(null)
  setMultiPeriod(false)
  setRetryNote(true)
  setStep(1)
  void startFromDataset(datasetId, true)
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
 setError(errorDetail(e) || t('qs.clone_err'))
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
 const msg = errorDetail(e) || t('qs.err_upload')
 setError(msg)
 setFileName(null)
 setBusy(false)
 }
 }

 // ── Step 2: Confirm columns → trigger training ───────────────────────────────
 const handleConfirm = async () => {
 if (!sessionId || !inspection) return
 await confirmMapping(sessionId, mapping, false)
 }

 // Returns true when training was launched. `detached` hands the progress poll
 // off without awaiting it (the auto-confirm path returns to its caller at once).
 const confirmMapping = async (
  sessionId: string, mapping: Record<string, string | null>, detached: boolean,
 ): Promise<boolean> => {
 setError(null)
 setBusy(true)

 try {
 // POST canonical columns mapping
 await chooseColumnsCanonical(sessionId, {
  canonical_mapping: mapping,
  defaults_override: {},
 })

 // The gate, re-run against the mapping the user just confirmed. Everything
 // before this point was judged from DETECTED columns — a guess. This is the
 // same verdict `POST /train` enforces, so asking it here is the difference
 // between a question the user can answer and a refusal they cannot.
 //
 // Deliberately BEFORE `setStep(3)`: showing "el sistema está aprendiendo"
 // and only then discovering the run is refused is exactly what the removed
 // "continuar de todos modos" link did, and the reason it had to go.
 const liveGate = await getDataGate(sessionId, { silent: true })
 setGate(liveGate)

 // Nothing can be done about these, so there is nothing to ask. Stay on the
 // mapping screen, where DataIssuesPanel says why.
 if ((liveGate.blocking_fatal?.length ?? 0) > 0) {
  setBusy(false)
  return false
 }

 // Fixable, and unanswered: the questions have just appeared below the
 // mapping. Nothing started, so nothing has to be undone.
 const stillUnresolved = (liveGate.unresolved ?? []).filter(
  issueType => !remediationChoices[issueType],
 )
 if (stillUnresolved.length > 0) {
  setBusy(false)
  return false
 }

 // Only send answers to findings the file STILL has. A choice made against an
 // earlier mapping (say "keep the last row" for duplicates, before the user
 // mapped a second key column that made the duplicates disappear) is stale:
 // the backend refuses it with `remediation_not_offered`, which used to
 // surface as the raw token "duplicates=duplicates_keep_last".
 const liveOffered = new Set(
  (liveGate.issues ?? [])
   .filter(i => (i.remediations?.length ?? 0) > 0)
   .map(i => i.type),
 )
 const liveChoices = Object.fromEntries(
  Object.entries(remediationChoices).filter(([issueType]) => liveOffered.has(issueType)),
 )
 if (Object.keys(liveChoices).length !== Object.keys(remediationChoices).length) {
  setRemediationChoices(liveChoices)
 }
 if (Object.keys(liveChoices).length > 0) {
  await setRemediations(sessionId, liveChoices)
 }

 setStep(3)

 // POST features config. `holiday_country` decides whose public holidays the
 // model learns from; without it every tenant trained on one fixed calendar.
 await setFeatures(sessionId, {
 lags: [1, 7, 14, 28],
 rolling: [7, 14, 28],
 diffs: [1],
 calendar: true,
 ewm_spans: [7, 14],
 holiday_country: holidayCountry,
 })

 // POST models config
 // `global_lgbm` leads the list: one model fitted across the whole catalogue,
 // which is what gives a short or newly-launched SKU a usable forecast at all.
 await setModels(sessionId, ['global_lgbm', 'lightgbm', 'prophet', 'croston', 'xgboost'])

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
 // One source of truth for what StockAI assumes (src/lib/inventoryDefaults.ts,
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
 if (detached) void pollFamily(res.job_id, res.family)
 else await pollFamily(res.job_id, res.family)
 return true
 } catch (e: unknown) {
 const msg = errorDetail(e) || t('qs.err_config')
 setError(msg)
 setBusy(false)
 // Nothing was launched, so the "el sistema está aprendiendo" screen is a
 // lie — and it is the screen with no controls on it. Send the user back to
 // the mapping, where the error, the column selectors and any gate questions
 // all are. The gate can still refuse here if the file changed underneath us
 // between the check and the launch.
 if (!trainLaunchedRef.current) {
  setStep(2)
  if (sessionId) {
   try { setGate(await getDataGate(sessionId, { silent: true })) }
   catch { /* the message above already says what failed */ }
  }
 }
 return false
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
 // Tell the top-bar pill right away instead of at its next idle beat.
 trainingCtx?.refresh()
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
 const gen = ++pollGenRef.current

 const poll = async (): Promise<void> => {
 // The user left, or asked to change the columns (a newer run replaces
 // this one). Stop polling and, above all, do not navigate.
 if (unmountedRef.current || gen !== pollGenRef.current) return
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
 if (unmountedRef.current || gen !== pollGenRef.current) return
 await planningCtx?.reload()
 if (unmountedRef.current || gen !== pollGenRef.current) return
 // Land on the forecast of the run the user just waited for: the first
 // thing they want to see is what the model predicted, not the
 // purchasing panel. `?session=` is honoured once by /pronosticos.
 router.push(`/pronosticos?session=${encodeURIComponent(baseJob.session_id)}`)
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
 if (unmountedRef.current || gen !== pollGenRef.current) return
 return poll()
 } catch (e: unknown) {
 const msg = errorDetail(e) || t('qs.err_status')
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

 // Back to step 1 with a clean slate. Reached from the mapping step when the
 // file cannot train: continuing is pointless, so the way out is a new file.
 const handleStartOver = () => {
 setStep(1)
 setBusy(false)
 setError(null)
 setFileName(null)
 setSessionId(null)
 setDatasetId(null)
 setInspection(null)
 setRetryNote(false)
 setReusedMapping(null)
 setCsvWarnings([])
 setCsvIssues([])
 setGuided(null)
 setGuidedApplied(null)
 setGuidedDecisions({})
 setShowAdvanced(false)
 mappingTouchedRef.current = false
 appliedKeyRef.current = ''
 setMapping(Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null])))
 }

 // ── Preview table (first 3 rows sample) ─────────────────────────────────────
 function PreviewTable() {
 if (!inspection) return null
 const profile = inspection.profile
 const cols = profile.columns.slice(0, 5)
 const maxRows = 3
 if (narrow) return (
 <div style={{ marginTop: 16 }}>
 <p style={{ fontSize: 13, color: 'var(--dim)', marginBottom: 8 }}>{t('qs.preview')}</p>
 <ul style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
 {cols.map((c, i) => (
  <li key={c.name} style={{ padding: '8px 12px', borderTop: i ? '1px solid var(--border)' : 'none', minWidth: 0 }}>
  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--dim)' }}>{c.name}</div>
  <div style={{ fontSize: 13.5, color: 'var(--text)', overflowWrap: 'anywhere' }}>
   {Array.from({ length: maxRows }).map((_, k) => String(c.sample?.[k] ?? '—')).join(' · ')}
  </div>
  </li>
 ))}
 </ul>
 {profile.columns.length > 5 && (
 <p style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6 }}>
 + {profile.columns.length - 5} {t('qs.more_columns')}
 </p>
 )}
 </div>
 )
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

 // Not one product in this file can reach the engine's min_history, so training
 // it can only end in `no_models_trained`. The rule lives in the profiler — the
 // frontend only reacts to the flag, so the threshold has one owner.
 // Fatal only: nothing the user can answer changes the verdict, so the screen
 // offers another file. Once the gate has run against the confirmed mapping it
 // is the authority — the profiler's flag was a guess from detected columns.
 const blockedByData = gate
 ? (gate.blocking_fatal?.length ?? 0) > 0
 : (inspection?.profile.data_quality?.blocking === true ||
    (inspection?.profile.data_quality?.issues ?? []).some(i => i.blocking === true))

 // Fixable and unanswered. Not the same as blocked: there IS a way forward,
 // and it is one radio button away — so the confirm button stays visible and
 // simply cannot fire until every question has an answer.
 const unansweredFixable = (gate?.issues ?? [])
 .filter(i => i.classification === 'blocking_fixable' && (i.remediations?.length ?? 0) > 0)
 .filter(i => !remediationChoices[i.type])
 .length

 const missingRequired = CANONICAL_FIELDS.filter(f => f.required).some(f => !mapping[f.name])

 // The guide still has a question open, or says the file cannot be used with
 // this mapping: confirming would train on a reading nobody settled.
 const guidedStops = guided !== null && guided.verdict !== 'ready'
 const confirmBlocked = busy || guidedBusy || missingRequired || unansweredFixable > 0 || guidedStops

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
 padding: narrow ? '0 0 24px' : '20px 20px 48px',
 }}>
 <div style={{ width: '100%', maxWidth: 580 }}>

 {/* Same nav entry as /data — the two routes are tabs of each other. */}
 <DataTabs style={{ marginBottom: narrow ? 18 : 32 }} />

 {/* Header */}
 <div style={{ textAlign: 'center', marginBottom: narrow ? 20 : 32 }}>
 {/* The top bar says "Mis ventas" and the tab strip "Cargar ventas": only the promise here. */}
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
 padding: narrow ? 16 : 32,
 }}>

 {/* ── Step 1 ──────────────────────────────────────────────────────── */}
 {step === 1 && (
 <div>
 {/* No "Sube tus ventas" heading here: the page title above says it and
 the step bar's first label says it again. */}
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
 {t('qs.upload_desc')}
 {' '}<strong style={{ color: 'var(--text)' }}>{t('qs.upload_desc_bold')}</strong>
 </p>

 {/* Source selector: upload a new file vs reuse a previously uploaded
 dataset. The reuse tab only exists once the tenant has datasets. */}
 {(datasets.length > 0 || clonableSessions.length > 0) && (
 <div data-tour="qs.tabs" style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
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
  ...(narrow ? { minHeight: 44, fontSize: 14, padding: '0 6px' } : {}),
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
 <UploadGuide kind="sales" />
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

 {/* Plan settings: name + horizon + detail + holiday calendar. Closed by
 default — the defaults (shown beside the title) fit most files — and
 applied to the file-upload path, the reuse tabs and the demo alike. */}
 <details data-tour="qs.options" style={{ marginTop: 16 }}>
 <summary style={{
 cursor: 'pointer', fontSize: 13, fontWeight: 600, color: 'var(--text)',
 display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
 ...(narrow ? { minHeight: 44 } : { padding: '4px 0' }),
 }}>
 {t('qs.options_title')}
 <span style={{ fontWeight: 400, color: 'var(--dim)', fontSize: 12 }}>
 {[
 spanText(horizonDays, t),
 t(GRANULARITY_OPTIONS.find(o => o.value === granularity)?.labelKey ?? 'qs.plan_granularity_auto'),
 t(HOLIDAY_COUNTRIES.find(c => c.code === holidayCountry)?.labelKey ?? 'qs.country_CR'),
 ].join(' · ')}
 </span>
 </summary>
 <div style={{ marginTop: 14 }}>
 <PlanSettings
 name={sessionName} onName={setSessionName}
 horizonDays={horizonDays} onHorizonDays={setHorizonDays}
 granularity={granularity} onGranularity={setGranularity}
 country={holidayCountry} onCountry={chooseCountry}
 busy={busy}
 />
 </div>
 </details>

 {/* Shared between both tabs: upload errors AND attach/inspect errors
 from the reuse path land here. */}
 {error && (
 <div style={{
 marginTop: 12, padding: '10px 14px',
 background: '#fee2e2', borderRadius: 8,
 fontSize: 13, color: '#B94A4A',
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

 {/* One-click demo: see the semaphore without preparing any file */}
 <div data-tour="qs.demo" style={{
 marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border)',
 textAlign: 'center',
 }}>
 <p style={{ fontSize: 13, color: 'var(--dim)', margin: '0 0 10px' }}>
 {t('qs.demo_prompt')}
 </p>
 <TrainingBudgetNote />
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
 ...(narrow ? { minHeight: 48, width: '100%' } : {}),
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
 {!guided && (
 <>
 <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
 {t('qs.confirm_title')}
 </h2>
 <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
 {t('qs.confirm_desc')}
 </p>
 </>
 )}
 {inspection.guidance_error && !guided && (
 <div style={{ marginBottom: 16, padding: '8px 14px', background: 'rgba(217,119,6,0.07)',
  border: '1px solid #d9770655', borderRadius: 8, fontSize: 13, color: 'var(--text)', lineHeight: 1.55 }}>
  {t('guide.error_unavailable')}
 </div>
 )}
 {guided && (
 <div style={{ marginBottom: 20 }}>
  <GuidedReading
   report={guided}
   applied={guidedApplied}
   busy={busy || guidedBusy}
   onAnswer={handleGuidedAnswer}
   onUndo={handleGuidedUndo}
   onPickAnother={handleStartOver}
   horizonMax={guided.summary
    ? spanText(Math.max(1, Math.floor(guided.summary.history_days / HISTORY_FRACTION)), t)
    : null}
  />
  <button type="button" onClick={() => setShowAdvanced(v => !v)} aria-expanded={showAdvanced}
   style={{ marginTop: 14, background: 'none', border: 'none', padding: 0, cursor: 'pointer',
    fontSize: 13, color: 'var(--accent)', textDecoration: 'underline' }}>
   {t(showAdvanced ? 'guide.advanced_hide' : 'guide.advanced_show')}
  </button>
  {showAdvanced && (
   <p style={{ fontSize: 12.5, color: 'var(--dim)', margin: '6px 0 0', lineHeight: 1.5 }}>{t('guide.advanced_note')}</p>
  )}
 </div>
 )}

 {/* Calm warning, never a block: the engine can forecast this horizon, the
 numbers just get less reliable the further past the history they reach. */}
 {historyDays !== null && horizonDays > Math.floor(historyDays / HISTORY_FRACTION) && (
 <div style={{
 marginBottom: 16, padding: '8px 14px', background: 'rgba(217,119,6,0.07)',
 border: '1px solid #d9770655', borderRadius: 8, fontSize: 13, color: 'var(--text)', lineHeight: 1.55,
 }}>
 {t('qs.horizon_history_warn', {
  history: spanText(historyDays, t),
  max: spanText(Math.max(1, Math.floor(historyDays / HISTORY_FRACTION)), t),
  chosen: spanText(horizonDays, t),
 })}
 </div>
 )}

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

 {(!guided || showAdvanced || missingRequired) && (
 <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
 {CANONICAL_FIELDS.map(field => {
  const allCols = inspection.profile.columns.map(c => c.name)
  const val     = mapping[field.name]
  const isNone  = val === null

  return (
  <div key={field.name} style={{
   display: 'grid', gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : '1fr 1fr',
   alignItems: 'center', gap: narrow ? 6 : 12,
   padding: '10px 0',
   borderBottom: '1px solid var(--border)',
  }}>
   <div>
   <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
    {field.required && <span style={{ color: '#C0504D', marginRight: 4 }}>★</span>}
    {t(field.labelKey)}
   </span>
   {!field.required && isNone && (
    <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
    {t('qs.default_prefix')}{' '}
    {'defaultKey' in field
     ? t((field as { defaultKey: string }).defaultKey)
     : (field as { defaultLiteral?: string }).defaultLiteral}
    </div>
   )}
   </div>
   <select
   value={val ?? '__none__'}
   onChange={e => {
    const v = e.target.value
    mappingTouchedRef.current = true
    setMapping(prev => ({ ...prev, [field.name]: v === '__none__' ? null : v }))
   }}
   style={{
    padding: '8px 10px', borderRadius: 8,
    border: `1px solid ${field.required && !val ? '#C0504D' : 'var(--border)'}`,
    background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
    cursor: 'pointer',
    ...(narrow ? { fontSize: 16, minHeight: 44, width: '100%', minWidth: 0, boxSizing: 'border-box', borderRadius: 10 } : {}),
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
 )}

 {!guided && <PreviewTable />}

 {guidedStops && !showAdvanced ? null : (<>

 {/* The profiler has always found these; nothing used to show them. This is
     the last screen where the user can still go fix the file. */}
 {/* Only the findings nobody is being asked about. A finding with options is
     rendered ONCE, by RemediationChoices below, with its ways out — showing
     it here too produced the contradiction "puedes continuar igual" sitting
     directly above "tienes que decidir algo antes de seguir". */}
 <DataIssuesPanel
  issues={(gate?.issues ?? inspection.profile.data_quality?.issues ?? [])
   .filter(i => (i.remediations?.length ?? 0) === 0)}
  granularity={inspection.granularity}
 />

 {/* The questions. Only appear once the gate has run against the mapping the
     user confirmed — before that the column reading is a guess, and asking
     someone to decide about a problem we may have imagined is noise. */}
 <RemediationChoices
  issues={gate?.issues ?? []}
  chosen={remediationChoices}
  onChoose={(issueType, code) =>
   setRemediationChoices(prev => ({ ...prev, [issueType]: code }))}
  disabled={busy}
 />

 {error && (
 <div style={{ marginTop: 16, padding: '10px 14px', background: '#fee2e2',
  borderRadius: 8, fontSize: 13, color: '#B94A4A' }}>
  {error}
 </div>
 )}

 {/* No bypass. There used to be a "Continuar de todos modos" link here,
     justified by the profiler judging the file from DETECTED columns —
     the user might know better. That justification died when the gate
     started re-running on the CONFIRMED mapping at launch: the button led
     to the training screen, sat there as if something had started, and
     then printed the backend's refusal. An escape hatch that cannot
     escape is worse than no escape hatch.

     Nothing is lost by removing it. The column selectors are on this same
     screen: a user who thinks we read the wrong column fixes the mapping
     and the file is judged again. That is the real answer to "I know
     better" — correcting the reading, not overriding the verdict. */}
 {blockedByData ? (
 <>
  <button
  onClick={handleStartOver}
  disabled={busy}
  style={{
   marginTop: 28, width: '100%', padding: '14px 0',
   background: 'var(--accent)', color: '#fff',
   border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 700,
   cursor: busy ? 'not-allowed' : 'pointer',
   opacity: busy ? 0.7 : 1,
   transition: 'opacity 0.15s',
  }}
  >
  {t('qs.pick_another_file')}
  </button>
  <p style={{
   marginTop: 10, fontSize: 12, color: 'var(--dim)',
   textAlign: 'center', lineHeight: 1.5,
  }}>
  {t('qs.blocked_remap_hint')}
  </p>
 </>
 ) : narrow ? (
 <>
 <TrainingBudgetNote />
 {unansweredFixable > 0 && !busy && (
 <p style={{ marginTop: 16, fontSize: 13, color: 'var(--dim)', textAlign: 'center', lineHeight: 1.5 }}>
  {t('gate.answer_first').replace('{count}', String(unansweredFixable))}
 </p>
 )}
 <StickyActionBar>
 <button type="button" className="mobile-btn mobile-btn-primary" onClick={handleConfirm}
  disabled={confirmBlocked}>
  {busy ? t('qs.processing') : t('qs.looks_good')}
 </button>
 </StickyActionBar>
 </>
 ) : (
 <>
 <button
 onClick={handleConfirm}
 disabled={confirmBlocked}
 style={{
  marginTop: 28, width: '100%', padding: '14px 0',
  background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 700,
  cursor: confirmBlocked ? 'not-allowed' : 'pointer',
  opacity: (busy || unansweredFixable > 0 || guidedStops) ? 0.7 : 1,
  transition: 'opacity 0.15s',
 }}
 >
 {busy ? t('qs.processing') : t('qs.looks_good')}
 </button>
 <TrainingBudgetNote />
 {/* Why the button is dead, said next to the button. A disabled control
     with no explanation is how a user concludes the app is broken. */}
 {unansweredFixable > 0 && !busy && (
 <p style={{
  marginTop: 8, fontSize: 12, color: 'var(--dim)',
  textAlign: 'center', lineHeight: 1.5,
 }}>
  {t('gate.answer_first').replace('{count}', String(unansweredFixable))}
 </p>
 )}
 </>
 )}
 </>)}
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
 {guidedApplied && (
 <p style={{ fontSize: 12.5, color: 'var(--dim)', margin: '-20px 0 24px', lineHeight: 1.5 }}>
 {t('guide.applied_note', { n: guidedApplied.fixes.length })}
 </p>
 )}

 {!error && <TrainingLoader message={trainMsg} pct={trainPct} multiPeriod={multiPeriod} />}

 {/* The mapping step was skipped because the columns were certain: say
 which ones were used, and keep the way back to the mapping open. */}
 {autoMapped && (
 <div style={{
 marginTop: 20, padding: '10px 14px', borderRadius: 8,
 border: '1px solid var(--border)', background: 'var(--surface-2)',
 fontSize: 13, color: 'var(--text)', lineHeight: 1.55, textAlign: 'left',
 }}>
 {t('qs.mapping_auto_summary', {
  sku: autoMapped.sku ?? '', date: autoMapped.date ?? '', demand: autoMapped.demand ?? '',
 })}
 {' '}
 <button
  type="button"
  onClick={handleChangeColumns}
  style={{
  background: 'none', border: 'none', padding: 0, cursor: 'pointer',
  color: 'var(--accent)', fontWeight: 700, fontSize: 13, textDecoration: 'underline',
  ...(narrow ? { minHeight: 44 } : {}),
  }}
 >
  {t('qs.change_columns')}
 </button>
 </div>
 )}

 {error && (
 <div style={{ marginTop: 20 }}>
 <div style={{
 padding: '14px 18px',
 background: '#fee2e2', borderRadius: 10,
 fontSize: 14, color: '#B94A4A',
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
 ...(narrow ? { minHeight: 48, width: '100%' } : {}),
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

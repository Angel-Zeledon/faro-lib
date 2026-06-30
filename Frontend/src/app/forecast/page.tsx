'use client'
import { useState, useEffect, useRef, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  createSession, attachDataset, inspectSession,
  chooseColumns, setFeatures, setModels, setValidationConfig,
  startTraining, getJob, getAvailableModels, getMetrics,
  listDataSources, getDatasetAnalysis, getModelHyperparams,
  getSession, getConfigSummary, generateReport, downloadReportBlob,
  getForecastSeries, saveForecastOverrides,
} from '@/lib/api'
import type {
  InspectionResult, FieldSchema, MetricsResponse, JobResponse,
  DataSource, DatasetAnalysis, HyperparamDef, SessionInfo, OutlierConfig,
  ForecastOverride, ForecastSeries,
} from '@/lib/types'
import OverrideCell from '@/components/forecast/OverrideCell'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import HelpTip from '@/components/ui/HelpTip'
import Spinner from '@/components/ui/Spinner'
import { useActiveSession } from '@/contexts/ActiveSessionContext'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  Check, Database, Sliders, Cpu, FileSpreadsheet,
  GitBranch, Play, BarChart2, Network, AlertTriangle, RefreshCw,
  TrendingUp, Settings, X, Download, FileText, Table,
} from 'lucide-react'

// ── Wizard step definitions ────────────────────────────────────────────────────
const STEPS = [
  { id: 1, labelKey: 'forecast.step1_label',    Icon: Database,   descKey: 'forecast.step1_desc' },
  { id: 2, labelKey: 'forecast.step2_label',    Icon: TrendingUp, descKey: 'forecast.step2_desc' },
  { id: 3, labelKey: 'forecast.step3_label',    Icon: Sliders,    descKey: 'forecast.step3_desc' },
  { id: 4, labelKey: 'forecast.step4_label',    Icon: GitBranch,  descKey: 'forecast.step4_desc' },
  { id: 5, labelKey: 'forecast.step5_label',    Icon: Cpu,        descKey: 'forecast.step5_desc' },
  { id: 6, labelKey: 'forecast.step6_label',    Icon: Network,    descKey: 'forecast.step6_desc' },
  { id: 7, labelKey: 'forecast.step7_label',    Icon: GitBranch,  descKey: 'forecast.step7_desc' },
  { id: 8, labelKey: 'forecast.step8_label',    Icon: Play,       descKey: 'forecast.step8_desc' },
  { id: 9, labelKey: 'forecast.step9_label',    Icon: BarChart2,  descKey: 'forecast.step9_desc' },
]

const TOTAL = STEPS.length

// ── Status → step mapping ─────────────────────────────────────────────────────
function statusToStep(status: SessionInfo['status']): number {
  switch (status) {
    case 'DRAFT':                return 1
    case 'DATASET_LOADED':       return 2
    case 'INSPECTED':            return 3
    case 'COLUMNS_CONFIGURED':   return 4
    case 'FEATURES_CONFIGURED':  return 5
    case 'MODELS_CONFIGURED':    return 7
    case 'QUEUED':               return 8
    case 'RUNNING':              return 8
    case 'COMPLETED':            return 9
    case 'FAILED':               return 8
    case 'CANCELLED':            return 8
    default:                     return 1
  }
}

// ── Smart defaults ─────────────────────────────────────────────────────────────
const SMART_FEATURES = {
  lags:      [1, 7, 14, 28],
  rolling:   [7, 14, 28],
  diffs:     [1],
  calendar:  true,
  ewm_spans: [14],
}

const SMART_MODELS = {
  mode: 'selected' as const,
  selected_models: ['lightgbm', 'prophet', 'ets'],
  hyperparameters: {} as Record<string, Record<string, unknown>>,
  auto_select_best: true,
  selection_metric: 'wape',
}

const SMART_VALIDATION = {
  train_ratio:     0.8,
  walk_forward:    true,
  wfv_splits:      3,
  min_history:     20,
  seasonal_period: 7,
  horizon:         14,
}

function smartColumns(inspection: InspectionResult) {
  const opts = inspection.column_options
  return {
    target_column: opts.target_candidates[0] ?? '',
    date_column:   opts.date_candidates[0]   ?? '',
    sku_column:    opts.group_candidates[0]  ?? null,
    exogenous:     [] as string[],
    transforms:    {} as Record<string, { impute: string; encode: string; scale: string }>,
  }
}

const addSteps = (s: Set<number>, ...nums: number[]) => new Set(Array.from(s).concat(nums))

// ── Saved configs type ────────────────────────────────────────────────────────
interface SavedConfigs {
  columns?:    Record<string, unknown>
  features?:   Record<string, unknown>
  models?:     Record<string, unknown>
  validation?: Record<string, unknown>
}

// ── Default features schema ───────────────────────────────────────────────────
const DEFAULT_FEATURES_SCHEMA: Record<string, FieldSchema> = {
  lags:      { type: 'int_list',   default: [1, 7, 14, 28], label: 'forecast.feat_lags_label' },
  rolling:   { type: 'int_list',   default: [7, 14, 28],    label: 'forecast.feat_rolling_label' },
  diffs:     { type: 'int_list',   default: [1],            label: 'forecast.feat_diffs_label' },
  calendar:  { type: 'bool',       default: true,           label: 'forecast.feat_calendar_label' },
  ewm_spans: { type: 'int_list',   default: [],             label: 'forecast.feat_ewm_label' },
}

const MODEL_DESC_KEYS: Record<string, string> = {
  lightgbm: 'forecast.model_desc_lightgbm',
  xgboost:  'forecast.model_desc_xgboost',
  prophet:  'forecast.model_desc_prophet',
  arima:    'forecast.model_desc_arima',
  ets:      'forecast.model_desc_ets',
  croston:  'forecast.model_desc_croston',
  lstm:     'forecast.model_desc_lstm',
}

const MODEL_EXOG_SUPPORT: Record<string, boolean> = {
  lightgbm: true, xgboost: true, prophet: true,
  arima: true, ets: false, croston: false, lstm: true,
}

// ── Step indicator ─────────────────────────────────────────────────────────────
function StepIndicator({ current, completed }: { current: number; completed: Set<number> }) {
  const { t } = useLanguage()
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      {STEPS.map((s, i) => {
        const done   = completed.has(s.id) || s.id < current
        const active = s.id === current
        return (
          <div key={s.id} style={{ display: 'flex', alignItems: 'center', flex: i < STEPS.length - 1 ? 1 : undefined }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div style={{
                width: 26, height: 26, borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: done ? '#22c55e' : active ? 'var(--accent)' : 'var(--surface-2)',
                border: `2px solid ${done ? '#22c55e' : active ? 'var(--accent)' : 'var(--border)'}`,
                fontSize: 10, fontWeight: 700,
                color: done || active ? '#fff' : 'var(--dim)',
                flexShrink: 0, transition: 'all 0.2s',
              }}>
                {done ? <Check size={11} strokeWidth={3} /> : s.id}
              </div>
              <div style={{
                fontSize: 10, fontWeight: active ? 600 : 400,
                color: active ? 'var(--accent)' : done ? '#22c55e' : 'var(--dim)',
                marginTop: 3, whiteSpace: 'nowrap',
              }}>
                {t(s.labelKey)}
              </div>
            </div>
            {i < STEPS.length - 1 && (
              <div style={{
                flex: 1, height: 2, margin: '0 3px', marginBottom: 16,
                background: done ? '#22c55e44' : 'var(--border)',
                transition: 'all 0.2s',
              }} />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: Select Data Source ────────────────────────────────────────────────
function Step1({ onNext }: { onNext: (id: string, inspection: InspectionResult) => void }) {
  const { t } = useLanguage()
  const [sources,  setSources]  = useState<DataSource[]>([])
  const [loading,  setLoading]  = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [name,     setName]     = useState('')
  const [busy,     setBusy]     = useState(false)
  const [status,   setStatus]   = useState<string | null>(null)
  const [err,      setErr]      = useState<string | null>(null)
  const [loadErr,  setLoadErr]  = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setLoadErr(null)
    listDataSources(0, 100)
      .then(r => setSources(r.items.filter(s => s.connection_status === 'connected')))
      .catch((e: unknown) => {
        setSources([])
        setLoadErr(e instanceof Error ? e.message : t('forecast.step1_load_sources_error'))
      })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const go = async () => {
    if (!selected) return
    setBusy(true); setErr(null)
    try {
      setStatus(t('forecast.step1_status_creating'))
      const session = await createSession(name.trim() || undefined)
      setStatus(t('forecast.step1_status_attaching'))
      await attachDataset(session.session_id, selected)
      setStatus(t('forecast.step1_status_inspecting'))
      const inspection = await inspectSession(session.session_id)
      onNext(session.session_id, inspection)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false); setStatus(null) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ background: 'var(--surface-2)', borderRadius: 10, padding: 18, border: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{t('forecast.step1_title')}</div>
          <button onClick={load} style={{ background: 'transparent', border: 'none', color: 'var(--dim)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
            <RefreshCw size={12} /> {t('forecast.step1_refresh')}
          </button>
        </div>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><Spinner size={20} /></div>
        ) : loadErr ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#ef4444', fontSize: 13 }}>
            {loadErr}
          </div>
        ) : sources.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--dim)', fontSize: 13 }}>
            {t('forecast.step1_no_sources_prefix')} <b>{t('forecast.step1_no_sources_data_link')}</b> {t('forecast.step1_no_sources_suffix')}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
            {sources.map(src => (
              <div key={src.id} onClick={() => setSelected(src.id === selected ? null : src.id)}
                style={{
                  padding: '10px 14px', borderRadius: 8, cursor: 'pointer', transition: 'all 0.15s',
                  background: selected === src.id ? 'var(--accent-dim)' : 'var(--surface)',
                  border: `1px solid ${selected === src.id ? 'var(--accent)' : 'var(--border)'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {src.source_type === 'sql'
                    ? <Database size={15} color="var(--accent)" />
                    : <FileSpreadsheet size={15} color="#22c55e" />}
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: selected === src.id ? 'var(--accent)' : 'var(--text)' }}>
                      {src.name}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>
                      {src.source_type === 'sql'
                        ? `${src.sql_config?.engine} · ${src.sql_config?.host}/${src.sql_config?.database}`
                        : `${src.file_type?.toUpperCase()} · ${src.row_count ? src.row_count.toLocaleString() + ' ' + t('forecast.step1_rows_suffix') : t('forecast.step1_unknown_size')}`}
                    </div>
                  </div>
                </div>
                {selected === src.id && <Check size={15} color="var(--accent)" strokeWidth={3} />}
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ background: 'var(--surface-2)', borderRadius: 10, padding: 18, border: '1px solid var(--border)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>{t('forecast.step1_session_name')}</div>
        <input className="form-input" placeholder={t('forecast.step1_session_name_placeholder')}
          value={name} onChange={e => setName(e.target.value)} style={{ marginBottom: 0 }} />
      </div>
      {err && (
        <div style={{ padding: '10px 14px', borderRadius: 8, background: '#ef444415', border: '1px solid #ef444430', color: '#ef4444', fontSize: 13 }}>{err}</div>
      )}
      {status && (
        <div style={{ fontSize: 11, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <Spinner size={11} /> {status}
        </div>
      )}
      <Button variant="primary" loading={busy} disabled={!selected || busy} onClick={go} style={{ alignSelf: 'flex-start' }}>
        {t('forecast.step1_continue_btn')}
      </Button>
    </div>
  )
}

// ── Step 2: Dataset Analysis ──────────────────────────────────────────────────
const GAP_FILL_OPTIONS = [
  { value: 'leave',       labelKey: 'forecast.gap_fill_leave_label',       descKey: 'forecast.gap_fill_leave_desc' },
  { value: 'zero',        labelKey: 'forecast.gap_fill_zero_label',        descKey: 'forecast.gap_fill_zero_desc' },
  { value: 'mean',        labelKey: 'forecast.gap_fill_mean_label',        descKey: 'forecast.gap_fill_mean_desc' },
  { value: 'forward',     labelKey: 'forecast.gap_fill_forward_label',     descKey: 'forecast.gap_fill_forward_desc' },
  { value: 'interpolate', labelKey: 'forecast.gap_fill_interpolate_label', descKey: 'forecast.gap_fill_interpolate_desc' },
]

const OUTLIER_STRATEGIES = [
  { value: 'leave',           labelKey: 'forecast.outlier_leave_label',           descKey: 'forecast.outlier_leave_desc' },
  { value: 'winsorize_sigma', labelKey: 'forecast.outlier_winsorize_sigma_label', descKey: 'forecast.outlier_winsorize_sigma_desc' },
  { value: 'winsorize_pct',   labelKey: 'forecast.outlier_winsorize_pct_label',   descKey: 'forecast.outlier_winsorize_pct_desc' },
  { value: 'iqr_fence',       labelKey: 'forecast.outlier_iqr_fence_label',       descKey: 'forecast.outlier_iqr_fence_desc' },
  { value: 'remove',          labelKey: 'forecast.outlier_remove_label',          descKey: 'forecast.outlier_remove_desc' },
  { value: 'log1p',           labelKey: 'forecast.outlier_log1p_label',           descKey: 'forecast.outlier_log1p_desc' },
]

function defaultOutlierConfig(): OutlierConfig {
  return { strategy: 'leave', n_sigma: 3, percentile: 1, iqr_k: 1.5,
           per_sku_overrides: {}, per_sku_n_sigma: {}, per_sku_percentile: {}, per_sku_iqr_k: {} }
}

function Step2({
  sessionId, inspection, onNext,
}: { sessionId: string; inspection: InspectionResult; onNext: (gapFill: string, outlierCfg: OutlierConfig) => void }) {
  const { t } = useLanguage()
  const [analysis,      setAnalysis]      = useState<DatasetAnalysis | null>(null)
  const [loading,       setLoading]       = useState(true)
  const [err,           setErr]           = useState<string | null>(null)
  const [gapFillChoice, setGapFillChoice] = useState<string>('leave')
  const [outlierCfg,    setOutlierCfg]    = useState<OutlierConfig>(defaultOutlierConfig())
  const [perSkuOpen,    setPerSkuOpen]    = useState(false)

  useEffect(() => {
    getDatasetAnalysis(sessionId)
      .then(setAnalysis).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [sessionId])

  const profile = inspection.profile
  const stats   = profile.stats

  const seasonalColor: Record<string, string> = {
    none: 'var(--dim)', weak: '#f59e0b', moderate: '#0ea5e9', strong: '#22c55e',
  }
  const seasonalLabelKey: Record<string, string> = {
    none: 'forecast.seasonal_none', weak: 'forecast.seasonal_weak',
    moderate: 'forecast.seasonal_moderate', strong: 'forecast.seasonal_strong',
  }

  const granularityKeys: Record<string, string> = {
    Hour: 'forecast.granularity_hour', Day: 'forecast.granularity_day',
    Week: 'forecast.granularity_week', Month: 'forecast.granularity_month',
    Year: 'forecast.granularity_year', Quarter: 'forecast.granularity_quarter',
  }

  // Temporal granularity hierarchy
  const freqLabel = analysis?.temporal?.freq_label ?? profile.recommended.freq ?? ''
  const granularities = freqLabel.includes('H') || freqLabel.toLowerCase().includes('hour')
    ? ['Hour', 'Day', 'Week', 'Month', 'Year']
    : freqLabel === 'D' || freqLabel.toLowerCase().includes('day')
    ? ['Day', 'Week', 'Month', 'Year']
    : freqLabel === 'W' || freqLabel.toLowerCase().includes('week')
    ? ['Week', 'Month', 'Year']
    : freqLabel === 'M' || freqLabel.toLowerCase().includes('month')
    ? ['Month', 'Year']
    : freqLabel === 'Q' || freqLabel.toLowerCase().includes('quarter')
    ? ['Quarter', 'Year']
    : ['Day', 'Week', 'Month', 'Year']

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        {[
          { label: t('forecast.stat_rows'),       value: stats.n_rows?.toLocaleString() ?? '—',    color: 'var(--accent)' },
          { label: t('forecast.stat_columns'),    value: stats.n_cols?.toLocaleString() ?? '—',    color: '#0ea5e9' },
          { label: t('forecast.stat_skus'),       value: stats.n_skus?.toLocaleString() ?? '—',    color: '#22c55e' },
          { label: t('forecast.stat_duplicates'), value: analysis ? analysis.n_duplicates.toLocaleString() : '…',
            color: analysis?.n_duplicates ? '#ef4444' : '#22c55e' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ padding: '14px 16px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><Spinner size={20} /></div>}
      {err && <div style={{ padding: '10px 14px', borderRadius: 8, background: '#ef444415', border: '1px solid #ef444430', color: '#ef4444', fontSize: 12 }}>{t('forecast.analysis_unavailable')} {err}</div>}

      {analysis && (
        <>
          {analysis.temporal && Object.keys(analysis.temporal).length > 0 && (
            <div style={{ background: 'var(--surface-2)', borderRadius: 10, padding: 18, border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
                <TrendingUp size={13} color="var(--accent)" /> {t('forecast.temporal_overview_title')}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
                {[
                  { label: t('forecast.stat_date_range'),  value: `${analysis.temporal.date_min} → ${analysis.temporal.date_max}` },
                  { label: t('forecast.stat_periods'),     value: analysis.temporal.n_periods?.toLocaleString() ?? '—' },
                  { label: t('forecast.stat_native_freq'), value: analysis.temporal.freq_label ?? '—' },
                  { label: t('forecast.stat_memory'),      value: `${analysis.memory_mb} MB` },
                  { label: t('forecast.stat_gaps_detected'), value: analysis.temporal.gap_count > 0
                    ? <span style={{ color: '#f59e0b' }}>{analysis.temporal.gap_count} {t('forecast.gap_count_unit')}{analysis.temporal.gap_count !== 1 ? 's' : ''}</span>
                    : <span style={{ color: '#22c55e' }}>{t('forecast.none_label')}</span> },
                  { label: t('forecast.stat_seasonality'), value: analysis.seasonality
                    ? <span style={{ color: seasonalColor[analysis.seasonality.classification] ?? 'var(--dim)' }}>
                        {t(seasonalLabelKey[analysis.seasonality.classification]) ?? analysis.seasonality.classification}
                        {analysis.seasonality.dominant_period ? ` (${t('forecast.seasonal_period_label')} ${analysis.seasonality.dominant_period})` : ''}
                      </span>
                    : '—' },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <div style={{ fontSize: 10, color: 'var(--dim)', marginBottom: 2 }}>{label}</div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{value}</div>
                  </div>
                ))}
              </div>
              {/* Temporal granularity hierarchy */}
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                <div style={{ fontSize: 10, color: 'var(--dim)', marginBottom: 6 }}>{t('forecast.available_granularities_title')}</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {granularities.map(g => (
                    <span key={g} style={{
                      fontSize: 11, fontWeight: 500, padding: '2px 10px', borderRadius: 6,
                      background: 'var(--accent-dim)', color: 'var(--accent)',
                      border: '1px solid var(--accent)', opacity: 0.8,
                    }}>{t(granularityKeys[g]) ?? g}</span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {analysis.sku_stats && analysis.sku_stats.n_skus > 0 && (
            <div style={{ background: 'var(--surface-2)', borderRadius: 10, padding: 18, border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 12 }}>{t('forecast.sku_health_title')}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                {[
                  { label: t('forecast.stat_total_skus'),     value: analysis.sku_stats.n_skus,                warn: false },
                  { label: t('forecast.stat_intermittent'),   value: analysis.sku_stats.intermittent_count,   warn: analysis.sku_stats.intermittent_count > 0 },
                  { label: t('forecast.stat_short_series'),   value: analysis.sku_stats.short_series_count,   warn: analysis.sku_stats.short_series_count > 0 },
                  { label: t('forecast.stat_avg_zero_demand'),value: `${analysis.sku_stats.avg_zero_pct}%`,   warn: analysis.sku_stats.avg_zero_pct > 20 },
                ].map(({ label, value, warn }) => (
                  <div key={label}>
                    <div style={{ fontSize: 10, color: 'var(--dim)', marginBottom: 2 }}>{label}</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: warn ? '#f59e0b' : 'var(--text)' }}>{value}</div>
                  </div>
                ))}
              </div>
              {analysis.sku_stats.intermittent_count > 0 && (
                <div style={{ marginTop: 10, fontSize: 11, color: '#f59e0b', display: 'flex', gap: 6 }}>
                  <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />
                  {analysis.sku_stats.intermittent_count} {t('forecast.intermittent_sku_unit')}{analysis.sku_stats.intermittent_count !== 1 ? 's' : ''} {t('forecast.intermittent_sku_suggestion')}
                </div>
              )}
            </div>
          )}

          <div style={{ background: 'var(--surface-2)', borderRadius: 10, padding: 18, border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 12 }}>{t('forecast.column_types_title')}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {analysis.columns.map(col => (
                <div key={col.name} style={{
                  padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)',
                  background: 'var(--surface)', fontSize: 11,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span style={{ color: col.role === 'numeric' ? 'var(--accent)' : '#a78bfa', fontWeight: 600 }}>
                    {col.role === 'numeric' ? '#' : 'A'}
                  </span>
                  <span>{col.name}</span>
                  {col.null_pct > 0 && (
                    <span style={{ fontSize: 10, color: col.null_pct > 10 ? '#ef4444' : '#f59e0b' }}>
                      {col.null_pct}% {t('forecast.null_suffix')}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {profile.warnings.length > 0 && (
        <div style={{ padding: '12px 14px', borderRadius: 8, background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.2)' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#f59e0b', marginBottom: 6 }}>{t('forecast.dataset_warnings_title')}</div>
          {profile.warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 11, color: '#fbbf24', display: 'flex', gap: 6, marginTop: 3 }}>
              <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} /> {w}
            </div>
          ))}
        </div>
      )}

      {/* ── Gap-fill prompt (shown when temporal gaps are detected) ── */}
      {(profile.data_quality?.gap_fill_needed || (analysis?.temporal?.gap_count ?? 0) > 0) && (
        <div style={{ padding: '16px 18px', borderRadius: 10, background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.3)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#f59e0b', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={13} /> {t('forecast.missing_dates_title')}
            <HelpTip text={t('forecast.missing_dates_help')} size={13} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
            {analysis?.temporal?.gap_count ?? t('forecast.some_label')} {t('forecast.gap_count_unit')}{(analysis?.temporal?.gap_count ?? 1) !== 1 ? 's' : ''} {t('forecast.gaps_found_suffix')}
            {' '}{t('forecast.gap_fill_question')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {GAP_FILL_OPTIONS.map(opt => (
              <label key={opt.value} style={{
                display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer',
                padding: '8px 12px', borderRadius: 8,
                background: gapFillChoice === opt.value ? 'rgba(245,158,11,0.12)' : 'transparent',
                border: `1px solid ${gapFillChoice === opt.value ? 'rgba(245,158,11,0.5)' : 'var(--border)'}`,
                transition: 'all 0.15s',
              }}>
                <input
                  type="radio" name="gap_fill" value={opt.value}
                  checked={gapFillChoice === opt.value}
                  onChange={() => setGapFillChoice(opt.value)}
                  style={{ marginTop: 2 }}
                />
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: gapFillChoice === opt.value ? '#f59e0b' : 'var(--text)' }}>
                    {t(opt.labelKey)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--dim)' }}>{t(opt.descKey)}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* ── Outlier Treatment ── */}
      {(() => {
        const outlierInfo = profile.data_quality?.outliers
        const hasOutliers = outlierInfo && outlierInfo.total_count > 0
        const skuList = outlierInfo ? Object.entries(outlierInfo.per_sku) : []
        const hasMultiSku = skuList.length > 1
        const strategy = outlierCfg.strategy
        const paramColor = '#818cf8'

        return (
          <div style={{ padding: '16px 18px', borderRadius: 10, background: hasOutliers ? 'rgba(129,140,248,0.06)' : 'var(--surface-2)', border: `1px solid ${hasOutliers ? 'rgba(129,140,248,0.3)' : 'var(--border)'}` }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6, color: hasOutliers ? '#818cf8' : 'var(--text)' }}>
              <Sliders size={13} /> {t('forecast.outlier_treatment_title')}
              <HelpTip text={t('forecast.outlier_treatment_help')} size={13} />
              {hasOutliers && (
                <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 4, color: 'var(--dim)' }}>
                  — {outlierInfo!.total_count} {t('forecast.outlier_unit')}{outlierInfo!.total_count !== 1 ? 's' : ''} {t('forecast.outlier_detected_suffix')} ({outlierInfo!.total_pct}% {t('forecast.of_data_suffix')})
                </span>
              )}
              {!hasOutliers && (
                <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 4, color: 'var(--dim)' }}>
                  — {t('forecast.no_outliers_detected')}
                </span>
              )}
            </div>

            {/* Global strategy */}
            <div style={{ marginTop: 12, marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 6 }}>{t('forecast.global_strategy_label')}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                {OUTLIER_STRATEGIES.map(opt => (
                  <label key={opt.value} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer',
                    padding: '8px 10px', borderRadius: 7,
                    background: strategy === opt.value ? 'rgba(129,140,248,0.12)' : 'transparent',
                    border: `1px solid ${strategy === opt.value ? 'rgba(129,140,248,0.5)' : 'var(--border)'}`,
                    transition: 'all 0.15s',
                  }}>
                    <input type="radio" name="outlier_strategy" value={opt.value}
                      checked={strategy === opt.value}
                      onChange={() => setOutlierCfg(c => ({ ...c, strategy: opt.value }))}
                      style={{ marginTop: 2 }} />
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: strategy === opt.value ? '#818cf8' : 'var(--text)' }}>{t(opt.labelKey)}</div>
                      <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 1 }}>{t(opt.descKey)}</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Strategy parameters */}
            {strategy === 'winsorize_sigma' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('forecast.std_multiplier_label')}</span>
                <input type="number" className="form-input" value={outlierCfg.n_sigma} min={1} max={10} step={0.5}
                  onChange={e => setOutlierCfg(c => ({ ...c, n_sigma: parseFloat(e.target.value) || 3 }))}
                  style={{ width: 80, fontSize: 12 }} />
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>→ {t('forecast.clips_beyond_mean_prefix')} {outlierCfg.n_sigma}σ</span>
              </div>
            )}
            {strategy === 'winsorize_pct' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('forecast.tail_percentile_label')}</span>
                <input type="number" className="form-input" value={outlierCfg.percentile} min={0.1} max={10} step={0.5}
                  onChange={e => setOutlierCfg(c => ({ ...c, percentile: parseFloat(e.target.value) || 1 }))}
                  style={{ width: 80, fontSize: 12 }} />
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>→ {t('forecast.clips_bottom_top_prefix')} {outlierCfg.percentile}%</span>
              </div>
            )}
            {strategy === 'iqr_fence' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('forecast.iqr_multiplier_label')}</span>
                <input type="number" className="form-input" value={outlierCfg.iqr_k} min={0.5} max={5} step={0.5}
                  onChange={e => setOutlierCfg(c => ({ ...c, iqr_k: parseFloat(e.target.value) || 1.5 }))}
                  style={{ width: 80, fontSize: 12 }} />
                <span style={{ fontSize: 11, color: 'var(--dim)' }}>→ {t('forecast.fence_prefix')} Q1 − {outlierCfg.iqr_k}×IQR … Q3 + {outlierCfg.iqr_k}×IQR</span>
              </div>
            )}

            {/* Per-SKU overrides (only if multi-SKU) */}
            {hasMultiSku && (
              <div style={{ marginTop: 8 }}>
                <button onClick={() => setPerSkuOpen(o => !o)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 11, color: paramColor, display: 'flex', alignItems: 'center', gap: 4, padding: 0 }}>
                  {perSkuOpen ? '▾' : '▸'} {t('forecast.override_per_sku_label')} ({skuList.length} {t('forecast.skus_detected_suffix')})
                </button>
                {perSkuOpen && (
                  <div style={{ marginTop: 8, maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {skuList.map(([sku, info]) => {
                      const skuStrategy = outlierCfg.per_sku_overrides[sku] ?? 'inherit'
                      const hasSkuOutliers = info.count > 0
                      return (
                        <div key={sku} style={{
                          display: 'grid', gridTemplateColumns: '1fr 180px auto', gap: 8, alignItems: 'center',
                          padding: '6px 10px', borderRadius: 6, background: 'var(--surface)',
                          border: `1px solid ${skuStrategy !== 'inherit' ? paramColor + '44' : 'var(--border)'}`,
                        }}>
                          <div style={{ fontSize: 11, fontFamily: 'monospace' }}>
                            {sku}
                            {hasSkuOutliers && (
                              <span style={{ marginLeft: 6, fontSize: 10, color: '#f59e0b' }}>
                                {info.count} {t('forecast.outlier_unit')}{info.count !== 1 ? 's' : ''} ({info.pct}%)
                              </span>
                            )}
                            {!hasSkuOutliers && (
                              <span style={{ marginLeft: 6, fontSize: 10, color: '#22c55e' }}>{t('forecast.clean_label')}</span>
                            )}
                          </div>
                          <select
                            value={skuStrategy}
                            onChange={e => {
                              const v = e.target.value
                              setOutlierCfg(c => {
                                const overrides = { ...c.per_sku_overrides }
                                if (v === 'inherit') delete overrides[sku]
                                else overrides[sku] = v
                                return { ...c, per_sku_overrides: overrides }
                              })
                            }}
                            className="form-input form-select" style={{ fontSize: 11 }}>
                            <option value="inherit">{t('forecast.inherit_global_option')}</option>
                            {OUTLIER_STRATEGIES.map(o => (
                              <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                            ))}
                          </select>
                          {/* per-SKU param when override is sigma/pct/iqr */}
                          {skuStrategy === 'winsorize_sigma' && (
                            <input type="number" className="form-input" placeholder="σ"
                              value={outlierCfg.per_sku_n_sigma[sku] ?? outlierCfg.n_sigma}
                              onChange={e => setOutlierCfg(c => ({ ...c, per_sku_n_sigma: { ...c.per_sku_n_sigma, [sku]: parseFloat(e.target.value) || 3 } }))}
                              style={{ width: 60, fontSize: 11 }} />
                          )}
                          {skuStrategy === 'winsorize_pct' && (
                            <input type="number" className="form-input" placeholder="%"
                              value={outlierCfg.per_sku_percentile[sku] ?? outlierCfg.percentile}
                              onChange={e => setOutlierCfg(c => ({ ...c, per_sku_percentile: { ...c.per_sku_percentile, [sku]: parseFloat(e.target.value) || 1 } }))}
                              style={{ width: 60, fontSize: 11 }} />
                          )}
                          {skuStrategy === 'iqr_fence' && (
                            <input type="number" className="form-input" placeholder="k"
                              value={outlierCfg.per_sku_iqr_k[sku] ?? outlierCfg.iqr_k}
                              onChange={e => setOutlierCfg(c => ({ ...c, per_sku_iqr_k: { ...c.per_sku_iqr_k, [sku]: parseFloat(e.target.value) || 1.5 } }))}
                              style={{ width: 60, fontSize: 11 }} />
                          )}
                          {!['winsorize_sigma','winsorize_pct','iqr_fence'].includes(skuStrategy) && (
                            <div />
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })()}

      <Button variant="primary" onClick={() => onNext(gapFillChoice, outlierCfg)} style={{ alignSelf: 'flex-start' }}>
        {t('forecast.continue_to_columns_btn')}
      </Button>
    </div>
  )
}

// ── Step 3: Columns ────────────────────────────────────────────────────────────
const IMPUTE_OPTS = ['none', 'mean', 'median', 'forward', 'interpolate', 'zero']
const SCALE_OPTS  = ['none', 'standard', 'minmax', 'robust', 'log']
const ENCODE_OPTS = ['none', 'label', 'one_hot']

const SKU_PATTERNS = [
  'sku', 'item_id', 'item_code', 'item_nbr', 'product_id', 'product_code',
  'material_id', 'material', 'codigo', 'code', 'product', 'item', 'id',
]

function detectSkuColumn(cols: string[]): string {
  const lower = cols.map(c => c.toLowerCase().replace(/[^a-z0-9]/g, ''))
  for (const pat of SKU_PATTERNS) {
    const i = lower.findIndex(c => c === pat || c.startsWith(pat))
    if (i >= 0) return cols[i]
  }
  return ''
}

function Step3({
  sessionId, inspection, initialValues, gapFill, outlierCfg, onNext,
}: {
  sessionId: string
  inspection: InspectionResult
  initialValues?: Record<string, unknown>
  gapFill?: string
  outlierCfg?: OutlierConfig
  onNext: (exogCount: number) => void
}) {
  const { t } = useLanguage()
  const opts = inspection.column_options
  const allCols = inspection.profile.columns.map(c => c.name)

  const detectedSku = detectSkuColumn(allCols)

  // Derive ALL non-reserved columns as exog candidates (numeric + categorical)
  const reservedCols = new Set([
    ...opts.date_candidates, ...opts.target_candidates,
  ])
  const allExogCandidates = allCols.filter(c => !reservedCols.has(c))

  const [form, setForm] = useState({
    target_column: (initialValues?.target_column as string) ?? opts.target_candidates[0] ?? '',
    date_column:   (initialValues?.date_column   as string) ?? opts.date_candidates[0]   ?? '',
    sku_column:    (initialValues?.sku_column     as string) ?? opts.group_candidates[0]  ?? detectedSku ?? '',
    exogenous:     (initialValues?.exogenous      as string[]) ?? [],
  })
  const [transforms, setTransforms] = useState<Record<string, { impute: string; encode: string; scale: string }>>(
    (initialValues?.transforms as Record<string, { impute: string; encode: string; scale: string }>) ?? {}
  )
  const [saving, setSave] = useState(false)

  const colRole = (col: string): 'numeric' | 'categorical' => {
    const c = inspection.profile.columns.find(c => c.name === col)
    return (c?.role_hint === 'numeric' || c?.dtype?.includes('int') || c?.dtype?.includes('float')) ? 'numeric' : 'categorical'
  }

  const toggleExog = (col: string) => {
    setForm(f => {
      const next = f.exogenous.includes(col)
        ? f.exogenous.filter(x => x !== col)
        : [...f.exogenous, col]
      if (!next.includes(col)) {
        setTransforms(tr => { const n = { ...tr }; delete n[col]; return n })
      } else if (!transforms[col]) {
        const role = colRole(col)
        setTransforms(tr => ({
          ...tr,
          [col]: role === 'numeric'
            ? { impute: 'median', encode: 'none', scale: 'standard' }
            : { impute: 'none',   encode: 'label', scale: 'none' },
        }))
      }
      return { ...f, exogenous: next }
    })
  }

  const save = async () => {
    setSave(true)
    try {
      await chooseColumns(sessionId, {
        target_column:  form.target_column,
        date_column:    form.date_column,
        sku_column:     form.sku_column || null,
        exogenous:      form.exogenous,
        transforms,
        gap_fill:       gapFill || 'leave',
        outlier_config: outlierCfg,
      })
      onNext(form.exogenous.length)
    } finally { setSave(false) }
  }

  const sel = (field: 'target_column' | 'date_column' | 'sku_column', label: string, options: string[], color: string) => (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginBottom: 5 }}>{label}</label>
      <select value={form[field]} onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))}
              className="form-input form-select"
              style={{ borderColor: form[field] ? color + '60' : undefined }}>
        <option value="">{t('forecast.select_none_option')}</option>
        {options.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  )

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 16 }}>{t('forecast.column_mapping_title')}</div>
        {sel('target_column', t('forecast.target_column_label'), opts.target_candidates, '#818cf8')}
        {sel('date_column',   t('forecast.date_column_label'),   opts.date_candidates,   '#0ea5e9')}
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--dim)', marginBottom: 5 }}>
            {t('forecast.sku_column_label')}
            {detectedSku && form.sku_column === detectedSku && (
              <span style={{ fontSize: 9, fontWeight: 700, color: '#22c55e', background: '#22c55e18', borderRadius: 4, padding: '1px 5px' }}>
                {t('forecast.auto_detected_badge')}
              </span>
            )}
          </label>
          <select
            value={form.sku_column}
            onChange={e => setForm(f => ({ ...f, sku_column: e.target.value }))}
            className="form-input form-select"
            style={{ borderColor: form.sku_column ? '#22c55e60' : undefined }}
          >
            <option value="">{t('forecast.sku_column_none_option')}</option>
            {allCols.map(c => (
              <option key={c} value={c}>
                {c}{c === detectedSku ? ` ✓ ${t('forecast.recommended_suffix')}` : ''}
              </option>
            ))}
          </select>
          {form.sku_column && opts.target_candidates.includes(form.sku_column) && (
            <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 4, display: 'flex', gap: 4 }}>
              <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />
              {t('forecast.sku_also_target_warning')}
            </div>
          )}
        </div>
        {inspection.profile.warnings.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            {inspection.profile.warnings.map((w, i) => (
              <div key={i} style={{ fontSize: 11, color: '#f59e0b', display: 'flex', gap: 5, marginTop: 4 }}>
                <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} /> {w}
              </div>
            ))}
          </div>
        )}
        <Button variant="primary" loading={saving} disabled={!form.target_column || !form.date_column} onClick={save}>
          {t('forecast.confirm_columns_btn')}
        </Button>
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{t('forecast.exogenous_regressors_title')}</div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
          {t('forecast.exogenous_regressors_desc')}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 380, overflowY: 'auto' }}>
          {allExogCandidates.map(c => {
            const isOn = form.exogenous.includes(c)
            const role = colRole(c)
            const tx   = transforms[c]
            return (
              <div key={c} style={{
                borderRadius: 8,
                border: `1px solid ${isOn ? 'var(--accent)' : 'var(--border)'}`,
                background: isOn ? 'var(--accent-dim)' : 'var(--surface-2)',
                overflow: 'hidden',
              }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '8px 12px' }}>
                  <input type="checkbox" checked={isOn} onChange={() => toggleExog(c)}
                         style={{ accentColor: 'var(--accent)' }} />
                  <span style={{ fontSize: 12, fontWeight: 600, flex: 1, color: isOn ? 'var(--accent)' : 'var(--text)' }}>{c}</span>
                  <span style={{ fontSize: 10, color: role === 'numeric' ? 'var(--accent)' : '#a78bfa',
                    background: 'var(--surface)', borderRadius: 4, padding: '1px 6px' }}>
                    {role}
                  </span>
                </label>
                {isOn && tx && (
                  <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                    <div>
                      <div style={{ fontSize: 9, color: 'var(--dim)', marginBottom: 3 }}>{t('forecast.impute_label')}</div>
                      <select value={tx.impute} onChange={e => setTransforms(tr => ({ ...tr, [c]: { ...tr[c], impute: e.target.value } }))}
                              className="form-input form-select" style={{ fontSize: 11, padding: '3px 6px' }}>
                        {IMPUTE_OPTS.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                    {role === 'categorical' ? (
                      <div>
                        <div style={{ fontSize: 9, color: 'var(--dim)', marginBottom: 3 }}>{t('forecast.encode_label')}</div>
                        <select value={tx.encode} onChange={e => setTransforms(tr => ({ ...tr, [c]: { ...tr[c], encode: e.target.value } }))}
                                className="form-input form-select" style={{ fontSize: 11, padding: '3px 6px' }}>
                          {ENCODE_OPTS.map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </div>
                    ) : (
                      <div>
                        <div style={{ fontSize: 9, color: 'var(--dim)', marginBottom: 3 }}>{t('forecast.scale_label')}</div>
                        <select value={tx.scale} onChange={e => setTransforms(tr => ({ ...tr, [c]: { ...tr[c], scale: e.target.value } }))}
                                className="form-input form-select" style={{ fontSize: 11, padding: '3px 6px' }}>
                          {SCALE_OPTS.map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </div>
                    )}
                    <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 1 }}>
                      <div style={{ fontSize: 9, color: 'var(--dim)' }}>
                        {role === 'numeric' ? t('forecast.impute_scale_chain') : t('forecast.impute_encode_chain')}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {allExogCandidates.length === 0 && (
            <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('forecast.no_additional_columns')}</span>
          )}
        </div>
        <div style={{ marginTop: 16, padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dim)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t('forecast.dataset_label')}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {[
              [t('forecast.stat_rows'), inspection.profile.stats.n_rows],
              [t('forecast.stat_cols'), inspection.profile.stats.n_cols],
              [t('forecast.stat_skus'), inspection.profile.stats.n_skus ?? '—'],
              [t('forecast.stat_freq'), inspection.profile.recommended.freq ?? '—'],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <div style={{ fontSize: 10, color: 'var(--dim)' }}>{k}</div>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Step 4: Features ──────────────────────────────────────────────────────────
function Step4({
  sessionId, inspection, initialValues, onNext,
}: {
  sessionId: string
  inspection: InspectionResult
  initialValues?: Record<string, unknown>
  onNext: () => void
}) {
  const { t } = useLanguage()
  const schema = (inspection.config_schema?.features && Object.keys(inspection.config_schema.features).length > 0)
    ? inspection.config_schema.features
    : DEFAULT_FEATURES_SCHEMA
  // DEFAULT_FEATURES_SCHEMA stores translation keys in `label`; a backend-provided
  // config_schema stores literal human-readable text — only resolve via t() when
  // it looks like one of our namespaced keys.
  const fieldLabel = (label: string) => label.startsWith('forecast.') ? t(label) : label

  const [vals, setVals] = useState<Record<string, string | number | boolean>>(() => {
    const init: Record<string, string | number | boolean> = {}
    Object.entries(schema).forEach(([k, meta]) => {
      const sv = initialValues?.[k]
      if (sv !== undefined) {
        init[k] = Array.isArray(sv) ? (sv as number[]).join(', ') : sv as string | number | boolean
      } else {
        const dv = SMART_FEATURES[k as keyof typeof SMART_FEATURES]
        init[k] = Array.isArray(dv)
          ? (dv as number[]).join(', ')
          : dv !== undefined
          ? dv as string | number | boolean
          : Array.isArray(meta.default)
          ? (meta.default as number[]).join(', ')
          : (meta.default as string | number | boolean)
      }
    })
    return init
  })
  const [saving, setSave] = useState(false)

  const [fourierPeriods, setFourierPeriods] = useState<number[]>(
    (initialValues?.fourier_periods as number[] | undefined) ?? []
  )
  const [fourierK, setFourierK] = useState<number>(
    (initialValues?.fourier_K as number | undefined) ?? 2
  )

  const togglePeriod = (p: number) =>
    setFourierPeriods(ps => ps.includes(p) ? ps.filter(x => x !== p) : [...ps, p])

  const save = async () => {
    setSave(true)
    const parse = (k: string) => {
      const meta = schema[k]; const v = vals[k]
      if (meta.type === 'int_list')   return String(v).split(',').map(s => parseInt(s.trim(),  10)).filter(n => !isNaN(n))
      if (meta.type === 'float_list') return String(v).split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n))
      return v
    }
    try {
      await setFeatures(sessionId, {
        lags: parse('lags'), rolling: parse('rolling'), diffs: parse('diffs'),
        calendar: vals['calendar'], ewm_spans: parse('ewm_spans'),
        fourier_periods: fourierPeriods, fourier_K: fourierK,
      })
      onNext()
    } finally { setSave(false) }
  }

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
        {t('forecast.feature_engineering_title')}
        <HelpTip text={t('forecast.feature_engineering_help')} size={13} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 16 }}>
        {t('forecast.feature_engineering_desc')}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
        {Object.entries(schema).map(([key, meta]) => (
          <div key={key}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--dim)', marginBottom: 5 }}>{fieldLabel(meta.label)}</label>
            {meta.type === 'bool' ? (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!vals[key]}
                       onChange={e => setVals(v => ({ ...v, [key]: e.target.checked }))}
                       style={{ accentColor: 'var(--accent)', width: 15, height: 15 }} />
                <span style={{ fontSize: 12 }}>{vals[key] ? t('forecast.enabled_label') : t('forecast.disabled_label')}</span>
              </label>
            ) : (
              <input className="form-input" value={String(vals[key] ?? '')}
                     onChange={e => setVals(v => ({ ...v, [key]: e.target.value }))}
                     placeholder={Array.isArray(meta.default) ? (meta.default as number[]).join(', ') : String(meta.default)} />
            )}
            {(meta.type === 'int_list' || meta.type === 'float_list') && (
              <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 3 }}>{t('forecast.comma_separated_hint')}</div>
            )}
          </div>
        ))}
      </div>
      {/* Fourier features */}
      <div style={{ marginTop: 4, marginBottom: 20, padding: '14px 16px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
          <TrendingUp size={13} color="var(--accent)" /> {t('forecast.fourier_title')}
          <span style={{ fontSize: 10, color: 'var(--dim)', fontWeight: 400, marginLeft: 4 }}>
            {t('forecast.fourier_subtitle')}
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 10 }}>
          {t('forecast.fourier_desc')}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {[
            { p: 7,   labelKey: 'forecast.fourier_7d_label',   hintKey: 'forecast.fourier_7d_hint' },
            { p: 14,  labelKey: 'forecast.fourier_14d_label',  hintKey: 'forecast.fourier_14d_hint' },
            { p: 30,  labelKey: 'forecast.fourier_30d_label',  hintKey: 'forecast.fourier_30d_hint' },
            { p: 90,  labelKey: 'forecast.fourier_90d_label',  hintKey: 'forecast.fourier_90d_hint' },
            { p: 365, labelKey: 'forecast.fourier_365d_label', hintKey: 'forecast.fourier_365d_hint' },
          ].map(({ p, labelKey, hintKey }) => {
            const on = fourierPeriods.includes(p)
            return (
              <label key={p} style={{
                display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                padding: '6px 12px', borderRadius: 7,
                background: on ? 'var(--accent-dim)' : 'var(--surface)',
                border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                transition: 'all 0.15s',
              }}>
                <input type="checkbox" checked={on} onChange={() => togglePeriod(p)}
                       style={{ accentColor: 'var(--accent)' }} />
                <span style={{ fontSize: 11, fontWeight: 600, color: on ? 'var(--accent)' : 'var(--text)' }}>{t(labelKey)}</span>
                <span style={{ fontSize: 10, color: 'var(--dim)' }}>{t(hintKey)}</span>
              </label>
            )
          })}
        </div>
        {fourierPeriods.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('forecast.harmonics_per_period_label')}</span>
            {[1, 2, 3, 4, 5].map(k => (
              <button key={k} onClick={() => setFourierK(k)} style={{
                width: 28, height: 28, borderRadius: 6, border: `1px solid ${fourierK === k ? 'var(--accent)' : 'var(--border)'}`,
                background: fourierK === k ? 'var(--accent-dim)' : 'var(--surface)',
                color: fourierK === k ? 'var(--accent)' : 'var(--text)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              }}>{k}</button>
            ))}
            <span style={{ fontSize: 10, color: 'var(--dim)', marginLeft: 4 }}>
              → {fourierPeriods.length * fourierK * 2} {t('forecast.fourier_features_added_suffix')}
            </span>
          </div>
        )}
        {fourierPeriods.length === 0 && (
          <div style={{ fontSize: 11, color: 'var(--dim)', fontStyle: 'italic' }}>
            {t('forecast.fourier_none_selected')}
          </div>
        )}
      </div>

      <Button variant="primary" loading={saving} onClick={save}>{t('forecast.configure_features_btn')}</Button>
    </div>
  )
}

// ── Hyperparameter Drawer ─────────────────────────────────────────────────────
function HyperparamDrawer({ model, params, initialValues, onChange, onClose }: {
  model: string; params: HyperparamDef[]
  initialValues?: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void; onClose: () => void
}) {
  const { t } = useLanguage()
  const [vals, setVals] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {}
    params.forEach(p => { init[p.name] = initialValues?.[p.name] ?? p.default })
    return init
  })
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const set = (name: string, v: unknown) => {
    const updated = { ...vals, [name]: v }
    setVals(updated)
    const p = params.find(x => x.name === name)
    let err = ''
    if (p && (p.type === 'int' || p.type === 'float') && p.min !== undefined && p.max !== undefined) {
      const n = Number(v)
      if (isNaN(n)) err = t('forecast.hyperparam_invalid_number')
      else if (n < p.min) err = `${t('forecast.hyperparam_must_be_at_least')} ${p.min}`
      else if (n > p.max) err = `${t('forecast.hyperparam_must_be_at_most')} ${p.max}`
    }
    setFieldErrors(e => ({ ...e, [name]: err }))
    if (!err) onChange(updated)
  }

  const hasErrors = Object.values(fieldErrors).some(Boolean)

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 380,
      background: 'var(--surface)', borderLeft: '1px solid var(--border)',
      zIndex: 1000, overflowY: 'auto', boxShadow: '-8px 0 24px rgba(0,0,0,0.3)',
    }}>
      <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
            {model} {t('forecast.hyperparameters_suffix')}
            <HelpTip text={t('forecast.hyperparameters_help')} size={13} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{t('forecast.advanced_configuration_label')}</div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--dim)' }}>
          <X size={18} />
        </button>
      </div>
      <div style={{ padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {params.map(p => (
          <div key={p.name}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
              <label style={{ fontSize: 12, fontWeight: 600 }}>{p.name}</label>
              <span style={{ fontSize: 10, color: 'var(--accent)', background: 'var(--accent-dim)', borderRadius: 4, padding: '1px 6px' }}>
                {String(vals[p.name])}
              </span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--dim)', marginBottom: 6 }}>{p.desc}</div>
            {p.type === 'bool' ? (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!vals[p.name]} onChange={e => set(p.name, e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
                <span style={{ fontSize: 12 }}>{vals[p.name] ? t('forecast.enabled_label') : t('forecast.disabled_label')}</span>
              </label>
            ) : p.type === 'select' ? (
              <select value={String(vals[p.name])} onChange={e => set(p.name, e.target.value)} className="form-input form-select" style={{ fontSize: 12 }}>
                {(p.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : p.type === 'int' ? (
              <input type="number" className="form-input" value={Number(vals[p.name])} min={p.min} max={p.max} step={1}
                     onChange={e => set(p.name, parseInt(e.target.value, 10))} />
            ) : (
              <input type="number" className="form-input" value={Number(vals[p.name])} min={p.min} max={p.max} step={(p.max ?? 1) <= 1 ? 0.001 : 0.01}
                     onChange={e => set(p.name, parseFloat(e.target.value))} />
            )}
            {(p.type === 'int' || p.type === 'float') && p.min !== undefined && p.max !== undefined && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--dim)', marginTop: 2 }}>
                <span>{p.min}</span><span>{p.max}</span>
              </div>
            )}
            {fieldErrors[p.name] && (
              <div style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>{fieldErrors[p.name]}</div>
            )}
          </div>
        ))}
        <Button variant="secondary" onClick={onClose} disabled={hasErrors} style={{ width: '100%' }}>{t('forecast.done_btn')}</Button>
      </div>
    </div>
  )
}

// ── Step 5: Models ─────────────────────────────────────────────────────────────
function Step5({
  sessionId, exogCount, initialValues, columnTransforms, onNext,
}: {
  sessionId: string; exogCount: number
  initialValues?: Record<string, unknown>
  columnTransforms?: Record<string, { impute?: string; encode?: string; scale?: string }>
  onNext: (models: string[]) => void
}) {
  const { t } = useLanguage()
  const savedModels = (initialValues?.selected_models as string[] | undefined) ?? []
  const savedHyper  = (initialValues?.hyperparameters as Record<string, Record<string, unknown>> | undefined) ?? {}

  const [allModels,     setAll]      = useState<string[]>([])
  const [selected,      setSelected] = useState<string[]>(savedModels.length ? savedModels : SMART_MODELS.selected_models)
  const [hyperSchemas,  setSchemas]  = useState<Record<string, HyperparamDef[]>>({})
  const [hyperparams,   setHyperp]   = useState<Record<string, Record<string, unknown>>>(savedHyper)
  const [drawerModel,   setDrawer]   = useState<string | null>(null)
  const [saving,        setSave]     = useState(false)
  const [modelsLoadErr, setModelsErr] = useState(false)

  useEffect(() => {
    Promise.all([
      getAvailableModels(sessionId)
        .then(r => { if (r.models.length) setAll(r.models) })
        .catch(() => { setAll(Object.keys(MODEL_DESC_KEYS)); setModelsErr(true) }),
      getModelHyperparams(sessionId).then(setSchemas).catch(() => {}),
    ])
  }, [sessionId])

  const save = async () => {
    if (!selected.length) return
    setSave(true)
    try { await setModels(sessionId, selected, hyperparams); onNext(selected) }
    finally { setSave(false) }
  }

  if (!allModels.length) return <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>

  const ML_MODELS = ['lightgbm', 'xgboost']
  const configWarnings: string[] = []

  if (columnTransforms) {
    const hasOneHot = Object.values(columnTransforms).some(tr => tr.encode === 'one_hot')
    const mlSelected = selected.filter(m => ML_MODELS.includes(m))
    if (hasOneHot && mlSelected.length > 0)
      configWarnings.push(`${t('forecast.warn_one_hot_prefix')} ${mlSelected.join('/')} ${t('forecast.warn_one_hot_suffix')}`)
  }

  const statNoExog = selected.filter(m => ['ets', 'croston'].includes(m))
  if (exogCount > 0 && statNoExog.length > 0)
    configWarnings.push(`${statNoExog.join(', ')} ${statNoExog.length > 1 ? t('forecast.warn_do_plural') : t('forecast.warn_do_singular')} ${t('forecast.warn_no_exog_support')} — ${exogCount} ${t('forecast.warn_configured_column')}${exogCount > 1 ? 's' : ''} ${t('forecast.warn_will_be_ignored_by')} ${statNoExog.length > 1 ? t('forecast.warn_these_models') : t('forecast.warn_this_model')}.`)

  if (selected.includes('lstm'))
    configWarnings.push(t('forecast.warn_lstm_training_time'))

  const noMLSelected = !selected.some(m => [...ML_MODELS, 'lstm'].includes(m))
  if (noMLSelected && exogCount > 0)
    configWarnings.push(`${exogCount} ${t('forecast.warn_exog_column')}${exogCount > 1 ? t('forecast.warn_exog_plural_verb') : t('forecast.warn_exog_singular_verb')} ${t('forecast.warn_no_ml_models_selected')}`)

  return (
    <div>
      {modelsLoadErr && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '9px 12px', marginBottom: 14, borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', fontSize: 12, color: '#f59e0b' }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          {t('forecast.models_load_error')}
        </div>
      )}
      {drawerModel && hyperSchemas[drawerModel] && (
        <HyperparamDrawer
          model={drawerModel}
          params={hyperSchemas[drawerModel]}
          initialValues={hyperparams[drawerModel]}
          onChange={vals => setHyperp(h => ({ ...h, [drawerModel]: vals }))}
          onClose={() => setDrawer(null)}
        />
      )}
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
        {t('forecast.model_selection_title')}
        <HelpTip text={t('forecast.model_selection_help')} size={13} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 16 }}>
        {exogCount > 0 ? `${exogCount} ${t('forecast.exog_column_selected_prefix')}${exogCount !== 1 ? 's' : ''} ${t('forecast.exog_column_selected_suffix')}`
          : t('forecast.select_models_to_train_hint')}
      </div>
      {configWarnings.length > 0 && (
        <div style={{ marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {configWarnings.map((w, i) => (
            <div key={i} style={{
              display: 'flex', gap: 8, alignItems: 'flex-start', padding: '9px 12px',
              borderRadius: 8, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)',
              fontSize: 12, color: '#f59e0b',
            }}>
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
              {w}
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 20 }}>
        {allModels.map(m => {
          const on = selected.includes(m); const supExog = MODEL_EXOG_SUPPORT[m] ?? false
          const hasHyper = !!hyperSchemas[m]; const customHp = hyperparams[m]
          return (
            <div key={m} style={{
              borderRadius: 10, background: on ? 'var(--accent-dim)' : 'var(--surface-2)',
              border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`, transition: 'all 0.15s', overflow: 'hidden',
            }}>
              <div onClick={() => setSelected(s => on ? s.filter(x => x !== m) : [...s, m])} style={{ padding: '12px 14px', cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <div style={{ width: 18, height: 18, borderRadius: 4, flexShrink: 0, background: on ? 'var(--accent)' : 'var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {on && <Check size={11} color="#fff" strokeWidth={3} />}
                  </div>
                  <span style={{ fontSize: 13, fontWeight: 600, flex: 1, color: on ? 'var(--accent)' : 'var(--text)' }}>
                    {m}
                    {exogCount > 0 && supExog && <span style={{ fontSize: 9, color: '#22c55e', marginLeft: 4 }}>★</span>}
                  </span>
                  {customHp && <span style={{ fontSize: 9, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', borderRadius: 4, padding: '1px 5px' }}>{t('forecast.custom_badge')}</span>}
                </div>
                <div style={{ fontSize: 11, color: 'var(--dim)', paddingLeft: 26 }}>{MODEL_DESC_KEYS[m] ? t(MODEL_DESC_KEYS[m]) : ''}</div>
              </div>
              {on && hasHyper && (
                <div style={{ borderTop: '1px solid var(--border)', padding: '6px 14px' }}>
                  <button onClick={() => setDrawer(m)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 11, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Settings size={11} /> {t('forecast.configure_hyperparams_btn')}
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ marginBottom: 16 }}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>{selected.length} {selected.length !== 1 ? t('forecast.models_selected_plural') : t('forecast.models_selected_singular')}</span>
      </div>
      <Button variant="primary" loading={saving} disabled={selected.length === 0} onClick={save}>{t('forecast.confirm_models_btn')}</Button>
    </div>
  )
}

// ── Step 6: Routing Preview ────────────────────────────────────────────────────
const MODEL_SERIES_FIT: Record<string, string[]> = {
  lightgbm: ['stable', 'seasonal', 'volatile'], xgboost: ['stable', 'seasonal', 'volatile'],
  prophet: ['seasonal', 'stable'], arima: ['stable', 'seasonal'], ets: ['stable', 'seasonal'],
  croston: ['intermittent'], lstm: ['seasonal', 'volatile', 'stable'],
}

function Step6({ inspection, selectedModels, onNext }: { inspection: InspectionResult; selectedModels: string[]; onNext: () => void }) {
  const { t } = useLanguage()
  const profile = inspection.profile; const stats = profile.stats
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
        {t('forecast.routing_preview_title')}
        <HelpTip text={t('forecast.routing_preview_help')} size={13} />
      </div>
      <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 20 }}>
        {t('forecast.routing_preview_desc')}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 20 }}>
        {[
          { label: t('forecast.stat_total_rows'), value: stats.n_rows?.toLocaleString() ?? '—' },
          { label: t('forecast.stat_skus'), value: stats.n_skus?.toLocaleString() ?? '—' },
          { label: t('forecast.stat_frequency'), value: profile.recommended.freq ?? '—' },
          { label: t('forecast.stat_models'), value: selectedModels.length },
        ].map(({ label, value }) => (
          <div key={label} style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--accent)' }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10 }}>{t('forecast.model_assignment_by_series_type')}</div>
      <div style={{ borderRadius: 8, border: '1px solid var(--border)', overflow: 'hidden', marginBottom: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: `140px repeat(${selectedModels.length}, 1fr)`, padding: '8px 12px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', fontSize: 11, color: 'var(--dim)', fontWeight: 600 }}>
          <span>{t('forecast.series_type_col')}</span>
          {selectedModels.map(m => <span key={m} style={{ textAlign: 'center' }}>{m}</span>)}
        </div>
        {['stable', 'seasonal', 'volatile', 'intermittent', 'short'].map(type => (
          <div key={type} style={{ display: 'grid', gridTemplateColumns: `140px repeat(${selectedModels.length}, 1fr)`, padding: '9px 12px', alignItems: 'center', borderBottom: '1px solid var(--border)' }}>
            <span style={{ fontSize: 12 }}>{type}</span>
            {selectedModels.map(m => {
              const fits = (MODEL_SERIES_FIT[m] ?? []).includes(type)
              return <div key={m} style={{ display: 'flex', justifyContent: 'center' }}>
                {fits ? <Check size={13} color="#22c55e" strokeWidth={2.5} /> : <span style={{ fontSize: 11, color: 'var(--border)' }}>—</span>}
              </div>
            })}
          </div>
        ))}
      </div>
      <Button variant="primary" onClick={onNext}>{t('forecast.continue_to_validation_btn')}</Button>
    </div>
  )
}

// ── Step 7: Validation ─────────────────────────────────────────────────────────
function Step7({ sessionId, initialValues, onNext }: { sessionId: string; initialValues?: Record<string, unknown>; onNext: () => void }) {
  const [cfg, setCfg] = useState({
    train_ratio:     (initialValues?.train_ratio     as number  | undefined) ?? SMART_VALIDATION.train_ratio,
    walk_forward:    (initialValues?.walk_forward    as boolean | undefined) ?? SMART_VALIDATION.walk_forward,
    wfv_splits:      (initialValues?.wfv_splits      as number  | undefined) ?? SMART_VALIDATION.wfv_splits,
    min_history:     (initialValues?.min_history     as number  | undefined) ?? SMART_VALIDATION.min_history,
    seasonal_period: (initialValues?.seasonal_period as number  | undefined) ?? SMART_VALIDATION.seasonal_period,
    horizon:         (initialValues?.horizon         as number  | undefined) ?? SMART_VALIDATION.horizon,
  })
  const [saving, setSave] = useState(false)

  const save = async () => {
    setSave(true)
    try { await setValidationConfig(sessionId, { ...cfg }); onNext() }
    finally { setSave(false) }
  }

  const num = (label: string, key: keyof typeof cfg, min?: number, max?: number, step = 1, help?: string) => (
    <div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--dim)', marginBottom: 5 }}>
        {label}{help && <HelpTip text={help} size={12} />}
      </label>
      <input type="number" className="form-input" value={Number(cfg[key])} min={min} max={max} step={step}
             onChange={e => setCfg(c => ({ ...c, [key]: parseFloat(e.target.value) }))} />
    </div>
  )

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 16 }}>Validation & Forecast Config</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
        {num('Train ratio',      'train_ratio',     0.5, 0.95, 0.05,
          'Qué porción de tu historia se usa para entrenar el modelo; el resto se reserva para medir qué tan bien predice. 0.8 = 80% entrenar, 20% probar.')}
        {num('WFV splits',       'wfv_splits',      1, 10, 1,
          'Número de ventanas de prueba en la validación walk-forward. Más ventanas = medición de precisión más robusta, pero entrenamiento más lento.')}
        {num('Min history rows', 'min_history',     5, undefined, 1,
          'Mínimo de registros que necesita un producto para entrenarse. Los que tengan menos se excluyen del pronóstico (al terminar verás cuáles y por qué).')}
        {num('Seasonal period',  'seasonal_period', 2, undefined, 1,
          'Cada cuántos periodos se repite el patrón de demanda. Con datos diarios: 7 = patrón semanal. Con datos mensuales: 12 = patrón anual.')}
        {num('Forecast horizon', 'horizon',         1, undefined, 1,
          'Cuántos periodos hacia el futuro se pronostica. Conviene que sea mayor que tu lead time, para poder anticipar el pedido antes de que llegue un pico.')}
        <div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--dim)', marginBottom: 5 }}>
            Walk-forward validation
            <HelpTip text="En vez de una sola división entrenar/probar, mueve la ventana de prueba a lo largo del tiempo (como predecir semana tras semana con lo conocido hasta ese momento). Da una medida de precisión mucho más realista para series temporales. Recomendado dejarlo activado." size={12} />
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '8px 12px', background: cfg.walk_forward ? 'var(--accent-dim)' : 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 7 }}>
            <input type="checkbox" checked={cfg.walk_forward} onChange={e => setCfg(c => ({ ...c, walk_forward: e.target.checked }))} style={{ accentColor: 'var(--accent)' }} />
            <span style={{ fontSize: 12 }}>{cfg.walk_forward ? 'Enabled' : 'Disabled'}</span>
          </label>
        </div>
      </div>
      <Button variant="primary" loading={saving} onClick={save}>Save Config →</Button>
    </div>
  )
}

// ── Step 8: Training ───────────────────────────────────────────────────────────
function Step8({ sessionId, inspection, onNext }: { sessionId: string; inspection: InspectionResult; onNext: () => void }) {
  const [job,     setJob]     = useState<JobResponse | null>(null)
  const [error,   setError]   = useState<string | null>(null)
  const [started, setStarted] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  useEffect(() => () => stopPoll(), [])

  const run = async () => {
    setStarted(true); setError(null)
    try {
      const { job_id } = await startTraining(sessionId)
      const initial = await getJob(job_id); setJob(initial)
      pollRef.current = setInterval(async () => {
        try {
          const d = await getJob(job_id); setJob(d)
          if (d.status === 'COMPLETED') { stopPoll(); setTimeout(onNext, 800) }
          if (d.status === 'FAILED' || d.status === 'CANCELLED') { stopPoll(); setError(d.error ?? `Training ${d.status.toLowerCase()}`) }
        } catch (e: unknown) { setError((e as Error).message); stopPoll() }
      }, 2000)
    } catch (e: unknown) { setError((e as Error).message); setStarted(false) }
  }

  const isRunning = job?.status === 'QUEUED' || job?.status === 'RUNNING'
  const isDone    = job?.status === 'COMPLETED'
  const hasFailed = job?.status === 'FAILED' || job?.status === 'CANCELLED'
  const warnings  = inspection.profile.warnings ?? []
  const stats     = inspection.profile.stats

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 16 }}>Training Execution</div>
      {!started ? (
        <div>
          {(warnings.length > 0 || stats.n_rows) && (
            <div style={{ padding: '14px 16px', borderRadius: 10, marginBottom: 20, background: warnings.length ? 'rgba(245,158,11,0.06)' : 'rgba(34,197,94,0.05)', border: `1px solid ${warnings.length ? 'rgba(245,158,11,0.2)' : 'rgba(34,197,94,0.15)'}` }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10 }}>Dataset Quality Check</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: warnings.length ? 12 : 0 }}>
                {[{ k: 'Rows', v: stats.n_rows?.toLocaleString() }, { k: 'SKUs', v: stats.n_skus?.toLocaleString() ?? '—' }, { k: 'Freq', v: inspection.profile.recommended.freq ?? '—' }].map(({ k, v }) => (
                  <div key={k}><div style={{ fontSize: 10, color: 'var(--dim)' }}>{k}</div><div style={{ fontSize: 13, fontWeight: 600 }}>{v}</div></div>
                ))}
              </div>
              {warnings.map((w, i) => (
                <div key={i} style={{ fontSize: 11, color: '#fbbf24', display: 'flex', gap: 6, marginTop: 4 }}>
                  <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} /> {w}
                </div>
              ))}
            </div>
          )}
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <Play size={40} strokeWidth={1} style={{ color: 'var(--dim)', margin: '0 auto 16px' }} />
            <div style={{ fontSize: 14, color: 'var(--muted)', marginBottom: 20 }}>Ready to train. The pipeline will run in the background.</div>
            <Button variant="primary" size="lg" icon={<Play size={15} />} onClick={run}>Start Training</Button>
          </div>
        </div>
      ) : (
        <div>
          <div style={{ padding: '16px 20px', borderRadius: 10, marginBottom: 20, background: isDone ? 'rgba(34,197,94,0.08)' : hasFailed ? 'rgba(239,68,68,0.08)' : 'rgba(129,140,248,0.08)', border: `1px solid ${isDone ? 'rgba(34,197,94,0.3)' : hasFailed ? 'rgba(239,68,68,0.3)' : 'var(--accent)'}`, display: 'flex', alignItems: 'center', gap: 12 }}>
            {isRunning && <Spinner />}
            {isDone    && <Check size={18} color="#22c55e" />}
            <div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>
                {isDone ? 'Training Complete' : hasFailed ? `Training ${job?.status}` : job?.status === 'QUEUED' ? 'Queued — waiting for worker…' : 'Training in Progress…'}
              </div>
              {job?.progress && !isDone && <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>{job.progress.message} ({job.progress.percent}%)</div>}
              {error && <div style={{ fontSize: 12, color: '#ef4444', marginTop: 2 }}>{error}</div>}
            </div>
          </div>
          {job && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ height: 6, borderRadius: 3, background: 'var(--surface-2)', overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 3, width: `${job.progress?.percent ?? 0}%`, background: isDone ? '#22c55e' : hasFailed ? '#ef4444' : 'var(--accent)', transition: 'width 0.5s ease' }} />
              </div>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {['Data Quality Check', 'Model Routing (per series type)', 'Feature Engineering (lags + rolling + calendar)', 'Walk-Forward Validation', 'Ensemble (inverse-MAE weights)', 'Registry + Inventory Advisory'].map((label, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 7, background: 'var(--surface-2)', border: '1px solid var(--border)', opacity: isRunning ? 0.5 + i * 0.08 : 1 }}>
                <div style={{ width: 20, height: 20, borderRadius: '50%', flexShrink: 0, background: isDone ? '#22c55e22' : 'var(--surface)', border: `1px solid ${isDone ? '#22c55e' : 'var(--border)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: isDone ? '#22c55e' : 'var(--dim)' }}>
                  {isDone ? <Check size={10} strokeWidth={3} /> : i + 1}
                </div>
                <span style={{ fontSize: 12 }}>{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Step 9: Results ────────────────────────────────────────────────────────────
function Step9({
  sessionId, onBack, onRetrain, onNewForecast,
}: { sessionId: string; onBack: () => void; onRetrain: () => void; onNewForecast: () => void }) {
  const [data,          setData]      = useState<MetricsResponse | null>(null)
  const [page,          setPage]      = useState(0)
  const [loading,       setLoad]      = useState(true)
  const [exporting,     setExport]    = useState<string | null>(null)
  // Override panel
  const [overrideDraft, setDraft]     = useState<Record<string, ForecastOverride>>({})
  const [overrideSaving,setSaving]    = useState(false)
  const [overrideDone,  setDone]      = useState(false)
  const [showOverrides, setShowOv]    = useState(false)
  const [selectedSku,   setSelSku]    = useState<string | null>(null)
  const [seriesData,    setSeries]    = useState<ForecastSeries | null>(null)
  const [seriesLoading, setSeriesLd]  = useState(false)
  const [seriesError,   setSeriesErr] = useState<string | null>(null)
  const [overrideErr,   setOverrideErr] = useState<string | null>(null)
  const PAGE_SIZE = 50

  useEffect(() => {
    getMetrics(sessionId).then(setData).catch(console.error).finally(() => setLoad(false))
  }, [sessionId])

  useEffect(() => {
    if (!selectedSku) return
    setSeriesLd(true)
    setSeriesErr(null)
    getForecastSeries(sessionId, selectedSku)
      .then(setSeries)
      .catch((e: unknown) => {
        setSeries(null)
        setSeriesErr(e instanceof Error ? e.message : `No se pudo cargar el forecast del SKU ${selectedSku}. Verifica tu conexión e intenta de nuevo.`)
      })
      .finally(() => setSeriesLd(false))
  }, [selectedSku, sessionId])

  useEffect(() => {
    if (!showOverrides || selectedSku) return
    const skus = Array.from(new Set(data?.rows.map(r => r.sku).filter(Boolean) as string[]))
    if (skus.length) setSelSku(skus[0])
  }, [showOverrides, selectedSku, data])

  const handleOverride = (sku: string, date: string, original: number, override: number) => {
    const key = `${sku}|${date}`
    setDraft(d => ({ ...d, [key]: { sku, date, original, override } }))
    setDone(false)
  }

  const saveOverrides = async () => {
    const items = Object.values(overrideDraft)
    if (!items.length) return
    setSaving(true)
    setOverrideErr(null)
    try {
      await saveForecastOverrides(sessionId, items)
      setDone(true)
      setDraft({})
    } catch (e: any) {
      setOverrideErr(e?.message || 'No se pudieron guardar los ajustes manuales. Verifica tu conexión e intenta de nuevo.')
    } finally { setSaving(false) }
  }

  const doExport = async (format: 'excel' | 'pdf') => {
    setExport(format)
    try {
      await generateReport(sessionId, 'operational', [format])
      await downloadReportBlob(sessionId, format)
    } catch (e: any) {
      console.error('Export failed:', e.message)
    } finally { setExport(null) }
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>
  if (!data)   return <div style={{ color: '#ef4444', fontSize: 13 }}>Failed to load metrics.</div>

  const best  = Object.entries(data.by_model).sort((a, b) => a[1].avg_mae - b[1].avg_mae)[0]
  const total = data.rows.length
  const rows  = data.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Training Results</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {best && (
            <>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>Best model:</span>
              <Badge variant="success">{best[0]} · MAE {best[1].avg_mae.toFixed(4)}</Badge>
            </>
          )}
          <Button variant="ghost" size="sm" loading={exporting === 'excel'}
            icon={<Table size={11} />} onClick={() => doExport('excel')}>
            Excel
          </Button>
          <Button variant="ghost" size="sm" loading={exporting === 'pdf'}
            icon={<FileText size={11} />} onClick={() => doExport('pdf')}>
            PDF
          </Button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>
        {Object.entries(data.by_model).map(([model, m]) => (
          <div key={model} style={{ padding: '14px 16px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{model}</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8 }}>
              {[['MAE', m.avg_mae], ['RMSE', m.avg_rmse], ['WAPE', m.avg_wape], ['MAPE', m.avg_mape], ['sMAPE', m.avg_smape]].map(([k, v]) => (
                <div key={String(k)}>
                  <div style={{ fontSize: 10, color: 'var(--dim)' }}>{k}</div>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>{Number(v).toFixed(4)}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>{total} total rows</span>
        {pages > 1 && (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <Button variant="ghost" size="sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>←</Button>
            <span style={{ fontSize: 12, color: 'var(--dim)' }}>{page + 1} / {pages}</span>
            <Button variant="ghost" size="sm" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>→</Button>
          </div>
        )}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>Model</th><th>Type</th><th>SKU</th><th>MAE</th><th>RMSE</th><th>WAPE</th><th>MAPE</th><th>sMAPE</th><th>Folds</th></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td style={{ fontWeight: 500 }}>{r.model}</td>
              <td><Badge variant={r.type === 'ml' ? 'default' : r.type === 'stat' ? 'info' : 'muted'}>{r.type}</Badge></td>
              <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.sku ?? '—'}</td>
              <td>{r.mae?.toFixed(4) ?? '—'}</td>
              <td>{r.rmse?.toFixed(4) ?? '—'}</td>
              <td>{r.wape?.toFixed(4) ?? '—'}</td>
              <td>{r.mape?.toFixed(4) ?? '—'}</td>
              <td>{r.smape?.toFixed(4) ?? '—'}</td>
              <td style={{ color: 'var(--dim)' }}>{r.n_folds ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* ── Forecast Override panel ─────────────────────────────────── */}
      <div style={{ marginTop: 24, borderTop: '1px solid var(--border)', paddingTop: 20 }}>
        <button
          onClick={() => setShowOv(v => !v)}
          style={{
            all: 'unset', cursor: 'pointer', fontSize: 13, fontWeight: 600,
            color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8,
          }}
        >
          <Settings size={14} /> Adjust Forecast Values
          <span style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 400 }}>
            {showOverrides ? '▲' : '▼'}
          </span>
        </button>

        {showOverrides && (() => {
          const skus = Array.from(new Set(data?.rows.map(r => r.sku).filter(Boolean) as string[]))
          const activeSku = selectedSku ?? skus[0] ?? null
          if (!activeSku && skus.length === 0) {
            return <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 12 }}>No SKU-level forecasts available.</div>
          }
          return (
            <div style={{ marginTop: 14 }}>
              {/* SKU tabs */}
              {skus.length > 1 && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                  {skus.map(s => (
                    <button
                      key={s}
                      onClick={() => setSelSku(s)}
                      style={{
                        all: 'unset', cursor: 'pointer', fontSize: 11, padding: '3px 10px',
                        borderRadius: 6, border: `1px solid ${s === activeSku ? 'var(--accent)' : 'var(--border)'}`,
                        background: s === activeSku ? 'var(--accent-dim)' : 'transparent',
                        color: s === activeSku ? 'var(--accent)' : 'var(--muted)',
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}

              {seriesLoading && <div style={{ padding: 20, textAlign: 'center' }}><Spinner /></div>}

              {!seriesLoading && seriesError && (
                <div style={{ fontSize: 12, color: '#ef4444', marginTop: 8, padding: '8px 12px', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
                  {seriesError}
                </div>
              )}

              {!seriesLoading && seriesData && (
                <div style={{ maxHeight: 280, overflowY: 'auto' }}>
                  <table className="data-table">
                    <thead>
                      <tr><th>Date</th><th>Forecast Value</th></tr>
                    </thead>
                    <tbody>
                      {seriesData.forecast.map(pt => {
                        const key = `${activeSku}|${pt.date}`
                        const draft = overrideDraft[key]
                        return (
                          <tr key={pt.date}>
                            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{pt.date}</td>
                            <td>
                              <OverrideCell
                                value={draft ? draft.override : pt.value}
                                original={pt.value}
                                overridden={!!draft}
                                onSave={val => handleOverride(activeSku!, pt.date, pt.value, val)}
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {!seriesLoading && !seriesData && !seriesError && activeSku && (
                <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 8 }}>
                  Select a SKU to see its forecast values.
                </div>
              )}
            </div>
          )
        })()}
      </div>

      {/* ── Sticky pending-changes bar ───────────────────────────────── */}
      {Object.keys(overrideDraft).length > 0 && (
        <div style={{
          position: 'sticky', bottom: 0, marginTop: 16,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 16px', borderRadius: 8,
          background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)',
        }}>
          <span style={{ fontSize: 12, color: '#f59e0b' }}>
            {Object.keys(overrideDraft).length} change{Object.keys(overrideDraft).length > 1 ? 's' : ''} pending
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={() => setDraft({})}>Discard</Button>
            <Button variant="primary" size="sm" loading={overrideSaving} onClick={saveOverrides}>
              Save overrides
            </Button>
          </div>
        </div>
      )}
      {overrideDone && (
        <div style={{ fontSize: 12, color: '#22c55e', textAlign: 'right', marginTop: 6 }}>
          Overrides saved successfully.
        </div>
      )}
      {overrideErr && (
        <div style={{ fontSize: 12, color: '#ef4444', textAlign: 'right', marginTop: 6 }}>
          {overrideErr}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border)' }}>
        <Button variant="secondary" onClick={onBack}>← Back to Models</Button>
        <Button variant="ghost"     onClick={onRetrain}>Train Again</Button>
        <Button variant="primary"   onClick={onNewForecast} style={{ marginLeft: 'auto' }}>
          Start New Forecast →
        </Button>
      </div>
    </div>
  )
}

// INSTANCIA-3 TAREA-3A: COMPLETADA

// ── Restoring overlay ─────────────────────────────────────────────────────────
function RestoringOverlay() {
  return (
    <div style={{ textAlign: 'center', padding: '60px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
      <Spinner size={24} />
      <div style={{ fontSize: 14, color: 'var(--muted)' }}>Restoring session…</div>
      <div style={{ fontSize: 11, color: 'var(--dim)' }}>Loading your previous configurations</div>
    </div>
  )
}

// ── Page inner (uses useSearchParams) ────────────────────────────────────────
function ForecastPageContent() {
  const searchParams = useSearchParams()
  const router       = useRouter()
  const sessionParam = searchParams.get('session')

  const [step,          setStep]       = useState(1)
  const [sessionId,     setSession]    = useState<string | null>(null)
  const [inspection,    setInspect]    = useState<InspectionResult | null>(null)
  const [selectedModels,setModels_]    = useState<string[]>([])
  const [exogCount,     setExogCount]  = useState(0)
  const [savedConfigs,  setSavedCfgs]  = useState<SavedConfigs>({})
  const [completedSteps,setCompleted]  = useState<Set<number>>(new Set())
  const [restoring,     setRestoring]  = useState(false)
  const [restoreError,  setRestoreErr] = useState<string | null>(null)
  const [gapFill,       setGapFill]    = useState<string>('leave')
  const [outlierCfg,    setOutlierCfg] = useState<OutlierConfig>(defaultOutlierConfig())

  const isDirty = sessionId !== null && (
    step > 1 || Object.values(savedConfigs).some(v => v !== undefined)
  )

  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  // ── Session restoration from URL param ──────────────────────────────────────
  useEffect(() => {
    if (!sessionParam) return
    setRestoring(true)
    setRestoreErr(null)

    const restore = async () => {
      try {
        const [session, inspection, summary] = await Promise.all([
          getSession(sessionParam),
          inspectSession(sessionParam).catch(() => null),
          getConfigSummary(sessionParam).catch(() => null),
        ])

        setSession(sessionParam)
        if (inspection) setInspect(inspection)

        // Restore saved configs
        const configs: SavedConfigs = {}
        if (summary?.columns)    configs.columns    = summary.columns    as Record<string, unknown>
        if (summary?.features)   configs.features   = summary.features   as Record<string, unknown>
        if (summary?.models)     configs.models     = summary.models     as Record<string, unknown>
        if (summary?.validation) configs.validation = summary.validation as Record<string, unknown>
        setSavedCfgs(configs)

        // Restore selected models
        if (summary?.models) {
          const sm = (summary.models as Record<string, unknown>).selected_models as string[] | undefined
          if (sm?.length) setModels_(sm)
        }

        // Restore exog count
        if (summary?.columns) {
          const exog = (summary.columns as Record<string, unknown>).exogenous as string[] | undefined
          if (exog) setExogCount(exog.length)
        }

        // Mark completed steps based on what config exists
        const completed = new Set<number>()
        if (inspection) { completed.add(1); completed.add(2) }
        if (configs.columns)    completed.add(3)
        if (configs.features)   completed.add(4)
        if (configs.models)     completed.add(5)
        if (configs.validation) completed.add(7)
        if (session.status === 'COMPLETED') { completed.add(8); completed.add(9) }
        setCompleted(completed)

        // Jump to the right step
        const target = statusToStep(session.status)
        setStep(target)

        // Clean URL to prevent re-restore on navigation
        router.replace('/forecast', { scroll: false })
      } catch (e) {
        setRestoreErr(
          e instanceof Error
            ? `No se pudo restaurar la sesión: ${e.message}`
            : 'No se pudo restaurar la sesión solicitada. Es posible que haya sido eliminada o que no tengas acceso. Puedes empezar una nueva configuración abajo.'
        )
      } finally {
        setRestoring(false)
      }
    }
    restore()
  }, [sessionParam])

  // ── Smart skip with auto-save defaults ──────────────────────────────────────
  const handleSkip = async (currentStep: number) => {
    if (!sessionId || !inspection) { setStep(s => Math.min(TOTAL, s + 1)); return }

    try {
      switch (currentStep) {
        case 3:
          if (!savedConfigs.columns) {
            const defaults = { ...smartColumns(inspection), gap_fill: gapFill, outlier_config: outlierCfg }
            await chooseColumns(sessionId, defaults)
            setSavedCfgs(c => ({ ...c, columns: defaults as unknown as Record<string, unknown> }))
            setCompleted(s => addSteps(s, 3))
          }
          break
        case 4:
          if (!savedConfigs.features) {
            await setFeatures(sessionId, SMART_FEATURES)
            setSavedCfgs(c => ({ ...c, features: SMART_FEATURES as unknown as Record<string, unknown> }))
            setCompleted(s => addSteps(s, 4))
          }
          break
        case 5:
          if (!savedConfigs.models) {
            await setModels(sessionId, SMART_MODELS.selected_models)
            setModels_(SMART_MODELS.selected_models)
            setSavedCfgs(c => ({ ...c, models: SMART_MODELS as unknown as Record<string, unknown> }))
            setCompleted(s => addSteps(s, 5))
          }
          break
        case 7:
          if (!savedConfigs.validation) {
            await setValidationConfig(sessionId, SMART_VALIDATION)
            setSavedCfgs(c => ({ ...c, validation: SMART_VALIDATION as unknown as Record<string, unknown> }))
            setCompleted(s => addSteps(s, 7))
          }
          break
      }
    } catch (e) {
      // If auto-save fails, still advance
      console.warn('Smart default save failed:', e)
    }
    setStep(s => Math.min(TOTAL, s + 1))
  }

  const reset = () => {
    setStep(1); setSession(null); setInspect(null)
    setModels_([]); setExogCount(0); setSavedCfgs({})
    setCompleted(new Set()); setGapFill('leave'); setOutlierCfg(defaultOutlierConfig())
  }

  const { setActiveSessionId } = useActiveSession()
  useEffect(() => {
    setActiveSessionId(sessionId)
    return () => setActiveSessionId(null)
  }, [sessionId, setActiveSessionId])

  return (
    <div style={{ animation: 'fadeIn 0.3s ease-out' }}>
      <div style={{ textAlign: 'right', marginBottom: 8 }}>
        <Link href="/quick-start" style={{ fontSize: 12, color: 'var(--dim)', textDecoration: 'none' }}>
          ¿Primera vez? → <span style={{ color: 'var(--accent)' }}>Usa el flujo simplificado</span>
        </Link>
      </div>
      {restoreError && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', marginBottom: 16,
          borderRadius: 8, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          fontSize: 13, color: '#f87171',
        }}>
          {restoreError}
        </div>
      )}

      <div style={{
        position: 'sticky', top: 0, zIndex: 100,
        background: 'var(--bg)', paddingTop: 4, paddingBottom: 16,
        borderBottom: '1px solid transparent',
      }}>
        <StepIndicator current={step} completed={completedSteps} />
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 28, minHeight: 400 }}>
        {restoring ? (
          <RestoringOverlay />
        ) : (
          <>
            {step === 1 && (
              <Step1 onNext={(id, insp) => { setSession(id); setInspect(insp); setCompleted(s => addSteps(s, 1)); setStep(2) }} />
            )}
            {step === 2 && sessionId && inspection && (
              <Step2 sessionId={sessionId} inspection={inspection}
                onNext={(gf, oc) => { setGapFill(gf); setOutlierCfg(oc); setCompleted(s => addSteps(s, 2)); setStep(3) }} />
            )}
            {step === 3 && sessionId && inspection && (
              <Step3
                sessionId={sessionId} inspection={inspection}
                initialValues={savedConfigs.columns}
                gapFill={gapFill}
                outlierCfg={outlierCfg}
                onNext={(n) => { setExogCount(n); setCompleted(s => addSteps(s, 3)); setStep(4) }}
              />
            )}
            {step === 4 && sessionId && inspection && (
              <Step4
                sessionId={sessionId} inspection={inspection}
                initialValues={savedConfigs.features}
                onNext={() => { setCompleted(s => addSteps(s, 4)); setStep(5) }}
              />
            )}
            {step === 5 && sessionId && (
              <Step5
                sessionId={sessionId} exogCount={exogCount}
                initialValues={savedConfigs.models}
                columnTransforms={savedConfigs.columns?.transforms as Record<string, { impute?: string; encode?: string; scale?: string }> | undefined}
                onNext={models => { setModels_(models); setCompleted(s => addSteps(s, 5)); setStep(6) }}
              />
            )}
            {step === 6 && inspection && (
              <Step6 inspection={inspection} selectedModels={selectedModels} onNext={() => setStep(7)} />
            )}
            {step === 7 && sessionId && (
              <Step7
                sessionId={sessionId}
                initialValues={savedConfigs.validation}
                onNext={() => { setCompleted(s => addSteps(s, 7)); setStep(8) }}
              />
            )}
            {step === 8 && sessionId && inspection && (
              <Step8 sessionId={sessionId} inspection={inspection} onNext={() => { setCompleted(s => addSteps(s, 8, 9)); setStep(9) }} />
            )}
            {step === 9 && sessionId && (
              <Step9
                sessionId={sessionId}
                onBack={() => setStep(5)}
                onRetrain={() => setStep(8)}
                onNewForecast={reset}
              />
            )}
          </>
        )}
      </div>

      {!restoring && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
          <Button variant="secondary"
            onClick={() => {
              if (isDirty && Object.values(savedConfigs).some(v => v !== undefined)) {
                if (!window.confirm('You have unsaved configuration. Leave anyway?')) return
              }
              setStep(s => Math.max(1, s - 1))
            }}
            disabled={step === 1 || step === 9}>
            ← Back
          </Button>
          <div style={{ fontSize: 12, color: 'var(--dim)', alignSelf: 'center' }}>
            Step {step} of {TOTAL}
          </div>
          {step < TOTAL && step !== 9 && (
            <Button variant="ghost" onClick={() => handleSkip(step)}>
              Skip →
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Page export (Suspense boundary for useSearchParams) ───────────────────────
export default function ForecastPage() {
  return (
    <Suspense fallback={
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 400 }}>
        <Spinner size={24} />
      </div>
    }>
      <ForecastPageContent />
    </Suspense>
  )
}

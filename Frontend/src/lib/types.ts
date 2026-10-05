// ── Session ──────────────────────────────────────────────────────────────────
import type { RuleScope, ValueSource } from './inventoryDefaults'
export type { RuleScope, ValueSource }

export type SessionStatus =
  | 'DRAFT' | 'DATASET_LOADED' | 'INSPECTED'
  | 'COLUMNS_CONFIGURED' | 'FEATURES_CONFIGURED' | 'MODELS_CONFIGURED'
  | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export interface SessionInfo {
  session_id:   string
  name:         string
  status:       SessionStatus
  created_at:   string
  updated_at:   string
  error:        string | null
  file_path:    string | null
  dataset_id?:  string | null
  /** Granularity of a family run (daily/weekly/monthly); null for single-grain
   * sessions trained before the family feature. */
  granularity?: string | null
  /** A holdout test run: it trains on a truncated copy of a dataset to grade the
   *  engine, and never drives planning. */
  is_backtest?: boolean
  /** Set on archived sessions. `/sessions` lists active ones only, but the
   *  field rides along on every row. */
  archived_at?: string | null
}

// Enriched row from GET /sessions/summary (session-history page).
export interface SessionSummary {
  id:               string
  session_id:       string
  name:             string
  description:      string | null
  status:           SessionStatus
  pipeline_step:    string
  created_at:       string
  updated_at:       string
  dataset_id:       string | null
  dataset_name:     string | null
  dataset_filename: string | null
  horizon:          number | null
  granularity:      string | null
  sku_count:        number | null
  // Why the run failed, straight from the job that died. Null for every other
  // status — and for a FAILED session old enough that its job row is gone.
  failure_reason?:  string | null
  tags:             string[]
  /** 1 - mean best-model WAPE over the SKUs; null until the run has results. */
  accuracy?:        number | null
  models?:          string[]
  archived_at?:     string | null
  is_backtest?:     boolean
  backtest_holdout_periods?: number | null
}

// ── Dataset ───────────────────────────────────────────────────────────────────
export interface DatasetMeta {
  id:                string
  name:              string
  original_filename: string
  file_type:         string
  file_path:         string
  size_bytes:        number
  row_count:         number | null
  column_count:      number | null
  uploaded_at:       string
}

export interface ProfileColumn {
  name:      string
  dtype:     string
  role_hint: string | null
  n_unique:  number
  null_pct:  number
  sample:    unknown[]
}

/** One way out of a blocking-but-fixable finding, with what it costs.
 *
 * The cost is the whole point: "fill the gaps with zero" and "interpolate the
 * gaps" are not two flavours of one button, they are two different claims about
 * what happened, and only the user knows which is true. `action` and
 * `consequence` arrive in English from the engine and are the fallback; the
 * Spanish comes from `gateopt.<code>.*` in translations.ts, per CLAUDE.md. */
export interface RemediationOption {
  code:        string
  action:      string
  consequence: string
  params:      Record<string, unknown>
  recommended: boolean
  applied_by:  string
}

export interface DataQualityIssue {
  type:     string
  severity: 'error' | 'warning' | 'info'
  message:  string
  // Set only by issues that make a forecast impossible, not merely worse.
  // Severity cannot carry this: `all_zeros` and the granularity conflict are
  // already 'error' and the user may still continue past them.
  blocking?: boolean
  // Which of the gate's three outcomes this is. Absent on issues produced
  // before the gate existed, which the UI treats as advisory.
  classification?: 'blocking_fatal' | 'blocking_fixable' | 'advisory'
  // False for something we can see, cannot fix, and must still say out loud.
  remediable?:    boolean
  params?:        Record<string, unknown>
  remediations?:  RemediationOption[]
  [key: string]: unknown
}

/** `GET /sessions/{id}/data-gate` — the same computation `POST /train` enforces. */
export interface DataGate {
  issues:              DataQualityIssue[]
  blocking_fatal?:     string[]
  blocking_fixable?:   string[]
  advisory?:           string[]
  // {issue_type: option_code} already recorded for this session.
  chosen_remediations: Record<string, string>
  // Fixable findings with no answer yet. Training stays blocked while non-empty.
  unresolved:          string[]
  [key: string]: unknown
}

export interface SkuOutlierInfo {
  count:   number
  pct:     number
  iqr_lo:  number
  iqr_hi:  number
  n_rows:  number
}

export interface OutlierInfo {
  total_count: number
  total_pct:   number
  per_sku:     Record<string, SkuOutlierInfo>
}

export interface DataQuality {
  issues:          DataQualityIssue[]
  gap_fill_needed: boolean
  // True when at least one issue is blocking — training this file cannot
  // produce a forecast.
  blocking?:       boolean
  outliers?:       OutlierInfo
}

export interface OutlierConfig {
  strategy:           string   // leave | winsorize_sigma | winsorize_pct | iqr_fence | remove | log1p
  n_sigma:            number
  percentile:         number
  iqr_k:              number
  per_sku_overrides:  Record<string, string>
  per_sku_n_sigma:    Record<string, number>
  per_sku_percentile: Record<string, number>
  per_sku_iqr_k:      Record<string, number>
}

export interface DataProfile {
  columns:       ProfileColumn[]
  recommended:   { date: string | null; target: string | null; group: string | null; freq: string | null }
  stats:         { n_rows: number; n_cols: number; n_skus?: number; date_min?: string; date_max?: string }
  warnings:      string[]
  data_quality?: DataQuality
}

// ── Run warnings (validation layers, WARNING mode) ───────────────────────────
// `code` is the engine's stable English error_id; the Spanish comes from i18n.
export interface RunWarningSample {
  message:     string
  context:     Record<string, unknown>
  suggestions: string[]
}

export interface RunWarningGroup {
  code:     string
  severity: 'error' | 'warning' | 'info'
  layer:    string
  count:    number
  samples:  RunWarningSample[]
}

export interface RunCorrection {
  action:      string
  description: string
  n_skus:      number
}

export interface RunWarnings {
  validation:  RunWarningGroup[]
  corrections: RunCorrection[]
}

export interface ColumnOptions {
  date_candidates:        string[]
  target_candidates:      string[]
  group_candidates:       string[]
  exog_candidates:        string[]
  canonical_suggestions?: CanonicalMapping   // NEW
}

// ── Canonical mapping (14-field schema) ──────────────────────────────────────
export interface CanonicalFieldSuggestion {
  top:             string | null
  candidates:      string[]
  confidence:      number
  can_use_default: boolean
}

export type CanonicalMapping = Record<string, CanonicalFieldSuggestion>

export interface CanonicalColumnsBody {
  canonical_mapping:  Record<string, string | null>
  defaults_override?: Record<string, unknown>
}

// Mirrors `SKUReport.to_dict()` in the engine
// (ForecastingCore/forecasting_core/data/quality.py). The fields below the
// original seven were always sent and simply undeclared, so the UI could not
// reach the engine's own decisions — which is why the quality panel ended up
// re-printing its English sentences instead of rebuilding them from the data.
// Optional because a session trained before a field existed lacks it.
export interface QualityReport {
  [sku: string]: {
    quality_score: number
    series_type:   string
    n_rows:        number
    missing_pct:   number | null
    n_outliers:    number
    warnings:      string[]
    is_valid:      boolean
    /** The engine's own "is there enough history" verdict, not a threshold the
     *  UI may re-derive. */
    has_min_history?: boolean
    missing_dates?:   number
    zero_ratio?:      number
    /** Multi-label classification: 'seasonal', 'intermittent', 'volatile', … */
    series_flags?:    string[]
    /** flag → why the engine assigned it, e.g. "STL strength=0.32 > 0.3". */
    series_reasons?:  Record<string, string>
  }
}

// ── Inspection result (from GET /sessions/{id}/inspect) ───────────────────────
// Per-SKU temporal granularity. `conflict` means some products are reported
// daily and others monthly in the same file: they would all train on one time
// axis, so a monthly-reported SKU gets modeled as a daily series and its
// reorder quantity comes out roughly 30x off.
export interface GranularityDetection {
  status:            'homogeneous' | 'conflict' | 'unknown'
  detected:          string[]
  skus_by_frequency: Record<string, string[]>
  suggested_target:  string | null
}

// ── Guided upload ─────────────────────────────────────────────────────────────
export interface GuidanceOption {
  id:       string
  decision: Record<string, unknown>
  params:   Record<string, unknown>
}

export interface GuidanceFinding {
  code:       string
  severity:   'info' | 'auto' | 'ask' | 'block'
  check:      string
  column:     string | null
  confidence: number
  params:     Record<string, unknown>
  options?:   GuidanceOption[]
  examples?:  Array<Record<string, unknown> | string | string[]>
  handled_by?: string
}

export interface GuidanceSummary {
  rows: number; products: number; periods: number
  date_min: string; date_max: string; history_days: number
  granularity: 'daily' | 'weekly' | 'monthly' | 'irregular' | null
  step_days: number; short_products: number; short_threshold: number; history_ok: boolean
}

export interface GuidanceReport {
  version:       number
  verdict:       'ready' | 'ask' | 'unusable'
  findings:      GuidanceFinding[]
  fixes:         { code: string; params: Record<string, unknown> }[]
  questions:     GuidanceFinding[]
  next_question: GuidanceFinding | null
  needs_apply:   boolean
  shape:         'long' | 'wide'
  mapping:       Partial<Record<'sku' | 'date' | 'demand' | 'store', string>>
  columns:       { slot: 'sku' | 'date' | 'demand'; column: string | null;
                   status: 'ok' | 'check' | 'bad'; reason: string | null; samples?: unknown[] }[]
  checks:        { id: string; status: 'ok' | 'check' | 'ask' | 'block' | 'pending' }[]
  checks_done:   number
  checks_total:  number
  summary:       GuidanceSummary | null
  preview:       {
    columns: string[]
    rows: { cells: unknown[]; read: { sku?: string | null; date?: string | null; demand?: number | null } }[]
    slots: Record<string, string>
    total_rows: number
  } | null
  sheet:         { selected: string; ranked: { name: string; score: number; rows: number }[] } | null
}

export interface GuidedRecord {
  source_dataset_id: string
  derived_dataset_id: string
  fixes: { code: string; params: Record<string, unknown> }[]
  decisions: Record<string, unknown>
  mapping: Record<string, string>
  findings: Pick<GuidanceFinding, 'code' | 'severity' | 'check' | 'column' | 'params'>[]
  rows_in: number
  rows_out: number
  applied_at: string
}

export interface InspectionResult {
  guidance?:              GuidanceReport | null
  guidance_error?:        string | null
  guided_reading?:        GuidedRecord | null
  profile:                DataProfile
  column_options:         ColumnOptions
  canonical_suggestions?: CanonicalMapping   // NEW (also nested in column_options)
  config_schema:          ConfigSchema | null
  granularity?:           GranularityDetection
  inspected_at:           string
}

// ── Config ────────────────────────────────────────────────────────────────────
export interface FieldSchema {
  type:    'float' | 'int' | 'bool' | 'int_list' | 'float_list'
  default: unknown
  min?:    number
  max?:    number
  label:   string
}

export interface ConfigSchema {
  training:         Record<string, FieldSchema>
  features:         Record<string, FieldSchema>
  forecast:         Record<string, FieldSchema>
  business:         Record<string, FieldSchema>
  available_models: string[]
}

export interface ChooseColumnsBody {
  target_column:  string
  date_column:    string
  sku_column?:    string | null
  exogenous?:     string[]
  transforms?:    Record<string, { impute?: string; encode?: string; scale?: string }>
  gap_fill?:      string          // zero | mean | forward | interpolate | leave
  outlier_config?: OutlierConfig
}

// ── Dataset Analysis (GET /sessions/{id}/analysis) ────────────────────────────
export interface DatasetAnalysisColumn {
  name:     string
  dtype:    string
  role:     'numeric' | 'categorical'
  null_pct: number
  n_unique: number
}

export interface DatasetAnalysis {
  columns:      DatasetAnalysisColumn[]
  n_rows:       number
  n_cols:       number
  n_duplicates: number
  memory_mb:    number
  temporal: {
    date_min:   string
    date_max:   string
    n_periods:  number
    freq_days:  number
    gap_count:  number
    freq_label: string
  } | null
  seasonality: {
    dominant_period:   number | null
    top_periods:       number[]
    seasonal_strength: number
    classification:    'none' | 'weak' | 'moderate' | 'strong'
  } | null
  sku_stats: {
    n_skus:             number
    intermittent_count: number
    short_series_count: number
    avg_zero_pct:       number
    min_series_len:     number
    max_series_len:     number
  } | null
  analyzed_at: string
}

// ── Model hyperparameter schema ───────────────────────────────────────────────
export interface HyperparamDef {
  name:     string
  type:     'int' | 'float' | 'bool' | 'select'
  default:  unknown
  min?:     number
  max?:     number
  options?: string[]
  desc:     string
}

// ── Job (training) ────────────────────────────────────────────────────────────
export interface JobProgress {
  percent:  number
  step:     string
  message:  string
}

export interface JobResponse {
  id:           string
  session_id:   string
  status:       'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  progress:     JobProgress
  error:        string | null
  created_at:   string
  started_at:   string | null
  completed_at: string | null
}

// ── Metrics & Results ─────────────────────────────────────────────────────────
export interface MetricRow {
  model:      string
  type:       string
  sku:        string | null
  store:      string | null   // NEW
  mae:        number | null
  rmse:       number | null
  wape:       number | null
  bias:       number | null
  mape:       number | null
  smape:      number | null
  // Asymmetric per-unit error: a unit short is charged more than a unit spare.
  // This — not WAPE — is what the engine ranks each SKU's models by, so any
  // screen naming "the model used" has to rank by the same thing.
  cost:       number | null
  // The same asymmetric cost measured over the FULL h-step forecast, which is
  // the only version comparable across model families. The engine and the
  // backend both prefer it; a screen that stops at `cost` will name a different
  // model than the one the purchase order came from.
  cost_horizon: number | null
  n_folds:    number | null
  validation: string | null
}

// ── Policy backtest ───────────────────────────────────────────────────────────
//
// What the buyer would have LIVED THROUGH, not how close the curve was. The
// engine replays the ordering policy over demand that actually happened, twice:
// once driven by the model's forecast and once by "repeat the last value" — the
// policy a distributor runs without the product. Every `baseline_` field is the
// second run, so the pair is the only meaningful reading; either number alone is
// an absolute floating free of any reference.
//
// See ForecastingCore/forecasting_core/business/policy_backtest.py.

/** One run of the simulation over one series. */
export interface PolicyOutcome {
  n_buckets:        number
  total_demand:     number
  units_short:      number
  units_ordered:    number
  stockout_buckets: number
  fill_rate:        number
  stockout_rate:    number
  avg_inventory:    number
  peak_inventory:   number
  days_of_cover:    number
  /** null when the session has no unit cost to value the stock with. */
  capital_tied_up:  number | null
}

export interface PolicySkuComparison {
  model:             string
  policy:            PolicyOutcome
  baseline:          PolicyOutcome
  fill_rate_gain:    number
  stockouts_avoided: number
  inventory_delta:   number
  capital_freed:     number | null
}

export interface PolicyBacktestSummary {
  /** How many series the simulation actually covers — usually FEWER than the
   *  catalogue, since only series whose model ran a rolling-origin backtest
   *  have both a past forecast and the actuals that followed it. Never render
   *  these figures without it. */
  n_series:                  number
  fill_rate:                 number
  baseline_fill_rate:        number
  stockout_buckets:          number
  baseline_stockout_buckets: number
  stockouts_avoided:         number
  avg_inventory:             number
  baseline_avg_inventory:    number
  capital_tied_up:           number | null
  baseline_capital_tied_up:  number | null
}

/** `{}` for runs whose models produced no rolling-origin backtest, and for
 *  every session trained before the engine computed this. Absent, not zero. */
export interface PolicyBacktest {
  summary?: PolicyBacktestSummary
  by_sku?:  Record<string, PolicySkuComparison>
}

/** Measured uncertainty of CUMULATIVE demand over a lead time: add
 *  `cumulative_offsets[L][q]` to the summed point forecast over L buckets to get
 *  that quantile of total lead-time demand. Present only for SKUs whose champion
 *  model ran a rolling-origin backtest. */
export interface DemandRiskEntry {
  model:              string
  quantiles:          number[]
  cumulative_offsets: Record<string, Record<string, number>>
}

/** GET /sessions/{id}/results — the whole stored training result. Every field is
 *  optional because sessions trained before a field existed simply lack it. */
export interface TrainingResults {
  job_id?:          string
  run_id?:          string
  completed_at?:    string
  metrics?:         MetricsResponse
  inventory?:       InventoryResponse
  demand_risk?:     Record<string, DemandRiskEntry>
  policy_backtest?: PolicyBacktest
  routing?:         RoutingPlan
  data_quality?:    Record<string, unknown>
  warnings?:        RunWarnings
}

export interface MetricsResponse {
  rows:     MetricRow[]
  by_model: Record<string, {
    avg_mae:   number
    avg_rmse:  number
    avg_wape:  number
    avg_bias:  number
    avg_mape:  number
    avg_smape: number
  }>
}

export interface InventoryRecommendation {
  sku:            string
  reorder_point:  number | null
  safety_stock:   number | null
  stockout_risk:  number | null
  holding_cost?:  number | null
  days_coverage?: number | null
  forecast_mean?: number | null
  overstock_alert?: boolean
  action:         'REORDER' | 'OVERSTOCK' | 'OK'
}

export interface InventoryResponse {
  recommendations: InventoryRecommendation[]
}

export interface RoutingPlan {
  [sku: string]: string[]
}

// ── Data Health Check ─────────────────────────────────────────────────────────
export interface SKUHealthReport {
  sku:             string
  n_rows:          number
  series_type:     string
  missing_dates:   number
  outliers:        number
  zero_ratio:      number
  quality_score:   number
  has_min_history: boolean
  warnings:        string[]
}

export interface HealthDiagnosis {
  executive_summary: string
  risks:             string
  recommendation:    string
}

export interface DataHealthReport {
  global_score:  number
  status:        'HEALTHY' | 'NEEDS_ATTENTION' | 'POOR'
  can_train:     boolean
  n_skus:        number
  sku_reports:   Record<string, SKUHealthReport>
  diagnosis:     HealthDiagnosis | null
  columns_used:  { date: string; target: string; group: string | null }
  analyzed_at:   string
}

// ── AI Analyst Chats ──────────────────────────────────────────────────────────

export interface Chat {
  id:                   string
  title:                string
  is_favorite:          boolean
  session_id:           string | null
  data_sources:         string[]
  last_message_at:      string
  message_count:        number
  created_at:           string
  last_message_preview: string | null
}

export interface ChatMessage {
  id:               string
  chat_id:          string
  role:             'user' | 'assistant'
  content:          string
  source?:          string
  retrieved_count?: number
  created_at:       string
}

export interface MessagesPage {
  messages: ChatMessage[]
  has_more: boolean
}

export interface ChatSourceType {
  id:    string
  label: string
}

// ── Data Sources ──────────────────────────────────────────────────────────────
export type DataSourceType = 'file' | 'sql'
export type ConnectionStatus = 'connected' | 'pending' | 'error'
export type SqlEngine = 'postgresql' | 'mysql' | 'mssql' | 'oracle'

export type SqlSslMode = 'disable' | 'prefer' | 'require' | 'verify-ca' | 'verify-full'

/** What the server keeps about an uploaded CA certificate (the PEM itself is
 *  stored encrypted and never sent back). */
export interface SqlCaInfo {
  subject:            string
  expires:            string
  fingerprint_sha256: string
  count:              number
}

export type ProbeStageName = 'dns' | 'tcp' | 'tls' | 'auth' | 'privileges' | 'select' | 'tables'
export type ProbeStatus = 'ok' | 'warning' | 'failed' | 'skipped'

export interface ProbeStage {
  stage:        ProbeStageName
  status:       ProbeStatus
  code?:        string
  params?:      Record<string, unknown>
  detail?:      Record<string, unknown>
  duration_ms?: number
}

/** The staged connection test (POST /data-sources/{id}/test-connection). */
export interface ConnectionProbe {
  ok:                boolean
  status:            'connected' | 'error'
  stages:            ProbeStage[]
  failed_stage:      ProbeStageName | null
  error:             string | null
  tested_at:         string
  write_access:      { can_write: boolean; privileges: string[]; superuser: boolean } | null
  grant_sql:         string[] | null
  tables_sample:     string[]
  table_count:       number | null
  tables_truncated?: boolean
  server_version:    string | null
  read_only_session: boolean | null
}

/** The summary of the last test kept on the source (no details, no SQL). */
export interface StoredProbe {
  ok:             boolean
  tested_at:      string
  failed_stage:   ProbeStageName | null
  error:          string | null
  stages:         ProbeStage[]
  can_write:      boolean | null
  table_count:    number | null
  server_version: string | null
}

export interface SqlConfig {
  host:                string
  port:                number
  database:            string
  username:            string
  engine:              SqlEngine
  ssl_mode?:           SqlSslMode
  has_password?:       boolean
  has_ssl_ca?:         boolean
  ssl_ca?:             SqlCaInfo | null
  connect_timeout_s?:  number
  statement_timeout_s?: number
  last_test?:          StoredProbe | null
}

/** Fields of a parsed connection string (the password only as a flag). */
export interface ParsedConnectionString {
  engine?:      SqlEngine
  host?:        string
  port?:        number
  database?:    string
  username?:    string
  ssl_mode?:    SqlSslMode
  has_password: boolean
}

export interface SchemaTable {
  schema:       string | null
  name:         string
  kind:         'table' | 'view'
  row_estimate: number | null
  select_sql:   string | null
}

export interface SchemaTables {
  tables:    SchemaTable[]
  truncated: boolean
  cap:       number
  cached_at: string
}

export interface SchemaColumns {
  schema:     string
  table:      string
  truncated:  boolean
  columns:    { name: string; type: string; nullable: boolean }[]
  cached_at:  string
  select_sql: string | null
}

export interface DataSource {
  id:                string
  name:              string
  description:       string | null
  source_type:       DataSourceType
  connection_status: ConnectionStatus
  original_filename: string | null
  file_type:         string | null
  file_path:         string | null
  size_bytes:        number | null
  row_count:         number | null
  column_count:      number | null
  sql_config:        SqlConfig | null
  saved_query:       string | null
  uploaded_by:       string | null
  uploaded_at:       string
  updated_at:        string | null
  parent_id:         string | null
}

export interface DataPreview {
  columns:      string[]
  rows:         Record<string, unknown>[]
  row_count:    number
  sheets:       string[] | null
  active_sheet: string | null
  truncated:    boolean
}

export interface EditableTable {
  columns: string[]
  rows:    Record<string, unknown>[]
}

export interface SqlQueryResult {
  columns:     string[]
  rows:        Record<string, unknown>[]
  row_count:   number
  truncated:   boolean
  offset?:     number
  limit?:      number
  has_more?:   boolean
  elapsed_ms?: number
}

// ── Forecast Series (ECharts) ─────────────────────────────────────────────────
export interface ForecastPoint {
  date:   string
  value:  number
  lower?: number
  upper?: number
}

export interface ForecastSeries {
  sku:              string
  model:            string | null
  historical:       { date: string; value: number }[]
  forecast:         ForecastPoint[]
  available_models: string[]
}

// ── User Preferences ──────────────────────────────────────────────────────────
export interface UserPreferences {
  language:         'es' | 'en'
  theme:            'dark' | 'light'
  /** Forward new direct messages as an SMS to the user's linked number. */
  dm_sms_enabled:   boolean
}

// ── Team messaging (GET/POST /messages/*) ─────────────────────────────────────
export interface DmContact {
  id:        string
  full_name: string | null
  email:     string
  role:      string
}

export interface DmConversation {
  counterpart_id: string
  full_name:      string | null
  email:          string
  last_body:      string
  last_at:        string
  last_is_mine:   boolean
  unread_count:   number
}

export interface DirectMessage {
  id:           number
  sender_id:    string
  recipient_id: string
  body:         string
  read_at?:     string | null
  created_at:   string
}

export interface DmThread {
  counterpart: { id: string; full_name: string | null; email: string }
  messages:    DirectMessage[]
  has_more:    boolean
}

// ── Authenticated user (GET /users/me) ────────────────────────────────────────
// The backend returns the full user row minus the password hash. A WhatsApp
// number is only usable once `whatsapp_verified_at` is set.
export interface MeUser {
  id:                    string
  tenant_id:             string
  email:                 string
  full_name:             string | null
  role:                  string
  status:                string
  email_verified?:       boolean
  whatsapp_number:       string | null
  whatsapp_verified_at:  string | null
  last_login_at?:        string | null
  created_at?:           string
  updated_at?:           string
}

// ── Activity Logs ─────────────────────────────────────────────────────────────
export interface ActivityLog {
  id:         string
  action:     string
  resource:   string | null
  context:    Record<string, unknown>
  status:     'success' | 'error'
  created_at: string
}

export interface ActivityLogsResponse {
  items: ActivityLog[]
  total: number
}

// ── Platform Models ───────────────────────────────────────────────────────────
export interface PlatformModel {
  name:        string
  category:    'ML' | 'Statistical' | 'Deep Learning'
  status:      'available' | 'beta' | 'disabled'
  description: string
}

// ── Statistical Analysis ──────────────────────────────────────────────────────
export interface AnalysisSummaryRow {
  sku:                   string | null
  n:                     number | null
  mean:                  number | null
  std:                   number | null
  min:                   number | null
  max:                   number | null
  median:                number | null
  cv:                    number | null
  skewness:              number | null
  kurtosis:              number | null
  zero_pct:              number | null
  outlier_pct:           number | null
  best_distribution:     string | null
  croston_class:         string | null
  adi:                   number | null
  cv2:                   number | null
  stationarity:          string | null
  diff_order:            number | null
  dominant_period:       number | null
  seasonal_strength:     number | null
  seasonality_class:     string | null
  trend_direction:       string | null
  trend_pvalue:          number | null
  sens_slope:            number | null
  linear_r2:             number | null
  n_change_points:       number | null
  suggested_ar_order:    number | null
  suggested_ma_order:    number | null
  is_white_noise:        boolean | null
  error?:                string
  [key: string]:         unknown
}

export interface AnalysisResult {
  date_col:   string
  target_col: string
  sku_col:    string | null
  detected:   { date_col: string | null; target_col: string | null; sku_col: string | null }
  columns:    string[]
  summary:    AnalysisSummaryRow[]
}

export interface OutlierPoint {
  date:         string
  value:        number
  z_score:      number
  lower_bound:  number
  upper_bound:  number
  reason:       string
}

export interface SkuDetailResult {
  sku:      string
  report:   Record<string, unknown>
  series:   { date: string; value: number | null }[]
  outliers: OutlierPoint[]
}

// ── Forecast Overrides ────────────────────────────────────────────────────────
export interface ForecastOverride {
  sku:      string
  date:     string
  original: number
  override: number
  reason?:  string
}

// ── Accuracy Tracking ─────────────────────────────────────────────────────────
export interface AccuracySnapshot {
  sku:        string
  date:       string
  forecasted: number
  actual:     number | null
  mae:        number | null
  wape:       number | null
}

export interface AccuracyReport {
  snapshots:    AccuracySnapshot[]
  overall_wape: number | null
  threshold:    number
}

// ── API Keys ──────────────────────────────────────────────────────────────────
export type ApiKeyScope = 'read' | 'write'

export interface ApiKey {
  id:         string
  name:       string
  role:       'viewer' | 'analyst'
  scope:      ApiKeyScope
  last4:      string | null
  last_used:  string | null
  expires_at: string | null
  created_at: string
}

export interface ApiKeyUsage {
  month:    string            // YYYY-MM, UTC
  timezone: 'UTC'
  total:    number
  today:    number | null     // null when `month` is not the current one
  by_day:   { day: string; calls: number }[]
  by_key:   { api_key_id: string; name: string; scope: ApiKeyScope | null; active: boolean; calls: number }[]
  limits:   { per_minute_per_key: number; per_day_per_key: number | null }
}

// ── Webhooks ──────────────────────────────────────────────────────────────────
export interface Webhook {
  id:         string
  url:        string
  events:     string[]
  created_at: string
}

// ── Job Schedule ──────────────────────────────────────────────────────────────
export interface JobSchedule {
  id:         string
  session_id: string
  cron_expr:  string
  next_run:   string
  enabled:    boolean
  // A trigger that has been failing for weeks looks identical to a healthy one
  // without these, which is why the API sends them.
  last_run?:       string | null
  last_error?:     string | null
  last_error_at?:  string | null
}

// ── Inventory ─────────────────────────────────────────────────────────────────
export type InventorySignal = 'PEDIR_YA' | 'PEDIR_PRONTO' | 'OK' | 'SOBRESTOCK' | 'SIN_DATOS'

// ── Semáforo multipliers (backend/inventory/signal_thresholds.py) ─────────────
/** PEDIR_YA below `order_now_factor` x lead time; SOBRESTOCK from
 *  `overstock_factor` x lead time (or twice the reorder point, if larger). The
 *  PEDIR_PRONTO boundary is the reorder point and is not a multiplier. */
export interface SignalThresholdFactors {
  order_now_factor: number
  overstock_factor: number
}
export type SignalThresholdScope = 'global' | 'supplier' | 'category'
export interface ResolvedSignalThresholds extends SignalThresholdFactors {
  source:      'default' | SignalThresholdScope
  scope_value: string | null
}
export interface SignalThresholdRule extends SignalThresholdFactors {
  scope_type:  SignalThresholdScope
  scope_value: string | null
  updated_at:  string | null
}
export interface SignalThresholdsState {
  defaults:  SignalThresholdFactors
  /** The tenant-wide rule; null when nobody configured one (defaults apply). */
  tenant:    SignalThresholdRule | null
  effective: SignalThresholdFactors
  source:    'default' | 'global'
  overrides: SignalThresholdRule[]
  bounds:    Record<keyof SignalThresholdFactors, { min: number; max: number }>
  overstock_reorder_point_multiple: number
}
export interface SignalThresholdsPreview {
  available:      boolean
  reason?:        'no_session'
  total?:         number
  changed?:       number
  counts_before?: Record<InventorySignal, number>
  counts_after?:  Record<InventorySignal, number>
  /** "FROM>TO" -> how many products make that move. */
  transitions?:   Record<string, number>
  sample?:        { sku: string; name: string | null; from: InventorySignal; to: InventorySignal }[]
}

export interface InventoryStock {
  id?:            string
  sku:            string
  display_name:   string | null
  current_stock:   number
  min_stock:   number
  // Rows from /inventory/stock are per (sku, warehouse) since the 5.4
  // migration — the column was always in the DB, the type just lagged.
  warehouse?:     string | null
  lead_time_days: number
  unit_cost: number | null
  moq:            number
  supplier:      string | null
  notes:          string | null
  product_type?:  string
  service_level?: number
  updated_at?:    string
  sale_price?:  number | null
  category?:     string | null
  /** Grouping between category and SKU; event multipliers can target it. */
  family?:        string | null
  brand?:         string | null
  unit_of_measure?: string | null
  barcode?: string | null
}

export interface InventoryCalcExplanation {
  suficiente?:        boolean
  daily_demand?:    number
  lead_time_days?:    number
  // Where the lead time came from: user | file | supplier_rule | learned |
  // default. 'default' means StockAI assumed it — the case the old
  // 'learned' | 'configured' pair could not express, so an untouched SKU was
  // labelled "configurado por ti".
  lead_time_source?: ValueSource
  // supplier | category | global — set only when a stock_defaults rule won.
  lead_time_rule_scope?: RuleScope | null
  lead_time_demand?: number
  safety_stock?:      number
  current_stock?:      number
  antes_moq?:         number
  // Units already on their way (sent POs + transfers in transit), subtracted
  // before `antes_moq`.
  incoming?:          number
  moq?:               number
  final_qty?:    number
  // Days the order has to cover: lead time + the supplier's review period.
  protection_interval_days?: number
  // A declared event (stability.md 19.5) that overlaps THIS sku's lead-time
  // window and moved the recommendation — never a simulation, a standing
  // fact the semáforo already applied. Empty when no event touches the
  // window right now, even if one is declared for a different date range.
  events_applied?:     InventoryCalcEventApplied[]
  // A person's manual adjustment of this product's forecast that moved the
  // number: who, how much, why. Empty when none applies.
  adjustments_applied?: AppliedAdjustment[]
  // Customer orders placed ahead of time that were added on top of the
  // forecast for this product. Empty when none applies.
  committed_applied?: CommittedApplied[]
  // Set when a person's analogy stood in for a trained model on this product.
  analogy_applied?: AnalogyApplied[]
}

/** One entry of `committed_applied` on a recommendation row. */
export interface CommittedApplied {
  commitment_id: string
  customer: string | null
  delivery_date: string
  quantity: number
  probability: number
  units: number
  scope: string
  overdue: boolean
}

export type CommittedDemandStatus = 'open' | 'fulfilled' | 'cancelled'

/** A customer order placed ahead of time (committed demand). */
export interface CommittedDemand {
  id: string
  sku: string
  warehouse_id: string | null
  delivery_date: string
  quantity: number
  customer: string | null
  /** 0-1 on the wire; the UI shows it as a percentage. */
  probability: number
  on_top_of_base: boolean
  status: CommittedDemandStatus
  note: string | null
  overdue: boolean
  /** Open items only. null = no verdict (closed, or no stock row for the SKU). */
  at_risk?: boolean | null
  shortfall?: number | null
  covered_units?: number | null
  latest_safe_order_date?: string | null
  /** The latest safe order date is already behind us. */
  order_date_passed?: boolean | null
}

/** One line of the "by customer" summary of open commitments. */
export interface CommittedDemandCustomer {
  customer: string | null
  open: number
  at_risk: number
  unknown: number
  shortfall: number
  first_safe_order_date: string | null
}

export interface CommittedDemandInput {
  sku: string
  delivery_date: string
  quantity: number
  customer?: string | null
  probability?: number
  warehouse_id?: string | null
  on_top_of_base?: boolean
  note?: string | null
}

/** One declared event whose window overlapped this sku's lead-time window,
 *  as `_event_demand_multiplier` (backend/inventory/service.py) resolved it. */
export interface InventoryCalcEventApplied {
  event_id:           string
  event_name:         string
  /** The multiplier actually resolved for this sku — the event's own figure,
   *  or the narrowest override that matched it. */
  multiplier:          number
  /** 'event' = the event's own multiplier; 'sku' | 'family' | 'category' =
   *  an override the tenant set took effect instead. */
  multiplier_source:  'sku' | 'family' | 'category' | 'event'
  overlap_days:        number
  window_days:         number
  /** What was actually applied to the window's demand after blending for
   *  partial overlap — smaller than `multiplier` whenever the event covers
   *  only part of the lead-time window. */
  blended_multiplier: number
}

export interface InventoryEvent {
  id:         string
  tenant_id:  string
  name:       string
  start_date: string
  end_date:   string
  multiplier: number
  notes:      string | null
  created_at: string
  /** Preloaded LatAm calendar events carry these; user-created ones do not. */
  catalog_key?: string | null
  country?:     string | null
  source?:      'catalog' | 'user'
  active?:      boolean
}

/** One entry of the preloaded LatAm commercial calendar (feature 3.4). */
export interface CalendarCatalogEntry {
  key:         string
  name:        string
  country:     string
  multiplier:  number
  notes:       string
  seeded:      boolean
  occurrences: number
  active:      boolean
  next_start:  string | null
}

export interface CalendarCatalogResponse {
  country:   string
  countries: string[]
  entries:   CalendarCatalogEntry[]
}

export interface CalendarSeedResult {
  country:         string
  inserted:        number
  already_present: number
  total_catalog:   number
}

// ── Multi-warehouse (feature 5.4) ────────────────────────────────────────────

export interface Warehouse {
  id: string
  name: string
  is_default: boolean
  demand_share: number | null
}

/** Structured explanation of a transfer-vs-buy decision (PENDIENTES #2).
 * The backend NEVER ships a rendered sentence — the UI renders the Spanish
 * from `transfers.reason_<reason_code>` with these params. */
export interface TransferReason {
  reason_code:
    | 'transfer_faster_and_cheaper'
    /** Accepted on the time argument alone — no unit cost on file, so the money
     *  comparison never ran. This used to be reported as
     *  `transfer_faster_and_cheaper`, which told the buyer the move "costs less
     *  than buying" about a comparison that never happened. */
    | 'transfer_faster_price_unknown'
    | 'transfer_too_slow'
    | 'transfer_more_expensive'
  params: {
    from_warehouse: string
    qty: number
    lane_days: number
    purchase_days: number
    /** True when this pair has no configured lane, so `lane_days` and the costs
     *  are transfer_lane_service's optimistic fallback rather than a
     *  measurement. */
    lane_is_default?: boolean
    /** Money saved vs buying; null when no unit cost is on file. */
    saving?: number | null
    transfer_cost?: number
    purchase_cost?: number
  }
}

export interface TransferSuggestion {
  from_warehouse: string
  qty: number
  /** Why the transfer won (always 'transfer_faster_and_cheaper' today).
   * Absent on legacy payloads. */
  reason_code?: TransferReason['reason_code']
  params?: TransferReason['params']
  /** Lane transit time in days, from the configured transfer lane. */
  lane_days?: number
  /** null = donor has no measurable demand (ample coverage) — same null
   * convention as coverage_days; the backend never ships a 9999 sentinel.
   * The value is expressed in `coverage_unit` (day/week/month), matching the
   * active planning period — NOT always days. */
  donor_coverage_days_after: number | null
  /** Unit the value above is in, mirroring the status envelope's coverage_unit
   * so the UI labels it "N semanas" under a weekly horizon, not "N días".
   * Absent on legacy payloads -> the UI falls back to 'day'. */
  coverage_unit?: CoverageUnit
}

/** One row of the network-aware per-(SKU, warehouse) semáforo. */
export interface WarehouseStatusItem {
  sku: string
  warehouse: string
  display_name: string | null
  supplier: string | null
  current_stock: number | null
  lead_time_days: number
  lead_time_source: ValueSource
  lead_time_rule_scope?: RuleScope | null
  moq: number
  daily_demand: number | null
  coverage_days: number | null
  reorder_point: number | null
  signal: InventorySignal
  /** Why this row has no signal, when the reason is something the buyer can
   *  fix. `stock_not_recorded_in_this_warehouse` means nobody ever recorded
   *  stock for this SKU HERE — which is not the same as zero, and reading it as
   *  zero is what put every branch of an ERP-synced tenant in PEDIR_YA at full
   *  reorder quantity (stability 11.5). */
  sin_datos_reason?: string | null
  recommended_qty: number | null
  recommended_action: 'order' | 'transfer' | null
  transfer_suggestion: TransferSuggestion | null
  /** Set when a transfer was possible on coverage but LOST against buying
   * (too slow / more expensive). Rendered as plain text, never as an alert. */
  transfer_rejected_reason?: TransferReason | null
  /** An OPTION next to the purchase, not the recommendation: a donor that can
   *  cover part of the gap on a sound lane. Accepting it leaves the rest to be
   *  bought, and the purchase shrinks by itself (in-transit units net out). */
  partial_transfer?: {
    from_warehouse: string
    qty: number
    remaining_qty: number
    lane_days: number
  } | null
  unit_cost: number | null
}

/** Time + money for one ordered (from, to) warehouse pair (PENDIENTES #2).
 * A pair with no lane on file falls back to the backend default: 1 day, free. */
export interface TransferLane {
  id: string
  from_warehouse: string
  to_warehouse: string
  lead_time_days: number
  cost_per_unit: number
  fixed_cost: number
}

export interface WarehouseStatusResponse {
  items: WarehouseStatusItem[]
  period?:        PlanningPeriod
  coverage_unit?: CoverageUnit
  summary: {
    total_rows: number
    order_now: number
    order_soon: number
    transfers_suggested: number
  }
}

export interface TransferItem {
  id: string
  sku: string
  qty_sent: number
  qty_received: number
}

export interface Transfer {
  id: string
  from_warehouse: string
  to_warehouse: string
  status: 'in_transit' | 'partial' | 'received' | 'cancelled' | 'closed'
  notes: string | null
  created_by: string
  created_at: string
  received_at: string | null
  /** Lane lead time frozen at send time; null on pre-lane transfers. */
  lead_time_days?: number | null
  /** created_at + lead_time_days; null on pre-lane transfers. */
  expected_arrival?: string | null
  items: TransferItem[]
}

/** Why a row's service level cannot be taken at face value. */
export type ServiceLevelCaveat = 'intermittent_demand'

export interface InventoryStatusItem extends InventoryStock {
  has_forecast:         boolean
  has_stock:            boolean
  /** Set when the supplier came from the SKU's configured primary supplier
   *  (sku_suppliers); a free-text name on the stock row has no id. */
  supplier_id?:         string | null
  daily_demand:       number | null
  lead_time_demand:    number | null
  coverage_days:       number | null
  signal:               InventorySignal
  /** The lead-time multipliers this row's signal was judged by, and which
   *  rule they came from. Absent on a backend older than the feature. */
  signal_thresholds?:   ResolvedSignalThresholds
  recommended_qty: number | null
  /** Units already on their way and not yet received: purchase orders the buyer
   *  has SENT, plus transfers in transit into this warehouse. Subtracted from
   *  `recommended_qty` — without it the buyer was told to order the same units
   *  again every day until they landed. Show it wherever the quantity is shown,
   *  or a drop to 0 looks like the app forgetting. */
  incoming_qty?:        number
  /** Which open orders / transfers make up `incoming_qty`, so the screen can
   *  say "426 on the way (OC-000001, OC-000002)". `reference` is the order
   *  number for a PO and the origin warehouse for a transfer. */
  incoming_sources?:    IncomingSource[]
  /** Customer orders placed ahead of time that count for this row. Set even when
   *  the row has no forecast or no stock row. */
  committed_applied?:   CommittedApplied[]
  /** Where the demand came from: a model fitted on this product, a person's
   *  analogy (never a trained forecast), or none. */
  forecast_source?:     'trained' | 'analogy' | null
  /** True for an analogy row: show it as a soft estimate. */
  low_confidence?:      boolean
  analogy_applied?:     AnalogyApplied[]
  /** A trained model has since taken over from an analogy. */
  analogy_retired?:     { analogy_id: string; retired_at: string } | null
  /** An analogy exists but did not apply (no reference forecast, or no stock). */
  analogy_unavailable?: { analogy_id: string; references_missing: string[]; reason?: string } | null
  /** True when this row's signal came from its commitments alone (no forecast
   *  or no stock row). `committed_shortfall` = units no stock plus incoming
   *  covers; `committed_stock_unknown` = no stock figure, counted as zero and
   *  no quantity recommended. */
  committed_only?:          boolean
  committed_shortfall?:     number
  committed_stock_unknown?: boolean
  inventory_value:     number | null
  n_models:             number
  abc:                  string
  xyz:                  string
  abc_xyz:              string
  stock_history:        { stock: number; date: string }[]
  calc_explanation:     InventoryCalcExplanation | null
  // The "why" behind the recommendation — all computed in the backend
  lead_time_source?:      ValueSource
  lead_time_rule_scope?:  RuleScope | null
  lead_time_configured?: number
  lead_time_learned?:   number | null
  // Provenance of the other planning values, same vocabulary.
  unit_cost_source?:      ValueSource
  moq_source?:            ValueSource
  moq_rule_scope?:        RuleScope | null
  service_level_source?:  ValueSource
  service_level_rule_scope?: RuleScope | null
  /** Set when this SKU's cushion was MEASURED unable to keep the service
   *  level (stability.md 17b). The screen must say so next to the %. */
  service_level_caveat?:  ServiceLevelCaveat | null
  reorder_point?:         number | null
  // English fallback sentence. The Spanish is rendered by the frontend from
  // explanation_code + explanation_params (lib/explanationCopy.ts).
  explanation?:           string | null
  explanation_code?:      string | null
  explanation_params?:    Record<string, unknown> | null
  // Gross margin per unit; null when sale_price or unit_cost is missing.
  unit_margin?:       number | null
}

// transfer_loss is system-generated (closing a partial transfer) — the manual
// shrinkage form does not offer it, but history rows can carry it.
export type ShrinkageReason = 'breakage' | 'expiry' | 'self_consumption' | 'gift' | 'transfer_loss'

export interface ShrinkageRecord {
  id:             string
  tenant_id:      string
  sku:            string
  warehouse:         string
  quantity:       number
  reason:         ShrinkageReason
  unit_cost: number | null
  total_cost:    number | null
  notes:          string | null
  created_by:     string | null
  created_at:     string
}

export type ProductType =
  | 'finished_good' | 'semi_finished' | 'component'
  | 'raw_material'  | 'packaging'     | 'service'

export interface BomItem {
  id:           string
  parent_sku:   string
  child_sku:    string
  child_name:   string | null
  quantity:     number
  unit:         string | null
  notes:        string | null
  child_stock:  number | null
  child_type:   string | null
  child_cost:   number | null
}

export interface ProductionRequirement {
  child_sku:          string
  display_name:       string
  product_type:       string
  quantity_per_unit:  number
  unit:               string | null
  required_quantity:  number
  current_stock:      number
  shortage:           number
  status:             'SHORTAGE' | 'OK'
}

export interface FinishedGoodPlan {
  sku:             string
  display_name:    string
  product_type:    string
  forecast_demand: number
  current_stock:   number
  to_produce:      number
  signal:          string
  requirements:    ProductionRequirement[]
}

export interface RawMaterialSummary {
  sku:            string
  display_name:   string
  product_type:   string
  unit:           string | null
  total_required: number
  current_stock:  number
  shortage:       number
  status:         'SHORTAGE' | 'OK'
  must_order:     number
  estimated_cost: number | null
}

export interface ProductionPlan {
  session_id:           string
  horizon_days:         number
  finished_goods_count: number
  has_shortages:        boolean
  total_shortage_value: number
  finished_goods:       FinishedGoodPlan[]
  raw_material_summary: RawMaterialSummary[]
}

// A product that was uploaded but left out of the forecast, with the reason.
export interface ExcludedSku {
  sku:     string
  n_rows:  number
  reason:  'insufficient_history' | 'no_forecast' | string
  // Rows needed, present only on `insufficient_history` — the sentence quotes it.
  min_history?: number
  // English fallback. The rendered line comes from `inventory.excluded_reason.<reason>`.
  detail:  string
}

// ── Purchasing/transfers optimizer (MW-3) ─────────────────────────────────────

export interface OptimizationOrder {
  sku:             string
  warehouse:          string
  qty:             number
  unit_cost:  number | null
  supplier:       string | null
  // The solve ran on a placeholder cost for this line, so its share of
  // `total_cost` means nothing. Optional: a response from before this existed
  // has no flag. See backend/inventory/optimizer_service.py.
  assumed_unit_cost?:  boolean
  // Math audit O1, owner's decision "igual que el Panel": a SKU whose next
  // order lands past the horizon is not solved by the MILP; it carries the
  // Panel's own quantity, and `effective_horizon_days` is the lead time +
  // review period that quantity protects.
  sized_like_panel?:       boolean
  horizon_extended?:       boolean
  effective_horizon_days?: number
}

export interface OptimizationTransfer {
  sku:          string
  from_warehouse:  string
  to_warehouse:    string
  qty:          number
}

export interface OptimizationResponse {
  status:        'optimal' | 'fallback'
  total_cost:    number
  horizon_days:  number
  orders:        OptimizationOrder[]
  transfers:     OptimizationTransfer[]
  // Lines whose plan reaches past `horizon_days` (see OptimizationOrder).
  extended_lines?: number
  // SKUs left out of the optimization because nobody has told us what is on the
  // shelf. How much to buy depends on how much is left, so there is no honest
  // quantity to show — the screen names them instead of printing a number.
  needs_stock?:  string[]
}

export type CoverageUnit = 'day' | 'week' | 'month'

export interface InventoryStatusResponse {
  items: InventoryStatusItem[]
  excluded_skus?: ExcludedSku[]
  // Active planning period (multi-period Phase C). Coverage values in `items`
  // are expressed in `coverage_unit`; the UI labels them accordingly.
  period?:        PlanningPeriod
  coverage_unit?: CoverageUnit
  summary: {
    total_skus:               number
    order_now:                 number
    order_soon:             number
    ok:                       number
    overstock:               number
    sin_datos:                number
    total_inventory_value:   number
    /** Over the whole filtered set, never the loaded page (page requests only). */
    without_stock?:          number
    with_forecast?:          number
  }
  /** Present only when the request asked for a page. `total` counts the
   *  filtered set. */
  page?: { limit: number; offset: number; total: number; sort: string; order?: string } | null
}

export type InventoryStatusSort =
  | 'urgency' | 'decision' | 'supplier_urgency' | 'signal' | 'name' | 'stock' | 'coverage'
  | 'demand_lt' | 'qty' | 'lead_time' | 'moq' | 'abc_xyz' | 'value'

export interface InventoryStatusPageParams {
  limit: number
  offset?: number
  sort?: InventoryStatusSort
  order?: 'asc' | 'desc'
  q?: string
  signal?: string
  /** ABC class (value ranking of the whole catalogue). */
  abc?: AbcClass
  /** Exact SKUs to look up (names of a known few rows). */
  skus?: string[]
}

export type AbcClass = 'A' | 'B' | 'C'

/** One ABC class in the "service level by class" suggestion. */
export interface ServiceLevelClassRow {
  abc: AbcClass
  skus: number
  /** Share of the catalogue's demand value, 0..1. */
  value_share: number
  /** Mean service level the class plans on today (null when it has no SKUs). */
  current_service_level: number | null
  suggested_service_level: number
  /** SKUs a click would change (unconfigured, and not already at the suggestion). */
  would_change: number
  /** SKUs whose level someone already set: left alone. */
  owned: number
}

export type ServiceLevelClassesState =
  | { available: false; reason: string }
  | { available: true; session_id: string; classes: ServiceLevelClassRow[];
      cutoffs: { a: number; b: number; x: number; y: number } }

export interface ServiceLevelClassApplied {
  abc: AbcClass
  service_level: number
  updated: number
  kept_own_level: number
  without_stock_row: number
  skus: string[]
}

export interface SuppliersPageResponse {
  items: Supplier[]
  total: number
  limit: number
  offset: number
}

export interface InventoryDashboardSummary {
  session_id:             string
  total_skus:             number
  order_now:               number
  order_soon:           number
  ok:                     number
  overstock:             number
  sin_datos:              number
  total_inventory_value: number
  top_critical:           { sku: string; display_name: string | null; coverage_days: number | null }[]
}

// ── Inventory ROI ─────────────────────────────────────────────────────────────
export interface InventoryROISummary {
  total_pos_generated:       number
  total_skus_protected:      number
  total_units_ordered:       number
  estimated_value_protected: number
  // Adoption metrics (decision tracking)
  total_suggested:           number
  total_approved:            number
  total_rejected:            number
  adoption_rate:             number | null
  first_po_at:               string | null
  last_po_at:                string | null
  active_days:               number
  pos_this_month:            number
  pos_last_month:            number
}

/** Why `capital_freed` is what it is. A single null used to mean both "we never
 *  took one of the two measurements" and "we took both and overstock GREW", and
 *  the UI printed the first sentence for both cases — so the column could only
 *  ever report good news. */
export type CapitalFreedStatus = 'measured' | 'not_measured' | 'grew'

export interface ROIMonthlyRow {
  month:             string          // 'YYYY-MM'
  pos_count:         number
  skus_order_now:     number
  total_value:       number
  adoption_rate:     number | null
  capital_freed:     number | null
  capital_freed_status: CapitalFreedStatus
}

// Monthly recap (feature 3.2). A null metric means "could not be derived from
// this tenant's own records" — render it as unavailable, never as zero.
export interface ROIMonthReport {
  month:                   string          // 'YYYY-MM'
  has_sufficient_history:  boolean
  orders_generated:        number
  recommendations_shown:   number
  recommendations_followed: number
  adoption_rate:           number | null
  stockout_risks_handled:  number | null
  managed_purchase_value:  number | null
  /** false when only SOME ordered lines carried a unit cost, so the value above
   *  is a floor rather than the month's total. */
  managed_purchase_value_complete: boolean
  capital_freed:           number | null
  capital_freed_status:    CapitalFreedStatus
}

export interface IncomingSource {
  kind:      'po' | 'transfer'
  reference: string
  qty:       number
}

export interface POLogEntry {
  id:                string
  po_number?:        number | null
  // NULL for manual orders (source === 'manual'), which have no forecast
  // session behind them.
  session_id:        string | null
  source?:           'forecast' | 'manual'
  generated_at:      string
  sku_count:         number
  total_units:       number
  total_value:       number | null
  skus_order_now:     number
  skus_order_soon: number
  // Adoption metrics (present once a cart with decisions is logged)
  suggested_count?:  number
  approved_count?:   number
  modified_count?:   number
  rejected_count?:   number
  // Reception (feature 1.4): pending | partial | received | not_received
  reception_status?: 'pending' | 'partial' | 'received' | 'not_received'
  /** When the order was sent to the supplier; null if it never was.
   *  The payables calendar reads it, which is why undoing a send is a real
   *  action and not a cosmetic flag. (`incoming_qty` does not: every open
   *  order counts as on its way, sent from here or not.) */
  sent_at?: string | null
  received_at?:      string | null
  /** When the buyer marked the supplier's invoice as paid; null while owed.
   *  A paid order leaves the payments calendar. */
  paid_at?:          string | null
  /** When the order was cancelled; null while it stands. A cancelled order
   *  is not on its way, not overdue and not owed. */
  cancelled_at?:     string | null
  cancel_reason?:    string | null
  /** True when the server answered an `Idempotency-Key` it had already seen:
   *  this is the order the FIRST request created, nothing new was written. */
  replayed?: boolean
  /** Present only when the tenant has an approval rule. */
  approval?: POApprovalBadge
}

// ── PO approval (opt-in workflow) ────────────────────────────────────────────
export type POApprovalStatus =
  'not_required' | 'approval_needed' | 'pending_approval' | 'approved' | 'rejected'
export interface POApprovalBadge { required: boolean; status: POApprovalStatus }
export interface POApprovalEntry {
  id: string
  status: 'requested' | 'approved' | 'rejected'
  amount: number
  requested_by: string
  requested_by_name: string | null
  requested_at: string
  request_note: string | null
  decided_by: string | null
  decided_by_name: string | null
  decided_at: string | null
  comment: string | null
}
export interface POApproval extends POApprovalBadge {
  po_log_id: string
  amount: number | null
  amount_known: boolean | null
  history: POApprovalEntry[]
  open_request: POApprovalEntry | null
  can_decide: boolean
}
export interface POApprovalRule {
  id: string
  threshold: number
  warehouse: string | null
  supplier_id: string | null
  supplier_name: string | null
  self_approve_below: number | null
  active: boolean
}
export interface POApprover { id: string; email: string; full_name: string | null; role: string }
export interface POApprovalSettings {
  rules: POApprovalRule[]
  enabled: boolean
  approvers: POApprover[]
  is_approver: boolean
}
export interface POApprovalPendingItem {
  approval_id: string
  po_log_id: string
  reference: string
  amount: number
  sku_count: number
  suppliers: string | null
  warehouse: string | null
  requested_by_name: string | null
  requested_at: string
  note: string | null
  can_decide: boolean
}

// ── Forecast adjustments and their measured value ────────────────────────────
export type AdjustmentReason =
  'promotion' | 'price_change' | 'new_customer' | 'lost_customer' | 'seasonality'
  | 'supply_issue' | 'market_news' | 'data_error' | 'other'
export type SpikeEditReason =
  'one_off_order' | 'promotion' | 'backlog_catch_up' | 'data_error' | 'external_event' | 'other'
/** A past period a person marked as a one-off ("exclude from the baseline"). */
export interface SpikeEdit {
  id: string
  dataset_id: string
  sku: string
  start_date: string
  end_date: string
  reason_code: SpikeEditReason
  reason_note: string | null
  created_by: string
  created_by_name: string | null
  created_at: string
  reverted_by: string | null
  reverted_at: string | null
  /** What THIS session's run did with the mark; null = the run predates it. */
  applied: {
    status: 'applied' | 'no_match' | 'no_baseline'
    points_treated: number
    original_total: number
    replacement_total: number
    applied_at: string
  } | null
}
/** A person's "this new product sells like these" (forecast by analogy). */
export interface SkuAnalogy {
  id: string
  new_sku: string
  reference_skus: string[]
  scale_factor: number
  start_date: string | null
  note: string | null
  created_by: string
  created_by_name: string | null
  created_at: string
  reverted_at: string | null
  /** Set once, when a trained model took over and the analogy stopped applying. */
  superseded_at: string | null
}
export interface AnalogyLimits {
  min_references: number; max_references: number; min_scale: number; max_scale: number
}
/** `analogy_applied` on a status row: what stood in for a trained model. */
export interface AnalogyApplied {
  analogy_id: string
  references: string[]
  references_missing: string[]
  scale_factor: number
  start_date: string | null
  alignment: 'calendar' | 'since_start'
  band_widen_factor: number
  min_relative_sigma: number
  note: string | null
  created_by_name: string | null
  created_at: string | null
}
export interface ForecastAdjustment {
  id: string
  session_id: string
  sku: string
  start_date: string
  end_date: string
  mode: 'percent' | 'absolute'
  value: number
  pct: number
  baseline_units: number | null
  reason_code: AdjustmentReason
  reason_note: string | null
  created_by: string
  created_by_name: string | null
  created_at: string
  superseded_by: string | null
}
/** One entry of `adjustments_applied` on a recommendation row. */
export interface AppliedAdjustment {
  adjustment_id: string
  pct: number
  mode: 'percent' | 'absolute'
  reason_code: AdjustmentReason
  reason_note: string | null
  created_by: string
  created_by_name: string | null
  overlap_days: number
  window_days: number
  blended_multiplier: number
}
export interface ValueAddedGroup {
  n_points: number
  base_error: number
  adjusted_error: number
  base_wape: number | null
  adjusted_wape: number | null
  /** Signed direction over the graded points: + = ran high (over-forecast). */
  base_bias?: number | null
  adjusted_bias?: number | null
  /** + = the adjusted forecast's error was that much SMALLER than the model's. */
  improvement_pct: number | null
  better_points: number
  worse_points: number
  verdict: 'improved' | 'worsened' | 'neutral' | 'too_little' | 'no_data'
}
export interface AdjustmentValueAdded {
  session_id: string
  status: string
  n_adjustments: number
  n_adjustments_graded?: number
  source: { dataset_id: string; name: string } | null
  aggregate: ValueAddedGroup | null
  by_user: (ValueAddedGroup & { user: string; name: string | null })[]
  by_reason: (ValueAddedGroup & { reason: AdjustmentReason })[]
}

// A line of a PO as stored server-side, with reception progress.
export interface POItemLine {
  id:                   string
  sku:                  string
  display_name:         string | null
  supplier:            string | null
  signal:               string | null
  status:               string
  recommended_qty: number
  final_qty:       number
  received_qty:    number | null
  unit_cost:       number | null
}

export interface POItemsResponse {
  po_log_id:        string
  reception_status: string
  generated_at:     string | null
  received_at:      string | null
  items:            POItemLine[]
}

export interface ReceptionResult {
  po_log_id:          string
  reception_status:   string
  received_at:        string
  lead_time_days:     number
  suppliers_observed: string[]
  items:              POItemLine[]
}

export interface SendPOResult {
  sent:    { supplier: string; email: boolean; whatsapp: boolean }[]
  /** `reason` is a stable code (e.g. 'no_contact_details') rendered via
   *  `roi.send_po_reason_<code>`; unknown codes fall back to the raw string. */
  skipped: { supplier: string | null; reason: string }[]
  /** Lines whose supplier could not be resolved to a supplier record at all —
   *  previously dropped in silence. */
  unresolved?: { sku: string; supplier: string | null }[]
}

// ── Event / promo impact simulation (feature 2.3) ────────────────────────────

/** The "why" behind the multiplier, so a ×2.2 is never shown unjustified. */
export interface MultiplierExplanation {
  base_multiplier:      number
  source:                  'catalog' | 'user'
  reason:                  string | null
  editable:                boolean
  es_estimacion:           boolean
  active_overrides:       number
  overrides_by_sku:       number
  overrides_by_category: number
}

/** A per-SKU or per-category multiplier override inside an event. */
export interface EventMultiplier {
  id:          string
  tenant_id:   string
  event_id:    string
  scope:       'sku' | 'family' | 'category'
  scope_value: string
  multiplier:  number
  created_at:  string
}
export interface EventSimulationRow {
  sku:             string
  display_name:    string | null
  supplier:       string | null
  category:       string | null
  /** Multiplier applied to THIS product, and where it came from. */
  multiplier:        number
  multiplier_source: 'sku' | 'family' | 'category' | 'event'
  daily_demand:  number
  baseline_units:  number
  event_units:     number
  extra_units:     number
  current_stock:    number | null
  stock_al_inicio: number | null
  deficit:         number | null
  qty_to_order:  number | null
  order_value:    number | null
  lead_time_days:  number
  order_by:        string
  llega_tarde:     boolean
  en_risk:       boolean
}

export interface EventSimulationResult {
  event_name: string | null
  start_date: string
  end_date:   string
  event_days: number
  multiplier: number
  event_id:   string | null
  explanation: MultiplierExplanation
  /** How many SKUs ran with each multiplier. */
  multipliers_applied: { multiplier: number; source: string; skus: number }[]
  items:      EventSimulationRow[]
  summary: {
    skus_simulados:     number
    skus_at_risk:     number
    extra_units:     number
    total_to_order:        number
    total_order_value: number
    order_before:     string | null
    any_order_late: boolean
  }
}

// A PO/supplier pair whose expected arrival (order date + the supplier's
// already-learned lead time) has passed with no reception recorded yet.
export interface OverdueReception {
  po_log_id:         string
  supplier:         string
  generated_at:      string
  expected_arrival:  string
  days_overdue:      number
  lead_time_used:    number
  // Unified with the semáforo's vocabulary: 'observed' is now 'learned' and
  // 'declared' is 'supplier_rule'. Two words for one question was the bug.
  lead_time_source:  ValueSource
}

export interface SupplierScorecardRow {
  supplier:            string
  n_receptions:        number
  lead_time_real_min:   number | null
  lead_time_real_max:   number | null
  lead_time_real_avg:   number | null
  /** Observed tail of the lead times (display only, null below the sample floor). */
  lead_time_p80_days?:  number | null
  lead_time_p95_days?:  number | null
  lead_time_declarado:  number | null
  deviation_days:      number | null
  on_time_rate:         number | null
  fill_rate:            number | null
  /** null when no ordered line of this supplier carries a unit cost — the same
   *  rule /impacto applies to managed_purchase_value. A confident 0 would read
   *  as "you bought nothing from them", which is a different statement. */
  purchased_value:       number | null
  /** false when only SOME ordered lines carried a cost, so the figure above is
   *  a floor rather than the total. */
  purchased_value_complete: boolean
  last_reception:     string | null
  /** Enough receptions, none of them saying anything: every delivery landed the
   *  same day it was ordered, so the observed average is 0. Same rule as
   *  `lead_time_learned_unusable` on the supplier card — one definition. */
  lead_time_unusable?:  boolean
  /** A trend needs two points. False on 0 or 1 reception. */
  trend_measurable?:    boolean
  /** False when the on-time percentage is computed off too few receptions to
   *  mean anything. The backend has produced these two since the day it added
   *  the sample floor, with a comment naming the defect ("one reception printed
   *  100% in bold green") — and the screen never declared them, so it printed
   *  the raw number anyway and the fix lived only in the API. */
  on_time_measurable?:   boolean
  /** Same rule for the fill rate, over orders rather than receptions. */
  fill_rate_measurable?: boolean
  /** Orders left out of `fill_rate` because they are still inside the delivery
   *  window the supplier promised. An empty fill rate with orders in transit is
   *  a supplier nobody can judge yet, not a supplier nobody buys from. */
  orders_in_transit?:    number
}

// Feature 2.5 — a supplier the PO-send path would silently skip.
export interface SupplierContactHealthRow {
  supplier:             string
  supplier_id:           string | null
  reason:                'no_supplier_record' | 'no_contact'
  reason_text:          string
  has_email:           boolean
  has_whatsapp:        boolean
  open_pos:    number
  has_open_pos: boolean
}

// Feature 3.3 — a supplier whose recent lead time drifted off its own history.
export interface SupplierLeadTimeAlert {
  supplier:             string
  lead_time_historical: number
  lead_time_recent:     number
  deviation_days:       number
  z_score:              number
  sigma:                number
  n_baseline:           number
  n_recent:             number
  severity:             'medium' | 'high'
  // `message` is the English fallback; the rendered sentence comes from
  // `scorecard.<message_code>` with `message_params`.
  message:              string
  message_code:         string
  message_params:       Record<string, unknown>
}

// Feature 3.5 — a supplier quantity scale: "from min_qty units on, each unit
// costs unit_price" (all-units semantics).
export interface PriceBreak {
  id:            string
  supplier_id:   string
  supplier_name: string
  sku:           string
  min_qty:       number
  unit_price:    number
  notes:         string | null
  created_at:    string
}

// Why a step-up was or was not recommended. The backend owns this verdict —
// the UI only renders it.
export type PriceBreakReason =
  | 'worth_it'
  | 'no_discount'
  | 'no_demand'
  | 'would_overstock'
  | 'holding_exceeds_saving'
  | 'saving_immaterial'

export interface PriceBreakOpportunity {
  sku:                 string
  supplier_name:       string | null
  current_quantity:    number
  step_quantity:       number
  extra_units:         number
  current_unit_price:  number
  step_unit_price:     number
  unit_price_drop_pct: number | null
  gross_saving:        number
  holding_cost:        number
  net_saving:          number
  extra_coverage_days: number | null
  total_coverage_days: number | null
  coverage_limit_days: number
  extra_cash_now:      number
  worth_it:            boolean
  reason_code:         PriceBreakReason
}

export interface PriceBreakEvaluation {
  opportunities:    PriceBreakOpportunity[]
  worth_it_count:   number
  total_net_saving: number
  holding_cost_pct: number
}

// Feature 3.6 — one invoice coming due from a PO already sent.
export interface PayableItem {
  po_log_id:      string
  supplier_name:  string | null
  amount:         number
  sent_date:      string
  credit_days:    number
  due_date:       string
  days_until_due: number
  overdue:        boolean
  within_horizon: boolean
  // Lines of this (PO, supplier) with no unit cost: `amount` leaves them out.
  uncosted_lines?:  number
  amount_complete?: boolean
}

export interface PayableUnknownTerms {
  po_log_id:     string
  supplier_name: string | null
  amount:        number
  payment_terms: string | null
  uncosted_lines?:  number
  amount_complete?: boolean
}

export interface CashWeek {
  start:  string
  end:    string
  amount: number
}

export interface CashCalendar {
  today:               string
  horizon_days:        number
  due_items:           PayableItem[]
  weeks:               CashWeek[]
  overdue_total:       number
  this_week_total:     number
  horizon_total:       number
  unknown_terms:       PayableUnknownTerms[]
  unknown_terms_total: number
  // Lines on sent, unpaid orders that carry no unit cost — the totals above
  // are missing them (math audit O3). Optional for older responses.
  uncosted_lines?:           number
  uncosted_lines_committed?: number
  uncosted_po_count?:        number
  totals_complete?:          boolean
}

export interface CashFitLine {
  sku:            string | null
  supplier_name:  string | null
  amount:         number
  credit_days:    number
  terms_known:    boolean
  due_date:       string
  within_horizon: boolean
}

// `fits: null` means "no budget supplied" — never a guess.
export interface CashFitResult {
  today:                       string
  horizon_days:                number
  budget:                      number | null
  committed_total:             number
  overdue_total:               number
  this_week_total:             number
  purchase_total:              number
  purchase_in_horizon:         number
  required_total:              number
  fits:                        boolean | null
  // Why `fits` is null: no budget typed, or costs missing so a "fits" would
  // be a guess. Over budget is still `false` either way — a missing cost can
  // only add to what is required.
  fits_unknown_reason?:        'no_budget' | 'missing_costs' | null
  shortfall:                   number | null
  total_complete?:             boolean
  uncosted_committed_lines?:   number
  uncosted_purchase_lines?:    number
  uncosted_purchase_skus?:     string[]
  lines:                       CashFitLine[]
  suppliers_assumed_immediate: string[]
  unknown_terms_total:         number
  weeks:                       CashWeek[]
}

// A single buyer decision sent to /inventory/log-po when a PO is downloaded.
export interface POLineDecision {
  sku:                   string
  display_name?:         string | null
  supplier?:            string | null
  /** The supplier the buyer picked for this line, when they picked one. */
  supplier_id?:         string | null
  signal?:               string | null
  recommended_qty:  number
  final_qty:        number
  status:                'approved' | 'modified' | 'rejected'
  unit_cost?:       number | null
  warehouse?:               string | null
}

// ── Suppliers ─────────────────────────────────────────────────────────────────

export interface Supplier {
  id:             string
  tenant_id:      string
  name:           string
  email:          string | null
  phone:          string | null
  whatsapp:       string | null
  lead_time_days: number
  lead_time_std:  number
  /** How often the buyer orders from this supplier, in days. 0 means no
   *  declared cadence: the order then only has to cover the lead time,
   *  which is how every tenant behaved before the field existed. */
  review_period_days?: number
  payment_terms:  string | null
  notes:          string | null
  active:         boolean
  created_at:     string
}

export interface SkuSupplier {
  id:             string
  sku:            string
  supplier_id:    string
  is_primary:     boolean
  unit_cost:      number | null
  moq:            number
  lead_time_days: number | null  // override; null = use supplier default
  notes:          string | null
  // Joined supplier fields:
  supplier_name:  string
  supplier_email: string | null
  supplier_phone: string | null
  effective_lead_time: number   // sku_suppliers.lead_time_days ?? suppliers.lead_time_days
}

// ── Morning Briefing ──────────────────────────────────────────────────────────
export interface BriefingRecommendation {
  priority:  number
  sku:       string
  name:      string
  rec_type:  'STOCKOUT_RISK' | 'REORDER_SOON' | 'DEMAND_UP' | 'DEMAND_DOWN' | 'OVERSTOCK'
  // English, and only the fallback: the sentence the user reads is built from
  // `rec_type` + `text_params` against the catalogue, so it follows the language
  // toggle. Kept for a frontend older than its API — see recText/recAction.
  text:      string
  action:    string
  text_code?:     string
  text_params?:   Record<string, unknown>
  action_code?:   string
  action_params?: Record<string, unknown>
  signal:    string
}

export interface MorningBriefingKPIs {
  total_skus:            number
  order_now:              number
  order_soon:          number
  ok:                    number
  overstock:            number
  sin_datos:             number
  avg_accuracy:          number | null
  total_inventory_value: number
  // How many products that value could be computed from. 0 means nobody
  // recorded a unit cost, so the total is not "₡0 of stock" — it is unknown.
  // Optional: a briefing from before this existed does not carry it.
  valued_skus?:          number
  capital_in_overstock:  number
  demand_alerts:         number
  demand_spikes?:        number
}

// Proactive future-peak alert: a spike the forecast sees ahead, with the
// latest date to order (peak_date − lead_time) so it's covered.
export interface DemandSpike {
  sku:             string
  display_name:    string
  supplier:       string | null
  baseline_diaria: number
  peak_value:      number
  uplift_pct:      number
  peak_date:       string | null
  days_until_peak: number
  lead_time_days:  number
  order_by_date:   string | null
  already_late:    boolean
  signal:          string | null
}

export interface MorningBriefing {
  date:             string
  session_id:       string
  session_name:     string
  has_data:         boolean
  risks:            InventoryStatusItem[]
  warnings:         InventoryStatusItem[]
  overstocked:      InventoryStatusItem[]
  demand_changes:   (InventoryStatusItem & { demand_trend_pct: number })[]
  demand_spikes?:   DemandSpike[]
  /** Network transfer suggestions (5.4), computed server-side inside the
   * briefing so /hoy never re-runs the by-warehouse status for them. */
  transfer_suggestions?: WarehouseStatusItem[]
  excluded_skus?:   ExcludedSku[]
  recommendations:  BriefingRecommendation[]
  kpis:             MorningBriefingKPIs
  /** Active planning grain + its coverage unit. Per-period coverage figures in
   * the risks/warnings/overstock lists are in this unit (a weekly session's
   * coverage_days of 3 means 3 weeks); the /hoy cards label them accordingly. */
  period?:          PlanningPeriod
  coverage_unit?:   CoverageUnit
}

// ── SKU Intelligence ──────────────────────────────────────────────────────────
export interface SkuIntelligenceData {
  sku:                     string
  model:                   string | null
  available_models:        string[]
  original_freq:           string
  applied_granularity:     string
  available_granularities: string[]
  historical:              { date: string; value: number }[]
  forecast:                ForecastPoint[]
  metrics:                 MetricRow[]
  quality:                 QualityReport[string] | null
  stats: {
    mean:   number
    std:    number
    min:    number
    max:    number
    median: number
    n:      number
  } | null
}

/** `GET /sessions/{id}/forecast-total`: the catalogue summed per date. Same
 *  shape as one SKU's intelligence plus how many SKUs went into it and the mean
 *  of their champion WAPEs. */
export interface ForecastTotalData extends SkuIntelligenceData {
  n_skus:        number
  accuracy_wape: number | null
}

// ── Series decomposition ──────────────────────────────────────────────────────
/** One bucket of the STL split. The four values are aligned by construction on
 *  the backend — `observed === trend + seasonal + residual` for every row — so
 *  a chart binds straight off this array without re-deriving anything. */
export interface DecompositionPoint {
  date:     string
  observed: number
  trend:    number
  seasonal: number
  residual: number
}

export interface DecompositionData {
  sku:                     string
  granularity:             string
  original_granularity:    string
  available_granularities: string[]
  /** Buckets in one seasonal cycle: 7 for daily, 52 weekly, 12 monthly. */
  period:                  number
  /** Stable English key ('weekly' | 'annual') — label the panel off this, not
   *  off `period`, which is a count and means nothing to a reader. */
  seasonal_cycle:          string
  cycles_covered:          number
  n_points:                number
  series:                  DecompositionPoint[]
  /** 0-1. How much of the movement the trend and the repeating cycle explain. */
  trend_strength:          number
  seasonal_strength:       number
}

// ── AI Narratives ─────────────────────────────────────────────────────────────
// The key points survive a SUCCESSFUL AI call — the narrative comes back in the
// reader's language but these are composed by the backend — so they travel as
// code + params and get their wording from the catalogue (`narrative.kp.*`).
// `text` is the English fallback for a code this build does not know.
export interface NarrativeKeyPoint {
  code:   string
  params: Record<string, unknown>
  text:   string
}

export interface MorningNarrative {
  narrative:   string
  key_points:  NarrativeKeyPoint[]
  urgency:     'critical' | 'warning' | 'ok'
  fallback:    boolean
  error?:      string
}

export interface InventoryInsight {
  insight:  string
  urgency:  'critical' | 'warning' | 'ok'
  fallback: boolean
}

export interface ForecastExplanation {
  explanation: string
  fallback:    boolean
}

/** GET /analyst/welcome — the assistant screen's personal opening. */
export interface AssistantWelcome {
  first_name: string
  company: string
  has_forecast: boolean
  summary: {
    order_now: number
    order_soon: number
    overstock: number
    no_stock_data: number
    overdue_orders: number
  } | null
  /** `code` selects `analyst.suggest.<code>`; `params` fill its placeholders. */
  suggestions: { code: string; params: Record<string, string | number> }[]
}

export interface SuggestedQuestion {
  // Clicking one puts it in the composer and sends it, so it has to be in the
  // reader's language: `analyst.q.<code>` is what gets rendered, `text` is the
  // English fallback.
  code: string
  text: string
  icon: string
}

/** Why a unit's value could not be priced. Never a silent 0 — see
 *  `backend/inventory/dead_capital.py`'s module docstring. */
export type DeadCapitalValueUnknownReason = 'no_unit_cost'

export interface DeadCapitalItem {
  sku:                     string
  display_name:            string | null
  supplier:                string | null
  category:                string | null
  current_stock:           number
  unit_cost:               number | null
  /** null when the unit cost is unknown — never 0. See `value_unknown_reason`. */
  value:                   number | null
  value_unknown_reason:    DeadCapitalValueUnknownReason | null
  days_still:              number
  /** true when a real stock decrease was found and dated; false means no
   *  decrease was ever observed and `days_still` is only the span the
   *  recorded history covers — a floor, not a confirmed count. */
  days_still_exact:        boolean
  /** The SKU's current semáforo signal, when a session exists to compute it
   *  — null otherwise. This view does not need a session to work. */
  signal:                  InventorySignal | null
}

export interface DeadCapitalResponse {
  window_days:            number
  items:                  DeadCapitalItem[]
  /** Sum of `value` over priced items only — unpriced items are never folded
   *  in as 0. */
  total_value:            number
  sku_count:              number
  unpriced_sku_count:     number
  total_skus_with_stock:  number
  excluded_no_history:    number
  excluded_too_recent:    number
}

// Supplier cost inflation (stability.md #20 item 5): `inventory_po_items
// .unit_cost` read as a price history, but only from orders that were
// actually RECEIVED — a quote or a rejected order proves nothing was paid.
// See `backend/inventory/cost_alerts.py`.
export interface SupplierInflationProduct {
  sku:                 string
  display_name:        string | null
  first_cost:          number
  last_cost:            number
  first_observed_at:    string
  last_observed_at:     string
  cumulative_pct:      number
  increases_count:      number
  observations_count:  number
}

export interface SupplierInflation {
  supplier:              string
  sku_count_affected:    number
  increases_count:       number
  /** Qty-weighted across the supplier's affected SKUs — see the aggregation
   *  comment in `cost_alerts.get_supplier_cost_inflation`. */
  cumulative_pct:        number
  worst_products:        SupplierInflationProduct[]
}

export interface SupplierCostInflationResponse {
  window_days:                 number
  suppliers:                   SupplierInflation[]
  supplier_count:               number
  skus_single_observation:      number
  skus_zero_cost_base:          number
  lines_excluded_no_supplier:  number
}

// Margin erosion (stability.md #20 item 6): cost history crossed with the
// SKU's CURRENT sale_price — the only one StockAI stores. `margin_pct_then` is
// therefore not a historical margin; see `price_history_available` below and
// `cost_alerts.get_margin_erosion`'s docstring.
export interface MarginErosionItem {
  sku:                 string
  display_name:        string | null
  sale_price:          number
  cost_then:            number
  cost_now:             number
  /** null when cost_then is 0 — a % change from a zero base is undefined. */
  cost_change_pct:      number | null
  /** `calc_unit_margin`'s discipline: never clamped, negative reported as-is. */
  unit_margin_then:    number
  unit_margin_now:      number
  margin_pct_then:      number
  margin_pct_now:        number
  erosion_pts:          number
  first_observed_at:    string
  last_observed_at:      string
}

export interface MarginErosionResponse {
  window_days:              number
  items:                    MarginErosionItem[]
  sku_count:                number
  /** Always false today — StockAI stores no sale_price history. Load-bearing:
   *  it is what stops `margin_pct_then` from being read as a historical
   *  fact rather than a today's-price hypothetical. */
  price_history_available: boolean
  excluded_no_cost_history: number
  excluded_no_sale_price:   number
  excluded_invalid_price:   number
  lines_excluded_no_supplier: number
}

// ── The forecast in money (stability.md #20, item 1) ─────────────────────────
// The engine predicts units; the product already knows price and cost per
// SKU. See `backend/inventory/forecast_money.py`'s module docstring for the
// honesty constraints this payload is built under.
export type ForecastMoneyUnknownReason = 'no_sale_price' | 'no_unit_cost'

export interface ForecastMoneyItem {
  sku:                     string
  display_name:            string | null
  supplier:                string | null
  category:                string | null
  units_forecast:          number
  /** The CURRENT price/cost on file — StockAI stores no price history, so this
   *  is what the projection is built from, not a claim about the future. */
  sale_price:              number | null
  unit_cost:               number | null
  /** null when `sale_price` is unknown — NEVER 0. See `revenue_unknown_reason`. */
  revenue:                 number | null
  revenue_unknown_reason:  ForecastMoneyUnknownReason | null
  /** null when `unit_cost` is unknown — NEVER 0. See `cost_unknown_reason`. */
  cost:                    number | null
  cost_unknown_reason:     ForecastMoneyUnknownReason | null
  /** null exactly when either `revenue` or `cost` is null. A negative
   *  margin (selling below cost) is reported as-is, never clamped. */
  margin:                  number | null
  margin_pct:              number | null
  /** This SKU's share of `total_margin`, null when its own margin is
   *  unknown or the total is 0. What lets the screen say "these products
   *  are N% of it". */
  contribution_pct:        number | null
}

export interface ForecastMoneyResponse {
  session_id:                  string
  /** The session's real forecast horizon, in days — derived from the
   *  forecast actually stored, not a screen default. */
  horizon_days:                number
  horizon_start:               string | null
  horizon_end:                 string | null
  /** Always false today — StockAI stores no sale_price history, so this
   *  projection is built entirely on today's price. Same flag
   *  `MarginErosionResponse` uses for the identical caveat. */
  price_history_available:     boolean
  items:                       ForecastMoneyItem[]
  /** Sum of `revenue` over priced items only. */
  total_revenue:                number
  /** Sum of `cost` over items with both price and cost known. */
  total_cost:                   number
  /** Sum of `margin` over items with both price and cost known — never a
   *  mix with unpriced/uncosted SKUs folded in as 0. */
  total_margin:                 number
  /** null when no SKU has both price and cost known. */
  total_margin_pct:             number | null
  sku_count:                    number
  priced_sku_count:             number
  costed_sku_count:             number
  excluded_no_price_count:      number
  excluded_no_cost_count:       number
  excluded_no_forecast_count:   number
  top_contributors:             number
  /** null when `total_margin` is 0. */
  top10_margin_share_pct:       number | null
}

// ── "What did it cost me to ignore you" (stability.md 19.4) ──────────────────
// Per-SKU, over a window: did the semáforo ask to order, did a purchase order
// follow, and — only when it did not AND a snapshot actually recorded stock
// at or below zero — the estimated unserved units and their value.
// `no_po_no_stockout_observed` is the honest default: it means the report
// cannot show a cost, not that the cost was zero. See
// `backend/inventory/recommendation_reports.py`.
export type CostOfIgnoringOutcome = 'ordered' | 'likely_stockout' | 'no_po_no_stockout_observed'

/** Why `lost_value` is null even though the SKU is a likely stockout. */
export type CostOfIgnoringValueUnknownReason = 'no_stockout_detected' | 'no_demand_rate_recorded' | 'sale_price_unknown'

export interface CostOfIgnoringSku {
  sku:                       string
  times_flagged:             number
  first_flagged_on:          string
  last_flagged_on:           string
  latest_signal:             string
  latest_recommended_qty:    number | null
  po_window_days:            number
  outcome:                   CostOfIgnoringOutcome
  /** Set only when `outcome === 'ordered'`. */
  po_generated_at?:          string
  /** Set only when `outcome === 'likely_stockout'`. */
  stockout_observed_at?:     string
  recovery_observed_at?:     string | null
  /** true when the window ended before stock was observed to recover — the
   *  units/value below are a LOWER BOUND, not the full cost: nothing past
   *  the requested window was visible. */
  partial_window?:           boolean
  days_out_of_stock?:        number
  avg_daily_demand_used?:    number | null
  /** null unless a stockout was actually observed. */
  lost_units:                number | null
  /** null whenever `lost_units` is null, OR the SKU has no current
   *  sale_price on file — NEVER a silent 0. See `lost_value_reason`. */
  lost_value:                number | null
  lost_value_reason:         CostOfIgnoringValueUnknownReason | null
}

export interface CostOfIgnoringSummary {
  skus_flagged:                            number
  skus_ordered:                            number
  skus_likely_stockout:                    number
  /** "Unclear" on purpose — no PO followed AND no stockout was observed, so
   *  the report cannot say ignoring the advice cost anything. Not a good
   *  outcome by default: it may simply mean the buyer was still covered. */
  skus_unclear:                            number
  /** Sum over priced+quantified SKUs only — never padded with SKUs whose
   *  units or value are unknown. */
  total_estimated_lost_units:              number | null
  total_estimated_lost_value:              number | null
  skus_with_lost_units_but_unknown_value:  number
}

export interface CostOfIgnoringResponse {
  from_date:       string
  to_date:         string
  po_window_days:  number
  summary:         CostOfIgnoringSummary
  skus:            CostOfIgnoringSku[]
}

// ── "Why is today's number different" (stability.md 19.7) ───────────────────
// The latest recorded recommendation for one SKU against the previous
// recorded one, decomposed into the inputs that moved. See
// `backend/inventory/recommendation_reports.why_changed`.
export type WhyChangedFieldOrigin = 'session' | 'operational' | 'derived'

export interface WhyChangedField {
  previous: number | string | null
  current:  number | string | null
  delta:    number | null
  origin:   WhyChangedFieldOrigin
}

export type WhyChangedExplanationCode =
  | 'recommendation_change_new_session'
  | 'recommendation_change_same_session'

export interface WhyChangedResponse {
  available:              boolean
  sku:                    string
  /** Set when `available` is false: why there is nothing to compare yet. */
  reason?:                'no_recorded_recommendations' | 'no_previous_recommendation'
  latest?:                Record<string, unknown>
  latest_recorded_on?:    string
  previous_recorded_on?:  string
  latest_session_id?:     string | null
  previous_session_id?:   string | null
  /** true when the change came from a new training run (a model opinion)
   *  rather than the tenant's own operational data moving. */
  session_changed?:       boolean
  fields?:                Record<string, WhyChangedField>
  explanation_code?:      WhyChangedExplanationCode
}

// Multi-period planning (Phase B): the tenant's active view granularity.
export type PlanningPeriod = 'daily' | 'weekly' | 'monthly'

export type PlanningPeriodSource = 'auto' | 'manual'

// Why the app is planning at this grain. The app always chose it, but until
// this shipped it only offered a bare dropdown, so a tenant-wide setting that
// also drives the daily alerts read like a personal view toggle.
export type PlanningPeriodReason =
  | 'natural_frequency'        // finest grain the data supports
  | 'only_option'              // the history affords no other
  | 'manual_choice'            // someone picked it in Settings
  | 'chosen_grain_unavailable' // their pick no longer exists

export interface PlanningState {
  period:            PlanningPeriod
  horizon:           number
  available_periods: PlanningPeriod[]
  max_horizon:       number
  active_session_id: string | null
  period_source:     PlanningPeriodSource
  period_reason:     PlanningPeriodReason
  // Only set when an explicit pick could not be honored.
  requested_period:  PlanningPeriod | null
}

// The horizon the tenant's buying need (lead time + review period) asks for.
// `need` is null when nothing is declared: the horizon is then untouched.
export interface HorizonNeed {
  need_days:          number   // with the safety margin, clamped to the ceiling
  required_days:      number   // lead time + review period, no margin
  lead_time_days:     number
  review_period_days: number
  supplier:           string | null
  sku:                string
  capped:             boolean
}
export interface HorizonPreview {
  need:         HorizonNeed | null
  ceiling_days: number
  by_grain: Record<PlanningPeriod, { configured_steps: number; steps: number; extended: boolean }>
}

// ── What-if scenarios (PENDIENTES #7) ────────────────────────────────────────

export type ScenarioRuleType =
  | 'demand_multiplier'
  | 'promo'
  | 'supplier_delay'
  | 'safety_stock'

/** One typed what-if rule. Only the fields its `type` uses are ever sent. */
export interface ScenarioRule {
  type:           ScenarioRuleType
  label?:         string
  /** demand_multiplier / promo */
  multiplier?:    number
  sku?:           string
  category?:      string
  date_from?:     string
  date_to?:       string
  /** supplier_delay */
  extra_days?:    number
  supplier?:      string
  /** safety_stock */
  service_level?: number
}

export interface Scenario {
  id:          string
  session_id:  string
  name:        string
  rules:       ScenarioRule[]
  created_by:  string | null
  created_at:  string | null
}

/** Portfolio totals of one semáforo run (base or scenario). */
export interface ScenarioTotals {
  skus_evaluated:            number
  skus_to_order:             number
  total_units_to_order:      number
  estimated_purchase_value:  number
  order_now:                 number
  order_soon:                number
  ok:                        number
  overstock:                 number
  no_data:                   number
}

/** Same keys as ScenarioTotals minus skus_evaluated — scenario minus base. */
export type ScenarioDelta = Omit<ScenarioTotals, 'skus_evaluated'>

export interface ScenarioChangeRow {
  sku:                      string
  display_name:             string | null
  supplier:                 string | null
  category:                 string | null
  current_stock:            number | null
  base_signal:              string | null
  scenario_signal:          string | null
  base_qty:                 number
  scenario_qty:             number
  delta_qty:                number
  delta_value:              number | null
  base_daily_demand:        number | null
  scenario_daily_demand:    number | null
  base_lead_time_days:      number | null
  scenario_lead_time_days:  number | null
  base_coverage_days:       number | null
  scenario_coverage_days:   number | null
}

export interface ScenarioRunResult {
  scenario_id: string | null
  name:        string | null
  session_id:  string
  rules:       ScenarioRule[]
  base:        ScenarioTotals
  scenario:    ScenarioTotals
  delta:       ScenarioDelta
  changes:     ScenarioChangeRow[]
  applied: {
    demand_rules:    number
    series_adjusted: number
    service_level:   number
  }
}

// ── Sales by e-mail (Configuración > Datos) ───────────────────────────────────
export type InboundOutcome = 'ingested' | 'needs_review' | 'rejected' | 'processing'

export interface InboundEmailMessage {
  id:            string
  sender:        string
  filename:      string | null
  outcome:       InboundOutcome
  reason:        string | null
  reason_params: Record<string, unknown>
  dataset_id:    string | null
  /** 'none' | 'launched' | 'skipped' | 'failed': what happened to a scheduled retrain. */
  retrain:       string | null
  received_at:   string
}

export interface InboundEmailState {
  /** False when this installation has no inbound mail domain and secret. */
  enabled:         boolean
  address:         string | null
  allowed_senders: string[]
  messages:        InboundEmailMessage[]
}

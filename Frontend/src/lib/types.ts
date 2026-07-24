// ── Session ──────────────────────────────────────────────────────────────────
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

export interface DataQualityIssue {
  type:     string
  severity: 'error' | 'warning' | 'info'
  message:  string
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

export interface QualityReport {
  [sku: string]: {
    quality_score: number
    series_type:   string
    n_rows:        number
    missing_pct:   number | null
    n_outliers:    number
    warnings:      string[]
    is_valid:      boolean
  }
}

// ── Inspection result (from GET /sessions/{id}/inspect) ───────────────────────
export interface InspectionResult {
  profile:                DataProfile
  column_options:         ColumnOptions
  canonical_suggestions?: CanonicalMapping   // NEW (also nested in column_options)
  config_schema:          ConfigSchema | null
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
  n_folds:    number | null
  validation: string | null
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

export interface SqlConfig {
  host:     string
  port:     number
  database: string
  username: string
  engine:   SqlEngine
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
  columns:   string[]
  rows:      Record<string, unknown>[]
  row_count: number
  truncated: boolean
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
  advanced_mode?:   boolean
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
export interface ApiKey {
  id:         string
  name:       string
  last_used:  string | null
  created_at: string
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
}

// ── Inventory ─────────────────────────────────────────────────────────────────
export type InventorySignal = 'PEDIR_YA' | 'PEDIR_PRONTO' | 'OK' | 'SOBRESTOCK' | 'SIN_DATOS'

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
  brand?:         string | null
  unit_of_measure?: string | null
  barcode?: string | null
}

export interface InventoryCalcExplanation {
  suficiente?:        boolean
  daily_demand?:    number
  lead_time_days?:    number
  // 'learned' (from real receptions) vs 'configured' (typed on the SKU card).
  lead_time_source?: 'learned' | 'configured'
  lead_time_demand?: number
  safety_stock?:      number
  current_stock?:      number
  antes_moq?:         number
  moq?:               number
  final_qty?:    number
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

export interface TransferSuggestion {
  from_warehouse: string
  qty: number
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
  lead_time_source: 'learned' | 'configured'
  moq: number
  daily_demand: number | null
  coverage_days: number | null
  reorder_point: number | null
  signal: InventorySignal
  recommended_qty: number | null
  recommended_action: 'order' | 'transfer' | null
  transfer_suggestion: TransferSuggestion | null
  unit_cost: number | null
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
  items: TransferItem[]
}

export interface InventoryStatusItem extends InventoryStock {
  has_forecast:         boolean
  has_stock:            boolean
  daily_demand:       number | null
  lead_time_demand:    number | null
  coverage_days:       number | null
  signal:               InventorySignal
  recommended_qty: number | null
  inventory_value:     number | null
  n_models:             number
  abc:                  string
  xyz:                  string
  abc_xyz:              string
  stock_history:        { stock: number; date: string }[]
  calc_explanation:     InventoryCalcExplanation | null
  // The "why" behind the recommendation — all computed in the backend
  lead_time_source?:      'learned' | 'configured'
  lead_time_configured?: number
  lead_time_learned?:   number | null
  reorder_point?:         number | null
  explanation?:           string | null
  // Margen bruto por unit; null cuando falta sale_price o unit_cost
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
  detail:  string
}

// ── Purchasing/transfers optimizer (MW-3) ─────────────────────────────────────

export interface OptimizationOrder {
  sku:             string
  warehouse:          string
  qty:             number
  unit_cost:  number | null
  supplier:       string | null
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
  }
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

export interface ROIMonthlyRow {
  month:             string          // 'YYYY-MM'
  pos_count:         number
  skus_order_now:     number
  total_value:       number
  adoption_rate:     number | null
  capital_liberado:  number | null
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
  capital_freed:           number | null
}

export interface POLogEntry {
  id:                string
  po_number?:        number | null
  session_id:        string
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
  received_at?:      string | null
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
  skipped: { supplier: string | null; reason: string }[]
}

// ── Event / promo impact simulation (feature 2.3) ────────────────────────────

/** El "por qué" del multiplier, para no mostrar un ×2.2 sin justificar. */
export interface MultiplierExplanation {
  base_multiplier:      number
  source:                  'catalog' | 'user'
  reason:                  string | null
  editable:                boolean
  es_estimacion:           boolean
  overrides_activos:       number
  overrides_por_sku:       number
  overrides_by_category: number
}

/** Override de multiplier por SKU o categoría dentro de un event. */
export interface EventMultiplier {
  id:          string
  tenant_id:   string
  event_id:    string
  scope:       'sku' | 'category'
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
  multiplier_source: 'sku' | 'category' | 'event'
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
  /** Cuántos SKU corrieron con cada multiplier. */
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
  lead_time_source:  'observed' | 'declared' | 'default'
}

export interface SupplierScorecardRow {
  supplier:            string
  n_recepciones:        number
  lead_time_real_min:   number | null
  lead_time_real_max:   number | null
  lead_time_real_avg:   number | null
  lead_time_declarado:  number | null
  deviation_days:      number | null
  on_time_rate:         number | null
  fill_rate:            number | null
  purchased_value:       number
  ultima_recepcion:     string | null
}

// Feature 2.5 — a supplier the PO-send path would silently skip.
export interface SupplierContactHealthRow {
  supplier:             string
  supplier_id:           string | null
  reason:                'no_supplier_record' | 'no_contact'
  reason_text:          string
  tiene_email:           boolean
  tiene_whatsapp:        boolean
  ordenes_pendientes:    number
  en_ordenes_pendientes: boolean
}

// Feature 3.3 — a supplier whose recent lead time drifted off its own history.
export interface SupplierLeadTimeAlert {
  supplier:           string
  lead_time_historico: number
  lead_time_reciente:  number
  deviation_days:     number
  z_score:             number
  sigma:               number
  n_baseline:          number
  n_reciente:          number
  severidad:           'media' | 'alta'
  mensaje:             string
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
}

export interface PayableUnknownTerms {
  po_log_id:     string
  supplier_name: string | null
  amount:        number
  payment_terms: string | null
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
  shortfall:                   number | null
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
  text:      string
  action:    string
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

// ── AI Narratives ─────────────────────────────────────────────────────────────
export interface MorningNarrative {
  narrative:   string
  key_points:  string[]
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

export interface SuggestedQuestion {
  text: string
  icon: string
}

// ── Dead stock / immobilised inventory ────────────────────────────────────────
export interface DeadStockItem {
  sku:                    string
  display_name:           string | null
  supplier:              string | null
  current_stock:           number
  unit_cost:         number | null
  capital_trapped:        number
  holding_cost_monthly:   number
  days_without_movement:  number
  depletion_pct:          number
  avg_daily_demand:       number
  signal:                 string
  abc:                    string
  action_suggested:       string
}

export interface DeadStockResponse {
  items:                        DeadStockItem[]
  total_capital_trapped:        number
  total_holding_cost_monthly:   number
  sku_count:                    number
  min_days_static:              number
}

// Multi-period planning (Phase B): the tenant's active view granularity.
export type PlanningPeriod = 'daily' | 'weekly' | 'monthly'

export interface PlanningState {
  period:            PlanningPeriod
  horizon:           number
  available_periods: PlanningPeriod[]
  max_horizon:       number
  active_session_id: string | null
}

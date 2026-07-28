// Types for the stock-setup screen (onboarding-friction plan #1, phases 3-4):
// GET /inventory/setup-gaps and the tolerant importer behind
// POST /inventory/bulk[/preview]. Kept in their own module so the shared
// `types.ts` stays out of this branch's way.

/** Which configuration field a SKU is still missing. */
export type SetupGapField = 'stock' | 'lead_time' | 'cost' | 'price' | 'supplier'

/** Where the unit price used to weigh a SKU came from. */
export type PriceSource = 'unit_cost' | 'sale_price' | 'tenant_median' | 'none'

export interface SetupGapItem {
  sku:              string
  display_name:     string | null
  category:         string | null
  has_row:          boolean
  current_stock:    number | null
  missing:          SetupGapField[]
  is_gap:           boolean
  projected_demand: number
  unit_price:       number | null
  price_source:     PriceSource
  projected_spend:  number
  rank:             number
  share_pct:        number
  /** Share of projected spend covered once THIS row and every row above it is done. */
  cumulative_pct:   number
}

export interface SetupGapsResponse {
  session_id:   string
  period:       string
  horizon_days: number
  /** 'money' when real costs/prices exist; 'units' when we must rank by volume. */
  basis:        'money' | 'units'
  target_pct:   number
  total_skus:       number
  configured_skus:  number
  gap_skus:         number
  /** Share of projected spend already configured — what the progress bar fills. */
  covered_pct:           number
  total_projected_spend: number
  gap_projected_spend:   number
  recommended_count: number
  recommended_pct:   number
  items:     SetupGapItem[]
  truncated: boolean
}

/** {canonical_field: source_column} */
export type StockImportMapping = Record<string, string>

export interface StockImportRowError {
  row:    number
  sku:    string
  /** Stable code the UI renders through i18n; `error` is the English fallback. */
  code:   string
  params: Record<string, unknown>
  error:  string
}

export interface StockImportIssueGroup {
  code:    string
  column:  string | null
  count:   number
  samples: { row: number; sku: string; value: unknown }[]
}

export interface StockImportPreview {
  format:            'csv' | 'excel'
  separator:         string
  columns:           string[]
  total_rows:        number
  mapping:           StockImportMapping
  detected_mapping:  StockImportMapping
  unmapped_columns:  string[]
  missing_required:  string[]
  importable_rows:   number
  rejected_rows:     number
  skipped_no_sku:    number
  sample_rows:       Record<string, unknown>[]
  issues:            StockImportIssueGroup[]
  fields:            string[]
}

export interface StockImportResult {
  imported:          number
  total_rows:        number
  format:            'csv' | 'excel'
  mapping:           StockImportMapping
  detected_mapping:  StockImportMapping
  unmapped_columns:  string[]
  skipped_no_sku:    number
  errors?:           StockImportRowError[]
  error_count?:      number
}

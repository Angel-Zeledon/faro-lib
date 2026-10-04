// Types for the suppliers / purchase-orders bulk importers:
// POST /inventory/suppliers/import[/preview] and /inventory/po/import[/preview].

export type BulkImportKind = 'suppliers' | 'orders'

/** {canonical_field: source_column} */
export type BulkImportMapping = Record<string, string>

export interface BulkImportRowError {
  row:    number
  /** The supplier name / product code / order reference the row was about. */
  ref:    string
  /** Stable code the UI renders through i18n; `error` is the English fallback. */
  code:   string
  params: Record<string, unknown>
  error:  string
}

export interface BulkImportIssueGroup {
  code:    string
  column:  string | null
  count:   number
  samples: { row: number; ref: string; value: unknown }[]
}

interface PreviewCommon {
  format:           'csv' | 'excel'
  separator:        string
  columns:          string[]
  total_rows:       number
  mapping:          BulkImportMapping
  detected_mapping: BulkImportMapping
  unmapped_columns: string[]
  missing_required: string[]
  fields:           string[]
  rejected_rows:    number
  blank_rows:       number
  issues:           BulkImportIssueGroup[]
  errors:           BulkImportRowError[]
  error_count:      number
  sample_rows:      Record<string, unknown>[]
  importable_rows:  number
}

export interface SuppliersImportPreview extends PreviewCommon {
  new_suppliers:         number
  existing_suppliers:    number
  deactivated_suppliers: number
  duplicate_rows:        number
}

export interface OrderImportSummary {
  order_ref:          string
  supplier:           string
  warehouse:          string | null
  line_count:         number
  total_units:        number
  total_value:        number | null
  lines_without_cost: number
  already_imported:   boolean
}

export interface OrdersImportPreview extends PreviewCommon {
  order_count:             number
  orders:                  OrderImportSummary[]
  already_imported_orders: number
  folded_rows:             number
}

interface ResultCommon {
  total_rows:  number
  format:      'csv' | 'excel'
  mapping:     BulkImportMapping
  blank_rows:  number
  error_count: number
  errors:      BulkImportRowError[]
}

export interface SuppliersImportResult extends ResultCommon {
  created:          number
  updated:          number
  skipped_existing: number
  duplicate_rows:   number
  on_existing:      'skip' | 'update'
}

export interface OrdersImportResult extends ResultCommon {
  created_orders:          number
  already_imported_orders: number
  folded_rows:             number
  orders: { id: string; po_number: string; order_ref: string; supplier: string; line_count: number }[]
}

export type BulkImportPreview = SuppliersImportPreview | OrdersImportPreview
export type BulkImportResult = SuppliersImportResult | OrdersImportResult

// Pre-upload CSV validation (feature 1.3): catches the classic onboarding
// failures — wrong separator, missing columns, unparseable dates, non-numeric
// quantities — and reports them with row numbers BEFORE the file leaves the
// browser. Excel files (.xlsx/.xls) skip this and are validated server-side.
//
// Issues are returned twice: as flat `errors`/`warnings` strings (blocking
// summary, kept for backwards compatibility) and as `issueGroups` — grouped by
// kind, each carrying the first few offending rows plus how many were elided
// ("y 37 más"). The UI renders the groups; the strings drive the block/continue
// decision.

export type CsvIssueKind =
  | 'column_count'
  | 'invalid_date'
  | 'non_numeric_qty'
  | 'negative_qty'
  | 'empty_sku'

export interface CsvIssue {
  /** 1-based line number in the file, counting the header as row 1. */
  row:     number
  kind:    CsvIssueKind
  /** Human message in Spanish, already prefixed with "Fila N:". */
  message: string
}

export interface CsvIssueGroup {
  kind:  CsvIssueKind
  /** Short Spanish title for the group, e.g. "Fechas inválidas". */
  title: string
  /** What the user should do about it. */
  hint:  string
  /** Total rows affected (not just the reported sample). */
  count: number
  /** First `MAX_PER_GROUP` messages. */
  samples: string[]
  /** count - samples.length — drives the "y N más" line. */
  hidden: number
  /** True when this group alone makes the file unusable. */
  fatal: boolean
}

export interface CsvCheckResult {
  ok: boolean
  /** Fatal problems — uploading would fail or produce a useless forecast */
  errors: string[]
  /** Suspicious but not fatal — the user can continue */
  warnings: string[]
  rowCount: number
  columns: string[]
  /** Per-row problems grouped by kind. Populated even when `ok` is true. */
  issueGroups: CsvIssueGroup[]
}

/** How many offending rows are listed per group before collapsing into "y N más". */
const MAX_PER_GROUP = 5

// Ceiling for a single period's sales of one SKU. Anything above this is a
// corrupted cell (a barcode or an id landing in the quantity column), not a
// real order: it matches the backend's own per-line cap so the file is
// rejected here instead of failing later on the server.
const MAX_REASONABLE_QTY = 1_000_000_000

const DATE_HINTS = ['fecha', 'date', 'dia', 'día', 'day', 'periodo']
const QTY_HINTS  = ['cantidad', 'demanda', 'venta', 'ventas', 'qty', 'quantity', 'demand', 'sales', 'units', 'unidades']
const SKU_HINTS  = ['sku', 'producto', 'product', 'articulo', 'artículo', 'item', 'codigo', 'código', 'code', 'referencia', 'ref']

// ── Downloadable template ─────────────────────────────────────────────────────
// Header names are the canonical aliases the backend profiler recognises with
// high confidence (forecasting_core/data/profiler.py `_CANONICAL_ALIASES`), so
// a file built from this template auto-maps without manual column picking.
// sku / fecha / demanda are the three required canonical fields
// (forecasting_core/data/canonical.py `REQUIRED_FIELDS`); the rest are optional
// and may be deleted.

export const CSV_TEMPLATE_FILENAME = 'plantilla_faro.csv'

/** Canonical header row of the template, in order. */
export const CSV_TEMPLATE_HEADERS = [
  'sku', 'fecha', 'demanda', 'tienda', 'inventario', 'costo', 'precio',
] as const

const CSV_TEMPLATE_ROWS: string[][] = [
  ['SKU-001', '2026-01-01', '32', 'Bodega Central', '480', '8.50', '12.90'],
  ['SKU-001', '2026-01-02', '28', 'Bodega Central', '452', '8.50', '12.90'],
  ['SKU-001', '2026-01-03', '35', 'Bodega Central', '417', '8.50', '12.90'],
  ['SKU-002', '2026-01-01', '15', 'Bodega Central', '210', '5.20', '7.80'],
  ['SKU-002', '2026-01-02', '18', 'Bodega Central', '192', '5.20', '7.80'],
  ['SKU-002', '2026-01-03', '12', 'Bodega Central', '180', '5.20', '7.80'],
]

/** Build the example CSV as text (comma-separated, one header + sample rows). */
export function buildCsvTemplate(): string {
  const lines = [CSV_TEMPLATE_HEADERS.join(','), ...CSV_TEMPLATE_ROWS.map(r => r.join(','))]
  return lines.join('\r\n') + '\r\n'
}

/**
 * Trigger a browser download of the example CSV. No-op outside the browser.
 * BOM prefixed so Excel opens the accented headers correctly.
 */
export function downloadCsvTemplate(filename: string = CSV_TEMPLATE_FILENAME): void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  const blob = new Blob(['﻿' + buildCsvTemplate()], { type: 'text/csv;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── Parsing helpers ───────────────────────────────────────────────────────────

export function detectSeparator(headerLine: string): string {
  const counts: [string, number][] = [
    [',', (headerLine.match(/,/g) || []).length],
    [';', (headerLine.match(/;/g) || []).length],
    ['\t', (headerLine.match(/\t/g) || []).length],
  ]
  counts.sort((a, b) => b[1] - a[1])
  return counts[0][1] > 0 ? counts[0][0] : ','
}

function splitLine(line: string, sep: string): string[] {
  // Minimal quoted-field-aware split (handles "a,b" cells)
  const out: string[] = []
  let cur = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') { cur += '"'; i++ }
      else inQuotes = !inQuotes
    } else if (ch === sep && !inQuotes) {
      out.push(cur); cur = ''
    } else {
      cur += ch
    }
  }
  out.push(cur)
  return out.map(c => c.trim())
}

/**
 * `Date.parse` alone is uselessly permissive — in V8 it happily accepts
 * "not-a-date-13" (it latches onto the trailing number). So a free-form string
 * is only trusted when it also *looks* like a date: it must carry a 4-digit
 * year. Everything else falls through to the strict numeric patterns below.
 */
function looseDate(raw: string): boolean {
  if (!/\d{4}/.test(raw)) return false
  return !isNaN(Date.parse(raw))
}

function parseableDate(raw: string): boolean {
  if (!raw) return false
  // ISO / y-m-d / d-m-y / d/m/y with 2-4 digit year
  const m = raw.match(/^(\d{1,4})[-/](\d{1,2})[-/](\d{1,4})$/)
  if (!m) return looseDate(raw)
  const a = parseInt(m[1]), b = parseInt(m[2]), c = parseInt(m[3])
  const [day, month] = a > 31 ? [c, b] : [a, b]   // yyyy-mm-dd vs dd/mm/yyyy
  if (month < 1 || month > 12) return false
  if (day < 1 || day > 31) return false
  // reject impossible dates like 31/02
  const year = a > 31 ? a : c
  const fullYear = year < 100 ? 2000 + year : year
  const daysInMonth = new Date(fullYear, month, 0).getDate()
  return day <= daysInMonth
}

/** Why this particular date string could not be read — used in the row message. */
function dateFailureReason(raw: string): string {
  if (!raw.trim()) return 'la celda está vacía'
  const m = raw.match(/^(\d{1,4})[-/](\d{1,2})[-/](\d{1,4})$/)
  if (!m) return 'no tiene un formato de fecha reconocible'
  const a = parseInt(m[1]), b = parseInt(m[2]), c = parseInt(m[3])
  const [day, month] = a > 31 ? [c, b] : [a, b]
  if (month < 1 || month > 12) return `el mes ${month} no existe`
  if (day < 1 || day > 31)     return `el día ${day} no existe`
  return 'ese día no existe en ese mes'
}

const GROUP_META: Record<CsvIssueKind, { title: string; hint: string }> = {
  column_count: {
    title: 'Filas con un número de columnas distinto al encabezado',
    hint:  'Suele pasar cuando una celda contiene el separador (una coma dentro de un texto). Enciérrala entre comillas dobles o quita la coma.',
  },
  invalid_date: {
    title: 'Fechas inválidas',
    hint:  'Se espera el formato AAAA-MM-DD (ej. 2026-01-15). También se aceptan DD/MM/AAAA y DD-MM-AAAA.',
  },
  non_numeric_qty: {
    title: 'Cantidades no numéricas',
    hint:  'La columna de cantidad debe contener solo números. Quita símbolos de moneda, separadores de miles y textos como "N/D".',
  },
  negative_qty: {
    title: 'Cantidades negativas',
    hint:  'Las devoluciones se registran aparte. Una demanda negativa distorsiona el pronóstico de ese producto.',
  },
  empty_sku: {
    title: 'Filas sin producto',
    hint:  'Cada fila necesita el código o nombre del producto para poder pronosticar por SKU.',
  },
}

// ── Validator ─────────────────────────────────────────────────────────────────

export function validateSalesCsv(text: string): CsvCheckResult {
  const errors: string[] = []
  const warnings: string[] = []

  // strip BOM, normalize newlines, drop trailing empties
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim().length > 0)
  if (lines.length < 2) {
    return {
      ok: false,
      errors: ['El archivo está vacío o solo tiene encabezado.'],
      warnings, rowCount: 0, columns: [], issueGroups: [],
    }
  }

  const sep = detectSeparator(lines[0])
  const header = splitLine(lines[0], sep).map(h => h.toLowerCase())
  const columns = splitLine(lines[0], sep)

  if (header.length < 3) {
    errors.push(
      `Solo se detectó ${header.length} columna(s). Se necesitan al menos 3: fecha, producto y cantidad. ` +
      'Revisa que el separador sea coma (,) o punto y coma (;).'
    )
    return { ok: false, errors, warnings, rowCount: lines.length - 1, columns, issueGroups: [] }
  }

  const dateIdx = header.findIndex(h => DATE_HINTS.some(k => h.includes(k)))
  const qtyIdx  = header.findIndex(h => QTY_HINTS.some(k => h.includes(k)))
  const skuIdx  = header.findIndex(h => SKU_HINTS.some(k => h.includes(k)))

  if (dateIdx === -1) {
    warnings.push('No se identificó una columna de fecha por su nombre (ej: "fecha"). Podrás elegirla manualmente en el siguiente paso.')
  }
  if (qtyIdx === -1) {
    warnings.push('No se identificó una columna de cantidad por su nombre (ej: "demanda" o "cantidad"). Podrás elegirla manualmente en el siguiente paso.')
  }
  if (skuIdx === -1) {
    warnings.push('No se identificó una columna de producto por su nombre (ej: "sku" o "producto"). Podrás elegirla manualmente en el siguiente paso.')
  }

  const issues: CsvIssue[] = []
  const rows = lines.length - 1

  for (let i = 1; i < lines.length; i++) {
    const cells = splitLine(lines[i], sep)
    const rowN = i + 1  // 1-based, counting the header as row 1

    if (cells.length !== header.length) {
      issues.push({
        row: rowN, kind: 'column_count',
        message: `Fila ${rowN}: tiene ${cells.length} columna(s) y el encabezado tiene ${header.length}.`,
      })
      continue   // cells are misaligned — per-column checks would be nonsense
    }

    if (dateIdx >= 0 && !parseableDate(cells[dateIdx])) {
      issues.push({
        row: rowN, kind: 'invalid_date',
        message: `Fila ${rowN}: fecha inválida "${cells[dateIdx]}" en la columna "${columns[dateIdx]}" — ${dateFailureReason(cells[dateIdx])}. Se esperaba AAAA-MM-DD.`,
      })
    }

    if (qtyIdx >= 0) {
      const raw = cells[qtyIdx]
      const v = raw.replace(',', '.')
      // `Number.isFinite`, not `!isNaN`: Number('1e309') is Infinity and
      // isNaN(Infinity) is false, so an overflowing figure — the kind a
      // corrupted export produces — used to sail through as a valid quantity
      // and reach training as `inf`, poisoning that SKU's metrics.
      if (v === '' || !Number.isFinite(Number(v))) {
        issues.push({
          row: rowN, kind: 'non_numeric_qty',
          message: raw === ''
            ? `Fila ${rowN}: cantidad vacía en la columna "${columns[qtyIdx]}" — se esperaba un número.`
            : `Fila ${rowN}: cantidad no numérica "${raw}" en la columna "${columns[qtyIdx]}" — se esperaba un número.`,
        })
      } else if (Number(v) > MAX_REASONABLE_QTY) {
        issues.push({
          row: rowN, kind: 'non_numeric_qty',
          message: `Fila ${rowN}: cantidad fuera de rango "${raw}" en la columna "${columns[qtyIdx]}" — se esperaba un número menor que ${MAX_REASONABLE_QTY.toLocaleString('es')}.`,
        })
      } else if (Number(v) < 0) {
        issues.push({
          row: rowN, kind: 'negative_qty',
          message: `Fila ${rowN}: cantidad negativa "${raw}" en la columna "${columns[qtyIdx]}".`,
        })
      }
    }

    if (skuIdx >= 0 && cells[skuIdx] === '') {
      issues.push({
        row: rowN, kind: 'empty_sku',
        message: `Fila ${rowN}: la columna de producto "${columns[skuIdx]}" está vacía.`,
      })
    }
  }

  const countOf = (k: CsvIssueKind) => issues.reduce((n, it) => n + (it.kind === k ? 1 : 0), 0)
  const badCols  = countOf('column_count')
  const badDates = countOf('invalid_date')
  const badQtys  = countOf('non_numeric_qty')

  // A few bad rows in a big file are tolerable (the backend skips them);
  // a majority of bad rows means the file itself is malformed.
  const fatalKinds = new Set<CsvIssueKind>()
  if (badCols  > rows * 0.1) fatalKinds.add('column_count')
  if (badDates > rows * 0.2) fatalKinds.add('invalid_date')
  if (badQtys  > rows * 0.2) fatalKinds.add('non_numeric_qty')
  const fatal = fatalKinds.size > 0

  // Group in a stable, severity-ordered sequence
  const ORDER: CsvIssueKind[] = ['column_count', 'invalid_date', 'non_numeric_qty', 'empty_sku', 'negative_qty']
  const issueGroups: CsvIssueGroup[] = ORDER.flatMap(kind => {
    const of = issues.filter(it => it.kind === kind)
    if (of.length === 0) return []
    const samples = of.slice(0, MAX_PER_GROUP).map(it => it.message)
    return [{
      kind,
      title:   GROUP_META[kind].title,
      hint:    GROUP_META[kind].hint,
      count:   of.length,
      samples,
      hidden:  of.length - samples.length,
      fatal:   fatalKinds.has(kind),
    }]
  })

  if (fatal) {
    for (const g of issueGroups.filter(g => g.fatal)) {
      errors.push(`${g.title}: ${g.count} de ${rows} fila(s).`)
    }
  } else if (issues.length > 0) {
    warnings.push(`${issues.length} fila(s) con problemas serán ignoradas al procesar. El resto se usará normalmente.`)
  }

  if (rows < 30) {
    warnings.push(`Solo ${rows} filas de datos — se recomienda al menos 60 días de historial para un pronóstico útil.`)
  }

  return {
    ok: !fatal,
    errors,
    warnings,
    rowCount: rows,
    columns,
    issueGroups,
  }
}

// ── Stock file validation (onboarding-friction plan #1, phase 4) ──────────────
// The stock importer used to demand our exact headers, which no LatAm ERP
// exports — so it went unused. This is the same pre-upload check the sales
// file gets, reusing the separator sniffing and the quoted-field split above,
// tuned for a stock export: a product code, a quantity, a cost.
//
// Unlike `validateSalesCsv` (whose Spanish messages predate the i18n layer and
// are consumed as plain strings by the wizard), NOTHING here returns copy:
// every issue is an i18n KEY plus params, and the component renders it. New
// user-visible text in this file goes through i18n, per CLAUDE.md.

export type StockCsvIssueKind =
  | 'column_count'
  | 'empty_sku'
  | 'duplicate_sku'
  | 'non_numeric'
  | 'negative'

export interface StockCsvIssue {
  row:    number
  kind:   StockCsvIssueKind
  /** i18n key for the per-row line, e.g. `csvStock.row.non_numeric`. */
  key:    string
  params: Record<string, string | number>
}

export interface StockCsvIssueGroup {
  kind:     StockCsvIssueKind
  titleKey: string
  hintKey:  string
  count:    number
  /** First few offending rows, as key+params for the renderer. */
  samples:  StockCsvIssue[]
  hidden:   number
  fatal:    boolean
}

export interface StockCsvCheckResult {
  ok:          boolean
  columns:     string[]
  rowCount:    number
  separator:   string
  /** True when the file writes decimals with a comma ("3,75"). */
  decimalComma: boolean
  /** Column index guessed for the product code; -1 when none looks like one. */
  skuIndex:    number
  /** Blocking problems as i18n keys — an empty file, or no product column. */
  errors:      { key: string; params: Record<string, string | number> }[]
  groups:      StockCsvIssueGroup[]
}

// Header hints, unaccented and lowercased. Kept deliberately in step with the
// backend alias table (backend/utils/stock_import.py `_ALIASES`) — this side
// only has to be good enough to warn before uploading; the backend's answer is
// the one that decides the import.
const STOCK_SKU_HINTS   = [...SKU_HINTS, 'clave', 'cod', 'material']
const STOCK_QTY_HINTS   = ['existencia', 'existencias', 'stock', 'inventario', 'cantidad',
                           'saldo', 'disponible', 'on hand', 'unidades']
const STOCK_MONEY_HINTS = ['costo', 'precio', 'cost', 'price', 'pvp']

/** Accent- and punctuation-blind header key, mirroring the backend's normalize(). */
function normalizeHeader(header: string): string {
  return (header || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

/**
 * True when a comma is used as the decimal mark anywhere in these cells: a
 * comma followed by anything other than a 3-digit group is never a thousands
 * separator. Decided once per FILE so an ambiguous "1,250" inherits the answer
 * its unambiguous neighbours already gave — the same rule the backend applies.
 */
export function hasDecimalComma(samples: string[]): boolean {
  return samples.some(s => /,\d{1,2}(?!\d)/.test(s || '') || /,\d{4,}/.test(s || ''))
}

/**
 * Read a spreadsheet cell as a number, or null when it is not one. Mirrors
 * backend/utils/stock_import.py parse_number: money symbols, spaced or dotted
 * thousands, comma decimals, accounting negatives.
 */
export function parseLatamNumber(raw: string, decimalComma?: boolean): number | null {
  if (raw == null) return null
  let text = String(raw).trim()
  if (!text) return null

  let negative = false
  if (text.startsWith('(') && text.endsWith(')')) { negative = true; text = text.slice(1, -1).trim() }
  if (text.endsWith('-')) { negative = true; text = text.slice(0, -1).trim() }

  const tokens = text.split(/\s+/).filter(t => /\d/.test(t))
  if (tokens.length === 0) return null
  if (tokens.length === 1) text = tokens[0]
  else if (/^\d{1,3}$/.test(tokens[0]) && tokens.slice(1).every(t => /^\d{3}$/.test(t))) {
    text = tokens.join('')          // spaced thousands: "1 234 567"
  } else return null

  // Symbols glued to the figure, never letters: stripping letters would turn
  // the garbage cell "12abc" into a silent 12.
  text = text.replace(/^[^\w.,-]+|[^\w.,-]+$/g, '')
  if (text.startsWith('-')) { negative = true; text = text.slice(1) }
  if (!text || /[^\d.,]/.test(text)) return null

  const hasDot = text.includes('.'), hasComma = text.includes(',')
  if (hasDot && hasComma) {
    text = text.lastIndexOf(',') > text.lastIndexOf('.')
      ? text.replace(/\./g, '').replace(',', '.')
      : text.replace(/,/g, '')
  } else if (hasComma) {
    const groups = text.split(',')
    const looksLikeThousands = groups.length > 1 && groups.slice(1).every(g => g.length === 3)
    const useDecimal = decimalComma === undefined ? !looksLikeThousands : decimalComma
    text = useDecimal ? text.replace(',', '.') : text.replace(/,/g, '')
  } else if (hasDot) {
    const groups = text.split('.')
    if (groups.length > 2 && groups.slice(1).every(g => g.length === 3)) text = text.replace(/\./g, '')
    else if (groups.length === 2 && groups[1].length === 3 && decimalComma) text = text.replace('.', '')
  }

  if (!/^\d*\.?\d+$/.test(text)) return null
  const value = Number(text)
  if (!Number.isFinite(value)) return null
  return negative ? -value : value
}

// A malformed row here and there is skipped by the importer; a majority of
// them means the file itself is broken and uploading is a waste of time.
// Ratios above 1 mean "never fatal".
const STOCK_FATAL_RATIO: Record<StockCsvIssueKind, number> = {
  column_count:  0.1,
  empty_sku:     0.5,
  duplicate_sku: 1.1,   // the last row simply wins
  non_numeric:   0.5,
  negative:      1.1,   // those rows are rejected one by one
}

const STOCK_ISSUE_ORDER: StockCsvIssueKind[] =
  ['column_count', 'empty_sku', 'non_numeric', 'duplicate_sku', 'negative']

/**
 * Pre-upload check for a stock file. Returns i18n keys, never copy.
 *
 * Excel files skip this (they are validated by POST /inventory/bulk/preview,
 * which reads them server-side) — exactly like the sales upload.
 */
export function validateStockCsv(text: string): StockCsvCheckResult {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim().length > 0)
  if (lines.length < 2) {
    return {
      ok: false, columns: [], rowCount: 0, separator: ',', decimalComma: false,
      skuIndex: -1, errors: [{ key: 'csvStock.error.empty_file', params: {} }], groups: [],
    }
  }

  const sep     = detectSeparator(lines[0])
  const columns = splitLine(lines[0], sep)
  const header  = columns.map(normalizeHeader)

  const findIdx = (hints: string[]) =>
    header.findIndex(h => hints.some(k => h === k || h.includes(k)))

  const skuIdx   = findIdx(STOCK_SKU_HINTS)
  const qtyIdx   = findIdx(STOCK_QTY_HINTS)
  const moneyIdx = findIdx(STOCK_MONEY_HINTS)

  const rows = lines.length - 1
  const numericIdx = [qtyIdx, moneyIdx].filter(i => i >= 0)

  // File-level decimal verdict first — every cell is then read the same way.
  const numericSamples: string[] = []
  for (let i = 1; i < lines.length; i++) {
    const cells = splitLine(lines[i], sep)
    for (const idx of numericIdx) if (cells[idx]) numericSamples.push(cells[idx])
  }
  const decimalComma = hasDecimalComma(numericSamples)

  const issues: StockCsvIssue[] = []
  const seen = new Set<string>()

  for (let i = 1; i < lines.length; i++) {
    const cells = splitLine(lines[i], sep)
    const rowN  = i + 1

    if (cells.length !== header.length) {
      issues.push({ row: rowN, kind: 'column_count', key: 'csvStock.row.column_count',
                    params: { row: rowN, found: cells.length, expected: header.length } })
      continue
    }

    if (skuIdx >= 0) {
      const sku = cells[skuIdx]
      if (!sku) {
        issues.push({ row: rowN, kind: 'empty_sku', key: 'csvStock.row.empty_sku',
                      params: { row: rowN, column: columns[skuIdx] } })
      } else if (seen.has(sku)) {
        issues.push({ row: rowN, kind: 'duplicate_sku', key: 'csvStock.row.duplicate_sku',
                      params: { row: rowN, sku } })
      } else {
        seen.add(sku)
      }
    }

    for (const idx of numericIdx) {
      const raw = cells[idx]
      if (!raw) continue                       // blank optional cell, not garbage
      const value = parseLatamNumber(raw, decimalComma)
      if (value === null) {
        issues.push({ row: rowN, kind: 'non_numeric', key: 'csvStock.row.non_numeric',
                      params: { row: rowN, column: columns[idx], value: raw } })
      } else if (value < 0) {
        issues.push({ row: rowN, kind: 'negative', key: 'csvStock.row.negative',
                      params: { row: rowN, column: columns[idx], value: raw } })
      }
    }
  }

  const groups: StockCsvIssueGroup[] = STOCK_ISSUE_ORDER.flatMap(kind => {
    const of = issues.filter(it => it.kind === kind)
    if (of.length === 0) return []
    const samples = of.slice(0, MAX_PER_GROUP)
    return [{
      kind,
      titleKey: `csvStock.group.${kind}.title`,
      hintKey:  `csvStock.group.${kind}.hint`,
      count:    of.length,
      samples,
      hidden:   of.length - samples.length,
      fatal:    of.length > rows * STOCK_FATAL_RATIO[kind],
    }]
  })

  const errors: { key: string; params: Record<string, string | number> }[] = []
  if (skuIdx === -1) {
    // Not fatal on its own: the mapping wizard lets the user point at the
    // column by hand, which is the entire point of having a wizard.
    errors.push({ key: 'csvStock.error.no_sku_column', params: { columns: columns.join(', ') } })
  }
  for (const g of groups.filter(g => g.fatal)) {
    errors.push({ key: `csvStock.error.too_many_${g.kind}`, params: { count: g.count, rows } })
  }

  return {
    ok: !groups.some(g => g.fatal),
    columns, rowCount: rows, separator: sep, decimalComma, skuIndex: skuIdx,
    errors, groups,
  }
}

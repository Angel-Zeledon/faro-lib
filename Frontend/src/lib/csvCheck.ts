// Pre-upload CSV validation (feature 1.3): catches the classic onboarding
// failures — wrong separator, missing columns, unparseable dates, non-numeric
// quantities — and reports them with row numbers BEFORE the file leaves the
// browser. Excel files (.xlsx/.xls) skip this and are validated server-side.

export interface CsvCheckResult {
  ok: boolean
  /** Fatal problems — uploading would fail or produce a useless forecast */
  errors: string[]
  /** Suspicious but not fatal — the user can continue */
  warnings: string[]
  rowCount: number
  columns: string[]
}

const MAX_REPORTED = 8
const DATE_HINTS = ['fecha', 'date', 'dia', 'día', 'day', 'periodo']
const QTY_HINTS  = ['cantidad', 'demanda', 'venta', 'ventas', 'qty', 'quantity', 'demand', 'sales', 'units', 'unidades']

function detectSeparator(headerLine: string): string {
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

function parseableDate(raw: string): boolean {
  if (!raw) return false
  // ISO / y-m-d / d-m-y / d/m/y with 2-4 digit year
  const m = raw.match(/^(\d{1,4})[-/](\d{1,2})[-/](\d{1,4})$/)
  if (!m) return !isNaN(Date.parse(raw))
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

export function validateSalesCsv(text: string): CsvCheckResult {
  const errors: string[] = []
  const warnings: string[] = []

  // strip BOM, normalize newlines, drop trailing empties
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim().length > 0)
  if (lines.length < 2) {
    return { ok: false, errors: ['El archivo está vacío o solo tiene encabezado.'], warnings, rowCount: 0, columns: [] }
  }

  const sep = detectSeparator(lines[0])
  const header = splitLine(lines[0], sep).map(h => h.toLowerCase())
  const columns = splitLine(lines[0], sep)

  if (header.length < 3) {
    errors.push(
      `Solo se detectó ${header.length} columna(s). Se necesitan al menos 3: fecha, producto y cantidad. ` +
      'Revisa que el separador sea coma (,) o punto y coma (;).'
    )
    return { ok: false, errors, warnings, rowCount: lines.length - 1, columns }
  }

  const dateIdx = header.findIndex(h => DATE_HINTS.some(k => h.includes(k)))
  const qtyIdx  = header.findIndex(h => QTY_HINTS.some(k => h.includes(k)))

  if (dateIdx === -1) {
    warnings.push('No se identificó una columna de fecha por su nombre (ej: "fecha"). Podrás elegirla manualmente en el siguiente paso.')
  }
  if (qtyIdx === -1) {
    warnings.push('No se identificó una columna de cantidad por su nombre (ej: "cantidad"). Podrás elegirla manualmente en el siguiente paso.')
  }

  let badDates = 0
  let badQtys = 0
  let badCols = 0
  const rows = lines.length - 1

  for (let i = 1; i < lines.length; i++) {
    const cells = splitLine(lines[i], sep)
    const rowN = i + 1  // 1-based, counting the header as row 1

    if (cells.length !== header.length) {
      badCols++
      if (badCols <= MAX_REPORTED) {
        errors.push(`Fila ${rowN}: tiene ${cells.length} columnas y el encabezado tiene ${header.length}.`)
      }
      continue
    }
    if (dateIdx >= 0 && !parseableDate(cells[dateIdx])) {
      badDates++
      if (badDates <= MAX_REPORTED) {
        errors.push(`Fila ${rowN}: fecha inválida "${cells[dateIdx]}" en columna "${columns[dateIdx]}".`)
      }
    }
    if (qtyIdx >= 0) {
      const v = cells[qtyIdx].replace(',', '.')
      if (v === '' || isNaN(Number(v))) {
        badQtys++
        if (badQtys <= MAX_REPORTED) {
          errors.push(`Fila ${rowN}: cantidad no numérica "${cells[qtyIdx]}" en columna "${columns[qtyIdx]}".`)
        }
      }
    }
  }

  const totalBad = badDates + badQtys + badCols
  if (totalBad > MAX_REPORTED) {
    errors.push(`… y ${totalBad - MAX_REPORTED} problema(s) más.`)
  }

  // A few bad rows in a big file are tolerable (backend skips them);
  // a majority of bad rows means the file itself is malformed.
  const fatal = badCols > rows * 0.1 || badDates > rows * 0.2 || badQtys > rows * 0.2
  if (totalBad > 0 && !fatal) {
    warnings.push(`${totalBad} fila(s) con problemas serán ignoradas al procesar. El resto se usará normalmente.`)
  }

  if (rows < 30) {
    warnings.push(`Solo ${rows} filas de datos — se recomienda al menos 60 días de historial para un pronóstico útil.`)
  }

  return {
    ok: !fatal && !(header.length < 3),
    errors: fatal ? errors : [],
    warnings: fatal ? warnings : [...warnings],
    rowCount: rows,
    columns,
  }
}

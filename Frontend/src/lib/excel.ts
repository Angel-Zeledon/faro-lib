// Shared XLSX export helper built on exceljs (maintained), replacing the
// abandoned-on-npm `xlsx` package (last npm release 0.18.5, flagged by audit).
// Write-only: this app never parses spreadsheets in the browser.

export type SheetRow = (string | number | null | undefined)[]
export interface SheetSpec {
  name: string
  rows: SheetRow[]
}

const XLSX_MIME =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** Excel sheet names: max 31 chars, no \ / : * ? [ ] */
const SHEET_NAME_MAX = 31

function safeSheetName(name: string): string {
  return (name.replace(/[\\/:*?[\]]/g, '_').substring(0, SHEET_NAME_MAX)) || 'Sheet'
}

/**
 * A unique sheet name, with the disambiguating suffix kept INSIDE the 31-char
 * budget instead of being truncated off the end.
 *
 * This used to be `while (used.has(name)) name = safeSheetName(`${s.name}_${i++}`)`.
 * Because `safeSheetName` truncates to 31, appending `_2` to a name that is
 * already 31 characters or longer produced the exact same string — so the loop
 * never terminated. It runs synchronously on the main thread, so two SKUs
 * sharing their first 31 characters (`PROD-DISTRIBUIDORA-NORTE-0000001` and
 * `…0000002` — ordinary in a real catalogue) froze the tab for good, right
 * after the progress counter had reached N/N.
 */
function uniqueSheetName(rawName: string, used: Set<string>): string {
  const base = safeSheetName(rawName)
  if (!used.has(base)) return base
  for (let i = 2; i < 10_000; i++) {
    const suffix = `_${i}`
    const candidate = base.substring(0, SHEET_NAME_MAX - suffix.length) + suffix
    if (!used.has(candidate)) return candidate
  }
  // 9,998 collisions on one 31-char prefix is not a spreadsheet anybody asked
  // for, but returning something unique still beats hanging.
  return safeSheetName(`${Date.now()}`)
}

export async function downloadWorkbook(filename: string, sheets: SheetSpec[]): Promise<void> {
  const ExcelJS = await import('exceljs')
  const wb = new ExcelJS.Workbook()
  const used = new Set<string>()
  for (const s of sheets) {
    const name = uniqueSheetName(s.name, used)
    used.add(name)
    const ws = wb.addWorksheet(name)
    for (const row of s.rows) {
      ws.addRow(row.map(v => (v === undefined ? null : v)))
    }
  }
  const buf = await wb.xlsx.writeBuffer()
  const blob = new Blob([buf], { type: XLSX_MIME })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

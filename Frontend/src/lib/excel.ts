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
function safeSheetName(name: string): string {
  return (name.replace(/[\\/:*?[\]]/g, '_').substring(0, 31)) || 'Sheet'
}

export async function downloadWorkbook(filename: string, sheets: SheetSpec[]): Promise<void> {
  const ExcelJS = await import('exceljs')
  const wb = new ExcelJS.Workbook()
  const used = new Set<string>()
  for (const s of sheets) {
    let name = safeSheetName(s.name)
    let i = 2
    while (used.has(name)) name = safeSheetName(`${s.name}_${i++}`)
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

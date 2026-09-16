/**
 * The one place the browser writes a CSV the user downloads.
 *
 * Two of these files — both named `purchase_order.csv`, both leaving the
 * tenant and arriving at a supplier — were built by raw template
 * interpolation:
 *
 *     rows.push(`${sku},"${name}",${qty},"${supplier || ''}",${value}`)
 *
 * which does none of the three things the backend writer of the SAME artifact
 * does deliberately (`backend/api/v1/inventory.py`, `backend/utils/csv_safe.py`):
 *
 *  1. it never doubles an embedded `"`, so a product called `Cable 3/4" negro`
 *     silently shifted every later column — in a document a supplier acts on;
 *  2. it never neutralised a leading `=` `+` `-` `@`, so a supplier name
 *     imported from the tenant's own catalogue executed as a formula on open;
 *  3. it never prefixed a UTF-8 BOM, so Excel on a Spanish-locale Windows read
 *     `Señal` as `SeÃ±al` and `Distribuidora Peña` as `Distribuidora PeÃ±a`.
 *
 * `csvCheck.ts` already prefixed the BOM on the template it writes, so the
 * product knew — it just applied it in one writer out of several.
 */

const DANGEROUS_PREFIXES = ['=', '+', '-', '@', '\t', '\r']

/**
 * One cell, escaped and made inert. Mirrors `backend/utils/csv_safe.py` so the
 * same purchase order is written the same way whichever layer produced it.
 */
export function csvCell(value: string | number | null | undefined): string {
  if (value == null) return ''
  let text = String(value)
  if (text.length > 0 && DANGEROUS_PREFIXES.includes(text[0])) text = `'${text}`
  // Always quoted, quotes doubled: a value carrying a comma, a newline or a
  // quote is then unambiguous, and a value carrying none is unharmed.
  return `"${text.replace(/"/g, '""')}"`
}

/** A number as a bare CSV cell, or an EMPTY cell when it is not known.
 *
 * `qty * (unit_cost ?? 0)` printed a line nobody had priced as a confident 0
 * under a header that says "estimated value" — the exact defect
 * `backend/inventory/po_pdf.py` documents having fixed for the PDF ("a line
 * whose cost nobody recorded is priced as UNKNOWN, not as zero… the supplier
 * has no way to tell that apart from a price the buyer meant"). A real cost of
 * 0 still prints 0: free and unpriced are different facts.
 */
export function csvNumber(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '' : String(value)
}

export function buildCsv(header: string[], rows: string[][]): string {
  return [header.join(','), ...rows.map(r => r.join(','))].join('\r\n') + '\r\n'
}

/** Hand the file to the browser, BOM first. No-op outside the browser. */
export function downloadCsv(filename: string, content: string): void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  const blob = new Blob(['﻿' + content], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

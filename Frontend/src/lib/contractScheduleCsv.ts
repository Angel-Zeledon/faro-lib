/**
 * Parses a release schedule pasted from a spreadsheet (or typed) for a blanket
 * contract. Pure: no React, no translation; problems come back as i18n keys.
 *
 * Accepted shapes, tab / comma / semicolon separated, with or without a header:
 *   date, quantity            (a contract with one product)
 *   sku, date, quantity       (any contract)
 * Header aliases are Spanish and English (fecha / date, cantidad / quantity,
 * sku / producto). A row with a date and no quantity is a problem, never a
 * zero: the server refuses it too, and the user must say what the release is.
 */
import { normHeader, parseDate, parseNumber, splitRecords } from './committedDemandCsv'
import type { SupplyContractReleaseInput } from './types'

export interface ScheduleProblem { row: number; reasonKey: string }
export interface ParsedSchedule {
  rows: SupplyContractReleaseInput[]
  problems: ScheduleProblem[]
  /** i18n key when the text as a whole cannot be used. */
  fatal?: string
}

const ALIASES: Record<'sku' | 'date' | 'quantity', string[]> = {
  sku: ['sku', 'producto', 'product', 'codigo', 'item', 'articulo'],
  date: ['date', 'fecha', 'delivery_date', 'fecha_entrega', 'entrega', 'release_date'],
  quantity: ['quantity', 'qty', 'cantidad', 'unidades', 'units'],
}

export function parseReleaseTable(text: string): ParsedSchedule {
  const records = splitRecords(text)
  if (records.length === 0) return { rows: [], problems: [], fatal: 'contracts.paste_empty' }

  const header = records[0].map(normHeader)
  const named: Partial<Record<'sku' | 'date' | 'quantity', number>> = {}
  for (const field of Object.keys(ALIASES) as ('sku' | 'date' | 'quantity')[]) {
    const idx = header.findIndex(h => ALIASES[field].includes(h))
    if (idx >= 0) named[field] = idx
  }
  let body = records
  let col: Partial<Record<'sku' | 'date' | 'quantity', number>>
  if (named.date !== undefined) {
    col = named
    body = records.slice(1)
  } else {
    // No header: the width says which shape it is.
    const width = Math.max(...records.map(r => r.length))
    if (width === 2) col = { date: 0, quantity: 1 }
    else if (width >= 3) col = { sku: 0, date: 1, quantity: 2 }
    else return { rows: [], problems: [], fatal: 'contracts.paste_columns' }
  }

  const get = (r: string[], f: 'sku' | 'date' | 'quantity') =>
    (col[f] === undefined ? '' : (r[col[f] as number] ?? '').trim())
  const rows: SupplyContractReleaseInput[] = []
  const problems: ScheduleProblem[] = []
  body.forEach((r, i) => {
    const row = i + 1
    const date = parseDate(get(r, 'date'))
    if (!date) { problems.push({ row, reasonKey: 'contracts.paste_bad_date' }); return }
    const rawQty = get(r, 'quantity')
    if (rawQty === '') { problems.push({ row, reasonKey: 'contracts.paste_missing_quantity' }); return }
    const quantity = parseNumber(rawQty)
    if (quantity === null || quantity <= 0) { problems.push({ row, reasonKey: 'contracts.paste_bad_quantity' }); return }
    const sku = get(r, 'sku')
    rows.push({ sku: sku || null, date, quantity })
  })
  if (rows.length === 0 && problems.length === 0) return { rows, problems, fatal: 'contracts.paste_empty' }
  return { rows, problems }
}

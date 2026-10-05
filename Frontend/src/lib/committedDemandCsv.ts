/**
 * Client-side CSV parsing for committed-demand import. Pure: no React, no
 * translation. Problems come back as i18n keys so the caller translates them.
 *
 * Columns (header aliases in Spanish and English): sku, delivery_date,
 * quantity, customer, probability, warehouse_id, note. Probability may be
 * 0-1 (0.8) or 1-100 with an optional percent sign (80 or 80%); empty means
 * 100%. The server remains the authority: it re-validates every row.
 */
import type { CommittedDemandInput } from './types'

export interface CsvProblem { row: number; reasonKey: string }
export interface ParsedCommittedCsv {
  rows: CommittedDemandInput[]
  problems: CsvProblem[]
  /** i18n key when the file as a whole cannot be used. */
  fatal?: string
}

const ALIASES: Record<string, string[]> = {
  sku: ['sku', 'producto', 'product', 'codigo', 'item', 'articulo'],
  delivery_date: ['delivery_date', 'deliverydate', 'date', 'fecha', 'fecha_entrega', 'fechaentrega', 'fecha_de_entrega', 'entrega'],
  quantity: ['quantity', 'qty', 'cantidad', 'unidades', 'units'],
  customer: ['customer', 'cliente'],
  probability: ['probability', 'probabilidad', 'prob'],
  warehouse_id: ['warehouse_id', 'warehouse', 'bodega', 'almacen', 'deposito'],
  note: ['note', 'notes', 'nota', 'notas', 'comentario', 'comment'],
}

export function normHeader(h: string): string {
  return h.replace(/^﻿/, '').trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[\s-]+/g, '_')
}

/** Splits CSV text into records, honouring quotes; delimiter is , ; or tab. */
export function splitRecords(text: string): string[][] {
  const firstLine = text.replace(/^﻿/, '').split(/\r?\n/, 1)[0] ?? ''
  const count = (ch: string) => firstLine.split(ch).length - 1
  const delim = [',', ';', '\t'].reduce((best, d) => (count(d) > count(best) ? d : best), ',')
  const out: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  const s = text.replace(/^﻿/, '')
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (quoted) {
      if (ch === '"') {
        if (s[i + 1] === '"') { cell += '"'; i++ } else quoted = false
      } else cell += ch
    } else if (ch === '"') quoted = true
    else if (ch === delim) { row.push(cell); cell = '' }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && s[i + 1] === '\n') i++
      row.push(cell); cell = ''
      out.push(row); row = []
    } else cell += ch
  }
  if (cell !== '' || row.length) { row.push(cell); out.push(row) }
  return out.filter(r => r.some(c => c.trim() !== ''))
}

export function parseDate(raw: string): string | null {
  const v = raw.trim()
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(v)
  let y: number, mo: number, d: number
  if (m) { y = +m[1]; mo = +m[2]; d = +m[3] }
  else if ((m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(v))) { d = +m[1]; mo = +m[2]; y = +m[3] }
  else return null
  const dt = new Date(Date.UTC(y, mo - 1, d))
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null
  return dt.toISOString().slice(0, 10)
}

export function parseNumber(raw: string): number | null {
  let v = raw.trim().replace(/\s/g, '')
  if (v === '') return null
  // "1.234,5" (comma decimal) vs "1,234.5": the last separator is the decimal one.
  if (v.includes(',') && v.includes('.')) {
    v = v.lastIndexOf(',') > v.lastIndexOf('.') ? v.replace(/\./g, '').replace(',', '.') : v.replace(/,/g, '')
  } else if (v.includes(',')) v = v.replace(',', '.')
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** 0-1 stays as is; 1-100 (optionally with %) becomes 0-1; empty is 100%. */
function parseProbability(raw: string): number | null {
  const v = raw.trim()
  if (v === '') return 1
  const pct = v.endsWith('%')
  const n = parseNumber(pct ? v.slice(0, -1) : v)
  if (n === null || n <= 0) return null
  if (pct) return n <= 100 ? n / 100 : null
  if (n <= 1) return n
  return n <= 100 ? n / 100 : null
}

export function parseCommittedCsv(text: string): ParsedCommittedCsv {
  const records = splitRecords(text)
  if (records.length === 0) return { rows: [], problems: [], fatal: 'committed.csv_empty' }
  const header = records[0].map(normHeader)
  const col: Record<string, number> = {}
  for (const [field, names] of Object.entries(ALIASES)) {
    const idx = header.findIndex(h => names.includes(h))
    if (idx >= 0) col[field] = idx
  }
  if (col.sku === undefined || col.delivery_date === undefined || col.quantity === undefined) {
    return { rows: [], problems: [], fatal: 'committed.csv_missing_columns' }
  }
  const get = (r: string[], f: string) => (col[f] === undefined ? '' : (r[col[f]] ?? '').trim())
  const rows: CommittedDemandInput[] = []
  const problems: CsvProblem[] = []
  records.slice(1).forEach((r, i) => {
    const row = i + 1
    const sku = get(r, 'sku')
    if (!sku) { problems.push({ row, reasonKey: 'committed.csv_bad_sku' }); return }
    const date = parseDate(get(r, 'delivery_date'))
    if (!date) { problems.push({ row, reasonKey: 'committed.csv_bad_date' }); return }
    const quantity = parseNumber(get(r, 'quantity'))
    if (quantity === null || quantity <= 0) { problems.push({ row, reasonKey: 'committed.csv_bad_quantity' }); return }
    const probability = parseProbability(get(r, 'probability'))
    if (probability === null) { problems.push({ row, reasonKey: 'committed.csv_bad_probability' }); return }
    rows.push({
      sku, delivery_date: date, quantity, probability,
      customer: get(r, 'customer') || null,
      warehouse_id: get(r, 'warehouse_id') || null,
      note: get(r, 'note') || null,
    })
  })
  if (rows.length === 0 && problems.length === 0) return { rows, problems, fatal: 'committed.csv_empty' }
  return { rows, problems }
}

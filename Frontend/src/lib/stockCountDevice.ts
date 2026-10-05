/**
 * Everything the stock-count screen needs from the device, kept out of the
 * component so each piece can be reasoned about on its own:
 *
 *  - the OFFLINE QUEUE: scans are written here first and sent when there is a
 *    connection. A scan is never dropped: it leaves the queue only when the
 *    server accepted it, and a scan the server REFUSED stays visible (flagged)
 *    until the person dismisses it.
 *  - a LOCAL CATALOGUE of the SKUs, so a scan resolves instantly and also
 *    works with no connection.
 *  - camera / BarcodeDetector support, and the beep + vibration feedback.
 *
 * Browser storage can be missing or full (private window, quota): every access
 * is wrapped, and the queue then lives in memory for the session — the screen
 * still works, it just cannot survive a reload, and says so (`persistent`).
 */

import type { InventoryStock } from '@/lib/types'

// ── Offline queue ────────────────────────────────────────────────────────────

export interface QueuedScan {
  ref:      string               // idempotency key the server de-duplicates on
  sku:      string
  quantity: number
  mode:     'add' | 'set'
  source:   'scan' | 'manual'
  at:       number
  /** Set when the server refused this scan; it is kept so nothing is lost. */
  error?:   { code: string; params: Record<string, unknown> }
}

const QUEUE_PREFIX = 'stockai_count_queue_'
const memoryQueues = new Map<string, QueuedScan[]>()

export function newRef(): string {
  try {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  } catch { /* fall through */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export function loadQueue(countId: string): QueuedScan[] {
  try {
    const raw = localStorage.getItem(QUEUE_PREFIX + countId)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed as QueuedScan[]
    }
  } catch { /* use memory */ }
  return memoryQueues.get(countId) ?? []
}

/** Returns false when the queue could only be kept in memory. */
export function saveQueue(countId: string, queue: QueuedScan[]): boolean {
  memoryQueues.set(countId, queue)
  try {
    if (queue.length === 0) localStorage.removeItem(QUEUE_PREFIX + countId)
    else localStorage.setItem(QUEUE_PREFIX + countId, JSON.stringify(queue))
    return true
  } catch {
    return false
  }
}

/** The count total of one SKU once every queued scan is applied on top of the
 *  last total the server confirmed. Pure, so the screen and the tests agree. */
export function projectedQty(serverQty: number, sku: string, queue: QueuedScan[]): number {
  let qty = serverQty
  for (const q of queue) {
    if (q.sku !== sku || q.error) continue
    qty = q.mode === 'add' ? qty + q.quantity : q.quantity
  }
  return qty
}

// ── Local catalogue ──────────────────────────────────────────────────────────

export interface CatalogueItem {
  sku:             string
  display_name:    string | null
  barcode:         string | null
  unit_of_measure: string | null
  unit_cost:       number | null
  /** System quantity per warehouse, as of when the catalogue was fetched. */
  qty:             Record<string, number>
}

const CATALOGUE_KEY = 'stockai_count_catalogue'

export function buildCatalogue(rows: InventoryStock[]): CatalogueItem[] {
  const bySku = new Map<string, CatalogueItem>()
  for (const r of rows) {
    const item = bySku.get(r.sku) ?? {
      sku: r.sku, display_name: r.display_name, barcode: r.barcode ?? null,
      unit_of_measure: r.unit_of_measure ?? null, unit_cost: r.unit_cost, qty: {},
    }
    item.qty[r.warehouse || 'principal'] = r.current_stock
    if (item.unit_cost == null && r.unit_cost != null) item.unit_cost = r.unit_cost
    if (!item.barcode && r.barcode) item.barcode = r.barcode
    bySku.set(r.sku, item)
  }
  return Array.from(bySku.values())
}

export function saveCatalogue(items: CatalogueItem[]): void {
  try { localStorage.setItem(CATALOGUE_KEY, JSON.stringify(items)) } catch { /* memory only */ }
}

export function loadCatalogue(): CatalogueItem[] {
  try {
    const raw = localStorage.getItem(CATALOGUE_KEY)
    if (raw) return JSON.parse(raw) as CatalogueItem[]
  } catch { /* none */ }
  return []
}

export type LocalMatch =
  | { kind: 'found'; item: CatalogueItem; matchedBy: 'barcode' | 'sku' }
  | { kind: 'ambiguous'; skus: string[] }
  | { kind: 'none' }

/** Same order as the server: barcode, then SKU; exact before case-insensitive.
 *  Two SKUs behind one code is reported, never guessed. */
export function matchLocal(items: CatalogueItem[], code: string): LocalMatch {
  const c = code.trim()
  if (!c) return { kind: 'none' }
  const lc = c.toLowerCase()
  const tiers: Array<[CatalogueItem[], 'barcode' | 'sku']> = [
    [items.filter(i => i.barcode === c), 'barcode'],
    [items.filter(i => i.sku === c), 'sku'],
    [items.filter(i => (i.barcode ?? '').toLowerCase() === lc), 'barcode'],
    [items.filter(i => i.sku.toLowerCase() === lc), 'sku'],
  ]
  for (const [hits, matchedBy] of tiers) {
    if (hits.length === 1) return { kind: 'found', item: hits[0], matchedBy }
    if (hits.length > 1) return { kind: 'ambiguous', skus: hits.map(h => h.sku) }
  }
  return { kind: 'none' }
}

// ── Camera ───────────────────────────────────────────────────────────────────

export const BARCODE_FORMATS = [
  'ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code',
]

/** The camera path needs BOTH a camera API and the barcode decoder. Without
 *  either, the screen offers the keyboard-wedge / manual path instead. */
export function cameraScanSupported(): boolean {
  if (typeof window === 'undefined') return false
  return 'BarcodeDetector' in window
    && !!navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
}

// ── Feedback ─────────────────────────────────────────────────────────────────

let audio: AudioContext | null = null

function tone(freq: number, ms: number, startAt = 0): void {
  try {
    const Ctor = (window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
    if (!Ctor) return
    audio = audio ?? new Ctor()
    const t0 = audio.currentTime + startAt
    const osc = audio.createOscillator()
    const gain = audio.createGain()
    osc.frequency.value = freq
    gain.gain.setValueAtTime(0.0001, t0)
    gain.gain.exponentialRampToValueAtTime(0.18, t0 + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + ms / 1000)
    osc.connect(gain).connect(audio.destination)
    osc.start(t0)
    osc.stop(t0 + ms / 1000 + 0.02)
  } catch { /* feedback is a courtesy, never a failure */ }
}

function vibrate(pattern: number | number[]): void {
  try { navigator.vibrate?.(pattern) } catch { /* unsupported */ }
}

export function feedbackOk(): void { tone(880, 90); vibrate(35) }
export function feedbackError(): void { tone(220, 120); tone(180, 160, 0.16); vibrate([60, 40, 60]) }

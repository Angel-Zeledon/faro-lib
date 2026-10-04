import type { IncomingSource } from './types'
import { fmtNum } from './numberLocale'

type Translate = (key: string, params?: Record<string, unknown>) => string

// `t` echoes an unmapped key; fall back to English rather than print the key.
function tOr(t: Translate, key: string, fallback: string, params?: Record<string, unknown>): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

/**
 * "426 already on the way (OC-000001, OC-000002)".
 *
 * A recommended quantity is NET of open purchase orders and transfers
 * (backend `service.get_incoming_detail`). Without naming them, a drop to 0
 * reads as the app forgetting; with them the buyer sees which order already
 * covers the units. Shared by /compras and /inventario so both say the same.
 */
export function incomingText(
  t: Translate, qty: number | undefined | null, sources: IncomingSource[] | undefined | null,
): string | null {
  if (!qty || qty <= 0) return null
  const refs = (sources ?? []).map(s => s.kind === 'transfer'
    ? tOr(t, 'hoy.incoming_transfer_from', `transfer from ${s.reference}`, { warehouse: s.reference })
    : s.reference)
  const qtyText = fmtNum(Math.round(qty))
  if (refs.length === 0) {
    return tOr(t, 'inventory.incoming_on_the_way', `${qtyText} already on the way`, { qty: qtyText })
  }
  const refsText = refs.join(', ')
  return tOr(t, 'hoy.incoming_on_the_way_refs', `${qtyText} already on the way (${refsText})`,
    { qty: qtyText, refs: refsText })
}

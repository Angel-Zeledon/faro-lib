// The supplier lead-time deviation sentence, in the reader's language.
//
// `supplier_health_service` used to build it in Spanish and ship it as
// `mensaje`, so /proveedores/scorecard read "Acme está tardando 12 días, no 7"
// with every other word on the page in English. It now sends `message_code` +
// `message_params` and an English `message` as the fallback — the same shape as
// `explanationCopy.ts`.

import type { SupplierLeadTimeAlert } from './types'

type Translate = (k: string, params?: Record<string, unknown>) => string

export function renderSupplierAlert(t: Translate, alert: SupplierLeadTimeAlert): string {
  const code = alert.message_code
  if (!code) return alert.message ?? ''
  const key = `scorecard.${code}`
  const text = t(key, alert.message_params ?? {})
  // `t` echoes an unmapped key back; the backend's English sentence beats
  // printing "scorecard.supplier_taking_longer" at a buyer.
  return text === key ? (alert.message ?? '') : text
}

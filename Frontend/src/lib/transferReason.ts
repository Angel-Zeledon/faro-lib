// Renders the backend's structured transfer-vs-buy verdict (PENDIENTES #2).
//
// The backend never ships a sentence — it ships {reason_code, params}, and the
// Spanish/English copy lives in `transfers.reason_<reason_code>` in the i18n
// catalog. This is the single place that turns one into the other, so both the
// /hoy cards and the per-warehouse table read identically.
import type { TransferReason } from './types'

type Translate = (key: string) => string

export function transferReasonText(
  reason: TransferReason | null | undefined, t: Translate,
): string | null {
  if (!reason?.reason_code) return null
  const template = t(`transfers.reason_${reason.reason_code}`)
  // An unmapped key comes back as the key itself — show nothing rather than
  // printing "transfers.reason_x" at the user.
  if (!template || template.startsWith('transfers.reason_')) return null

  // The day count needs an agreeing noun ("llega en 1 día" vs "en 3 días"),
  // which only the locale can decide — so the unit travels as its own
  // placeholder instead of being baked into the sentence.
  const days = Number(reason.params?.lane_days)
  const params: Record<string, unknown> = {
    ...reason.params,
    lane_days_unit: t(
      Math.round(days) === 1 ? 'hoy.reason_day_unit_singular' : 'hoy.reason_days_unit',
    ),
  }
  const text = Object.entries(params).reduce(
    (acc, [key, value]) =>
      value == null
        ? acc
        : acc.replace(new RegExp(`\\{${key}\\}`, 'g'), String(value)),
    template,
  )

  // An unconfigured pair resolves to transfer_lane_service's fallback — 1 day,
  // zero cost, which its own comment calls "deliberately optimistic". Those are
  // the numbers the sentence above just quoted, and the optimistic default is
  // exactly the one that wins a transfer-vs-buy comparison. Saying so is the
  // difference between a measurement and an assumption.
  if (reason.params?.lane_is_default) {
    const note = t('transfers.lane_is_default_note')
    if (note && note !== 'transfers.lane_is_default_note') return `${text} ${note}`
  }
  return text
}

/**
 * Shape of `GET /alerts` — the history of what the 08:00 UTC loop actually sent.
 *
 * Everything here is a machine value: `kind`, `status`, `channel` and
 * `failure_reason` are stable English enums and `details` holds bare numbers,
 * because the Spanish sentence is built by the i18n layer (`alerts.*`), never
 * by the backend. Same rule as the AppError envelope.
 */

/** What the alert was about. One key per `alerts.kind.*` i18n string. */
export type AlertKind =
  | 'stockout_digest'     // daily PEDIR_YA / PEDIR_PRONTO digest
  | 'supplier_lead_time'  // a supplier drifting off its historical lead time
  | 'data_freshness'      // the tenant stopped uploading; the numbers are aging
  | 'monthly_roi'         // the month's recap

/**
 * Delivery outcome of the whole fan-out. Three-way, not a boolean: one alert
 * goes to every admin over up to two channels, so "reached two of them and
 * failed for the third" is a real state and rounding it to 'delivered' is the
 * silence this screen exists to break.
 */
export type AlertStatus = 'delivered' | 'failed' | 'partial'

export type AlertChannel = 'email' | 'whatsapp' | 'mixed'

/** Why a send failed, as the transport reported it. Fixed by different people:
 *  `not_configured` is an operator setting credentials, `transport_error` is
 *  the provider rejecting us. */
export type AlertFailureReason = 'not_configured' | 'transport_error' | string

export interface AlertEntry {
  id:              string
  kind:            AlertKind
  /** ISO-8601 UTC. */
  created_at:      string
  channel:         AlertChannel
  status:          AlertStatus
  delivered_count: number
  failed_count:    number
  failure_reason:  AlertFailureReason | null
  /** Kind-specific numbers, interpolated into `alerts.body.*`. */
  details:         Record<string, number | string>
  /** Newer than this user's last "opened the bell" marker. */
  unread:          boolean
}

export interface AlertHistory {
  items:        AlertEntry[]
  unread_count: number
  last_read_at: string | null
  limit:        number
}

export interface MarkAlertsReadResult {
  last_read_at: string | null
  unread_count: number
}

/**
 * An in-session notice the TopBar produces itself (a training run finishing or
 * failing while the tab is open). It has no server record and does not survive
 * a reload — which is exactly why it is kept separate from `AlertEntry` in the
 * same panel instead of being pretended into one.
 */
export interface LocalNotice {
  id:    string
  title: string
  body:  string
  type:  'success' | 'error' | 'info'
  time:  Date
  read:  boolean
}

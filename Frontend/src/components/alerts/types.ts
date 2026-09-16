/**
 * Shape of `GET /alerts` — the history of what the 08:00 UTC loop actually sent.
 *
 * Everything here is a machine value: `kind`, `status`, `channel` and
 * `failure_reason` are stable English enums and `details` holds bare numbers,
 * because the Spanish sentence is built by the i18n layer (`alerts.*`), never
 * by the backend. Same rule as the AppError envelope.
 */

/** What the entry was about. One key per `events.kind.*` i18n string.
 *
 *  The first four are DELIVERIES — something Faro mailed on a schedule. The
 *  rest are system events: things the product did, declared in
 *  `backend/activity/events.py`. They share one feed because the user is
 *  asking one question ("what happened?"), and `source` is what tells them
 *  apart. */
export type AlertKind =
  | 'stockout_digest'     // daily PEDIR_YA / PEDIR_PRONTO digest
  | 'supplier_lead_time'  // a supplier drifting off its historical lead time
  | 'data_freshness'      // the tenant stopped uploading; the numbers are aging
  | 'monthly_roi'         // the month's recap
  | 'training'            // a forecast run finished, failed or never started
  | 'integration'         // an ERP sync
  | 'purchase'            // an order generated, sent, or sent to nobody
  | 'data'                // stock the tenant imported, shrank or moved
  | 'limit'               // a plan ceiling stopped a write
  | 'account'             // users, roles and machine credentials

/** Where the entry came from. A delivery is a fan-out with recipients; a
 *  system event happened once and carries its own severity and reason. */
export type AlertSource = 'delivery' | 'system'

/** How much of the user's attention this deserves. Only `critical` and
 *  `warning` reach the bell; `info` is history, and lives on /actividad. */
export type AlertSeverity = 'critical' | 'warning' | 'info'

/**
 * Delivery outcome of the whole fan-out. Three-way, not a boolean: one alert
 * goes to every admin over up to two channels, so "reached two of them and
 * failed for the third" is a real state and rounding it to 'delivered' is the
 * silence this screen exists to break.
 */
export type AlertStatus = 'delivered' | 'failed' | 'partial' | 'recorded'

export type AlertChannel = 'email' | 'whatsapp' | 'mixed'

/** Why a send failed, as the transport reported it. Fixed by different people:
 *  `not_configured` is an operator setting credentials, `transport_error` is
 *  the provider rejecting us. */
export type AlertFailureReason = 'not_configured' | 'transport_error' | string

export interface AlertEntry {
  id:              string
  kind:            AlertKind
  source:          AlertSource
  /** The declared event name (`training.failed`), null on a delivery row.
   *  Renders through `events.action.<action>`. */
  action:          string | null
  severity:        AlertSeverity
  /** WHY, as a code — never prose. Rendered through `events.reason.<reason>`
   *  with `reason_params` interpolated, the same contract as AppError. On a
   *  delivery row it carries the transport's failure reason. */
  reason:          string | null
  reason_params:   Record<string, number | string>
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

/** `GET /alerts/activity` — the full history, `info` included, paged. */
export interface ActivityFeed {
  items:  AlertEntry[]
  total:  number
  limit:  number
  offset: number
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

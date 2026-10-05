'use client'
/**
 * The TopBar bell.
 *
 * Two sources, kept visibly distinct because they are not the same kind of
 * thing:
 *
 *  1. **Alerts StockAI sent** (`GET /alerts`) — the daily stockout digest, the
 *     supplier lead-time warning, the data-freshness reminder, the monthly
 *     recap. Durable, server-side, survives a reload. Before this existed the
 *     email was the only copy: delete it and the information was gone.
 *  2. **This session's notices** — a training run finishing or failing while
 *     the tab is open. In-memory, gone on reload. Passed in by the TopBar,
 *     which is what produces them.
 *
 * A failed delivery is rendered, never dropped. "No alerts" must not be
 * readable as "nothing went wrong" — the same reason `send_*` returns a bool
 * the loop writes to activity_logs instead of swallowing the failure.
 *
 * Unread comes from the server (`unread`, per entry, derived from this user's
 * last "opened the bell" marker) plus the local notices' own flag. Nothing is
 * invented: with no marker the server reports every alert unread, which is
 * true.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import {
  AlertTriangle, Bell, CalendarClock, CheckCircle2, Clock, Database, Gauge,
  KeyRound, LineChart, PackageX, RefreshCw, ShoppingCart, TrendingUp, Truck, X,
} from 'lucide-react'

import { getAlertHistory, markAlertsRead } from '@/lib/api'
import { useAttention } from '@/hooks/useAttention'
import { formatMoney } from '@/lib/currency'
import { useLanguage } from '@/contexts/LanguageContext'
import type { AlertEntry, AlertKind, AlertSeverity, AlertStatus, LocalNotice } from './types'

const POLL_MS = 60_000
const HISTORY_LIMIT = 20

const KIND_ICON: Record<AlertKind, typeof PackageX> = {
  stockout_digest:    PackageX,
  supplier_lead_time: Truck,
  data_freshness:     CalendarClock,
  monthly_roi:        TrendingUp,
  training:           LineChart,
  integration:        RefreshCw,
  purchase:           ShoppingCart,
  data:               Database,
  limit:              Gauge,
  account:            KeyRound,
}

/** A system event was sent to nobody, so the delivery colours do not apply:
 *  what it costs the user is its severity. */
const SEVERITY_COLOR: Record<AlertSeverity, string> = {
  critical: '#C0504D',
  warning:  '#B7791F',
  info:     'var(--accent)',
}

/** Identifiers the feed stores so an entry can be traced, and that mean
 *  nothing to a person reading it. Kept out of the chips rather than out of
 *  the payload — /actividad may want to link a session one day. */
const OPAQUE_DETAILS = new Set(['session_id', 'started_by', 'attempted_by'])

/** Details that are MONEY. Rendered through the tenant's currency like every
 *  other amount in the product: a bare `150` beside `Líneas: 1` reads as a
 *  count, and this one is what the order is worth. */
const MONEY_DETAILS = new Set(['value'])

/** Colour follows the DELIVERY outcome, not the topic: a digest nobody
 *  received is a different event from one that arrived. */
const STATUS_COLOR: Record<AlertStatus, string> = {
  delivered: 'var(--muted)',
  partial:   '#B7791F',
  failed:    '#C0504D',
  // A system event was sent to nobody, so it has no delivery colour — its
  // severity decides. Present so the map stays total: an entry the table
  // cannot answer for used to render `undefined` as a colour.
  recorded:  'var(--muted)',
}

/** Relative age. Absolute timestamps ("07:00") are useless on a list whose
 *  entries are days apart, which is the normal spacing of a daily loop. */
function useRelativeTime() {
  const { t } = useLanguage()
  return useCallback((iso: string): string => {
    const then = new Date(iso).getTime()
    if (Number.isNaN(then)) return ''
    const mins = Math.max(0, Math.round((Date.now() - then) / 60_000))
    if (mins < 2)      return t('alerts.time.just_now')
    if (mins < 60)     return t('alerts.time.minutes', { n: mins })
    if (mins < 60 * 24) return t('alerts.time.hours', { n: Math.round(mins / 60) })
    return t('alerts.time.days', { n: Math.round(mins / (60 * 24)) })
  }, [t])
}

/**
 * The one-line summary of what the alert was about, built from the numbers the
 * loop recorded. `data_freshness` has three variants on purpose: the reminder
 * only names the clock that is actually late, so a body claiming "your sales
 * are 4 days old" on a stock-triggered reminder would name a healthy figure as
 * a problem.
 */
function useAlertBody() {
  const { t } = useLanguage()
  return useCallback((a: AlertEntry): string => {
    const d = a.details ?? {}
    if (a.kind === 'data_freshness') {
      const sales = d.sales_age_days
      const stock = d.stock_age_days
      if (sales != null && stock != null) return t('alerts.body.data_freshness_both', { sales, stock })
      if (sales != null) return t('alerts.body.data_freshness_sales', { sales })
      if (stock != null) return t('alerts.body.data_freshness_stock', { stock })
      return ''
    }
    return t(`alerts.body.${a.kind}`, d)
  }, [t])
}

/**
 * The numbers an event carries, as `Label: value` chips.
 *
 * Built from the keys the backend whitelisted for that action, each rendered
 * through `events.detail.<key>` — so a new field cannot reach the screen as a
 * bare identifier (this product printed `inventory.source_file` at buyers
 * once), and a number is never baked into a sentence a translator cannot
 * reorder.
 */
export function useEventDetails() {
  const { t } = useLanguage()
  return useCallback((a: AlertEntry): string => {
    const d = a.details ?? {}
    return Object.keys(d)
      .filter(k => !OPAQUE_DETAILS.has(k) && d[k] !== null && d[k] !== '')
      .map(k => {
        const v = d[k]
        const shown = MONEY_DETAILS.has(k) && typeof v === 'number' ? formatMoney(v) : v
        return `${t(`events.detail.${k}`)}: ${shown}`
      })
      .join('  ·  ')
  }, [t])
}

/** WHY it happened. A warning with no reason is worse than silence, so the
 *  backend refuses to record one — here it is simply rendered. */
export function useEventReason() {
  const { t } = useLanguage()
  return useCallback((a: AlertEntry): string => {
    if (!a.reason) return ''
    if (a.source === 'system') return t(`events.reason.${a.reason}`, a.reason_params ?? {})
    // A delivery row's reason is the transport's, and it already has copy of
    // its own from before the event vocabulary existed.
    return t(`alerts.delivery.reason_${a.reason}`)
  }, [t])
}

/** Title. System events name what happened; deliveries name their topic. */
export function useEventTitle() {
  const { t } = useLanguage()
  return useCallback((a: AlertEntry): string => (
    a.source === 'system' && a.action
      ? t(`events.action.${a.action}`, a.details ?? {})
      : t(`alerts.kind.${a.kind}`)
  ), [t])
}

/** The delivery line. Rendered only when something did NOT arrive — a
 *  successful send needs no explanation, a failed one must not be silent. */
function DeliveryNote({ alert }: { alert: AlertEntry }) {
  const { t } = useLanguage()
  if (alert.status === 'delivered') return null

  const channel = t(`alerts.channel.${alert.channel}`)
  const headline = alert.status === 'failed'
    ? t('alerts.delivery.failed', { channel })
    : t('alerts.delivery.partial', {
        delivered: alert.delivered_count, failed: alert.failed_count,
      })
  const reason = alert.failure_reason
    ? t(`alerts.delivery.reason_${alert.failure_reason}`)
    : ''

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 5, marginTop: 4,
      fontSize: 11, color: STATUS_COLOR[alert.status], lineHeight: 1.4,
    }}>
      <AlertTriangle size={11} style={{ marginTop: 2, flexShrink: 0 }} />
      <span>{reason ? `${headline} — ${reason}` : headline}</span>
    </div>
  )
}

/**
 * One entry, whichever source it came from.
 *
 * A delivery keeps the body it always had (a sentence built from the digest's
 * own numbers) and its three-way delivery note. A system event has no
 * recipients, so it renders what it carries instead: the declared title, the
 * numbers as chips, and the reason it happened — which is the half the product
 * used to keep to itself.
 */
export function AlertRow({ alert, dense = false }: { alert: AlertEntry; dense?: boolean }) {
  const relative = useRelativeTime()
  const body = useAlertBody()
  const details = useEventDetails()
  const reasonOf = useEventReason()
  const title = useEventTitle()

  const system = alert.source === 'system'
  const Icon = KIND_ICON[alert.kind] ?? Bell
  const bad = system ? alert.severity !== 'info' : alert.status !== 'delivered'
  const color = system
    ? SEVERITY_COLOR[alert.severity] ?? 'var(--accent)'
    : (alert.status === 'delivered' ? 'var(--accent)' : STATUS_COLOR[alert.status])
  const reason = system ? reasonOf(alert) : ''
  const line = system ? details(alert) : body(alert)

  return (
    <div style={{
      padding: dense ? '12px 16px' : '10px 16px',
      borderBottom: '1px solid var(--border)',
      display: 'flex', gap: 10, alignItems: 'flex-start',
      background: bad ? (alert.severity === 'warning' && system
        ? 'rgba(183,121,31,0.04)' : 'rgba(192,80,77,0.04)')
        : alert.unread && !dense ? 'color-mix(in srgb, var(--accent) 5%, transparent)' : 'transparent',
    }}>
      <Icon size={14} color={color} style={{ marginTop: 2, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 8, justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: 12, fontWeight: alert.unread && !dense ? 700 : 600 }}>
            {title(alert)}
          </span>
          <span style={{ fontSize: 10, color: 'var(--dim)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            {relative(alert.created_at)}
          </span>
        </div>
        {line && (
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.45 }}>
            {line}
          </div>
        )}
        {reason && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: 5, marginTop: 4,
            fontSize: 11, color: alert.severity === 'info' ? 'var(--dim)' : color,
            lineHeight: 1.4,
          }}>
            {alert.severity !== 'info' && (
              <AlertTriangle size={11} style={{ marginTop: 2, flexShrink: 0 }} />
            )}
            <span>{reason}</span>
          </div>
        )}
        {!system && <DeliveryNote alert={alert} />}
      </div>
    </div>
  )
}

function LocalRow({ notice }: { notice: LocalNotice }) {
  return (
    <div style={{
      padding: '10px 16px', borderBottom: '1px solid var(--border)',
      display: 'flex', gap: 10, alignItems: 'flex-start',
      background: notice.type === 'success' ? 'rgba(46,139,98,0.04)'
        : notice.type === 'error' ? 'rgba(192,80,77,0.04)' : 'transparent',
    }}>
      {notice.type === 'success'
        ? <CheckCircle2 size={14} color="#2E8B62" style={{ marginTop: 1, flexShrink: 0 }} />
        : notice.type === 'error'
          ? <AlertTriangle size={14} color="#C0504D" style={{ marginTop: 1, flexShrink: 0 }} />
          : <Clock size={14} color="#B7791F" style={{ marginTop: 1, flexShrink: 0 }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{notice.title}</div>
        <div style={{
          fontSize: 11, color: 'var(--dim)', marginTop: 1,
          overflow: 'hidden', overflowWrap: 'anywhere',
        }}>
          {notice.body}
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>
          {notice.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
      </div>
    </div>
  )
}

/**
 * What is waiting on the buyer right now: late deliveries, suppliers that
 * could not be sent an order, suppliers running late. Derived from the live
 * lists (see hooks/useAttention), so an entry stays until its cause is fixed,
 * there is one per kind rather than one per order, and each carries the link
 * that resolves it. These used to be banners on the Panel.
 */
export function AttentionRows({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useLanguage()
  const { overdue, contactHealth, leadTimeAlerts } = useAttention()
  const contacts = contactHealth.filter(r => r.has_open_pos)
  const rows: { id: string; Icon: typeof PackageX; title: string; detail: string; href: string; cta: string }[] = []
  if (overdue.length > 0) {
    rows.push({
      id: 'overdue', Icon: Truck,
      title: t(overdue.length === 1 ? 'alerts.attention.overdue_one' : 'alerts.attention.overdue_other', { n: overdue.length }),
      detail: overdue.slice(0, 3).map(o => `${o.supplier} (${t('alerts.attention.days_late', { n: o.days_overdue })})`).join(' · '),
      href: '/pedidos', cta: t('alerts.attention.overdue_cta'),
    })
  }
  if (contacts.length > 0) {
    rows.push({
      id: 'contacts', Icon: PackageX,
      title: t(contacts.length === 1 ? 'alerts.attention.contacts_one' : 'alerts.attention.contacts_other', { n: contacts.length }),
      detail: contacts.slice(0, 3).map(r => r.supplier).join(' · '),
      href: `/proveedores?focus=${encodeURIComponent(contacts[0].supplier)}`, cta: t('alerts.attention.contacts_cta'),
    })
  }
  if (leadTimeAlerts.length > 0) {
    rows.push({
      id: 'lead_time', Icon: Clock,
      title: t(leadTimeAlerts.length === 1 ? 'alerts.attention.lead_time_one' : 'alerts.attention.lead_time_other', { n: leadTimeAlerts.length }),
      detail: leadTimeAlerts.slice(0, 3).map(a => `${a.supplier} (${a.lead_time_recent} / ${a.lead_time_historical} ${t('alerts.attention.days_abbrev')})`).join(' · '),
      href: '/proveedores/scorecard', cta: t('alerts.attention.lead_time_cta'),
    })
  }
  if (rows.length === 0) return null
  return (
    <>
      <SectionLabel>{t('alerts.section_attention')}</SectionLabel>
      {rows.map(r => (
        <div key={r.id} style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <r.Icon size={14} color="var(--muted)" style={{ marginTop: 2, flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>{r.title}</div>
            {r.detail && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, lineHeight: 1.45, overflowWrap: 'anywhere' }}>{r.detail}</div>}
            <Link href={r.href} onClick={onNavigate} style={{ display: 'inline-block', marginTop: 4, fontSize: 11.5, fontWeight: 600, color: 'var(--accent)', textDecoration: 'none' }}>
              {r.cta}
            </Link>
          </div>
        </div>
      ))}
    </>
  )
}

export function useAttentionTotal(): number {
  const { overdue, contactHealth, leadTimeAlerts } = useAttention()
  return (overdue.length > 0 ? 1 : 0) + (contactHealth.some(r => r.has_open_pos) ? 1 : 0) + (leadTimeAlerts.length > 0 ? 1 : 0)
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: '7px 16px', fontSize: 10, fontWeight: 700, letterSpacing: '0.04em',
      textTransform: 'uppercase', color: 'var(--dim)',
      background: 'var(--bg)', borderBottom: '1px solid var(--border)',
    }}>
      {children}
    </div>
  )
}

export interface AlertBellProps {
  /** In-session notices the TopBar produced. */
  localNotices:    LocalNotice[]
  /** Called when the panel opens — the TopBar marks its own notices read. */
  onLocalRead:     () => void
  /** Called by "clear all"; only the in-session list can be cleared, because
   *  the server-side history is a record and not a to-do list. */
  onClearLocal:    () => void
}

export default function AlertBell({ localNotices, onLocalRead, onClearLocal }: AlertBellProps) {
  const { t } = useLanguage()
  // A thumb, not a pointer: 44px on a phone (it was 28x28). Desktop unchanged.
  const narrow = useIsNarrow()
  const [open, setOpen] = useState(false)
  const [alerts, setAlerts] = useState<AlertEntry[]>([])
  const [serverUnread, setServerUnread] = useState(0)
  const [failedToLoad, setFailedToLoad] = useState(false)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    try {
      // `silent`: a bell polling in the background must not raise a toast when
      // the backend blinks. The panel says so itself instead.
      const data = await getAlertHistory(HISTORY_LIMIT, { silent: true })
      if (!mounted.current) return
      setAlerts(data.items)
      setServerUnread(data.unread_count)
      setFailedToLoad(false)
    } catch {
      if (mounted.current) setFailedToLoad(true)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    const id = setInterval(load, POLL_MS)
    return () => { mounted.current = false; clearInterval(id) }
  }, [load])

  const localUnread = localNotices.filter(n => !n.read).length
  // One per kind of pending thing (not per order): the badge counts what needs
  // doing, and drops by itself once it is done.
  const attentionTotal = useAttentionTotal()
  const unread = serverUnread + localUnread + attentionTotal

  async function openPanel() {
    setOpen(true)
    onLocalRead()
    if (serverUnread === 0) return
    try {
      // Optimistic: the badge clears on open even if the write is refused
      // (a viewer has no alerts to clear anyway), and the reload below is what
      // makes it true.
      setServerUnread(0)
      await markAlertsRead({ silent: true })
      await load()
    } catch {
      /* A viewer gets 403 here. Nothing to surface: they have no alerts. */
    }
  }

  const isEmpty = alerts.length === 0 && localNotices.length === 0 && attentionTotal === 0

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => open ? setOpen(false) : openPanel()}
        title={t('topbar.notifications')}
        aria-label={t('topbar.notifications')}
        style={{
          position: 'relative', padding: 6, borderRadius: narrow ? 10 : 7,
          background: 'transparent',
          border: narrow ? 'none' : `1px solid ${unread > 0 ? 'var(--accent)' : 'var(--border)'}`,
          cursor: 'pointer',
          color: unread > 0 ? 'var(--accent)' : 'var(--muted)',
          display: 'flex', alignItems: 'center',
          ...(narrow ? { width: 44, height: 44, justifyContent: 'center', boxSizing: 'border-box' } : {}),
          transition: 'all 0.15s',
        }}
      >
        <Bell size={narrow ? 20 : 14} />
        {unread > 0 && (
          <span style={{
            position: 'absolute', top: narrow ? 5 : -5, right: narrow ? 4 : -5,
            minWidth: 16, height: 16, borderRadius: 8,
            background: '#C0504D', color: '#fff',
            fontSize: 9, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '2px solid var(--surface)', padding: '0 3px',
          }}>
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 98 }} />
          {/* The short fade-and-drop is what says this panel hangs off the
              bell, rather than being a new surface that just appeared. */}
          <div className="popover-enter" style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0,
            // Never wider than the phone it opens on.
            width: 'min(340px, calc(100vw - 16px))', zIndex: 99,
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 10,
            boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: '12px 16px', borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{t('topbar.notifications')}</span>
              <button
                onClick={() => setOpen(false)}
                aria-label={t('common.close')}
                style={{
                  all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex',
                  ...(narrow ? { width: 44, height: 44, margin: '-12px -14px -12px 0', alignItems: 'center', justifyContent: 'center' } : {}),
                }}
              >
                <X size={narrow ? 18 : 13} />
              </button>
            </div>

            <div style={{ maxHeight: 420, overflowY: 'auto' }}>
              {failedToLoad && (
                <div style={{
                  padding: '8px 16px', fontSize: 11, color: '#B7791F',
                  borderBottom: '1px solid var(--border)',
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <AlertTriangle size={12} style={{ flexShrink: 0 }} />
                  {t('alerts.load_error')}
                </div>
              )}

              {isEmpty && !failedToLoad ? (
                <div style={{
                  padding: '28px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 12,
                }}>
                  <Bell size={24} style={{ margin: '0 auto 10px', opacity: 0.25, display: 'block' }} />
                  {t('topbar.no_notifications')}
                  <div style={{ fontSize: 11, marginTop: 4, opacity: 0.7 }}>
                    {t('alerts.empty_hint')}
                  </div>
                </div>
              ) : (
                <>
                  <AttentionRows onNavigate={() => setOpen(false)} />
                  {alerts.length > 0 && (
                    <>
                      {/* Not "what we sent" any more: the same list now carries
                          what the product DID, so the label says so. */}
                      <SectionLabel>{t('alerts.section_events')}</SectionLabel>
                      {alerts.map(a => <AlertRow key={a.id} alert={a} />)}
                    </>
                  )}
                  {localNotices.length > 0 && (
                    <>
                      <SectionLabel>{t('alerts.section_session')}</SectionLabel>
                      {localNotices.map(n => <LocalRow key={n.id} notice={n} />)}
                    </>
                  )}
                </>
              )}
            </div>

            {/* The bell shows only what needs a decision. Without this link the
                rest of the history — every successful import, sync and order —
                would be recorded and unreachable, which is the same as not
                being recorded at all. */}
            <div style={{
              padding: '8px 16px', borderTop: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <Link
                href="/actividad"
                onClick={() => setOpen(false)}
                style={{ fontSize: 11, color: 'var(--accent)', textDecoration: 'none' }}
              >
                {t('alerts.see_all')}
              </Link>
              {localNotices.length > 0 && (
                <button
                  onClick={onClearLocal}
                  style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)' }}
                >
                  {t('topbar.clear_all')}
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

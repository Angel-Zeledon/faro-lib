'use client'
/**
 * The TopBar bell.
 *
 * Two sources, kept visibly distinct because they are not the same kind of
 * thing:
 *
 *  1. **Alerts Faro sent** (`GET /alerts`) — the daily stockout digest, the
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
import {
  AlertTriangle, Bell, CalendarClock, CheckCircle2, Clock, PackageX,
  TrendingUp, Truck, X,
} from 'lucide-react'

import { getAlertHistory, markAlertsRead } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'
import type { AlertEntry, AlertKind, LocalNotice } from './types'

const POLL_MS = 60_000
const HISTORY_LIMIT = 20

const KIND_ICON: Record<AlertKind, typeof PackageX> = {
  stockout_digest:    PackageX,
  supplier_lead_time: Truck,
  data_freshness:     CalendarClock,
  monthly_roi:        TrendingUp,
}

/** Colour follows the DELIVERY outcome, not the topic: a digest nobody
 *  received is a different event from one that arrived. */
const STATUS_COLOR = {
  delivered: 'var(--muted)',
  partial:   '#f59e0b',
  failed:    '#ef4444',
} as const

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

function AlertRow({ alert }: { alert: AlertEntry }) {
  const relative = useRelativeTime()
  const body = useAlertBody()
  const { t } = useLanguage()
  const Icon = KIND_ICON[alert.kind] ?? Bell
  const failed = alert.status !== 'delivered'

  return (
    <div style={{
      padding: '10px 16px', borderBottom: '1px solid var(--border)',
      display: 'flex', gap: 10, alignItems: 'flex-start',
      background: failed ? 'rgba(239,68,68,0.04)'
        : alert.unread ? 'color-mix(in srgb, var(--accent) 5%, transparent)' : 'transparent',
    }}>
      <Icon
        size={14}
        color={failed ? STATUS_COLOR[alert.status] : 'var(--accent)'}
        style={{ marginTop: 2, flexShrink: 0 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', gap: 8, justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: 12, fontWeight: alert.unread ? 700 : 600 }}>
            {t(`alerts.kind.${alert.kind}`)}
          </span>
          <span style={{ fontSize: 10, color: 'var(--dim)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            {relative(alert.created_at)}
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
          {body(alert)}
        </div>
        <DeliveryNote alert={alert} />
      </div>
    </div>
  )
}

function LocalRow({ notice }: { notice: LocalNotice }) {
  return (
    <div style={{
      padding: '10px 16px', borderBottom: '1px solid var(--border)',
      display: 'flex', gap: 10, alignItems: 'flex-start',
      background: notice.type === 'success' ? 'rgba(34,197,94,0.04)'
        : notice.type === 'error' ? 'rgba(239,68,68,0.04)' : 'transparent',
    }}>
      {notice.type === 'success'
        ? <CheckCircle2 size={14} color="#22c55e" style={{ marginTop: 1, flexShrink: 0 }} />
        : notice.type === 'error'
          ? <AlertTriangle size={14} color="#ef4444" style={{ marginTop: 1, flexShrink: 0 }} />
          : <Clock size={14} color="#f59e0b" style={{ marginTop: 1, flexShrink: 0 }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{notice.title}</div>
        <div style={{
          fontSize: 11, color: 'var(--dim)', marginTop: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
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
  const unread = serverUnread + localUnread

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

  const isEmpty = alerts.length === 0 && localNotices.length === 0

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => open ? setOpen(false) : openPanel()}
        title={t('topbar.notifications')}
        aria-label={t('topbar.notifications')}
        style={{
          position: 'relative', padding: 6, borderRadius: 7,
          background: 'transparent',
          border: `1px solid ${unread > 0 ? 'var(--accent)' : 'var(--border)'}`,
          cursor: 'pointer',
          color: unread > 0 ? 'var(--accent)' : 'var(--muted)',
          display: 'flex', alignItems: 'center',
          transition: 'all 0.15s',
        }}
      >
        <Bell size={14} />
        {unread > 0 && (
          <span style={{
            position: 'absolute', top: -5, right: -5,
            minWidth: 16, height: 16, borderRadius: 8,
            background: '#ef4444', color: '#fff',
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
            width: 340, zIndex: 99,
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
                style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex' }}
              >
                <X size={13} />
              </button>
            </div>

            <div style={{ maxHeight: 420, overflowY: 'auto' }}>
              {failedToLoad && (
                <div style={{
                  padding: '8px 16px', fontSize: 11, color: '#f59e0b',
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
                  {alerts.length > 0 && (
                    <>
                      <SectionLabel>{t('alerts.section_sent')}</SectionLabel>
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

            {localNotices.length > 0 && (
              <div style={{
                padding: '8px 16px', borderTop: '1px solid var(--border)',
                display: 'flex', justifyContent: 'flex-end',
              }}>
                <button
                  onClick={onClearLocal}
                  style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)' }}
                >
                  {t('topbar.clear_all')}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

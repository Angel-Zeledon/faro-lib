'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { usePathname } from 'next/navigation'
import { Bell, CheckCircle2, AlertTriangle, Clock, X, ChevronRight, Search } from 'lucide-react'
import { getSessions } from '@/lib/api'
import type { SessionInfo } from '@/lib/types'
import { useToast } from '@/contexts/ToastContext'
import { useActiveSession } from '@/contexts/ActiveSessionContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { useSkuSearch } from '@/contexts/SkuSearchContext'
import Badge from '@/components/ui/Badge'
import PlanningControl from './PlanningControl'

interface Notif {
  id:    string
  title: string
  body:  string
  type:  'success' | 'error' | 'info'
  time:  Date
  read:  boolean
}

// Titles resolved via i18n so they follow the language toggle.
const PAGE_TITLE_KEYS: Record<string, string> = {
  '/data':      'topbar.title_data',
  '/analyst':   'topbar.title_analyst',
  '/config':    'topbar.title_config',
  '/users':     'topbar.title_users',
  '/skus':      'skus.page_title',
  '/pedidos':   'orders.page_title',
}

const STATUS_VARIANT: Record<string, 'success' | 'warning' | 'info' | 'danger' | 'muted'> = {
  COMPLETED:          'success',
  RUNNING:            'info',
  QUEUED:             'warning',
  FAILED:             'danger',
  CANCELLED:          'muted',
  MODELS_CONFIGURED:  'muted',
  FEATURES_CONFIGURED:'muted',
  COLUMNS_CONFIGURED: 'muted',
  INSPECTED:          'muted',
  DATASET_LOADED:     'muted',
  DRAFT:              'muted',
}

export default function TopBar() {
  const path    = usePathname()
  const { t }   = useLanguage()
  const title   = PAGE_TITLE_KEYS[path] ? t(PAGE_TITLE_KEYS[path]) : 'Faro'
  const { addToast } = useToast()
  const { activeSessionId } = useActiveSession()
  const { open: openSkuSearch } = useSkuSearch()

  const [time,        setTime]        = useState('')
  const [notifs,      setNotifs]      = useState<Notif[]>([])
  const [showPanel,   setShowPanel]   = useState(false)
  const [sessions,    setSessions]    = useState<SessionInfo[]>([])

  const prevStatus  = useRef<Map<string, string>>(new Map())
  const initDone    = useRef(false)
  const timeoutRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const tick = () => setTime(new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }))
    tick()
    const id = setInterval(tick, 10000)
    return () => clearInterval(id)
  }, [])

  const poll = useCallback(async (): Promise<boolean> => {
    try {
      const list: SessionInfo[] = await getSessions()
      setSessions(list)
      const fresh: Notif[] = []

      list.forEach(s => {
        const prev = prevStatus.current.get(s.session_id)
        prevStatus.current.set(s.session_id, s.status)
        if (!initDone.current || prev === s.status) return

        if (s.status === 'COMPLETED' && prev !== 'COMPLETED') {
          addToast(t('topbar.notif_complete_title'), `"${s.name}" ${t('topbar.notif_complete_body')}`, 'success')
          fresh.push({ id: `${s.session_id}-done-${Date.now()}`, title: t('topbar.notif_complete_title'), body: `"${s.name}" ${t('topbar.notif_complete_body')}`, type: 'success', time: new Date(), read: false })
        } else if (s.status === 'FAILED' && prev !== 'FAILED') {
          addToast(t('topbar.notif_failed_title'), `"${s.name}" ${t('topbar.notif_failed_body')}`, 'error')
          fresh.push({ id: `${s.session_id}-fail-${Date.now()}`, title: t('topbar.notif_failed_title'), body: `"${s.name}" ${t('topbar.notif_failed_body')}`, type: 'error', time: new Date(), read: false })
        }
      })

      initDone.current = true
      if (fresh.length) setNotifs(prev => [...fresh, ...prev].slice(0, 30))

      return list.some(s => s.status === 'RUNNING' || s.status === 'QUEUED')
    } catch {
      return false
    }
  }, [addToast, t])

  useEffect(() => {
    let cancelled = false
    async function schedule() {
      if (cancelled) return
      const active = await poll()
      if (cancelled) return
      timeoutRef.current = setTimeout(schedule, active ? 5000 : 30000)
    }
    schedule()
    return () => {
      cancelled = true
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [poll])

  const unread = notifs.filter(n => !n.read).length
  function openPanel() {
    setShowPanel(true)
    setNotifs(prev => prev.map(n => ({ ...n, read: true })))
  }

  // Active session lookup (used on /forecast page)
  const activeSession = activeSessionId
    ? sessions.find(s => s.session_id === activeSessionId) ?? null
    : null

  return (
    <header style={{
      height: 52,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 24px',
      borderBottom: '1px solid var(--border)',
      background: 'var(--surface)',
      flexShrink: 0, position: 'relative', zIndex: 20,
    }}>

      {/* Title + breadcrumb */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <h1 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.01em' }}>
          {title}
        </h1>
        {activeSession && (
          <>
            <ChevronRight size={13} color="var(--border-strong)" />
            <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 500, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {activeSession.name}
            </span>
            <Badge
              variant={STATUS_VARIANT[activeSession.status] ?? 'muted'}
              pulse={activeSession.status === 'RUNNING'}
              dot={activeSession.status !== 'RUNNING'}
              style={{ fontSize: 10 }}
            >
              {activeSession.status}
            </Badge>
          </>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* Active planning period + horizon (multi-period, Phase B) */}
        <PlanningControl />

        {/* Global SKU search (Ctrl/Cmd-K) */}
        <button
          onClick={openSkuSearch}
          title={t('topbar.sku_search_title')}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '5px 10px', borderRadius: 7,
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            cursor: 'pointer',
            color: 'var(--muted)',
            fontSize: 12,
            transition: 'all 0.15s',
          }}
        >
          <Search size={13} />
          <span>
            {t('topbar.sku_search_placeholder')}
          </span>
          <span style={{
            fontSize: 10, fontWeight: 600, color: 'var(--dim)',
            border: '1px solid var(--border)', borderRadius: 4, padding: '1px 5px',
          }}>
            Ctrl K
          </span>
        </button>

        <span style={{ fontSize: 12, color: 'var(--dim)', fontVariantNumeric: 'tabular-nums' }}>
          {time}
        </span>

        {/* Notification bell */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => showPanel ? setShowPanel(false) : openPanel()}
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

          {showPanel && (
            <>
              <div onClick={() => setShowPanel(false)} style={{ position: 'fixed', inset: 0, zIndex: 98 }} />
              <div style={{
                position: 'absolute', top: 'calc(100% + 8px)', right: 0,
                width: 300, zIndex: 99,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 10,
                boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
                overflow: 'hidden',
              }}>
                <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{t('topbar.notifications')}</span>
                  <button onClick={() => setShowPanel(false)} style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', display: 'flex' }}>
                    <X size={13} />
                  </button>
                </div>
                <div style={{ maxHeight: 380, overflowY: 'auto' }}>
                  {notifs.length === 0 ? (
                    <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: 12 }}>
                      <Bell size={24} style={{ margin: '0 auto 10px', opacity: 0.25, display: 'block' }} />
                      {t('topbar.no_notifications')}
                      <div style={{ fontSize: 11, marginTop: 4, opacity: 0.7 }}>{t('topbar.no_notifications_hint')}</div>
                    </div>
                  ) : notifs.map(n => (
                    <div key={n.id} style={{
                      padding: '10px 16px', borderBottom: '1px solid var(--border)',
                      display: 'flex', gap: 10, alignItems: 'flex-start',
                      background: n.type === 'success' ? 'rgba(34,197,94,0.04)' : n.type === 'error' ? 'rgba(239,68,68,0.04)' : 'transparent',
                    }}>
                      {n.type === 'success' ? <CheckCircle2 size={14} color="#22c55e" style={{ marginTop: 1, flexShrink: 0 }} />
                        : n.type === 'error' ? <AlertTriangle size={14} color="#ef4444" style={{ marginTop: 1, flexShrink: 0 }} />
                        : <Clock size={14} color="#f59e0b" style={{ marginTop: 1, flexShrink: 0 }} />}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600 }}>{n.title}</div>
                        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.body}</div>
                        <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>{n.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                      </div>
                    </div>
                  ))}
                </div>
                {notifs.length > 0 && (
                  <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
                    <button onClick={() => setNotifs([])} style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)' }}>
                      {t('topbar.clear_all')}
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}

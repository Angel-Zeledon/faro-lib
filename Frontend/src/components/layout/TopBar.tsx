'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { ChevronRight } from 'lucide-react'
import { getSessions } from '@/lib/api'
import type { SessionInfo } from '@/lib/types'
import AlertBell from '@/components/alerts/AlertBell'
import TourLauncher from '@/components/tour/TourLauncher'
import type { LocalNotice } from '@/components/alerts/types'
import { useToast } from '@/contexts/ToastContext'
import { usePlanning } from '@/contexts/PlanningContext'
import { useLanguage } from '@/contexts/LanguageContext'

// Titles resolved via i18n so they follow the language toggle.
const PAGE_TITLE_KEYS: Record<string, string> = {
  // One title for the pair: /quick-start and /data are two tabs of the same
  // sidebar entry, so the bar names the section and the tabs name the tab.
  '/data':        'topbar.title_data',
  '/quick-start': 'topbar.title_data',
  '/analyst':   'topbar.title_analyst',
  '/config':    'topbar.title_config',
  '/users':     'topbar.title_users',
  '/settings':  'topbar.title_settings',
  '/skus':      'skus.page_title',
  '/pedidos':   'orders.page_title',
  '/sessions':  'sessions.page_title',
}

// Granularity label of the active session, reusing the planning vocabulary.
const GRAIN_KEY: Record<string, string> = {
  daily: 'planning.daily', weekly: 'planning.weekly', monthly: 'planning.monthly',
}

// The page owns its session picker (deep links `/skus?session=<id>` and compare
// mode can point at a session other than the tenant's active one), so a global
// badge here would contradict what that page is actually showing.
const PATHS_WITH_OWN_SESSION_PICKER = ['/skus']

export default function TopBar() {
  const path    = usePathname()
  const { t }   = useLanguage()
  const title   = PAGE_TITLE_KEYS[path] ? t(PAGE_TITLE_KEYS[path]) : 'Faro'
  const { addToast } = useToast()
  // Active-session badge source of truth.
  //
  // This used to read a dedicated ActiveSessionContext, whose setter was never
  // called anywhere — activeSessionId stayed null forever and the badge never
  // rendered. That context has been deleted rather than wired up: PlanningContext
  // already holds `active_session_id` straight from the server-side resolver
  // (`planning_service.resolve_active_session`), which is the very session every
  // screen loads its numbers from. A second client-side "active session" store
  // would have to be pushed from each page and could drift from the resolver;
  // reading the resolver's answer cannot.
  const planning = usePlanning()?.planning ?? null

  const [time,        setTime]        = useState('')
  const [notifs,      setNotifs]      = useState<LocalNotice[]>([])
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
      const fresh: LocalNotice[] = []

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

  // The bell itself lives in components/alerts/AlertBell: it owns the durable,
  // server-side alert history (`GET /alerts` — what the 08:00 UTC loop sent,
  // failed deliveries included) and takes these in-session training notices as
  // a second, clearly separate source. They are not the same thing: these are
  // gone on reload and have no server record.
  const markLocalRead = useCallback(
    () => setNotifs(prev => prev.map(n => ({ ...n, read: true }))), [])
  const clearLocal = useCallback(() => setNotifs([]), [])

  // Resolve the badge against the list this bar already polls — no extra fetch.
  // Stays null when the tenant has no completed session, when planning has not
  // loaded yet, or when the id is not in the polled page, so the badge simply
  // does not render instead of showing a placeholder.
  const activeSession = planning?.active_session_id && !PATHS_WITH_OWN_SESSION_PICKER.includes(path)
    ? sessions.find(s => s.session_id === planning.active_session_id) ?? null
    : null
  // Granularity only matters when the tenant actually has siblings of the same
  // upload (daily/weekly/monthly): "demo1" and "demo1 · weekly" are different
  // plans whose coverage numbers are counted in different units.
  const grain = (planning?.available_periods.length ?? 0) > 1 ? activeSession?.granularity : null
  const grainLabel = grain ? t(GRAIN_KEY[grain] ?? '') : ''
  // Coarser family siblings are created with the machine-appended English
  // suffix " · <granularity>" (family_service). Drop it from the display name
  // when the localized chip is already saying the same thing.
  const suffix = grain ? ` · ${grain}` : ''
  const sessionLabel = activeSession && grainLabel && suffix && activeSession.name.endsWith(suffix)
    ? activeSession.name.slice(0, -suffix.length)
    : activeSession?.name ?? ''

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
            {/* Status indicator, not a control: it links to the session history
                so switching happens there instead of in a second switcher. */}
            <Link
              href="/sessions"
              title={t('topbar.active_session_title')}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                textDecoration: 'none', color: 'inherit',
              }}
            >
              <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 500, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {sessionLabel}
              </span>
              {grainLabel && (
                <span style={{
                  fontSize: 10, fontWeight: 600, color: 'var(--dim)',
                  border: '1px solid var(--border)', borderRadius: 4, padding: '1px 5px',
                  whiteSpace: 'nowrap',
                }}>
                  {grainLabel}
                </span>
              )}
            </Link>
          </>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* The planning-period selector used to sit here, labelled "Ver por".
            It reads as a view toggle and is nothing of the sort: it decides
            which trained sibling feeds the purchasing panel, inventory AND the
            daily alert emails, it is stored per TENANT, and only an admin may
            change it. It now lives in Settings, where an account-wide setting
            belongs, and the screens state which grain they are using.

            The global SKU search left too: /skus and /inventory each have
            their own search box, so this was a third way to do the same thing
            from a bar that should only carry what is true everywhere. */}

        <span style={{ fontSize: 12, color: 'var(--dim)', fontVariantNumeric: 'tabular-nums' }}>
          {time}
        </span>

        {/* Replay the guided tour. Only appears on screens that have one, so
            it is never a dead control, and it is how someone who dismissed the
            tour on their first visit gets it back. */}
        <TourLauncher />

        {/* Notification bell — durable alert history + this session's notices */}
        <AlertBell
          localNotices={notifs}
          onLocalRead={markLocalRead}
          onClearLocal={clearLocal}
        />

      </div>
    </header>
  )
}

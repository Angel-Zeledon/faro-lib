'use client'
import { InstallAppButton } from './InstallAppButton'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { LogOut, User, ChevronLeft, ChevronRight, X, LifeBuoy } from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { Wordmark } from '@/components/brand/Wordmark'
import { NAV, TOOLS_NAV, SETTINGS_ITEM, drawn, activePrimary, rememberOrigin, type Screen } from './navItems'
import { siteHref } from '@/lib/siteUrls'
import { useTenantFacts } from '@/hooks/useTenantFacts'

// The nav definition lives in ./navItems: six daily screens, then
// Configuración pinned at the foot. Every other screen is reached from the one
// it belongs to, and lights up that entry while it is open.

export default function Sidebar() {
  const path    = usePathname()
  const router  = useRouter()
  const user    = getUser()
  const { collapsed, toggle, drawerOpen, closeDrawer } = useSidebar()
  const { t } = useLanguage()

  // On a phone the rail is not a column of the layout — it is a drawer that
  // slides over the page. `collapsed` (the icons-only desktop rail) is
  // meaningless there: a 48px strip of unlabelled icons is worse than either
  // full nav or no nav, so inside the drawer the sidebar is always expanded.
  const narrow    = useIsNarrow()
  const isDrawer  = narrow
  const collapsedNow = isDrawer ? false : collapsed

  // Navigating closes the drawer. Without this the destination renders behind
  // the panel that took the user there.
  useEffect(() => {
    if (drawerOpen) closeDrawer()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  // Escape closes it too — and leaving the narrow viewport (rotating a tablet,
  // resizing a desktop window) must not strand a fixed panel over the page.
  useEffect(() => {
    if (!isDrawer && drawerOpen) closeDrawer()
  }, [isDrawer, drawerOpen, closeDrawer])

  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeDrawer() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerOpen, closeDrawer])

  function handleLogout() {
    authLogout().catch(() => {})
    clearAuth()
    router.replace('/login')
  }

  // Six daily screens and Configuración. Role still hides an entry, never a
  // plan: there are no locks.
  // Entries with a rule (Proveedores, Escenarios, Mensajes, Actividad) are drawn
  // when the tenant has something for them, or when you are on them; the
  // command palette lists everything regardless.
  const facts = useTenantFacts()
  const show = (item: Screen) => drawn(item, user?.role, facts, path)
  const visibleNav = NAV.filter(show)
  const visibleTools = TOOLS_NAV.filter(show)
  const order = [...visibleNav, ...visibleTools, SETTINGS_ITEM]

  // Where the user came from, so a secondary screen lights the entry it was
  // opened from (see activePrimary).
  const [origin, setOrigin] = useState<string | null>(null)
  useEffect(() => { setOrigin(rememberOrigin(path)) }, [path])
  const lit = activePrimary(path, origin)

  function renderItem(item: Screen) {
    const { href, labelKey, Icon } = item
    const active = lit === href
    const label = t(labelKey)
    return (
      <Link key={href} href={href} onClick={isDrawer ? closeDrawer : undefined}
            aria-current={active ? 'page' : undefined}
            style={{ textDecoration: 'none' }} title={collapsedNow ? label : undefined}>
        <div
          className={clsx('nav-item', 'sb-unfold', active ? 'nav-item-active' : 'nav-item-idle')}
          style={{
            // The menu unfolds top to bottom when the app opens
            // (globals.css "Sidebar unfold"). The sidebar stays
            // mounted across navigations, so it plays once per load.
            animationDelay: `${0.15 + order.indexOf(item) * 0.035}s`,
            display: 'flex', alignItems: 'center',
            // A finger, not a mouse pointer: 44px minimum in the
            // drawer, a roomier 9px row on desktop now that there are
            // seven entries instead of eighteen.
            minHeight: isDrawer ? 44 : undefined,
            justifyContent: collapsedNow ? 'center' : 'flex-start',
            gap: collapsedNow ? 0 : 10,
            padding: collapsedNow ? '9px 0' : isDrawer ? '8px 12px' : '9px 10px',
            borderRadius: 7, marginBottom: 2,
            background: active ? 'var(--sidebar-active-bg)' : 'transparent',
            color: active ? 'var(--sidebar-text-active)' : 'var(--sidebar-text)',
            fontWeight: active ? 600 : 400, fontSize: 13,
            transition: 'all 0.15s', cursor: 'pointer',
          }}
        >
          <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
          {!collapsedNow && label}
        </div>
      </Link>
    )
  }

  // Off-canvas on a phone: taken out of the flex row entirely (so the page gets
  // the full width) and slid in over it. On desktop this object is empty and
  // the rail behaves exactly as it always has.
  const drawerStyle: React.CSSProperties = isDrawer
    ? {
      position: 'fixed', top: 0, left: 0, bottom: 0, zIndex: 70,
      width: 260, minWidth: 260, maxWidth: '85vw',
      transform: drawerOpen ? 'translateX(0)' : 'translateX(-100%)',
      // On the shared timing scale so the panel and its veil move as one
      // gesture instead of two hand-tuned durations that drift apart.
      transition: 'transform var(--dur-4) var(--ease-out)',
      boxShadow: drawerOpen ? '0 0 40px rgba(0,0,0,0.45)' : 'none',
    }
    : {}

  return (
    <>
    {/* Overlay: dismisses the drawer and, just as importantly, stops taps
        landing on the page underneath it. It fades in rather than snapping to
        black, so it reads as part of the drawer arriving and not as a second,
        harsher event on top of it. */}
    {isDrawer && drawerOpen && (
      <div
        onClick={closeDrawer}
        aria-hidden="true"
        className="modal-backdrop-enter"
        style={{
          position: 'fixed', inset: 0, zIndex: 69,
          background: 'rgba(0,0,0,0.5)',
        }}
      />
    )}
    <aside
      aria-hidden={isDrawer && !drawerOpen ? true : undefined}
      style={{
        width: collapsedNow ? 48 : 220, minWidth: collapsedNow ? 48 : 220,
        // Brand carrier: petrol in BOTH themes (see --sidebar-* in globals.css)
        background: 'var(--sidebar-bg)', borderRight: '1px solid var(--sidebar-border)',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
        transition: 'width 0.2s ease, min-width 0.2s ease',
        ...drawerStyle,
      }}
    >

      {/* Logo */}
      {(!collapsedNow || isDrawer) && <div style={{
        padding: '22px 20px 18px',
        borderBottom: '1px solid var(--sidebar-border)',
        display: 'flex', alignItems: 'center',
      }}>
        {/* Type-only mark; the sidebar is petrol in both themes, so "ai"
            takes the light end of the brand gradient to hold its contrast.
            On the collapsed rail there is no mark at all: an initial or a
            fragment of the name is not the name. */}
        {!collapsedNow && (
          <div>
            <Wordmark size={21} color="var(--sidebar-text-active)" accent="#4CC3B5" />
            <div style={{ fontSize: 11, color: 'var(--sidebar-dim)', marginTop: 5 }}>
              {t('sidebar.tagline')}
            </div>
          </div>
        )}
        {isDrawer && (
          <button
            onClick={closeDrawer}
            aria-label={t('common.close')}
            style={{
              all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
              marginLeft: 'auto', width: 40, height: 40, borderRadius: 9,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--sidebar-text)',
            }}
          >
            <X size={18} aria-hidden="true" />
          </button>
        )}
      </div>}

      {/* Navigation */}
      <nav aria-label={t('mobile.tabbar_label')} style={{
        flex: 1, overflowY: 'auto', overflowX: 'hidden',
        padding: collapsedNow ? '12px 6px' : '14px 10px',
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Collapsed to an icon rail, the daily screens share the height
            evenly instead of bunching at the top. */}
        {collapsedNow ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-evenly', minHeight: 0 }}>
            {[...visibleNav, ...visibleTools].map(renderItem)}
          </div>
        ) : (
          <>
            {visibleNav.map(renderItem)}
            {/* Tools that belong to no single flow. */}
            <div style={{ borderTop: '1px solid var(--sidebar-border)', margin: '8px 0 10px' }} />
            {visibleTools.map(renderItem)}
            {/* Configuración sits apart from the daily screens: it is where the
                rest of the product lives (account, team, data, automation). */}
            <div style={{ flex: 1, minHeight: 16 }} />
          </>
        )}
        <div style={{ borderTop: '1px solid var(--sidebar-border)', paddingTop: 10, marginBottom: 2 }}>
          {renderItem(SETTINGS_ITEM)}
        </div>

        <InstallAppButton collapsed={collapsedNow} />

        {/* The help center lives on the landing, which may be another origin,
            so it opens in its own tab and the screen behind keeps its state. */}
        <a
          href={siteHref('/docs')}
          target="_blank"
          rel="noopener"
          title={collapsedNow ? t('help.center') : t('help.center_hint')}
          style={{
            all: 'unset', cursor: 'pointer', boxSizing: 'border-box', marginTop: 4,
            display: 'flex', alignItems: 'center', justifyContent: collapsedNow ? 'center' : 'flex-start',
            gap: collapsedNow ? 0 : 8, width: '100%',
            padding: collapsedNow ? '8px 0' : '8px 10px', borderRadius: 7,
            color: 'var(--sidebar-text)', fontSize: 12.5,
          }}
        >
          <LifeBuoy size={14} aria-hidden="true" />
          {!collapsedNow && <span>{t('help.center')}</span>}
        </a>

        {/* Collapse toggle — desktop only. In the drawer there is nothing to
            collapse to: the panel is either open over the page or gone. */}
        {!isDrawer && (
        <button
          onClick={toggle}
          title={collapsedNow ? t('sidebar.expand') : t('sidebar.collapse')}
          style={{
            all: 'unset', cursor: 'pointer', marginTop: 8,
            display: 'flex', alignItems: 'center',
            justifyContent: collapsedNow ? 'center' : 'flex-start',
            gap: collapsedNow ? 0 : 8, width: '100%',
            padding: collapsedNow ? '8px 0' : '8px 10px', borderRadius: 7,
            color: 'var(--sidebar-dim)', fontSize: 12, transition: 'all 0.15s',
          }}
        >
          {collapsedNow ? <ChevronRight size={14} /> : <><ChevronLeft size={14} /><span>{t('sidebar.collapse')}</span></>}
        </button>
        )}

      </nav>

      {/* User footer — no profile selector */}
      <div style={{ borderTop: '1px solid var(--sidebar-border)' }}>
        {user && (
          <div style={{
            padding: collapsedNow ? '10px 0' : '10px 14px',
            display: 'flex', alignItems: 'center',
            justifyContent: collapsedNow ? 'center' : 'flex-start', gap: 8,
          }}>
            <div style={{
              width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
              background: 'var(--sidebar-active-bg)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <User size={12} color="var(--sidebar-beam)" />
            </div>
            {!collapsedNow && (
              <>
                {/* The name opens your own account: profile and security
                    are one click away without a sidebar entry of their own. */}
                <Link href="/mi-cuenta" title={t('nav.account')} style={{ flex: 1, minWidth: 0, textDecoration: 'none' }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--sidebar-text-active)', overflow: 'hidden', overflowWrap: 'anywhere', }}>
                    {user.full_name || user.email.split('@')[0]}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--sidebar-dim)' }}>{roleLabel(t, user.role)}</div>
                </Link>
                <button
                  onClick={handleLogout}
                  title={t('sidebar.logout')}
                  style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: 'var(--sidebar-dim)', display: 'flex', alignItems: 'center' }}
                >
                  <LogOut size={13} />
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
    </>
  )
}

'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ShoppingCart, ClipboardList, Package, BrainCircuit, Menu } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useDmUnread } from '@/lib/dmUnread'
import { getUser } from '@/lib/auth'
import { visibleNavFor, navItemMatches } from '@/components/layout/navItems'
import MoreSheet from './MoreSheet'

/**
 * The phone's primary navigation: a fixed bottom tab bar, like a native app.
 *
 * Four destinations a buyer uses daily on the warehouse floor, plus "Más",
 * which opens a sheet with every other screen (grouped like the desktop
 * sidebar) and the account / preference actions. It replaces the hamburger
 * drawer on narrow screens — everything the sidebar offered is still one or
 * two taps away.
 *
 * Height is `--mobile-nav-h` (56px + the home-indicator inset), defined in
 * globals.css; `.page-content` is padded by it so the last row of any screen
 * clears the bar, and StickyActionBar / toasts / the /compras cart bar sit on
 * top of it. It hides while the on-screen keyboard is up, the way native tab
 * bars do, so a form field is never squeezed between keyboard and tabs.
 */
const TABS = [
  { href: '/compras',    labelKey: 'mobile.tab_panel',     Icon: ShoppingCart },
  { href: '/pedidos',    labelKey: 'nav.orders',           Icon: ClipboardList },
  { href: '/inventario', labelKey: 'nav.inventory',        Icon: Package },
  { href: '/asistente',  labelKey: 'mobile.tab_assistant', Icon: BrainCircuit },
] as const

export default function MobileTabBar() {
  const path = usePathname()
  const { t } = useLanguage()
  const unread = useDmUnread()
  const [moreOpen, setMoreOpen] = useState(false)
  const keyboard = useKeyboardOpen()

  // Navigating from the sheet closes it.
  useEffect(() => { setMoreOpen(false) }, [path])

  const isTab = (href: string) => path === href || path.startsWith(`${href}/`)
  // "Más" reads as active on any screen that is not one of the four tabs, so
  // the bar always tells you where you are.
  const role = getUser()?.role
  const onOtherScreen = !TABS.some(tab => isTab(tab.href))
    && visibleNavFor(role).some(item => navItemMatches(item, path))

  const tabStyle = (active: boolean): React.CSSProperties => ({
    all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
    flex: 1, minWidth: 0, height: 56,
    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3,
    color: active ? 'var(--accent)' : 'var(--dim)',
    fontSize: 10.5, fontWeight: active ? 700 : 500, textDecoration: 'none',
    position: 'relative',
  })

  const label = (key: string) => (
    <span style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '0 2px' }}>{t(key)}</span>
  )

  return (
    <>
      <nav
        aria-label={t('mobile.tabbar_label')}
        data-mobile-tabbar=""
        className="mobile-tabbar"
        style={{
          position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 60,
          display: 'flex',
          background: 'var(--surface)', borderTop: '1px solid var(--border)',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          paddingLeft: 'env(safe-area-inset-left, 0px)', paddingRight: 'env(safe-area-inset-right, 0px)',
          transform: keyboard ? 'translateY(100%)' : 'none',
          transition: 'transform var(--dur-3) var(--ease-out)',
        }}
      >
        {TABS.map(({ href, labelKey, Icon }) => {
          const active = isTab(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className="tap-feedback"
              style={tabStyle(active)}
            >
              <TabIndicator active={active} />
              <Icon size={22} strokeWidth={active ? 2.3 : 1.8} aria-hidden="true" />
              {label(labelKey)}
            </Link>
          )
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          aria-current={onOtherScreen ? 'page' : undefined}
          aria-label={unread > 0 ? `${t('mobile.tab_more')} · ${t('mobile.unread_count', { n: unread })}` : undefined}
          className="tap-feedback"
          style={tabStyle(onOtherScreen || moreOpen)}
        >
          <TabIndicator active={onOtherScreen} />
          <span style={{ position: 'relative', display: 'flex' }}>
            <Menu size={22} strokeWidth={onOtherScreen ? 2.3 : 1.8} aria-hidden="true" />
            {unread > 0 && (
              <span aria-hidden="true" style={{
                position: 'absolute', top: -5, right: -10,
                minWidth: 17, height: 17, borderRadius: 9, padding: '0 4px', boxSizing: 'border-box',
                background: 'var(--accent)', color: '#fff', border: '2px solid var(--surface)',
                fontSize: 9.5, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{unread > 99 ? '99+' : unread}</span>
            )}
          </span>
          {label('mobile.tab_more')}
        </button>
      </nav>
      <MoreSheet open={moreOpen} onClose={() => setMoreOpen(false)} unread={unread} />
    </>
  )
}

function TabIndicator({ active }: { active: boolean }) {
  return (
    <span aria-hidden="true" style={{
      position: 'absolute', top: 0, left: '50%', width: 28, height: 3, borderRadius: '0 0 3px 3px',
      background: 'var(--accent)',
      transform: `translateX(-50%) scaleX(${active ? 1 : 0})`,
      transition: 'transform var(--dur-3) var(--ease-out)',
    }} />
  )
}

/** True while the on-screen keyboard is (probably) up: the visual viewport is
 *  much shorter than the layout viewport. Only phones have visualViewport
 *  shrink like this; desktop never reaches this component. */
function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const vv = typeof window !== 'undefined' ? window.visualViewport : null
    if (!vv) return
    const check = () => {
      const editing = document.activeElement instanceof HTMLInputElement
        || document.activeElement instanceof HTMLTextAreaElement
        || (document.activeElement as HTMLElement | null)?.isContentEditable === true
      setOpen(editing && window.innerHeight - vv.height > 150)
    }
    vv.addEventListener('resize', check)
    window.addEventListener('focusin', check)
    window.addEventListener('focusout', check)
    return () => {
      vv.removeEventListener('resize', check)
      window.removeEventListener('focusin', check)
      window.removeEventListener('focusout', check)
    }
  }, [])
  return open
}

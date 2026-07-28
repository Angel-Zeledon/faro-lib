'use client'
import Link from 'next/link'
import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  TrendingUp, Package,
  BrainCircuit, Settings, KeyRound, LogOut, User, Users,
  ChevronLeft, ChevronRight, X,
  ShoppingCart, Truck, Upload, Zap, ClipboardList, Plug, History,
  FlaskConical, ListChecks,
} from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import { useEntitlements } from '@/lib/entitlements'
import { useIsNarrow } from '@/hooks/useIsNarrow'

// ── Nav definition ────────────────────────────────────────────────────────────
interface NavItem {
  href:       string
  labelKey:   string
  Icon:       React.ElementType
  group:      string
  adminOnly?: boolean
  /** Feature enum value gating this route (see backend `Feature`). Items
   *  without this always render as a normal link. */
  feature?:   string
  /** Sibling routes this one entry stands for, so the item still reads as
   *  active while the user is on a tab that is not `href`. */
  alsoActive?: string[]
}

const NAV: NavItem[] = [
  { href: '/hoy',                 labelKey: 'nav.hoy',         Icon: ShoppingCart,    group: 'operation' },
  { href: '/pedidos',             labelKey: 'nav.orders',     Icon: ClipboardList,   group: 'operation' },
  { href: '/skus',                labelKey: 'nav.skus',        Icon: Package,         group: 'operation' },

  // One door, not two. "Subir mis ventas" and "mis archivos" are the same
  // errand to the person doing it, so the nav carries a single entry and the
  // two routes are tabs of each other (see components/layout/DataTabs.tsx).
  { href: '/quick-start',         labelKey: 'nav.data',        Icon: Upload,          group: 'data',
    alsoActive: ['/data'] },

  { href: '/inventory',           labelKey: 'nav.inventory',   Icon: Package,         group: 'purchasing' },
  // Ahead of the full inventory list on purpose: a tenant with 2.000
  // unconfigured products needs the 40 that carry 82% of the spend, not the
  // 2.000. The route is /inventory-setup rather than /inventory/setup because
  // the latter would nest under the inventory tree's own layout.
  { href: '/inventory-setup',     labelKey: 'nav.inventory_setup', Icon: ListChecks,  group: 'purchasing' },
  { href: '/inventory/suppliers', labelKey: 'nav.suppliers',   Icon: Truck,           group: 'purchasing' },

  { href: '/inventory/roi',       labelKey: 'nav.roi',         Icon: TrendingUp,      group: 'analysis' },
  { href: '/sessions',            labelKey: 'nav.sessions',    Icon: History,         group: 'analysis' },
  { href: '/analyst',             labelKey: 'nav.analyst',     Icon: BrainCircuit,    group: 'analysis', feature: 'ai_analyst' },
  { href: '/scenarios',           labelKey: 'nav.scenarios',   Icon: FlaskConical,    group: 'analysis', feature: 'event_simulator' },

  { href: '/users',               labelKey: 'nav.users',       Icon: Users,           group: 'system',  adminOnly: true },
  { href: '/integraciones',       labelKey: 'nav.integrations', Icon: Plug,           group: 'system',  feature: 'integrations' },
  { href: '/config',              labelKey: 'nav.config',      Icon: Settings,        group: 'system' },
  { href: '/settings',            labelKey: 'nav.settings',    Icon: KeyRound,        group: 'system',  adminOnly: true, feature: 'api_access' },
]

const GROUPS = ['operation', 'data', 'purchasing', 'analysis', 'system']

export default function Sidebar() {
  const path    = usePathname()
  const router  = useRouter()
  const user    = getUser()
  const { collapsed, toggle, drawerOpen, closeDrawer } = useSidebar()
  const { t, lang, setLang } = useLanguage()
  const { has } = useEntitlements()

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

  // Locked features are hidden, not shown padlocked. Four of the fourteen nav
  // items were permanent locks, so the nav taught a new user more about what
  // they do NOT have than about what they do. The upsell belongs where someone
  // reaches for the feature, not as fixed furniture.
  const visibleNav = NAV.filter(item => {
    if (item.adminOnly && user?.role !== 'admin') return false
    if (item.feature && !has(item.feature)) return false
    return true
  })

  // ...but hiding every lock would orphan /planes, which today is only reached
  // through the padlock's upsell (and through Integraciones, itself a lock). So
  // the four padlocks collapse into one deliberate way in.
  const hasLockedFeature = NAV.some(item => item.feature && !has(item.feature))

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
      <div style={{
        padding: collapsedNow ? '18px 0' : '22px 20px 18px',
        borderBottom: '1px solid var(--sidebar-border)',
        display: 'flex', alignItems: 'center',
        justifyContent: collapsedNow ? 'center' : 'flex-start',
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8, flexShrink: 0,
          background: 'var(--brand-grad)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={17} color="#fff" strokeWidth={2.5} />
        </div>
        {!collapsedNow && (
          <div style={{ marginLeft: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-0.02em', color: 'var(--sidebar-text-active)' }}>Faro</div>
            <div style={{ fontSize: 11, color: 'var(--sidebar-dim)', marginTop: 1 }}>
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
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: collapsedNow ? '12px 6px' : '12px 10px' }}>
        {GROUPS.map(group => {
          const items = visibleNav.filter(n => n.group === group)
          if (!items.length) return null
          return (
            <div key={group} style={{ marginBottom: collapsedNow ? 12 : 20 }}>
              {!collapsedNow && (
                <div style={{
                  fontSize: 10, fontWeight: 700, color: 'var(--sidebar-dim)',
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                  padding: '0 10px', marginBottom: 4,
                }}>
                  {t(`group.${group}`)}
                </div>
              )}
              {items.map(({ href, labelKey, Icon, alsoActive }) => {
                const matches = (p: string) => path === p || path.startsWith(`${p}/`)
                const active = matches(href) || (alsoActive?.some(matches) ?? false)
                const label = t(labelKey)

                return (
                  <Link key={href} href={href} onClick={isDrawer ? closeDrawer : undefined}
                        style={{ textDecoration: 'none' }} title={collapsedNow ? label : undefined}>
                    <div
                      className={clsx('nav-item', active ? 'nav-item-active' : 'nav-item-idle')}
                      style={{
                        display: 'flex', alignItems: 'center',
                        // A finger, not a mouse pointer: 44px minimum in the
                        // drawer, unchanged 8px padding on desktop.
                        minHeight: isDrawer ? 44 : undefined,
                        justifyContent: collapsedNow ? 'center' : 'flex-start',
                        gap: collapsedNow ? 0 : 10,
                        padding: collapsedNow ? '8px 0' : isDrawer ? '8px 12px' : '8px 10px',
                        borderRadius: 7, marginBottom: 1,
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
              })}
            </div>
          )
        })}

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

        {/* The single remaining way to the plan comparison. */}
        {!collapsedNow && hasLockedFeature && (
          <div style={{ marginTop: 8, padding: '0 10px' }}>
            <Link
              href="/planes"
              style={{
                display: 'block', textAlign: 'center',
                padding: '7px 0', borderRadius: 7,
                border: '1px dashed rgba(255,255,255,0.25)',
                color: 'var(--sidebar-dim)', fontSize: 11.5, fontWeight: 600,
              }}
            >
              {t('sidebar.see_plans')}
            </Link>
          </div>
        )}

        {/* Language switcher */}
        {!collapsedNow && (
          <div style={{ marginTop: 8, padding: '0 10px' }}>
            <div style={{ display: 'flex', gap: 4, border: '1px solid var(--sidebar-border)', borderRadius: 7, padding: 3 }}>
              {(['es', 'en'] as const).map(l => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  style={{
                    all: 'unset', cursor: 'pointer', flex: 1, textAlign: 'center',
                    padding: '4px 0', borderRadius: 5, fontSize: 11, fontWeight: 600,
                    background: lang === l ? 'var(--sidebar-active-bg)' : 'transparent',
                    color: lang === l ? 'var(--sidebar-text-active)' : 'var(--sidebar-dim)',
                    transition: 'all 0.15s',
                  }}
                >
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
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
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--sidebar-text-active)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user.full_name || user.email.split('@')[0]}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--sidebar-dim)' }}>{roleLabel(t, user.role)}</div>
                </div>
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
        {!collapsedNow && (
          <div style={{ padding: '4px 16px 12px', fontSize: 11, color: 'var(--sidebar-dim)', opacity: 0.6 }}>
            v{process.env.NEXT_PUBLIC_APP_VERSION ?? '1.0.0'}
          </div>
        )}
      </div>
    </aside>
    </>
  )
}

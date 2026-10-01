'use client'
import { InstallAppButton } from './InstallAppButton'
import Link from 'next/link'
import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  TrendingUp, Package,
  BrainCircuit, Settings, KeyRound, LogOut, User, Users,
  ChevronLeft, ChevronRight, X,
  ShoppingCart, Truck, Upload, ClipboardList, Plug, History,
  FlaskConical, ListChecks, MessageSquare, Target, Clock, Code2, ServerCog,
  ScrollText,
} from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { Wordmark } from '@/components/brand/Wordmark'

// ── Nav definition ────────────────────────────────────────────────────────────
interface NavItem {
  href:       string
  labelKey:   string
  Icon:       React.ElementType
  group:      string
  adminOnly?: boolean
  /** Feature enum value gating this route (see backend `Feature`). Items
   *  without this always render as a normal link. */
  /** Sibling routes this one entry stands for, so the item still reads as
   *  active while the user is on a tab that is not `href`. */
  alsoActive?: string[]
}

const NAV: NavItem[] = [
  { href: '/compras',             labelKey: 'nav.hoy',         Icon: ShoppingCart,    group: 'operation' },
  { href: '/pedidos',             labelKey: 'nav.orders',      Icon: ClipboardList,   group: 'operation' },
  { href: '/mensajes',            labelKey: 'nav.messages',    Icon: MessageSquare,   group: 'operation' },

  // One door, not two. "Subir mis ventas" and "mis archivos" are the same
  // errand to the person doing it, so the nav carries a single entry and the
  // two routes are tabs of each other (see components/layout/DataTabs.tsx).
  { href: '/ventas',              labelKey: 'nav.data',        Icon: Upload,          group: 'data',
    alsoActive: ['/archivos'] },

  { href: '/inventario',          labelKey: 'nav.inventory',   Icon: Package,         group: 'purchasing' },
  // Ahead of the full inventory list on purpose: a tenant with 2.000
  // unconfigured products needs the 40 that carry 82% of the spend, not the
  // 2.000. Kept flat as /configurar-inventario rather than nested under
  // /inventario, so it can never inherit a layout that screen does not want.
  { href: '/configurar-inventario', labelKey: 'nav.inventory_setup', Icon: ListChecks, group: 'purchasing' },
  { href: '/proveedores',         labelKey: 'nav.suppliers',   Icon: Truck,           group: 'purchasing' },

  // Forecasts belong here, not under Operación. Nobody opens this screen to
  // get today's work done — they open it to understand a product.
  { href: '/pronosticos',         labelKey: 'nav.skus',        Icon: TrendingUp,      group: 'analysis' },
  { href: '/impacto',             labelKey: 'nav.roi',         Icon: Target,          group: 'analysis' },
  { href: '/historial',           labelKey: 'nav.sessions',    Icon: History,         group: 'analysis' },
  // What the product did, and why — the other half of the bell, which only
  // carries what needs a decision. Under Análisis rather than Sistema because
  // it answers a buyer's question ("did the order go out?"), not an
  // administrator's.
  { href: '/actividad',           labelKey: 'nav.activity',    Icon: ScrollText,      group: 'analysis' },
  { href: '/asistente',           labelKey: 'nav.analyst',     Icon: BrainCircuit,    group: 'analysis' },
  { href: '/escenarios',          labelKey: 'nav.scenarios',   Icon: FlaskConical,    group: 'analysis' },

  { href: '/usuarios',            labelKey: 'nav.users',       Icon: Users,           group: 'system',  adminOnly: true },
  // These two used to be crossed: /config held your own profile while being
  // called "Configuración", and /settings held scheduled recalculation while
  // being called "Tareas programadas". Both routes said "settings" and neither
  // matched its screen.
  { href: '/mi-cuenta',           labelKey: 'nav.account',     Icon: User,            group: 'system' },
  { href: '/automatizacion',      labelKey: 'nav.automation',  Icon: Clock,           group: 'system',  adminOnly: true },
  // What this deployment's services are, and what is off. adminOnly hides it
  // from an analyst; the INSTANCE tab inside it is gated again by
  // INSTANCE_ADMIN_EMAILS, because `admin` is a role inside a tenant and the
  // deployment's credentials are not a tenant's to read.
  { href: '/instalacion',         labelKey: 'nav.installation', Icon: ServerCog,       group: 'system',  adminOnly: true },
  // NOT adminOnly: an analyst is exactly who wires a customer's own system up
  // to the public API, and the page only ever acts with the key the reader
  // pastes into it — never with their session.
  { href: '/api',                 labelKey: 'nav.api',         Icon: Code2,           group: 'system' },
]

const GROUPS = ['operation', 'data', 'purchasing', 'analysis', 'system']

export default function Sidebar() {
  const path    = usePathname()
  const router  = useRouter()
  const user    = getUser()
  const { collapsed, toggle, drawerOpen, closeDrawer } = useSidebar()
  const { t, lang, setLang } = useLanguage()

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

  // Four of the fourteen entries used to be plan locks — hidden, not padlocked,
  // because a nav full of padlocks teaches a new user what they do NOT have.
  // There are no locks left: role is the only thing that hides an entry now.
  const visibleNav = NAV.filter(item => !(item.adminOnly && user?.role !== 'admin'))

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
        {/* Type-only mark; the sidebar is petrol in both themes, so "ai"
            takes the light end of the brand gradient to hold its contrast. */}
        {collapsedNow ? (
          <Wordmark size={17} compact color="var(--sidebar-text-active)" accent="#4CC3B5" />
        ) : (
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
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: collapsedNow ? '12px 6px' : '12px 10px' }}>
        {GROUPS.map(group => {
          const items = visibleNav.filter(n => n.group === group)
          if (!items.length) return null
          return (
            <div key={group} style={{ marginBottom: collapsedNow ? 12 : 20 }}>
              {!collapsedNow && (
                <div className="sb-unfold" style={{
                  animationDelay: `${0.12 + visibleNav.indexOf(items[0]) * 0.035}s`,
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
                      className={clsx('nav-item', 'sb-unfold', active ? 'nav-item-active' : 'nav-item-idle')}
                      style={{
                        // The menu unfolds top to bottom when the app opens
                        // (globals.css "Sidebar unfold"). The sidebar stays
                        // mounted across navigations, so it plays once per load.
                        animationDelay: `${0.15 + visibleNav.indexOf(visibleNav.find(n => n.href === href)!) * 0.035}s`,
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

        <InstallAppButton collapsed={collapsedNow} />

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

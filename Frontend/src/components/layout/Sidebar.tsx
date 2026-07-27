'use client'
import Link from 'next/link'
import { useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  Database, TrendingUp, Package,
  BrainCircuit, Settings, KeyRound, LogOut, User, Users,
  ChevronLeft, ChevronRight,
  ShoppingCart, Truck, Upload, Zap, ClipboardList, Lock, Plug, History,
  FlaskConical,
} from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { roleLabel } from '@/lib/enumLabels'
import { useEntitlements } from '@/lib/entitlements'
import UpsellModal from './UpsellModal'

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
}

const NAV: NavItem[] = [
  { href: '/hoy',                 labelKey: 'nav.hoy',         Icon: ShoppingCart,    group: 'operation' },
  { href: '/pedidos',             labelKey: 'nav.orders',     Icon: ClipboardList,   group: 'operation' },
  { href: '/skus',                labelKey: 'nav.skus',        Icon: Package,         group: 'operation' },

  { href: '/quick-start',         labelKey: 'nav.quick_start', Icon: Upload,          group: 'data' },
  { href: '/data',                labelKey: 'nav.data',        Icon: Database,        group: 'data' },

  { href: '/inventory',           labelKey: 'nav.inventory',   Icon: Package,         group: 'purchasing' },
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
  const { collapsed, toggle } = useSidebar()
  const { t, lang, setLang } = useLanguage()
  const { has } = useEntitlements()
  const [lockedFeature, setLockedFeature] = useState<string | null>(null)

  function handleLogout() {
    authLogout().catch(() => {})
    clearAuth()
    router.replace('/login')
  }

  const visibleNav = NAV.filter(item => {
    if (item.adminOnly && user?.role !== 'admin') return false
    return true
  })

  return (
    <>
    <aside style={{
      width: collapsed ? 48 : 220, minWidth: collapsed ? 48 : 220,
      background: 'var(--surface)', borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      transition: 'width 0.2s ease, min-width 0.2s ease',
    }}>

      {/* Logo */}
      <div style={{
        padding: collapsed ? '18px 0' : '22px 20px 18px',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center',
        justifyContent: collapsed ? 'center' : 'flex-start',
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8, flexShrink: 0,
          background: 'linear-gradient(135deg, #818cf8, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Zap size={17} color="#fff" strokeWidth={2.5} />
        </div>
        {!collapsed && (
          <div style={{ marginLeft: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-0.02em', color: 'var(--text)' }}>Faro</div>
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1 }}>
              {t('sidebar.tagline')}
            </div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: collapsed ? '12px 6px' : '12px 10px' }}>
        {GROUPS.map(group => {
          const items = visibleNav.filter(n => n.group === group)
          if (!items.length) return null
          return (
            <div key={group} style={{ marginBottom: collapsed ? 12 : 20 }}>
              {!collapsed && (
                <div style={{
                  fontSize: 10, fontWeight: 700, color: 'var(--dim)',
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                  padding: '0 10px', marginBottom: 4,
                }}>
                  {t(`group.${group}`)}
                </div>
              )}
              {items.map(({ href, labelKey, Icon, feature }) => {
                const active = path === href || path.startsWith(`${href}/`)
                const label = t(labelKey)
                const locked = !!feature && !has(feature)

                if (locked) {
                  return (
                    <div
                      key={href}
                      role="button"
                      tabIndex={0}
                      onClick={() => setLockedFeature(feature!)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setLockedFeature(feature!) }}
                      title={collapsed ? label : t('entitlements.locked_tooltip')}
                      className="nav-item nav-item-idle"
                      style={{
                        display: 'flex', alignItems: 'center',
                        justifyContent: collapsed ? 'center' : 'flex-start',
                        gap: collapsed ? 0 : 10,
                        padding: collapsed ? '8px 0' : '8px 10px',
                        borderRadius: 7, marginBottom: 1,
                        color: 'var(--dim)', fontWeight: 400, fontSize: 13,
                        transition: 'all 0.15s', cursor: 'pointer',
                      }}
                    >
                      <Icon size={15} strokeWidth={1.8} />
                      {!collapsed && (
                        <span style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          {label}
                          <Lock size={11} style={{ flexShrink: 0, marginLeft: 6 }} />
                        </span>
                      )}
                    </div>
                  )
                }

                return (
                  <Link key={href} href={href} style={{ textDecoration: 'none' }} title={collapsed ? label : undefined}>
                    <div
                      className={clsx('nav-item', active ? 'nav-item-active' : 'nav-item-idle')}
                      style={{
                        display: 'flex', alignItems: 'center',
                        justifyContent: collapsed ? 'center' : 'flex-start',
                        gap: collapsed ? 0 : 10,
                        padding: collapsed ? '8px 0' : '8px 10px',
                        borderRadius: 7, marginBottom: 1,
                        background: active ? 'var(--accent-dim)' : 'transparent',
                        color: active ? 'var(--accent)' : 'var(--muted)',
                        fontWeight: active ? 600 : 400, fontSize: 13,
                        transition: 'all 0.15s', cursor: 'pointer',
                      }}
                    >
                      <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
                      {!collapsed && label}
                    </div>
                  </Link>
                )
              })}
            </div>
          )
        })}

        {/* Collapse toggle */}
        <button
          onClick={toggle}
          title={collapsed ? t('sidebar.expand') : t('sidebar.collapse')}
          style={{
            all: 'unset', cursor: 'pointer', marginTop: 8,
            display: 'flex', alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            gap: collapsed ? 0 : 8, width: '100%',
            padding: collapsed ? '8px 0' : '8px 10px', borderRadius: 7,
            color: 'var(--dim)', fontSize: 12, transition: 'all 0.15s',
          }}
        >
          {collapsed ? <ChevronRight size={14} /> : <><ChevronLeft size={14} /><span>{t('sidebar.collapse')}</span></>}
        </button>

        {/* Language switcher */}
        {!collapsed && (
          <div style={{ marginTop: 8, padding: '0 10px' }}>
            <div style={{ display: 'flex', gap: 4, border: '1px solid var(--border)', borderRadius: 7, padding: 3 }}>
              {(['es', 'en'] as const).map(l => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  style={{
                    all: 'unset', cursor: 'pointer', flex: 1, textAlign: 'center',
                    padding: '4px 0', borderRadius: 5, fontSize: 11, fontWeight: 600,
                    background: lang === l ? 'var(--accent-dim)' : 'transparent',
                    color: lang === l ? 'var(--accent)' : 'var(--dim)',
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
      <div style={{ borderTop: '1px solid var(--border)' }}>
        {user && (
          <div style={{
            padding: collapsed ? '10px 0' : '10px 14px',
            display: 'flex', alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start', gap: 8,
          }}>
            <div style={{
              width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
              background: 'var(--accent-dim)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <User size={12} color="var(--accent)" />
            </div>
            {!collapsed && (
              <>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user.full_name || user.email.split('@')[0]}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--dim)' }}>{roleLabel(t, user.role)}</div>
                </div>
                <button
                  onClick={handleLogout}
                  title={t('sidebar.logout')}
                  style={{ all: 'unset', cursor: 'pointer', padding: 4, borderRadius: 5, color: 'var(--dim)', display: 'flex', alignItems: 'center' }}
                >
                  <LogOut size={13} />
                </button>
              </>
            )}
          </div>
        )}
        {!collapsed && (
          <div style={{ padding: '4px 16px 12px', fontSize: 11, color: 'var(--dim)', opacity: 0.6 }}>
            v{process.env.NEXT_PUBLIC_APP_VERSION ?? '1.0.0'}
          </div>
        )}
      </div>
    </aside>
    {lockedFeature && (
      <UpsellModal feature={lockedFeature} onClose={() => setLockedFeature(null)} />
    )}
    </>
  )
}

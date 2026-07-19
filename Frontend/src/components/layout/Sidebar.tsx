'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  Database, TrendingUp, Package,
  BrainCircuit, Settings, LogOut, User, Users,
  ChevronLeft, ChevronRight, FlaskConical,
  ShoppingCart, Truck, Upload, Zap, ClipboardList,
} from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useBusinessProfile } from '@/contexts/BusinessProfileContext'
import { useLanguage } from '@/contexts/LanguageContext'

// ── Nav definition ────────────────────────────────────────────────────────────
interface NavItem {
  href:       string
  labelKey:   string
  Icon:       React.ElementType
  group:      string
  adminOnly?: boolean
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
  { href: '/analyst',             labelKey: 'nav.analyst',     Icon: BrainCircuit,    group: 'analysis' },

  { href: '/users',               labelKey: 'nav.users',       Icon: Users,           group: 'system',  adminOnly: true },
  { href: '/config',              labelKey: 'nav.config',      Icon: Settings,        group: 'system' },
]

const GROUPS = ['operation', 'data', 'purchasing', 'analysis', 'system']

export default function Sidebar() {
  const path    = usePathname()
  const router  = useRouter()
  const user    = getUser()
  const { collapsed, toggle } = useSidebar()
  const { advancedMode, setAdvancedMode } = useBusinessProfile()
  const { t, lang, setLang } = useLanguage()

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
              {items.map(({ href, labelKey, Icon }) => {
                const active = path === href || path.startsWith(`${href}/`)
                const label = t(labelKey)
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

        {/* Advanced mode toggle */}
        {!collapsed && (
          <div style={{ marginTop: 8, padding: '0 10px' }}>
            <button
              onClick={() => setAdvancedMode(!advancedMode)}
              style={{
                all: 'unset', cursor: 'pointer', width: '100%',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '6px 8px', borderRadius: 7, fontSize: 11,
                color: advancedMode ? 'var(--accent)' : 'var(--dim)',
                background: advancedMode ? 'var(--accent-dim)' : 'transparent',
                border: `1px solid ${advancedMode ? 'rgba(129,140,248,0.3)' : 'var(--border)'}`,
                transition: 'all 0.15s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <FlaskConical size={11} />
                <span style={{ fontWeight: 600 }}>{t('sidebar.advanced_mode')}</span>
              </div>
              <div style={{
                width: 28, height: 14, borderRadius: 7,
                background: advancedMode ? 'var(--accent)' : 'var(--border)',
                position: 'relative', transition: 'background 0.2s', flexShrink: 0,
              }}>
                <span style={{
                  position: 'absolute', top: 2,
                  left: advancedMode ? 16 : 2,
                  width: 10, height: 10, borderRadius: '50%',
                  background: '#fff', transition: 'left 0.2s',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                }} />
              </div>
            </button>
            {advancedMode && (
              <div style={{ fontSize: 10, color: 'var(--dim)', marginTop: 4, padding: '0 2px', lineHeight: 1.4 }}>
                {t('sidebar.advanced_desc')}
              </div>
            )}
          </div>
        )}

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
                  <div style={{ fontSize: 11, color: 'var(--dim)', textTransform: 'capitalize' }}>{user.role}</div>
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
  )
}

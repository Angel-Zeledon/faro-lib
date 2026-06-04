'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  LayoutDashboard, Database, TrendingUp, Package,
  BrainCircuit, BarChart2, Settings, Zap, LogOut, User, Users,
  ChevronLeft, ChevronRight, FileText, Target, SlidersHorizontal,
  ShoppingCart, Truck, Sun, Cog, Store, Factory, FlaskConical,
  ChevronDown,
} from 'lucide-react'
import clsx from 'clsx'
import { getUser, clearAuth } from '@/lib/auth'
import { authLogout } from '@/lib/api'
import { useSidebar } from '@/contexts/SidebarContext'
import { useBusinessProfile, PROFILE_NAV_RULES, PROFILE_TERMS } from '@/contexts/BusinessProfileContext'
import type { BusinessProfile } from '@/lib/types'

// ── Profile icon map ─────────────────────────────────────────────────────────
const PROFILE_ICON: Record<BusinessProfile, React.ElementType> = {
  retail:       Store,
  distributor:  Truck,
  manufacturer: Factory,
}

// ── Nav definition ────────────────────────────────────────────────────────────
interface NavItem {
  href:       string
  label:      string
  Icon:       React.ElementType
  group:      string
  adminOnly?: boolean
  advanced?:  boolean   // only shown in advanced mode
}

const NAV: NavItem[] = [
  { href: '/hoy',              label: 'Hoy',              Icon: Sun,              group: 'Intelligence' },
  { href: '/quick-start',      label: 'Inicio Rápido',    Icon: Zap,              group: 'Intelligence' },
  { href: '/dashboard',        label: 'Dashboard',        Icon: LayoutDashboard,  group: 'Intelligence', advanced: true },
  { href: '/data',             label: 'Datos',            Icon: Database,         group: 'Intelligence' },
  { href: '/forecast',         label: 'Forecast Studio',  Icon: TrendingUp,       group: 'Studio',       advanced: true },
  { href: '/inventory',        label: 'Inventario',       Icon: ShoppingCart,     group: 'Studio' },
  { href: '/inventory/roi',    label: 'Impacto & ROI',    Icon: BarChart2,        group: 'Studio' },
  { href: '/inventory/suppliers', label: 'Proveedores',   Icon: Truck,            group: 'Studio' },
  { href: '/produccion',       label: 'Producción',       Icon: Cog,              group: 'Studio' },
  { href: '/skus',             label: 'SKU Intelligence', Icon: Package,          group: 'Studio',       advanced: true },
  { href: '/reports',          label: 'Reportes',         Icon: FileText,         group: 'Studio',       advanced: true },
  { href: '/analyst',          label: 'AI Analyst',       Icon: BrainCircuit,     group: 'Insights' },
  { href: '/documents',        label: 'Documentos',       Icon: FileText,         group: 'Insights',     advanced: true },
  { href: '/accuracy',         label: 'Precisión',        Icon: Target,           group: 'Insights',     advanced: true },
  { href: '/users',            label: 'Usuarios',         Icon: Users,            group: 'Sistema',      adminOnly: true },
  { href: '/config',           label: 'Configuración',    Icon: Settings,         group: 'Sistema' },
  { href: '/settings',         label: 'Ajustes',          Icon: SlidersHorizontal, group: 'Sistema' },
]

const GROUPS = ['Intelligence', 'Studio', 'Insights', 'Sistema']

export default function Sidebar() {
  const path    = usePathname()
  const router  = useRouter()
  const user    = getUser()
  const { collapsed, toggle } = useSidebar()
  const { profile, advancedMode, setAdvancedMode } = useBusinessProfile()

  function handleLogout() {
    authLogout().catch(() => {})
    clearAuth()
    router.replace('/login')
  }

  // Filter nav items based on profile and advanced mode
  const visibleNav = NAV.filter(item => {
    // Admin-only filter
    if (item.adminOnly && user?.role !== 'admin') return false

    // Profile-based visibility
    const allowedProfiles = PROFILE_NAV_RULES[item.href]
    if (allowedProfiles && profile && !allowedProfiles.includes(profile)) return false

    // Advanced-only items hidden unless advanced mode is on
    if (item.advanced && !advancedMode) return false

    return true
  })

  const ProfileIcon = profile ? PROFILE_ICON[profile] : FlaskConical

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
              {profile ? PROFILE_TERMS[profile].profile_desc.split('·')[0].trim() : 'Inventario Inteligente'}
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
                  {group}
                </div>
              )}
              {items.map(({ href, label, Icon }) => {
                const active = path === href || path.startsWith(`${href}/`)
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
                <span style={{ fontWeight: 600 }}>Modo avanzado</span>
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
                Wizard completo, métricas técnicas y configuración ML activados.
              </div>
            )}
          </div>
        )}

        {/* Collapse toggle */}
        <button
          onClick={toggle}
          title={collapsed ? 'Expandir' : 'Colapsar'}
          style={{
            all: 'unset', cursor: 'pointer', marginTop: 8,
            display: 'flex', alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            gap: collapsed ? 0 : 8, width: '100%',
            padding: collapsed ? '8px 0' : '8px 10px', borderRadius: 7,
            color: 'var(--dim)', fontSize: 12, transition: 'all 0.15s',
          }}
        >
          {collapsed ? <ChevronRight size={14} /> : <><ChevronLeft size={14} /><span>Colapsar</span></>}
        </button>
      </nav>

      {/* Profile + User footer */}
      <div style={{ borderTop: '1px solid var(--border)' }}>

        {/* Profile indicator */}
        {!collapsed && profile && (
          <Link href="/perfil" style={{ textDecoration: 'none' }}>
            <div style={{
              padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 8,
              borderBottom: '1px solid var(--border)', cursor: 'pointer',
              transition: 'background 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{
                width: 20, height: 20, borderRadius: 5, flexShrink: 0,
                background: 'rgba(129,140,248,0.12)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <ProfileIcon size={11} color="var(--accent)" />
              </div>
              <span style={{ fontSize: 11, color: 'var(--dim)', flex: 1 }}>
                {PROFILE_TERMS[profile].profile_label}
              </span>
              <ChevronDown size={10} color="var(--dim)" style={{ transform: 'rotate(-90deg)' }} />
            </div>
          </Link>
        )}

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
                  title="Cerrar sesión"
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

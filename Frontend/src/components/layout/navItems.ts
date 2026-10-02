'use client'
import type { ElementType } from 'react'
import {
  TrendingUp, Package, BrainCircuit, Users, User,
  ShoppingCart, Truck, Upload, ClipboardList, History,
  FlaskConical, ListChecks, MessageSquare, Target, Clock, Code2, ServerCog,
  ScrollText,
} from 'lucide-react'

// ── Nav definition ────────────────────────────────────────────────────────────
export interface NavItem {
  href:       string
  labelKey:   string
  Icon:       ElementType
  group:      string
  adminOnly?: boolean
  /** Sibling routes this one entry stands for, so the item still reads as
   *  active while the user is on a tab that is not `href`. */
  alsoActive?: string[]
}

export const NAV: NavItem[] = [
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

export const GROUPS = ['operation', 'data', 'purchasing', 'analysis', 'system']

/** Role is the only thing that hides an entry (there are no plan locks). */
export function visibleNavFor(role: string | undefined | null): NavItem[] {
  return NAV.filter(item => !(item.adminOnly && role !== 'admin'))
}

/** True when `path` is this entry's route, one of its sub-routes, or one of
 *  the sibling routes it stands for. */
export function navItemMatches(item: NavItem, path: string): boolean {
  const matches = (p: string) => path === p || path.startsWith(`${p}/`)
  return matches(item.href) || (item.alsoActive?.some(matches) ?? false)
}

'use client'
import type { ElementType } from 'react'
import {
  TrendingUp, Package, MessagesSquare, Users, User,
  ShoppingCart, Truck, Upload, ClipboardList, History, Database,
  FlaskConical, ListChecks, MessageSquare, Target, Clock, Code2, ServerCog,
  ScrollText, Settings,
} from 'lucide-react'

// ── Screen registry ───────────────────────────────────────────────────────────
//
// Every app screen, once. The sidebar used to list all of them (18 entries in
// five groups), which made the daily screens hard to find among the ones a
// buyer opens twice a year. Now the sidebar carries only the daily screens and
// each secondary screen names the sidebar entry it lives under (`parent`), so:
//   · the sidebar and the phone tab bar highlight that parent while you are on
//     the secondary screen (/escenarios lights up Pronósticos);
//   · the desktop top bar shows a breadcrumb back to it ("Configuración ›
//     Usuarios") and the phone header shows a back arrow;
//   · the command palette still lists every screen (components/command).
// No route was removed or renamed — users have shared these URLs.

export interface Screen {
  href:       string
  labelKey:   string
  Icon:       ElementType
  /** Hidden from non-admins wherever it is listed (there are no plan locks). */
  adminOnly?: boolean
  /** The primary entry (sidebar / tab bar) this screen is reached from. */
  parent?:    string
}

export const SETTINGS_HREF = '/configuracion'

export const SCREENS: Screen[] = [
  // ── Daily screens: the sidebar ─────────────────────────────────────────────
  { href: '/compras',               labelKey: 'nav.hoy',             Icon: ShoppingCart },
  { href: '/pedidos',               labelKey: 'nav.orders',          Icon: ClipboardList },
  { href: '/inventario',            labelKey: 'nav.inventory',       Icon: Package },
  { href: '/proveedores',           labelKey: 'nav.suppliers',       Icon: Truck },
  { href: '/pronosticos',           labelKey: 'nav.skus',            Icon: TrendingUp },
  { href: '/asistente',             labelKey: 'nav.analyst',         Icon: MessagesSquare },
  { href: SETTINGS_HREF,            labelKey: 'nav.config',          Icon: Settings },

  // ── Under Inventario ───────────────────────────────────────────────────────
  // Kept flat as /configurar-inventario rather than nested under /inventario,
  // so it can never inherit a layout that screen does not want.
  { href: '/configurar-inventario', labelKey: 'nav.inventory_setup', Icon: ListChecks,   parent: '/inventario' },

  // ── Under Pronósticos: the analysis tabs (ANALYSIS_TABS below) ─────────────
  { href: '/escenarios',            labelKey: 'nav.scenarios',       Icon: FlaskConical, parent: '/pronosticos' },
  { href: '/impacto',               labelKey: 'nav.roi',             Icon: Target,       parent: '/pronosticos' },
  { href: '/historial',             labelKey: 'nav.sessions',        Icon: History,      parent: '/pronosticos' },

  // ── Under Configuración (the /configuracion hub lists them) ────────────────
  { href: '/mi-cuenta',             labelKey: 'nav.account',         Icon: User,         parent: SETTINGS_HREF },
  { href: '/usuarios',              labelKey: 'nav.users',           Icon: Users,        parent: SETTINGS_HREF, adminOnly: true },
  // "Subir mis ventas" and "mis archivos" are one errand; the two routes are
  // tabs of each other (components/layout/DataTabs.tsx).
  { href: '/ventas',                labelKey: 'nav.data',            Icon: Upload,       parent: SETTINGS_HREF },
  { href: '/archivos',              labelKey: 'data.page_title',     Icon: Database,     parent: SETTINGS_HREF },
  { href: '/automatizacion',        labelKey: 'nav.automation',      Icon: Clock,        parent: SETTINGS_HREF, adminOnly: true },
  // NOT adminOnly: an analyst is exactly who wires a customer's own system up
  // to the public API, and the page only ever acts with the key the reader
  // pastes into it — never with their session.
  { href: '/api',                   labelKey: 'nav.api',             Icon: Code2,        parent: SETTINGS_HREF },
  // adminOnly hides it from an analyst; the INSTANCE tab inside it is gated
  // again by INSTANCE_ADMIN_EMAILS, because `admin` is a role inside a tenant
  // and the deployment's credentials are not a tenant's to read.
  { href: '/instalacion',           labelKey: 'nav.installation',    Icon: ServerCog,    parent: SETTINGS_HREF, adminOnly: true },
  // The other half of the bell, which only carries what needs a decision.
  // Reached from the bell's "see all" link and from Configuración.
  { href: '/actividad',             labelKey: 'nav.activity',        Icon: ScrollText,   parent: SETTINGS_HREF },

  // ── No parent: reached from the top bar's messages icon (and "Más") ────────
  { href: '/mensajes',              labelKey: 'nav.messages',        Icon: MessageSquare },
]

/** The sidebar's daily screens, in order. Configuración is pinned separately
 *  at the foot of the rail (SETTINGS_ITEM). */
const PRIMARY = ['/compras', '/pedidos', '/inventario', '/proveedores', '/pronosticos', '/asistente']

const byHref = (href: string) => SCREENS.find(s => s.href === href)!

export const NAV: Screen[] = PRIMARY.map(byHref)
export const SETTINGS_ITEM: Screen = byHref(SETTINGS_HREF)

/** Pronósticos and the three analysis screens reached from it, shown as one
 *  tab strip at the top of all four (components/layout/SectionTabs.tsx). */
export const ANALYSIS_TABS: Screen[] =
  ['/pronosticos', '/escenarios', '/impacto', '/historial'].map(byHref)

/** Role is the only thing that hides an entry (there are no plan locks). */
export function canSee(screen: Screen, role: string | undefined | null): boolean {
  return !(screen.adminOnly && role !== 'admin')
}

const routeMatches = (href: string, path: string) => path === href || path.startsWith(`${href}/`)

/** The registered screen `path` belongs to (sub-routes included). */
export function screenFor(path: string): Screen | undefined {
  return SCREENS.find(s => routeMatches(s.href, path))
}

/** True when `path` is this entry's route, one of its sub-routes, or a
 *  secondary screen that lives under it. */
export function navItemMatches(item: Screen, path: string): boolean {
  if (routeMatches(item.href, path)) return true
  return screenFor(path)?.parent === item.href
}

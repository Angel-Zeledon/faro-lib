'use client'
import type { ElementType } from 'react'
import {
  TrendingUp, Package, MessagesSquare, Users, User,
  ShoppingCart, Truck, Upload, ClipboardList, History, Database,
  FlaskConical, ListChecks, MessageSquare, Target, Clock, Code2, ServerCog,
  ScrollText, Settings, ScanLine, ClipboardCheck, CreditCard,
} from 'lucide-react'
import { has, UNKNOWN_FACTS, type TenantFacts } from '@/hooks/useTenantFacts'

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
  /** Presentation rule: drawn in the sidebar, tab strips and settings hub only
   *  when this holds for what the tenant already has (hooks/useTenantFacts).
   *  Never a lock — the route stays reachable by URL and from the command
   *  palette, and the screen you are on is always drawn. Unknown counts must
   *  answer true (`has` does). */
  visibleWhen?: (facts: TenantFacts) => boolean
}

export const SETTINGS_HREF = '/configuracion'

export const SCREENS: Screen[] = [
  // ── Daily screens: the sidebar ─────────────────────────────────────────────
  { href: '/compras',               labelKey: 'nav.hoy',             Icon: ShoppingCart },
  { href: '/pedidos',               labelKey: 'nav.orders',          Icon: ClipboardList },
  { href: '/inventario',            labelKey: 'nav.inventory',       Icon: Package },
  { href: '/proveedores',           labelKey: 'nav.suppliers',       Icon: Truck,        visibleWhen: f => has(f.suppliers) },
  { href: '/pronosticos',           labelKey: 'nav.skus',            Icon: TrendingUp },
  { href: '/asistente',             labelKey: 'nav.analyst',         Icon: MessagesSquare },
  { href: SETTINGS_HREF,            labelKey: 'nav.config',          Icon: Settings },

  // ── Under Inventario ───────────────────────────────────────────────────────
  // Kept flat as /configurar-inventario rather than nested under /inventario,
  // so it can never inherit a layout that screen does not want.
  { href: '/configurar-inventario', labelKey: 'nav.inventory_setup', Icon: ListChecks,   parent: '/inventario' },
  // Counted with the phone, from Inventario's menu and the command palette. Not
  // a sidebar entry: it is a task done now and then, not a daily screen.
  { href: '/conteo-fisico',         labelKey: 'count.page_title',    Icon: ScanLine,     parent: '/inventario' },

  // ── Under Pronósticos: the analysis tabs (ANALYSIS_TABS below) ─────────────
  { href: '/escenarios',            labelKey: 'nav.scenarios',       Icon: FlaskConical, parent: '/pronosticos', visibleWhen: f => has(f.completedSessions) },
  { href: '/impacto',               labelKey: 'nav.roi',             Icon: Target,       parent: '/pronosticos', visibleWhen: f => has(f.completedSessions) },
  { href: '/historial',             labelKey: 'nav.sessions',        Icon: History,      parent: '/pronosticos', visibleWhen: f => has(f.completedSessions, 2) },

  // ── Under Configuración (the /configuracion hub lists them) ────────────────
  { href: '/mi-cuenta',             labelKey: 'nav.account',         Icon: User,         parent: SETTINGS_HREF },
  // The plan and its payment; where Stripe / PayPal send the buyer back to.
  { href: '/facturacion',           labelKey: 'nav.billing',         Icon: CreditCard,   parent: SETTINGS_HREF },
  { href: '/usuarios',              labelKey: 'nav.users',           Icon: Users,        parent: SETTINGS_HREF, adminOnly: true },
  // "Subir mis ventas" and "mis archivos" are one errand; the two routes are
  // tabs of each other (components/layout/DataTabs.tsx).
  { href: '/ventas',                labelKey: 'nav.data',            Icon: Upload,       parent: SETTINGS_HREF },
  { href: '/archivos',              labelKey: 'data.page_title',     Icon: Database,     parent: SETTINGS_HREF },
  // Who must approve which purchase orders. A row in the hub, never in the
  // sidebar; it only matters to a tenant that wants the workflow.
  { href: '/aprobaciones',          labelKey: 'nav.po_approval',     Icon: ClipboardCheck, parent: SETTINGS_HREF, adminOnly: true },
  { href: '/automatizacion',        labelKey: 'nav.automation',      Icon: Clock,        parent: SETTINGS_HREF, adminOnly: true },
  // NOT adminOnly: an analyst is exactly who wires a customer's own system up
  // to the public API, and the page only ever acts with the key the reader
  // pastes into it — never with their session.
  { href: '/api',                   labelKey: 'nav.api',             Icon: Code2,        parent: SETTINGS_HREF, visibleWhen: f => has(f.apiKeys) },
  // adminOnly hides it from an analyst; the INSTANCE tab inside it is gated
  // again by INSTANCE_ADMIN_EMAILS, because `admin` is a role inside a tenant
  // and the deployment's credentials are not a tenant's to read.
  { href: '/instalacion',           labelKey: 'nav.installation',    Icon: ServerCog,    parent: SETTINGS_HREF, adminOnly: true, visibleWhen: f => f.isInstanceOperator !== false },
  // The other half of the bell, which only carries what needs a decision.
  // Reached from the bell's "see all" link and from Configuración.
  { href: '/actividad',             labelKey: 'nav.activity',        Icon: ScrollText,   parent: SETTINGS_HREF, visibleWhen: f => has(f.teammates) },

  // ── No parent: reached from the top bar's messages icon (and "Más") ────────
  { href: '/mensajes',              labelKey: 'nav.messages',        Icon: MessageSquare, visibleWhen: f => has(f.teammates) },
]

/** The sidebar's daily screens, in order. Configuración is pinned separately
 *  at the foot of the rail (SETTINGS_ITEM). */
const PRIMARY = ['/compras', '/pedidos', '/inventario', '/proveedores', '/pronosticos', '/asistente']

/** Standalone tools that belong to no single flow (a what-if, an upload, the
 *  inbox, the activity log). The desktop sidebar lists them under the daily
 *  screens; each lights itself when open. */
const TOOLS = ['/ventas', '/escenarios', '/mensajes', '/actividad']

const byHref = (href: string) => SCREENS.find(s => s.href === href)!

export const NAV: Screen[] = PRIMARY.map(byHref)
export const TOOLS_NAV: Screen[] = TOOLS.map(byHref)
export const SETTINGS_ITEM: Screen = byHref(SETTINGS_HREF)

/** Pronósticos and the three analysis screens reached from it, shown as one
 *  tab strip at the top of all four (components/layout/SectionTabs.tsx). */
export const ANALYSIS_TABS: Screen[] =
  ['/pronosticos', '/escenarios', '/impacto', '/historial'].map(byHref)

/** Role and the screen's `visibleWhen` rule decide whether an entry is DRAWN
 *  (there are no plan locks and nothing is ever blocked: see Screen.visibleWhen).
 *  Without `facts` the rule is skipped, i.e. the entry is shown. */
export function canSee(
  screen: Screen, role: string | undefined | null, facts: TenantFacts = UNKNOWN_FACTS,
): boolean {
  if (screen.adminOnly && role !== 'admin') return false
  return screen.visibleWhen ? screen.visibleWhen(facts) : true
}

/** `canSee`, except the screen the user is standing on is always drawn, so a
 *  hidden entry reached by URL never leaves the navigation without a "you are
 *  here". */
export function drawn(
  screen: Screen, role: string | undefined | null, facts: TenantFacts, path: string,
): boolean {
  return routeMatches(screen.href, path) || canSee(screen, role, facts)
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

/** The primary entries (sidebar / tab bar), Configuración included. */
const PRIMARY_HREFS = [...PRIMARY, ...TOOLS, SETTINGS_HREF]

/** The primary entry `path` itself belongs to, or null on a secondary screen. */
export function primaryFor(path: string): string | null {
  return PRIMARY_HREFS.find(h => routeMatches(h, path)) ?? null
}

/**
 * Which primary entry to light up. On a primary screen, that screen. On a
 * secondary one (no menu entry of its own), the primary the user came from —
 * /configurar-inventario opened from Inventario lights Inventario, not
 * Configuración — and only when nothing is known (a pasted link, a reload),
 * the screen's registered parent.
 */
export function activePrimary(path: string, origin: string | null): string | null {
  return primaryFor(path) ?? origin ?? screenFor(path)?.parent ?? null
}

const ORIGIN_KEY = 'stockai_nav_origin'

/** Remember the last primary screen visited (per tab, sessionStorage). */
export function rememberOrigin(path: string): string | null {
  const primary = primaryFor(path)
  try {
    if (primary) sessionStorage.setItem(ORIGIN_KEY, primary)
    return primary ?? sessionStorage.getItem(ORIGIN_KEY)
  } catch {
    return primary
  }
}

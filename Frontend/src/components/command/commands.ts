import {
  ShoppingCart, ClipboardList, Package, Boxes, Truck, TrendingUp, Database,
  History, BrainCircuit, FlaskConical, Plug, Settings, Users, KeyRound,
  Upload, Plus, SunMoon, Languages, type LucideIcon,
} from 'lucide-react'
import type { InventoryStatusItem } from '@/lib/types'

/**
 * The Ctrl-K palette's command registry.
 *
 * Everything a command needs is data, so adding one is a single line in
 * `COMMANDS` (plus its i18n keys) — no branching anywhere else. Two kinds of
 * command exist: `href` ones, which the palette routes to, and `run` ones,
 * which get the small context below and do their work in place.
 */

export interface CommandContext {
  navigate:    (href: string) => void
  toggleTheme: () => void
  lang:        'es' | 'en'
  setLang:     (l: 'es' | 'en') => void
}

export interface Command {
  id: string
  /** 'navigate' groups under "Ir a…", 'action' under "Acciones". */
  group: 'navigate' | 'action'
  /** i18n key for the label the user reads. */
  labelKey: string
  /**
   * i18n key holding a comma-separated synonym list ("ordenes, oc, compras").
   * Never rendered — it only widens what the user can type to reach the
   * command, so the Spanish stays in the locale layer with the rest of the
   * copy instead of being hardcoded here.
   */
  aliasKey?: string
  Icon: LucideIcon
  /** Backend `Feature` value gating this command; absent = always available. */
  feature?: string
  adminOnly?: boolean
  /** Hidden for viewers, who cannot perform mutating actions. */
  writerOnly?: boolean
  href?: string
  run?: (ctx: CommandContext) => void
}

export const COMMANDS: Command[] = [
  // ── Go to ──────────────────────────────────────────────────────────────────
  { id: 'go.hoy',          group: 'navigate', href: '/hoy',                 labelKey: 'nav.hoy',           aliasKey: 'cmd.alias.hoy',        Icon: ShoppingCart },
  { id: 'go.orders',       group: 'navigate', href: '/pedidos',             labelKey: 'nav.orders',        aliasKey: 'cmd.alias.orders',     Icon: ClipboardList },
  { id: 'go.skus',         group: 'navigate', href: '/skus',                labelKey: 'nav.skus',          aliasKey: 'cmd.alias.skus',       Icon: Package },
  { id: 'go.inventory',    group: 'navigate', href: '/inventory',           labelKey: 'nav.inventory',     aliasKey: 'cmd.alias.inventory',  Icon: Boxes },
  { id: 'go.suppliers',    group: 'navigate', href: '/inventory/suppliers', labelKey: 'nav.suppliers',     aliasKey: 'cmd.alias.suppliers',  Icon: Truck },
  { id: 'go.roi',          group: 'navigate', href: '/inventory/roi',       labelKey: 'nav.roi',           aliasKey: 'cmd.alias.roi',        Icon: TrendingUp },
  { id: 'go.data',         group: 'navigate', href: '/data',                labelKey: 'nav.data',          aliasKey: 'cmd.alias.data',       Icon: Database },
  { id: 'go.sessions',     group: 'navigate', href: '/sessions',            labelKey: 'nav.sessions',      aliasKey: 'cmd.alias.sessions',   Icon: History },
  { id: 'go.analyst',      group: 'navigate', href: '/analyst',             labelKey: 'nav.analyst',       aliasKey: 'cmd.alias.analyst',    Icon: BrainCircuit, feature: 'ai_analyst' },
  { id: 'go.scenarios',    group: 'navigate', href: '/scenarios',           labelKey: 'nav.scenarios',     aliasKey: 'cmd.alias.scenarios',  Icon: FlaskConical, feature: 'event_simulator' },
  { id: 'go.integrations', group: 'navigate', href: '/integraciones',       labelKey: 'nav.integrations',                                    Icon: Plug,         feature: 'integrations' },
  { id: 'go.config',       group: 'navigate', href: '/config',              labelKey: 'nav.config',        aliasKey: 'cmd.alias.config',     Icon: Settings },
  { id: 'go.users',        group: 'navigate', href: '/users',               labelKey: 'nav.users',                                           Icon: Users,        adminOnly: true },
  { id: 'go.api',          group: 'navigate', href: '/settings',            labelKey: 'nav.settings',                                        Icon: KeyRound,     adminOnly: true, feature: 'api_access' },

  // ── Do ─────────────────────────────────────────────────────────────────────
  { id: 'act.upload',    group: 'action', href: '/quick-start',   labelKey: 'cmd.upload_sales',    aliasKey: 'cmd.alias.upload',       Icon: Upload,    writerOnly: true },
  // The manual-PO modal is local state on /pedidos, so the intent travels in
  // the URL; the page opens the modal and cleans the query string up.
  { id: 'act.new_order', group: 'action', href: '/pedidos?new=1', labelKey: 'cmd.create_order',    aliasKey: 'cmd.alias.create_order', Icon: Plus,      writerOnly: true },
  { id: 'act.theme',     group: 'action',                         labelKey: 'cmd.toggle_theme',    aliasKey: 'cmd.alias.theme',        Icon: SunMoon,   run: c => c.toggleTheme() },
  { id: 'act.language',  group: 'action',                         labelKey: 'cmd.toggle_language', aliasKey: 'cmd.alias.language',     Icon: Languages, run: c => c.setLang(c.lang === 'es' ? 'en' : 'es') },
]

/**
 * Locked features are hidden, never padlocked — the same choice the nav made,
 * for the same reason: a palette that lists four things the tenant cannot do
 * teaches them about their plan instead of about their work.
 */
export function visibleCommands(has: (f: string) => boolean, role?: string): Command[] {
  return COMMANDS.filter(c => {
    if (c.adminOnly && role !== 'admin') return false
    if (c.writerOnly && role === 'viewer') return false
    if (c.feature && !has(c.feature)) return false
    return true
  })
}

// ── Ranking ──────────────────────────────────────────────────────────────────
// Commands and SKUs compete in one list, so their scores share a scale. The
// rule that matters: a SKU code the user typed out beats every command, and a
// SKU whose code merely *contains* the query does not. That keeps "ped" on
// Pedidos while "PED-4471" still lands on the product.

// Accent-insensitive: nobody types "pronostico" with the accent in a hurry,
// and half the SKU catalogs in the region carry them.
const DIACRITICS = /[\u0300-\u036f]/g
const norm = (s: string) =>
  s.toLowerCase().trim().normalize('NFD').replace(DIACRITICS, '')

/** `t` echoes the key back when a translation is missing; a key is not a
 *  synonym, so an unresolved alias list is simply no aliases. */
function aliasesOf(cmd: Command, t: (k: string) => string): string[] {
  if (!cmd.aliasKey) return []
  const raw = t(cmd.aliasKey)
  if (raw === cmd.aliasKey) return []
  return norm(raw).split(',').map(a => a.trim()).filter(Boolean)
}

export function scoreCommand(cmd: Command, query: string, t: (k: string) => string): number {
  const q = norm(query)
  if (!q) return 0
  const label = norm(t(cmd.labelKey))
  if (label === q) return 900
  if (label.startsWith(q)) return 700
  // Word-start inside a multi-word label ("Nueva orden" ← "orden").
  if (label.split(/\s+/).some(w => w.startsWith(q))) return 550
  const aliases = aliasesOf(cmd, t)
  if (aliases.some(a => a === q || a.startsWith(q))) return 500
  if (label.includes(q) || aliases.some(a => a.includes(q))) return 300
  return 0
}

export function scoreSku(item: InventoryStatusItem, query: string): number {
  const q = norm(query)
  if (!q) return 0
  const sku  = norm(item.sku)
  const name = norm(item.display_name ?? '')
  if (sku === q) return 1000
  // A four-character head of a code is someone typing a code; one or two
  // letters is someone on their way to a word, and words are what commands
  // are made of — so short prefixes score below a command's 700.
  if (sku.startsWith(q)) return q.length >= 4 ? 800 : 650
  if (name.startsWith(q)) return 600
  if (sku.includes(q) || name.includes(q)) return 400
  const other = [item.category, item.brand, item.barcode, item.supplier]
  if (other.some(v => v && norm(v).includes(q))) return 200
  return 0
}

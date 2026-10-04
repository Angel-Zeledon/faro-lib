// The shape of a help-center page. Plain data: the pages under src/i18n/docs/
// own the wording, components/docs/ owns the rendering.
//
// Inline markup inside any `text` / list item / cell (components/docs/inline.tsx):
//   **bold**      strong
//   `code`        inline code — routes, column names, stored values
//   [label](href) a link. `/docs/...` and `/...` stay on the site, `http...`
//                 leaves it, and `app:/route` opens the APP (appHref): the app
//                 can live on its own origin, so a screen is never linked
//                 relatively from the help center.
import type { Lang } from '@/i18n/translations'

export type DocShot =
  | 'panel' | 'inventory' | 'pedidos' | 'mensajes' | 'ventas' | 'configurar'
  | 'proveedores' | 'scorecard' | 'forecast' | 'pattern' | 'escenarios' | 'impacto'
  | 'historial' | 'asistente' | 'usuarios' | 'cuenta' | 'automatizacion' | 'api'
  | 'instalacion'

export type DocBlock =
  | { t: 'p'; text: string }
  /** A section heading. `id` is the anchor, stable across languages. */
  | { t: 'h2'; id: string; text: string }
  | { t: 'h3'; id: string; text: string }
  | { t: 'ul'; items: string[] }
  /** Numbered steps — only for things that really happen in order. */
  | { t: 'steps'; items: string[] }
  /** Term / meaning pairs: a screen's fields, a column's meaning. */
  | { t: 'dl'; items: [string, string][] }
  | { t: 'note'; tone: 'info' | 'warn'; title?: string; text: string }
  /** A real screenshot from Frontend/public/shot-<key>(-en).png. */
  | { t: 'shot'; key: DocShot; alt: string; caption?: string }
  | { t: 'table'; head: string[]; rows: string[][] }
  | { t: 'code'; text: string }
  /** The four stock signals plus "no data", rendered with their real colours. */
  | { t: 'signals'; items: { signal: 'now' | 'soon' | 'ok' | 'over' | 'none'; label: string; text: string }[] }
  /** Release notes: a dated group of entries. */
  | { t: 'release'; date: string; title: string; items: string[] }

export interface DocPage {
  /** H1 and <title>. */
  title: string
  /** Sidebar label when the title is too long for it. */
  nav?: string
  /** One or two sentences: the lead under the H1, the meta description and
   *  the search snippet. */
  description: string
  blocks: DocBlock[]
}

/** A section file's export: every page, in both languages, or no build. */
export type DocSectionContent<K extends string> = Record<Lang, Record<K, DocPage>>

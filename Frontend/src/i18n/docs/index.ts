// Every help-center page in both languages, plus the pure helpers the route,
// the renderer and the search index share. No React here: the search index
// route (app/docs/search-index.json) runs it on the server at build time.
import type { Lang } from '@/i18n/translations'
import type { DocBlock, DocPage } from '@/i18n/docs/types'
import { DOC_ORDER, type DocPageId, type DocSectionSlug, sectionOf } from '@/i18n/docs/tree'
import { START } from '@/i18n/docs/start'
import { DAILY } from '@/i18n/docs/daily'
import { ANALYSIS } from '@/i18n/docs/analysis'
import { CONCEPTS } from '@/i18n/docs/concepts'
import { ASSISTANT } from '@/i18n/docs/assistant'
import { ADMIN } from '@/i18n/docs/admin'
import { INTEGRATIONS } from '@/i18n/docs/integrations'
import { TROUBLESHOOTING } from '@/i18n/docs/troubleshooting'
import { CHANGELOG } from '@/i18n/docs/changelog'

function merge(lang: Lang): Record<DocPageId, DocPage> {
  return {
    ...START[lang],
    ...DAILY[lang],
    ...ANALYSIS[lang],
    ...CONCEPTS[lang],
    ...ASSISTANT[lang],
    ...ADMIN[lang],
    ...INTEGRATIONS[lang],
    ...TROUBLESHOOTING[lang],
    ...CHANGELOG[lang],
  }
}

export const DOCS: Record<Lang, Record<DocPageId, DocPage>> = { es: merge('es'), en: merge('en') }

export interface DocHeading { id: string; text: string; level: 2 | 3 }

export function docHeadings(page: DocPage): DocHeading[] {
  const out: DocHeading[] = []
  for (const b of page.blocks) {
    if (b.t === 'h2') out.push({ id: b.id, text: stripInline(b.text), level: 2 })
    else if (b.t === 'h3') out.push({ id: b.id, text: stripInline(b.text), level: 3 })
    // A release is headed by its own <h3 id="rel-<date>"> (DocBody); on the
    // Novedades page those are the page's sections.
    else if (b.t === 'release') out.push({ id: `rel-${b.date}`, text: stripInline(b.title), level: 2 })
  }
  return out
}

/** The markup of components/docs/inline.tsx, removed: what a reader sees. */
export function stripInline(s: string): string {
  return s
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
}

function blockText(b: DocBlock): string {
  switch (b.t) {
    case 'p': case 'note': case 'code': return b.t === 'note' && b.title ? `${b.title}. ${b.text}` : b.text
    case 'h2': case 'h3': return b.text
    case 'ul': case 'steps': return b.items.join(' ')
    case 'dl': return b.items.map(([k, v]) => `${k}: ${v}`).join(' ')
    case 'shot': return b.caption ?? ''
    case 'table': return [b.head.join(' '), ...b.rows.map(r => r.join(' '))].join(' ')
    case 'signals': return b.items.map(i => `${i.label}: ${i.text}`).join(' ')
    case 'release': return `${b.title} ${b.items.join(' ')}`
  }
}

/** One search entry per heading-delimited chunk of a page, so a hit on
 *  "recibir parcial" lands on that part of the Pedidos page, not its top. */
export interface SearchEntry {
  /** Page id. */
  p: DocPageId
  /** Anchor inside the page, '' for the page's opening chunk. */
  a: string
  /** Page title. */
  t: string
  /** Heading of the chunk ('' for the opening one). */
  h: string
  /** Plain text of the chunk. */
  x: string
}

export function buildSearchIndex(lang: Lang): SearchEntry[] {
  const out: SearchEntry[] = []
  for (const id of DOC_ORDER) {
    const page = DOCS[lang][id]
    let cur: SearchEntry = { p: id, a: '', t: page.title, h: '', x: page.description }
    for (const b of page.blocks) {
      if (b.t === 'h2' || b.t === 'h3' || b.t === 'release') {
        out.push(cur)
        cur = b.t === 'release'
          ? { p: id, a: `rel-${b.date}`, t: page.title, h: stripInline(b.title), x: stripInline(b.items.join(' ')) }
          : { p: id, a: b.id, t: page.title, h: stripInline(b.text), x: '' }
        continue
      }
      cur.x += (cur.x ? ' ' : '') + stripInline(blockText(b))
    }
    out.push(cur)
  }
  return out
}

export function neighbours(id: DocPageId): { prev?: DocPageId; next?: DocPageId } {
  const i = DOC_ORDER.indexOf(id)
  return { prev: i > 0 ? DOC_ORDER[i - 1] : undefined, next: i < DOC_ORDER.length - 1 ? DOC_ORDER[i + 1] : undefined }
}

export function pagesOf(section: DocSectionSlug): DocPageId[] {
  return DOC_ORDER.filter(id => sectionOf(id) === section)
}

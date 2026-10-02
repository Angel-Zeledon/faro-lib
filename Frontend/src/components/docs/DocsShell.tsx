'use client'
// The help center's frame: the landing's nav and footer, a section sidebar on
// the left, the page in the middle and "On this page" on the right.
//
// The route (app/docs/[...slug]/page.tsx) renders this on the server with the
// CURRENT page in both languages, so the whole page is in the server HTML (in
// Spanish, the default) and switching language is instant. The rest of the
// catalogue never reaches the browser: the sidebar gets titles only, and the
// search index is fetched the first time somebody opens search.
import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, ChevronLeft, PanelLeft, Search, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import type { Lang } from '@/i18n/translations'
import type { DocPage } from '@/i18n/docs/types'
import { DOCS_CHROME, type DocsChromeCopy } from '@/i18n/docs/chrome'
import { DOC_TREE, DOCS_BASE, docHref, sectionOf, type DocPageId, type DocSectionSlug } from '@/i18n/docs/tree'
import type { DocHeading } from '@/i18n/docs/index'
import { LandingStyles } from '@/components/landing/primitives'
import { Nav, Footer, type ChromeProps } from '@/components/landing/chrome'
import { DocBody } from '@/components/docs/DocBody'
import { DocsSearch } from '@/components/docs/DocsSearch'
import { DOCS_CSS } from '@/components/docs/docsCss'

const CHROME: ChromeProps = { onHome: false, localAnchors: [] }

export type NavTitles = Record<Lang, Record<DocPageId, string>>

export interface DocsShellProps {
  titles: NavTitles
  /** Absent on the /docs index. */
  page?: {
    id: DocPageId
    content: Record<Lang, DocPage>
    headings: Record<Lang, DocHeading[]>
    prev?: DocPageId
    next?: DocPageId
  }
}

function sectionPages(slug: DocSectionSlug): DocPageId[] {
  const s = DOC_TREE.find(x => x.slug === slug)!
  return s.pages.length === 0 ? [slug as DocPageId] : s.pages.map(p => `${slug}/${p}` as DocPageId)
}

// ── Sidebar (also the phone sheet's body) ─────────────────────────────────────
function SidebarNav({ C, titles, current, onNavigate }: {
  C: DocsChromeCopy
  titles: Record<DocPageId, string>
  current?: DocPageId
  onNavigate?: () => void
}) {
  const currentSection = current ? sectionOf(current) : undefined
  const [open, setOpen] = useState<Set<DocSectionSlug>>(() => new Set<DocSectionSlug>([currentSection ?? 'primeros-pasos']))
  // A link from the page body to another section opens that section in the
  // sidebar too, so the reader always sees where they are.
  useEffect(() => {
    if (currentSection) setOpen(prev => (prev.has(currentSection) ? prev : new Set(prev).add(currentSection)))
  }, [currentSection])

  const toggle = (s: DocSectionSlug) => setOpen(prev => {
    const n = new Set(prev)
    if (n.has(s)) n.delete(s)
    else n.add(s)
    return n
  })

  return (
    <nav aria-label={C.sidebarLabel}>
      <Link href={DOCS_BASE} className="dc-nav-home" aria-current={current ? undefined : 'page'} onClick={onNavigate}>
        {C.label}
      </Link>
      {DOC_TREE.map(sec => {
        const pages = sectionPages(sec.slug)
        // A one-page section is a plain link, not a folder with one thing in it.
        if (sec.pages.length === 0) {
          return (
            <div key={sec.slug} className="dc-sec">
              <ul style={{ borderLeft: 0, margin: '2px 0 10px' }}>
                <li>
                  <Link href={docHref(pages[0])} aria-current={current === pages[0] ? 'page' : undefined} onClick={onNavigate}
                    style={{ fontWeight: 650, color: current === pages[0] ? undefined : 'var(--lp-text)', fontSize: 13.5, paddingLeft: 12 }}>
                    {C.sections[sec.slug].title}
                  </Link>
                </li>
              </ul>
            </div>
          )
        }
        const isOpen = open.has(sec.slug)
        const listId = `dc-sec-${sec.slug}`
        return (
          <div key={sec.slug} className="dc-sec">
            <button type="button" className="dc-sec-btn" aria-expanded={isOpen} aria-controls={listId} onClick={() => toggle(sec.slug)}>
              {C.sections[sec.slug].title}
              <ChevronRight size={15} aria-hidden />
            </button>
            {/* Collapsed lists stay in the HTML (hidden), so every page title
                is in the server render and in find-in-page. */}
            <ul id={listId} hidden={!isOpen}>
              {pages.map(id => (
                <li key={id}>
                  <Link href={docHref(id)} aria-current={current === id ? 'page' : undefined} onClick={onNavigate}>
                    {titles[id]}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}

// ── On this page ──────────────────────────────────────────────────────────────
function useActiveHeading(ids: string[]): string | undefined {
  const [active, setActive] = useState<string | undefined>(ids[0])
  useEffect(() => {
    if (ids.length === 0) return
    let queued = false
    const measure = () => {
      queued = false
      // The last heading whose top has passed the line under the nav.
      let cur: string | undefined = ids[0]
      for (const id of ids) {
        const el = document.getElementById(id)
        if (el && el.getBoundingClientRect().top <= 120) cur = id
      }
      // At the very bottom the last short sections can never reach that line.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) cur = ids[ids.length - 1]
      setActive(cur)
    }
    const onScroll = () => { if (!queued) { queued = true; requestAnimationFrame(measure) } }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [ids])
  return active
}

function Toc({ C, headings }: { C: DocsChromeCopy; headings: DocHeading[] }) {
  const ids = useMemo(() => headings.map(h => h.id), [headings])
  const active = useActiveHeading(ids)
  if (headings.length === 0) return <aside className="dc-toc" aria-hidden />
  return (
    <aside className="dc-toc">
      <nav aria-label={C.onThisPage}>
        <p className="dc-toc-head">{C.onThisPage}</p>
        <ol>
          {headings.map(h => (
            <li key={h.id} className={h.level === 3 ? 'is-l3' : undefined}>
              <a href={`#${h.id}`} aria-current={active === h.id ? 'true' : undefined}>{h.text}</a>
            </li>
          ))}
        </ol>
      </nav>
    </aside>
  )
}

// ── Shell ─────────────────────────────────────────────────────────────────────
export default function DocsShell({ titles, page }: DocsShellProps) {
  const { lang } = useLanguage()
  const C = DOCS_CHROME[lang]
  const T = titles[lang]
  const [searchOpen, setSearchOpen] = useState(false)
  const [sheetOpen, setSheetOpen] = useState(false)
  const lastFocus = useRef<HTMLElement | null>(null)

  const openSearch = useCallback(() => {
    lastFocus.current = document.activeElement as HTMLElement | null
    setSearchOpen(true)
  }, [])
  const closeSearch = useCallback(() => {
    setSearchOpen(false)
    lastFocus.current?.focus?.()
  }, [])

  // "/" and Ctrl/Cmd+K open search, as on every documentation site.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName))
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) {
        e.preventDefault()
        openSearch()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openSearch])

  useEffect(() => {
    if (!sheetOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSheetOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [sheetOpen])

  const doc = page?.content[lang]
  const headings = page?.headings[lang] ?? []
  const section = page ? sectionOf(page.id) : undefined
  const isSinglePageSection = section ? DOC_TREE.find(s => s.slug === section)!.pages.length === 0 : false

  return (
    <div className="lp dc" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: DOCS_CSS }} />
      <Nav {...CHROME} />

      <div className={`dc-frame${page ? '' : ' is-index'}`}>
        <aside className="dc-side">
          <button type="button" className="dc-search-btn" onClick={openSearch}>
            <Search size={15} aria-hidden />
            {C.search.open}
            <kbd aria-hidden>/</kbd>
          </button>
          <SidebarNav C={C} titles={T} current={page?.id} />
        </aside>

        <main className="dc-main" id="contenido">
          <div className="dc-bar">
            <button type="button" onClick={() => setSheetOpen(true)} aria-expanded={sheetOpen} aria-haspopup="dialog">
              <PanelLeft size={16} aria-hidden />
              <span>{section ? C.sections[section].title : C.menu}</span>
            </button>
            <button type="button" onClick={openSearch} aria-label={C.search.open}>
              <Search size={16} aria-hidden />
            </button>
          </div>

          {page && doc ? (
            <>
              <nav aria-label={C.breadcrumb} className="dc-crumbs">
                <ol>
                  <li><Link href="/">{C.home}</Link></li>
                  <li><Link href={DOCS_BASE}>{C.label}</Link></li>
                  {section && !isSinglePageSection && (
                    <li><Link href={`${DOCS_BASE}#${section}`}>{C.sections[section].title}</Link></li>
                  )}
                  <li><span aria-current="page">{doc.nav ?? doc.title}</span></li>
                </ol>
              </nav>
              <article className="dc-article">
                <h1 className="dc-h1">{doc.title}</h1>
                <p className="dc-lead">{doc.description}</p>
                {headings.length > 1 && (
                  <details className="dc-toc-fold">
                    <summary>{C.onThisPage}</summary>
                    <ol>
                      {headings.map(h => (
                        <li key={h.id} className={h.level === 3 ? 'is-l3' : undefined}><a href={`#${h.id}`}>{h.text}</a></li>
                      ))}
                    </ol>
                  </details>
                )}
                <DocBody blocks={doc.blocks} lang={lang} />
              </article>
              {(page.prev || page.next) && (
                <nav className="dc-pager" aria-label={`${C.prev} / ${C.next}`}>
                  {page.prev && (
                    <Link href={docHref(page.prev)} rel="prev">
                      <span><ChevronLeft size={12} aria-hidden style={{ verticalAlign: '-1px' }} /> {C.prev}</span>
                      <strong>{T[page.prev]}</strong>
                    </Link>
                  )}
                  {page.next && (
                    <Link href={docHref(page.next)} rel="next" className="is-next">
                      <span>{C.next} <ChevronRight size={12} aria-hidden style={{ verticalAlign: '-1px' }} /></span>
                      <strong>{T[page.next]}</strong>
                    </Link>
                  )}
                </nav>
              )}
            </>
          ) : (
            <DocsIndex C={C} titles={T} lang={lang} onSearch={openSearch} />
          )}
        </main>

        {page && <Toc C={C} headings={headings} />}
      </div>

      <Footer {...CHROME} />

      {sheetOpen && (
        <div className="dc-sheet" onClick={() => setSheetOpen(false)}>
          <div className="dc-sheet-panel" role="dialog" aria-modal="true" aria-label={C.menu} onClick={e => e.stopPropagation()}>
            <div className="dc-sheet-head">
              {C.menu}
              <button type="button" className="dc-search-close" onClick={() => setSheetOpen(false)} aria-label={C.closeMenu} style={{ minWidth: 44, minHeight: 44 }}>
                <X size={18} aria-hidden />
              </button>
            </div>
            <SidebarNav C={C} titles={T} current={page?.id} onNavigate={() => setSheetOpen(false)} />
          </div>
        </div>
      )}

      {searchOpen && <DocsSearch C={C} lang={lang} onClose={closeSearch} sectionTitle={id => C.sections[sectionOf(id)].title} />}
    </div>
  )
}

// ── /docs ─────────────────────────────────────────────────────────────────────
function DocsIndex({ C, titles, lang, onSearch }: {
  C: DocsChromeCopy
  titles: Record<DocPageId, string>
  lang: Lang
  onSearch: () => void
}) {
  return (
    <>
      <nav aria-label={C.breadcrumb} className="dc-crumbs">
        <ol>
          <li><Link href="/">{C.home}</Link></li>
          <li><span aria-current="page">{C.label}</span></li>
        </ol>
      </nav>
      <div className="dc-index-head dc-article">
        <h1 className="dc-h1">{C.title}</h1>
        <p className="dc-lead">{C.intro}</p>
      </div>
      <button type="button" className="dc-big-search" onClick={onSearch}>
        <Search size={18} aria-hidden />
        {C.search.placeholder}
        <kbd aria-hidden>/</kbd>
      </button>

      <div className="dc-sections">
        {DOC_TREE.map(sec => (
          <section key={sec.slug} id={sec.slug} className="dc-section-card" aria-labelledby={`h-${sec.slug}`}>
            <h2 id={`h-${sec.slug}`}>{C.sections[sec.slug].title}</h2>
            <p>{C.sections[sec.slug].blurb}</p>
            <ul>
              {sectionPages(sec.slug).map(id => (
                <li key={id}><Link href={docHref(id)}>{titles[id]}</Link></li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <div className="dc-extras">
        <div className="dc-extra">
          <h2>{C.manualTitle}</h2>
          <p>{C.manualBody}</p>
          <a href={`/stockai-manual-${lang}.pdf`} download>{C.manualCta}</a>
        </div>
        <div className="dc-extra">
          <h2>{C.apiTitle}</h2>
          <p>{C.apiBody}</p>
          <Link href="/desarrolladores">{C.apiCta}</Link>
        </div>
      </div>
    </>
  )
}

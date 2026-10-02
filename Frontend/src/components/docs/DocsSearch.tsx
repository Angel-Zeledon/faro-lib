'use client'
// Client-side search over the help center. The index is built on the server
// from the same typed pages the routes render (app/docs/search-index/[lang])
// and fetched the first time search opens, so a reader who never searches
// never downloads it. No external service, no library: a page set this size is
// a few hundred chunks, and a substring scan over them is instant.
import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import type { Lang } from '@/i18n/translations'
import type { DocsChromeCopy } from '@/i18n/docs/chrome'
import type { SearchEntry } from '@/i18n/docs/index'
import { docHref, type DocPageId } from '@/i18n/docs/tree'

// Cached per language for the life of the tab.
const cache: Partial<Record<Lang, SearchEntry[]>> = {}

/** Lower-case and accent-free, one output char per input char, so a match
 *  position in the folded text is the same position in the original. */
function fold(s: string): string {
  let out = ''
  for (const ch of s) {
    const base = ch.normalize('NFD')[0] ?? ch
    out += base.length === ch.length ? base.toLowerCase() : ch.toLowerCase()
  }
  return out
}

interface Hit { e: SearchEntry; score: number; snippet: string; start: number }

function search(index: SearchEntry[], query: string): Hit[] {
  const terms = fold(query).split(/\s+/).filter(t => t.length > 1 || /\d/.test(t))
  if (terms.length === 0) return []
  const hits: Hit[] = []
  for (const e of index) {
    const ft = fold(e.t), fh = fold(e.h), fx = fold(e.x)
    let score = 0
    let ok = true
    for (const term of terms) {
      const inT = ft.includes(term), inH = fh.includes(term), inX = fx.includes(term)
      if (!inT && !inH && !inX) { ok = false; break }
      if (inH) score += 6
      if (inT) score += e.h ? 2 : 8
      if (inX) score += 1 + Math.min(3, fx.split(term).length - 2)
    }
    if (!ok) continue
    // Snippet around the first body match.
    const pos = Math.max(0, ...terms.map(t => fx.indexOf(t)).filter(p => p >= 0).slice(0, 1))
    const from = Math.max(0, pos - 60)
    const raw = e.x.slice(from, from + 170)
    hits.push({ e, score, snippet: (from > 0 ? '…' : '') + raw + (from + 170 < e.x.length ? '…' : ''), start: from })
  }
  hits.sort((a, b) => b.score - a.score)
  // One page can match in many chunks; keep its best three.
  const perPage = new Map<DocPageId, number>()
  return hits.filter(h => {
    const n = perPage.get(h.e.p) ?? 0
    perPage.set(h.e.p, n + 1)
    return n < 3
  }).slice(0, 24)
}

function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0) return <>{text}</>
  const folded = fold(text)
  const marks: [number, number][] = []
  for (const t of terms) {
    let i = folded.indexOf(t)
    while (i !== -1) { marks.push([i, i + t.length]); i = folded.indexOf(t, i + t.length) }
  }
  if (marks.length === 0) return <>{text}</>
  marks.sort((a, b) => a[0] - b[0])
  const out: React.ReactNode[] = []
  let at = 0
  marks.forEach(([s, e], k) => {
    if (s < at) return
    if (s > at) out.push(text.slice(at, s))
    out.push(<mark key={k}>{text.slice(s, e)}</mark>)
    at = e
  })
  out.push(text.slice(at))
  return <>{out}</>
}

export function DocsSearch({ C, lang, onClose, sectionTitle }: {
  C: DocsChromeCopy
  lang: Lang
  onClose: () => void
  sectionTitle: (id: DocPageId) => string
}) {
  const [index, setIndex] = useState<SearchEntry[] | null>(cache[lang] ?? null)
  const [failed, setFailed] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    if (cache[lang]) { setIndex(cache[lang]!); return }
    let alive = true
    setIndex(null)
    setFailed(false)
    fetch(`/docs/search-index/${lang}`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json() as Promise<SearchEntry[]> })
      .then(data => { cache[lang] = data; if (alive) setIndex(data) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [lang])

  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  const hits = useMemo(() => (index ? search(index, q) : []), [index, q])
  const terms = useMemo(() => fold(q).split(/\s+/).filter(t => t.length > 1 || /\d/.test(t)), [q])
  useEffect(() => { setSel(0) }, [q])

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${sel}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [sel])

  const hrefOf = (h: Hit) => docHref(h.e.p) + (h.e.a ? `#${h.e.a}` : '')

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(s + 1, hits.length - 1)); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); return }
    if (e.key === 'Enter' && hits[sel]) {
      e.preventDefault()
      listRef.current?.querySelector<HTMLAnchorElement>(`[data-i="${sel}"]`)?.click()
    }
  }

  const listId = 'dc-search-results'
  return (
    <div className="dc-search-layer" onClick={onClose}>
      <div className="dc-search-box" role="dialog" aria-modal="true" aria-label={C.search.open} onClick={e => e.stopPropagation()} onKeyDown={onKey}>
        <div className="dc-search-field">
          <Search size={18} aria-hidden />
          <input
            ref={inputRef}
            type="text"
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder={C.search.placeholder}
            aria-label={C.search.open}
            role="combobox"
            aria-expanded={hits.length > 0}
            aria-controls={listId}
            aria-activedescendant={hits[sel] ? `dc-hit-${sel}` : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <button type="button" className="dc-search-close" onClick={onClose} aria-label={C.search.close}>
            <X size={18} aria-hidden />
          </button>
        </div>
        {failed ? (
          <p className="dc-search-msg" role="alert">{C.search.failed}</p>
        ) : !index ? (
          <p className="dc-search-msg">{C.search.loading}</p>
        ) : q.trim() && hits.length === 0 ? (
          <p className="dc-search-msg" role="status">{C.search.empty}</p>
        ) : hits.length > 0 ? (
          <ul className="dc-search-list" id={listId} role="listbox" aria-label={C.search.results} ref={listRef}>
            {hits.map((h, i) => (
              <li key={`${h.e.p}#${h.e.a}`} role="presentation">
                <Link
                  href={hrefOf(h)}
                  id={`dc-hit-${i}`}
                  data-i={i}
                  role="option"
                  aria-selected={i === sel}
                  onMouseEnter={() => setSel(i)}
                  onClick={onClose}
                >
                  <span className="dc-hit-path">{sectionTitle(h.e.p)}{h.e.h ? ` › ${h.e.t}` : ''}</span>
                  <span className="dc-hit-title"><Highlight text={h.e.h || h.e.t} terms={terms} /></span>
                  {h.snippet && <span className="dc-hit-snip"><Highlight text={h.snippet} terms={terms} /></span>}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  )
}

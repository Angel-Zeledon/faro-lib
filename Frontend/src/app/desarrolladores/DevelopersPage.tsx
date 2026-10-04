'use client'
// /desarrolladores — the developer reference, on the landing.
//
// Two halves with different owners:
//  · the prose (authentication, scopes, limits, billing, errors, pagination)
//    is copy, in src/i18n/developers.ts, Spanish and English;
//  · the endpoint reference is DATA, generated from the backend's OpenAPI by
//    backend/scripts/export_public_api.py into src/data/public-api.json. A
//    backend test fails when that file is stale, so this page cannot list a
//    route a key cannot call, or miss one it can.
//
// The reference is laid out like an API client (owner, 2026-10-02: "tipo
// Postman"): a searchable sidebar of endpoint groups, one endpoint at a time
// with its request bar and tabs, and a dark code panel with cURL, JavaScript
// and Python generated from the same data (./code.ts). Every endpoint is still
// rendered on the server — the ones not selected carry `hidden`, so the whole
// reference is in the HTML for crawlers and for find-in-page, and JS only
// decides which one is on screen.
//
// The landing's chrome (Nav/Footer) and primitives are imported, never edited
// here.
import Link from 'next/link'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Ban, Plug, Braces, Check, ChevronRight, Copy, Gauge, KeyRound, ListOrdered, Receipt,
  Search, TriangleAlert, X, PanelLeft,
} from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { DEVELOPERS, type DevelopersCopy } from '@/i18n/developers'
import { LandingStyles, useScrollReveal } from '@/components/landing/primitives'
import { Nav, Footer } from '@/components/landing/chrome'
import { FinalSection } from '@/components/landing/sections'
import { CONTACT_EMAIL, mailHref } from '@/components/landing/contact'
import { appHref, SITE_URL } from '@/lib/siteUrls'
import { loadSampleLang, saveSampleLang } from '@/lib/codeSamples'
import raw from '@/data/public-api.json'
import {
  CODE_LANGS, ENVELOPE_SAMPLE, STATUS_TEXT, highlight, hlLangOf, sampleFor,
  type CodeLang, type Endpoint, type HlLang,
} from './code'

type Snapshot = {
  base_path: string
  counts: { total: number; read: number; write: number }
  limits: { per_minute_per_key: number; per_day_per_key: Record<string, number | null> }
  tags: { tag: string; endpoints: Endpoint[] }[]
}

const API = raw as unknown as Snapshot
const ALL: Endpoint[] = API.tags.flatMap(g => g.endpoints)
const BY_ID = new Map(ALL.map(ep => [ep.id, ep]))
const HERO_EP = BY_ID.get('get-planning') ?? ALL[0]

/** The base URL an integration types. Absolute where the build knows the
 *  app's origin, so it can be copied as is. */
function apiBase(): string {
  const app = appHref(API.base_path)
  return app.startsWith('http') ? app : `${SITE_URL}${API.base_path}`
}

// ── Small pieces ──────────────────────────────────────────────────────────────

function MethodPill({ method, size = 'md' }: { method: string; size?: 'sm' | 'md' }) {
  return <span className={`dv-mp m-${method}${size === 'sm' ? ' is-sm' : ''}`}>{method === 'DELETE' ? 'DEL' : method}</span>
}

function Code({ code, lang }: { code: string; lang: HlLang }) {
  const tokens = useMemo(() => highlight(code, lang), [code, lang])
  return (
    <pre className="dv-pre" tabIndex={0}>
      <code>
        {tokens.map(([cls, text], i) => (cls ? <span key={i} className={`tk-${cls}`}>{text}</span> : <Fragment key={i}>{text}</Fragment>))}
      </code>
    </pre>
  )
}

function CopyButton({ text, label, done, className = '' }: { text: string; label: string; done: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number>()
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard refused (insecure origin, permissions): the text is on
      // screen and selectable, so the button simply does not confirm.
    }
  }
  return (
    <button type="button" className={`dv-copy ${className}`} onClick={copy} aria-label={copied ? done : label}>
      {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
      <span>{copied ? done : label}</span>
    </button>
  )
}

/** The dark editor window: a code block with a title bar and a copy button. */
function CodeWindow({ title, code, lang, W, tabs }: {
  title?: React.ReactNode; code: string; lang: HlLang; W: DevelopersCopy['workspace']; tabs?: React.ReactNode
}) {
  return (
    <div className="dv-win">
      <div className="dv-win-bar">
        {tabs ?? <span className="dv-win-title">{title}</span>}
        <CopyButton text={code} label={W.copy} done={W.copied} />
      </div>
      <Code code={code} lang={lang} />
    </div>
  )
}

// Real, trimmed example answers for the read endpoints, captured from a demo
// account by backend/scripts/capture_api_examples.py and served as a static
// file. Fetched once, the first time an endpoint is shown, so the page itself
// stays light; until it arrives (or for endpoints without one) the generic
// envelope is shown.
type ResponseExample = { example: unknown; fields: { name: string; type: string }[] }
let examplesCache: Record<string, ResponseExample> | null = null
let examplesPromise: Promise<Record<string, ResponseExample>> | null = null

function useResponseExample(id: string): ResponseExample | null {
  const [all, setAll] = useState<Record<string, ResponseExample> | null>(examplesCache)
  useEffect(() => {
    if (examplesCache) return
    examplesPromise ??= fetch('/api-response-examples.json')
      .then(r => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then(d => (examplesCache = d as Record<string, ResponseExample>))
    let alive = true
    examplesPromise.then(d => { if (alive) setAll(d) })
    return () => { alive = false }
  }, [])
  return all?.[id] ?? null
}

/** Request samples in three languages, then the response envelope. */
function CodePanel({ ep, base, codeLang, setCodeLang, W, idPrefix }: {
  ep: Endpoint; base: string; codeLang: CodeLang; setCodeLang: (l: CodeLang) => void
  W: DevelopersCopy['workspace']; idPrefix: string
}) {
  const code = useMemo(() => sampleFor(codeLang, ep, base), [codeLang, ep, base])
  const example = useResponseExample(ep.id)
  const responseCode = useMemo(
    () => (example ? JSON.stringify(example.example, null, 2) : ENVELOPE_SAMPLE),
    [example],
  )
  const tabs = (
    <div className="dv-langs" role="tablist" aria-label={W.languages}>
      {CODE_LANGS.map(l => (
        <button
          key={l.id}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${l.id}`}
          aria-selected={codeLang === l.id}
          className={codeLang === l.id ? 'is-on' : ''}
          onClick={() => setCodeLang(l.id)}
        >
          {l.label}
        </button>
      ))}
    </div>
  )
  return (
    <div className="dv-panel">
      <div className="dv-panel-label">{W.request}</div>
      <CodeWindow code={code} lang={hlLangOf(codeLang)} W={W} tabs={tabs} />
      <div className="dv-panel-label">{W.response}</div>
      {ep.success_status === 204 ? (
        <div className="dv-win">
          <div className="dv-win-bar">
            <span className="dv-win-title"><span className="dv-status">{ep.success_status}</span> {STATUS_TEXT[204]}</span>
          </div>
          <p className="dv-win-empty">{W.noContent}</p>
        </div>
      ) : (
        <CodeWindow
          code={responseCode}
          lang="json"
          W={W}
          title={<><span className="dv-status">{ep.success_status}</span> {STATUS_TEXT[ep.success_status] ?? ''} <span className="dv-win-dim">{ep.response_content_types.join(', ')}</span></>}
        />
      )}
    </div>
  )
}

/** The path with its {placeholders} picked out, as in an API client's URL bar. */
function PathText({ path }: { path: string }) {
  const parts = path.split(/(\{[^}]+\})/)
  return (
    <>
      {parts.map((p, i) => (p.startsWith('{') ? <span key={i} className="dv-ph">{p}</span> : <Fragment key={i}>{p}</Fragment>))}
    </>
  )
}

// ── One endpoint ──────────────────────────────────────────────────────────────

type TabId = 'params' | 'body' | 'headers' | 'response'

function EndpointView({ ep, D, base, active, tab, setTab, groupLabel }: {
  ep: Endpoint; D: DevelopersCopy; base: string; active: boolean
  tab: TabId; setTab: (t: TabId) => void; groupLabel: string
}) {
  const R = D.reference
  const W = D.workspace
  const body = ep.request_body
  const params = ep.parameters.filter(p => p.in !== 'header')
  const headerParams = ep.parameters.filter(p => p.in === 'header')
  const tabs: { id: TabId; label: string; count?: number }[] = [
    { id: 'params', label: W.tabs.params, count: params.length },
    { id: 'body', label: W.tabs.body, count: body?.fields.length },
    { id: 'headers', label: W.tabs.headers, count: 1 + headerParams.length + (body && !body.content_type.startsWith('multipart/') ? 1 : 0) },
    { id: 'response', label: W.tabs.response },
  ]
  // Inactive endpoints render every panel (hidden) so the reference is whole in
  // the HTML; the active one follows the selected tab.
  const shown = (id: TabId) => active && tab === id

  return (
    <article id={ep.id} className="dv-ep" hidden={!active} aria-labelledby={`${ep.id}-title`}>
      <div className="dv-ep-group">{groupLabel}</div>
      <h3 id={`${ep.id}-title`} className="dv-ep-title">{ep.summary}</h3>

      <div className="dv-reqbar">
        <MethodPill method={ep.method} />
        <code className="dv-url">
          <span className="dv-url-base">{API.base_path}</span><PathText path={ep.path} />
        </code>
        <CopyButton text={`${base}${ep.path}`} label={W.copyUrl} done={W.copied} className="is-bar" />
      </div>

      <div className="dv-ep-meta">
        <span className={`dv-scope${ep.scope === 'write' ? ' is-write' : ''}`}>
          <KeyRound size={13} aria-hidden />
          {ep.scope === 'write' ? R.write : R.read}
        </span>
        <span className="dv-returns">{R.returns(ep.success_status, ep.response_content_types.join(', '))}</span>
      </div>
      {ep.description && <p className="dv-ep-desc">{ep.description}</p>}

      <div className="dv-tabs" role="tablist" aria-label={ep.summary}>
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`${ep.id}-t-${t.id}`}
            aria-controls={`${ep.id}-p-${t.id}`}
            aria-selected={active && tab === t.id}
            className={active && tab === t.id ? 'is-on' : ''}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {typeof t.count === 'number' && t.count > 0 && <span className="dv-tab-n">{t.count}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`${ep.id}-p-params`} aria-labelledby={`${ep.id}-t-params`} hidden={!shown('params')} className="dv-tabpanel">
        {params.length === 0 ? <p className="dv-empty">{R.noParams}</p> : (
          <ul className="dv-fields">
            {params.map(p => (
              <li key={`${p.in}-${p.name}`}>
                <div className="dv-field-head">
                  <code className="dv-field-name">{p.name}</code>
                  <span className="dv-field-type">{p.type}</span>
                  <span className="dv-field-in">{p.in}</span>
                  {p.required && <span className="dv-req">{R.required}</span>}
                </div>
                {p.description && <p className="dv-field-desc">{p.description}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div role="tabpanel" id={`${ep.id}-p-body`} aria-labelledby={`${ep.id}-t-body`} hidden={!shown('body')} className="dv-tabpanel">
        {!body ? <p className="dv-empty">{W.noBody}</p> : (
          <>
            <div className="dv-ctype">{body.content_type}</div>
            {body.fields.length > 0 && (
              <ul className="dv-fields">
                {body.fields.map(f => (
                  <li key={f.name}>
                    <div className="dv-field-head">
                      <code className="dv-field-name">{f.name}</code>
                      <span className="dv-field-type">{f.type}</span>
                      {f.required && <span className="dv-req">{R.required}</span>}
                    </div>
                    {f.description && <p className="dv-field-desc">{f.description}</p>}
                  </li>
                ))}
              </ul>
            )}
            {!body.content_type.startsWith('multipart/') && active && (
              <div style={{ marginTop: 16 }}>
                <CodeWindow title={R.example} code={JSON.stringify(body.example ?? {}, null, 2)} lang="json" W={W} />
              </div>
            )}
          </>
        )}
      </div>

      <div role="tabpanel" id={`${ep.id}-p-headers`} aria-labelledby={`${ep.id}-t-headers`} hidden={!shown('headers')} className="dv-tabpanel">
        <p className="dv-empty" style={{ marginBottom: 6 }}>{W.headersLead}</p>
        <ul className="dv-fields">
          <li>
            <div className="dv-field-head">
              <code className="dv-field-name">Authorization</code>
              <code className="dv-field-type">Bearer sk_live_…</code>
              <span className="dv-req">{R.required}</span>
            </div>
            <p className="dv-field-desc">{W.authValue}</p>
          </li>
          {body && !body.content_type.startsWith('multipart/') && (
            <li>
              <div className="dv-field-head">
                <code className="dv-field-name">Content-Type</code>
                <code className="dv-field-type">{body.content_type}</code>
                <span className="dv-req">{R.required}</span>
              </div>
            </li>
          )}
          {headerParams.map(h => (
            <li key={h.name}>
              <div className="dv-field-head">
                <code className="dv-field-name">{h.name}</code>
                <span className="dv-field-type">{h.type}</span>
                <span className={h.required ? 'dv-req' : 'dv-opt'}>{h.required ? R.required : W.optional}</span>
              </div>
              {h.description && <p className="dv-field-desc">{h.description}</p>}
            </li>
          ))}
        </ul>
      </div>

      <div role="tabpanel" id={`${ep.id}-p-response`} aria-labelledby={`${ep.id}-t-response`} hidden={!shown('response')} className="dv-tabpanel">
        <div className="dv-resp-line">
          <span className="dv-resp-status">{ep.success_status} {STATUS_TEXT[ep.success_status] ?? ''}</span>
          <code className="dv-field-type">{ep.response_content_types.join(', ')}</code>
        </div>
        <p className="dv-field-desc" style={{ marginTop: 10 }}>
          {ep.success_status === 204 ? W.noContent : W.responseNote}
        </p>
        <ResponseFields id={ep.id} W={W} />
        <a href="#errores" className="dv-link">{D.errors.title}</a>
      </div>
    </article>
  )
}

/** The fields observed in the real example answer, with their types. */
function ResponseFields({ id, W }: { id: string; W: DevelopersCopy['workspace'] }) {
  const ex = useResponseExample(id)
  if (!ex || ex.fields.length === 0) return null
  return (
    <div style={{ marginTop: 14 }}>
      <p className="dv-field-desc" style={{ fontWeight: 600, marginBottom: 6 }}>{W.fieldsTitle}</p>
      <ul className="dv-fields">
        {ex.fields.map(f => (
          <li key={f.name} className="dv-field">
            <div className="dv-field-head">
              <code className="dv-field-name">{f.name}</code>
              <span className="dv-field-type">{f.type}</span>
            </div>
          </li>
        ))}
      </ul>
      <p className="dv-field-desc" style={{ marginTop: 8 }}>{W.exampleNote}</p>
    </div>
  )
}

// ── The workspace: sidebar + endpoint + code ──────────────────────────────────

function matches(ep: Endpoint, q: string, groupLabel: string): boolean {
  if (!q) return true
  const hay = `${ep.method} ${ep.path} ${ep.summary} ${ep.tag} ${groupLabel}`.toLowerCase()
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w))
}

function Sidebar({ D, query, setQuery, selected, onSelect, open, toggle, inputId }: {
  D: DevelopersCopy; query: string; setQuery: (q: string) => void; selected: string
  onSelect: (id: string) => void; open: Set<string>; toggle: (tag: string) => void; inputId: string
}) {
  const W = D.workspace
  const label = (tag: string) => D.reference.tags[tag] ?? tag
  const groups = API.tags
    .map(g => ({ ...g, eps: g.endpoints.filter(ep => matches(ep, query, label(g.tag))) }))
    .filter(g => g.eps.length > 0)
  return (
    <div className="dv-side-in">
      <label className="dv-search" htmlFor={inputId}>
        <Search size={15} aria-hidden />
        <input
          id={inputId}
          type="search"
          placeholder={W.search}
          aria-label={W.search}
          value={query}
          onChange={e => setQuery(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <nav aria-label={D.reference.title} className="dv-groups">
        {groups.length === 0 && <p className="dv-none">{W.noResults}</p>}
        {groups.map(g => {
          const isOpen = !!query || open.has(g.tag)
          return (
            <div key={g.tag} className="dv-group" id={inputId === 'dv-q' ? `tag-${g.tag}` : undefined}>
              <button
                type="button"
                className="dv-group-head"
                aria-expanded={isOpen}
                aria-controls={`list-${inputId}-${g.tag}`}
                onClick={() => toggle(g.tag)}
              >
                <ChevronRight size={14} aria-hidden className="dv-chev" />
                <span className="dv-group-name">{label(g.tag)}</span>
                <span className="dv-group-n">{g.eps.length}</span>
              </button>
              <ul id={`list-${inputId}-${g.tag}`} hidden={!isOpen}>
                {g.eps.map(ep => (
                  <li key={ep.id}>
                    <a
                      href={`#${ep.id}`}
                      className={`dv-row${selected === ep.id ? ' is-on' : ''}`}
                      aria-current={selected === ep.id ? 'true' : undefined}
                      onClick={e => { e.preventDefault(); onSelect(ep.id) }}
                    >
                      <MethodPill method={ep.method} size="sm" />
                      <span className="dv-row-text">
                        <span className="dv-row-sum">{ep.summary}</span>
                        <span className="dv-row-path">{ep.path}</span>
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </nav>
    </div>
  )
}

function Workspace({ D, base, codeLang, setCodeLang }: {
  D: DevelopersCopy; base: string; codeLang: CodeLang; setCodeLang: (l: CodeLang) => void
}) {
  const W = D.workspace
  const [selected, setSelected] = useState(ALL[0].id)
  const [tab, setTab] = useState<TabId>('params')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Set<string>>(() => new Set([ALL[0].tag]))
  const [sheet, setSheet] = useState(false)
  const wsRef = useRef<HTMLDivElement>(null)

  const select = useCallback((id: string, scroll = true) => {
    const ep = BY_ID.get(id)
    if (!ep) return
    setSelected(id)
    setTab('params')
    setOpen(prev => (prev.has(ep.tag) ? prev : new Set(prev).add(ep.tag)))
    setSheet(false)
    try { history.replaceState(null, '', `#${id}`) } catch { /* sandboxed frames */ }
    if (scroll && wsRef.current) {
      const top = wsRef.current.getBoundingClientRect().top
      if (top < 0 || top > window.innerHeight * 0.5) wsRef.current.scrollIntoView({ block: 'start' })
    }
  }, [])

  // Deep links: #<endpoint id> selects it (the old page used the same ids),
  // #tag-<tag> opens that group on its first endpoint.
  useEffect(() => {
    const apply = () => {
      const h = decodeURIComponent(location.hash.slice(1))
      if (!h) return
      if (BY_ID.has(h)) { select(h); return }
      if (h.startsWith('tag-')) {
        const g = API.tags.find(t => t.tag === h.slice(4))
        if (g) select(g.endpoints[0].id)
      }
    }
    apply()
    window.addEventListener('hashchange', apply)
    return () => window.removeEventListener('hashchange', apply)
  }, [select])

  // The phone sheet: Escape closes it, and the page behind does not scroll.
  useEffect(() => {
    if (!sheet) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSheet(false) }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.setTimeout(() => document.getElementById('dv-q-sheet')?.focus(), 30)
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev }
  }, [sheet])

  const toggle = (tag: string) => setOpen(prev => {
    const next = new Set(prev)
    if (next.has(tag)) next.delete(tag)
    else next.add(tag)
    return next
  })
  const current = BY_ID.get(selected) ?? ALL[0]
  const label = (tag: string) => D.reference.tags[tag] ?? tag
  const sideProps = { D, query, setQuery, selected, onSelect: (id: string) => select(id), open, toggle }

  return (
    <div className="dv-ws" ref={wsRef}>
      <aside className="dv-side">
        <Sidebar {...sideProps} inputId="dv-q" />
      </aside>

      {/* Phones and narrow tablets: the sidebar becomes a sheet behind this bar. */}
      <div className="dv-mbar">
        <button type="button" className="dv-mbar-btn" onClick={() => setSheet(true)} aria-haspopup="dialog" aria-expanded={sheet}>
          <PanelLeft size={16} aria-hidden />
          <span>{W.browse}</span>
          <span className="dv-group-n">{API.counts.total}</span>
        </button>
        <span className="dv-mbar-cur"><MethodPill method={current.method} size="sm" /> <span>{current.summary}</span></span>
      </div>
      {sheet && (
        <div className="dv-sheet" role="dialog" aria-modal="true" aria-label={W.browse}>
          <div className="dv-sheet-head">
            <strong>{W.browse}</strong>
            <button type="button" className="dv-sheet-x" onClick={() => setSheet(false)} aria-label={W.close}><X size={18} aria-hidden /></button>
          </div>
          <Sidebar {...sideProps} inputId="dv-q-sheet" />
        </div>
      )}

      <div className="dv-main">
        <div className="dv-detail">
          {API.tags.map(g => g.endpoints.map(ep => (
            <EndpointView
              key={ep.id}
              ep={ep}
              D={D}
              base={base}
              active={ep.id === current.id}
              tab={tab}
              setTab={setTab}
              groupLabel={label(g.tag)}
            />
          )))}
        </div>
        <div className="dv-code">
          <CodePanel ep={current} base={base} codeLang={codeLang} setCodeLang={setCodeLang} W={W} idPrefix="ws" />
        </div>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DevelopersPage() {
  const { lang } = useLanguage()
  const D = DEVELOPERS[lang]
  const W = D.workspace
  useScrollReveal()
  const base = useMemo(apiBase, [])
  const perDayFree = API.limits.per_day_per_key.free
  const [codeLang, setCodeLangState] = useState<CodeLang>('curl')
  useEffect(() => { setCodeLangState(loadSampleLang()) }, [])
  const setCodeLang = (l: CodeLang) => {
    setCodeLangState(l)
    saveSampleLang(l)
  }
  const toc: [string, string][] = [
    ['#autenticacion', D.auth.title],
    ['#limites', D.limits.title],
    ['#cobro', D.billing.title],
    ['#errores', D.errors.title],
    ['#referencia', D.reference.title],
  ]

  return (
    <div className="lp dv" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: DEV_CSS }} />
      <Nav onHome={false} localAnchors={['contacto']} />
      <main>
        <header className="dv-hero">
          <div className="dv-hero-bg" aria-hidden />
          <div className="dv-inner dv-hero-grid">
            <div className="dv-hero-text">
              <nav aria-label={D.breadcrumb} className="dv-crumbs">
                <ol>
                  <li><Link href="/">{D.home}</Link></li>
                  <li><span aria-current="page">{D.label}</span></li>
                </ol>
              </nav>
              <h1 className="dv-h1">{D.title}</h1>
              <p className="dv-intro">{D.intro}</p>

              <dl className="dv-facts">
                <div className="dv-fact is-wide">
                  <dt>{D.facts.base}</dt>
                  <dd className="dv-basebar">
                    <code>{base}</code>
                    <CopyButton text={base} label={W.copy} done={W.copied} className="is-bar" />
                  </dd>
                </div>
                <div className="dv-fact is-wide">
                  <dt>{D.facts.auth}</dt>
                  <dd><code className="dv-chip-code">Authorization: Bearer sk_live_…</code></dd>
                </div>
                <div className="dv-fact">
                  <dt>{D.facts.endpoints}</dt>
                  <dd className="dv-counts">
                    <span className="dv-count-total">{D.facts.endpointsValue(API.counts.total)}</span>
                    <span className="dv-count is-read">{W.stats.read(API.counts.read)}</span>
                    <span className="dv-count is-write">{W.stats.write(API.counts.write)}</span>
                  </dd>
                </div>
              </dl>
              <nav aria-label={D.toc} className="dv-toc">
                {toc.map(([href, text]) => <a key={href} href={href}>{text}</a>)}
              </nav>
            </div>
            <div className="dv-hero-code">
              <div className="dv-hero-req">
                <MethodPill method={HERO_EP.method} />
                <code><span className="dv-url-base">{API.base_path}</span>{HERO_EP.path}</code>
              </div>
              <CodePanel ep={HERO_EP} base={base} codeLang={codeLang} setCodeLang={setCodeLang} W={W} idPrefix="hero" />
            </div>
          </div>
        </header>

        <section id="autenticacion" className="dv-sec">
          <div className="dv-inner">
            <h2 className="dv-h2">{D.auth.title}</h2>
            <p className="dv-lead">{D.auth.body}</p>
            <div className="dv-grid">
              <div className="dv-card dv-wide is-code">
                <CodeWindow
                  title="shell"
                  code={`export STOCKAI=${base}\nexport STOCKAI_KEY=sk_live_…\n\ncurl "$STOCKAI/planning" \\\n  -H "Authorization: Bearer $STOCKAI_KEY"`}
                  lang="bash"
                  W={W}
                />
                <p className="dv-callout"><KeyRound size={16} aria-hidden />{D.auth.note}</p>
              </div>
              <div className="dv-card c-indigo">
                <span className="dv-icon" aria-hidden><KeyRound size={18} /></span>
                <h3>{D.scopes.title}</h3>
                <p>{D.scopes.body}</p>
                <ul className="dv-scopes">
                  <li><span className="dv-scope">{D.scopes.read}</span> {D.scopes.readDesc}</li>
                  <li><span className="dv-scope is-write">{D.scopes.write}</span> {D.scopes.writeDesc}</li>
                </ul>
                <p>{D.scopes.howToGet}</p>
              </div>
              <div className="dv-card c-coral">
                <span className="dv-icon" aria-hidden><Ban size={18} /></span>
                <h3>{D.never.title}</h3>
                <p>{D.never.body}</p>
                <ul>{D.never.items.map(i => <li key={i}>{i}</li>)}</ul>
                <p>{D.never.outward}</p>
              </div>
            </div>
          </div>
        </section>

        <section id="limites" className="dv-sec is-alt">
          <div className="dv-inner">
            <div className="dv-grid">
              <div className="dv-card c-teal">
                <span className="dv-icon" aria-hidden><Gauge size={18} /></span>
                <h3>{D.limits.title}</h3>
                <div className="dv-bignums">
                  <div><strong>{API.limits.per_minute_per_key}</strong><span>/ min</span></div>
                  {typeof perDayFree === 'number' && <div><strong>{perDayFree}</strong><span>/ 24 h</span></div>}
                </div>
                <ul>
                  <li>{D.limits.perMinute(API.limits.per_minute_per_key)}</li>
                  {typeof perDayFree === 'number' && <li>{D.limits.perDayFree(perDayFree)}</li>}
                  <li>{D.limits.perDayPaid}</li>
                </ul>
                <p>{D.limits.over}</p>
              </div>
              <div className="dv-card c-gold" id="cobro">
                <span className="dv-icon" aria-hidden><Receipt size={18} /></span>
                <h3>{D.billing.title}</h3>
                <p>{D.billing.body}</p>
                <p className="dv-card-links">
                  <Link href="/precios">{D.billing.pricingLink}</Link>
                  <a href={mailHref('API StockAI')}>{D.billing.contact(CONTACT_EMAIL)}</a>
                </p>
              </div>
              <div className="dv-card dv-wide c-coral dv-errors" id="errores">
                <div>
                  <span className="dv-icon" aria-hidden><TriangleAlert size={18} /></span>
                  <h3>{D.errors.title}</h3>
                  <p>{D.errors.body}</p>
                  <ul className="dv-codes">
                    {D.errors.codes.map(([code, desc]) => {
                      const m = /^(\d{3})\s*—\s*(.*)$/.exec(desc)
                      return (
                        <li key={code}>
                          {m && <span className={`dv-http s-${m[1][0]}`}>{m[1]}</span>}
                          <code>{code}</code>
                          <span>{m ? m[2] : desc}</span>
                        </li>
                      )
                    })}
                  </ul>
                </div>
                <CodeWindow
                  title={<><span className="dv-status is-err">403</span> Forbidden</>}
                  code={`{\n  "detail": "This endpoint writes and the API key is read-only. Use a write key.",\n  "error_code": "api_key_scope_insufficient",\n  "error_params": { "required_scope": "write", "key_scope": "read" }\n}`}
                  lang="json"
                  W={W}
                />
              </div>
              <div className="dv-trio dv-wide">
                <div className="dv-card c-indigo">
                  <span className="dv-icon" aria-hidden><Braces size={18} /></span>
                  <h3>{D.envelope.title}</h3>
                  <p>{D.envelope.body}</p>
                </div>
                <div className="dv-card c-teal">
                  <span className="dv-icon" aria-hidden><ListOrdered size={18} /></span>
                  <h3>{D.pagination.title}</h3>
                  <p>{D.pagination.body}</p>
                </div>
                <div className="dv-card c-violet">
                  <span className="dv-icon" aria-hidden><Plug size={18} /></span>
                  <h3>{D.mcp.title}</h3>
                  <p>{D.mcp.body}</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="referencia" className="dv-sec dv-ref">
          <div className="dv-inner">
            <h2 className="dv-h2">{D.reference.title}</h2>
            <p className="dv-lead">{D.reference.lead}</p>
          </div>
          <div className="dv-ws-wrap">
            <Workspace D={D} base={base} codeLang={codeLang} setCodeLang={setCodeLang} />
          </div>
        </section>

        <FinalSection />
      </main>
      <Footer onHome={false} localAnchors={['contacto']} />
    </div>
  )
}

// ── Stylesheet ────────────────────────────────────────────────────────────────
// The code surfaces are dark in BOTH site themes (owner: no white editor), so
// their colours are fixed values, not theme tokens. Token colours were checked
// against --dv-code-bg for at least 4.5:1 (comments included).
const DEV_CSS = `
.dv {
 --dv-code-bg: #08191C; --dv-code-bar: #0D2328; --dv-code-bd: #1A353B; --dv-code-text: #DCE8E6; --dv-code-dim: #8FA6A8;
 --dv-indigo: #4F46E5; --dv-coral: #E0473F; --dv-gold: #B7791F; --dv-violet: #8B3FD9; --dv-teal: #0F8A80;
 --dv-get: #15803D; --dv-post: #B45309; --dv-put: #1D4ED8; --dv-patch: #7E22CE; --dv-delete: #B91C1C;
 --dv-mesh-a: rgba(15,138,128,0.16); --dv-mesh-b: rgba(79,70,229,0.12); --dv-mesh-c: rgba(224,71,63,0.08);
 --font-mono: var(--font-code), ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace;
}
[data-theme="dark"] .dv {
 --dv-indigo: #8B87FF; --dv-coral: #FF7A70; --dv-gold: #F2B84B; --dv-violet: #C084FC; --dv-teal: #3CC7B8;
 --dv-get: #4ADE80; --dv-post: #C99A3E; --dv-put: #60A5FA; --dv-patch: #C084FC; --dv-delete: #D07878;
 --dv-mesh-a: rgba(60,199,184,0.14); --dv-mesh-b: rgba(139,135,255,0.12); --dv-mesh-c: rgba(255,122,112,0.07);
}
.dv code, .dv pre, .dv kbd { font-family: var(--font-mono); }

/* Method pills: Postman's convention, a colour per verb. */
.dv-mp { --m: var(--dv-get); display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
 font-family: var(--font-mono); font-size: 12px; font-weight: 700; letter-spacing: 0.02em; line-height: 1;
 min-width: 58px; padding: 6px 8px; border-radius: 7px; color: var(--m);
 background: color-mix(in srgb, var(--m) 13%, transparent); border: 1px solid color-mix(in srgb, var(--m) 30%, transparent); }
.dv-mp.is-sm { font-size: 10.5px; min-width: 42px; padding: 4px 5px; border-radius: 5px; }
.dv-mp.m-POST { --m: var(--dv-post); } .dv-mp.m-PUT { --m: var(--dv-put); } .dv-mp.m-PATCH { --m: var(--dv-patch); } .dv-mp.m-DELETE { --m: var(--dv-delete); }
/* On the always-dark surfaces the bright set reads; the light set would not. */
.dv-hero-req .dv-mp, .dv-reqbar .dv-mp { --dv-get: #4ADE80; --dv-post: #C99A3E; --dv-put: #60A5FA; --dv-patch: #C084FC; --dv-delete: #D07878; }

/* ── Hero ── */
.dv-hero { position: relative; padding: 120px 0 72px; overflow: hidden; isolation: isolate; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.dv-hero-bg { position: absolute; inset: 0; z-index: -1; pointer-events: none;
 background:
  radial-gradient(60% 70% at 88% 18%, var(--dv-mesh-b), transparent 70%),
  radial-gradient(50% 60% at 8% 0%, var(--dv-mesh-a), transparent 70%),
  radial-gradient(40% 50% at 60% 100%, var(--dv-mesh-c), transparent 70%); }
.dv-hero-bg::after { content: ''; position: absolute; inset: 0;
 background-image: radial-gradient(var(--lp-grid) 1px, transparent 1px); background-size: 22px 22px;
 -webkit-mask-image: linear-gradient(180deg, black, transparent 80%); mask-image: linear-gradient(180deg, black, transparent 80%); }
.dv-inner { position: relative; max-width: 1240px; margin: 0 auto; padding: 0 40px; }
.dv-hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 520px); gap: 56px; align-items: center; }
.dv-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 22px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.dv-crumbs li + li::before { content: '/'; margin-right: 8px; color: var(--lp-dim); }
.dv-crumbs a { color: var(--lp-muted); text-decoration: none; }
.dv-crumbs a:hover { color: var(--lp-accent); }
.dv-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.dv-h1 { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(34px, 4.6vw, 58px); font-weight: 600; line-height: 1.04; letter-spacing: -0.045em; margin: 0 0 20px;
 color: var(--lp-text); }
.dv-intro { font-size: clamp(16px, 1.4vw, 18px); color: var(--lp-body); line-height: 1.65; max-width: 60ch; margin: 0; }
.dv-facts { display: flex; flex-wrap: wrap; gap: 18px 28px; margin: 30px 0 0; }
.dv-fact { min-width: 0; }
.dv-fact.is-wide { flex: 1 1 100%; }
.dv-fact dt { font-size: 12.5px; font-weight: 600; color: var(--lp-muted); margin-bottom: 7px; }
.dv-fact dd { margin: 0; font-size: 14px; color: var(--lp-text); min-width: 0; }
.dv-basebar { display: flex; align-items: center; gap: 8px; max-width: 560px; padding: 6px 6px 6px 14px; border-radius: 11px;
 background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); box-shadow: 0 10px 30px -18px rgba(8,25,28,0.6); }
.dv-basebar code { flex: 1; min-width: 0; font-size: 13.5px; color: #7FE3D6; overflow-x: auto; white-space: nowrap; scrollbar-width: none; }
.dv-chip-code { display: inline-block; max-width: 100%; font-size: 13px; padding: 6px 11px; border-radius: 8px; background: var(--lp-surface); border: 1px solid var(--lp-border); color: var(--lp-text); overflow-wrap: anywhere; }
.dv-counts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.dv-count-total { font-weight: 700; margin-right: 4px; }
.dv-count { font-size: 12.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px; }
.dv-count.is-read { color: var(--dv-get); background: color-mix(in srgb, var(--dv-get) 12%, transparent); }
.dv-count.is-write { color: var(--dv-post); background: color-mix(in srgb, var(--dv-post) 13%, transparent); }
.dv-toc { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 28px; }
.dv-toc a { font-size: 13px; font-weight: 600; color: var(--lp-body); text-decoration: none; padding: 7px 14px; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass); transition: border-color 160ms ease, color 160ms ease, background-color 160ms ease; }
.dv-toc a:hover { border-color: var(--dv-teal); color: var(--dv-teal); background: color-mix(in srgb, var(--dv-teal) 8%, transparent); }
.dv-hero-code { min-width: 0; padding: 14px; border-radius: 20px; background: linear-gradient(150deg, #0E3B41, #08191C 55%, #1B1840); box-shadow: 0 40px 80px -40px rgba(8,25,28,0.7), 0 0 0 1px rgba(127,227,214,0.12) inset; }
.dv-hero-req { display: flex; align-items: center; gap: 10px; padding: 4px 4px 12px; min-width: 0; }
.dv-hero-req code { font-size: 14px; color: #EAF3F2; overflow-x: auto; white-space: nowrap; }
.dv-hero-code .dv-panel-label { color: #9CB4B6; }

/* ── The dark editor ── */
.dv-panel { display: grid; gap: 8px; min-width: 0; }
.dv-panel-label { font-size: 12px; font-weight: 600; color: var(--lp-muted); margin-top: 6px; }
.dv-panel-label:first-child { margin-top: 0; }
.dv-win { min-width: 0; border-radius: 12px; overflow: hidden; background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); color: var(--dv-code-text); }
.dv-win-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 42px; padding: 0 6px 0 12px; background: var(--dv-code-bar); border-bottom: 1px solid var(--dv-code-bd); }
.dv-win-title { font-size: 12.5px; font-weight: 600; color: #C9D8D6; display: inline-flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dv-win-dim { color: var(--dv-code-dim); font-weight: 500; }
.dv-win-empty { margin: 0; padding: 16px; font-size: 13px; color: var(--dv-code-dim); }
.dv-status { font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; color: #08191C; background: #4ADE80; border-radius: 5px; padding: 2px 6px; }
.dv-status.is-err { background: #FF8A80; }
.dv-pre { margin: 0; padding: 14px 16px 16px; overflow-x: auto; font-size: 13px; line-height: 1.7; white-space: pre; color: var(--dv-code-text); tab-size: 2; max-width: 100%; }
.dv-pre:focus-visible { outline: 2px solid #7FE3D6; outline-offset: -2px; }
.dv-pre::-webkit-scrollbar { height: 8px; } .dv-pre::-webkit-scrollbar-thumb { background: #24444A; border-radius: 8px; }
.tk-kw { color: #D7A6FF; } .tk-str { color: #A8E6A3; } .tk-num { color: #FFB27A; } .tk-lit { color: #FF9EB5; }
.tk-com { color: #8FA6A8; font-style: italic; } .tk-var { color: #FFD479; } .tk-flag { color: #FF9580; }
.tk-fn { color: #8CC8FF; } .tk-prop { color: #7FE3D6; } .tk-pun { color: #A9BDBF; }
.dv-langs { display: flex; gap: 2px; min-width: 0; overflow-x: auto; scrollbar-width: none; }
.dv-langs button { all: unset; cursor: pointer; font-size: 12.5px; font-weight: 600; color: #9CB4B6; padding: 12px 10px 10px; border-bottom: 2px solid transparent; white-space: nowrap; transition: color 140ms ease, border-color 140ms ease; }
.dv-langs button:hover { color: #EAF3F2; }
.dv-langs button.is-on { color: #fff; border-bottom-color: #3CC7B8; }
.dv-langs button:focus-visible { outline: 2px solid #7FE3D6; outline-offset: -2px; border-radius: 4px; }
.dv-copy { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; cursor: pointer; font: inherit; font-size: 12px; font-weight: 600;
 color: #C9D8D6; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); border-radius: 7px; padding: 6px 10px; min-height: 30px; transition: background-color 140ms ease, color 140ms ease; }
.dv-copy:hover { background: rgba(127,227,214,0.14); color: #fff; }
.dv-copy:focus-visible { outline: 2px solid #7FE3D6; outline-offset: 2px; }

/* ── Guide sections ── */
#cobro, #errores { scroll-margin-top: 88px; }
.dv-sec { padding: 88px 0; background: var(--lp-bg); position: relative; }
.dv-sec.is-alt { background:
 radial-gradient(50% 60% at 100% 0%, var(--dv-mesh-b), transparent 70%),
 radial-gradient(40% 50% at 0% 100%, var(--dv-mesh-a), transparent 70%), var(--lp-bg2); }
.dv-h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(28px, 3.4vw, 40px); font-weight: 600; letter-spacing: -0.035em; line-height: 1.1; color: var(--lp-text); margin: 0 0 14px; }
.dv-lead { font-size: 16.5px; color: var(--lp-body); line-height: 1.7; max-width: 66ch; margin: 0; }
.dv-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 32px; }
.dv-sec.is-alt .dv-grid { margin-top: 0; }
.dv-wide { grid-column: 1 / -1; }
.dv-card { --c: var(--dv-teal); position: relative; min-width: 0; border-radius: 16px; padding: 24px 26px; background: var(--lp-bg); border: 1px solid var(--lp-border);
 transition: border-color 200ms ease, transform 260ms var(--lp-ease); }
.dv-card::before { content: ''; position: absolute; left: 24px; right: 24px; top: -1px; height: 2px; border-radius: 2px; background: linear-gradient(90deg, var(--c), transparent); }
.dv-card.is-code { padding: 0; border: none; background: none; }
.dv-card.is-code::before { display: none; }
@media (hover: hover) and (pointer: fine) { .dv-card:not(.is-code):hover { border-color: color-mix(in srgb, var(--c) 45%, var(--lp-border)); transform: translateY(-2px); } }
.c-indigo { --c: var(--dv-indigo); } .c-coral { --c: var(--dv-coral); } .c-gold { --c: var(--dv-gold); } .c-violet { --c: var(--dv-violet); } .c-teal { --c: var(--dv-teal); }
.dv-icon { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border-radius: 11px; margin-bottom: 14px;
 color: #fff; background: linear-gradient(140deg, var(--c), color-mix(in srgb, var(--c) 70%, #000)); box-shadow: 0 8px 18px -10px var(--c); }
[data-theme="dark"] .dv-icon { color: #08191C; background: linear-gradient(140deg, var(--c), color-mix(in srgb, var(--c) 75%, #fff)); }
.dv-card h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -0.02em; color: var(--lp-text); margin: 0 0 10px; }
.dv-card p, .dv-card li { font-size: 14.5px; color: var(--lp-body); line-height: 1.7; margin: 0; }
.dv-card p + p, .dv-card p + ul, .dv-card ul + p { margin-top: 10px; }
.dv-card ul { padding-left: 18px; margin: 10px 0 0; }
.dv-card li::marker { color: var(--c); }
.dv-card a { color: var(--lp-accent); font-weight: 600; }
.dv-card-links { display: flex; flex-direction: column; gap: 6px; }
.dv-card .dv-callout { display: flex; gap: 10px; align-items: flex-start; margin: 14px 0 0; padding: 14px 16px; border-radius: 12px; font-size: 14.5px; line-height: 1.6; color: var(--lp-body);
 background: color-mix(in srgb, var(--dv-teal) 8%, var(--lp-bg)); border: 1px solid color-mix(in srgb, var(--dv-teal) 30%, transparent); }
.dv-callout svg { flex-shrink: 0; margin-top: 3px; color: var(--dv-teal); }
.dv-scopes { list-style: none; padding: 0 !important; display: grid; gap: 10px; }
.dv-scopes li { padding-left: 0; }
.dv-scope { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 700; padding: 3px 9px; border-radius: 999px; margin-right: 6px; white-space: nowrap;
 color: var(--dv-get); background: color-mix(in srgb, var(--dv-get) 12%, transparent); }
.dv-scope.is-write { color: var(--dv-post); background: color-mix(in srgb, var(--dv-post) 13%, transparent); }
.dv-bignums { display: flex; gap: 28px; margin: 4px 0 12px; }
.dv-bignums div { display: flex; align-items: baseline; gap: 6px; }
.dv-bignums strong { font-family: var(--font-brand), system-ui, sans-serif; font-size: 40px; font-weight: 600; letter-spacing: -0.04em; line-height: 1;
 background: linear-gradient(120deg, var(--dv-teal), var(--dv-indigo)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.dv-bignums span { font-size: 13px; font-weight: 600; color: var(--lp-muted); }
.dv-errors { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: 28px; align-items: start; }
.dv-codes { list-style: none; padding: 0 !important; margin: 16px 0 0 !important; display: grid; gap: 0; }
.dv-codes li { display: grid; grid-template-columns: auto minmax(0, auto) minmax(0, 1fr); gap: 4px 12px; align-items: baseline; padding: 9px 0; border-top: 1px solid var(--lp-border); font-size: 13.5px; }
.dv-codes code { font-size: 12.5px; color: var(--lp-text); font-weight: 500; overflow-wrap: anywhere; }
.dv-codes span:last-child { color: var(--lp-body); line-height: 1.55; }
.dv-http { font-family: var(--font-mono); font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 5px; color: var(--dv-coral); background: color-mix(in srgb, var(--dv-coral) 12%, transparent); }
.dv-http.s-5 { color: var(--dv-patch); background: color-mix(in srgb, var(--dv-patch) 12%, transparent); }
.dv-trio { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }

/* ── Reference workspace ── */
.dv-ref { padding-bottom: 96px; }
.dv-ws-wrap { max-width: 1440px; margin: 32px auto 0; padding: 0 24px; }
.dv-ws { scroll-margin-top: 72px; display: grid; grid-template-columns: 300px minmax(0, 1fr); border: 1px solid var(--lp-border); border-radius: 18px; overflow: clip; background: var(--lp-bg);
 box-shadow: 0 30px 70px -40px var(--lp-shadow); }
.dv-side { position: sticky; top: 64px; align-self: start; height: calc(100vh - 64px); max-height: 1100px; border-right: 1px solid var(--lp-border); background: var(--lp-bg2); min-width: 0; }
.dv-side-in { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.dv-search { display: flex; align-items: center; gap: 8px; margin: 14px; padding: 0 12px; border-radius: 10px; border: 1px solid var(--lp-border); background: var(--lp-bg); color: var(--lp-muted); transition: border-color 140ms ease, box-shadow 140ms ease; }
.dv-search:focus-within { border-color: var(--dv-teal); box-shadow: 0 0 0 3px color-mix(in srgb, var(--dv-teal) 20%, transparent); }
.dv-search input { all: unset; flex: 1; min-width: 0; height: 40px; font-size: 14px; color: var(--lp-text); }
.dv-search input::placeholder { color: var(--lp-dim); }
.dv-groups { flex: 1; min-height: 0; overflow-y: auto; padding: 0 8px 16px; overscroll-behavior: contain; scrollbar-width: thin; }
.dv-none { font-size: 13.5px; color: var(--lp-muted); padding: 8px 10px; margin: 0; }
.dv-group + .dv-group { margin-top: 2px; }
.dv-group-head { all: unset; box-sizing: border-box; cursor: pointer; width: 100%; display: flex; align-items: center; gap: 6px; min-height: 36px; padding: 6px 8px; border-radius: 8px; font-size: 13px; font-weight: 700; color: var(--lp-text); }
.dv-group-head:hover { background: var(--lp-accent-bg); }
.dv-group-head:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: -2px; }
.dv-chev { flex-shrink: 0; color: var(--lp-dim); transition: transform 180ms var(--lp-ease); }
.dv-group-head[aria-expanded="true"] .dv-chev { transform: rotate(90deg); }
.dv-group-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dv-group-n { font-size: 11px; font-weight: 700; color: var(--lp-muted); background: var(--lp-surface); border-radius: 999px; padding: 2px 7px; }
.dv-group ul { list-style: none; margin: 2px 0 8px; padding: 0 0 0 10px; }
.dv-row { display: flex; align-items: flex-start; gap: 8px; padding: 7px 8px; border-radius: 8px; text-decoration: none; min-width: 0; border-left: 2px solid transparent; transition: background-color 120ms ease; }
.dv-row:hover { background: var(--lp-accent-bg); }
.dv-row.is-on { background: color-mix(in srgb, var(--dv-teal) 14%, var(--lp-bg)); border-left-color: var(--dv-teal); }
.dv-row .dv-mp { margin-top: 1px; }
.dv-row-text { display: flex; flex-direction: column; min-width: 0; }
.dv-row-sum { font-size: 13px; font-weight: 600; color: var(--lp-text); line-height: 1.35; }
.dv-row-path { font-family: var(--font-mono); font-size: 11px; color: var(--lp-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.dv-mbar { display: none; }

.dv-main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 0.92fr); min-width: 0; }
.dv-detail { padding: 30px 32px 40px; min-width: 0; }
.dv-code { padding: 24px; min-width: 0; background:
 radial-gradient(70% 40% at 100% 0%, rgba(139,135,255,0.10), transparent 70%),
 linear-gradient(180deg, #0A2226, #071417); }
.dv-code .dv-panel { position: sticky; top: 88px; }
.dv-code .dv-panel-label { color: #9CB4B6; }
.dv-ep-group { font-size: 13px; font-weight: 600; color: var(--dv-teal); margin-bottom: 6px; }
.dv-ep-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(22px, 2.2vw, 28px); font-weight: 600; letter-spacing: -0.03em; line-height: 1.2; color: var(--lp-text); margin: 0 0 18px; }
.dv-reqbar { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 7px 7px 7px 8px; border-radius: 12px; background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); }
.dv-url { flex: 1; min-width: 0; font-size: 13.5px; color: #EAF3F2; overflow-x: auto; white-space: nowrap; scrollbar-width: none; padding: 4px 0; }
.dv-url::-webkit-scrollbar { display: none; }
.dv-url-base { color: #7E9799; }
.dv-ph { color: #FFD479; }
.dv-reqbar .dv-copy.is-bar, .dv-basebar .dv-copy.is-bar { background: #0F8A80; border-color: #0F8A80; color: #fff; }
.dv-reqbar .dv-copy.is-bar:hover, .dv-basebar .dv-copy.is-bar:hover { background: #12A397; }
.dv-ep-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin: 14px 0 0; }
.dv-returns { font-size: 12.5px; color: var(--lp-muted); }
.dv-ep-desc { font-size: 15px; color: var(--lp-body); line-height: 1.7; margin: 14px 0 0; max-width: 72ch; }
.dv-tabs { display: flex; gap: 2px; margin: 24px 0 0; border-bottom: 1px solid var(--lp-border); overflow-x: auto; scrollbar-width: none; }
.dv-tabs button { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 7px; padding: 11px 12px; font-size: 13.5px; font-weight: 600; color: var(--lp-muted); border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; transition: color 140ms ease, border-color 140ms ease; }
.dv-tabs button:hover { color: var(--lp-text); }
.dv-tabs button.is-on { color: var(--lp-text); border-bottom-color: var(--dv-teal); }
.dv-tabs button:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: -2px; border-radius: 6px; }
.dv-tab-n { font-size: 11px; font-weight: 700; color: var(--dv-teal); background: color-mix(in srgb, var(--dv-teal) 12%, transparent); border-radius: 999px; padding: 1px 7px; }
.dv-tabpanel { padding-top: 16px; min-width: 0; }
.dv-empty { font-size: 14px; color: var(--lp-muted); margin: 0; }
.dv-fields { list-style: none; margin: 0; padding: 0; }
.dv-fields li { padding: 13px 0; border-bottom: 1px solid var(--lp-border); min-width: 0; }
.dv-fields li:first-child { padding-top: 4px; }
.dv-field-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; }
.dv-field-name { font-size: 13.5px; font-weight: 700; color: var(--lp-text); overflow-wrap: anywhere; }
.dv-field-type { font-family: var(--font-mono); font-size: 12px; color: var(--dv-indigo); overflow-wrap: anywhere; }
.dv-field-in { font-size: 11.5px; font-weight: 600; color: var(--lp-muted); background: var(--lp-surface); border-radius: 5px; padding: 1px 7px; }
.dv-req { font-size: 11.5px; font-weight: 700; color: var(--dv-coral); }
.dv-opt { font-size: 11.5px; font-weight: 600; color: var(--lp-dim); }
.dv-field-desc { font-size: 13.5px; color: var(--lp-body); line-height: 1.65; margin: 6px 0 0; max-width: 72ch; }
.dv-ctype { display: inline-block; font-family: var(--font-mono); font-size: 12px; color: var(--lp-muted); background: var(--lp-surface); border-radius: 6px; padding: 3px 8px; margin-bottom: 10px; }
.dv-resp-line { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.dv-resp-status { font-family: var(--font-mono); font-size: 13px; font-weight: 700; color: var(--dv-get); background: color-mix(in srgb, var(--dv-get) 12%, transparent); border-radius: 6px; padding: 3px 9px; }
.dv-link { display: inline-flex; align-items: center; min-height: 32px; margin-top: 6px; font-size: 13.5px; font-weight: 600; color: var(--lp-accent); }

/* Reduced motion: no lifts or slides. */
@media (prefers-reduced-motion: reduce) {
 .dv-card, .dv-chev { transition: none !important; }
 .dv-card:hover { transform: none !important; }
}

@media (max-width: 1180px) {
 .dv-hero-grid { grid-template-columns: minmax(0, 1fr); gap: 40px; }
 .dv-hero-code { max-width: 680px; }
 .dv-main { grid-template-columns: minmax(0, 1fr); }
 .dv-code { padding: 20px 24px 28px; }
 .dv-code .dv-panel { position: static; }
}
@media (max-width: 900px) {
 .dv-grid, .dv-trio, .dv-errors { grid-template-columns: minmax(0, 1fr); }
 .dv-ws { grid-template-columns: minmax(0, 1fr); }
 .dv-side { display: none; }
 .dv-mbar { display: flex; align-items: center; gap: 10px; position: sticky; top: 60px; z-index: 5; padding: 10px 12px; min-width: 0;
  background: var(--lp-nav); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-bottom: 1px solid var(--lp-border); }
 .dv-mbar-btn { all: unset; box-sizing: border-box; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 14px; border-radius: 10px; flex-shrink: 0;
  font-size: 14px; font-weight: 700; color: #fff; background: var(--lp-cta-bg); }
 [data-theme="dark"] .dv-mbar-btn { color: var(--lp-cta-fg); }
 .dv-mbar-btn .dv-group-n { background: rgba(255,255,255,0.18); color: inherit; }
 .dv-mbar-btn:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; }
 .dv-mbar-cur { display: inline-flex; align-items: center; gap: 8px; min-width: 0; font-size: 13px; font-weight: 600; color: var(--lp-text); }
 .dv-mbar-cur > span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .dv-sheet { position: fixed; inset: 0; z-index: 200; display: flex; flex-direction: column; background: var(--lp-bg); animation: dv-sheet 220ms var(--lp-ease) both; }
 .dv-sheet-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 0 16px; min-height: 56px; font-size: 16px; color: var(--lp-text); }
 .dv-sheet-x { all: unset; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 10px; color: var(--lp-text); }
 .dv-sheet-x:focus-visible { outline: 2px solid var(--lp-accent); }
 .dv-sheet .dv-side-in { flex: 1; min-height: 0; }
 .dv-sheet .dv-row { min-height: 44px; align-items: center; }
 .dv-sheet .dv-group-head { min-height: 44px; }
}
@keyframes dv-sheet { from { opacity: 0; transform: translate3d(0, 12px, 0); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .dv-sheet { animation: none; } }
@media (max-width: 760px) {
 .dv-hero { padding: 96px 0 48px; }
 .dv-inner { padding: 0 16px; }
 .dv-sec { padding: 56px 0; }
 .dv-ref { padding-bottom: 56px; }
 .dv-ws-wrap { padding: 0; margin-top: 24px; }
 .dv-ws { border-radius: 0; border-left: none; border-right: none; scroll-margin-top: 60px; }
 .dv-detail { padding: 22px 16px 28px; }
 .dv-code { padding: 18px 16px 24px; }
 .dv-card { padding: 20px 18px; }
 .dv-hero-code { padding: 10px; border-radius: 16px; }
 .dv-crumbs a, .dv-toc a { min-height: 44px; display: inline-flex; align-items: center; }
 .dv-codes li { grid-template-columns: auto minmax(0, 1fr); }
 .dv-codes li span:last-child { grid-column: 1 / -1; }
 .dv-pre { font-size: 12.5px; }
 .dv-tabs button { min-height: 44px; box-sizing: border-box; }
 .dv-copy { min-height: 36px; }
 .dv-reqbar .dv-copy span { display: none; }
 .dv-reqbar .dv-copy { min-width: 40px; justify-content: center; }
 .dv-bignums strong { font-size: 34px; }
}
`

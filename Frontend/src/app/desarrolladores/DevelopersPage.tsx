'use client'
// /desarrolladores — the developer reference, on the landing.
//
// Two halves with different owners:
//  · the guide (authentication, scopes, limits, errors, response format,
//    pagination, idempotency) is copy, in src/i18n/developers.ts, Spanish and
//    English;
//  · the endpoint reference is DATA. Requests (parameters, body schema, example,
//    error statuses) come from the backend's OpenAPI via
//    backend/scripts/export_public_api.py into src/data/public-api.json; what an
//    endpoint ANSWERS comes from real calls, recorded by
//    backend/scripts/capture_api_examples.py into public/api-response-examples.json.
//    Backend tests keep both files honest (test_public_api_snapshot.py,
//    test_public_api_examples.py), so this page cannot list a route a key cannot
//    call, show an example the route refuses, or document a list as an object.
//
// Layout: a sticky guide index and prose on top; below it the reference, in
// three panes — endpoint groups with search, the endpoint's request/response
// documentation, and a sticky dark code panel (cURL, JavaScript, Python and six
// more, from the same generator as the in-app /api screen). Every endpoint's
// summary is in the server-rendered HTML; the one selected is expanded in full,
// and `#<endpoint id>` deep-links to it.
//
// The landing's chrome (Nav/Footer) and primitives are imported, never edited
// here.
import Link from 'next/link'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, Copy, Link2, PanelLeft, Search, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { DEVELOPERS, type DevelopersCopy } from '@/i18n/developers'
import { LandingStyles } from '@/components/landing/primitives'
import { Nav, Footer } from '@/components/landing/chrome'
import { FinalSection } from '@/components/landing/sections'
import { CONTACT_EMAIL, mailHref } from '@/components/landing/contact'
import { SchemaTree, SCHEMA_TREE_CSS } from '@/components/apidocs/SchemaTree'
import type { SchemaNode, SchemaField } from '@/lib/apiSchema'
import { useResponseExample } from '@/lib/useResponseExamples'
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
  limits: { per_minute_per_key: number; window_seconds: number; per_day_per_key: Record<string, number | null> }
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

function CopyButton({ text, label, done, className = '', icon = 'copy' }: {
  text: string; label: string; done: string; className?: string; icon?: 'copy' | 'link'
}) {
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
  const Icon = icon === 'link' ? Link2 : Copy
  return (
    <button type="button" className={`dv-copy ${className}`} onClick={copy} aria-label={copied ? done : label}>
      {copied ? <Check size={14} aria-hidden /> : <Icon size={14} aria-hidden />}
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

function StatusChip({ status }: { status: number }) {
  return <span className={`dv-st s-${String(status)[0]}`}>{status}</span>
}

/** Request samples in nine languages, then the response that endpoint really gave. */
function CodePanel({ ep, base, codeLang, setCodeLang, W, idPrefix }: {
  ep: Endpoint; base: string; codeLang: CodeLang; setCodeLang: (l: CodeLang) => void
  W: DevelopersCopy['workspace']; idPrefix: string
}) {
  const code = useMemo(() => sampleFor(codeLang, ep, base), [codeLang, ep, base])
  const example = useResponseExample(ep.id)
  const hasJson = example?.example !== undefined
  const responseCode = useMemo(
    () => (hasJson ? JSON.stringify(example!.example, null, 2) : ENVELOPE_SAMPLE),
    [example, hasJson],
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
  const status = example?.status ?? ep.success_status
  const types = (example?.content_type ?? ep.response_content_types.join(', '))
  return (
    <div className="dv-panel">
      <div className="dv-panel-label">{W.request}</div>
      <CodeWindow code={code} lang={hlLangOf(codeLang)} W={W} tabs={tabs} />
      <div className="dv-panel-label">{W.response}</div>
      {status === 204 ? (
        <div className="dv-win">
          <div className="dv-win-bar">
            <span className="dv-win-title"><StatusChip status={204} /> {STATUS_TEXT[204]}</span>
          </div>
          <p className="dv-win-empty">{W.noContent}</p>
        </div>
      ) : example?.binary ? (
        <div className="dv-win">
          <div className="dv-win-bar">
            <span className="dv-win-title"><StatusChip status={status} /> {STATUS_TEXT[status] ?? ''} <span className="dv-win-dim">{types}</span></span>
          </div>
          <p className="dv-win-empty">{W.fileResponse(types)}</p>
        </div>
      ) : (
        <CodeWindow
          code={responseCode}
          lang="json"
          W={W}
          title={<><StatusChip status={status} /> {STATUS_TEXT[status] ?? ''} <span className="dv-win-dim">{types}</span></>}
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

function paramsNode(params: Endpoint['parameters']): SchemaNode {
  const fields: SchemaField[] = params.map(p => ({ ...p.schema, name: p.name, required: p.required }))
  return { type: 'object', fields }
}

/** What a caller can expect when the call does not succeed. The statuses a key
 *  can always meet come first; the route's own follow. */
function errorRows(ep: Endpoint): { status: number; text: string }[] {
  const generic: Record<number, string> = {
    401: 'Authorization header missing, or API key invalid, revoked or expired.',
    403: 'Key may not call this endpoint, is read-only on a write, or a permission check refused it.',
    429: 'Per-minute limit or daily ceiling reached; honour Retry-After.',
  }
  const own = ep.errors.filter(e => ![401, 403, 429].includes(e.status))
  const rows = [401, 403, 429].map(status => ({ status, text: generic[status] }))
  return [...rows, ...own.map(e => ({ status: e.status, text: e.description }))]
}

function EndpointView({ ep, D, base, active, groupLabel }: {
  ep: Endpoint; D: DevelopersCopy; base: string; active: boolean; groupLabel: string
}) {
  const R = D.reference
  const W = D.workspace
  const body = ep.request_body
  const example = useResponseExample(active ? ep.id : '')
  const params = ep.parameters
  const groups: { key: 'path' | 'query'; items: Endpoint['parameters'] }[] = [
    { key: 'path', items: params.filter(p => p.in === 'path') },
    { key: 'query', items: params.filter(p => p.in === 'query') },
  ]
  const headerParams = params.filter(p => p.in === 'header')
  const isFile = ep.response_content_types.some(t => !t.includes('json'))
  const link = typeof window === 'undefined' ? '' : `${window.location.origin}${window.location.pathname}#${ep.id}`

  return (
    <article id={ep.id} className="dv-ep" hidden={!active} aria-labelledby={`${ep.id}-title`}>
      <div className="dv-ep-group">{groupLabel}</div>
      <div className="dv-ep-titlebar">
        <h3 id={`${ep.id}-title`} className="dv-ep-title">{ep.summary}</h3>
        {active && <CopyButton text={link} label={W.copyLink} done={W.copied} icon="link" className="is-quiet" />}
      </div>

      <div className="dv-reqbar">
        <MethodPill method={ep.method} />
        <code className="dv-url">
          <span className="dv-url-base">{API.base_path}</span><PathText path={ep.path} />
        </code>
        <CopyButton text={`${base}${ep.path}`} label={W.copyUrl} done={W.copied} className="is-bar" />
      </div>

      <div className="dv-ep-meta">
        <span className={`dv-scope${ep.scope === 'write' ? ' is-write' : ''}`}>
          {ep.scope === 'write' ? W.scopeWrite : W.scopeRead}
        </span>
        <span className="dv-returns">{R.returns(ep.success_status, ep.response_content_types.join(', '))}</span>
      </div>
      {ep.description && <p className="dv-ep-desc">{ep.description}</p>}

      {active && (
        <>
          <section className="dv-block" aria-labelledby={`${ep.id}-req`}>
            <h4 id={`${ep.id}-req`} className="dv-h4">{W.sections.request}</h4>

            {groups.filter(g => g.items.length > 0).map(g => (
              <div key={g.key} className="dv-sub">
                <h5 className="dv-h5">{W.paramGroups[g.key]}</h5>
                <SchemaTree node={paramsNode(g.items)} L={D.schema} bare toolbar={false} defaultOpen={1} />
              </div>
            ))}

            <div className="dv-sub">
              <h5 className="dv-h5">{W.headersTitle}</h5>
              <ul className="dv-hdrs">
                <li>
                  <code className="st-name">Authorization</code>
                  <code className="st-chip">Bearer sk_live_…</code>
                  <span className="st-req">{R.required}</span>
                  <p className="st-desc">{W.authValue}</p>
                </li>
                {body && !body.content_type.startsWith('multipart/') && (
                  <li>
                    <code className="st-name">Content-Type</code>
                    <code className="st-chip">{body.content_type}</code>
                    <span className="st-req">{R.required}</span>
                  </li>
                )}
              </ul>
              {headerParams.length > 0 && (
                <SchemaTree node={paramsNode(headerParams)} L={D.schema} bare toolbar={false} defaultOpen={1} />
              )}
            </div>

            <div className="dv-sub">
              <h5 className="dv-h5">{W.bodyTitle}</h5>
              {!body ? <p className="dv-empty">{W.noBody}</p> : (
                <>
                  <p className="dv-body-meta">
                    <code className="st-chip">{body.content_type}</code>
                    <span>{body.required ? W.bodyRequired : W.bodyOptional}</span>
                  </p>
                  <SchemaTree node={body.schema} L={D.schema} defaultOpen={2} />
                </>
              )}
            </div>
          </section>

          <section className="dv-block" aria-labelledby={`${ep.id}-res`}>
            <h4 id={`${ep.id}-res`} className="dv-h4">{W.sections.responses}</h4>
            <div className="dv-sub">
              <h5 className="dv-h5"><StatusChip status={example?.status ?? ep.success_status} /> {W.successTitle}</h5>
              {ep.id === 'post-mcp' && <p className="dv-empty">{W.rpcResponse}</p>}
              {ep.success_status === 204 ? (
                <p className="dv-empty">{W.noContent}</p>
              ) : isFile ? (
                <p className="dv-empty">{W.fileResponse(ep.response_content_types.join(', '))}</p>
              ) : example?.schema ? (
                <>
                  <p className="dv-body-meta"><code className="st-chip">{example.content_type ?? 'application/json'}</code></p>
                  <SchemaTree node={example.schema} L={D.schema} defaultOpen={2} showRequired={false} />
                  <p className="dv-fine">{W.exampleNote}</p>
                </>
              ) : (
                <p className="dv-empty">{W.notCaptured}</p>
              )}
            </div>
            <div className="dv-sub">
              <h5 className="dv-h5">{W.errorsTitle}</h5>
              <p className="dv-fine">{W.errorsLead}</p>
              <ul className="dv-errs">
                {errorRows(ep).map(e => (
                  <li key={`${e.status}-${e.text}`}>
                    <StatusChip status={e.status} />
                    <span>{e.text}</span>
                  </li>
                ))}
              </ul>
              <a href="#errores" className="dv-link">{D.errors.title}</a>
            </div>
          </section>
        </>
      )}
    </article>
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
        {query && (
          <button type="button" className="dv-search-x" onClick={() => setQuery('')} aria-label={W.clear}><X size={14} aria-hidden /></button>
        )}
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
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Set<string>>(() => new Set([ALL[0].tag]))
  const [sheet, setSheet] = useState(false)
  const wsRef = useRef<HTMLDivElement>(null)

  const select = useCallback((id: string, scroll = true) => {
    const ep = BY_ID.get(id)
    if (!ep) return
    setSelected(id)
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
  // Keep the selected row visible in the sidebar (a deep link can land far down it).
  useEffect(() => {
    const row = document.querySelector('.dv-side .dv-row.is-on') as HTMLElement | null
    const box = row?.closest('.dv-groups') as HTMLElement | null
    if (!row || !box) return
    const r = row.getBoundingClientRect()
    const b = box.getBoundingClientRect()
    if (r.top < b.top || r.bottom > b.bottom) box.scrollTop += r.top - b.top - b.height / 3
  }, [selected, open])
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

// ── The guide ─────────────────────────────────────────────────────────────────

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="dv-gs" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="dv-h2">{title}</h2>
      {children}
    </section>
  )
}

function Table({ rows, head }: { rows: React.ReactNode[][]; head?: string[] }) {
  return (
    <div className="dv-table-wrap">
      <table className="dv-table">
        {head && <thead><tr>{head.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>}
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => (j === 0 ? <th key={j} scope="row">{c}</th> : <td key={j}>{c}</td>))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const SAMPLE_403 = `{
  "detail": "This endpoint writes and the API key is read-only. Use a write key.",
  "error_code": "api_key_scope_insufficient",
  "error_params": { "required_scope": "write", "key_scope": "read" }
}`

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DevelopersPage() {
  const { lang } = useLanguage()
  const D = DEVELOPERS[lang]
  const W = D.workspace
  const base = useMemo(apiBase, [])
  const perDay = API.limits.per_day_per_key
  const [codeLang, setCodeLangState] = useState<CodeLang>('curl')
  useEffect(() => { setCodeLangState(loadSampleLang()) }, [])
  const setCodeLang = (l: CodeLang) => {
    setCodeLangState(l)
    saveSampleLang(l)
  }
  const toc: [string, string][] = [
    ['#autenticacion', D.auth.title],
    ['#limites', D.limits.title],
    ['#errores', D.errors.title],
    ['#formato', D.envelope.title],
    ['#paginacion', D.pagination.title],
    ['#idempotencia', D.idempotency.title],
    ['#referencia', D.reference.title],
  ]

  return (
    <div className="lp dv" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: DEV_CSS + SCHEMA_TREE_CSS }} />
      <Nav onHome={false} localAnchors={['contacto']} />
      <main>
        <header className="dv-hero">
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

        <div className="dv-doc">
          <div className="dv-inner dv-doc-grid">
            <nav aria-label={D.toc} className="dv-toc">
              <div className="dv-toc-title">{D.guideNav}</div>
              <ul>
                {toc.map(([href, text]) => <li key={href}><a href={href}>{text}</a></li>)}
              </ul>
            </nav>

            <div className="dv-doc-body">
              <Section id="autenticacion" title={D.auth.title}>
                <p>{D.auth.body}</p>
                <CodeWindow
                  title="shell"
                  code={`export STOCKAI=${base}\nexport STOCKAI_KEY=sk_live_…\n\ncurl "$STOCKAI/planning" \\\n  -H "Authorization: Bearer $STOCKAI_KEY"`}
                  lang="bash"
                  W={W}
                />
                <p className="dv-note">{D.auth.note}</p>

                <h3 className="dv-h3">{D.scopes.title}</h3>
                <p>{D.scopes.body}</p>
                <Table rows={[
                  [<span key="r" className="dv-scope">{D.scopes.read}</span>, D.scopes.readDesc],
                  [<span key="w" className="dv-scope is-write">{D.scopes.write}</span>, D.scopes.writeDesc],
                ]} />
                <p>{D.scopes.howToGet}</p>

                <h3 className="dv-h3">{D.never.title}</h3>
                <p>{D.never.body}</p>
                <ul className="dv-list">{D.never.items.map(i => <li key={i}>{i}</li>)}</ul>
                <p>{D.never.outward}</p>
              </Section>

              <Section id="limites" title={D.limits.title}>
                <Table
                  head={[D.facts.auth, '']}
                  rows={[
                    [<code key="a">/ min</code>, D.limits.perMinute(API.limits.per_minute_per_key)],
                    ...(typeof perDay.demo === 'number' ? [[<code key="d">/ 24 h</code>, D.limits.perDayDemo(perDay.demo)]] : []),
                    ...(typeof perDay.free === 'number' ? [[<code key="f">/ 24 h</code>, D.limits.perDayFree(perDay.free)]] : []),
                    [<code key="p">/ 24 h</code>, D.limits.perDayPaid],
                  ]}
                />
                <p>{D.limits.over}</p>
                <p>{D.limits.noHeaders}</p>
                <h3 className="dv-h3" id="cobro">{D.billing.title}</h3>
                <p>{D.billing.body}</p>
                <p className="dv-links">
                  <Link href="/precios">{D.billing.pricingLink}</Link>
                  <a href={mailHref('API StockAI')}>{D.billing.contact(CONTACT_EMAIL)}</a>
                </p>
              </Section>

              <Section id="errores" title={D.errors.title}>
                <p>{D.errors.body}</p>
                <Table
                  rows={D.errors.codes.map(([code, desc]) => {
                    const m = /^(\d{3}(?:\s*\/\s*\d{3})?)\s*—\s*(.*)$/.exec(desc)
                    return [
                      <code key={code + desc}>{code}</code>,
                      m ? <span key="s" className="dv-row-desc"><span className="dv-st s-4">{m[1]}</span> {m[2]}</span> : desc,
                    ]
                  })}
                />
                <h3 className="dv-h3">{D.errors.shapeTitle}</h3>
                <Table rows={D.errors.shapes.map(([k, v]) => [<code key={k}>{k}</code>, v])} />
                <CodeWindow
                  title={<><StatusChip status={403} /> {D.errors.sampleTitle}</>}
                  code={SAMPLE_403}
                  lang="json"
                  W={W}
                />
              </Section>

              <Section id="formato" title={D.envelope.title}>
                <p>{D.envelope.body}</p>
                <CodeWindow title={<><StatusChip status={200} /> application/json</>} code={ENVELOPE_SAMPLE} lang="json" W={W} />
                <p>{D.envelope.fileNote}</p>
                <p>{D.envelope.rpcNote}</p>
                <h3 className="dv-h3">{D.envelope.formatsTitle}</h3>
                <Table rows={D.envelope.formats.map(([k, v]) => [k, v])} />
              </Section>

              <Section id="paginacion" title={D.pagination.title}>
                <p>{D.pagination.body}</p>
                <Table rows={D.pagination.items.map(([k, v]) => [<code key={k}>{k}</code>, v])} />
              </Section>

              <Section id="idempotencia" title={D.idempotency.title}>
                <p>{D.idempotency.body}</p>
                <CodeWindow title="HTTP" code={D.idempotency.header} lang="bash" W={W} />
              </Section>

              <Section id="mcp" title={D.mcp.title}>
                <p>{D.mcp.body}</p>
              </Section>
            </div>
          </div>
        </div>

        <section id="referencia" className="dv-ref" aria-labelledby="referencia-h">
          <div className="dv-inner">
            <h2 id="referencia-h" className="dv-h2">{D.reference.title}</h2>
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
// A calm, document-first palette: the landing's own neutrals and its teal accent,
// no gradients, no decorative colour. The code surfaces are dark in BOTH site
// themes (owner: no white editor), so their colours are fixed values, not theme
// tokens; token colours were checked against --dv-code-bg for at least 4.5:1.
// Method colours are muted, one hue per verb, used only on the small pills.
const DEV_CSS = `
.dv {
 --dv-code-bg: #08191C; --dv-code-bar: #0D2328; --dv-code-bd: #1A353B; --dv-code-text: #DCE8E6; --dv-code-dim: #8FA6A8;
 --dv-teal: var(--lp-accent);
 --dv-get: #17795E; --dv-post: #946213; --dv-put: #2B64A8; --dv-patch: #735599; --dv-delete: #A8433D;
 --dv-err: #A8433D;
 --font-mono: var(--font-code), ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace;
 --st-fg: var(--lp-text); --st-muted: var(--lp-body); --st-dim: var(--lp-muted); --st-border: var(--lp-border);
 --st-surface: var(--lp-surface); --st-accent: var(--lp-accent); --st-warn: var(--dv-post); --st-mono: var(--font-mono);
}
[data-theme="dark"] .dv {
 --dv-get: #5FC3A0; --dv-post: #D1A24C; --dv-put: #6FA3E0; --dv-patch: #B79BDD; --dv-delete: #E08A83; --dv-err: #E08A83;
}
.dv code, .dv pre, .dv kbd { font-family: var(--font-mono); }
.dv :focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; }

/* Method pills: one muted hue per verb. */
.dv-mp { --m: var(--dv-get); display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
 font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; letter-spacing: 0.03em; line-height: 1;
 min-width: 56px; padding: 6px 8px; border-radius: 6px; color: var(--m);
 background: color-mix(in srgb, var(--m) 11%, transparent); border: 1px solid color-mix(in srgb, var(--m) 28%, transparent); }
.dv-mp.is-sm { font-size: 10px; min-width: 40px; padding: 4px 5px; border-radius: 5px; }
.dv-mp.m-POST { --m: var(--dv-post); } .dv-mp.m-PUT { --m: var(--dv-put); } .dv-mp.m-PATCH { --m: var(--dv-patch); } .dv-mp.m-DELETE { --m: var(--dv-delete); }
/* On the always-dark surfaces the bright set reads; the light set would not. */
.dv-hero-req .dv-mp, .dv-reqbar .dv-mp { --dv-get: #5FC3A0; --dv-post: #D1A24C; --dv-put: #6FA3E0; --dv-patch: #B79BDD; --dv-delete: #E08A83; }

/* Status chips: the digit family tells the story, calmly. */
.dv-st { display: inline-block; font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; line-height: 1.2; padding: 2px 7px; border-radius: 5px;
 color: var(--dv-get); background: color-mix(in srgb, var(--dv-get) 12%, transparent); }
.dv-st.s-4 { color: var(--dv-post); background: color-mix(in srgb, var(--dv-post) 13%, transparent); }
.dv-st.s-5 { color: var(--dv-err); background: color-mix(in srgb, var(--dv-err) 12%, transparent); }
.dv-win .dv-st { color: #08191C; background: #7FD8B8; }
.dv-win .dv-st.s-4 { background: #E6C27A; } .dv-win .dv-st.s-5 { background: #F0A8A0; }

/* ── Hero ── */
.dv-hero { padding: 112px 0 64px; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.dv-inner { position: relative; max-width: 1240px; margin: 0 auto; padding: 0 40px; }
.dv-hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 520px); gap: 56px; align-items: center; }
.dv-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 20px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.dv-crumbs li + li::before { content: '/'; margin-right: 8px; color: var(--lp-dim); }
.dv-crumbs a { color: var(--lp-muted); text-decoration: none; }
.dv-crumbs a:hover { color: var(--lp-accent); }
.dv-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.dv-h1 { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(32px, 4vw, 48px); font-weight: 600; line-height: 1.08; letter-spacing: -0.04em; margin: 0 0 18px; color: var(--lp-text); }
.dv-intro { font-size: clamp(15.5px, 1.3vw, 17px); color: var(--lp-body); line-height: 1.7; max-width: 60ch; margin: 0; }
.dv-facts { display: flex; flex-wrap: wrap; gap: 18px 28px; margin: 28px 0 0; }
.dv-fact { min-width: 0; }
.dv-fact.is-wide { flex: 1 1 100%; }
.dv-fact dt { font-size: 12.5px; font-weight: 600; color: var(--lp-muted); margin-bottom: 7px; }
.dv-fact dd { margin: 0; font-size: 14px; color: var(--lp-text); min-width: 0; }
.dv-basebar { display: flex; align-items: center; gap: 8px; max-width: 560px; padding: 6px 6px 6px 14px; border-radius: 10px; background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); }
.dv-basebar code { flex: 1; min-width: 0; font-size: 13.5px; color: #9ADFD0; overflow-x: auto; white-space: nowrap; scrollbar-width: none; }
.dv-chip-code { display: inline-block; max-width: 100%; font-size: 13px; padding: 6px 11px; border-radius: 8px; background: var(--lp-surface); border: 1px solid var(--lp-border); color: var(--lp-text); overflow-wrap: anywhere; }
.dv-counts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.dv-count-total { font-weight: 700; margin-right: 4px; }
.dv-count { font-size: 12.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--lp-border); color: var(--lp-body); background: var(--lp-surface); }
.dv-hero-code { min-width: 0; padding: 12px; border-radius: 14px; background: #0B2227; border: 1px solid var(--dv-code-bd); }
.dv-hero-req { display: flex; align-items: center; gap: 10px; padding: 4px 4px 12px; min-width: 0; }
.dv-hero-req code { font-size: 14px; color: #EAF3F2; overflow-x: auto; white-space: nowrap; scrollbar-width: none; }
.dv-hero-code .dv-panel-label { color: #9CB4B6; }

/* ── The dark editor ── */
.dv-panel { display: grid; gap: 8px; min-width: 0; }
.dv-panel-label { font-size: 11.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--lp-muted); margin-top: 6px; }
.dv-panel-label:first-child { margin-top: 0; }
.dv-win { min-width: 0; border-radius: 10px; overflow: hidden; background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); color: var(--dv-code-text); }
.dv-win-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 42px; padding: 0 6px 0 12px; background: var(--dv-code-bar); border-bottom: 1px solid var(--dv-code-bd); }
.dv-win-title { font-size: 12.5px; font-weight: 600; color: #C9D8D6; display: inline-flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dv-win-dim { color: var(--dv-code-dim); font-weight: 500; }
.dv-win-empty { margin: 0; padding: 16px; font-size: 13px; color: var(--dv-code-dim); }
.dv-pre { margin: 0; padding: 14px 16px 16px; overflow: auto; max-height: 460px; font-size: 12.75px; line-height: 1.7; white-space: pre; color: var(--dv-code-text); tab-size: 2; max-width: 100%; }
.dv-pre:focus-visible { outline: 2px solid #7FE3D6; outline-offset: -2px; }
.dv-pre::-webkit-scrollbar { height: 8px; width: 8px; } .dv-pre::-webkit-scrollbar-thumb { background: #24444A; border-radius: 8px; }
.tk-kw { color: #D7A6FF; } .tk-str { color: #A8E6A3; } .tk-num { color: #FFB27A; } .tk-lit { color: #FF9EB5; }
.tk-com { color: #8FA6A8; font-style: italic; } .tk-var { color: #FFD479; } .tk-flag { color: #FF9580; }
.tk-fn { color: #8CC8FF; } .tk-prop { color: #7FE3D6; } .tk-pun { color: #A9BDBF; }
.dv-langs { display: flex; gap: 2px; min-width: 0; overflow-x: auto; scrollbar-width: none;
 -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent); mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent); }
.dv-langs button { all: unset; cursor: pointer; font-size: 12.5px; font-weight: 600; color: #9CB4B6; padding: 12px 10px 10px; border-bottom: 2px solid transparent; white-space: nowrap; transition: color 140ms ease, border-color 140ms ease; }
.dv-langs button:hover { color: #EAF3F2; }
.dv-langs button.is-on { color: #fff; border-bottom-color: #5FC3A0; }
.dv-langs button:focus-visible { outline: 2px solid #7FE3D6; outline-offset: -2px; border-radius: 4px; }
.dv-copy { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; cursor: pointer; font: inherit; font-size: 12px; font-weight: 600;
 color: #C9D8D6; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); border-radius: 7px; padding: 6px 10px; min-height: 30px; transition: background-color 140ms ease, color 140ms ease; }
.dv-copy:hover { background: rgba(127,227,214,0.14); color: #fff; }
.dv-copy:focus-visible { outline: 2px solid #7FE3D6; outline-offset: 2px; }
.dv-copy.is-quiet { color: var(--lp-body); background: transparent; border-color: var(--lp-border); }
.dv-copy.is-quiet:hover { color: var(--lp-text); background: var(--lp-surface); }
.dv-copy.is-quiet:focus-visible { outline-color: var(--lp-accent); }

/* ── Guide: index + prose ── */
.dv-doc { background: var(--lp-bg); padding: 56px 0 24px; }
.dv-doc-grid { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 56px; align-items: start; }
.dv-toc { position: sticky; top: 88px; }
.dv-toc-title { font-size: 11.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--lp-muted); margin-bottom: 10px; }
.dv-toc ul { list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--lp-border); }
.dv-toc a { display: block; padding: 6px 0 6px 14px; margin-left: -1px; border-left: 2px solid transparent; font-size: 13.5px; color: var(--lp-body); text-decoration: none; }
.dv-toc a:hover { color: var(--lp-text); border-left-color: var(--lp-accent); }
.dv-doc-body { min-width: 0; max-width: 820px; }
.dv-gs { padding: 0 0 48px; scroll-margin-top: 88px; }
.dv-gs + .dv-gs { padding-top: 40px; border-top: 1px solid var(--lp-border); }
.dv-gs p { font-size: 15px; color: var(--lp-body); line-height: 1.75; margin: 0 0 14px; max-width: 70ch; }
.dv-gs .dv-win { margin: 16px 0; }
.dv-h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(24px, 2.6vw, 30px); font-weight: 600; letter-spacing: -0.03em; line-height: 1.15; color: var(--lp-text); margin: 0 0 16px; }
.dv-h3 { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; color: var(--lp-text); margin: 32px 0 10px; scroll-margin-top: 88px; }
.dv-note { padding: 12px 14px; border-left: 3px solid var(--lp-accent); background: var(--lp-surface); border-radius: 0 8px 8px 0; }
.dv-list { margin: 0 0 14px; padding-left: 20px; }
.dv-list li { font-size: 15px; color: var(--lp-body); line-height: 1.7; margin-bottom: 4px; }
.dv-links { display: flex; flex-direction: column; gap: 6px; }
.dv-links a { color: var(--lp-accent); font-weight: 600; }
.dv-table-wrap { overflow-x: auto; margin: 12px 0 16px; border: 1px solid var(--lp-border); border-radius: 10px; }
.dv-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.dv-table th, .dv-table td { text-align: left; vertical-align: top; padding: 10px 14px; border-bottom: 1px solid var(--lp-border); color: var(--lp-body); line-height: 1.6; }
.dv-table tr:last-child > * { border-bottom: none; }
.dv-table thead th { font-size: 12px; font-weight: 700; color: var(--lp-muted); background: var(--lp-surface); }
.dv-table tbody th { font-weight: 600; color: var(--lp-text); white-space: nowrap; width: 1%; }
.dv-table code { font-size: 12.75px; color: var(--lp-text); }
.dv-row-desc { display: inline; }
.dv-scope { display: inline-flex; align-items: center; font-size: 11.5px; font-weight: 700; padding: 3px 9px; border-radius: 999px; white-space: nowrap;
 color: var(--dv-get); background: color-mix(in srgb, var(--dv-get) 11%, transparent); border: 1px solid color-mix(in srgb, var(--dv-get) 26%, transparent); }
.dv-scope.is-write { color: var(--dv-post); background: color-mix(in srgb, var(--dv-post) 11%, transparent); border-color: color-mix(in srgb, var(--dv-post) 28%, transparent); }

/* ── Reference workspace ── */
.dv-ref { padding: 56px 0 96px; background: var(--lp-bg2); border-top: 1px solid var(--lp-border); scroll-margin-top: 64px; }
.dv-lead { font-size: 15.5px; color: var(--lp-body); line-height: 1.7; max-width: 70ch; margin: 0; }
.dv-ws-wrap { max-width: 1480px; margin: 28px auto 0; padding: 0 24px; }
.dv-ws { scroll-margin-top: 72px; display: grid; grid-template-columns: 290px minmax(0, 1fr); border: 1px solid var(--lp-border); border-radius: 14px; overflow: clip; background: var(--lp-bg); }
.dv-side { position: sticky; top: 64px; align-self: start; height: calc(100vh - 64px); max-height: 1100px; border-right: 1px solid var(--lp-border); background: var(--lp-bg2); min-width: 0; }
.dv-side-in { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.dv-search { display: flex; align-items: center; gap: 8px; margin: 14px; padding: 0 6px 0 12px; border-radius: 9px; border: 1px solid var(--lp-border); background: var(--lp-bg); color: var(--lp-muted); transition: border-color 140ms ease, box-shadow 140ms ease; }
.dv-search:focus-within { border-color: var(--lp-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--lp-accent) 18%, transparent); }
.dv-search input { all: unset; flex: 1; min-width: 0; height: 40px; font-size: 14px; color: var(--lp-text); }
.dv-search input::placeholder { color: var(--lp-dim); }
.dv-search input::-webkit-search-cancel-button { display: none; }
.dv-search-x { all: unset; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 6px; color: var(--lp-muted); }
.dv-search-x:hover { background: var(--lp-surface); color: var(--lp-text); }
.dv-groups { flex: 1; min-height: 0; overflow-y: auto; padding: 0 8px 16px; overscroll-behavior: contain; scrollbar-width: thin; }
.dv-none { font-size: 13.5px; color: var(--lp-muted); padding: 8px 10px; margin: 0; }
.dv-group + .dv-group { margin-top: 2px; }
.dv-group-head { all: unset; box-sizing: border-box; cursor: pointer; width: 100%; display: flex; align-items: center; gap: 6px; min-height: 36px; padding: 6px 8px; border-radius: 8px; font-size: 12.5px; font-weight: 700; color: var(--lp-text); }
.dv-group-head:hover { background: var(--lp-surface); }
.dv-group-head:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: -2px; }
.dv-chev { flex-shrink: 0; color: var(--lp-dim); transition: transform 160ms ease; }
.dv-group-head[aria-expanded="true"] .dv-chev { transform: rotate(90deg); }
.dv-group-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dv-group-n { font-size: 11px; font-weight: 700; color: var(--lp-muted); background: var(--lp-surface); border-radius: 999px; padding: 2px 7px; }
.dv-group ul { list-style: none; margin: 2px 0 8px; padding: 0 0 0 8px; }
.dv-row { display: flex; align-items: flex-start; gap: 8px; padding: 7px 8px; border-radius: 8px; text-decoration: none; min-width: 0; border-left: 2px solid transparent; transition: background-color 120ms ease; }
.dv-row:hover { background: var(--lp-surface); }
.dv-row.is-on { background: color-mix(in srgb, var(--lp-accent) 11%, var(--lp-bg)); border-left-color: var(--lp-accent); }
.dv-row .dv-mp { margin-top: 1px; }
.dv-row-text { display: flex; flex-direction: column; min-width: 0; }
.dv-row-sum { font-size: 13px; font-weight: 600; color: var(--lp-text); line-height: 1.35; }
.dv-row-path { font-family: var(--font-mono); font-size: 11px; color: var(--lp-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.dv-mbar { display: none; }

.dv-main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 440px); min-width: 0; }
.dv-detail { padding: 28px 32px 48px; min-width: 0; }
.dv-code { padding: 20px; min-width: 0; background: #0A1F23; border-left: 1px solid var(--dv-code-bd); }
.dv-code .dv-panel { position: sticky; top: 84px; }
.dv-code .dv-panel-label { color: #9CB4B6; }
/* The panel is sticky, so it must fit the viewport: each window scrolls inside itself. */
.dv-code .dv-panel .dv-win:nth-child(2) .dv-pre { max-height: 230px; }
.dv-code .dv-panel .dv-win:nth-child(4) .dv-pre { max-height: max(240px, calc(100vh - 470px)); }
.dv-ep-group { font-size: 12.5px; font-weight: 600; color: var(--lp-muted); margin-bottom: 6px; }
.dv-ep-titlebar { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin: 0 0 16px; }
.dv-ep-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(21px, 2vw, 26px); font-weight: 600; letter-spacing: -0.03em; line-height: 1.2; color: var(--lp-text); margin: 0; }
.dv-reqbar { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 7px 7px 7px 8px; border-radius: 10px; background: var(--dv-code-bg); border: 1px solid var(--dv-code-bd); }
.dv-url { flex: 1; min-width: 0; font-size: 13.5px; color: #EAF3F2; overflow-x: auto; white-space: nowrap; scrollbar-width: none; padding: 4px 0; }
.dv-url::-webkit-scrollbar { display: none; }
.dv-url-base { color: #7E9799; }
.dv-ph { color: #FFD479; }
.dv-reqbar .dv-copy.is-bar, .dv-basebar .dv-copy.is-bar { background: rgba(255,255,255,0.08); }
.dv-ep-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin: 14px 0 0; }
.dv-returns { font-size: 12.5px; color: var(--lp-muted); }
.dv-ep-desc { white-space: pre-line; font-size: 15px; color: var(--lp-body); line-height: 1.7; margin: 14px 0 0; max-width: 72ch; }
.dv-block { margin-top: 32px; padding-top: 24px; border-top: 1px solid var(--lp-border); }
.dv-h4 { font-size: 13px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--lp-muted); margin: 0 0 16px; }
.dv-h5 { display: flex; align-items: center; gap: 8px; font-size: 14.5px; font-weight: 650; color: var(--lp-text); margin: 0 0 10px; }
.dv-sub { margin-bottom: 26px; min-width: 0; }
.dv-sub:last-child { margin-bottom: 0; }
.dv-empty { font-size: 14px; color: var(--lp-muted); margin: 0; }
.dv-fine { font-size: 12.5px; color: var(--lp-muted); margin: 10px 0 0; line-height: 1.6; }
.dv-body-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin: 0 0 12px; font-size: 13px; color: var(--lp-body); }
.dv-hdrs { list-style: none; margin: 0; padding: 0; }
.dv-hdrs li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px 8px; padding: 10px 0; border-bottom: 1px solid var(--lp-border); }
.dv-hdrs li:last-child { border-bottom: none; }
.dv-hdrs .st-desc { flex-basis: 100%; padding-left: 0; }
.dv-errs { list-style: none; margin: 10px 0 8px; padding: 0; }
.dv-errs li { display: flex; gap: 10px; align-items: baseline; padding: 8px 0; border-bottom: 1px solid var(--lp-border); font-size: 13.5px; color: var(--lp-body); line-height: 1.55; }
.dv-errs li:last-child { border-bottom: none; }
.dv-errs .dv-st { flex-shrink: 0; }
.dv-link { display: inline-flex; align-items: center; min-height: 32px; font-size: 13.5px; font-weight: 600; color: var(--lp-accent); }

@media (prefers-reduced-motion: reduce) { .dv-chev { transition: none !important; } .dv-sheet { animation: none !important; } }

@media (max-width: 1280px) {
 .dv-main { grid-template-columns: minmax(0, 1fr) minmax(320px, 380px); }
 .dv-detail { padding: 24px 24px 40px; }
}
@media (max-width: 1180px) {
 .dv-hero-grid { grid-template-columns: minmax(0, 1fr); gap: 36px; }
 .dv-hero-code { max-width: 680px; }
 .dv-doc-grid { grid-template-columns: minmax(0, 1fr); gap: 0; }
 .dv-toc { position: static; margin-bottom: 28px; }
 .dv-toc ul { display: flex; flex-wrap: wrap; gap: 6px; border-left: none; }
 .dv-toc a { padding: 7px 14px; margin: 0; border: 1px solid var(--lp-border); border-radius: 999px; font-size: 13px; }
 .dv-main { grid-template-columns: minmax(0, 1fr); }
 .dv-code { padding: 20px 24px 28px; border-left: none; border-top: 1px solid var(--dv-code-bd); }
 .dv-code .dv-panel { position: static; }
}
@media (max-width: 900px) {
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
 .dv-sheet { position: fixed; inset: 0; z-index: 200; display: flex; flex-direction: column; background: var(--lp-bg); animation: dv-sheet 200ms ease both; }
 .dv-sheet-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 0 16px; min-height: 56px; font-size: 16px; color: var(--lp-text); }
 .dv-sheet-x { all: unset; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 10px; color: var(--lp-text); }
 .dv-sheet-x:focus-visible { outline: 2px solid var(--lp-accent); }
 .dv-sheet .dv-side-in { flex: 1; min-height: 0; }
 .dv-sheet .dv-row { min-height: 44px; align-items: center; }
 .dv-sheet .dv-group-head { min-height: 44px; }
}
@keyframes dv-sheet { from { opacity: 0; transform: translate3d(0, 10px, 0); } to { opacity: 1; transform: none; } }
@media (max-width: 760px) {
 .dv-hero { padding: 92px 0 40px; }
 .dv-inner { padding: 0 16px; }
 .dv-doc { padding-top: 36px; }
 .dv-gs { padding-bottom: 32px; }
 .dv-gs + .dv-gs { padding-top: 28px; }
 .dv-ref { padding: 40px 0 56px; }
 .dv-ws-wrap { padding: 0; margin-top: 20px; }
 .dv-ws { border-radius: 0; border-left: none; border-right: none; scroll-margin-top: 60px; }
 .dv-detail { padding: 20px 16px 28px; }
 .dv-code { padding: 18px 16px 24px; }
 .dv-hero-code { padding: 10px; border-radius: 12px; }
 .dv-crumbs a, .dv-toc a { min-height: 44px; display: inline-flex; align-items: center; box-sizing: border-box; }
 .dv-pre { font-size: 12.5px; }
 .dv-copy { min-height: 36px; }
 .dv-ep-titlebar { flex-direction: column; gap: 8px; }
 .dv-reqbar .dv-copy span { display: none; }
 .dv-reqbar .dv-copy { min-width: 40px; justify-content: center; }
 .dv-table th, .dv-table td { padding: 9px 11px; }
 .dv-table tbody th { white-space: normal; }
}
`

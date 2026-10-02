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
// The landing's chrome (Nav/Footer) and primitives are imported, never edited
// here. Each endpoint is a <details>: two hundred of them open would be a wall,
// and the closed summary line (method, path, scope, summary) is what a reader
// scans for anyway. The content is in the HTML either way.
import Link from 'next/link'
import { useMemo } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { DEVELOPERS, type DevelopersCopy } from '@/i18n/developers'
import { LandingStyles, Section, H2, Lead, useScrollReveal } from '@/components/landing/primitives'
import { Nav, Footer } from '@/components/landing/chrome'
import { FinalSection } from '@/components/landing/sections'
import { CONTACT_EMAIL, mailHref } from '@/components/landing/contact'
import { appHref, SITE_URL } from '@/lib/siteUrls'
import raw from '@/data/public-api.json'

type Param = {
  name: string; in: string; required: boolean; type: string; description: string; example: unknown
}
type Field = { name: string; type: string; required: boolean; description: string }
type Endpoint = {
  id: string
  method: string
  path: string
  tag: string
  scope: 'read' | 'write'
  summary: string
  description: string
  parameters: Param[]
  request_body: { content_type: string; required: boolean; fields: Field[]; example: unknown } | null
  success_status: number
  response_content_types: string[]
}
type Snapshot = {
  base_path: string
  counts: { total: number; read: number; write: number }
  limits: { per_minute_per_key: number; per_day_per_key: Record<string, number | null> }
  tags: { tag: string; endpoints: Endpoint[] }[]
}

const API = raw as unknown as Snapshot

const DEV_CSS = `
.dev-hero { position: relative; padding: 132px 0 56px; overflow: hidden; isolation: isolate; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.dev-inner { position: relative; max-width: 1120px; margin: 0 auto; padding: 0 48px; }
.dev-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 24px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.dev-crumbs li + li::before { content: '/'; margin-right: 8px; color: var(--lp-dim); }
.dev-crumbs a { color: var(--lp-muted); text-decoration: none; }
.dev-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.dev-h1 { font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(32px, 4.6vw, 54px); font-weight: 600; line-height: 1.06; letter-spacing: -0.04em; color: var(--lp-text); margin: 0 0 20px; }
.dev-intro { font-size: clamp(16px, 1.5vw, 18px); color: var(--lp-body); line-height: 1.65; max-width: 64ch; margin: 0; }
.dev-facts { display: flex; flex-wrap: wrap; gap: 28px 44px; margin-top: 32px; }
.dev-fact dt { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--lp-dim); margin-bottom: 6px; }
.dev-fact dd { margin: 0; font-size: 14px; color: var(--lp-text); }
.dev-code-inline { font-family: ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace; font-size: 13px; background: var(--lp-surface); border: 1px solid var(--lp-border); border-radius: 6px; padding: 2px 7px; word-break: break-all; }
.dev-toc { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 30px; }
.dev-toc a { font-size: 13px; font-weight: 600; color: var(--lp-body); text-decoration: none; padding: 6px 13px; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass); }
.dev-toc a:hover { border-color: var(--lp-accent); color: var(--lp-accent); }
.dev-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 28px; }
.dev-card { border: 1px solid var(--lp-border); border-radius: 14px; background: var(--lp-surface); padding: 22px 24px; min-width: 0; }
.dev-card h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 18px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 0 0 10px; }
.dev-card p, .dev-card li { font-size: 14.5px; color: var(--lp-body); line-height: 1.7; margin: 0; }
.dev-card p + p, .dev-card p + ul, .dev-card ul + p { margin-top: 10px; }
.dev-card ul { padding-left: 18px; margin: 10px 0 0; }
.dev-card a { color: var(--lp-accent); font-weight: 600; }
.dev-wide { grid-column: 1 / -1; }
.dev-pre { margin: 0; padding: 14px 16px; border-radius: 10px; background: var(--lp-bg2); border: 1px solid var(--lp-border); font-family: ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace; font-size: 12.5px; line-height: 1.7; color: var(--lp-text); overflow-x: auto; white-space: pre; max-width: 100%; }
.dev-codes { display: grid; grid-template-columns: minmax(0, auto) minmax(0, 1fr); gap: 8px 16px; margin-top: 12px; font-size: 13.5px; }
.dev-codes code { font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: 12.5px; color: var(--lp-text); word-break: break-all; }
.dev-codes span { color: var(--lp-body); line-height: 1.55; }
.dev-scope { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--lp-border); color: var(--lp-muted); white-space: nowrap; }
.dev-scope.is-write { color: var(--lp-amber); border-color: color-mix(in srgb, var(--lp-amber) 45%, transparent); }
.dev-tag { margin-top: 44px; scroll-margin-top: 90px; }
.dev-tag h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 22px; font-weight: 600; letter-spacing: -0.02em; color: var(--lp-text); margin: 0 0 14px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.dev-tag h3 small { font-size: 13px; font-weight: 500; color: var(--lp-dim); font-family: inherit; letter-spacing: 0; }
.dev-ep { border: 1px solid var(--lp-border); border-radius: 12px; background: var(--lp-surface); margin-top: 8px; min-width: 0; }
.dev-ep > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 16px; min-height: 44px; }
.dev-ep > summary::-webkit-details-marker { display: none; }
.dev-ep[open] > summary { border-bottom: 1px solid var(--lp-border); }
.dev-method { font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: 11px; font-weight: 700; min-width: 54px; text-align: center; padding: 3px 0; border-radius: 6px; background: var(--lp-bg2); color: var(--lp-text); border: 1px solid var(--lp-border); }
.dev-method.m-GET { color: var(--lp-accent); }
.dev-method.m-DELETE { color: var(--lp-red); }
.dev-method.m-POST, .dev-method.m-PUT, .dev-method.m-PATCH { color: var(--lp-amber); }
.dev-path { font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: 13px; color: var(--lp-text); word-break: break-all; flex: 1 1 260px; min-width: 0; }
.dev-sum { font-size: 13px; color: var(--lp-muted); flex-basis: 100%; }
.dev-body { padding: 16px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; min-width: 0; }
.dev-body > * { min-width: 0; }
.dev-body p { margin: 0; font-size: 14px; color: var(--lp-body); line-height: 1.7; }
.dev-sub { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--lp-dim); margin-bottom: 6px; }
.dev-params { width: 100%; border-collapse: collapse; font-size: 13px; }
.dev-params td { border-top: 1px solid var(--lp-border); padding: 7px 8px 7px 0; vertical-align: top; color: var(--lp-body); line-height: 1.55; }
.dev-params td:first-child { font-family: ui-monospace, 'SF Mono', Consolas, monospace; color: var(--lp-text); white-space: nowrap; }
.dev-params .dev-req { color: var(--lp-amber); font-size: 11px; margin-left: 4px; font-family: system-ui, sans-serif; }
.dev-params .dev-in { color: var(--lp-dim); font-size: 12px; }
.dev-scroll { overflow-x: auto; max-width: 100%; }
@media (max-width: 900px) { .dev-grid { grid-template-columns: 1fr; } }
@media (max-width: 760px) {
 .dev-hero { padding: 100px 0 40px; }
 .dev-inner { padding: 0 16px; }
 .dev-crumbs a, .dev-toc a { min-height: 44px; display: inline-flex; align-items: center; }
 .dev-card { padding: 18px 16px; }
 .dev-codes { grid-template-columns: 1fr; gap: 2px; }
 .dev-codes span { margin-bottom: 8px; }
 .dev-params td:first-child { white-space: normal; word-break: break-all; }
}
`

/** The base URL an integration types. Absolute where the build knows the
 *  app's origin, so it can be copied as is. */
function apiBase(): string {
  const app = appHref(API.base_path)
  return app.startsWith('http') ? app : `${SITE_URL}${API.base_path}`
}

function envName(param: string): string {
  return param.replace(/[^a-zA-Z0-9]/g, '_').toUpperCase()
}

function curlFor(ep: Endpoint): string {
  let path = ep.path
  for (const p of ep.parameters.filter(p => p.in === 'path')) {
    path = path.replace(`{${p.name}}`, `$${envName(p.name)}`)
  }
  const query = ep.parameters
    .filter(p => p.in === 'query' && p.required)
    .map(p => `${p.name}=$${envName(p.name)}`)
  const url = `$STOCKAI${path}${query.length ? `?${query.join('&')}` : ''}`
  const lines = [`curl${ep.method === 'GET' ? '' : ` -X ${ep.method}`} "${url}"`, `  -H "Authorization: Bearer $STOCKAI_KEY"`]
  const body = ep.request_body
  if (body) {
    if (body.content_type.startsWith('multipart/')) {
      for (const f of body.fields) {
        lines.push(f.type === 'file' ? `  -F "${f.name}=@sales.csv"` : `  -F "${f.name}=…"`)
      }
    } else {
      lines.push(`  -H "Content-Type: application/json"`)
      lines.push(`  -d '${JSON.stringify(body.example ?? {})}'`)
    }
  }
  return lines.join(' \\\n')
}

function EndpointItem({ ep, D }: { ep: Endpoint; D: DevelopersCopy }) {
  const R = D.reference
  const body = ep.request_body
  return (
    <details className="dev-ep" id={ep.id}>
      <summary>
        <span className={`dev-method m-${ep.method}`}>{ep.method}</span>
        <span className="dev-path">{ep.path}</span>
        <span className={`dev-scope${ep.scope === 'write' ? ' is-write' : ''}`}>
          {ep.scope === 'write' ? R.write : R.read}
        </span>
        <span className="dev-sum">{ep.summary}</span>
      </summary>
      <div className="dev-body">
        {ep.description && <p>{ep.description}</p>}

        <div>
          <div className="dev-sub">{R.params}</div>
          {ep.parameters.length === 0 ? (
            <p style={{ fontSize: 13 }}>{R.noParams}</p>
          ) : (
            <div className="dev-scroll">
              <table className="dev-params">
                <tbody>
                  {ep.parameters.map(p => (
                    <tr key={`${p.in}-${p.name}`}>
                      <td>
                        {p.name}
                        {p.required && <span className="dev-req">{R.required}</span>}
                      </td>
                      <td className="dev-in">{p.in} · {p.type}</td>
                      <td>{p.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {body && (
          <div>
            <div className="dev-sub">{R.body} · {body.content_type}</div>
            {body.fields.length > 0 && (
              <div className="dev-scroll">
                <table className="dev-params">
                  <tbody>
                    {body.fields.map(f => (
                      <tr key={f.name}>
                        <td>
                          {f.name}
                          {f.required && <span className="dev-req">{R.required}</span>}
                        </td>
                        <td className="dev-in">{f.type}</td>
                        <td>{f.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!body.content_type.startsWith('multipart/') && (
              <>
                <div className="dev-sub" style={{ marginTop: 12 }}>{R.example}</div>
                <pre className="dev-pre">{JSON.stringify(body.example ?? {}, null, 2)}</pre>
              </>
            )}
          </div>
        )}

        <div>
          <div className="dev-sub">{R.curl}</div>
          <pre className="dev-pre">{curlFor(ep)}</pre>
        </div>
        <p style={{ fontSize: 12.5, color: 'var(--lp-dim)' }}>
          {R.returns(ep.success_status, ep.response_content_types.join(', '))}
        </p>
      </div>
    </details>
  )
}

export default function DevelopersPage() {
  const { lang } = useLanguage()
  const D = DEVELOPERS[lang]
  useScrollReveal()
  const base = useMemo(apiBase, [])
  const perDayFree = API.limits.per_day_per_key.free
  const toc: [string, string][] = [
    ['#autenticacion', D.auth.title],
    ['#limites', D.limits.title],
    ['#cobro', D.billing.title],
    ['#errores', D.errors.title],
    ['#referencia', D.reference.title],
  ]

  return (
    <div className="lp" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: DEV_CSS }} />
      <Nav onHome={false} localAnchors={['contacto']} />
      <main>
        <header className="dev-hero">
          <div className="hero-bg" aria-hidden>
            <div className="hero-glow hero-glow-b" />
            <div className="hero-grid" />
          </div>
          <div className="dev-inner">
            <nav aria-label={D.breadcrumb} className="dev-crumbs">
              <ol>
                <li><Link href="/">{D.home}</Link></li>
                <li><span aria-current="page">{D.label}</span></li>
              </ol>
            </nav>
            <h1 className="dev-h1">{D.title}</h1>
            <p className="dev-intro">{D.intro}</p>
            <dl className="dev-facts">
              <div className="dev-fact">
                <dt>{D.facts.base}</dt>
                <dd><code className="dev-code-inline">{base}</code></dd>
              </div>
              <div className="dev-fact">
                <dt>{D.facts.auth}</dt>
                <dd><code className="dev-code-inline">Authorization: Bearer sk_live_…</code></dd>
              </div>
              <div className="dev-fact">
                <dt>{D.facts.endpoints}</dt>
                <dd>{D.facts.endpointsValue(API.counts.total)}</dd>
              </div>
            </dl>
            <nav aria-label={D.toc} className="dev-toc">
              {toc.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
            </nav>
          </div>
        </header>

        <Section id="autenticacion">
          <H2>{D.auth.title}</H2>
          <Lead maxWidth={720}>{D.auth.body}</Lead>
          <div className="dev-grid">
            <div className="dev-card dev-wide">
              <pre className="dev-pre">{`export STOCKAI=${base}\nexport STOCKAI_KEY=sk_live_…\n\ncurl "$STOCKAI/planning" \\\n  -H "Authorization: Bearer $STOCKAI_KEY"`}</pre>
              <p style={{ marginTop: 12 }}>{D.auth.note}</p>
            </div>
            <div className="dev-card">
              <h3>{D.scopes.title}</h3>
              <p>{D.scopes.body}</p>
              <ul>
                <li><strong>{D.scopes.read}.</strong> {D.scopes.readDesc}</li>
                <li><strong>{D.scopes.write}.</strong> {D.scopes.writeDesc}</li>
              </ul>
              <p>{D.scopes.howToGet}</p>
            </div>
            <div className="dev-card">
              <h3>{D.never.title}</h3>
              <p>{D.never.body}</p>
              <ul>{D.never.items.map(i => <li key={i}>{i}</li>)}</ul>
              <p>{D.never.outward}</p>
            </div>
          </div>
        </Section>

        <Section id="limites" alt>
          <div className="dev-grid" style={{ marginTop: 0 }}>
            <div className="dev-card">
              <h3>{D.limits.title}</h3>
              <ul>
                <li>{D.limits.perMinute(API.limits.per_minute_per_key)}</li>
                {typeof perDayFree === 'number' && <li>{D.limits.perDayFree(perDayFree)}</li>}
                <li>{D.limits.perDayPaid}</li>
              </ul>
              <p>{D.limits.over}</p>
            </div>
            <div className="dev-card" id="cobro" style={{ scrollMarginTop: 90 }}>
              <h3>{D.billing.title}</h3>
              <p>{D.billing.body}</p>
              <p>
                <Link href="/precios">{D.billing.pricingLink}</Link>
                {' · '}
                <a href={mailHref('API StockAI')}>{D.billing.contact(CONTACT_EMAIL)}</a>
              </p>
            </div>
            <div className="dev-card" id="errores" style={{ scrollMarginTop: 90 }}>
              <h3>{D.errors.title}</h3>
              <p>{D.errors.body}</p>
              <pre className="dev-pre" style={{ marginTop: 12 }}>{`{\n  "detail": "This endpoint writes and the API key is read-only. Use a write key.",\n  "error_code": "api_key_scope_insufficient",\n  "error_params": { "required_scope": "write", "key_scope": "read" }\n}`}</pre>
              <div className="dev-codes">
                {D.errors.codes.map(([code, desc]) => (
                  <div key={code} style={{ display: 'contents' }}>
                    <code>{code}</code>
                    <span>{desc}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="dev-card">
              <h3>{D.envelope.title}</h3>
              <p>{D.envelope.body}</p>
              <h3 style={{ marginTop: 22 }}>{D.pagination.title}</h3>
              <p>{D.pagination.body}</p>
              <h3 style={{ marginTop: 22 }}>{D.mcp.title}</h3>
              <p>{D.mcp.body}</p>
            </div>
          </div>
        </Section>

        <Section id="referencia">
          <H2>{D.reference.title}</H2>
          <Lead maxWidth={720}>{D.reference.lead}</Lead>
          <nav aria-label={D.reference.title} className="dev-toc" style={{ marginTop: 20 }}>
            {API.tags.map(g => (
              <a key={g.tag} href={`#tag-${g.tag}`}>
                {D.reference.tags[g.tag] ?? g.tag} <span style={{ color: 'var(--lp-dim)', marginLeft: 6 }}>{g.endpoints.length}</span>
              </a>
            ))}
          </nav>
          {API.tags.map(g => (
            <div key={g.tag} id={`tag-${g.tag}`} className="dev-tag">
              <h3>
                {D.reference.tags[g.tag] ?? g.tag}
                <small>{g.tag}</small>
              </h3>
              {g.endpoints.map(ep => <EndpointItem key={ep.id} ep={ep} D={D} />)}
            </div>
          ))}
        </Section>

        <FinalSection />
      </main>
      <Footer onHome={false} localAnchors={['contacto']} />
    </div>
  )
}

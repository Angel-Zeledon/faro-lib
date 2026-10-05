'use client'
// The public API, documented where the key is issued — and callable from here.
//
// Three constraints shaped this page, all of them measured rather than assumed:
//
// 1. It calls RELATIVE paths through the Next rewrite (`/api/:path*` →
//    `${BACKEND_URL}/api/v1/:path*`). Two reasons: the CSP is `connect-src
//    'self'`, so the browser refuses the backend origin outright, and the proxy
//    is what adds `/v1`. Writing `/api/v1/planning` here yields
//    `/api/v1/v1/planning` and a 404 that looks like a broken API.
//
// 2. It uses raw fetch, not `api.ts`'s `request()`. That helper overwrites
//    Authorization with the signed-in user's JWT — so the customer's key would
//    never be sent — and treats any 401 as an expired session, clearing auth and
//    redirecting to /login. Typing a wrong key must not sign you out of StockAI.
//
// 3. Every endpoint runs, INCLUDING the writes — but a write asks first, and the
//    question names the actual consequence. There is no sandbox in this product:
//    replacing a source file overwrites the customer's real data, and log-po
//    records a purchase order that reception and lead-time learning then read.
//    Hiding those buttons would have been the timid choice and a useless
//    console; firing them silently would be worse. It is the customer's own
//    tenant — they are owed the button and the truth about it.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { Play, Copy, Check, KeyRound, AlertTriangle, BookOpen } from 'lucide-react'
import Card from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Input, { Textarea } from '@/components/ui/Input'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getApiKeyUsage } from '@/lib/api'
import { SAMPLE_LANGS, generateSample, type SampleSpec } from '@/lib/codeSamples'
import { useSampleLang } from '@/lib/useSampleLang'
import { SchemaTree, SCHEMA_TREE_CSS, type SchemaLabels } from '@/components/apidocs/SchemaTree'
import { useResponseExample } from '@/lib/useResponseExamples'
import { getUser } from '@/lib/auth'
import type { ApiKeyUsage } from '@/lib/types'
import { useUpgradePrompt } from '@/components/limits/UpgradeDialog'
import '@/components/mobile/mobileForms.css'

const MONO = "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', 'Fira Code', Consolas, 'Liberation Mono', monospace"

type Param = { name: string; placeholder?: string; required?: boolean }

type Endpoint = {
  id: string
  method: 'GET' | 'POST'
  /** Path WITHOUT /v1 — the proxy adds it. See the note at the top of the file. */
  path: string
  /** Path segments the caller substitutes, e.g. {session_id}. */
  pathParams?: Param[]
  query?: Param[]
  /** A write mutates the customer's real tenant: it runs, but only after a
   *  confirmation naming what it will do. Reads fire straight away. */
  write?: boolean
  /** The confirmation body for a write. Never generic — it says what changes. */
  consequenceKey?: string
  /** Writes that take a JSON body offer an editable one, prefilled. */
  bodyTemplate?: string
  /** The file endpoint is multipart, so it needs a picker rather than a body. */
  multipart?: boolean
}

// The nightly-integration job, runnable from here. Not the whole API any more:
// every route an API key may call (backend/api/public_surface.py decides) is in
// the generated reference at /desarrolladores. Kept in the order of the job, so
// the page reads as the work it describes rather than as an alphabetical index.
const ENDPOINTS: Endpoint[] = [
  {
    id: 'planning',
    method: 'GET',
    path: '/planning',
  },
  {
    id: 'sources',
    method: 'GET',
    path: '/data-sources',
    query: [{ name: 'skip', placeholder: '0' }, { name: 'limit', placeholder: '50' }],
  },
  {
    id: 'file',
    method: 'POST',
    path: '/data-sources/{source_id}/file',
    pathParams: [{ name: 'source_id', required: true }],
    write: true,
    multipart: true,
    consequenceKey: 'apidocs.consequence_file',
  },
  {
    id: 'train',
    method: 'POST',
    path: '/sessions/{session_id}/train',
    pathParams: [{ name: 'session_id', required: true }],
    write: true,
    bodyTemplate: '{}',
    consequenceKey: 'apidocs.consequence_train',
  },
  {
    id: 'train_status',
    method: 'GET',
    path: '/sessions/{session_id}/train/status',
    pathParams: [{ name: 'session_id', required: true }],
  },
  {
    id: 'status',
    method: 'GET',
    path: '/inventory/status',
    query: [
      { name: 'session_id' },
      { name: 'signal', placeholder: 'PEDIR_YA' },
      { name: 'supplier' },
    ],
  },
  {
    id: 'briefing',
    method: 'GET',
    path: '/inventory/morning-briefing',
    query: [{ name: 'session_id' }],
  },
  {
    id: 'logpo',
    method: 'POST',
    path: '/inventory/log-po',
    query: [{ name: 'session_id', required: true }],
    write: true,
    consequenceKey: 'apidocs.consequence_logpo',
    bodyTemplate: `{
  "items": [
    { "sku": "ABC-1", "recommended_qty": 120, "final_qty": 100,
      "status": "modified", "unit_cost": 12.5 }
  ]
}`,
  },
]

/** `t()` echoes an unmapped key back, and the two lookups on this page are built
 *  at runtime — `apidocs.<id>_desc` and the consequence key — so the catalogue
 *  checker, which only matches literal t('…') calls, cannot see them. Adding a
 *  ninth endpoint would therefore print `apidocs.foo_desc` at a customer with
 *  every check green. This is the guard that makes that impossible. */
function useSafeCopy() {
  const { t } = useLanguage()
  return (key: string, fallback = '') => {
    const text = t(key)
    return text === key ? fallback : text
  }
}

/** The method is the first thing read on every row, so it carries the colour. */
function MethodChip({ method }: { method: 'GET' | 'POST' }) {
  const read = method === 'GET'
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, fontFamily: MONO, letterSpacing: '0.04em',
      padding: '4px 9px', borderRadius: 6, minWidth: 48, textAlign: 'center',
      color: read ? 'var(--info)' : 'var(--warning)',
      background: read
        ? 'color-mix(in srgb, var(--info) 12%, transparent)'
        : 'color-mix(in srgb, var(--warning) 14%, transparent)',
      border: `1px solid ${read
        ? 'color-mix(in srgb, var(--info) 30%, transparent)'
        : 'color-mix(in srgb, var(--warning) 34%, transparent)'}`,
    }}>
      {method}
    </span>
  )
}

/** The console's endpoints and their entries in the generated reference
 *  (src/data/public-api.json), whose captured answers say what each one returns. */
const REFERENCE_ID: Record<string, string> = {
  planning: 'get-planning',
  sources: 'get-data-sources',
  file: 'post-data-sources-source_id-file',
  train: 'post-sessions-session_id-train',
  train_status: 'get-sessions-session_id-train-status',
  status: 'get-inventory-status',
  briefing: 'get-inventory-morning-briefing',
  logpo: 'post-inventory-log-po',
}

function useSchemaLabels(): SchemaLabels {
  const { t } = useLanguage()
  return useMemo(() => ({
    required: t('apidocs.schema_required'),
    optional: t('apidocs.schema_optional'),
    nullable: t('apidocs.schema_nullable'),
    expandAll: t('apidocs.schema_expand_all'),
    collapseAll: t('apidocs.schema_collapse_all'),
    item: t('apidocs.schema_item'),
    eachValue: t('apidocs.schema_each_value'),
    freeForm: t('apidocs.schema_free_form'),
    oneOf: t('apidocs.schema_one_of'),
    recursive: t('apidocs.schema_recursive'),
    constraint: {
      min: t('apidocs.schema_min'),
      max: t('apidocs.schema_max'),
      default: t('apidocs.schema_default'),
      minLength: t('apidocs.schema_min_length'),
      maxLength: t('apidocs.schema_max_length'),
    },
    more: (n: number) => t('apidocs.schema_more', { n }),
    rootArray: t('apidocs.schema_root_array'),
    rootObject: t('apidocs.schema_root_object'),
    rootMap: t('apidocs.schema_root_map'),
    empty: t('apidocs.schema_empty'),
  }), [t])
}

/** What this endpoint answers, from a real recorded call: the shape as a tree
 *  and the example body. Closed by default so the console stays the first thing
 *  you see; a list is a list here too, never an object drawn with a bullet. */
function ResponseShape({ endpoint }: { endpoint: Endpoint }) {
  const { t } = useLanguage()
  const labels = useSchemaLabels()
  const rec = useResponseExample(REFERENCE_ID[endpoint.id] ?? '')
  const body = rec?.example !== undefined ? JSON.stringify(rec.example, null, 2) : null
  return (
    <details className="api-shape" style={{ borderTop: '1px solid var(--border)' }}>
      <summary style={{
        cursor: 'pointer', padding: '12px 24px', fontSize: 12.5, fontWeight: 600, color: 'var(--muted)',
        display: 'flex', alignItems: 'center', gap: 10, listStyle: 'none',
      }}>
        <span>{t('apidocs.response_toggle')}</span>
        {rec && (
          <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 700, color: 'var(--success)', border: '1px solid var(--success)', borderRadius: 5, padding: '1px 7px' }}>
            {rec.status}
          </span>
        )}
      </summary>
      <div style={{ padding: '4px 24px 20px', display: 'grid', gap: 14, minWidth: 0 }}>
        {rec?.schema && body ? (
          <>
            <div className="api-st"><SchemaTree node={rec.schema} L={labels} showRequired={false} defaultOpen={2} /></div>
            <CodeBlock text={body} label="json" />
            <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.6 }}>{t('apidocs.response_note')}</div>
          </>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('apidocs.response_missing')}</div>
        )}
      </div>
    </details>
  )
}

/** The small uppercase label that names a region of a section. */
function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em',
      textTransform: 'uppercase', color: 'var(--dim)',
    }}>
      {children}
    </div>
  )
}

function CodeBlock({ text, label = 'curl', tabs }: { text: string; label?: string; tabs?: React.ReactNode }) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const [copied, setCopied] = useState(false)
  return (
    <div style={{ border: '1px solid var(--border-strong)', borderRadius: 10, overflow: 'hidden', background: 'var(--surface-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
        {tabs ?? (
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: 'var(--dim)', textTransform: 'uppercase', flex: 1 }}>
            {label}
          </span>
        )}
        <Button
          variant="ghost" size="sm"
          icon={copied ? <Check size={12} /> : <Copy size={12} />}
          onClick={() => {
            navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }}
        >
          {copied ? t('settings.copied') : t('settings.copy')}
        </Button>
      </div>
      {/* On a phone the command wraps rather than scrolling sideways inside
          the card: a curl you have to pan to read is one you mis-copy. */}
      <pre style={{
        margin: 0, padding: narrow ? '12px 14px' : '14px 16px', fontFamily: MONO, fontSize: 12.5, lineHeight: 1.75, color: 'var(--text)',
        ...(narrow ? { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } : { overflowX: 'auto', whiteSpace: 'pre' }),
      }}>
        {text}
      </pre>
    </div>
  )
}

/** The request an endpoint card describes, in the neutral shape the shared
 *  generator (lib/codeSamples.ts) renders into every language. Values are the
 *  placeholders the card already shows; a required parameter is something the
 *  caller supplies, so cURL names it as a shell variable. */
function specFor(endpoint: Endpoint, base: string): SampleSpec {
  let body: SampleSpec['body'] = null
  if (endpoint.multipart) {
    body = { kind: 'multipart', fields: [{ name: 'file', file: true }] }
  } else if (endpoint.bodyTemplate !== undefined) {
    let value: unknown = {}
    try { value = JSON.parse(endpoint.bodyTemplate) } catch { /* static template: cannot happen */ }
    body = { kind: 'json', value }
  }
  return {
    method: endpoint.method,
    base,
    path: endpoint.path,
    pathParams: (endpoint.pathParams ?? []).map(p => ({ name: p.name, value: `<${p.name}>` })),
    query: (endpoint.query ?? [])
      .filter(p => p.required || p.placeholder)
      .map(p => ({ name: p.name, value: p.placeholder ?? `<${p.name}>`, env: !p.placeholder })),
    headers: [],
    body,
    expectsJson: true,
  }
}

/** A code block with the language tabs. The choice is remembered and shared with
 *  /desarrolladores, so the two screens agree on what a visitor last picked. */
function SampleBlock({ spec }: { spec: SampleSpec }) {
  const { t } = useLanguage()
  const [lang, setLang] = useSampleLang()
  const text = useMemo(() => generateSample(lang, spec), [lang, spec])
  const tabs = (
    <div role="tablist" aria-label={t('apidocs.sample_languages')} style={{ display: 'flex', gap: 2, flex: 1, minWidth: 0, overflowX: 'auto', scrollbarWidth: 'none' }}>
      {SAMPLE_LANGS.map(l => (
        <button
          key={l.id}
          type="button"
          role="tab"
          aria-selected={lang === l.id}
          onClick={() => setLang(l.id)}
          style={{
            all: 'unset', cursor: 'pointer', whiteSpace: 'nowrap', fontSize: 11.5, fontWeight: 600,
            padding: '5px 9px', borderRadius: 6,
            color: lang === l.id ? 'var(--text)' : 'var(--dim)',
            background: lang === l.id ? 'var(--surface-3)' : 'transparent',
          }}
        >
          {l.label}
        </button>
      ))}
    </div>
  )
  return <CodeBlock text={text} tabs={tabs} />
}

/** The MCP endpoint, described rather than wired into the console.
 *
 *  It is deliberately NOT another `EndpointCard`. The console's whole grammar
 *  is a method, a path and a body; MCP is JSON-RPC, where the method lives
 *  inside the body and `POST` would be painted in the write colour for a
 *  surface that only reads. Two grammars in one list teaches the wrong thing
 *  about both.
 *
 *  What an integrator needs here is the URL, what the tools are, and the
 *  sentence that says nothing writes — plus one curl so they can prove their
 *  key works before going near a client's configuration. */
function McpSection({ baseUrl, narrow }: { baseUrl: string; narrow: boolean }) {
  const { t } = useLanguage()
  const url = baseUrl ? `${baseUrl}/mcp` : '…'
  const tools: Array<[string, string]> = [
    ['get_planning_context', t('apidocs.mcp_tool_planning')],
    ['get_morning_briefing', t('apidocs.mcp_tool_briefing')],
    ['get_inventory_status', t('apidocs.mcp_tool_status')],
    ['list_data_sources',    t('apidocs.mcp_tool_sources')],
    ['get_training_status',  t('apidocs.mcp_tool_training')],
  ]

  return (
    <Card id="ep-mcp" padding={narrow ? '18px' : '22px 24px'} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <Eyebrow>{t('apidocs.mcp_eyebrow')}</Eyebrow>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)', margin: '8px 0 0' }}>
          {t('apidocs.mcp_title')}
        </h2>
        <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.7, margin: '8px 0 0', maxWidth: 680 }}>
          {t('apidocs.mcp_intro')}
        </p>
      </div>

      <div>
        <Eyebrow>{t('apidocs.mcp_url_heading')}</Eyebrow>
        <code style={{
          display: 'inline-block', marginTop: 7, fontFamily: MONO, fontSize: 13,
          color: 'var(--text)', background: 'var(--surface-3)',
          border: '1px solid var(--border-strong)', borderRadius: 7, padding: '7px 12px',
          wordBreak: 'break-all',
        }}>
          {url}
        </code>
      </div>

      <div>
        <Eyebrow>{t('apidocs.mcp_tools_heading')}</Eyebrow>
        <ul style={{ listStyle: 'none', margin: '9px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 7 }}>
          {tools.map(([name, desc]) => (
            <li key={name} style={{
              display: 'flex', gap: 10, alignItems: 'baseline',
              flexDirection: narrow ? 'column' : 'row',
            }}>
              <code style={{
                fontFamily: MONO, fontSize: 11.5, color: 'var(--info)', flexShrink: 0,
                minWidth: narrow ? undefined : 172,
              }}>
                {name}
              </code>
              <span style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6 }}>{desc}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* The one thing somebody handing out a key has to understand, and the
          reason it is a panel rather than a footnote. */}
      <div style={{
        display: 'flex', gap: 10, padding: '12px 14px', borderRadius: 9,
        background: 'color-mix(in srgb, var(--info) 8%, transparent)',
        border: '1px solid color-mix(in srgb, var(--info) 26%, transparent)',
      }}>
        <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.7 }}>
          <strong style={{ color: 'var(--text)' }}>{t('apidocs.mcp_readonly_title')}</strong>{' '}
          {t('apidocs.mcp_readonly_body')}
        </div>
      </div>

      <div>
        <Eyebrow>{t('apidocs.mcp_test_heading')}</Eyebrow>
        <div style={{ marginTop: 9 }}>
          <SampleBlock spec={{ method: 'POST', base: baseUrl || 'https://app.stockai.es/api/v1', path: '/mcp', pathParams: [], query: [], headers: [], expectsJson: true, body: { kind: 'json', value: { jsonrpc: '2.0', id: 1, method: 'tools/list' } } }} />
        </div>
      </div>

      <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.7 }}>
        <strong style={{ color: 'var(--text)' }}>{t('apidocs.mcp_desktop_heading')}</strong>{' '}
        {t('apidocs.mcp_desktop_desc')}
      </div>
    </Card>
  )
}

/** "Llamadas este mes": what the API is billed on, read from the meter.
 *
 *  Admin only, like the endpoint behind it. A non-admin sees one line saying
 *  who can see it rather than an empty box — an empty box reads as "zero
 *  calls", which is exactly the wrong thing to tell somebody about a bill. */
function UsagePanel({ narrow }: { narrow: boolean }) {
  const { t, lang } = useLanguage()
  const openContact = useUpgradePrompt()
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null)
  const [usage, setUsage] = useState<ApiKeyUsage | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const admin = getUser()?.role === 'admin'
    setIsAdmin(admin)
    if (!admin) return
    getApiKeyUsage()
      .then(setUsage)
      .catch(() => setFailed(true))
  }, [])

  if (isAdmin === null) return null
  const fmt = (n: number) => n.toLocaleString(lang === 'es' ? 'es-CR' : 'en-US')
  const peak = usage ? Math.max(1, ...usage.by_day.map(d => d.calls)) : 1

  return (
    <Card id="api-usage" padding={narrow ? '18px' : '20px 24px'} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
          {t('apidocs.usage_heading')}
        </h2>
        {usage && (
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>
            {usage.month} · <strong style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
              {t('apidocs.usage_total', { n: fmt(usage.total) })}
            </strong>
            {usage.today !== null && <> · {t('apidocs.usage_today', { n: fmt(usage.today) })}</>}
          </span>
        )}
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.65, margin: 0, maxWidth: 760 }}>
        {t('apidocs.usage_desc')}
      </p>

      {!isAdmin && (
        <div style={{ fontSize: 12.5, color: 'var(--dim)' }}>{t('apidocs.usage_admin_only')}</div>
      )}
      {isAdmin && failed && (
        <div role="alert" style={{ fontSize: 12.5, color: 'var(--danger)' }}>{t('apidocs.usage_error')}</div>
      )}

      {usage && (
        <>
          <div>
            <Eyebrow>{t('apidocs.usage_by_day')}</Eyebrow>
            {/* One bar per day of the month so far. Zero days are drawn as a
                hairline so the axis reads as time, not as missing data. */}
            <div
              role="img"
              aria-label={`${t('apidocs.usage_by_day')}: ${t('apidocs.usage_total', { n: fmt(usage.total) })}`}
              style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 96, marginTop: 10 }}
            >
              {usage.by_day.map(d => (
                <div
                  key={d.day}
                  title={t('apidocs.usage_day_title', { day: d.day, n: fmt(d.calls) })}
                  // Capped width: on the 1st of the month one bar must read as
                  // one day, not as a block spanning the whole panel.
                  style={{
                    flex: 1, minWidth: 2, maxWidth: 26, borderRadius: '3px 3px 0 0',
                    height: d.calls === 0 ? 1 : `${Math.max(4, (d.calls / peak) * 100)}%`,
                    background: d.calls === 0 ? 'var(--border-strong)' : 'var(--accent)',
                  }}
                />
              ))}
            </div>
            {usage.by_day.length > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--dim)', marginTop: 5, fontFamily: MONO, maxWidth: Math.max(96, usage.by_day.length * 29), whiteSpace: 'nowrap' }}>
                <span>{usage.by_day[0].day.slice(5)}</span>
                {usage.by_day.length > 1 && <span>{usage.by_day[usage.by_day.length - 1].day.slice(5)}</span>}
              </div>
            )}
          </div>

          <div>
            <Eyebrow>{t('apidocs.usage_by_key')}</Eyebrow>
            {usage.by_key.length === 0 ? (
              <div style={{ fontSize: 12.5, color: 'var(--dim)', marginTop: 8 }}>{t('apidocs.usage_empty')}</div>
            ) : (
              <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {usage.by_key.map(k => (
                  <li key={k.api_key_id} style={{ display: 'flex', alignItems: 'baseline', gap: 10, fontSize: 13 }}>
                    <span style={{ color: 'var(--text)', fontWeight: 500, minWidth: 0, overflow: 'hidden', overflowWrap: 'anywhere', }}>
                      {k.name}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--dim)' }}>
                      {!k.active
                        ? t('apidocs.usage_revoked')
                        : k.scope === 'write' ? t('settings.scope_write') : t('settings.scope_read')}
                    </span>
                    <span style={{ marginLeft: 'auto', fontFamily: MONO, fontVariantNumeric: 'tabular-nums', color: 'var(--text)' }}>
                      {fmt(k.calls)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 12, color: 'var(--muted)' }}>
            <span>
              {usage.limits.per_day_per_key === null
                ? t('apidocs.usage_limit_none')
                : t('apidocs.usage_limit_day', { n: fmt(usage.limits.per_day_per_key) })}
            </span>
            <span>·</span>
            <span>{t('apidocs.usage_pricing')}</span>
            <Button variant="secondary" size="sm" onClick={() => openContact(null)}>
              {t('apidocs.usage_contact')}
            </Button>
          </div>
        </>
      )}
    </Card>
  )
}

function EndpointCard({ endpoint, token, baseUrl }: { endpoint: Endpoint; token: string; baseUrl: string }) {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  const safe = useSafeCopy()
  const confirm = useConfirm()
  const [values, setValues] = useState<Record<string, string>>({})
  const [body, setBody] = useState(endpoint.bodyTemplate ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ status: number; ms: number; body: string } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  // Touching any input drops the previous result. Leaving it on screen let a
  // green 201 from the last run sit beside freshly edited inputs, which reads
  // as "your edit was recorded" — the most expensive possible misreading on a
  // page whose writes are real.
  function invalidate() {
    setResult(null)
    setFailure(null)
  }
  const set = (name: string, v: string) => {
    invalidate()
    setValues(prev => ({ ...prev, [name]: v }))
  }

  function buildPath(): string {
    let path = endpoint.path
    for (const p of endpoint.pathParams ?? []) {
      // Trimmed, like the enable-check at the bottom of this component. They
      // disagreed: Run lit up on a trimmed value while the URL was built from
      // the raw one, so an id pasted with a trailing space produced a 404 that
      // told the customer their session did not exist.
      path = path.replace(`{${p.name}}`, encodeURIComponent((values[p.name] ?? '').trim()))
    }
    const qs = (endpoint.query ?? [])
      .filter(p => (values[p.name] ?? '').trim() !== '')
      .map(p => `${p.name}=${encodeURIComponent(values[p.name].trim())}`)
      .join('&')
    return qs ? `${path}?${qs}` : path
  }

  async function run() {
    // A write touches the customer's live tenant, so it asks first — and the
    // question names the actual consequence rather than "are you sure?". Reads
    // never ask: nothing to undo.
    // Bad JSON must be caught HERE, before the dialog. Otherwise the user
    // accepts "this records a real purchase order" and receives a 422 — and the
    // console cannot tell them whether they typed it wrong or the server said
    // no.
    let parsed: unknown = undefined
    if (endpoint.bodyTemplate !== undefined && body.trim() !== '') {
      try {
        parsed = JSON.parse(body)
      } catch {
        setFailure(t('apidocs.body_invalid'))
        return
      }
    }

    if (endpoint.write) {
      // An EMPTY body on log-po is not "send nothing". The backend reads a body
      // without `items` as the legacy export path and writes a purchase order
      // containing every actionable SKU in the session — lines the user never
      // typed, which then feed reception tracking and lead-time learning. The
      // console may still do it; it may not do it quietly.
      const ordersEverything =
        endpoint.id === 'logpo' &&
        !Array.isArray((parsed as { items?: unknown } | undefined)?.items)
      const consequence = ordersEverything
        ? t('apidocs.consequence_logpo_all')
        : safe(endpoint.consequenceKey ?? '', t('apidocs.confirm_generic'))

      const go = await confirm({
        title: t('apidocs.confirm_title'),
        message: consequence,
        danger: true,
      })
      if (!go) return
    }

    setBusy(true)
    setFailure(null)
    setResult(null)
    const started = performance.now()
    try {
      const headers: Record<string, string> = { Authorization: `Bearer ${token.trim()}` }
      let payload: BodyInit | undefined
      if (endpoint.multipart) {
        if (!file) { setBusy(false); return }
        const form = new FormData()
        form.append('file', file)
        payload = form                        // no Content-Type: the browser adds the boundary
      } else if (endpoint.method === 'POST') {
        headers['Content-Type'] = 'application/json'
        payload = body.trim() || '{}'
      }
      // `/api` + path, never `/api/v1` — the rewrite adds the version. And a
      // hand-built fetch, so the key travels instead of the session's JWT and a
      // 401 stays here instead of signing the user out.
      const res = await fetch(`/api${buildPath()}`, { method: endpoint.method, headers, body: payload })
      const text = await res.text()
      // `/inventory/status` has no pagination — the page's own copy says so. A
      // tenant with tens of thousands of SKUs returns a body that, parsed and
      // pretty-printed, roughly doubles and lands in the DOM as one text node.
      // Capped, because a console that freezes the tab is worse than one that
      // shows less: the tail is truncated, and it says that it truncated.
      const MAX_SHOWN = 200_000
      let shown = text
      if (text.length <= MAX_SHOWN) {
        try { shown = JSON.stringify(JSON.parse(text), null, 2) } catch { /* not JSON: show it raw */ }
      }
      if (shown.length > MAX_SHOWN) {
        shown = shown.slice(0, MAX_SHOWN) + `

… ${t('apidocs.try_truncated')}`
      }
      setResult({ status: res.status, ms: Math.round(performance.now() - started), body: shown })
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const missingPathParam = (endpoint.pathParams ?? []).some(p => p.required && !(values[p.name] ?? '').trim())
  const missingQueryParam = (endpoint.query ?? []).some(p => p.required && !(values[p.name] ?? '').trim())
  const missingFile = Boolean(endpoint.multipart) && file === null
  const missingNames = [...(endpoint.pathParams ?? []), ...(endpoint.query ?? [])]
    .filter(p => p.required && !(values[p.name] ?? '').trim())
    .map(p => p.name)
  const canRun = token.trim() !== '' && !missingPathParam && !missingQueryParam && !missingFile
  const statusColor = !result ? 'var(--dim)'
    : result.status < 300 ? 'var(--success)'
    : result.status < 500 ? 'var(--warning)' : 'var(--danger)'

  // The section is the unit of the reference now, not a card in a stack: a
  // header that states the contract, then the console and the curl side by side
  // so the page uses the width it has, then the response spanning the full
  // width underneath — JSON is the widest thing here and it was the thing being
  // squeezed into the narrowest column.
  const split = !narrow
  return (
    <section
      id={`ep-${endpoint.id}`}
      // scrollMarginTop clears the sticky key bar: without it the rail jumps to
      // a heading the bar is covering.
      style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 12, overflow: 'hidden', scrollMarginTop: 92,
      }}
    >
      <header style={{ padding: narrow ? '16px' : '20px 24px 18px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <MethodChip method={endpoint.method} />
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text)', overflowWrap: 'anywhere', minWidth: 0 }}>
            {endpoint.path}
          </span>
          {endpoint.write && (
            <span style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
              padding: '3px 8px', borderRadius: 5, color: 'var(--warning)',
              border: '1px solid color-mix(in srgb, var(--warning) 40%, transparent)',
            }}>
              {t('apidocs.badge_write')}
            </span>
          )}
        </div>
        <p style={{ margin: '10px 0 0', fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.65, maxWidth: 780 }}>
          {safe(`apidocs.${endpoint.id}_desc`)}
        </p>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: split ? 'minmax(0,1fr) minmax(0,1fr)' : '1fr' }}>
        <div style={{
          padding: narrow ? '16px' : '18px 24px 20px', display: 'flex', flexDirection: 'column', gap: 12,
          borderRight: split ? '1px solid var(--border)' : 'none',
          borderBottom: split ? 'none' : '1px solid var(--border)',
        }}>
          <Eyebrow>{t('apidocs.section_console')}</Eyebrow>

          {(endpoint.pathParams ?? []).concat(endpoint.query ?? []).length > 0 && (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {(endpoint.pathParams ?? []).concat(endpoint.query ?? []).map(p => (
                <label key={p.name} style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '1 1 190px', minWidth: 0 }}>
                  <span style={{ fontSize: 11, color: 'var(--dim)', fontFamily: MONO }}>
                    {p.name}{p.required ? ' *' : ''}
                  </span>
                  <Input
                    size="sm"
                    name={`${endpoint.id}_${p.name}`}
                    // Not `p.name`: an aria-label OVERRIDES the visible text, so
                    // labelling it with the bare name hid the `*` from every
                    // screen reader while showing it to everyone else.
                    aria-label={p.required ? `${p.name} *` : p.name}
                    required={p.required}
                    placeholder={p.placeholder ?? ''}
                    value={values[p.name] ?? ''}
                    onChange={e => set(p.name, e.target.value)}
                    style={{ width: '100%', fontFamily: MONO, fontSize: 12 }}
                  />
                </label>
              ))}
            </div>
          )}

          {endpoint.multipart && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('apidocs.try_file_label')} *</span>
              <input
                type="file"
                name={`${endpoint.id}_file`}
                aria-label={t('apidocs.try_file_label')}
                accept=".csv,.xlsx,.xls,.parquet,.json"
                onChange={e => { invalidate(); setFile(e.target.files?.[0] ?? null) }}
                style={{ fontSize: 12, color: 'var(--text)' }}
              />
            </label>
          )}

          {endpoint.bodyTemplate !== undefined && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('apidocs.try_body_label')}</span>
              <Textarea
                name={`${endpoint.id}_body`}
                aria-label={t('apidocs.try_body_label')}
                value={body}
                onChange={e => { invalidate(); setBody(e.target.value) }}
                rows={endpoint.id === 'logpo' ? 8 : 3}
                style={{ fontFamily: MONO, fontSize: 12, lineHeight: 1.7 }}
              />
            </label>
          )}

          {endpoint.write && (
            <div style={{
              display: 'flex', gap: 9, alignItems: 'flex-start', fontSize: 12.5, lineHeight: 1.6,
              color: 'var(--text)', background: 'color-mix(in srgb, var(--warning) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)',
              borderRadius: 8, padding: '10px 12px',
            }}>
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: 'var(--warning)' }} aria-hidden="true" />
              <span>{safe(endpoint.consequenceKey ?? '', t('apidocs.confirm_generic'))}</span>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginTop: 'auto', paddingTop: 4 }}>
            <Button
              variant={endpoint.write ? 'danger' : 'primary'}
              size="sm" icon={<Play size={12} />}
              disabled={!canRun} loading={busy} onClick={run}
            >
              {busy ? t('apidocs.try_running') : t('apidocs.try_run')}
            </Button>
            {/* The only hint used to be the missing token. With a valid key and
                a blank required field the button was simply grey and mute,
                which gives the user nothing to act on. */}
            {!canRun && (
              <span style={{ fontSize: 12, color: 'var(--dim)' }}>
                {!token.trim()
                  ? t('apidocs.try_empty')
                  : missingFile
                    ? t('apidocs.try_missing_file')
                    : `${t('apidocs.try_missing_fields')} ${missingNames.join(', ')}`}
              </span>
            )}
            {result && (
              // Announced, like `automatizacion` and `pronosticos` already do. A
              // screen-reader user pressed Run and got total silence — on
              // success AND on failure.
              <span role="status" aria-live="polite" style={{ fontSize: 12, color: 'var(--dim)' }}>
                {t('apidocs.try_status')}: <strong style={{ color: statusColor }}>{result.status}</strong>
                {'  ·  '}{t('apidocs.try_duration_ms', { ms: result.ms })}
              </span>
            )}
          </div>

          {failure && (
            <div role="alert" style={{ fontSize: 12, color: 'var(--danger)', lineHeight: 1.6 }}>
              {t('apidocs.try_error')} {failure}
            </div>
          )}
        </div>

        <div style={{ padding: narrow ? '16px' : '18px 24px 20px', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
          <Eyebrow>{t('apidocs.section_example')}</Eyebrow>
          <SampleBlock spec={specFor(endpoint, baseUrl || 'https://app.stockai.es/api/v1')} />
        </div>
      </div>

      <ResponseShape endpoint={endpoint} />

      {result && (
        <div style={{ borderTop: '1px solid var(--border)', padding: narrow ? '14px 16px 16px' : '16px 24px 20px', background: 'var(--surface-2)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <Eyebrow>{t('apidocs.try_response')}</Eyebrow>
            <span style={{
              fontFamily: MONO, fontSize: 11, fontWeight: 700, color: statusColor,
              border: `1px solid ${statusColor}`, borderRadius: 5, padding: '1px 7px',
            }}>
              {result.status}
            </span>
          </div>
          <pre style={{
            margin: 0, padding: '14px 16px', maxHeight: 420, overflow: 'auto',
            ...(narrow ? { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', padding: '12px' } : {}),
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
            fontFamily: MONO, fontSize: 12, lineHeight: 1.7, color: 'var(--text)',
          }}>
            {result.body}
          </pre>
        </div>
      )}
    </section>
  )
}

export default function ApiDocsPage() {
  const { t } = useLanguage()
  const narrow = useIsNarrow()
  // React state only, never localStorage. The raw key exists nowhere else — the
  // server stores a hash — so persisting it here would be the only copy at rest,
  // reachable by any script on the page.
  const [token, setToken] = useState('')
  // Read on the client only — there is no window during SSR, and hardcoding a
  // host would be wrong on every deployment but one.
  const [baseUrl, setBaseUrl] = useState('')
  useEffect(() => { setBaseUrl(`${window.location.origin}/api/v1`) }, [])

  // The facts an integrator checks before writing a line of code. They sit in
  // the header rather than in prose further down because they are the terms of
  // the contract, not commentary on it.
  //
  // A third one said "Included from: Professional". With one plan the API is
  // included, full stop, and a row answering a question nobody can ask any more
  // is not a term of the contract — it is a leftover of a price list.
  const specs = [
    { label: t('apidocs.spec_auth'), value: 'Bearer sk_live_…', mono: true },
    { label: t('apidocs.spec_rate'), value: t('apidocs.spec_rate_value'), mono: false },
  ]

  // How to get a key and where the full reference lives. Inside the sticky
  // bar on desktop; on a phone they sit under it, so what stays pinned while
  // scrolling is one input high rather than a third of the screen.
  const keyLinks = (
    <>
      <Link href="/automatizacion" style={{ textDecoration: 'none', ...(narrow ? { flex: 1, minWidth: 0 } : {}) }}>
        <Button variant="secondary" size="sm" icon={<KeyRound size={12} />}
                style={narrow ? { width: '100%', justifyContent: 'center' } : undefined}>
          {t('apidocs.get_your_key')}
        </Button>
      </Link>
      {/* A plain anchor: /desarrolladores is a landing page with its own
          chrome, not a screen of the app shell. */}
      <a href="/desarrolladores" style={{ textDecoration: 'none', ...(narrow ? { flex: 1, minWidth: 0 } : {}) }}>
        <Button variant="ghost" size="sm" icon={<BookOpen size={12} />}
                style={narrow ? { width: '100%', justifyContent: 'center' } : undefined}>
          {t('apidocs.full_reference')}
        </Button>
      </a>
    </>
  )

  return (
    // `-24px` cancels `.page-content`'s own padding so the header band reaches
    // the edges. Full bleed is the point: a reference that starts with a
    // floating card reads as one more screen, and this one is a contract with
    // somebody else's engineering team.
    // On a phone it bleeds over the shell's 12px gutter instead; `m-form` and
    // `m-tap-min` give every field 16px text (no iOS zoom) and every button a
    // 44px height there (components/mobile/mobileForms.css).
    <div className={narrow ? 'm-form m-tap-min' : undefined} style={{ margin: narrow ? '-12px -12px 0' : -24 }}>
      <style dangerouslySetInnerHTML={{ __html: `${SCHEMA_TREE_CSS}
.api-st { --st-fg: var(--text); --st-muted: var(--muted); --st-dim: var(--dim); --st-border: var(--border); --st-surface: var(--surface-2); --st-accent: var(--accent); --st-warn: var(--warning); --st-mono: ${MONO}; }
.api-shape > summary::-webkit-details-marker { display: none; }
.api-shape > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.api-shape > summary::before { content: ''; width: 6px; height: 6px; border-right: 1.5px solid var(--dim); border-bottom: 1.5px solid var(--dim); transform: rotate(-45deg); transition: transform 140ms ease; flex-shrink: 0; }
.api-shape[open] > summary::before { transform: rotate(45deg); }
` }} />
      <header style={{ background: 'var(--sidebar-bg)', color: '#fff', borderBottom: '1px solid rgba(255,255,255,0.10)' }}>
        <div style={{ maxWidth: 1440, margin: '0 auto', padding: narrow ? '28px 20px 24px' : '44px 48px 34px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span style={{
              fontSize: 10.5, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase',
              color: 'var(--sidebar-beam)',
            }}>
              {t('apidocs.eyebrow')}
            </span>
            <span style={{
              fontFamily: MONO, fontSize: 10.5, fontWeight: 700, color: 'rgba(255,255,255,0.72)',
              border: '1px solid rgba(255,255,255,0.24)', borderRadius: 5, padding: '2px 7px',
            }}>
              v1
            </span>
          </div>

          {/* The top bar already says "API": the hero leads with the one
              sentence that adds something instead of repeating the title. */}
          <p style={{
            fontSize: narrow ? 20 : 26, fontWeight: 700, letterSpacing: '-0.02em',
            color: '#fff', margin: 0, lineHeight: 1.3, maxWidth: 700,
          }}>
            {t('apidocs.intro')}
          </p>

          {/* The base URL, stated once and prominently. Every curl on this page
              says `$STOCKAI`, and the console itself calls a RELATIVE path through
              the Next rewrite — so the /v1 an integrator must type was the one
              thing the page never showed. */}
          <div style={{ marginTop: 26, display: 'flex', flexWrap: 'wrap', gap: narrow ? 18 : 40, alignItems: 'flex-end' }}>
            <div>
              <div style={{
                fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.50)', marginBottom: 7,
              }}>
                {t('apidocs.base_url_heading')}
              </div>
              <code style={{
                display: 'inline-block', fontFamily: MONO, fontSize: 13.5, color: '#fff',
                background: 'rgba(255,255,255,0.09)', border: '1px solid rgba(255,255,255,0.16)',
                borderRadius: 7, padding: '7px 12px', overflowWrap: 'anywhere',
              }}>
                {baseUrl || '…'}
              </code>
              <div style={{
                fontSize: 11.5, color: 'rgba(255,255,255,0.55)', lineHeight: 1.55,
                marginTop: 8, maxWidth: 380,
              }}>
                {t('apidocs.base_url_desc')}
              </div>
            </div>
            {specs.map(sp => (
              <div key={sp.label}>
                <div style={{
                  fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                  color: 'rgba(255,255,255,0.50)', marginBottom: 7,
                }}>
                  {sp.label}
                </div>
                <div style={{
                  fontSize: 13.5, color: 'rgba(255,255,255,0.92)', paddingBottom: 7,
                  fontFamily: sp.mono ? MONO : undefined,
                }}>
                  {sp.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </header>

      {/* The key follows you down the page. Eight sections is more than one
          screen, and a console whose credential scrolls out of reach makes you
          hunt for it before every call. */}
      <div style={{
        // -12px on a phone: the scroll container's 12px top padding would
        // otherwise leave a strip of scrolling content showing above the bar.
        position: 'sticky', top: narrow ? -12 : 0, zIndex: 6,
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
      }}>
        <div style={{
          maxWidth: 1440, margin: '0 auto', padding: narrow ? '10px 16px' : '14px 48px',
          display: 'flex', alignItems: 'center', gap: narrow ? 8 : 14, flexWrap: 'wrap',
        }}>
          <label style={narrow
            ? { display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 100%', minWidth: 0 }
            : { display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 260 }}>
            {narrow && <KeyRound size={16} color="var(--dim)" aria-hidden="true" style={{ flexShrink: 0 }} />}
            <span style={narrow
              ? { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' }
              : { fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>
              {t('apidocs.try_token_label')}
            </span>
            <Input
              type="password"
              name="api_console_token"
              // An unmarked password field is an invitation in BOTH directions:
              // the manager offers to save the raw sk_live_ key (which exists
              // nowhere else — the server keeps only a hash), and it autofills
              // the user's saved StockAI PASSWORD into it, which would then travel
              // in an Authorization header. Any input that takes a secret
              // needs all four of the attributes below, not just the first.
              autoComplete="off"
              spellCheck={false}
              data-1p-ignore
              data-lpignore="true"
              size="sm"
              aria-label={t('apidocs.try_token_label')}
              placeholder={t('apidocs.try_token_ph')}
              value={token}
              onChange={e => setToken(e.target.value)}
              style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 12, ...(narrow ? { width: '100%' } : {}) }}
            />
          </label>
          {!narrow && keyLinks}
        </div>
      </div>
      {narrow && (
        <div style={{ display: 'flex', gap: 8, padding: '12px 12px 0' }}>{keyLinks}</div>
      )}

      <div style={{
        maxWidth: 1440, margin: '0 auto',
        padding: narrow ? '16px 12px 8px' : '32px 48px 64px',
        display: 'grid',
        gridTemplateColumns: narrow ? '1fr' : '218px minmax(0,1fr)',
        gap: narrow ? 20 : 44,
        alignItems: 'start',
      }}>
        {!narrow && (
          <nav aria-label={t('apidocs.nav_label')} style={{ position: 'sticky', top: 84 }}>
            <div style={{
              fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
              color: 'var(--dim)', marginBottom: 12,
            }}>
              {t('apidocs.nav_label')}
            </div>
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {ENDPOINTS.map(ep => (
                <li key={ep.id}>
                  <a
                    href={`#ep-${ep.id}`}
                    className="api-nav-link"
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                      borderRadius: 6, textDecoration: 'none', color: 'var(--muted)',
                    }}
                  >
                    <span style={{
                      fontFamily: MONO, fontSize: 9.5, fontWeight: 700, width: 30, flexShrink: 0,
                      color: ep.method === 'GET' ? 'var(--info)' : 'var(--warning)',
                    }}>
                      {ep.method}
                    </span>
                    <span style={{
                      fontFamily: MONO, fontSize: 11.5, overflow: 'hidden',
                      overflowWrap: 'anywhere',
                    }}>
                      {ep.path}
                    </span>
                  </a>
                </li>
              ))}
              {/* Not an endpoint in the console's sense, but the index is how
                  anybody discovers this page has an MCP surface at all. */}
              <li>
                <a
                  href="#ep-mcp"
                  className="api-nav-link"
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                    borderRadius: 6, textDecoration: 'none', color: 'var(--muted)',
                  }}
                >
                  <span style={{
                    fontFamily: MONO, fontSize: 9.5, fontWeight: 700, width: 30, flexShrink: 0,
                    color: 'var(--info)',
                  }}>
                    MCP
                  </span>
                  <span style={{
                    fontFamily: MONO, fontSize: 11.5, overflow: 'hidden',
                    overflowWrap: 'anywhere',
                  }}>
                    /mcp
                  </span>
                </a>
              </li>
            </ul>
          </nav>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
          <UsagePanel narrow={narrow} />
          <div style={{
            display: 'grid',
            gridTemplateColumns: narrow ? '1fr' : 'repeat(3, minmax(0,1fr))',
            gap: 14,
          }}>
            {[
              { h: t('apidocs.auth_heading'), b: `${t('apidocs.auth_desc')} ${t('apidocs.auth_role_note')}` },
              { h: t('apidocs.limits_heading'), b: `${t('apidocs.limits_desc')} ${t('apidocs.limits_429')}` },
              { h: t('apidocs.envelope_heading'), b: `${t('apidocs.envelope_desc')} ${t('apidocs.envelope_errors')}` },
            ].map(c => (
              <Card key={c.h} padding="16px 18px" style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{c.h}</div>
                <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.65 }}>{c.b}</div>
              </Card>
            ))}
          </div>

          {ENDPOINTS.map(ep => <EndpointCard key={ep.id} endpoint={ep} token={token} baseUrl={baseUrl} />)}
          <McpSection baseUrl={baseUrl} narrow={narrow} />
          <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.7 }}>
            {t('apidocs.footer_promise')}{' '}
            <a href="/desarrolladores" style={{
              color: 'var(--accent)', fontWeight: 600,
              ...(narrow ? { display: 'inline-flex', alignItems: 'center', minHeight: 44 } : {}),
            }}>
              {t('apidocs.full_reference')}
            </a>
          </div>
        </div>
      </div>
    </div>
  )
}

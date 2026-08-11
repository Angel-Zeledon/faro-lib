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
//    redirecting to /login. Typing a wrong key must not sign you out of Faro.
//
// 3. Every endpoint runs, INCLUDING the writes — but a write asks first, and the
//    question names the actual consequence. There is no sandbox in this product:
//    replacing a source file overwrites the customer's real data, and log-po
//    records a purchase order that reception and lead-time learning then read.
//    Hiding those buttons would have been the timid choice and a useless
//    console; firing them silently would be worse. It is the customer's own
//    tenant — they are owed the button and the truth about it.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Play, Copy, Check, KeyRound, AlertTriangle } from 'lucide-react'
import Card from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Input, { Textarea } from '@/components/ui/Input'
import FeatureGate from '@/components/ui/FeatureGate'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'

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
  curl: string
}

// Mirrors backend/api/public_surface.py. Kept in the same order as the doc, so
// the page reads as the job it describes rather than as an alphabetical index.
const ENDPOINTS: Endpoint[] = [
  {
    id: 'planning',
    method: 'GET',
    path: '/planning',
    curl: `curl "$FARO/planning" \\\n  -H "Authorization: Bearer $KEY"`,
  },
  {
    id: 'sources',
    method: 'GET',
    path: '/data-sources',
    query: [{ name: 'skip', placeholder: '0' }, { name: 'limit', placeholder: '50' }],
    curl: `curl "$FARO/data-sources?limit=50" \\\n  -H "Authorization: Bearer $KEY"`,
  },
  {
    id: 'file',
    method: 'POST',
    path: '/data-sources/{source_id}/file',
    pathParams: [{ name: 'source_id', required: true }],
    write: true,
    multipart: true,
    consequenceKey: 'apidocs.consequence_file',
    curl: `curl -X POST "$FARO/data-sources/$SOURCE_ID/file" \\\n  -H "Authorization: Bearer $KEY" \\\n  -F "file=@ventas.csv"`,
  },
  {
    id: 'train',
    method: 'POST',
    path: '/sessions/{session_id}/train',
    pathParams: [{ name: 'session_id', required: true }],
    write: true,
    bodyTemplate: '{}',
    consequenceKey: 'apidocs.consequence_train',
    curl: `curl -X POST "$FARO/sessions/$SESSION/train" \\\n  -H "Authorization: Bearer $KEY" \\\n  -H "Content-Type: application/json" -d '{}'`,
  },
  {
    id: 'train_status',
    method: 'GET',
    path: '/sessions/{session_id}/train/status',
    pathParams: [{ name: 'session_id', required: true }],
    curl: `curl "$FARO/sessions/$SESSION/train/status" \\\n  -H "Authorization: Bearer $KEY"`,
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
    curl: `curl "$FARO/inventory/status?signal=PEDIR_YA" \\\n  -H "Authorization: Bearer $KEY"`,
  },
  {
    id: 'briefing',
    method: 'GET',
    path: '/inventory/morning-briefing',
    query: [{ name: 'session_id' }],
    curl: `curl "$FARO/inventory/morning-briefing" \\\n  -H "Authorization: Bearer $KEY"`,
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
    curl: `curl -X POST "$FARO/inventory/log-po?session_id=$SESSION" \\\n  -H "Authorization: Bearer $KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"items":[{"sku":"ABC-1","recommended_qty":120,\n                "final_qty":100,"status":"modified"}]}'`,
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

function CodeBlock({ text }: { text: string }) {
  const { t } = useLanguage()
  const [copied, setCopied] = useState(false)
  return (
    <div style={{ border: '1px solid var(--border-strong)', borderRadius: 10, overflow: 'hidden', background: 'var(--surface-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: 'var(--dim)', textTransform: 'uppercase', flex: 1 }}>
          curl
        </span>
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
      <pre style={{ margin: 0, padding: '14px 16px', fontFamily: MONO, fontSize: 12.5, lineHeight: 1.75, color: 'var(--text)', overflowX: 'auto', whiteSpace: 'pre' }}>
        {text}
      </pre>
    </div>
  )
}

function EndpointCard({ endpoint, token }: { endpoint: Endpoint; token: string }) {
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
      <header style={{ padding: '20px 24px 18px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <MethodChip method={endpoint.method} />
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text)' }}>
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
          padding: '18px 24px 20px', display: 'flex', flexDirection: 'column', gap: 12,
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

        <div style={{ padding: '18px 24px 20px', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
          <Eyebrow>{t('apidocs.section_example')}</Eyebrow>
          <CodeBlock text={endpoint.curl} />
        </div>
      </div>

      {result && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '16px 24px 20px', background: 'var(--surface-2)' }}>
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

function ApiDocsPage() {
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

  // The three facts an integrator checks before writing a line of code. They sit
  // in the header rather than in prose further down because they are the terms
  // of the contract, not commentary on it.
  const specs = [
    { label: t('apidocs.spec_auth'), value: 'Bearer sk_live_…', mono: true },
    { label: t('apidocs.spec_rate'), value: t('apidocs.spec_rate_value'), mono: false },
    { label: t('apidocs.spec_plan'), value: t('apidocs.spec_plan_value'), mono: false },
  ]

  return (
    // `-24px` cancels `.page-content`'s own padding so the header band reaches
    // the edges. Full bleed is the point: a reference that starts with a
    // floating card reads as one more screen, and this one is a contract with
    // somebody else's engineering team.
    <div style={{ margin: narrow ? 0 : -24 }}>
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

          <h1 style={{
            fontSize: narrow ? 26 : 36, fontWeight: 700, letterSpacing: '-0.025em',
            color: '#fff', margin: 0, lineHeight: 1.15,
          }}>
            {t('apidocs.title')}
          </h1>
          <p style={{
            fontSize: narrow ? 14 : 16, color: 'rgba(255,255,255,0.72)',
            margin: '12px 0 0', lineHeight: 1.6, maxWidth: 660,
          }}>
            {t('apidocs.intro')}
          </p>

          {/* The base URL, stated once and prominently. Every curl on this page
              says `$FARO`, and the console itself calls a RELATIVE path through
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
                borderRadius: 7, padding: '7px 12px',
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
        position: 'sticky', top: 0, zIndex: 6,
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
      }}>
        <div style={{
          maxWidth: 1440, margin: '0 auto', padding: narrow ? '12px 20px' : '14px 48px',
          display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
        }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 260 }}>
            <span style={{ fontSize: 12, color: 'var(--dim)', whiteSpace: 'nowrap' }}>
              {t('apidocs.try_token_label')}
            </span>
            <Input
              type="password"
              name="api_console_token"
              // An unmarked password field is an invitation in BOTH directions:
              // the manager offers to save the raw sk_live_ key (which exists
              // nowhere else — the server keeps only a hash), and it autofills
              // the user's saved Faro PASSWORD into it, which would then travel
              // in an Authorization header. `integraciones/page.tsx` already
              // does this on its credential input; this page had omitted it.
              autoComplete="off"
              spellCheck={false}
              data-1p-ignore
              data-lpignore="true"
              size="sm"
              aria-label={t('apidocs.try_token_label')}
              placeholder={t('apidocs.try_token_ph')}
              value={token}
              onChange={e => setToken(e.target.value)}
              style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 12 }}
            />
          </label>
          <Link href="/automatizacion" style={{ textDecoration: 'none' }}>
            <Button variant="secondary" size="sm" icon={<KeyRound size={12} />}>
              {t('apidocs.get_your_key')}
            </Button>
          </Link>
        </div>
      </div>

      <div style={{
        maxWidth: 1440, margin: '0 auto',
        padding: narrow ? '20px' : '32px 48px 64px',
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
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {ep.path}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
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

          {ENDPOINTS.map(ep => <EndpointCard key={ep.id} endpoint={ep} token={token} />)}
          <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.7 }}>
            {t('apidocs.footer_promise')}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Page() {
  return (
    <FeatureGate feature="api_access">
      <ApiDocsPage />
    </FeatureGate>
  )
}

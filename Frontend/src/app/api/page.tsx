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
import { useState } from 'react'
import Link from 'next/link'
import { Play, Copy, Check, KeyRound, AlertTriangle } from 'lucide-react'
import Card from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import Input, { Textarea } from '@/components/ui/Input'
import FeatureGate from '@/components/ui/FeatureGate'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'

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
  const safe = useSafeCopy()
  const confirm = useConfirm()
  const [values, setValues] = useState<Record<string, string>>({})
  const [body, setBody] = useState(endpoint.bodyTemplate ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ status: number; ms: number; body: string } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const set = (name: string, v: string) => setValues(prev => ({ ...prev, [name]: v }))

  function buildPath(): string {
    let path = endpoint.path
    for (const p of endpoint.pathParams ?? []) {
      path = path.replace(`{${p.name}}`, encodeURIComponent(values[p.name] ?? ''))
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
    if (endpoint.write) {
      const go = await confirm({
        title: t('apidocs.confirm_title'),
        message: safe(endpoint.consequenceKey!, t('apidocs.confirm_title')),
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
      let shown = text
      try { shown = JSON.stringify(JSON.parse(text), null, 2) } catch { /* not JSON: show it raw */ }
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
  const canRun = token.trim() !== '' && !missingPathParam && !missingQueryParam && !missingFile
  const statusColor = !result ? 'var(--dim)'
    : result.status < 300 ? 'var(--success)'
    : result.status < 500 ? 'var(--warning)' : 'var(--danger)'

  return (
    <Card padding="16px 18px" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 11, fontWeight: 700, fontFamily: MONO, padding: '2px 8px', borderRadius: 6,
          color: endpoint.method === 'GET' ? 'var(--info)' : 'var(--warning)',
          background: endpoint.method === 'GET'
            ? 'color-mix(in srgb, var(--info) 12%, transparent)'
            : 'color-mix(in srgb, var(--warning) 14%, transparent)',
        }}>
          {endpoint.method}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>{endpoint.path}</span>
      </div>

      <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6 }}>
        {safe(`apidocs.${endpoint.id}_desc`)}
      </div>

      {(endpoint.pathParams ?? []).concat(endpoint.query ?? []).length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {(endpoint.pathParams ?? []).concat(endpoint.query ?? []).map(p => (
            <label key={p.name} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 11, color: 'var(--dim)', fontFamily: MONO }}>
                {p.name}{p.required ? ' *' : ''}
              </span>
              <Input
                size="sm"
                name={`${endpoint.id}_${p.name}`}
                aria-label={p.name}
                placeholder={p.placeholder ?? ''}
                value={values[p.name] ?? ''}
                onChange={e => set(p.name, e.target.value)}
                style={{ width: 170, fontFamily: MONO, fontSize: 12 }}
              />
            </label>
          ))}
        </div>
      )}

      <CodeBlock text={endpoint.curl} />

      {endpoint.write && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, lineHeight: 1.6,
          color: 'var(--text)', background: 'color-mix(in srgb, var(--warning) 8%, transparent)',
          border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)',
          borderRadius: 8, padding: '10px 12px',
        }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: 'var(--warning)' }} aria-hidden="true" />
          <span>{safe(endpoint.consequenceKey!)}</span>
        </div>
      )}

      {endpoint.multipart && (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('apidocs.try_file_label')} *</span>
          <input
            type="file"
            name={`${endpoint.id}_file`}
            aria-label={t('apidocs.try_file_label')}
            accept=".csv,.xlsx,.xls,.parquet,.json"
            onChange={e => setFile(e.target.files?.[0] ?? null)}
            style={{ fontSize: 12, color: 'var(--text)' }}
          />
        </label>
      )}

      {endpoint.bodyTemplate !== undefined && (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 11, color: 'var(--dim)' }}>{t('apidocs.try_body_label')}</span>
          <Textarea
            name={`${endpoint.id}_body`}
            aria-label={t('apidocs.try_body_label')}
            value={body}
            onChange={e => setBody(e.target.value)}
            rows={endpoint.id === 'logpo' ? 7 : 2}
            style={{ fontFamily: MONO, fontSize: 12, lineHeight: 1.7 }}
          />
        </label>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Button
          variant={endpoint.write ? 'danger' : 'primary'}
          size="sm" icon={<Play size={12} />}
          disabled={!canRun} loading={busy} onClick={run}
        >
          {busy ? t('apidocs.try_running') : t('apidocs.try_run')}
        </Button>
        {!token.trim() && (
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('apidocs.try_empty')}</span>
        )}
        {result && (
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>
            {t('apidocs.try_status')}: <strong style={{ color: statusColor }}>{result.status}</strong>
            {'  ·  '}{t('apidocs.try_duration_ms', { ms: result.ms })}
          </span>
        )}
      </div>

      {failure && (
        <div style={{ fontSize: 12, color: 'var(--danger)' }}>{t('apidocs.try_error')} {failure}</div>
      )}

      {result && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', color: 'var(--dim)', textTransform: 'uppercase', marginBottom: 6 }}>
            {t('apidocs.try_response')}
          </div>
          <pre style={{
            margin: 0, padding: '12px 14px', maxHeight: 320, overflow: 'auto',
            background: 'var(--surface-3)', border: '1px solid var(--border)', borderRadius: 8,
            fontFamily: MONO, fontSize: 12, lineHeight: 1.7, color: 'var(--text)',
          }}>
            {result.body}
          </pre>
        </div>
      )}
    </Card>
  )
}

function ApiDocsPage() {
  const { t } = useLanguage()
  // React state only, never localStorage. The raw key exists nowhere else — the
  // server stores a hash — so persisting it here would be the only copy at rest,
  // reachable by any script on the page.
  const [token, setToken] = useState('')

  return (
    <div style={{ padding: '32px 40px', maxWidth: 1000, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)', margin: 0 }}>{t('apidocs.title')}</h1>
        <p style={{ fontSize: 14, color: 'var(--muted)', margin: '6px 0 0', lineHeight: 1.6 }}>{t('apidocs.intro')}</p>
      </div>

      <Card padding="16px 18px" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 260 }}>
            <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('apidocs.try_token_label')}</span>
            <Input
              type="password"
              name="api_console_token"
              aria-label={t('apidocs.try_token_label')}
              placeholder={t('apidocs.try_token_ph')}
              value={token}
              onChange={e => setToken(e.target.value)}
              style={{ fontFamily: MONO, fontSize: 12 }}
            />
          </label>
          <Link href="/automatizacion" style={{ textDecoration: 'none' }}>
            <Button variant="secondary" size="sm" icon={<KeyRound size={12} />}>
              {t('apidocs.get_your_key')}
            </Button>
          </Link>
        </div>
        <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.6 }}>
          {t('apidocs.try_token_warning')}
        </div>
      </Card>

      <Card padding="16px 18px" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7 }}>
          <strong>{t('apidocs.auth_heading')}. </strong>{t('apidocs.auth_desc')} {t('apidocs.auth_role_note')}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7 }}>
          <strong>{t('apidocs.limits_heading')}. </strong>{t('apidocs.limits_desc')} {t('apidocs.limits_429')}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7 }}>
          <strong>{t('apidocs.envelope_heading')}. </strong>{t('apidocs.envelope_desc')} {t('apidocs.envelope_errors')}
        </div>
      </Card>

      {ENDPOINTS.map(ep => <EndpointCard key={ep.id} endpoint={ep} token={token} />)}

      <div style={{ fontSize: 12, color: 'var(--dim)', lineHeight: 1.7, paddingBottom: 20 }}>
        {t('apidocs.footer_promise')}
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

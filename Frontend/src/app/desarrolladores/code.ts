// The code samples on /desarrolladores: generated from the endpoint data
// (src/data/public-api.json) and coloured by a deliberately tiny highlighter.
//
// Tiny on purpose: the page shows four languages (bash, JavaScript, Python,
// JSON) in snippets this module writes itself, so a general-purpose
// highlighter would be a large dependency to colour text whose shape is known
// in advance. Pure functions, no React — the page renders the tokens.

export type Param = {
  name: string; in: string; required: boolean; type: string; description: string; example: unknown
}
export type Field = { name: string; type: string; required: boolean; description: string }
export type Endpoint = {
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

export type CodeLang = 'curl' | 'js' | 'python'
export const CODE_LANGS: { id: CodeLang; label: string }[] = [
  { id: 'curl', label: 'cURL' },
  { id: 'js', label: 'JavaScript' },
  { id: 'python', label: 'Python' },
]

// ── Samples ───────────────────────────────────────────────────────────────────

function envName(param: string): string {
  return param.replace(/[^a-zA-Z0-9]/g, '_').toUpperCase()
}

function identName(param: string): string {
  return param.replace(/[^a-zA-Z0-9_]/g, '_')
}

/** A header parameter's example in the export is its own name, which reads as
 *  a value someone should type. A placeholder says "put yours here" instead. */
function headerValue(p: Param): string {
  return typeof p.example === 'string' && p.example !== p.name ? p.example : `<${p.name.toLowerCase()}>`
}

function queryValue(p: Param): string {
  return p.example === undefined || p.example === null ? `<${p.name}>` : String(p.example)
}

function isMultipart(ep: Endpoint): boolean {
  return !!ep.request_body && ep.request_body.content_type.startsWith('multipart/')
}

function indent(text: string, by: string): string {
  return text.split('\n').map((l, i) => (i === 0 ? l : by + l)).join('\n')
}

export function curlFor(ep: Endpoint): string {
  let path = ep.path
  for (const p of ep.parameters.filter(p => p.in === 'path')) {
    path = path.replace(`{${p.name}}`, `$${envName(p.name)}`)
  }
  const query = ep.parameters
    .filter(p => p.in === 'query' && p.required)
    .map(p => `${p.name}=$${envName(p.name)}`)
  const url = `$STOCKAI${path}${query.length ? `?${query.join('&')}` : ''}`
  const lines = [`curl${ep.method === 'GET' ? '' : ` -X ${ep.method}`} "${url}"`, `  -H "Authorization: Bearer $STOCKAI_KEY"`]
  for (const h of ep.parameters.filter(p => p.in === 'header')) {
    lines.push(`  -H "${h.name}: ${headerValue(h)}"`)
  }
  const body = ep.request_body
  if (body) {
    if (isMultipart(ep)) {
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

export function jsFor(ep: Endpoint, base: string): string {
  const out: string[] = [`const STOCKAI = '${base}'`]
  const pathParams = ep.parameters.filter(p => p.in === 'path')
  for (const p of pathParams) out.push(`const ${identName(p.name)} = '${queryValue(p)}'`)
  let path = ep.path
  for (const p of pathParams) path = path.replace(`{${p.name}}`, `\${${identName(p.name)}}`)
  const query = ep.parameters
    .filter(p => p.in === 'query' && p.required)
    .map(p => `${p.name}=${encodeURIComponent(queryValue(p))}`)
  const url = `\`\${STOCKAI}${path}${query.length ? `?${query.join('&')}` : ''}\``

  const body = ep.request_body
  if (body && isMultipart(ep)) {
    out.push('', 'const form = new FormData()')
    for (const f of body.fields) {
      out.push(f.type === 'file'
        ? `form.append('${f.name}', file) // a File from an <input type="file">, or a Blob`
        : `form.append('${f.name}', '…')`)
    }
  }

  const opts: string[] = []
  if (ep.method !== 'GET') opts.push(`  method: '${ep.method}',`)
  const headers = [`    Authorization: \`Bearer \${process.env.STOCKAI_KEY}\`,`]
  if (body && !isMultipart(ep)) headers.push(`    'Content-Type': 'application/json',`)
  for (const h of ep.parameters.filter(p => p.in === 'header')) headers.push(`    '${h.name}': '${headerValue(h)}',`)
  opts.push('  headers: {', ...headers, '  },')
  if (body) {
    opts.push(isMultipart(ep)
      ? '  body: form,'
      : `  body: JSON.stringify(${indent(JSON.stringify(body.example ?? {}, null, 2), '  ')}),`)
  }
  out.push('', `const res = await fetch(${url}, {`, ...opts, '})')
  out.push(ep.success_status === 204 ? 'if (!res.ok) throw new Error(`StockAI ${res.status}`)' : 'const { data } = await res.json()')
  return out.join('\n')
}

/** JSON text as a Python literal: true/false/null outside strings become
 *  True/False/None. Walks the text so a string containing "null" is left alone. */
function pyLiteral(value: unknown, pad: string): string {
  const json = JSON.stringify(value, null, 4)
  let out = ''
  for (let i = 0; i < json.length;) {
    if (json[i] === '"') {
      let j = i + 1
      while (j < json.length && json[j] !== '"') j += json[j] === '\\' ? 2 : 1
      out += json.slice(i, j + 1)
      i = j + 1
      continue
    }
    const word = /^(true|false|null)/.exec(json.slice(i))
    if (word) {
      out += word[1] === 'true' ? 'True' : word[1] === 'false' ? 'False' : 'None'
      i += word[1].length
      continue
    }
    out += json[i]
    i++
  }
  return indent(out, pad)
}

export function pythonFor(ep: Endpoint, base: string): string {
  const out = ['import os', 'import requests', '', `STOCKAI = "${base}"`]
  const pathParams = ep.parameters.filter(p => p.in === 'path')
  for (const p of pathParams) out.push(`${identName(p.name)} = "${queryValue(p)}"`)
  let path = ep.path
  for (const p of pathParams) path = path.replace(`{${p.name}}`, `{${identName(p.name)}}`)

  const args = [`    f"{STOCKAI}${path}",`]
  const headers = [`"Authorization": f"Bearer {os.environ['STOCKAI_KEY']}"`]
  for (const h of ep.parameters.filter(p => p.in === 'header')) headers.push(`"${h.name}": "${headerValue(h)}"`)
  args.push('    headers={', ...headers.map(h => `        ${h},`), '    },')
  const query = ep.parameters.filter(p => p.in === 'query' && p.required)
  if (query.length) {
    args.push(`    params={${query.map(p => `"${p.name}": ${JSON.stringify(p.example ?? `<${p.name}>`)}`).join(', ')}},`)
  }
  const body = ep.request_body
  if (body) {
    if (isMultipart(ep)) {
      const files = body.fields.filter(f => f.type === 'file')
      const data = body.fields.filter(f => f.type !== 'file')
      if (files.length) args.push(`    files={${files.map(f => `"${f.name}": open("sales.csv", "rb")`).join(', ')}},`)
      if (data.length) args.push(`    data={${data.map(f => `"${f.name}": "…"`).join(', ')}},`)
    } else {
      args.push(`    json=${pyLiteral(body.example ?? {}, '    ')},`)
    }
  }
  out.push('', `res = requests.${ep.method.toLowerCase()}(`, ...args, ')', 'res.raise_for_status()')
  if (ep.success_status !== 204) out.push('data = res.json()["data"]')
  return out.join('\n')
}

export function sampleFor(lang: CodeLang, ep: Endpoint, base: string): string {
  return lang === 'curl' ? curlFor(ep) : lang === 'js' ? jsFor(ep, base) : pythonFor(ep, base)
}

/** The envelope every JSON answer comes in (see `envelope` in i18n/developers.ts).
 *  What `data` holds is endpoint-specific and not in the export, so it stays
 *  empty rather than invented. */
export const ENVELOPE_SAMPLE = `{
  "success": true,
  "data": {},
  "meta": {
    "timestamp": "2026-10-02T08:00:00Z"
  }
}`

export const STATUS_TEXT: Record<number, string> = {
  200: 'OK', 201: 'Created', 202: 'Accepted', 204: 'No Content',
}

// ── Highlighter ───────────────────────────────────────────────────────────────

export type HlLang = 'bash' | 'js' | 'python' | 'json'
/** [css class suffix ('' = plain), text] */
export type Token = [string, string]

const KEYWORDS: Record<HlLang, Set<string>> = {
  bash: new Set(['export']),
  js: new Set(['const', 'let', 'await', 'async', 'new', 'return', 'if', 'throw', 'of', 'import', 'from']),
  python: new Set(['import', 'from', 'as', 'def', 'return', 'if']),
  json: new Set(),
}
const LITERALS = new Set(['true', 'false', 'null', 'True', 'False', 'None', 'undefined'])

export function highlight(src: string, lang: HlLang): Token[] {
  const out: Token[] = []
  const push = (cls: string, text: string) => {
    const last = out[out.length - 1]
    if (last && last[0] === cls) last[1] += text
    else out.push([cls, text])
  }
  const lineStart = (i: number) => {
    let j = i - 1
    while (j >= 0 && (src[j] === ' ' || src[j] === '\t')) j--
    return j < 0 || src[j] === '\n'
  }
  let i = 0
  while (i < src.length) {
    const c = src[i]
    // Comments.
    if (((lang === 'bash' || lang === 'python') && c === '#' && (i === 0 || /\s/.test(src[i - 1])))
      || (lang === 'js' && c === '/' && src[i + 1] === '/')) {
      const end = src.indexOf('\n', i)
      const j = end === -1 ? src.length : end
      push('com', src.slice(i, j))
      i = j
      continue
    }
    // Strings (Python's f-prefix rides along with its string).
    const fPrefix = lang === 'python' && c === 'f' && (src[i + 1] === '"' || src[i + 1] === "'") && !/\w/.test(src[i - 1] ?? '')
    const q = fPrefix ? src[i + 1] : c
    if (q === '"' || q === "'" || (q === '`' && lang === 'js')) {
      let j = i + (fPrefix ? 2 : 1)
      while (j < src.length && src[j] !== q) j += src[j] === '\\' ? 2 : 1
      j = Math.min(j + 1, src.length)
      let k = j
      while (src[k] === ' ') k++
      const isKey = lang !== 'bash' && src[k] === ':'
      push(isKey ? 'prop' : 'str', src.slice(i, j))
      i = j
      continue
    }
    // Shell variables: $NAME and ${NAME}.
    if (lang === 'bash' && c === '$' && /[A-Za-z_{]/.test(src[i + 1] ?? '')) {
      const m = /^\$(\{[^}]*\}|[A-Za-z_]\w*)/.exec(src.slice(i))
      if (m) { push('var', m[0]); i += m[0].length; continue }
    }
    // curl flags.
    if (lang === 'bash' && c === '-' && /\s/.test(src[i - 1] ?? ' ') && /[A-Za-z-]/.test(src[i + 1] ?? '')) {
      const m = /^--?[A-Za-z][\w-]*/.exec(src.slice(i))
      if (m) { push('flag', m[0]); i += m[0].length; continue }
    }
    // Numbers.
    if (/\d/.test(c) && !/[\w$]/.test(src[i - 1] ?? '')) {
      const m = /^\d[\d._]*/.exec(src.slice(i))!
      push('num', m[0])
      i += m[0].length
      continue
    }
    // Identifiers.
    if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][\w]*/.exec(src.slice(i))!
      const w = m[0]
      let cls = ''
      if (KEYWORDS[lang].has(w)) cls = 'kw'
      else if (LITERALS.has(w)) cls = 'lit'
      else if (src[i + w.length] === '(') cls = 'fn'
      else if (lang === 'bash' && lineStart(i)) cls = 'fn'
      else if (lang === 'js' && /^[A-Z][A-Z_]+$/.test(w)) cls = 'var'
      else if (lang === 'python' && /^[A-Z][A-Z_]+$/.test(w)) cls = 'var'
      push(cls, w)
      i += w.length
      continue
    }
    if ('{}[]():,=.;\\'.includes(c)) { push('pun', c); i++; continue }
    push('', c)
    i++
  }
  return out
}

export function hlLangOf(lang: CodeLang): HlLang {
  return lang === 'curl' ? 'bash' : lang
}

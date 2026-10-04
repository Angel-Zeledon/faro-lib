// The code samples on /desarrolladores: generated from the endpoint data
// (src/data/public-api.json) and coloured by a deliberately tiny highlighter.
//
// Tiny on purpose: the page shows snippets this module's shared generator
// writes itself, so a general-purpose
// highlighter would be a large dependency to colour text whose shape is known
// in advance. Pure functions, no React — the page renders the tokens.

import { SAMPLE_LANGS, generateSample, highlightLangOf, type SampleLang, type SampleSpec } from '@/lib/codeSamples'

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

// The sample generator itself is shared with the in-app console (/api):
// lib/codeSamples.ts. This module only turns an exported endpoint into the
// generator's neutral request description.

export type CodeLang = SampleLang
export const CODE_LANGS = SAMPLE_LANGS

/** A header parameter's example in the export is its own name, which reads as
 *  a value someone should type. A placeholder says "put yours here" instead. */
function headerValue(p: Param): string {
  return typeof p.example === 'string' && p.example !== p.name ? p.example : `<${p.name.toLowerCase()}>`
}

function paramValue(p: Param): string {
  return p.example === undefined || p.example === null ? `<${p.name}>` : String(p.example)
}

export function specFor(ep: Endpoint, base: string): SampleSpec {
  const body = ep.request_body
  return {
    method: ep.method,
    base,
    path: ep.path,
    pathParams: ep.parameters.filter(p => p.in === 'path').map(p => ({ name: p.name, value: paramValue(p) })),
    query: ep.parameters.filter(p => p.in === 'query' && p.required).map(p => ({ name: p.name, value: paramValue(p), env: true })),
    headers: ep.parameters.filter(p => p.in === 'header').map(h => ({ name: h.name, value: headerValue(h) })),
    body: !body ? null
      : body.content_type.startsWith('multipart/')
        ? { kind: 'multipart', fields: body.fields.map(f => ({ name: f.name, file: f.type === 'file' })) }
        : { kind: 'json', value: body.example ?? {} },
    expectsJson: ep.success_status !== 204,
  }
}

export function sampleFor(lang: CodeLang, ep: Endpoint, base: string): string {
  return generateSample(lang, specFor(ep, base))
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

// 'clike' covers Java, Kotlin, Swift, C#, Go and Rust: one grammar for the
// shared shape (// comments, double-quoted strings, call-style functions).
export type HlLang = 'bash' | 'js' | 'python' | 'json' | 'clike'
/** [css class suffix ('' = plain), text] */
export type Token = [string, string]

const KEYWORDS: Record<HlLang, Set<string>> = {
  bash: new Set(['export']),
  js: new Set(['const', 'let', 'await', 'async', 'new', 'return', 'if', 'throw', 'of', 'import', 'from']),
  python: new Set(['import', 'from', 'as', 'def', 'return', 'if']),
  json: new Set(),
  clike: new Set([
    'import', 'package', 'use', 'public', 'class', 'static', 'void', 'throws', 'new', 'val', 'var', 'let', 'fun',
    'func', 'fn', 'async', 'await', 'try', 'return', 'if', 'else', 'guard', 'using', 'defer', 'mut',
  ]),
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
      || ((lang === 'js' || lang === 'clike') && c === '/' && src[i + 1] === '/')) {
      const end = src.indexOf('\n', i)
      const j = end === -1 ? src.length : end
      push('com', src.slice(i, j))
      i = j
      continue
    }
    // Triple-quoted strings (Java text block, Kotlin/C# raw, Swift #""").
    if (lang === 'clike') {
      const m = /^#?"""/.exec(src.slice(i))
      if (m) {
        const close = m[0].startsWith('#') ? '"""#' : '"""'
        const end = src.indexOf(close, i + m[0].length)
        const j = end === -1 ? src.length : end + close.length
        push('str', src.slice(i, j))
        i = j
        continue
      }
    }
    // Strings (Python's f-prefix rides along with its string).
    const fPrefix = lang === 'python' && c === 'f' && (src[i + 1] === '"' || src[i + 1] === "'") && !/\w/.test(src[i - 1] ?? '')
    const q = fPrefix ? src[i + 1] : c
    if (q === '"' || q === "'" || (q === '`' && (lang === 'js' || lang === 'clike'))) {
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
      else if ((lang === 'js' || lang === 'clike') && /^[A-Z][A-Z_]+$/.test(w)) cls = 'var'
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
  return highlightLangOf(lang)
}

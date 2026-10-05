// Unit check for the shared code-sample generator (src/lib/codeSamples.ts).
//
// The frontend has no test runner, so this is a plain node script (Node 22.18+
// strips the types itself): `npm run check:samples`. It generates every
// language for representative endpoints AND for every endpoint of the public
// API export, and fails on a sample that
//   - is missing the bearer header, the method or the route,
//   - leaks `undefined` / `[object Object]`,
//   - has unbalanced brackets, or
//   - (JavaScript, Python) does not even parse.
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { SAMPLE_LANGS, generateSample } from '../src/lib/codeSamples.ts'

const api = JSON.parse(readFileSync(new URL('../src/data/public-api.json', import.meta.url), 'utf8'))
const ALL = api.tags.flatMap(g => g.endpoints)
const BASE = 'https://app.stockai.es/api/v1'
let failures = 0
const fail = (msg) => { failures++; console.error(`FAIL ${msg}`) }

const specOf = (ep) => {
  const val = (p) => (p.example === undefined || p.example === null ? `<${p.name}>` : String(p.example))
  const b = ep.request_body
  return {
    method: ep.method,
    base: BASE,
    path: ep.path,
    pathParams: ep.parameters.filter(p => p.in === 'path').map(p => ({ name: p.name, value: val(p) })),
    query: ep.parameters.filter(p => p.in === 'query' && p.required).map(p => ({ name: p.name, value: val(p), env: true })),
    headers: ep.parameters.filter(p => p.in === 'header').map(h => ({ name: h.name, value: `<${h.name.toLowerCase()}>` })),
    body: !b ? null
      : b.content_type.startsWith('multipart/')
        ? { kind: 'multipart', fields: (b.schema.fields ?? []).map(f => ({ name: f.name, file: f.type === 'file' })) }
        : { kind: 'json', value: b.example ?? {} },
    expectsJson: ep.success_status !== 204,
  }
}

// Strings and comments removed, then every bracket must pair up.
function balanced(code) {
  const stripped = code
    .replace(/"""[\s\S]*?"""#?/g, '""')
    .replace(/`[^`]*`/g, '``')
    .replace(/"(?:\\.|[^"\\\n])*"/g, '""')
    .replace(/'(?:\\.|[^'\\\n])*'/g, "''")
    .replace(/\/\/[^\n]*/g, '')
    .replace(/#[^\n]*/g, '')
  const pairs = { ')': '(', ']': '[', '}': '{' }
  const stack = []
  for (const c of stripped) {
    if ('([{'.includes(c)) stack.push(c)
    else if (c in pairs && stack.pop() !== pairs[c]) return false
  }
  return stack.length === 0
}

function check(label, lang, spec, code) {
  const where = `${label} [${lang}]`
  if (/undefined|\[object/.test(code)) fail(`${where}: leaked undefined/[object Object]`)
  if (!/Authorization|bearer_auth/.test(code)) fail(`${where}: no bearer header`)
  if (!code.includes('STOCKAI')) fail(`${where}: key is not read from STOCKAI_KEY`)
  const stem = spec.path.split('{')[0]
  if (!code.includes(stem)) fail(`${where}: route ${stem} missing`)
  if (lang !== 'curl' && lang !== 'js' && lang !== 'python' && !code.includes(BASE)) fail(`${where}: base URL missing`)
  if (spec.body?.kind === 'json' && !code.includes('application/json') && lang !== 'rust' && lang !== 'python') fail(`${where}: JSON body without Content-Type`)
  if (!balanced(code)) fail(`${where}: unbalanced brackets`)
  if (lang === 'js') {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
    try { new AsyncFunction('file', 'process', code) } catch (e) { fail(`${where}: does not parse: ${e.message}`) }
  }
  if (lang === 'python') {
    const r = spawnSync('python', ['-c', 'import ast,sys; ast.parse(sys.stdin.read())'], { input: code, encoding: 'utf8' })
    if (r.error) console.warn('skip python parse: python not found')
    else if (r.status !== 0) fail(`${where}: does not parse: ${r.stderr.trim().split('\n').pop()}`)
  }
}

// Representative specs, each with something the others lack.
const reps = {
  'GET plain': { method: 'GET', base: BASE, path: '/planning', pathParams: [], query: [], headers: [], body: null, expectsJson: true },
  'GET path+query': {
    method: 'GET', base: BASE, path: '/sessions/{session_id}/train/status',
    pathParams: [{ name: 'session_id', value: 'a b/c' }], query: [{ name: 'signal', value: 'PEDIR_YA' }, { name: 'q', value: 'x&y', env: true }],
    headers: [], body: null, expectsJson: true,
  },
  'POST json': {
    method: 'POST', base: BASE, path: '/inventory/log-po', pathParams: [],
    query: [{ name: 'session_id', value: '<session_id>', env: true }], headers: [{ name: 'Idempotency-Key', value: 'abc' }],
    body: { kind: 'json', value: { items: [{ sku: 'A"1', recommended_qty: 120, note: 'back\\slash $x', ok: true, none: null }] } }, expectsJson: true,
  },
  'POST multipart': {
    method: 'POST', base: BASE, path: '/data-sources/{source_id}/file', pathParams: [{ name: 'source_id', value: '<source_id>' }],
    query: [], headers: [], body: { kind: 'multipart', fields: [{ name: 'file', file: true }, { name: 'note', file: false }] }, expectsJson: true,
  },
  'PATCH json': { method: 'PATCH', base: BASE, path: '/x', pathParams: [], query: [], headers: [], body: { kind: 'json', value: { a: 1 } }, expectsJson: true },
  'DELETE 204': { method: 'DELETE', base: BASE, path: '/x/{id}', pathParams: [{ name: 'id', value: '7' }], query: [], headers: [], body: null, expectsJson: false },
}

for (const lang of SAMPLE_LANGS.map(l => l.id)) {
  for (const [name, spec] of Object.entries(reps)) check(name, lang, spec, generateSample(lang, spec))
}
let n = 0
for (const ep of ALL) {
  for (const { id } of SAMPLE_LANGS) { check(`${ep.method} ${ep.path}`, id, specOf(ep), generateSample(id, specOf(ep))); n++ }
}

// Spot checks on what each idiom must contain.
const post = reps['POST json']
const must = {
  js: ['fetch(', 'JSON.stringify('],
  java: ['HttpClient', '.POST(', '"""'],
  kotlin: ['HttpClient', 'trimIndent()', "${'$'}x"],
  swift: ['URLSession.shared.data(for: request)', '#"""'],
  python: ['requests.post(', 'json={'],
  csharp: ['HttpClient', 'StringContent(', 'AuthenticationHeaderValue'],
  go: ['http.NewRequest("POST"', 'strings.NewReader'],
  rust: ['reqwest::Client', '.json(&json!('],
}
for (const [lang, needles] of Object.entries(must)) {
  const code = generateSample(lang, post)
  for (const s of needles) if (!code.includes(s)) fail(`POST json [${lang}]: expected ${JSON.stringify(s)}`)
}
if (!generateSample('swift', reps['POST multipart']).includes('multipart/form-data')) fail('swift multipart')
if (!generateSample('go', reps['POST multipart']).includes('multipart.NewWriter')) fail('go multipart')
if (!generateSample('csharp', reps['POST multipart']).includes('MultipartFormDataContent')) fail('csharp multipart')
if (!generateSample('rust', reps['POST multipart']).includes('reqwest::multipart::Form')) fail('rust multipart')

if (failures) {
  console.error(`\n${failures} failure(s)`)
  process.exit(1)
}
console.log(`ok: ${SAMPLE_LANGS.length} languages x (${Object.keys(reps).length} representative + ${ALL.length} exported endpoints) = ${SAMPLE_LANGS.length * Object.keys(reps).length + n} samples`)

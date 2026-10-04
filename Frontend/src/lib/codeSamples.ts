// The ONE request-sample generator behind every code example in the product:
// the in-app console (/api) and the public docs (/desarrolladores) both build a
// `SampleSpec` and call `generateSample`, so the two screens cannot drift.
//
// Pure functions, no React, no DOM. Each language uses only its own standard or
// most common HTTP library (fetch, java.net.http, URLSession, requests,
// HttpClient, net/http, reqwest). The key is read from the STOCKAI_KEY
// environment variable in every language — it is an `sk_live_…` secret and a
// sample must never teach pasting it into source.
//
// Checked by `npm run check:samples` (scripts/check-code-samples.mjs).

export type SampleLang =
  | 'curl' | 'js' | 'java' | 'kotlin' | 'swift' | 'python' | 'csharp' | 'go' | 'rust'

export const SAMPLE_LANGS: { id: SampleLang; label: string }[] = [
  { id: 'curl', label: 'cURL' },
  { id: 'js', label: 'JavaScript' },
  { id: 'java', label: 'Java' },
  { id: 'kotlin', label: 'Kotlin' },
  { id: 'swift', label: 'Swift' },
  { id: 'python', label: 'Python' },
  { id: 'csharp', label: 'C#' },
  { id: 'go', label: 'Go' },
  { id: 'rust', label: 'Rust' },
]

export function isSampleLang(v: unknown): v is SampleLang {
  return SAMPLE_LANGS.some(l => l.id === v)
}

export type SampleBody =
  | { kind: 'json'; value: unknown }
  | { kind: 'multipart'; fields: { name: string; file: boolean }[] }

export type SampleSpec = {
  method: string
  /** Origin plus API prefix, no trailing slash: `https://app.stockai.es/api/v1`. */
  base: string
  /** Route with `{name}` placeholders. */
  path: string
  pathParams: { name: string; value: string }[]
  /** `env`: the value is something the caller supplies, so cURL shows it as a
   *  shell variable instead of a literal. */
  query: { name: string; value: string; env?: boolean }[]
  /** Extra headers; Authorization and Content-Type are added by the generator. */
  headers: { name: string; value: string }[]
  body: SampleBody | null
  /** False for a 204: nothing to parse. */
  expectsJson: boolean
}

// ── Shared helpers ────────────────────────────────────────────────────────────

const envName = (s: string) => s.replace(/[^a-zA-Z0-9]/g, '_').toUpperCase()
const identName = (s: string) => s.replace(/[^a-zA-Z0-9_]/g, '_')

function indent(text: string, by: string): string {
  return text.split('\n').map((l, i) => (i === 0 ? l : by + l)).join('\n')
}

/** The route with path values percent-encoded and the query string appended. */
export function fullUrl(spec: SampleSpec): string {
  // A `<placeholder>` stays readable (the caller replaces it); real values are encoded.
  const enc = (v: string) => (/^<[^<>]+>$/.test(v) ? v : encodeURIComponent(v))
  let path = spec.path
  for (const p of spec.pathParams) path = path.replace(`{${p.name}}`, enc(p.value))
  const qs = spec.query.map(q => `${q.name}=${enc(q.value)}`).join('&')
  return `${spec.base}${path}${qs ? `?${qs}` : ''}`
}

const prettyJson = (v: unknown) => JSON.stringify(v ?? {}, null, 2)

/** A double-quoted string literal in the C family (Java, Kotlin, Swift, C#, Go,
 *  Rust): backslash and quote escaped; Kotlin also needs `$`. */
function quoted(s: string, opts: { dollar?: boolean } = {}): string {
  let out = s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r')
  if (opts.dollar) out = out.replace(/\$/g, '\\$')
  return `"${out}"`
}

function lines(...parts: (string | null | false | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === 'string').join('\n')
}

const KEY_NOTE = 'your sk_live_… key'

// ── cURL ──────────────────────────────────────────────────────────────────────

function curl(spec: SampleSpec): string {
  let path = spec.path
  for (const p of spec.pathParams) path = path.replace(`{${p.name}}`, `$${envName(p.name)}`)
  const qs = spec.query.map(q => `${q.name}=${q.env ? `$${envName(q.name)}` : encodeURIComponent(q.value)}`).join('&')
  const url = `$STOCKAI${path}${qs ? `?${qs}` : ''}`
  const out = [`curl${spec.method === 'GET' ? '' : ` -X ${spec.method}`} "${url}"`, `  -H "Authorization: Bearer $STOCKAI_KEY"`]
  for (const h of spec.headers) out.push(`  -H "${h.name}: ${h.value}"`)
  const b = spec.body
  if (b?.kind === 'multipart') {
    for (const f of b.fields) out.push(f.file ? `  -F "${f.name}=@sales.csv"` : `  -F "${f.name}=…"`)
  } else if (b) {
    out.push(`  -H "Content-Type: application/json"`)
    out.push(`  -d '${JSON.stringify(b.value ?? {}).replace(/'/g, `'\\''`)}'`)
  }
  return out.join(' \\\n')
}

// ── JavaScript (fetch, Node 18+ or any browser) ───────────────────────────────

function js(spec: SampleSpec): string {
  const out: string[] = [`const STOCKAI = '${spec.base}'`]
  for (const p of spec.pathParams) out.push(`const ${identName(p.name)} = '${p.value.replace(/'/g, "\\'")}'`)
  let path = spec.path
  for (const p of spec.pathParams) path = path.replace(`{${p.name}}`, `\${encodeURIComponent(${identName(p.name)})}`)
  const qs = spec.query.map(q => `${q.name}=${encodeURIComponent(q.value)}`).join('&')
  const url = `\`\${STOCKAI}${path}${qs ? `?${qs}` : ''}\``

  const b = spec.body
  if (b?.kind === 'multipart') {
    out.push('', 'const form = new FormData()')
    for (const f of b.fields) {
      out.push(f.file
        ? `form.append('${f.name}', file) // a File from an <input type="file">, or a Blob`
        : `form.append('${f.name}', '…')`)
    }
  }
  const opts: string[] = []
  if (spec.method !== 'GET') opts.push(`  method: '${spec.method}',`)
  const headers = [`    Authorization: \`Bearer \${process.env.STOCKAI_KEY}\`, // ${KEY_NOTE}`]
  if (b?.kind === 'json') headers.push(`    'Content-Type': 'application/json',`)
  for (const h of spec.headers) headers.push(`    '${h.name}': '${h.value.replace(/'/g, "\\'")}',`)
  opts.push('  headers: {', ...headers, '  },')
  if (b) {
    opts.push(b.kind === 'multipart'
      ? '  body: form,'
      : `  body: JSON.stringify(${indent(prettyJson(b.value), '  ')}),`)
  }
  out.push('', `const res = await fetch(${url}, {`, ...opts, '})')
  out.push(spec.expectsJson ? 'if (!res.ok) throw new Error(`StockAI ${res.status}`)\nconst { data } = await res.json()' : 'if (!res.ok) throw new Error(`StockAI ${res.status}`)')
  return out.join('\n')
}

// ── Java (java.net.http, JDK 15+ for text blocks) ─────────────────────────────

function javaTextBlock(s: string, pad: string): string {
  // Text blocks process escapes, so a JSON backslash must be doubled.
  const body = s.replace(/\\/g, '\\\\').replace(/"""/g, '\\"\\"\\"')
  return `"""\n${body.split('\n').map(l => pad + l).join('\n')}\n${pad}"""`
}

function java(spec: SampleSpec): string {
  const b = spec.body
  const I = '        '
  const out: string[] = [
    'import java.net.URI;',
    'import java.net.http.HttpClient;',
    'import java.net.http.HttpRequest;',
    'import java.net.http.HttpResponse;',
  ]
  if (b?.kind === 'multipart') out.push('import java.io.ByteArrayOutputStream;', 'import java.nio.charset.StandardCharsets;', 'import java.nio.file.Files;', 'import java.nio.file.Path;')
  out.push('', 'public class StockAiExample {', '    public static void main(String[] args) throws Exception {',
    `${I}String apiKey = System.getenv("STOCKAI_KEY"); // ${KEY_NOTE}`)

  let publisher = 'HttpRequest.BodyPublishers.noBody()'
  if (b?.kind === 'json') {
    out.push(`${I}String body = ${javaTextBlock(prettyJson(b.value), I + '    ')};`)
    publisher = 'HttpRequest.BodyPublishers.ofString(body)'
  } else if (b?.kind === 'multipart') {
    out.push(`${I}String boundary = "StockAI" + System.nanoTime();`, `${I}ByteArrayOutputStream out = new ByteArrayOutputStream();`)
    for (const f of b.fields) {
      if (f.file) {
        out.push(`${I}out.write(("--" + boundary + "\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"; filename=\\"sales.csv\\"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n").getBytes(StandardCharsets.UTF_8));`,
          `${I}out.write(Files.readAllBytes(Path.of("sales.csv")));`,
          `${I}out.write("\\r\\n".getBytes(StandardCharsets.UTF_8));`)
      } else {
        out.push(`${I}out.write(("--" + boundary + "\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"\\r\\n\\r\\n…\\r\\n").getBytes(StandardCharsets.UTF_8));`)
      }
    }
    out.push(`${I}out.write(("--" + boundary + "--\\r\\n").getBytes(StandardCharsets.UTF_8));`)
    publisher = 'HttpRequest.BodyPublishers.ofByteArray(out.toByteArray())'
  }

  out.push('', `${I}HttpRequest request = HttpRequest.newBuilder()`,
    `${I}    .uri(URI.create(${quoted(fullUrl(spec))}))`,
    `${I}    .header("Authorization", "Bearer " + apiKey)`)
  if (b?.kind === 'json') out.push(`${I}    .header("Content-Type", "application/json")`)
  if (b?.kind === 'multipart') out.push(`${I}    .header("Content-Type", "multipart/form-data; boundary=" + boundary)`)
  for (const h of spec.headers) out.push(`${I}    .header(${quoted(h.name)}, ${quoted(h.value)})`)
  if (spec.method === 'GET' && !b) out.push(`${I}    .GET()`)
  else if (spec.method === 'DELETE' && !b) out.push(`${I}    .DELETE()`)
  else if (spec.method === 'POST') out.push(`${I}    .POST(${publisher})`)
  else if (spec.method === 'PUT') out.push(`${I}    .PUT(${publisher})`)
  else out.push(`${I}    .method("${spec.method}", ${publisher})`)
  out.push(`${I}    .build();`, '',
    `${I}HttpResponse<String> response = HttpClient.newHttpClient()`,
    `${I}    .send(request, HttpResponse.BodyHandlers.ofString());`,
    `${I}System.out.println(response.statusCode());`)
  if (spec.expectsJson) out.push(`${I}System.out.println(response.body());`)
  out.push('    }', '}')
  return out.join('\n')
}

// ── Kotlin (java.net.http — no dependency; Ktor needs a Gradle entry) ─────────

function kotlin(spec: SampleSpec): string {
  const b = spec.body
  const I = '    '
  const out: string[] = [
    'import java.net.URI',
    'import java.net.http.HttpClient',
    'import java.net.http.HttpRequest',
    'import java.net.http.HttpResponse',
  ]
  if (b?.kind === 'multipart') out.push('import java.io.ByteArrayOutputStream', 'import java.nio.file.Files', 'import java.nio.file.Path')
  out.push('', 'fun main() {', `${I}val apiKey = System.getenv("STOCKAI_KEY") // ${KEY_NOTE}`)

  let publisher = 'HttpRequest.BodyPublishers.noBody()'
  if (b?.kind === 'json') {
    const json = prettyJson(b.value).replace(/\$/g, () => "${'$'}").replace(/"""/g, () => '${\'"\'}${\'"\'}${\'"\'}')
    out.push(`${I}val body = """`, ...json.split('\n').map(l => `${I}${l}`), `${I}""".trimIndent()`)
    publisher = 'HttpRequest.BodyPublishers.ofString(body)'
  } else if (b?.kind === 'multipart') {
    out.push(`${I}val boundary = "StockAI\${System.nanoTime()}"`, `${I}val out = ByteArrayOutputStream()`)
    for (const f of b.fields) {
      if (f.file) {
        out.push(`${I}out.write("--$boundary\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"; filename=\\"sales.csv\\"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n".toByteArray())`,
          `${I}out.write(Files.readAllBytes(Path.of("sales.csv")))`,
          `${I}out.write("\\r\\n".toByteArray())`)
      } else {
        out.push(`${I}out.write("--$boundary\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"\\r\\n\\r\\n…\\r\\n".toByteArray())`)
      }
    }
    out.push(`${I}out.write("--$boundary--\\r\\n".toByteArray())`)
    publisher = 'HttpRequest.BodyPublishers.ofByteArray(out.toByteArray())'
  }

  out.push('', `${I}val request = HttpRequest.newBuilder()`,
    `${I}    .uri(URI.create(${quoted(fullUrl(spec), { dollar: true })}))`,
    `${I}    .header("Authorization", "Bearer $apiKey")`)
  if (b?.kind === 'json') out.push(`${I}    .header("Content-Type", "application/json")`)
  if (b?.kind === 'multipart') out.push(`${I}    .header("Content-Type", "multipart/form-data; boundary=$boundary")`)
  for (const h of spec.headers) out.push(`${I}    .header(${quoted(h.name, { dollar: true })}, ${quoted(h.value, { dollar: true })})`)
  out.push(`${I}    .method("${spec.method}", ${publisher})`, `${I}    .build()`, '',
    `${I}val response = HttpClient.newHttpClient()`,
    `${I}    .send(request, HttpResponse.BodyHandlers.ofString())`,
    `${I}println(response.statusCode())`)
  if (spec.expectsJson) out.push(`${I}println(response.body())`)
  out.push('}')
  return out.join('\n')
}

// ── Swift (Foundation URLSession, async/await) ────────────────────────────────

function swift(spec: SampleSpec): string {
  const b = spec.body
  const out: string[] = ['import Foundation', '',
    `let apiKey = ProcessInfo.processInfo.environment["STOCKAI_KEY"] ?? "" // ${KEY_NOTE}`,
    `var request = URLRequest(url: URL(string: ${quoted(fullUrl(spec))})!)`,
    `request.httpMethod = "${spec.method}"`,
    'request.setValue("Bearer \\(apiKey)", forHTTPHeaderField: "Authorization")']
  for (const h of spec.headers) out.push(`request.setValue(${quoted(h.value)}, forHTTPHeaderField: ${quoted(h.name)})`)
  if (b?.kind === 'json') {
    out.push('request.setValue("application/json", forHTTPHeaderField: "Content-Type")',
      'request.httpBody = #"""', prettyJson(b.value), '"""#.data(using: .utf8)')
  } else if (b?.kind === 'multipart') {
    out.push('', 'let boundary = "StockAI-\\(UUID().uuidString)"',
      'request.setValue("multipart/form-data; boundary=\\(boundary)", forHTTPHeaderField: "Content-Type")',
      'var body = Data()')
    for (const f of b.fields) {
      if (f.file) {
        out.push(`body.append("--\\(boundary)\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"; filename=\\"sales.csv\\"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n".data(using: .utf8)!)`,
          'body.append(try Data(contentsOf: URL(fileURLWithPath: "sales.csv")))',
          'body.append("\\r\\n".data(using: .utf8)!)')
      } else {
        out.push(`body.append("--\\(boundary)\\r\\nContent-Disposition: form-data; name=\\"${f.name}\\"\\r\\n\\r\\n…\\r\\n".data(using: .utf8)!)`)
      }
    }
    out.push('body.append("--\\(boundary)--\\r\\n".data(using: .utf8)!)', 'request.httpBody = body')
  }
  out.push('', 'let (data, response) = try await URLSession.shared.data(for: request)',
    'let status = (response as? HTTPURLResponse)?.statusCode ?? 0',
    'print(status)')
  if (spec.expectsJson) out.push('print(String(data: data, encoding: .utf8) ?? "")')
  return out.join('\n')
}

// ── Python (requests) ─────────────────────────────────────────────────────────

/** JSON as a Python literal: true/false/null outside strings become
 *  True/False/None. Walks the text so a string containing "null" is untouched. */
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

function python(spec: SampleSpec): string {
  const out = ['import os', 'import requests', '', `STOCKAI = ${JSON.stringify(spec.base)}`]
  for (const p of spec.pathParams) out.push(`${identName(p.name)} = ${JSON.stringify(p.value)}`)
  let path = spec.path
  for (const p of spec.pathParams) path = path.replace(`{${p.name}}`, `{${identName(p.name)}}`)
  const args = [`    f"{STOCKAI}${path}",`]
  const headers = [`"Authorization": f"Bearer {os.environ['STOCKAI_KEY']}"`]
  for (const h of spec.headers) headers.push(`${JSON.stringify(h.name)}: ${JSON.stringify(h.value)}`)
  args.push('    headers={', ...headers.map(h => `        ${h},`), `    },  # STOCKAI_KEY is ${KEY_NOTE}`)
  if (spec.query.length) {
    args.push(`    params={${spec.query.map(q => `${JSON.stringify(q.name)}: ${JSON.stringify(q.value)}`).join(', ')}},`)
  }
  const b = spec.body
  if (b?.kind === 'multipart') {
    const files = b.fields.filter(f => f.file)
    const data = b.fields.filter(f => !f.file)
    if (files.length) args.push(`    files={${files.map(f => `"${f.name}": open("sales.csv", "rb")`).join(', ')}},`)
    if (data.length) args.push(`    data={${data.map(f => `"${f.name}": "…"`).join(', ')}},`)
  } else if (b) {
    args.push(`    json=${pyLiteral(b.value ?? {}, '    ')},`)
  }
  out.push('', `res = requests.${spec.method.toLowerCase()}(`, ...args, ')', 'res.raise_for_status()')
  if (spec.expectsJson) out.push('data = res.json()["data"]')
  return out.join('\n')
}

// ── C# (HttpClient, .NET 8 top-level statements) ──────────────────────────────

function csharp(spec: SampleSpec): string {
  const b = spec.body
  const method = spec.method[0] + spec.method.slice(1).toLowerCase()
  const known = ['Get', 'Post', 'Put', 'Delete', 'Patch', 'Head'].includes(method)
  const out: string[] = ['using System.Net.Http.Headers;']
  if (b?.kind === 'json') out.push('using System.Text;')
  out.push('',
    `var apiKey = Environment.GetEnvironmentVariable("STOCKAI_KEY"); // ${KEY_NOTE}`,
    'using var client = new HttpClient();',
    `using var request = new HttpRequestMessage(${known ? `HttpMethod.${method}` : `new HttpMethod("${method}")`}, ${quoted(fullUrl(spec))});`,
    'request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);')
  for (const h of spec.headers) out.push(`request.Headers.Add(${quoted(h.name)}, ${quoted(h.value)});`)
  if (b?.kind === 'json') {
    out.push('request.Content = new StringContent("""', prettyJson(b.value), '""", Encoding.UTF8, "application/json");')
  } else if (b?.kind === 'multipart') {
    out.push('using var form = new MultipartFormDataContent();')
    for (const f of b.fields) {
      out.push(f.file
        ? `form.Add(new ByteArrayContent(await File.ReadAllBytesAsync("sales.csv")), "${f.name}", "sales.csv");`
        : `form.Add(new StringContent("…"), "${f.name}");`)
    }
    out.push('request.Content = form;')
  }
  out.push('', 'using var response = await client.SendAsync(request);',
    'response.EnsureSuccessStatusCode();',
    'Console.WriteLine((int)response.StatusCode);')
  if (spec.expectsJson) out.push('Console.WriteLine(await response.Content.ReadAsStringAsync());')
  return out.join('\n')
}

// ── Go (net/http) ─────────────────────────────────────────────────────────────

function goString(s: string): string {
  return s.includes('`') ? quoted(s) : `\`${s}\``
}

function go(spec: SampleSpec): string {
  const b = spec.body
  const imports = ['fmt', 'io', 'net/http', 'os']
  if (b?.kind === 'json') imports.push('strings')
  if (b?.kind === 'multipart') imports.push('bytes', 'mime/multipart')
  imports.sort()
  const out: string[] = ['package main', '', 'import (', ...imports.map(i => `\t"${i}"`), ')', '', 'func main() {']
  let reader = 'nil'
  if (b?.kind === 'json') {
    out.push(`\tbody := strings.NewReader(${goString(prettyJson(b.value))})`)
    reader = 'body'
  } else if (b?.kind === 'multipart') {
    out.push('\tvar buf bytes.Buffer', '\tw := multipart.NewWriter(&buf)')
    for (const f of b.fields) {
      if (f.file) {
        out.push('\tfile, err := os.Open("sales.csv")', '\tif err != nil {', '\t\tpanic(err)', '\t}', '\tdefer file.Close()',
          `\tpart, _ := w.CreateFormFile("${f.name}", "sales.csv")`, '\tio.Copy(part, file)')
      } else {
        out.push(`\tw.WriteField("${f.name}", "…")`)
      }
    }
    out.push('\tw.Close()')
    reader = '&buf'
  }
  out.push('', `\treq, err := http.NewRequest("${spec.method}", ${quoted(fullUrl(spec))}, ${reader})`,
    '\tif err != nil {', '\t\tpanic(err)', '\t}',
    `\treq.Header.Set("Authorization", "Bearer "+os.Getenv("STOCKAI_KEY")) // ${KEY_NOTE}`)
  if (b?.kind === 'json') out.push('\treq.Header.Set("Content-Type", "application/json")')
  if (b?.kind === 'multipart') out.push('\treq.Header.Set("Content-Type", w.FormDataContentType())')
  for (const h of spec.headers) out.push(`\treq.Header.Set(${quoted(h.name)}, ${quoted(h.value)})`)
  out.push('', '\tres, err := http.DefaultClient.Do(req)', '\tif err != nil {', '\t\tpanic(err)', '\t}', '\tdefer res.Body.Close()')
  if (spec.expectsJson) {
    out.push('\tdata, _ := io.ReadAll(res.Body)', '\tfmt.Println(res.StatusCode, string(data))')
  } else {
    out.push('\tfmt.Println(res.StatusCode)')
  }
  out.push('}')
  return out.join('\n')
}

// ── Rust (reqwest + tokio) ────────────────────────────────────────────────────

function rust(spec: SampleSpec): string {
  const b = spec.body
  const features = [b?.kind === 'json' ? '"json"' : null, b?.kind === 'multipart' ? '"multipart"' : null].filter(Boolean)
  const reqwest = features.length ? `{ version = "0.12", features = [${features.join(', ')}] }` : '"0.12"'
  const out: string[] = [
    '// Cargo.toml:',
    `//   reqwest = ${reqwest}`,
    '//   tokio = { version = "1", features = ["full"] }',
  ]
  if (b?.kind === 'json') out.push('//   serde_json = "1"')
  if (b?.kind === 'json') out.push('', 'use serde_json::json;')
  out.push('', '#[tokio::main]', 'async fn main() -> Result<(), Box<dyn std::error::Error>> {',
    `    let api_key = std::env::var("STOCKAI_KEY")?; // ${KEY_NOTE}`,
    '    let client = reqwest::Client::new();')
  if (b?.kind === 'multipart') {
    const parts = b.fields.map(f => f.file ? `.file("${f.name}", "sales.csv").await?` : `.text("${f.name}", "…")`)
    out.push('    let form = reqwest::multipart::Form::new()', ...parts.map(p => `        ${p}`).slice(0, -1), `        ${parts[parts.length - 1]};`)
  }
  const verb = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].includes(spec.method)
    ? `.${spec.method.toLowerCase()}(${quoted(fullUrl(spec))})`
    : `.request(reqwest::Method::from_bytes(b"${spec.method}")?, ${quoted(fullUrl(spec))})`
  out.push('    let res = client', `        ${verb}`, '        .bearer_auth(api_key)')
  for (const h of spec.headers) out.push(`        .header(${quoted(h.name)}, ${quoted(h.value)})`)
  if (b?.kind === 'json') out.push(`        .json(&json!(${indent(prettyJson(b.value), '        ')}))`)
  if (b?.kind === 'multipart') out.push('        .multipart(form)')
  out.push('        .send()', '        .await?;', '    println!("{}", res.status());')
  if (spec.expectsJson) out.push('    println!("{}", res.text().await?);')
  out.push('    Ok(())', '}')
  return out.join('\n')
}

// ── Entry point ───────────────────────────────────────────────────────────────

export function generateSample(lang: SampleLang, spec: SampleSpec): string {
  switch (lang) {
    case 'curl': return curl(spec)
    case 'js': return js(spec)
    case 'java': return java(spec)
    case 'kotlin': return kotlin(spec)
    case 'swift': return swift(spec)
    case 'python': return python(spec)
    case 'csharp': return csharp(spec)
    case 'go': return go(spec)
    case 'rust': return rust(spec)
  }
}

// ── Persisted choice (shared by both screens) ─────────────────────────────────

const LANG_KEY = 'stockai.codeLang'
const LEGACY_KEY = 'stockai.dev.codeLang'

export function loadSampleLang(): SampleLang {
  try {
    const saved = localStorage.getItem(LANG_KEY) ?? localStorage.getItem(LEGACY_KEY)
    if (isSampleLang(saved)) return saved
  } catch { /* storage blocked: cURL it is */ }
  return 'curl'
}

export function saveSampleLang(l: SampleLang) {
  try { localStorage.setItem(LANG_KEY, l) } catch { /* per-visitor nicety only */ }
}

/** Which highlighter grammar a sample language uses. */
export function highlightLangOf(lang: SampleLang): 'bash' | 'js' | 'python' | 'clike' {
  return lang === 'curl' ? 'bash' : lang === 'js' ? 'js' : lang === 'python' ? 'python' : 'clike'
}

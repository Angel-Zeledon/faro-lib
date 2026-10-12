// Fails when a public landing route is missing from one of the places a landing
// route must be listed. Run: npm run check:landing-routes
//
// Why: a landing page is wired into four lists that nothing links together,
// and forgetting one fails quietly. Missing from the sitemap, it is never
// indexed. Missing from robots.ts, the blanket `Disallow: /` hides it. Missing
// from deploy/Caddyfile.split's `@landing` matcher, the landing domain
// redirects it to the app host's sign-in. Missing from the page files, it 404s.
//
// The route list is read from the same plain modules the app reads
// (subpagePaths.ts, legalPaths.ts, contentPaths.ts), as text, so this script
// needs no TypeScript runtime. Those modules write every path as a quoted
// literal for exactly that reason.
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = join(fileURLToPath(new URL('.', import.meta.url)), '..')
const SRC = join(FRONTEND, 'src')
const read = p => readFileSync(p, 'utf8')

// Routes that are public but not defined in one of the three path modules.
const EXTRA = ['/desarrolladores', '/docs']

const MODULES = ['subpagePaths.ts', 'legalPaths.ts', 'contentPaths.ts']
const PATH_RE = /(?::|=)\s*'(\/[a-z0-9][a-z0-9\-/]*)'/g

const routes = new Set(['/', ...EXTRA])
for (const m of MODULES) {
  const text = read(join(SRC, 'components', 'landing', m))
  for (const hit of text.matchAll(PATH_RE)) routes.add(hit[1])
}

const problems = []

// 1. Each route has a page file (an industry page may be the dynamic [slug]).
for (const r of routes) {
  if (r === '/') continue
  const parts = r.split('/').filter(Boolean)
  const direct = join(SRC, 'app', ...parts, 'page.tsx')
  const dynamic = parts.length === 2 ? join(SRC, 'app', parts[0], '[slug]', 'page.tsx') : null
  if (!existsSync(direct) && !(dynamic && existsSync(dynamic))) {
    problems.push(`${r}: no page file (expected src/app${r}/page.tsx)`)
  }
}

// 2. Each industry path ends in its slug.
const content = read(join(SRC, 'components', 'landing', 'contentPaths.ts'))
const slugs = Object.fromEntries([...content.matchAll(/^\s*(\w+):\s*'([a-z0-9-]+)',?$/gm)].map(m => [m[1], m[2]]))
const industryPaths = Object.fromEntries([...content.matchAll(/^\s*(\w+):\s*'(\/industrias\/[a-z0-9-]+)',?$/gm)].map(m => [m[1], m[2]]))
for (const [key, path] of Object.entries(industryPaths)) {
  if (slugs[key] && !path.endsWith(`/${slugs[key]}`)) problems.push(`${path}: does not end in its slug "${slugs[key]}" (INDUSTRY_SLUGS.${key})`)
}

// 3. Sitemap, robots and the Caddy matcher name every route.
const sitemap = read(join(SRC, 'app', 'sitemap.ts'))
const robots = read(join(SRC, 'app', 'robots.ts'))
const caddy = read(join(FRONTEND, '..', 'deploy', 'Caddyfile.split'))
const matcher = (caddy.split(/\r?\n/).find(l => /^\s*@landing\s+path\s/.test(l)) ?? '').trim().split(/\s+/).slice(2)
if (matcher.length === 0) problems.push('deploy/Caddyfile.split: no "@landing path ..." matcher found')

for (const r of routes) {
  if (r === '/') continue
  // /docs and its tree are listed by DOC_ORDER in the sitemap; the root is enough.
  if (!sitemap.includes('${SITE_URL}' + r + '`')) problems.push(`${r}: missing from src/app/sitemap.ts`)
  if (!robots.includes(`'${r}$'`) && !(r === '/docs' && robots.includes("'/docs'"))) problems.push(`${r}: missing from src/app/robots.ts allow-list`)
  if (!matcher.includes(r)) problems.push(`${r}: missing from the @landing matcher of deploy/Caddyfile.split`)
}

// 4. The app shell and the auth guard read the path modules, so a new module
// would have to be added to both.
for (const f of ['AuthGuard.tsx', 'ConditionalShell.tsx']) {
  const text = read(join(SRC, 'components', 'layout', f))
  for (const imp of ['subpagePaths', 'legalPaths', 'contentPaths']) {
    if (!text.includes(`landing/${imp}`)) problems.push(`components/layout/${f}: does not read landing/${imp}`)
  }
}

// 5. The English site (components/landing/sitePaths.ts). Every Spanish page has
// an English counterpart under the same key, every English path is unique and
// under /en, and each one is wired into the same places as the Spanish pages.
const sitePaths = read(join(SRC, 'components', 'landing', 'sitePaths.ts'))
const block = (text, name) => {
  const m = text.match(new RegExp(`export const ${name}\\b[^=]*=\\s*\\{([\\s\\S]*?)\\n\\}`))
  if (!m) return null
  return Object.fromEntries([...m[1].matchAll(/^\s*'?([\w/-]+)'?:\s*'([^']+)',?\r?$/gm)].map(x => [x[1], x[2]]))
}
const scalar = (text, name) => (text.match(new RegExp(`export const ${name}\\s*=\\s*'([^']+)'`)) || [])[1] ?? null
const sources = {
  subpage: read(join(SRC, 'components', 'landing', 'subpagePaths.ts')),
  legal: read(join(SRC, 'components', 'landing', 'legalPaths.ts')),
  content,
  tree: read(join(SRC, 'i18n', 'docs', 'tree.ts')),
}
const pairs = [['/', scalar(sitePaths, 'EN_HOME_PATH')]]
for (const [esName, enName, src] of [
  ['SUBPAGE_PATHS', 'SUBPAGE_PATHS_EN', sources.subpage],
  ['INDUSTRY_PATHS', 'INDUSTRY_PATHS_EN', sources.content],
  ['CONTENT_PATHS', 'CONTENT_PATHS_EN', sources.content],
  ['GUIDE_PATHS', 'GUIDE_PATHS_EN', sources.content],
  ['LEGAL_PATHS', 'LEGAL_PATHS_EN', sources.legal],
]) {
  const es = block(src, esName)
  const en = block(sitePaths, enName)
  if (!es || !en) { problems.push(`sitePaths.ts: could not read ${esName} / ${enName}`); continue }
  for (const k of Object.keys(es)) {
    if (!en[k]) problems.push(`${es[k]}: no English counterpart (${enName}.${k} missing)`)
    else pairs.push([es[k], en[k]])
  }
  for (const k of Object.keys(en)) if (!es[k]) problems.push(`${en[k]}: ${enName}.${k} has no Spanish page`)
}
for (const [esName, enName, src] of [
  ['DEVELOPERS_PATH', 'DEVELOPERS_PATH_EN', sitePaths],
  ['INDUSTRIES_HUB_PATH', 'INDUSTRIES_HUB_PATH_EN', sources.content],
  ['LEGAL_HUB_PATH', 'LEGAL_HUB_PATH_EN', sources.legal],
  ['DOCS_BASE', 'DOCS_BASE_EN', sources.tree],
]) {
  const es = scalar(src, esName)
  const en = scalar(sitePaths, enName)
  if (!es || !en) problems.push(`sitePaths.ts: could not read ${esName} / ${enName}`)
  else pairs.push([es, en])
}
const enRoutes = pairs.map(([, en]) => en).filter(Boolean)

// Industry slugs in the English map end their paths, like the Spanish ones.
const enSlugs = block(sitePaths, 'INDUSTRY_SLUGS_EN') ?? {}
const enIndustry = block(sitePaths, 'INDUSTRY_PATHS_EN') ?? {}
for (const [k, path] of Object.entries(enIndustry)) {
  if (!enSlugs[k] || !path.endsWith(`/${enSlugs[k]}`)) problems.push(`${path}: does not end in its slug (INDUSTRY_SLUGS_EN.${k})`)
}

// Every Spanish public route is paired, nothing more and nothing less.
const pairedEs = new Set(pairs.map(([es]) => es))
for (const r of routes) if (!pairedEs.has(r)) problems.push(`${r}: no English counterpart in sitePaths.ts`)

// English paths: under /en, unique, never a Spanish path.
const seen = new Set()
for (const r of enRoutes) {
  if (r !== '/en' && !r.startsWith('/en/')) problems.push(`${r}: an English path must start with /en`)
  if (seen.has(r)) problems.push(`${r}: English path used twice`)
  if (routes.has(r)) problems.push(`${r}: English path collides with a Spanish one`)
  seen.add(r)
}

// Help-center ids: one English id per Spanish page, all different.
const docIds = block(sitePaths, 'DOC_IDS_EN') ?? {}
const treePages = [...sources.tree.matchAll(/slug:\s*'([a-z-]+)',\s*\n\s*pages:\s*\[([^\]]*)\]/g)]
  .flatMap(m => {
    const pages = [...m[2].matchAll(/'([a-z0-9-]+)'/g)].map(x => x[1])
    return pages.length ? pages.map(p => `${m[1]}/${p}`) : [m[1]]
  })
if (treePages.length === 0) problems.push('i18n/docs/tree.ts: could not read DOC_TREE')
const enDocSeen = new Set()
for (const id of treePages) {
  const en = docIds[id]
  if (!en) { problems.push(`/docs/${id}: no English id in DOC_IDS_EN`); continue }
  if (!/^[a-z0-9-]+(\/[a-z0-9-]+)?$/.test(en)) problems.push(`/en/docs/${en}: not a valid slug`)
  if (enDocSeen.has(en)) problems.push(`/en/docs/${en}: English help id used twice`)
  enDocSeen.add(en)
}
for (const id of Object.keys(docIds)) if (!treePages.includes(id)) problems.push(`DOC_IDS_EN['${id}']: not a page of DOC_TREE`)

// Page files for the English routes (an industry may be the dynamic [slug]).
for (const r of enRoutes) {
  const parts = r.split('/').filter(Boolean)
  const direct = join(SRC, 'app', ...parts, 'page.tsx')
  const dynamic = parts.length === 3 ? join(SRC, 'app', parts[0], parts[1], '[slug]', 'page.tsx') : null
  if (!existsSync(direct) && !(dynamic && existsSync(dynamic))) problems.push(`${r}: no page file (expected src/app${r}/page.tsx)`)
}
if (!existsSync(join(SRC, 'app', 'en', 'docs', '[...slug]', 'page.tsx'))) problems.push('/en/docs/*: no page file (src/app/en/docs/[...slug]/page.tsx)')

// The sitemap derives each English entry from its Spanish one (checked above).
if (!sitemap.includes('alternatesOf(')) problems.push('src/app/sitemap.ts: does not list the English pages (alternatesOf)')

// robots.ts and the Caddy matcher name every English page; /en/docs is a prefix.
for (const r of enRoutes) {
  if (r === '/en/docs') {
    if (!robots.includes("'/en/docs'")) problems.push('/en/docs: missing from src/app/robots.ts allow-list')
    if (!matcher.includes('/en/docs/*')) problems.push('/en/docs/*: missing from the @landing matcher of deploy/Caddyfile.split')
  } else if (!robots.includes(`'${r}$'`)) problems.push(`${r}: missing from src/app/robots.ts allow-list`)
  if (!matcher.includes(r)) problems.push(`${r}: missing from the @landing matcher of deploy/Caddyfile.split`)
}
if (!matcher.includes('/en/')) problems.push('/en/: missing from the @landing matcher (it would be sent to the app domain instead of 308 to /en)')

// The middleware that renders <html lang="en"> runs on /en and below.
const middleware = read(join(SRC, 'middleware.ts'))
if (!middleware.includes("'/en'") || !middleware.includes("'/en/:path*'")) problems.push('src/middleware.ts: matcher must cover /en and /en/:path*')

// The shell and the auth guard know the English pages are public.
for (const f of ['AuthGuard.tsx', 'ConditionalShell.tsx']) {
  if (!read(join(SRC, 'components', 'layout', f)).includes('landing/sitePaths')) problems.push(`components/layout/${f}: does not read landing/sitePaths`)
}

if (problems.length) {
  console.error(`check-landing-routes: ${problems.length} problem(s)`)
  for (const p of problems) console.error(`  ${p}`)
  process.exit(1)
}
console.log(`check-landing-routes: ${routes.size} Spanish and ${enRoutes.length} English public routes (+ ${treePages.length} help pages each), all listed everywhere`)

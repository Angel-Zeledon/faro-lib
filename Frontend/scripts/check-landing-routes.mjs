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

if (problems.length) {
  console.error(`check-landing-routes: ${problems.length} problem(s)`)
  for (const p of problems) console.error(`  ${p}`)
  process.exit(1)
}
console.log(`check-landing-routes: ${routes.size} public routes, all listed everywhere`)

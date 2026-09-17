/**
 * Critical-path browser suite: the two flows nobody had walked with a script.
 *
 * `smoke.mjs` covers layout/rendering regressions but logs in by injecting a
 * token directly (`fetch('/api/auth/login')`) — it never drives the login
 * FORM, and it never touches the upload wizard at all. Those are exactly the
 * two paths `docs/stability.md` names as the most expensive to re-walk by
 * hand after every change: signing in, and getting a sales file through
 * ingestion into the column-mapping step.
 *
 * One fresh @faro-e2e.io account carries the whole run (no MX on that
 * domain, nothing real is ever reached) — not `demo@faro.app`. Two reasons:
 * it never touches the seeded demo tenant's data, and `POST /auth/login`
 * rate-limits at 5 attempts / 5 minutes per email (backend/api/v1/auth.py:285)
 * — a shared account meant two runs back to back could 429 each other's
 * login check. A new email each run starts its own limiter window.
 *
 * The tenant this creates is deleted at the end via
 * `backend.scripts.cleanup_e2e_tenants` (best-effort — a cleanup failure
 * does not fail the suite). Run that script by hand to sweep any tenant left
 * behind by an interrupted run.
 *
 * Run:  node tests/critical_flows.mjs   (needs the app up on :5000 and the
 *       backend port from .env.local; a live Postgres)
 * Exit: non-zero on the first real failure.
 *
 * Written to FAIL, not to pass — see backend/tests/README.md before trusting
 * a change here: break the thing it watches and confirm the line goes red.
 */
import { chromium } from 'playwright'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '../..')
const BASE = process.env.SMOKE_BASE || 'http://localhost:5000'
const SAMPLE_CSV = path.resolve(__dirname, '../../scripts/sample_sales.csv')

const results = []
let currentGroup = ''
const group = (name) => { currentGroup = name }
function check(ok, what, detail = '') {
  results.push({ ok: Boolean(ok), group: currentGroup, what, detail })
  const mark = ok ? 'ok  ' : 'FAIL'
  console.log(`${mark} ${currentGroup} :: ${what}${detail ? `  (${detail})` : ''}`)
}

/**
 * `next dev` compiles each route on first hit and only attaches React's event
 * handlers after hydration. A click that lands before that attaches falls
 * through to the browser's native HTML form submission — a full-page GET to
 * `?email=...&password=...`, which both leaks the password into the URL/
 * history and wipes the in-memory state the rest of the flow depends on.
 * `smoke.mjs`'s `visit()` documents the same cold-compile trap on app routes.
 * Detect the fallback by its signature (a `?` appended to the same path) and
 * recover by reloading and retrying once, rather than either hiding it behind
 * a longer fixed sleep or failing the whole suite on a dev-server timing
 * artifact that a production build would not have.
 */
async function submitFormSafely(page, formPath) {
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.click('button[type="submit"]')
    await page.waitForTimeout(1200)
    if (!page.url().includes(`${formPath}?`)) return true
    if (attempt === 0) {
      console.log(`  (native GET fallback on ${formPath} — reloading and retrying once)`)
      await page.goto(`${BASE}${formPath}`, { waitUntil: 'domcontentloaded', timeout: 180000 })
      await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
      await page.waitForTimeout(1500)
      return false // caller must refill the form before calling again
    }
  }
  return false
}

async function gotoAndSettle(page, urlPath, selector, state = 'visible') {
  await page.goto(`${BASE}${urlPath}`, { waitUntil: 'domcontentloaded', timeout: 180000 })
  await page.waitForSelector(selector, { timeout: 30000, state })
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(800)
}

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page = await ctx.newPage()
const consoleErrors = []
page.on('pageerror', e => consoleErrors.push(String(e).slice(0, 160)))

const stamp = Date.now()
const email = `e2e-${stamp}@faro-e2e.io`
const password = 'E2eTest!2026'
// whatsapp_number is unique per account (found running this script twice in
// a row: the second run 409'd on a reused literal) — derive one per stamp.
const whatsapp = `+506${String(stamp).slice(-8)}`

// ── 1. Sign up and verify the one account this run uses throughout ─────────────
{
  group('signup')
  await gotoAndSettle(page, '/signup', '#signup-full-name')
  async function fillSignup() {
    await page.fill('#signup-full-name', 'E2E Critical Flow')
    await page.fill('#signup-company', `E2E Co ${stamp}`)
    await page.fill('#signup-email', email)
    await page.fill('#signup-whatsapp', whatsapp)
    await page.fill('#signup-password', password)
  }
  await fillSignup()
  let submitted = await submitFormSafely(page, '/signup')
  if (!submitted) { await fillSignup(); submitted = await submitFormSafely(page, '/signup') }
  await page.waitForTimeout(1000)

  const verifyHref = await page.locator('a[href*="/verify-email"]').first()
    .getAttribute('href').catch(() => null)
  check(Boolean(verifyHref), 'signup returns an on-screen verification link',
        verifyHref ?? 'none — check RESEND_API_KEY / whatsapp collision')

  if (verifyHref) {
    await page.goto(verifyHref, { waitUntil: 'domcontentloaded', timeout: 180000 })
    await page.waitForTimeout(1500)
  }
}

// ── 2. Login form: right password gets in, wrong password does not ────────────
let loggedIn = false
{
  group('login form')
  await gotoAndSettle(page, '/login', '#login-email')
  await page.fill('#login-email', email)
  await page.fill('#login-password', 'not-the-real-password')
  let submitted = await submitFormSafely(page, '/login')
  if (!submitted) {
    await page.fill('#login-email', email)
    await page.fill('#login-password', 'not-the-real-password')
    submitted = await submitFormSafely(page, '/login')
  }
  check(page.url().includes('/login') && !page.url().includes('?'),
        'a wrong password does not navigate away', page.url())
  // Poll rather than a one-shot check: the backend's first request after a
  // fresh restart pays connection-pool/import warmup that can outlast a
  // fixed sleep, and that must not read as a UI defect.
  const errorShown = await page.waitForSelector('svg.lucide-triangle-alert', { timeout: 8000 })
    .then(() => true).catch(() => false)
  check(errorShown, 'a wrong password shows an error message')

  // `/hoy` is a permanent redirect to `/compras` (next.config.mjs
  // `redirects()`), kept for links already shared with users — landing on
  // /compras after a successful login is correct, not a miss.
  await page.fill('#login-password', password)
  await submitFormSafely(page, '/login')
  await page.waitForURL('**/compras', { timeout: 15000 }).catch(() => {})
  loggedIn = page.url().includes('/compras')
  check(loggedIn, 'the correct password logs in', page.url())
  const token = await page.evaluate(() => localStorage.getItem('fp_access_token'))
  check(Boolean(token), 'a real access token is stored after login')
}

// ── 3. Upload a sales CSV ───────────────────────────────────────────────────────
if (loggedIn) {
  group('upload')
  await gotoAndSettle(page, '/ventas', 'input[type="file"]', 'attached')
  await page.setInputFiles('input[type="file"]', SAMPLE_CSV)

  // Upload -> inspect -> column mapping is a real round trip through the
  // backend; give it real time before deciding it failed.
  const mappingAppeared = await page.waitForSelector('select', { timeout: 60000 })
    .then(() => true).catch(() => false)
  check(mappingAppeared, 'uploading a CSV reaches the column-mapping step')
  check(consoleErrors.length === 0, 'no uncaught page errors during upload',
        consoleErrors.join(' | '))
}

await browser.close()

// ── Cleanup: delete the tenant this run created ─────────────────────────────────
try {
  const pythonExe = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe')
  await execFileAsync(pythonExe, ['-m', 'backend.scripts.cleanup_e2e_tenants'], { cwd: REPO_ROOT })
  console.log('\n(cleaned up this run\'s @faro-e2e.io tenant)')
} catch (e) {
  console.log(`\n(cleanup skipped: ${String(e).slice(0, 200)} — run ` +
    `"backend/.venv/Scripts/python.exe -m backend.scripts.cleanup_e2e_tenants" by hand)`)
}

// ── Report ───────────────────────────────────────────────────────────────────
const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
if (failed.length) {
  console.log('\nFAILED:')
  for (const f of failed) console.log(`  ${f.group} :: ${f.what} ${f.detail}`)
}
process.exit(failed.length ? 1 : 0)

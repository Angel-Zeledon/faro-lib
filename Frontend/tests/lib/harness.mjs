/**
 * Shared plumbing for the browser suites.
 *
 * `smoke.mjs`, `critical_flows.mjs` and `virgin_screens.mjs` each grew their
 * own copy of `check`/`group`/`visit`/`login`, and the copies had already
 * drifted — one waits on `.page-content`, another on a body-length threshold,
 * a third on neither. When a screen is slow the three disagree about whether
 * that is a defect, which is the worst possible answer from a test. This is
 * the one implementation; the older files still carry their own and can move
 * over one at a time, since a blind rewrite of three working suites buys
 * nothing.
 *
 * Nothing here asserts. It waits, it reports, and it counts — the assertions
 * belong in the suite that knows what it is looking at.
 */
import { chromium } from 'playwright'

export const BASE = process.env.SMOKE_BASE || 'http://localhost:5000'
export const EMAIL = process.env.SMOKE_EMAIL || 'demo@faro.app'
export const PASSWORD = process.env.SMOKE_PASSWORD || 'demo1234'

// ── Reporting ────────────────────────────────────────────────────────────────

export function createReporter() {
  const results = []
  let currentGroup = ''

  const group = (name) => { currentGroup = name }

  function check(ok, what, detail = '') {
    results.push({ ok: Boolean(ok), group: currentGroup, what, detail })
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${currentGroup} :: ${what}` +
                `${detail ? `  (${detail})` : ''}`)
  }

  /** Prints the summary and returns the exit code, so the caller decides when
   *  to leave — a suite may still have a browser or a tenant to clean up. */
  function report() {
    const failed = results.filter(r => !r.ok)
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
    if (failed.length) {
      console.log('\nFAILED:')
      for (const f of failed) console.log(`  ${f.group} :: ${f.what} ${f.detail}`)
    }
    return failed.length ? 1 : 0
  }

  return { group, check, report, results }
}

// ── Navigation ───────────────────────────────────────────────────────────────

/**
 * Go to a route and wait for the shell to actually be there.
 *
 * A fixed timeout is a flake generator against `next dev`, which compiles each
 * route on its first hit — an early run reported three screens as empty and
 * every one was a cold compile, not a defect. Wait on the DOM, not the clock.
 *
 * The `Escape` at the end dismisses the welcome tour, which fires in any
 * browser context with an empty localStorage — i.e. every run of every script,
 * and never for a real user who closed it once.
 */
export async function visit(page, route, { settle = 1200 } = {}) {
  await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 180000 })
  await page.waitForSelector('.page-content', { timeout: 120000 })
  await page.waitForFunction(
    () => (document.body.innerText || '').trim().length > 200, null, { timeout: 120000 },
  ).catch(() => {})   // a legitimately sparse screen is the suite's call, not ours
  await page.waitForTimeout(settle)
  await page.keyboard.press('Escape').catch(() => {})
  await page.waitForTimeout(250)
}

/**
 * Sign in by putting a real token where the app keeps it.
 *
 * Deliberately not the login FORM: `critical_flows.mjs` owns that path, and
 * `POST /auth/login` rate-limits at 5 attempts per 5 minutes per email
 * (`backend/api/v1/auth.py`), so a suite that walks 20+ screens must not spend
 * that budget on getting in.
 */
export async function login(page, { email = EMAIL, password = PASSWORD, lang = 'es' } = {}) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 180000 })
  const ok = await page.evaluate(async ([email, password, lang]) => {
    localStorage.setItem('theme', 'light')
    localStorage.setItem('lang', lang)
    const r = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!r.ok) return false
    const d = (await r.json()).data
    localStorage.setItem('fp_access_token', d.access_token)
    if (d.refresh_token) sessionStorage.setItem('fp_refresh_token', d.refresh_token)
    localStorage.setItem('fp_user', JSON.stringify(d.user))
    return true
  }, [email, password, lang])
  if (!ok) throw new Error(`could not log in as ${email} — is the backend up?`)
}

export async function newBrowser({ width = 1440, height = 900 } = {}) {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width, height } })
  const page = await ctx.newPage()
  return { browser, ctx, page }
}

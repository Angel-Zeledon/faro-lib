/**
 * Browser smoke suite: the class of defect the Python suite cannot see.
 *
 * Every assertion here corresponds to a bug that shipped and survived a green
 * 1,944-test run, because none of those tests ever asked whether a person could
 * use the screen:
 *
 *   · the landing could not be scrolled AT ALL — `body { overflow: hidden }`
 *     propagated to the viewport and the wheel did nothing, since the initial
 *     commit
 *   · the chat panels collapsed to their content instead of filling the viewport,
 *     because `height: 100%` resolved against a wrapper with no height
 *   · opening the contact picker pushed the conversation list down 220px, so a
 *     click aimed at a name landed on whichever row slid into that spot
 *   · a column tooltip was clipped by its table card's `overflow: hidden`
 *   · the historical line rendered in the forecast's own green, because a CSS
 *     custom property is not a colour a canvas can resolve
 *
 * Run:  node tests/smoke.mjs          (needs the app up on :5000 and :8010)
 * Exit: non-zero on the first real failure, so it is usable as a gate.
 *
 * Written to FAIL, not to pass. Before trusting a change to this file, break the
 * thing it watches and confirm the line goes red — see backend/tests/README.md.
 */
import { chromium } from 'playwright'

const BASE = process.env.SMOKE_BASE || 'http://localhost:5000'
const EMAIL = process.env.SMOKE_EMAIL || 'demo@faro.app'
const PASSWORD = process.env.SMOKE_PASSWORD || 'demo1234'

const results = []
let currentGroup = ''

const group = (name) => { currentGroup = name }
function check(ok, what, detail = '') {
  results.push({ ok: Boolean(ok), group: currentGroup, what, detail })
  const mark = ok ? 'ok  ' : 'FAIL'
  console.log(`${mark} ${currentGroup} :: ${what}${detail ? `  (${detail})` : ''}`)
}

/**
 * Go to a route and wait for the shell to actually be there.
 *
 * A fixed timeout is a flake generator against `next dev`, which compiles each
 * route on its first hit: the first run reported /mensajes as empty three times
 * over, and it was a cold compile, not a defect. Waiting on the DOM instead of
 * the clock is both faster and honest.
 */
async function visit(page, route, { settle = 1500 } = {}) {
  await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 180000 })
  await page.waitForSelector('.page-content', { timeout: 120000 })
  await page.waitForFunction(
    () => (document.body.innerText || '').trim().length > 200, null, { timeout: 120000 })
  await page.waitForTimeout(settle)
  await page.keyboard.press('Escape').catch(() => {})
  await page.waitForTimeout(300)
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 180000 })
  const ok = await page.evaluate(async ([email, password]) => {
    localStorage.setItem('theme', 'light')
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
  }, [EMAIL, PASSWORD])
  if (!ok) throw new Error(`could not log in as ${EMAIL} — is the backend up?`)
}

const browser = await chromium.launch()

// ── 1. The landing scrolls ────────────────────────────────────────────────────
// It did not, for months. `scrollTo` worked, which is why nothing noticed: only
// the WHEEL was dead, and only a person uses the wheel.
{
  group('landing')
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 180000 })
  await page.waitForTimeout(3500)

  const tall = await page.evaluate(() =>
    document.documentElement.scrollHeight > window.innerHeight + 200)
  check(tall, 'page is taller than the viewport')

  await page.mouse.move(720, 450)
  await page.mouse.wheel(0, 1200)
  await page.waitForTimeout(700)
  const y = await page.evaluate(() => Math.round(window.scrollY))
  check(y > 500, 'the wheel actually scrolls it', `scrollY=${y}`)

  // Reveal-on-scroll must not leave anything permanently invisible.
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await page.waitForTimeout(2500)
  const hidden = await page.evaluate(() =>
    [...document.querySelectorAll('[data-reveal]')]
      .filter(el => getComputedStyle(el).opacity !== '1').length)
  check(hidden === 0, 'nothing is left invisible after scrolling to the bottom',
        `${hidden} hidden`)
  await page.close()
}

// ── 2. Nothing overflows sideways on a phone ──────────────────────────────────
{
  group('mobile')
  for (const width of [360, 390, 414]) {
    const ctx = await browser.newContext({
      viewport: { width, height: 780 }, isMobile: true, hasTouch: true,
    })
    const page = await ctx.newPage()
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 180000 })
    await page.waitForTimeout(3000)
    const over = await page.evaluate(() =>
      document.documentElement.scrollWidth - window.innerWidth)
    check(over <= 1, `no horizontal overflow at ${width}px`, `${over}px`)

    // Below 900px the nav link row is hidden, and for months nothing replaced
    // it — a phone had no way to reach any section of a 22,000px page.
    if (width < 900) {
      const burger = await page.locator('.nav-burger').isVisible().catch(() => false)
      check(burger, `a menu button exists at ${width}px`)
    }
    await ctx.close()
  }
}

// ── 3. App screens: full height, no clipping, no console errors ───────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 900 } })
  const page = await ctx.newPage()
  const consoleErrors = []
  page.on('pageerror', e => consoleErrors.push(String(e).slice(0, 120)))
  await login(page)

  group('shell')
  const ROUTES = ['/compras', '/pedidos', '/mensajes', '/inventario', '/proveedores',
                  '/pronosticos', '/asistente', '/mi-cuenta']
  for (const route of ROUTES) {
    const before = consoleErrors.length
    await visit(page, route, { settle: route === '/pronosticos' ? 8000 : 2000 })

    const m = await page.evaluate(() => {
      const sc = document.querySelector('.page-content')
      if (!sc) return null
      // Content taller than the box must be REACHABLE, not clipped.
      sc.scrollTop = sc.scrollHeight
      return {
        overflowX: sc.scrollWidth - sc.clientWidth,
        bottomReached: Math.round(sc.scrollTop + sc.clientHeight) >= sc.scrollHeight - 2,
        text: document.body.innerText.trim().length,
      }
    })
    check(m && m.text > 200, `${route} rendered something`)
    check(m && m.overflowX <= 3, `${route} does not overflow sideways`,
          m ? `${m.overflowX}px` : '')
    check(m && m.bottomReached, `${route} can be scrolled to its own bottom`)
    check(consoleErrors.length === before, `${route} logged no page errors`,
          consoleErrors.slice(before).join(' | '))
  }

  // The two chat screens must FILL the viewport. Both used to collapse to their
  // message list, so two messages left the composer floating mid-page.
  group('chat fills the viewport')
  for (const route of ['/mensajes', '/asistente']) {
    await visit(page, route, { settle: 3000 })
    const m = await page.evaluate(() => {
      const sc = document.querySelector('.page-content')
      const enter = document.querySelector('.page-enter')
      return {
        box: sc ? sc.clientHeight : 0,
        filled: enter ? Math.round(enter.getBoundingClientRect().height) : 0,
      }
    })
    // The floor matters as much as the comparison. `filled >= box - 60` is
    // trivially true when both are 0, and on the first run of this file that is
    // exactly what happened: a route that had not rendered reported "fills its
    // container (0 of 0)" and passed. A check that green-lights an empty page is
    // one of the 295.
    check(m.box > 400, `${route} has a container to fill`, `box=${m.box}`)
    check(m.box > 400 && m.filled >= m.box - 60, `${route} fills its container`,
          `${m.filled} of ${m.box}`)
  }

  // Opening the contact search must not move the conversation rows. It used to
  // push them down 220px, so the click you aimed at a name hit another row.
  group('nothing moves under the cursor')
  await visit(page, '/mensajes', { settle: 3000 })
  const rowY = () => page.evaluate(() => {
    const b = [...document.querySelectorAll('button')]
      .filter(x => /\d{1,2} \w{3}|\d\d:\d\d/.test(x.innerText || ''))
    return b.length ? Math.round(b[0].getBoundingClientRect().y) : null
  })
  const yBefore = await rowY()
  await page.locator('input').first().fill('a')
  await page.waitForTimeout(900)
  const yAfter = await rowY()
  check(yBefore !== null && yBefore === yAfter,
        'conversation rows stay put while searching', `${yBefore} -> ${yAfter}`)

  // A tooltip must be inside the viewport. This one opened above a table header
  // inside a card with `overflow: hidden`, and was cut in half.
  group('tooltips are not clipped')
  await visit(page, '/proveedores', { settle: 3000 })
  const target = await page.evaluate(() => {
    const s = [...document.querySelectorAll('th span')].find(x => x.querySelector('svg'))
    if (!s) return null
    const r = s.getBoundingClientRect()
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
  })
  if (target) {
    await page.mouse.move(target.x, target.y)
    await page.waitForTimeout(800)
    const tip = await page.evaluate(() => {
      const t = document.querySelector('[role="tooltip"]')
      if (!t) return null
      const r = t.getBoundingClientRect()
      return {
        inside: r.top >= 0 && r.bottom <= window.innerHeight
                && r.left >= 0 && r.right <= window.innerWidth,
        rect: `${Math.round(r.top)}..${Math.round(r.bottom)}`,
      }
    })
    check(tip !== null, 'a column tooltip opens')
    check(tip && tip.inside, 'the tooltip is fully inside the viewport',
          tip ? tip.rect : '')
  } else {
    check(false, 'found a column heading with a tooltip to hover')
  }

  await ctx.close()
}

await browser.close()

// ── Report ───────────────────────────────────────────────────────────────────
const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
if (failed.length) {
  console.log('\nFAILED:')
  for (const f of failed) console.log(`  ${f.group} :: ${f.what} ${f.detail}`)
}
process.exit(failed.length ? 1 : 0)

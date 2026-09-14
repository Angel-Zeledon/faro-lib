/**
 * Every screen of a VIRGIN install: no data, no credentials, nobody else.
 *
 * The state a buyer sees in their first ten minutes and the one the app is
 * never developed in. A screen may legitimately be empty here; what it may not
 * do is throw, render a raw i18n key, spin forever, or answer 5xx — an empty
 * account is not an error condition.
 *
 * Run against a backend pointed at an empty database with only SECRET_KEY,
 * DATABASE_URL and FRONTEND_URL set:
 *   node tests/virgin_screens.mjs
 */
import { chromium } from 'playwright'

const BASE = process.env.SMOKE_BASE || 'http://localhost:5000'
const EMAIL = process.env.VIRGIN_EMAIL
const PASSWORD = process.env.VIRGIN_PASSWORD || 'StrongPass123!'

if (!EMAIL) {
  console.error('VIRGIN_EMAIL is required (the sole tenant admin of the empty install)')
  process.exit(2)
}

// Every route in the sidebar, plus the two that are reachable but unlisted.
const SCREENS = [
  '/compras', '/pedidos', '/mensajes',
  '/ventas', '/archivos',
  '/inventario', '/configurar-inventario', '/proveedores', '/proveedores/scorecard',
  '/pronosticos', '/impacto', '/historial', '/asistente', '/escenarios',
  '/usuarios', '/mi-cuenta', '/automatizacion', '/api', '/instalacion',
  '/integraciones',
]

const problems = []
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } })
const page = await ctx.newPage()

const seen = { fiveXX: [], consoleErrors: [] }
page.on('response', r => {
  if (r.url().includes('/api/') && r.status() >= 500) {
    seen.fiveXX.push(`${r.status()} ${r.url().split('/api')[1]}`)
  }
})
page.on('console', m => {
  if (m.type() !== 'error') return
  const t = m.text()
  // Known and already recorded in docs/inventario-pantallas.md: a hydration
  // warning on <html data-theme>, and an RSC prefetch artifact of hard
  // navigation, which a real user clicking <Link> never produces.
  if (t.includes('data-theme') || t.includes('RSC payload')) return
  seen.consoleErrors.push(t.slice(0, 160))
})

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill('input[type="email"]', EMAIL)
await page.fill('input[type="password"]', PASSWORD)
await page.click('button[type="submit"]')
await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 30000 })
console.log('signed in as the only company on an empty install\n')

for (const route of SCREENS) {
  seen.fiveXX.length = 0
  seen.consoleErrors.length = 0
  let text = ''
  try {
    await page.goto(BASE + route, { waitUntil: 'networkidle', timeout: 45000 })
    await page.waitForTimeout(3500)
    text = await page.locator('body').innerText()
  } catch (e) {
    problems.push(`${route}: did not load — ${String(e).slice(0, 120)}`)
    console.log(`  ${route.padEnd(26)} DID NOT LOAD`)
    continue
  }

  const flags = []
  if (/Application error|Unhandled Runtime|client-side exception/i.test(text)) {
    flags.push('renders a crash')
  }
  // A raw i18n key on screen: the failure that shipped Spanish key names to
  // buyers once already.
  const rawKeys = text.match(/\b(nav|ui|common|inventory|analyst|settings)\.[a-z_]+\.?[a-z_]*/gi)
  if (rawKeys) flags.push('raw i18n keys: ' + [...new Set(rawKeys)].slice(0, 3).join(', '))
  if (seen.fiveXX.length) flags.push('5xx: ' + seen.fiveXX.slice(0, 2).join(', '))
  if (seen.consoleErrors.length) flags.push('console: ' + seen.consoleErrors[0])
  // Nothing rendered at all is its own failure — an empty account still gets
  // chrome, a heading and an empty state.
  if (text.trim().length < 200) flags.push(`almost nothing rendered (${text.trim().length} chars)`)

  console.log(`  ${route.padEnd(26)} ${flags.length ? 'FLAGGED — ' + flags.join(' | ') : 'ok'}`)
  flags.forEach(f => problems.push(`${route}: ${f}`))
}

console.log('\n=== PROBLEMS: ' + problems.length + ' ===')
problems.forEach(p => console.log(' - ' + p))
await browser.close()
process.exit(problems.length ? 1 : 0)

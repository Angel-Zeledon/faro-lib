/**
 * Regression guard for the BASIC PATH — the walk a new, non-technical owner
 * makes: brand-new trial account -> upload sales -> "what do I order?" -> first
 * purchase order. It counts steps and visible controls per screen and fails
 * when a screen got busier than its versioned budget.
 *
 * Why it exists (docs/simplicity-audit.md section 4.3): nobody notices that a
 * block raised a screen from 12 to 20 controls until a customer complains.
 * There is no CI here (owner's rule), so this is run BY HAND before tagging a
 * version, like scripts/SMOKE.md.
 *
 * Needs a running stack, in the usual traps (see the running-stockai skill):
 *   · a backend with the TRAINING WORKER ON (WORKER_ENABLED=true) — the walk
 *     uploads a file and waits for a real forecast;
 *   · the frontend, whose URL goes in SMOKE_BASE (default http://localhost:5000);
 *   · NOT while scripts/run_tests.py runs (it refuses a live dev server), and
 *     never two heavy processes at once on a small machine.
 *
 *   node tests/basic_path.mjs                  # compare with the budget
 *   node tests/basic_path.mjs --write-budget   # (re)measure and store it
 *
 * The budget lives in tests/basic_path.budget.json and is part of the diff: a
 * deliberate increase edits that file in the same commit, so it is reviewed.
 * While `"baseline": true` the run only MEASURES (exit 0) — that is the state
 * of the first version, which recorded today's numbers before any budget was
 * set (audit 4.3 step 7); `--write-budget` without `--baseline` turns the
 * measured numbers into enforced limits.
 *
 * What is measured on every screen it visits, split chrome vs body:
 *   controls_chrome  visible, enabled button / a[href] / select / input /
 *                    [role=tab] OUTSIDE `.page-content` (sidebar, top bar,
 *                    tab bar)
 *   controls_body    the same INSIDE `.page-content`
 *   sections         visible h1 / h2 / [role=tabpanel] in the body
 *   jargon           occurrences of owner-hostile terms in the visible text
 *   raw_keys         strings that look like an unrendered i18n key
 * plus, for the whole walk: clicks and distinct screens.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { BASE, login, createReporter, newBrowser } from './lib/harness.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const BUDGET_FILE = path.join(HERE, 'basic_path.budget.json')
const SAMPLE_CSV = path.join(HERE, '..', '..', 'scripts', 'sample_sales.csv')
const WRITE = process.argv.includes('--write-budget')
const KEEP_BASELINE = process.argv.includes('--baseline')
const TRAIN_TIMEOUT_MS = Number(process.env.BASIC_PATH_TRAIN_MS || 12 * 60 * 1000)

// The glossary of audit section 6 (Spanish values the owner reads).
const JARGON = [/\bSKUs?\b/g, /\bMOQ\b/g, /\bWAPE\b/g, /backtest/gi, /lineage/gi, /granularidad/gi, /huella/gi, /\bhorizonte\b/gi]

const { group, check, report } = createReporter()
const measured = {}      // screen label -> metrics
let clicks = 0
const screensSeen = new Set()

const { browser, page } = await newBrowser({ width: 1280, height: 900 })
const errors5xx = []
page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 500) errors5xx.push(`${r.status()} ${r.url().split('/api')[1]}`) })

/** Measure the screen as the user sees it right now. */
async function measure(label) {
  const m = await page.evaluate(([jargon]) => {
    const visible = el => {
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
        && !el.closest('[aria-hidden="true"]') && !el.disabled
    }
    const SEL = 'button, a[href], select, input:not([type=hidden]), [role=tab]'
    const body = document.querySelector('.page-content')
    let chrome = 0, inBody = 0
    for (const el of document.querySelectorAll(SEL)) {
      if (!visible(el)) continue
      if (body && body.contains(el)) inBody++; else chrome++
    }
    const sections = body
      ? [...body.querySelectorAll('h1, h2, [role=tabpanel]')].filter(visible).length : 0
    const text = (document.body.innerText || '')
    const jargonHits = jargon.reduce((n, [src, flags]) => n + (text.match(new RegExp(src, flags)) || []).length, 0)
    const raw = text.match(/(^|\s)(nav|ui|common|inventory|analyst|settings|hoy|orders|suppliers|hub|users|sessions|scenarios|qs)\.[a-z_]+(\.[a-z_]+)?(?=\s|$)/g) || []
    return { controls_chrome: chrome, controls_body: inBody, sections, jargon: jargonHits, raw_keys: raw.length }
  }, [JARGON.map(r => [r.source, r.flags])])
  measured[label] = m
  screensSeen.add(new URL(page.url()).pathname)
  console.log(`   ${label.padEnd(24)} chrome ${String(m.controls_chrome).padStart(3)}  body ${String(m.controls_body).padStart(3)}  sections ${String(m.sections).padStart(2)}  jargon ${m.jargon}  raw ${m.raw_keys}`)
}

async function click(locator, what) {
  await locator.first().click({ timeout: 20000 })
  clicks++
  console.log(`   click: ${what}`)
}

async function settle(ms = 1500) {
  await page.waitForSelector('.page-content', { timeout: 180000 })
  await page.waitForTimeout(ms)
  await page.keyboard.press('Escape').catch(() => {})
  await page.waitForTimeout(250)
}

try {
  // ── 0. A brand-new account, the way the landing's "try it" makes one ───────
  group('account')
  const r = await fetch(`${BASE}/api/trial`, { method: 'POST' })
  check(r.ok, 'POST /trial creates a throwaway account', `HTTP ${r.status}`)
  const acct = (await r.json()).data
  await login(page, { email: acct.email, password: acct.password })

  // ── 1. First screen: nothing uploaded yet ───────────────────────────────────
  group('first screen')
  await page.goto(`${BASE}/compras`, { waitUntil: 'domcontentloaded', timeout: 400000 })
  await settle()
  const upload = page.getByRole('link', { name: /Subir mi historial/i })
    .or(page.getByRole('button', { name: /Subir mi historial/i }))
  await upload.first().waitFor({ timeout: 60000 }).catch(() => {})
  await measure('compras (empty)')
  check(await upload.count() > 0, 'the empty Panel offers "Subir mi historial" as its next step')

  // ── 2. Upload ───────────────────────────────────────────────────────────────
  group('upload')
  await click(upload, 'Subir mi historial')
  await page.waitForURL(/\/ventas/, { timeout: 400000 })   // first hit compiles the route under next dev
  await settle()
  await measure('ventas (step 1)')
  await page.setInputFiles('input[type=file]', SAMPLE_CSV)
  const proceed = page.getByRole('button', { name: /se ve bien, continuar|Continuar/i })
  await proceed.first().waitFor({ timeout: 120000 }).catch(() => {})
  await page.waitForTimeout(800)
  await measure('ventas (columns)')
  check(await proceed.count() > 0, 'the columns step offers a way forward')
  await click(proceed, 'Esto se ve bien, continuar')

  // ── 3. Wait for the forecast, then land on the Panel ────────────────────────
  group('forecast')
  const t0 = Date.now()
  let landed = false
  while (Date.now() - t0 < TRAIN_TIMEOUT_MS) {
    if (/\/(compras|hoy)(\/|$|\?)/.test(page.url())) { landed = true; break }
    await page.waitForTimeout(3000)
  }
  check(landed, 'training finishes and the app opens the Panel', `${Math.round((Date.now() - t0) / 1000)}s`)
  if (!landed) throw new Error('forecast did not finish; is the worker on?')
  await settle(4000)
  await measure('compras (with data)')

  // ── 4. First order: approve the first card, generate the PO ─────────────────
  group('first order')
  const approve = page.getByRole('button', { name: /^Aprobar/i })
  check(await approve.count() > 0, 'the Panel has at least one card to approve')
  if (await approve.count() > 0) {
    await click(approve, 'Aprobar (first card)')
    await page.waitForTimeout(800)
    await measure('compras (cart)')
    const generate = page.getByRole('button', { name: /Generar|Descargar orden de compra/i })
    check(await generate.count() > 0, 'approving a card offers "Generar orden"')
    if (await generate.count() > 0) {
      await click(generate, 'Generar orden')
      await page.waitForTimeout(2500)
      await measure('compras (order made)')
      const toOrders = page.getByRole('button', { name: /Ir a Pedidos/i }).or(page.getByRole('link', { name: /Ir a Pedidos/i }))
      if (await toOrders.count() > 0) await click(toOrders, 'Ir a Pedidos')
      else { await page.goto(`${BASE}/pedidos`, { waitUntil: 'domcontentloaded', timeout: 400000 }) }
      await settle()
      await measure('pedidos (after)')
      const rows = await page.locator('.page-content table tbody tr, .page-content [data-po-row]').count()
      check(rows > 0, '/pedidos lists the order that was just made', `${rows} rows`)
    }
  }
} catch (e) {
  check(false, 'walk completed', String(e.message || e).slice(0, 200))
} finally {
  await browser.close()
}

check(errors5xx.length === 0, 'no 5xx during the walk', errors5xx.slice(0, 3).join('; '))

// ── Budget ────────────────────────────────────────────────────────────────────
group('budget')
const summary = { clicks, screens: screensSeen.size }
console.log(`\nwalk: ${clicks} clicks, ${screensSeen.size} distinct screens`)

if (WRITE) {
  const out = {
    baseline: KEEP_BASELINE,
    note: 'Measured by tests/basic_path.mjs. While "baseline" is true the guard only reports. A deliberate increase edits this file in the same commit.',
    walk: summary,
    screens: measured,
  }
  fs.writeFileSync(BUDGET_FILE, JSON.stringify(out, null, 2) + '\n')
  console.log(`budget written to ${BUDGET_FILE} (baseline: ${KEEP_BASELINE})`)
} else if (!fs.existsSync(BUDGET_FILE)) {
  console.log('no budget file yet: run with --write-budget --baseline to record one')
} else {
  const budget = JSON.parse(fs.readFileSync(BUDGET_FILE, 'utf8'))
  const enforce = !budget.baseline
  console.log(`\n${'screen'.padEnd(24)} ${'metric'.padEnd(16)} measured  budget`)
  const over = (what, got, max) => {
    const bad = typeof max === 'number' && got > max
    if (bad || process.env.BASIC_PATH_VERBOSE) console.log(`${what.padEnd(41)} ${String(got).padStart(8)}  ${String(max).padStart(6)}${bad ? '  OVER' : ''}`)
    if (enforce) check(!bad, `${what} within budget`, bad ? `${got} > ${max}` : '')
    else if (bad) console.log('   (baseline mode: reported, not enforced)')
  }
  over('walk clicks', summary.clicks, budget.walk?.clicks)
  over('walk screens', summary.screens, budget.walk?.screens)
  for (const [label, m] of Object.entries(measured)) {
    const b = budget.screens?.[label]
    if (!b) { console.log(`   ${label}: not in the budget (new screen in the walk)`); continue }
    for (const k of Object.keys(m)) over(`${label} ${k}`, m[k], b[k])
  }
}

const code = report()
process.exit(code)

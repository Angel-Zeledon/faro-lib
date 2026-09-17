/**
 * Every authenticated screen, in both languages, checked for the things the
 * Python suite structurally cannot see.
 *
 * `docs/stability.md` was built by walking these screens BY HAND, 26 of them,
 * over and over, and the walk is where almost every real defect came from — a
 * green backend run has sat on top of 27 live defects before (2026-08-06).
 * `smoke.mjs` automates 8 screens and asks about layout; this asks the four
 * questions a hand walk answers on every screen it opens:
 *
 *   1. Does it render at all, or does it throw?
 *   2. Did any request behind it fail?
 *   3. Is it printing a raw i18n key at the user? `t()` echoes back a key that
 *      is in NEITHER language block, so a typo'd key puts
 *      `inventory.source_file` on screen — that has shipped to buyers.
 *   4. Is it printing `NaN`, `undefined`, `[object Object]` or `Infinity`?
 *      Every one is a calculation that failed quietly, and this product's
 *      whole claim is that its numbers mean something.
 *
 * Both languages, because a screen can render cleanly in one and break in the
 * other — a number formatted through a locale that is not loaded, a layout
 * that only overflows with the longer word.
 *
 * Read-only on purpose: it walks the seeded demo tenant and must not leave a
 * purchase order, a supplier or a session behind. Nothing here clicks a
 * mutating control.
 *
 * Run:  node tests/all_screens.mjs     (needs the app up on :5000 and the
 *       backend on the port `Frontend/.env.local` names)
 * Exit: non-zero if any check failed.
 *
 * Written to FAIL, not to pass: both content checks were confirmed against a
 * real mutation before this file was committed.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createReporter, visit, login, newBrowser, BASE } from './lib/harness.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const { group, check, report } = createReporter()

/** Every authenticated route with a page. Explicit rather than globbed: a new
 *  screen should have to be added here deliberately, so "is it covered?" has a
 *  written answer. */
const ALL_ROUTES = [
  '/compras', '/inventario', '/pedidos', '/pronosticos', '/proveedores',
  '/proveedores/scorecard', '/archivos', '/ventas', '/historial', '/impacto',
  '/escenarios', '/mi-cuenta', '/usuarios', '/actividad', '/asistente',
  '/mensajes', '/automatizacion', '/configurar-inventario', '/api',
  '/integraciones', '/instalacion',
]

// Narrowing hooks, for working on one screen instead of waiting out all 21 —
// and for the mutation check that proves this suite can go red at all:
//   SCREENS=pedidos LANGS=en node tests/all_screens.mjs
//
// The leading slash is optional, and that is not politeness: Git Bash rewrites
// a value starting with `/` into a Windows path, so `SCREENS=/pedidos` arrives
// as `C:/Program Files/Git/pedidos` and the run fails with an invalid URL
// rather than with anything about the screen.
const ROUTES = process.env.SCREENS
  ? process.env.SCREENS.split(',').map(r => `/${r.trim().replace(/^.*[/\\]/, '')}`)
  : ALL_ROUTES
const LANGS = process.env.LANGS ? process.env.LANGS.split(',') : ['es', 'en']

/**
 * The key prefixes the catalogue really uses.
 *
 * A bare `/\w+\.\w+/` would flag every filename and decimal on screen; keyed
 * to the real namespaces, a hit is a key the UI asked for and did not get.
 */
function catalogPrefixes() {
  const src = fs.readFileSync(
    path.resolve(__dirname, '../src/i18n/translations.ts'), 'utf8')
  const prefixes = new Set()
  for (const m of src.matchAll(/'([a-zA-Z][a-zA-Z0-9]*)\.[a-zA-Z0-9_]+'\s*:/g)) {
    prefixes.add(m[1])
  }
  return [...prefixes]
}

const PREFIXES = catalogPrefixes()

/** Text a user should never see. Each one is a calculation that gave up. */
const BROKEN_VALUES = ['NaN', 'undefined', '[object Object]', 'Infinity']

/**
 * NOT here: a check for Spanish leaking onto the English screen.
 *
 * It was written, run against a real mutation, and deleted because it could
 * not fail. A key missing from `en` does not render as a key —
 * `LanguageContext.t()` resolves `dict[k] ?? translations.es[k] ?? k`, so the
 * screen shows the SPANISH STRING. Two detectors were tried against the same
 * mutation (deleting `orders.page_subtitle` from the `en` block):
 *
 *  1. Diffing rendered text against the catalogue's `es` values — stayed
 *     green, because the detector builds its reference list FROM the
 *     catalogue, so the mutation removed the string from both sides at once.
 *  2. Spanish function words in the rendered text — stayed green, because
 *     "Órdenes generadas y registro de llegadas" carries none of them.
 *
 * `check_parity.py` catches that same mutation in under a second and names the
 * key. The static check is simply better at this, so the job stays there and
 * the runner calls it first. A heuristic that cannot fail is worse than no
 * check, because it reads like coverage.
 */

async function scanScreen(page) {
  return page.evaluate(({ prefixes, brokenValues }) => {
    // Leaf elements only: a container's textContent repeats its children's, so
    // scanning every node reports the same string once per ancestor.
    const leaves = [...document.querySelectorAll('.page-content *')]
      .filter(el => el.children.length === 0)

    const visible = (el) => {
      const s = getComputedStyle(el)
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false
      const r = el.getBoundingClientRect()
      return r.width > 0 && r.height > 0
    }

    const rawKeys = new Set()
    const broken = new Set()
    const keyShape = new RegExp(`^(${prefixes.join('|')})\\.[a-z0-9_]{2,}$`)

    for (const el of leaves) {
      if (!visible(el)) continue
      const text = (el.textContent || '').trim()
      if (!text || text.length > 400) continue

      if (keyShape.test(text)) rawKeys.add(text)
      for (const bad of brokenValues) {
        // Word-ish boundary: "undefined" standing alone in a cell is the bug;
        // the same letters inside a longer word are not.
        const re = new RegExp(`(^|[\\s(>:,])${bad.replace(/[[\]]/g, '\\$&')}($|[\\s)<:,.])`)
        if (re.test(text)) broken.add(`${bad} in "${text.slice(0, 70)}"`)
      }
    }
    return { rawKeys: [...rawKeys], broken: [...broken] }
  }, { prefixes: PREFIXES, brokenValues: BROKEN_VALUES })
}

for (const lang of LANGS) {
  const { browser, page } = await newBrowser()

  const pageErrors = []
  const badResponses = []
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 160)))
  page.on('response', r => {
    const url = r.url()
    if (!url.includes('/api/')) return
    // 401 is expected while the token is being placed, and a 404 is the app
    // asking whether an optional resource exists.
    if (r.status() < 400 || r.status() === 401 || r.status() === 404) return
    badResponses.push(`${r.status()} ${url.replace(BASE, '')}`)
  })

  await login(page, { lang })

  for (const route of ROUTES) {
    group(`${lang} ${route}`)
    pageErrors.length = 0
    badResponses.length = 0

    let rendered = true
    try {
      await visit(page, route)
    } catch (e) {
      rendered = false
      check(false, 'the screen renders', String(e).slice(0, 120))
    }
    if (!rendered) continue

    check(pageErrors.length === 0, 'no uncaught errors', pageErrors.join(' | '))
    check(badResponses.length === 0, 'every request behind it succeeded',
          badResponses.slice(0, 4).join(' | '))

    const { rawKeys, broken } = await scanScreen(page)
    check(rawKeys.length === 0, 'no raw i18n key is printed at the user',
          rawKeys.slice(0, 5).join(', '))
    check(broken.length === 0, 'no NaN/undefined/[object Object] on screen',
          broken.slice(0, 3).join(' | '))
  }

  await browser.close()
}

process.exit(report())

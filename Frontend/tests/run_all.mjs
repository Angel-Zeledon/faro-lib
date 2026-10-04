/**
 * One command for the whole front end, and one number at the end.
 *
 * Until now the browser suites were three separate `node tests/*.mjs` calls
 * you had to remember, plus two i18n scripts under `.claude/skills/`, and
 * `scripts/run_tests.py` covered none of them. So "does the product work?"
 * had no answer that did not start with "well, which script did you run?".
 * That is the whole reason this file exists.
 *
 * Order matters. The static checks run first because they are seconds, not
 * minutes, and because a catalogue with a missing key makes every screen that
 * asks for it suspicious — better to be told the key's name than to go
 * hunting for it in a browser. The suites that need a running app come after.
 *
 * Run:  node tests/run_all.mjs
 *       node tests/run_all.mjs --static    (no app needed; just the catalogue)
 *
 * Needs, for the browser part: the backend on the port `Frontend/.env.local`
 * names, the frontend on :5000, and the demo tenant seeded
 * (`python -m backend.scripts.seed_demo`).
 */
import { spawn } from 'node:child_process'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(__dirname, '../..')
const staticOnly = process.argv.includes('--static')

const PYTHON = [
  path.join(REPO, 'backend', '.venv', 'Scripts', 'python.exe'),
  path.join(REPO, 'backend', '.venv', 'bin', 'python'),
].find(p => fs.existsSync(p)) || 'python'

/** Static first: seconds, and they name the exact key when they fail. */
const STATIC = [
  { name: 'i18n parity (es/en counts, gaps, duplicates)',
    cmd: PYTHON, args: ['.claude/skills/stockai-i18n/scripts/check_parity.py'] },
  { name: 'i18n missing (keys the UI asks for)',
    cmd: PYTHON, args: ['.claude/skills/stockai-i18n/scripts/check_missing.py'] },
  // `node node_modules/typescript/bin/tsc`, not `npx`: since Node 20,
  // spawning a `.cmd` without a shell throws EINVAL on Windows, and turning
  // the shell on to work around it means quoting every path by hand.
  { name: 'typecheck (tsc --noEmit)', cmd: process.execPath,
    args: [path.join(REPO, 'Frontend', 'node_modules', 'typescript', 'bin', 'tsc'),
           '--noEmit'],
    cwd: path.join(REPO, 'Frontend') },
]

/** Then the browser. `all_screens` is first: if a screen cannot even render,
 *  the flow suites will fail in a way that says less about why. */
const BROWSER = [
  { name: 'all screens, both languages', cmd: process.execPath,
    args: [path.join(__dirname, 'all_screens.mjs')] },
  { name: 'smoke (layout and render regressions)', cmd: process.execPath,
    args: [path.join(__dirname, 'smoke.mjs')] },
  { name: 'critical flows (login form, signup, CSV upload)', cmd: process.execPath,
    args: [path.join(__dirname, 'critical_flows.mjs')] },
]

function run({ name, cmd, args, cwd = REPO }) {
  return new Promise(resolve => {
    console.log(`\n${'─'.repeat(70)}\n▶ ${name}\n${'─'.repeat(70)}`)
    let child
    try {
      child = spawn(cmd, args, { cwd, stdio: 'inherit', shell: false })
    } catch (err) {
      // spawn can throw synchronously (EINVAL on Windows for a .cmd), which
      // the 'error' event never sees — one suite failing to start must not
      // take the whole report down with it.
      console.log(`  could not start: ${err.message}`)
      return resolve({ name, code: 1 })
    }
    child.on('error', err => {
      console.log(`  could not start: ${err.message}`)
      resolve({ name, code: 1 })
    })
    child.on('close', code => resolve({ name, code: code ?? 1 }))
  })
}

const results = []
for (const step of STATIC) results.push(await run(step))

// A failing static check does not stop the browser run: the point of this
// script is one report of everything, not the first thing that broke. Losing
// the other 200 answers to save two minutes is a bad trade when you are trying
// to decide whether the product is shippable.
if (!staticOnly) {
  for (const step of BROWSER) results.push(await run(step))
}

console.log(`\n${'═'.repeat(70)}`)
for (const r of results) {
  console.log(`${r.code === 0 ? 'PASS' : 'FAIL'}  ${r.name}`)
}
const failed = results.filter(r => r.code !== 0)
console.log(`${'═'.repeat(70)}`)
console.log(`${results.length - failed.length}/${results.length} suites passed` +
            `${staticOnly ? '  (static only — the browser suites did not run)' : ''}`)
process.exit(failed.length ? 1 : 0)

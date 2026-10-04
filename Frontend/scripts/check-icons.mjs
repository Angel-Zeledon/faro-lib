// Fails when an "AI-looking" icon or emoji appears in Frontend/src.
// Allow-list: add a path (relative to Frontend/src, forward slashes) below
// with a reason when a use is a real exception.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'src')
const ALLOW = new Set([])
const BANNED_ICONS = ['Sparkles', 'Sparkle', 'Wand', 'Wand2', 'WandSparkles', 'Bot', 'BotMessageSquare', 'Brain', 'BrainCircuit', 'BrainCog', 'Stars', 'Cpu']
const IMPORT_RE = /import\s*\{([^}]*)\}\s*from\s*['"]lucide-react['"]/g
const EMOJI_RE = /[✨\u{1F916}\u{1F9E0}\u{1FA84}\u{1F52E}\u{1F4AB}]/u

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) yield* walk(p)
    else if (/\.(tsx?|css)$/.test(name)) yield p
  }
}

const problems = []
for (const file of walk(SRC)) {
  const rel = relative(SRC, file).replaceAll('\\', '/')
  if (ALLOW.has(rel)) continue
  const text = readFileSync(file, 'utf8')
  for (const m of text.matchAll(IMPORT_RE)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/)[0]
      if (BANNED_ICONS.includes(name)) problems.push(`${rel}: banned lucide icon "${name}"`)
    }
  }
  text.split('\n').forEach((line, i) => {
    if (EMOJI_RE.test(line)) problems.push(`${rel}:${i + 1}: banned AI emoji`)
  })
}
if (problems.length) {
  console.error(problems.join('\n'))
  console.error('\nSee src/components/ui/icons.ts for the sanctioned icon per concept.')
  process.exit(1)
}
console.log('check-icons: ok')

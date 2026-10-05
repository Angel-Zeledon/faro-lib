/**
 * Plain text out of an assistant message, and a clipboard write that works
 * without the clipboard permission.
 *
 * `markdownToPlainText` is the inverse of components/ui/Markdown.tsx: it
 * understands the same constructs (headings, lists, pipe tables, code fences,
 * quotes, rules, bold/italic/code/links) and drops the syntax while keeping the
 * structure a person reads — list markers and indentation stay, a table becomes
 * aligned columns.
 */

function stripInline(s: string): string {
  return s
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    // An external link keeps its address; an in-app link ("/ventas") only means
    // something inside the app, so it copies as its label.
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_m, text: string, url: string) =>
      !/^https?:\/\//.test(url) || text.trim() === url ? text : `${text} (${url})`)
    .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, '$1$2')
}

const LIST_RE = /^(\s*)([*\-•]|\d+[.)])\s+(.*)$/
const isTableSep = (l: string) =>
  /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l)
const splitRow = (l: string) =>
  l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => stripInline(c.trim()))

export function markdownToPlainText(md: string): string {
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (trimmed.startsWith('```')) {
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) out.push(lines[i++])
      i++
      continue
    }

    if (!trimmed) { if (out.length && out[out.length - 1] !== '') out.push(''); i++; continue }

    if (/^([-*_])(\s*\1){2,}$/.test(trimmed)) { i++; continue }

    const heading = trimmed.match(/^#{1,6}\s+(.*)$/)
    if (heading) {
      if (out.length && out[out.length - 1] !== '') out.push('')
      out.push(stripInline(heading[1]))
      i++
      continue
    }

    if (trimmed.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const rows: string[][] = [splitRow(trimmed)]
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(splitRow(lines[i++]))
      const cols = Math.max(...rows.map(r => r.length))
      const widths = Array.from({ length: cols }, (_, c) => Math.max(...rows.map(r => (r[c] ?? '').length)))
      const fmt = (r: string[]) =>
        widths.map((w, c) => (r[c] ?? '').padEnd(w)).join(' | ').replace(/\s+$/, '')
      out.push(fmt(rows[0]))
      out.push(widths.map(w => '-'.repeat(w)).join('-+-'))
      for (const r of rows.slice(1)) out.push(fmt(r))
      continue
    }

    if (trimmed.startsWith('>')) {
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        out.push(stripInline(lines[i++].trim().replace(/^>\s?/, '')))
      }
      continue
    }

    const li = line.match(LIST_RE)
    if (li) {
      const depth = Math.floor(li[1].replace(/\t/g, '  ').length / 2)
      const marker = /\d/.test(li[2]) ? li[2] : '-'
      out.push(`${'  '.repeat(depth)}${marker} ${stripInline(li[3])}`)
      i++
      continue
    }

    out.push(stripInline(trimmed))
    i++
  }
  while (out.length && out[out.length - 1] === '') out.pop()
  return out.join('\n')
}

/** The text a message copies as: the assistant's markdown flattened, a user's message as typed. */
export function messageToPlainText(role: 'user' | 'assistant', content: string): string {
  return role === 'assistant' ? markdownToPlainText(content) : content.trim()
}

/**
 * Write `text` to the clipboard. Uses the async Clipboard API where the page is
 * allowed to, and otherwise a hidden textarea + `execCommand('copy')`, which
 * needs no permission (an insecure origin, an iframe, an older WebView). Returns
 * whether anything was copied; it never throws.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Permission denied or the page is not focused: fall through to the legacy path.
  }
  return legacyCopy(text)
}

function legacyCopy(text: string): boolean {
  if (typeof document === 'undefined') return false
  const previous = document.activeElement as HTMLElement | null
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.setAttribute('aria-hidden', 'true')
  ta.tabIndex = -1
  // Off-screen but still selectable; `font-size: 16px` stops iOS from zooming.
  ta.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0;font-size:16px;pointer-events:none'
  document.body.appendChild(ta)
  let ok = false
  try {
    ta.focus({ preventScroll: true })
    ta.select()
    ta.setSelectionRange(0, text.length)
    ok = document.execCommand('copy')
  } catch {
    ok = false
  } finally {
    document.body.removeChild(ta)
    previous?.focus?.({ preventScroll: true })
  }
  return ok
}

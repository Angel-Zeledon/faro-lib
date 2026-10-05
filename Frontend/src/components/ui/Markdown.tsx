/**
 * The one markdown renderer for AI-written text (assistant replies, narrative
 * cards). Small and hand-written on purpose — no markdown dependency: it covers
 * what the assistant actually writes: headings, paragraphs, bullet/numbered
 * lists (one nesting level per two spaces), pipe tables, fenced and inline
 * code, blockquotes, rules, bold/italic and http(s) links.
 *
 * Every element is semantic HTML styled by `.msg-prose` in globals.css, so the
 * type scale lives in one place and the desktop and phone layouts cannot drift.
 */

const INLINE_RE =
  /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>()]+|\*[^*\s][^*\n]*?\*)/g

export function renderInline(s: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let last = 0
  let k = 0
  for (const m of Array.from(s.matchAll(INLINE_RE))) {
    const tok = m[0]
    const at = m.index ?? 0
    if (at > last) out.push(s.slice(last, at))
    if (tok.startsWith('`')) {
      out.push(<code key={k++}>{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**')) {
      out.push(<strong key={k++}>{renderInline(tok.slice(2, -2))}</strong>)
    } else if (tok.startsWith('[')) {
      const close = tok.indexOf('](')
      out.push(
        <a key={k++} href={tok.slice(close + 2, -1)} target="_blank" rel="noopener noreferrer">
          {tok.slice(1, close)}
        </a>,
      )
    } else if (tok.startsWith('http')) {
      // Trailing sentence punctuation belongs to the prose, not the address.
      const url = tok.replace(/[.,;:!?]+$/, '')
      out.push(<a key={k++} href={url} target="_blank" rel="noopener noreferrer">{url}</a>)
      if (url.length < tok.length) out.push(tok.slice(url.length))
    } else {
      out.push(<em key={k++}>{tok.slice(1, -1)}</em>)
    }
    last = at + tok.length
  }
  if (last < s.length) out.push(s.slice(last))
  return out
}

const LIST_RE = /^(\s*)([*\-•]|\d+[.)])\s+(.*)$/
const splitRow = (l: string) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())
const isTableSep = (l: string) =>
  /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l)
// A figure: digits with separators, an optional sign/currency prefix and a short unit.
const NUMERIC = /^[\s(+\-−~≈$€£]*\d[\d.,\s]*\s*(%|[A-Za-z]{0,3})?\)?$/

interface ListItem { ordered: boolean; text: string; depth: number; start: number }

function renderList(items: ListItem[], from: number, depth: number, key: string): [React.ReactNode, number] {
  const ordered = items[from].ordered
  const lis: React.ReactNode[] = []
  let i = from
  while (i < items.length && items[i].depth >= depth) {
    if (items[i].depth > depth) { i++; continue }
    const item = items[i]
    const at = i
    i++
    let nested: React.ReactNode = null
    if (i < items.length && items[i].depth > depth) {
      const [node, next] = renderList(items, i, items[i].depth, `${key}-${i}`)
      nested = node
      i = next
    }
    lis.push(<li key={`${key}-${at}`}>{renderInline(item.text)}{nested}</li>)
  }
  const Tag = ordered ? 'ol' : 'ul'
  return [<Tag key={key} start={ordered ? items[from].start : undefined}>{lis}</Tag>, i]
}

export function Markdown({ text, large = false }: { text: string; large?: boolean }) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const blocks: React.ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()
    const key = `b${i}`
    if (!trimmed) { i++; continue }

    // Fenced code. An unclosed fence (a reply cut off mid-stream) still renders.
    if (trimmed.startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) body.push(lines[i++])
      i++
      blocks.push(<pre key={key} tabIndex={0}><code>{body.join('\n')}</code></pre>)
      continue
    }

    // Headings. The model writes `### Capital tied up`.
    const heading = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      const level = Math.min(heading[1].length, 4)
      const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4'
      blocks.push(<Tag key={key}>{renderInline(heading[2])}</Tag>)
      i++
      continue
    }

    if (/^([-*_])(\s*\1){2,}$/.test(trimmed)) { blocks.push(<hr key={key} />); i++; continue }

    // Pipe table: a header row followed by a separator row.
    if (trimmed.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(trimmed)
      const aligns = splitRow(lines[i + 1]).map(c =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : null)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(splitRow(lines[i++]))
      // A column is numeric when every filled body cell in it looks like a figure.
      const numeric = head.map((_, c) => rows.length > 0 && rows.every(r => !r[c] || NUMERIC.test(r[c])))
      const cls = (c: number) => (aligns[c] ? `msg-${aligns[c]}` : numeric[c] ? 'msg-num' : undefined)
      blocks.push(
        <div key={key} className="msg-table-wrap" tabIndex={0}>
          <table>
            <thead><tr>{head.map((h, c) => <th key={c} className={cls(c)}>{renderInline(h)}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{head.map((_, c) => <td key={c} className={cls(c)}>{renderInline(r[c] ?? '')}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (trimmed.startsWith('>')) {
      const q: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) q.push(lines[i++].trim().replace(/^>\s?/, ''))
      blocks.push(<blockquote key={key}>{renderInline(q.join(' '))}</blockquote>)
      continue
    }

    if (LIST_RE.test(line)) {
      const items: ListItem[] = []
      while (i < lines.length && LIST_RE.test(lines[i])) {
        const m = lines[i].match(LIST_RE)!
        items.push({
          ordered: /\d/.test(m[2]),
          text: m[3],
          depth: Math.floor(m[1].replace(/\t/g, '  ').length / 2),
          start: parseInt(m[2], 10) || 1,
        })
        i++
      }
      const minDepth = Math.min(...items.map(it => it.depth))
      const [node] = renderList(items.map(it => ({ ...it, depth: it.depth - minDepth })), 0, 0, key)
      blocks.push(node)
      continue
    }

    // Paragraph: consecutive plain lines keep their line breaks.
    const para: string[] = []
    while (
      i < lines.length && lines[i].trim() &&
      !/^(#{1,6}\s|```|>|\|)/.test(lines[i].trim()) && !LIST_RE.test(lines[i])
    ) para.push(lines[i++].trim())
    if (para.length === 0) { para.push(trimmed); i++ }
    blocks.push(
      <p key={key}>
        {para.map((l, li) => <span key={li}>{li > 0 && <br />}{renderInline(l)}</span>)}
      </p>,
    )
  }
  return <div className={`msg-prose${large ? ' msg-prose-lg' : ''}`}>{blocks}</div>
}

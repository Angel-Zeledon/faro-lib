// Renders one help-center page's blocks. No state and no effects: the same
// output on the server and the client, so every word is in the server HTML.
import type { Lang } from '@/i18n/translations'
import type { DocBlock, DocShot } from '@/i18n/docs/types'
import { Inline } from '@/components/docs/inline'

// Pixel sizes of Frontend/public/shot-*.png, so a lazy image reserves its
// box and the text below it does not jump when it arrives. The es and en
// captures of a screen share a size.
const SHOT_SIZE: Partial<Record<DocShot, [number, number]>> = {
  automatizacion: [3200, 1137],
  scorecard: [3200, 649],
  usuarios: [3200, 763],
}

function shotSrc(key: DocShot, lang: Lang): string {
  return `/shot-${key}${lang === 'en' ? '-en' : ''}.png`
}

function Block({ b, lang }: { b: DocBlock; lang: Lang }) {
  switch (b.t) {
    case 'p':
      return <p><Inline text={b.text} /></p>
    case 'h2':
      return <h2 id={b.id}><a href={`#${b.id}`} className="dc-anchor"><Inline text={b.text} /></a></h2>
    case 'h3':
      return <h3 id={b.id}><a href={`#${b.id}`} className="dc-anchor"><Inline text={b.text} /></a></h3>
    case 'ul':
      return <ul>{b.items.map((it, i) => <li key={i}><Inline text={it} /></li>)}</ul>
    case 'steps':
      return <ol className="dc-steps">{b.items.map((it, i) => <li key={i}><Inline text={it} /></li>)}</ol>
    case 'dl':
      return (
        <dl className="dc-dl">
          {b.items.map(([k, v], i) => (
            <div key={i}>
              <dt><Inline text={k} /></dt>
              <dd><Inline text={v} /></dd>
            </div>
          ))}
        </dl>
      )
    case 'note':
      return (
        <aside className={`dc-note dc-note-${b.tone}`} role="note">
          {b.title && <p className="dc-note-title"><Inline text={b.title} /></p>}
          <p><Inline text={b.text} /></p>
        </aside>
      )
    case 'shot': {
      const [w, h] = SHOT_SIZE[b.key] ?? [3200, 2000]
      return (
        <figure className="dc-shot">
          <img src={shotSrc(b.key, lang)} alt={b.alt} width={w} height={h} loading="lazy" decoding="async" />
          {b.caption && <figcaption><Inline text={b.caption} /></figcaption>}
        </figure>
      )
    }
    case 'table':
      return (
        <div className="dc-table-wrap">
          <table className="dc-table">
            <thead><tr>{b.head.map((c, i) => <th key={i} scope="col"><Inline text={c} /></th>)}</tr></thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>{r.map((c, j) => <td key={j}><Inline text={c} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'code':
      return <pre className="dc-code"><code>{b.text}</code></pre>
    case 'signals':
      return (
        <ul className="dc-signals">
          {b.items.map((s, i) => (
            <li key={i} className={`dc-sig dc-sig-${s.signal}`}>
              <span className="dc-sig-chip"><span aria-hidden className="dc-sig-dot" />{s.label}</span>
              <span className="dc-sig-text"><Inline text={s.text} /></span>
            </li>
          ))}
        </ul>
      )
    case 'release':
      return (
        <section className="dc-release" aria-labelledby={`rel-${b.date}`}>
          <div className="dc-release-date"><time dateTime={b.date}>{b.date}</time></div>
          <div>
            <h3 id={`rel-${b.date}`}>{b.title}</h3>
            <ul>{b.items.map((it, i) => <li key={i}><Inline text={it} /></li>)}</ul>
          </div>
        </section>
      )
  }
}

export function DocBody({ blocks, lang }: { blocks: DocBlock[]; lang: Lang }) {
  return <>{blocks.map((b, i) => <Block key={i} b={b} lang={lang} />)}</>
}

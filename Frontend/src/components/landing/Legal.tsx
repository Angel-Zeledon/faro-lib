'use client'
// The legal documents — /terminos, /privacidad, /cookies, /aviso-legal and the
// rest listed in legalPaths.ts — in one reading shell, plus the /legal hub
// that lists them all (LegalHub below). Same nav and footer as the rest of the landing, but
// none of the sales furniture the marketing subpages close with: somebody who
// opened the privacy policy came to read, and a "create your account" band in
// the middle of it reads as not taking the question seriously.
//
// Layout: on a wide screen a sticky table of contents to the left of a single
// ~68ch reading column, with the section being read highlighted; on a phone
// the contents fold into a <details> above the text. Section numbers are real:
// these are clauses, and "see section 7" has to mean something.
//
// Copy lives in i18n/legal.ts; this file only renders it.
import Link from 'next/link'
import { Fragment, useEffect, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LEGAL, type LegalBlock, type LegalDoc } from '@/i18n/legal'
import { LANDING } from '@/i18n/landing'
import { LandingStyles } from '@/components/landing/primitives'
import { Nav, Footer, type ChromeProps } from '@/components/landing/chrome'
import { LEGAL_GROUPS, LEGAL_HUB_PATH, LEGAL_ORDER, LEGAL_PATHS, type LegalKey } from '@/components/landing/legalPaths'

const CHROME: ChromeProps = { onHome: false, localAnchors: [] }

const LEGAL_CSS = `
.lg-hero { padding: 128px 0 48px; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.lg-inner { max-width: 1120px; margin: 0 auto; padding: 0 48px; }
.lg-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 22px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.lg-crumbs li + li::before { content: '/'; margin-right: 8px; color: var(--lp-dim); }
.lg-crumbs a { color: var(--lp-muted); text-decoration: none; }
.lg-crumbs a:hover { color: var(--lp-accent); }
.lg-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.lg-h1 {
 font-family: var(--font-brand), system-ui, sans-serif;
 font-size: clamp(30px, 4vw, 46px); font-weight: 600; line-height: 1.08;
 letter-spacing: -0.035em; color: var(--lp-text); margin: 0 0 16px; max-width: 18em; text-wrap: balance;
}
.lg-intro { font-size: 17px; color: var(--lp-body); line-height: 1.65; max-width: 62ch; margin: 0 0 18px; text-wrap: pretty; }
.lg-updated { font-size: 13.5px; color: var(--lp-muted); margin: 0; font-variant-numeric: tabular-nums; }

.lg-body { background: var(--lp-bg); padding: 56px 0 72px; }
.lg-grid { display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 64px; align-items: start; }

.lg-toc { position: sticky; top: 96px; font-size: 13.5px; }
.lg-toc-head { font-size: 13px; font-weight: 700; color: var(--lp-text); margin: 0 0 12px; }
.lg-toc ol { list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--lp-border); }
.lg-toc a {
 display: grid; grid-template-columns: 22px 1fr; gap: 4px; padding: 6px 0 6px 14px; margin-left: -1px;
 border-left: 2px solid transparent; color: var(--lp-muted); text-decoration: none; line-height: 1.4;
 transition: color 140ms ease, border-color 140ms ease;
}
.lg-toc a:hover { color: var(--lp-text); }
.lg-toc a[aria-current='true'] { color: var(--lp-text); border-left-color: var(--lp-accent); font-weight: 600; }
.lg-toc-n { color: var(--lp-dim); font-variant-numeric: tabular-nums; }
.lg-toc-fold { display: none; }

.lg-doc { max-width: calc(68ch + 44px); min-width: 0; }
.lg-summary {
 margin: 0 0 48px; padding: 22px 26px 20px; border-radius: 14px;
 background: var(--lp-accent-bg); border: 1px solid var(--lp-accent-bd);
}
.lg-summary h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; letter-spacing: -0.01em; color: var(--lp-text); margin: 0 0 10px; }
.lg-summary ul { margin: 0; padding: 0 0 0 18px; list-style: disc; }
.lg-summary li { font-size: 15.5px; color: var(--lp-text); line-height: 1.6; margin: 0 0 6px; }
.lg-summary li::marker { color: var(--lp-accent); }

.lg-sec { scroll-margin-top: 96px; padding-top: 8px; margin-bottom: 40px; }
.lg-sec h2 {
 display: grid; grid-template-columns: 2.1em 1fr; align-items: baseline;
 font-family: var(--font-brand), system-ui, sans-serif; font-size: 21px; font-weight: 600;
 letter-spacing: -0.02em; line-height: 1.3; color: var(--lp-text); margin: 0 0 14px;
}
.lg-sec-n { color: var(--lp-accent); font-variant-numeric: tabular-nums; font-size: 0.86em; }
.lg-sec-body { padding-left: calc(21px * 2.1); }
.lg-sec p, .lg-sec li { font-size: 15.5px; color: var(--lp-body); line-height: 1.75; text-wrap: pretty; }
.lg-sec p { margin: 0 0 14px; }
.lg-sec ul { margin: 0 0 16px; padding-left: 20px; list-style: disc; }
.lg-sec ol { margin: 0 0 16px; padding-left: 22px; list-style: decimal; }
.lg-sec li::marker { color: var(--lp-dim); }
.lg-sec li { margin-bottom: 6px; }
.lg-doc strong { color: var(--lp-text); font-weight: 600; }
.lg-doc a { color: var(--lp-accent); text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
.lg-doc code { font-size: 0.92em; padding: 1px 5px; border-radius: 5px; background: var(--lp-bg2); border: 1px solid var(--lp-border); }

.lg-table { width: 100%; border-collapse: collapse; margin: 4px 0 18px; font-size: 14px; }
.lg-table th { text-align: left; font-weight: 600; color: var(--lp-text); padding: 10px 12px 10px 0; border-bottom: 1px solid var(--lp-border-strong); vertical-align: bottom; }
.lg-table td { color: var(--lp-body); padding: 11px 12px 11px 0; border-bottom: 1px solid var(--lp-border); vertical-align: top; line-height: 1.6; }
.lg-table td:first-child { color: var(--lp-text); font-weight: 600; overflow-wrap: break-word; hyphens: auto; }

.lg-questions { margin: 8px 0 0; padding-top: 22px; border-top: 1px solid var(--lp-border); font-size: 15px; color: var(--lp-body); }

.lg-others { border-top: 1px solid var(--lp-border); background: var(--lp-bg2); padding: 44px 0 52px; }
.lg-others h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; color: var(--lp-text); margin: 0 0 16px; }
.lg-others ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 32px; }
.lg-others a { display: block; padding: 14px 0; border-top: 1px solid var(--lp-border); text-decoration: none; }
.lg-others-label { display: block; font-size: 16px; font-weight: 600; color: var(--lp-text); margin-bottom: 4px; }
.lg-others-desc { display: block; font-size: 13.5px; color: var(--lp-muted); line-height: 1.5; }
.lg-others a:hover .lg-others-label { color: var(--lp-accent); }

.lg-hub-group { margin: 0 0 44px; }
.lg-hub-group h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 0 0 6px; }
.lg-hub-group ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 32px; }
.lg-hub-group a { display: block; padding: 16px 0; border-top: 1px solid var(--lp-border); text-decoration: none; }
.lg-hub-group a:hover .lg-others-label { color: var(--lp-accent); }

@media (max-width: 1000px) {
 .lg-grid { grid-template-columns: minmax(0, 1fr); gap: 0; }
 .lg-toc { display: none; }
 .lg-toc-fold { display: block; margin: 0 0 32px; border: 1px solid var(--lp-border); border-radius: 12px; background: var(--lp-bg2); }
 .lg-toc-fold summary { cursor: pointer; padding: 0 16px; min-height: 48px; display: flex; align-items: center; font-size: 14.5px; font-weight: 600; color: var(--lp-text); }
 .lg-toc-fold ol { list-style: none; margin: 0; padding: 0 8px 10px; }
 .lg-toc-fold a { display: grid; grid-template-columns: 26px 1fr; align-items: center; min-height: 44px; padding: 0 8px; color: var(--lp-body); text-decoration: none; font-size: 14.5px; }
 .lg-others ul { grid-template-columns: minmax(0, 1fr); }
 .lg-hub-group ul { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 760px) {
 .lg-hero { padding: 96px 0 32px; }
 .lg-inner { padding: 0 20px; }
 .lg-crumbs a { display: inline-flex; align-items: center; min-height: 44px; }
 .lg-crumbs ol { margin-bottom: 8px; }
 .lg-intro { font-size: 15.5px; }
 .lg-body { padding: 32px 0 48px; }
 .lg-summary { padding: 18px 18px 14px; margin-bottom: 36px; }
 .lg-sec h2 { font-size: 19px; grid-template-columns: 1.9em 1fr; }
 .lg-sec-body { padding-left: 0; }
 .lg-others a { min-height: 44px; }
 /* Tables become stacked records: four columns do not fit 320px of text,
    and a table that scrolls sideways hides the column that matters. */
 .lg-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
 .lg-table, .lg-table tbody, .lg-table tr, .lg-table td { display: block; width: 100%; }
 .lg-table tr { padding: 12px 0; border-bottom: 1px solid var(--lp-border); }
 .lg-table td { border: 0; padding: 2px 0; }
 .lg-table td:not(:first-child)::before { content: attr(data-label); display: block; font-size: 12px; font-weight: 600; color: var(--lp-muted); margin-top: 6px; }
}
`

// `[label](href)`, `**bold**` and `code` — the only markup the catalogue uses.
const INLINE = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g

export function LegalText({ text }: { text: string }) {
  const out: React.ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${m.index}`
    if (m[1] !== undefined) {
      const href = m[2]
      out.push(href.startsWith('/') && !href.startsWith('//')
        ? <Link key={key} href={href}>{m[1]}</Link>
        : <a key={key} href={href}>{m[1]}</a>)
    } else if (m[3] !== undefined) {
      out.push(<strong key={key}>{m[3]}</strong>)
    } else {
      out.push(<code key={key}>{m[4]}</code>)
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return <>{out}</>
}

function Block({ block }: { block: LegalBlock }) {
  if (typeof block === 'string') return <p><LegalText text={block} /></p>
  if ('list' in block) {
    return <ul>{block.list.map((item, i) => <li key={i}><LegalText text={item} /></li>)}</ul>
  }
  const { head, rows } = block.table
  return (
    <table className="lg-table">
      <thead><tr>{head.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r}>
            {row.map((cell, c) => <td key={c} data-label={head[c]}><LegalText text={cell} /></td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// The section in view, for the contents' highlight. The last heading that has
// crossed 40% of the viewport height wins — a section is "being read" from
// the moment its title reaches the reader's eye line, not when it fills the
// screen (a short section never would).
function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(null)
  useEffect(() => {
    const onScroll = () => {
      const line = window.innerHeight * 0.4
      let current: string | null = null
      for (const id of ids) {
        const el = document.getElementById(id)
        if (el && el.getBoundingClientRect().top <= line) current = id
      }
      setActive(current)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [ids])
  return active
}

function Contents({ doc, head, active }: { doc: LegalDoc; head: string; active: string | null }) {
  return (
    <nav aria-label={head} className="lg-toc">
      <p className="lg-toc-head">{head}</p>
      <ol>
        {doc.sections.map((s, i) => (
          <li key={s.id}>
            <a href={`#${s.id}`} aria-current={active === s.id ? 'true' : undefined}>
              <span className="lg-toc-n">{i + 1}</span>
              <span>{s.title}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  )
}

export function LegalPage({ doc: key }: { doc: LegalKey }) {
  const { lang } = useLanguage()
  const C = LEGAL[lang]
  const L = LANDING[lang]
  const doc = C.docs[key]
  const [ids] = useState(() => LEGAL[lang].docs[key].sections.map(s => s.id))
  const active = useActiveSection(ids)

  return (
    <div className="lp" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: LEGAL_CSS }} />
      <Nav {...CHROME} />
      <main>
        <header className="lg-hero">
          <div className="lg-inner">
            <nav aria-label={L.pages.breadcrumb} className="lg-crumbs">
              <ol>
                <li><Link href="/">{L.pages.home}</Link></li>
                <li><span aria-current="page">{doc.label}</span></li>
              </ol>
            </nav>
            <h1 className="lg-h1">{doc.title}</h1>
            <p className="lg-intro">{doc.intro}</p>
            <p className="lg-updated">{C.updated}</p>
          </div>
        </header>

        <div className="lg-body">
          <div className="lg-inner lg-grid">
            <Contents doc={doc} head={C.toc} active={active} />

            <article className="lg-doc">
              <details className="lg-toc-fold">
                <summary>{C.toc}</summary>
                <ol>
                  {doc.sections.map((s, i) => (
                    <li key={s.id}>
                      <a href={`#${s.id}`}><span className="lg-toc-n">{i + 1}</span><span>{s.title}</span></a>
                    </li>
                  ))}
                </ol>
              </details>

              <section className="lg-summary" aria-labelledby="lg-summary-title">
                <h2 id="lg-summary-title">{C.summaryTitle}</h2>
                <ul>{doc.summary.map((s, i) => <li key={i}><LegalText text={s} /></li>)}</ul>
              </section>

              {doc.sections.map((s, i) => (
                <section key={s.id} id={s.id} className="lg-sec" aria-labelledby={`${s.id}-title`}>
                  <h2 id={`${s.id}-title`}>
                    <span className="lg-sec-n">{i + 1}.</span>
                    <span>{s.title}</span>
                  </h2>
                  <div className="lg-sec-body">
                    {s.blocks.map((b, j) => <Fragment key={j}><Block block={b} /></Fragment>)}
                  </div>
                </section>
              ))}

              <p className="lg-questions"><LegalText text={C.questions} /></p>
            </article>
          </div>
        </div>

        <section className="lg-others" aria-labelledby="lg-others-title">
          <div className="lg-inner">
            <h2 id="lg-others-title">{C.otherDocs}</h2>
            <ul>
              {LEGAL_ORDER.filter(k => k !== key).map(k => (
                <li key={k}>
                  <Link href={LEGAL_PATHS[k]}>
                    <span className="lg-others-label">{C.docs[k].label}</span>
                    <span className="lg-others-desc">{C.docs[k].intro}</span>
                  </Link>
                </li>
              ))}
              <li>
                <Link href={LEGAL_HUB_PATH}>
                  <span className="lg-others-label">{C.hub.allLink}</span>
                  <span className="lg-others-desc">{C.hub.intro}</span>
                </Link>
              </li>
            </ul>
          </div>
        </section>
      </main>
      <Footer {...CHROME} />
    </div>
  )
}

// /legal: every document, grouped, with its one-line description. Same hero
// and reading width as a document, no table of contents (it IS one).
export function LegalHub() {
  const { lang } = useLanguage()
  const C = LEGAL[lang]
  const L = LANDING[lang]

  return (
    <div className="lp" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: LEGAL_CSS }} />
      <Nav {...CHROME} />
      <main>
        <header className="lg-hero">
          <div className="lg-inner">
            <nav aria-label={L.pages.breadcrumb} className="lg-crumbs">
              <ol>
                <li><Link href="/">{L.pages.home}</Link></li>
                <li><span aria-current="page">{C.hub.label}</span></li>
              </ol>
            </nav>
            <h1 className="lg-h1">{C.hub.title}</h1>
            <p className="lg-intro">{C.hub.intro}</p>
            <p className="lg-updated">{C.updated}</p>
          </div>
        </header>

        <div className="lg-body">
          <div className="lg-inner">
            {LEGAL_GROUPS.map(g => (
              <section key={g.id} className="lg-hub-group" aria-labelledby={`lg-hub-${g.id}`}>
                <h2 id={`lg-hub-${g.id}`}>{C.hub.groups[g.id]}</h2>
                <ul>
                  {g.keys.map(k => (
                    <li key={k}>
                      <Link href={LEGAL_PATHS[k]}>
                        <span className="lg-others-label">{C.docs[k].title}</span>
                        <span className="lg-others-desc">{C.docs[k].intro}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <p className="lg-questions" style={{ maxWidth: '68ch' }}><LegalText text={C.questions} /></p>
          </div>
        </div>
      </main>
      <Footer {...CHROME} />
    </div>
  )
}

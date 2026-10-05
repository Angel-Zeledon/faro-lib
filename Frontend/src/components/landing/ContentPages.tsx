'use client'
// The landing's content pages: the industries hub and one page per industry,
// "StockAI vs. Excel", "How it is calculated", "Integrations and data" and the
// changelog. Copy lives in i18n/landingContent.ts (typed es/en); URLs in
// components/landing/contentPaths.ts. They share the subpages' look (SUB_CSS,
// the hero, the closing band) so the site reads as one product.
//
// Every app link (sign-up, /prueba) goes through appHref(): the app may live
// on its own origin.
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { LANDING_CONTENT, type ContentCopy, type Industry } from '@/i18n/landingContent'
import { T } from '@/components/landing/theme'
import { LandingStyles, Section, Tag, H2, Lead, Check, Dash, Scroller, useScrollReveal } from '@/components/landing/primitives'
import { Nav, Footer, type ChromeProps } from '@/components/landing/chrome'
import { FinalSection } from '@/components/landing/sections'
import { SUB_CSS } from '@/components/landing/Subpages'
import { appHref } from '@/lib/siteUrls'
import { mailHref } from '@/components/landing/contact'
import {
  CONTENT_PATHS, INDUSTRIES_HUB_PATH, INDUSTRY_ORDER, INDUSTRY_PATHS,
  type ContentKey, type IndustryKey,
} from '@/components/landing/contentPaths'

export const CP_CSS = `
.cp-grid { display: grid; grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr)); gap: 20px; margin-top: 8px; }
.cp-grid .lp-card { height: 100%; }
/* A row of three that ends with two cards: the pair splits the full width instead of leaving a hole. */
.cp-grid.is-tail2, .cp-hub.is-tail2 { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.cp-grid.is-tail2 > *, .cp-hub.is-tail2 > * { grid-column: span 2; }
.cp-grid.is-tail2 > :nth-last-child(-n+2), .cp-hub.is-tail2 > :nth-last-child(-n+2) { grid-column: span 3; }
.cp-grid.is-tail1 > :last-child { grid-column: 1 / -1; }
.cp-grid + .cp-note, .cp-hub + .cp-note { margin-top: 28px; }
.cp-stack { display: grid; gap: 14px; max-width: 760px; }
.cp-badge { display: inline-flex; align-items: center; padding: 4px 11px; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass); font-size: 12px; font-weight: 700; letter-spacing: 0.02em; color: var(--lp-muted); }
.cp-badge.is-soon { color: var(--lp-accent); border-color: var(--lp-accent-bd); }
.cp-cta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
.cp-cta a { text-decoration: none; }
.cp-list { list-style: none; margin: 0; padding: 0; max-width: 760px; }
.cp-list li { display: flex; align-items: flex-start; gap: 10px; padding: 9px 0; font-size: 15px; color: var(--lp-body); line-height: 1.65; }
.cp-list li > svg { margin-top: 4px; }
.cp-prose { max-width: 68ch; font-size: 16px; color: var(--lp-body); line-height: 1.75; margin: 0 0 14px; }
.cp-note { max-width: 68ch; font-size: 13.5px; color: var(--lp-muted); line-height: 1.65; margin: 14px 0 0; }
.cp-hub { list-style: none; margin: 8px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.cp-hub a { display: flex; flex-direction: column; height: 100%; text-decoration: none; }
.cp-hub .cp-go { margin-top: auto; padding-top: 14px; font-size: 14px; font-weight: 700; color: var(--lp-accent); }
.cp-sample-row { display: grid; grid-template-columns: minmax(170px, 1.7fr) repeat(5, minmax(0, 0.9fr)) minmax(120px, 1.1fr); gap: 10px; align-items: center; }
.cp-sample .lp-table-head, .cp-sample .lp-table-row { padding-left: 18px; padding-right: 18px; }
.cp-cmp-row { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(150px, 1fr) minmax(220px, 1.5fr); gap: 16px; align-items: start; }
.cp-reading { margin-top: 20px; border-left: 3px solid var(--lp-accent); padding: 4px 0 4px 18px; max-width: 76ch; }
.cp-reading p { font-size: 15px; color: var(--lp-body); line-height: 1.7; margin: 6px 0 0; }
.cp-log { list-style: none; margin: 0; padding: 0; max-width: 820px; }
.cp-log > li { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 28px; padding: 30px 0; border-top: 1px solid var(--lp-border); }
.cp-log > li:last-child { border-bottom: 1px solid var(--lp-border); }
.cp-log time { font-size: 14px; font-weight: 700; color: var(--lp-accent); font-variant-numeric: tabular-nums; padding-top: 3px; }
.cp-log h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 20px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.3; color: var(--lp-text); margin: 0 0 12px; }
.cp-log ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.cp-log ul li { position: relative; padding-left: 18px; font-size: 14.5px; color: var(--lp-body); line-height: 1.65; }
.cp-log ul li::before { content: ''; position: absolute; left: 0; top: 0.75em; width: 6px; height: 6px; border-radius: 50%; background: var(--lp-accent); }
/* Article sections (the guides): the heading sits in a rail on wide screens so the text column keeps a readable line length and the page uses its width. */
.sec.gd-sec { padding: 76px 0; }
.gd-grid { display: grid; grid-template-columns: minmax(0, 300px) minmax(0, 1fr); gap: 56px; align-items: start; }
.gd-head { position: sticky; top: 104px; }
.gd-head .lp-h2 { font-size: clamp(26px, 2.5vw, 34px); margin: 0; }
.gd-body > :last-child { margin-bottom: 0; }
@media (max-width: 960px) {
 .sec.gd-sec { padding: 56px 0; }
 .gd-grid { grid-template-columns: minmax(0, 1fr); gap: 0; }
 .gd-head { position: static; }
 .gd-head .lp-h2 { font-size: clamp(28px, 3.6vw, 42px); margin: 0 0 16px; }
}
.cp-audience { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.cp-pills { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 28px; }
.cp-pills a { font-size: 13.5px; font-weight: 600; color: var(--lp-body); text-decoration: none; padding: 7px 14px; min-height: 36px; display: inline-flex; align-items: center; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass); transition: border-color 160ms ease, color 160ms ease; }
.cp-pills a:hover { border-color: var(--lp-accent); color: var(--lp-accent); }
.cp-tour { list-style: none; margin: 8px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }
.cp-tour figure { margin: 0; }
.cp-tour h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 18px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 16px 0 6px; }
.cp-tour p { font-size: 14px; color: var(--lp-body); line-height: 1.65; margin: 0; }
@media (max-width: 900px) {
 .cp-grid, .cp-grid.is-tail2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
 .cp-grid:not(.is-tail1) > :last-child:nth-child(odd) { grid-column: 1 / -1; }
 .cp-grid.is-tail2 > *, .cp-grid.is-tail2 > :nth-last-child(-n+2), .cp-hub.is-tail2 > *, .cp-hub.is-tail2 > :nth-last-child(-n+2) { grid-column: auto; }
 .cp-hub, .cp-hub.is-tail2, .cp-tour { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 760px) {
 .cp-grid, .cp-audience { grid-template-columns: minmax(0, 1fr); }
 .cp-log > li { grid-template-columns: minmax(0, 1fr); gap: 10px; padding: 24px 0; }
 .cp-cta a { width: 100%; }
}
`

function useContent() {
  const { lang } = useLanguage()
  return { lang, C: LANDING_CONTENT[lang], L: LANDING[lang] }
}

// A page head plus where it sits in the breadcrumb.
interface Crumb { label: string; href?: string }
interface Related { href: string; label: string; desc: string }

export function ContentShell({ title, intro, crumbs, related, children }: {
  title: string
  intro: string
  crumbs: Crumb[]
  related: Related[]
  children: React.ReactNode
}) {
  const { L, C } = useContent()
  const chrome: ChromeProps = { onHome: false, localAnchors: ['contacto'] }
  useScrollReveal()
  return (
    <div className="lp" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: SUB_CSS + CP_CSS }} />
      <Nav {...chrome} />
      <main>
        <header className="sub-hero">
          <div className="hero-bg" aria-hidden>
            <div className="hero-grid" />
          </div>
          <div className="sub-inner">
            <nav aria-label={L.pages.breadcrumb} className="sub-crumbs">
              <ol>
                <li><Link href="/">{L.pages.home}</Link></li>
                {crumbs.map((c, i) => (
                  <li key={c.label}>
                    {i === crumbs.length - 1
                      ? <span aria-current="page">{c.label}</span>
                      : <Link href={c.href ?? '/'}>{c.label}</Link>}
                  </li>
                ))}
              </ol>
            </nav>
            <h1 className="sub-h1">{title}</h1>
            <p className="sub-intro">{intro}</p>
          </div>
        </header>

        {children}

        <Section style={{ padding: '72px 0' }}>
          <h2 className="lp-h3" style={{ marginBottom: 22 }}>{C.common.related}</h2>
          <ul className="rel-list">
            {related.map(r => (
              <li key={r.href} className="rel-item">
                <Link href={r.href}>
                  <span className="rel-label">{r.label}</span>
                  <span className="rel-desc">{r.desc}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Section>
        <FinalSection />
      </main>
      <Footer {...chrome} />
    </div>
  )
}

function Cards({ items, cols = 3 }: { items: { title: string; desc: string; tag?: string; soon?: boolean }[]; cols?: number }) {
  // Four cards read better as a 2x2 than as three plus an orphan.
  const columns = cols === 3 && items.length === 4 ? 2 : cols
  const tail = columns === 3 && items.length % 3 === 2 ? ' is-tail2' : columns === 3 && items.length > 4 && items.length % 3 === 1 ? ' is-tail1' : ''
  return (
    <div className={`cp-grid${tail}`} style={{ '--cols': columns } as React.CSSProperties}>
      {items.map(({ title, desc, tag, soon }) => (
        <div key={title} className="lp-card" data-reveal>
          {tag && <div style={{ marginBottom: 12 }}><span className={`cp-badge${soon ? ' is-soon' : ''}`}>{tag}</span></div>}
          <div className="lp-card-title">{title}</div>
          <div className="lp-card-body">{desc}</div>
        </div>
      ))}
    </div>
  )
}

function TrialCtas({ C }: { C: ContentCopy }) {
  return (
    <div className="cp-cta">
      <Link href={appHref('/prueba')} className="btn-primary">{C.common.ctaTrial}</Link>
      <Link href={appHref('/signup')} className="btn-ghost">{C.common.ctaSignup}</Link>
    </div>
  )
}

function relatedFor(C: ContentCopy, keys: ContentKey[], industries: IndustryKey[] = []): Related[] {
  return [
    ...industries.map(k => ({ href: INDUSTRY_PATHS[k], label: C.industries.items[k].label, desc: C.industries.items[k].short })),
    ...keys.map(k => ({ href: CONTENT_PATHS[k], label: C[k].label, desc: C[k].title })),
  ]
}

// ── /industrias ──────────────────────────────────────────────────────────────
export function IndustriesHubPage() {
  const { C } = useContent()
  const H = C.industries.hub
  return (
    <ContentShell
      title={H.title}
      intro={H.intro}
      crumbs={[{ label: H.label }]}
      related={relatedFor(C, ['method', 'excel', 'integrations'])}
    >
      <Section>
        <Lead maxWidth={680}>{H.lead}</Lead>
        <ul className={`cp-hub${INDUSTRY_ORDER.length % 3 === 2 ? ' is-tail2' : ''}`}>
          {INDUSTRY_ORDER.map(k => {
            const I = C.industries.items[k]
            return (
              <li key={k} data-reveal>
                <Link href={INDUSTRY_PATHS[k]} className="lp-card">
                  <div className="lp-card-title">{I.label}</div>
                  <div className="lp-card-body">{I.short}</div>
                  <span className="cp-go">{C.common.seeIndustry} →</span>
                </Link>
              </li>
            )
          })}
        </ul>
      </Section>
      <Section alt>
        <H2>{H.commonTitle}</H2>
        <Cards items={H.common} />
        <p className="cp-note">{H.calendarNote}</p>
        <TrialCtas C={C} />
      </Section>
    </ContentShell>
  )
}

// ── /industrias/<slug> ───────────────────────────────────────────────────────
function SampleTable({ I, C, signals }: { I: Industry; C: ContentCopy; signals: string[] }) {
  const colors = [T.red, T.amber, T.green, T.muted]
  return (
    <>
      <div style={{ marginBottom: 14 }}><span className="cp-badge">{C.common.sampleBadge}</span></div>
      <Scroller minWidth={820}>
        <div className="lp-table cp-sample">
          <div className="lp-table-head cp-sample-row">
            {C.common.sampleHead.map((h, i) => <div key={h} className="lp-label" style={i > 0 && i < 6 ? { textAlign: 'right' } : undefined}>{h}</div>)}
          </div>
          {I.sampleRows.map(r => (
            <div key={r.product} className="lp-table-row cp-sample-row">
              <span style={{ fontSize: 14, color: T.text, fontWeight: 600 }}>{r.product}</span>
              {[r.daily, r.stock, r.lead, r.reorder, r.cover].map((v, i) => (
                <span key={i} style={{ fontSize: 13.5, color: T.body, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{v}</span>
              ))}
              <span className="lp-signal" style={{ color: colors[r.signal] }}><i aria-hidden />{signals[r.signal]}</span>
            </div>
          ))}
        </div>
      </Scroller>
      <p className="cp-note">{C.common.sampleDisclaimer}</p>
    </>
  )
}

export function IndustryPage({ industry }: { industry: IndustryKey }) {
  const { C, L } = useContent()
  const I = C.industries.items[industry]
  const signals = L.decide.signals.map(s => s.signal)
  const others = INDUSTRY_ORDER.filter(k => k !== industry).slice(0, 2)
  return (
    <ContentShell
      title={I.title}
      intro={I.intro}
      crumbs={[{ label: C.common.breadcrumbIndustries, href: INDUSTRIES_HUB_PATH }, { label: I.label }]}
      related={[...relatedFor(C, ['method'], others)]}
    >
      <Section>
        <H2>{I.problemsTitle}</H2>
        <Cards items={I.problems} />
      </Section>
      <Section alt>
        <H2>{I.howTitle}</H2>
        <Cards items={I.how} />
      </Section>
      <Section>
        <Tag>{C.common.sampleBadge}</Tag>
        <H2>{I.sampleTitle}</H2>
        <Lead maxWidth={680}>{I.sampleLead}</Lead>
        <SampleTable I={I} C={C} signals={signals} />
        <div className="cp-reading">
          <div className="lp-label">{C.common.readingLabel}</div>
          <p>{I.sampleReading}</p>
        </div>
        <div className="cp-cta">
          <Link href={appHref('/prueba')} className="btn-primary">{C.common.ctaTrial}</Link>
          <Link href="/como-funciona#como-decide" className="btn-ghost">{C.common.ctaRule}</Link>
        </div>
      </Section>
      <Section alt>
        <H2>{I.limitsTitle}</H2>
        <ul className="cp-list">
          {I.limits.map(l => <li key={l}><Dash /><span>{l}</span></li>)}
        </ul>
        <TrialCtas C={C} />
      </Section>
    </ContentShell>
  )
}

// ── /stockai-vs-excel ────────────────────────────────────────────────────────
export function ExcelPage() {
  const { C } = useContent()
  const E = C.excel
  return (
    <ContentShell
      title={E.title}
      intro={E.intro}
      crumbs={[{ label: E.label }]}
      related={relatedFor(C, ['method', 'integrations'], ['consumer'])}
    >
      <Section>
        <H2>{E.fairTitle}</H2>
        <Cards items={E.fair} cols={2} />
      </Section>
      <Section alt>
        <H2>{E.breaksTitle}</H2>
        <Cards items={E.breaks} />
      </Section>
      <Section>
        <H2>{E.tableTitle}</H2>
        <Scroller minWidth={760}>
          <div className="lp-table">
            <div className="lp-table-head cp-cmp-row">
              <div className="lp-label">{E.tableHead[0]}</div>
              <div className="lp-label">{E.tableHead[1]}</div>
              <div className="lp-label" style={{ color: T.accent }}>{E.tableHead[2]}</div>
            </div>
            {E.rows.map(r => (
              <div key={r.need} className="lp-table-row cp-cmp-row">
                <span style={{ fontSize: 14, color: T.text, fontWeight: 600 }}>{r.need}</span>
                <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.55 }}>{r.excel}</span>
                <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.55 }}>{r.stockai}</span>
              </div>
            ))}
          </div>
        </Scroller>
      </Section>
      <Section alt>
        <div className="cp-audience">
          <div>
            <H2>{E.keepTitle}</H2>
            <ul className="cp-list">
              {E.keep.map(k => <li key={k}><Check /><span>{k}</span></li>)}
            </ul>
          </div>
          <div className="lp-card">
            <div className="lp-card-title">{E.bridgeTitle}</div>
            <div className="lp-card-body">{E.bridge}</div>
          </div>
        </div>
        <TrialCtas C={C} />
      </Section>
    </ContentShell>
  )
}

// ── /como-se-calcula ─────────────────────────────────────────────────────────
export function MethodPage() {
  const { C, lang } = useContent()
  const M = C.method
  return (
    <ContentShell
      title={M.title}
      intro={M.intro}
      crumbs={[{ label: M.label }]}
      related={relatedFor(C, ['excel', 'integrations'], ['retail'])}
    >
      <Section>
        <H2>{M.stepsTitle}</H2>
        <Cards items={M.steps} />
      </Section>
      <Section alt>
        <H2>{M.leadTitle}</H2>
        {M.leadBody.map(p => <p key={p} className="cp-prose">{p}</p>)}
      </Section>
      <Section>
        <H2>{M.accuracyTitle}</H2>
        <Cards items={M.accuracy} cols={2} />
      </Section>
      <Section alt>
        <H2>{M.limitsTitle}</H2>
        <ul className="cp-list">
          {M.limits.map(l => <li key={l}><Dash /><span>{l}</span></li>)}
        </ul>
        <p className="cp-note">{M.docsNote}</p>
        <div className="cp-cta">
          <Link href="/como-funciona#como-decide" className="btn-primary">{C.common.ctaRule}</Link>
          <a href={`/stockai-manual-${lang}.pdf`} download className="btn-ghost">{LANDING[lang].manual.cta}</a>
        </div>
      </Section>
    </ContentShell>
  )
}

// ── /integraciones ───────────────────────────────────────────────────────────
export function IntegrationsPage() {
  const { C, L } = useContent()
  const G = C.integrations
  return (
    <ContentShell
      title={G.title}
      intro={G.intro}
      crumbs={[{ label: G.label }]}
      related={relatedFor(C, ['method', 'changelog'], ['retail'])}
    >
      <Section>
        <H2>{G.nowTitle}</H2>
        <Cards items={G.now} />
      </Section>
      <Section alt>
        <H2>{G.formatsTitle}</H2>
        <ul className="cp-list">
          {G.formats.map(f => <li key={f}><Check /><span>{f}</span></li>)}
        </ul>
        <div className="cp-cta">
          <Link href="/desarrolladores" className="btn-ghost">{G.devLink}</Link>
          <Link href="/docs" className="btn-ghost">{G.docsLink}</Link>
        </div>
      </Section>
      <Section>
        <H2>{G.soonTitle}</H2>
        <Lead maxWidth={680}>{G.soonLead}</Lead>
        <Cards items={G.soon.map(s => ({ ...s, tag: G.soonTitle, soon: true }))} cols={2} />
        <p className="cp-note">{G.soonNote}</p>
      </Section>
      <Section alt>
        <H2>{G.customTitle}</H2>
        <p className="cp-prose">{G.customBody}</p>
        <div className="cp-cta">
          <a href={mailHref()} className="btn-primary">{L.pricing.ctaEmail}</a>
        </div>
      </Section>
    </ContentShell>
  )
}

// ── /novedades ───────────────────────────────────────────────────────────────
export function ChangelogPage() {
  const { C } = useContent()
  const N = C.changelog
  return (
    <ContentShell
      title={N.title}
      intro={N.intro}
      crumbs={[{ label: N.label }]}
      related={relatedFor(C, ['method', 'integrations'], ['consumer'])}
    >
      <Section>
        <ol className="cp-log">
          {N.entries.map(e => (
            <li key={e.date}>
              <time dateTime={e.date}>{e.date}</time>
              <div>
                <h2>{e.title}</h2>
                <ul>{e.items.map(i => <li key={i}>{i}</li>)}</ul>
              </div>
            </li>
          ))}
        </ol>
        <p className="cp-note">{N.note}</p>
      </Section>
    </ContentShell>
  )
}

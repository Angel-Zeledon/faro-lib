'use client'
// The landing's guides (see i18n/landingGuides.ts): a long-form article with
// H2 sections, a visible FAQ (the same text the server wrapper publishes as
// FAQPage), links to the product pages and a call to action for the free plan.
// Same shell, stylesheet and closing band as the other content pages.
import Link from 'next/link'
import { Fragment } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { GUIDES } from '@/i18n/landingGuides'
import { Section, H2, Check } from '@/components/landing/primitives'
import { ContentShell } from '@/components/landing/ContentPages'
import { GUIDE_PATHS, type GuideKey } from '@/components/landing/contentPaths'
import { SUBPAGE_PATHS } from '@/components/landing/subpagePaths'
import { appHref } from '@/lib/siteUrls'

// `[text](/path)` inside a paragraph becomes a real link; everything else stays text.
function RichText({ text }: { text: string }) {
  const out: React.ReactNode[] = []
  const re = /\[([^\]]+)\]\((\/[^)\s]*)\)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const at = m.index
    if (at > last) out.push(text.slice(last, at))
    out.push(<Link key={at} href={m[2]} style={{ color: 'var(--lp-accent)', fontWeight: 600 }}>{m[1]}</Link>)
    last = at + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return <>{out.map((n, i) => <Fragment key={i}>{n}</Fragment>)}</>
}

export function GuidePage({ guide }: { guide: GuideKey }) {
  const { lang } = useLanguage()
  const G = GUIDES[lang]
  const g = G.items[guide]
  return (
    <ContentShell
      title={g.title}
      intro={g.intro}
      crumbs={[{ label: g.label }]}
      related={g.next.map(k => ({ href: GUIDE_PATHS[k], label: G.items[k].label, desc: G.items[k].metaDesc }))}
    >
      {g.sections.map((s, i) => (
        <Section key={s.h} alt={i % 2 === 1}>
          <H2>{s.h}</H2>
          {s.p.map(p => <p key={p} className="cp-prose"><RichText text={p} /></p>)}
          {s.list && (
            <ul className="cp-list">
              {s.list.map(item => <li key={item}><Check /><span><RichText text={item} /></span></li>)}
            </ul>
          )}
        </Section>
      ))}

      <Section alt={g.sections.length % 2 === 1}>
        <H2>{g.faqTitle}</H2>
        <div className="cp-stack">
          {g.faq.map(f => (
            <div key={f.q} className="lp-card">
              <h3 className="lp-card-title">{f.q}</h3>
              <div className="lp-card-body">{f.a}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section alt={g.sections.length % 2 === 0}>
        <H2>{G.productTitle}</H2>
        <nav aria-label={G.productTitle} className="cp-pills">
          <Link href={SUBPAGE_PATHS.how}>{G.productLinks[0]}</Link>
          <Link href={SUBPAGE_PATHS.pricing}>{G.productLinks[1]}</Link>
          <Link href="/como-se-calcula">{G.productLinks[2]}</Link>
        </nav>
        <p className="cp-prose" style={{ marginTop: 28 }}>{g.ctaLead}</p>
        <div className="cp-cta">
          <Link href={appHref('/prueba')} className="btn-primary">{G.ctaTrial}</Link>
          <Link href={appHref('/signup')} className="btn-ghost">{G.ctaSignup}</Link>
        </div>
      </Section>
    </ContentShell>
  )
}

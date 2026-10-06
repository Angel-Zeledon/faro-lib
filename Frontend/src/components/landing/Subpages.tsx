'use client'
// The four public subpages: /precios, /como-funciona, /preguntas-frecuentes
// and /seguridad. Each is a deeper version of a home-page section, built from
// the same components (components/landing/sections), so the home page and its
// subpage cannot say different things.
//
// Why they exist: Google builds the sitelinks under a search result from real
// URLs it can index, not from anchors on one long page. Each page therefore
// opens with its own H1 and intro (`L.pages.*`), different from the home
// section it deepens — the same heading and paragraph on two URLs reads to a
// search engine as one page published twice.
//
// Every app link (sign-up, sign-in, /prueba) still goes through appHref(): the
// app may live on its own origin.
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING, type LandingCopy } from '@/i18n/landing'
import { T } from '@/components/landing/theme'
import { LandingStyles, Section, Tag, H2, Lead, useScrollReveal } from '@/components/landing/primitives'
import { Nav, Footer, type ChromeProps } from '@/components/landing/chrome'
import { DecideSection, PricingSection, TrustSection, FinalSection, MorningSection, FeaturesSection } from '@/components/landing/sections'
import { EngineFlow, ModelsSection, ENGINE_CSS } from '@/components/landing/engine'
import { GUIDE_CSS, TourChapters } from '@/components/landing/ScreenGuide'
import { mailHref } from '@/components/landing/contact'
import { SUBPAGE_PATHS, SUBPAGE_ORDER, type SubpageKey as PageKey } from '@/components/landing/subpagePaths'

export const SUB_CSS = `
.sub-hero { position: relative; padding: 132px 0 64px; overflow: hidden; isolation: isolate; border-bottom: 1px solid var(--lp-border); background: var(--lp-bg); }
.sub-hero .hero-grid {
 -webkit-mask-image: radial-gradient(ellipse 60% 90% at 18% 20%, black 20%, transparent 70%);
 mask-image: radial-gradient(ellipse 60% 90% at 18% 20%, black 20%, transparent 70%);
}
.sub-inner { position: relative; max-width: 1120px; margin: 0 auto; padding: 0 48px; }
.sub-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 24px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.sub-crumbs li + li::before { content: '/'; margin-right: 8px; color: var(--lp-dim); }
.sub-crumbs a { color: var(--lp-muted); text-decoration: none; }
.sub-crumbs a:hover { color: var(--lp-accent); }
.sub-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.sub-h1 {
 font-family: var(--font-brand), system-ui, sans-serif;
 font-size: clamp(32px, 4.6vw, 54px); font-weight: 600; line-height: 1.06;
 letter-spacing: -0.04em; color: var(--lp-text); margin: 0 0 20px; max-width: 14em; text-wrap: balance;
}
.sub-intro { font-size: clamp(16px, 1.5vw, 18px); color: var(--lp-body); line-height: 1.65; max-width: 62ch; margin: 0; text-wrap: pretty; }
.sub-toc { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 32px; }
.sub-toc a {
 font-size: 13px; font-weight: 600; color: var(--lp-body); text-decoration: none;
 padding: 6px 13px; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass);
 transition: border-color 160ms ease, color 160ms ease;
}
.sub-toc a:hover { border-color: var(--lp-accent); color: var(--lp-accent); }

/* The FAQ, every answer open: the page IS the answers. */
.faq-list { max-width: 760px; border-top: 1px solid var(--lp-border); }
.faq-item { padding: 26px 0 24px; border-bottom: 1px solid var(--lp-border); }
.faq-item h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -0.015em; line-height: 1.4; color: var(--lp-text); margin: 0 0 10px; }
.faq-item p { font-size: 15px; color: var(--lp-body); line-height: 1.75; margin: 0; max-width: 66ch; }
.faq-more { margin: 32px 0 0; font-size: 15px; color: var(--lp-body); line-height: 1.7; max-width: 62ch; }
.faq-more a { color: var(--lp-accent); font-weight: 600; text-decoration: none; margin-left: 4px; }

/* The other subpages, so each one links to all the rest. */
.rel-list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 32px; }
.rel-item { border-top: 1px solid var(--lp-border); position: relative; }
.rel-item::before { content: ''; position: absolute; top: -1px; left: 0; width: 28px; height: 2px; background: linear-gradient(90deg, var(--lp-accent), var(--lp-beam)); }
.rel-item a { display: block; padding: 20px 0 8px; text-decoration: none; }
.rel-label { display: block; font-family: var(--font-brand), system-ui, sans-serif; font-size: 18px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin-bottom: 6px; transition: color 160ms ease; }
.rel-desc { display: block; font-size: 14px; color: var(--lp-body); line-height: 1.6; }
.rel-item a:hover .rel-label { color: var(--lp-accent); }

@media (max-width: 900px) {
 .rel-list { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
 .sub-hero { padding: 100px 0 44px; }
 .sub-inner { padding: 0 20px; }
 .sub-crumbs a { display: inline-flex; align-items: center; min-height: 44px; }
 .sub-crumbs ol { margin-bottom: 12px; }
 .sub-toc a { min-height: 44px; display: inline-flex; align-items: center; }
}
`

function useCopy() {
  const { lang } = useLanguage()
  return { lang, L: LANDING[lang] }
}

function SubpageShell({ page, chrome, toc, children }: {
  page: PageKey
  chrome: ChromeProps
  toc?: [string, string][]
  children: React.ReactNode
}) {
  const { L } = useCopy()
  const copy = L.pages[page]
  useScrollReveal()
  return (
    <div className="lp" id="top">
      <LandingStyles />
      <style dangerouslySetInnerHTML={{ __html: SUB_CSS }} />
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
                <li><span aria-current="page">{copy.label}</span></li>
              </ol>
            </nav>
            <h1 className="sub-h1">{copy.title}</h1>
            <p className="sub-intro">{copy.intro}</p>
            {toc && (
              <nav aria-label={L.pages.onThisPage} className="sub-toc">
                <span className="lp-label" style={{ marginRight: 4 }}>{L.pages.onThisPage}:</span>
                {toc.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
              </nav>
            )}
          </div>
        </header>

        {children}

        <RelatedPages current={page} L={L} />
        <FinalSection />
      </main>
      <Footer {...chrome} />
    </div>
  )
}

function RelatedPages({ current, L }: { current: PageKey; L: LandingCopy }) {
  return (
    <Section style={{ padding: '72px 0' }}>
      <h2 className="lp-h3" style={{ marginBottom: 22 }}>{L.pages.related}</h2>
      <ul className="rel-list">
        {SUBPAGE_ORDER.filter(k => k !== current).map(k => (
          <li key={k} className="rel-item">
            <Link href={SUBPAGE_PATHS[k]}>
              <span className="rel-label">{L.pages[k].label}</span>
              <span className="rel-desc">{L.pages[k].title}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  )
}

// ── /precios ──────────────────────────────────────────────────────────────────
export function PricingPage() {
  return (
    <SubpageShell page="pricing" chrome={{ onHome: false, localAnchors: ['precio', 'funciones', 'contacto'] }}>
      <PricingSection />
      {/* What the source code includes: the whole feature list. */}
      <FeaturesSection />
    </SubpageShell>
  )
}

// ── /como-funciona ────────────────────────────────────────────────────────────
// The steps, the rule, and the screen guide inline — the guide's real home.
// On the home page the same chapters sit behind a dialog (ScreenGuide).
export function HowItWorksPage() {
  const { L, lang } = useCopy()
  const toc: [string, string][] = [
    ['#pasos', L.how.tag],
    ['#motor', L.engine.tag],
    ['#como-decide', L.decide.tag],
    ['#tu-manana', L.morning.tag],
    ...L.tour.chapters.map((c, i) => [`#guia-capitulo-${i + 1}`, c.chapter] as [string, string]),
  ]
  const pdfHref = `/stockai-manual-${lang}.pdf`
  return (
    <SubpageShell page="how" chrome={{ onHome: false, localAnchors: ['como-decide', 'motor', 'tu-manana', 'contacto'] }} toc={toc}>
      <style dangerouslySetInnerHTML={{ __html: ENGINE_CSS }} />
      <Section id="pasos">
        <Tag>{L.how.tag}</Tag>
        <H2>{L.how.title}</H2>
        <Lead maxWidth={660}>{L.how.lead}</Lead>
        <EngineFlow />
      </Section>

      <ModelsSection />

      <DecideSection alt={false} />

      <MorningSection alt />

      <Section id="guia">
        <style dangerouslySetInnerHTML={{ __html: GUIDE_CSS }} />
        <H2>{L.tour.title}</H2>
        <Lead maxWidth={680}>{L.tour.lead}</Lead>
        <TourChapters tour={L.tour} shots />
        <div className="sg-manual">
          <div>
            <h3>{L.manual.title}</h3>
            <p>{L.manual.body}</p>
          </div>
          <div>
            <a href={pdfHref} download className="btn-primary btn-sm">{L.manual.cta}</a>
            <div style={{ fontSize: 12, color: T.muted, marginTop: 9 }}>{L.manual.note}</div>
          </div>
        </div>
      </Section>
    </SubpageShell>
  )
}

// ── /preguntas-frecuentes ─────────────────────────────────────────────────────
// Every answer is in the HTML and visible — no accordion. The FAQPage JSON-LD
// in app/preguntas-frecuentes/page.tsx is read from the same catalogue.
export function FaqPage() {
  const { L } = useCopy()
  return (
    <SubpageShell page="faq" chrome={{ onHome: false, localAnchors: ['contacto'] }}>
      <Section>
        <div className="faq-list">
          {L.faq.items.map(({ q, a }) => (
            <div key={q} className="faq-item">
              <h2>{q}</h2>
              <p>{a}</p>
            </div>
          ))}
        </div>
        <p className="faq-more">
          {L.faq.lead}
          <a href={mailHref()}>{L.faq.cta}</a>
        </p>
      </Section>
    </SubpageShell>
  )
}

// ── /seguridad ────────────────────────────────────────────────────────────────
export function SecurityPage() {
  const { L } = useCopy()
  const S = L.pages.security
  return (
    <SubpageShell page="security" chrome={{ onHome: false, localAnchors: ['confianza', 'contacto'] }}>
      <TrustSection
        decideHref="/como-funciona#como-decide"
        ruleDesc={S.ruleDesc}
        extra={[{ title: S.trialTitle, desc: S.trialDesc }]}
      />
    </SubpageShell>
  )
}

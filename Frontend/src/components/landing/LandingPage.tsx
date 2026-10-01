'use client'
// The landing is inside LanguageProvider (see app/layout.tsx), so it reads the
// same `lang` the app does — a visitor who switches here stays switched after
// signing in. The copy itself lives in i18n/landing.ts, typed so the two
// languages cannot drift apart.
//
// The stylesheet, the chrome (nav, footer), the layout primitives and the
// sections that also have a subpage of their own (/precios, /como-funciona,
// /preguntas-frecuentes, /seguridad) live in sibling modules, shared with
// those subpages — see components/landing/{theme,primitives,chrome,sections}.
import Link from 'next/link'
import { appHref } from '@/lib/siteUrls'
import { useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { ScreenGuide } from '@/components/landing/ScreenGuide'
import { T, DISPLAY } from '@/components/landing/theme'
import { LandingStyles, Section, Tag, H2, H3, Lead, Check, Dash, Scroller, useScrollReveal } from '@/components/landing/primitives'
import { Nav, Footer } from '@/components/landing/chrome'
import { DecideSection, PricingSection, TrustSection, FaqAccordion, FinalSection } from '@/components/landing/sections'
import { EngineFlow, ModelsSection, ENGINE_CSS } from '@/components/landing/engine'

const HOME = { onHome: true } as const

// The headline, set word by word on load (see .lp-w in theme.ts). Each word is
// its own clipping box; `--i` counts words across both lines so the second
// line carries on where the first stopped. The spaces stay real text nodes,
// so the heading reads and copies exactly as written.
function HeroTitle({ lines }: { lines: string[] }) {
 let i = 0
 return (
 <h1 className="lp-h1">
 {lines.map((line, li) => (
 <span key={li}>
 {li > 0 && <br />}
 {line.split(' ').map((word, wi) => (
 <span key={wi}>
 {wi > 0 && ' '}
 <span className="lp-w"><span className="lp-wi" style={{ '--i': i++ } as React.CSSProperties}>{word}</span></span>
 </span>
 ))}
 </span>
 ))}
 </h1>
 )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LandingPage() {
  const { lang } = useLanguage()
  const L = LANDING[lang]

  // Same names the render code already used, now sourced from the active
  // language. Nothing below this line had to change.
  const PROBLEMS   = L.problem.items
  const CASES      = L.cases.items
  const BENEFITS   = L.benefits.items
  const COMPARE    = L.compare.rows
  const ROLES      = L.includes.roles
  const INCLUDES   = L.includes.items
  const NEED       = L.start.need
  const NOT_NEED   = L.start.notNeed
  // The hero frame IS the first screen of the tour, so it follows the
  // language with everything else instead of pointing at a fixed file.
  const HERO_SHOT  = L.tour.chapters[0].screens[0]

 const [activeCase, setActiveCase] = useState(0)
 useScrollReveal()

 // Same rule as the stats strip below (see the comment there): a figure on this
 // page has to be one the product can back. This block used to carry fifteen
 // result percentages — "reducción de quiebres 20–35%", "compras de emergencia
 // −30–50%", "merma −25–40%" and twelve more. StockAI has never measured a single
 // one: there is no customer outcome study, no before/after dataset, nothing in
 // the repo that produces them. They were written to look like a case study.
 // What replaces them is what the product actually DOES for that operation,
 // each item checkable against code (signal thresholds and the 3-reception rule
 // in backend/inventory/service.py, the 30-day donor floor in the transfer
 // service, the BOM explosion in backend/inventory/bom_service.py).
 // If real customer outcomes ever get measured, they belong here — with the
 // customer, the period and the baseline named. A percentage with no source
 // does not go back in.

 return (
 <div className="lp" id="top">
 <LandingStyles />
 <style dangerouslySetInnerHTML={{ __html: ENGINE_CSS }} />

 <Nav {...HOME} />

 <main>

 {/* ── HERO ─────────────────────────────────────────────────────────── */}
 <section className="hero-sec">
 <div className="hero-bg" aria-hidden>
 <div className="hero-grid" />
 </div>
 <div className="hero-inner">

 <p className="hero-eyebrow lp-rise lp-d1">{L.hero.eyebrow}</p>

 <HeroTitle lines={[L.hero.title1, L.hero.title2]} />

 <p className="hero-lead lp-rise lp-d2">
 {L.hero.lead}
 </p>

 <div className="hero-ctas lp-rise lp-d3">
 <Link href={appHref('/signup?demo=1')} className="btn-primary">{L.hero.cta}</Link>
 {/* A temporary account for a visitor who wants to look before giving
     an email. The page itself (/prueba) belongs to the trial workstream. */}
 <Link href={appHref('/prueba')} className="btn-ghost">{L.hero.ctaTrial}</Link>
 </div>
 <p className="hero-note lp-rise lp-d4">
 <Check />
 <span>{L.hero.trialNote}</span>
 </p>

 {/* Framed real product screenshot */}
 <div className="hero-stage">
 <div id="demo" className="lp-frame lp-land">
 <div className="lp-frame-in">
 <div className="lp-chrome" aria-hidden>
 <i /><i /><i />
 <span>{L.hero.frame}</span>
 </div>
 <img src={HERO_SHOT.img} alt={HERO_SHOT.alt} fetchPriority="high" width={3200} height={2000} style={{ height: 'auto' }} />
 </div>
 </div>
 </div>

 <div className="hero-pills lp-rise lp-d5">
 <div className="lp-label" style={{ marginRight: 4 }}>{L.misc.industriesLabel}</div>
 {L.heroPills.map(s => (
 <span key={s} className="hero-pill">{s}</span>
 ))}
 </div>
 </div>
 </section>

 {/* ── STATS STRIP ──────────────────────────────────────────────────── */}
 <div className="strip-shell">
 <div className="strip-grid" data-reveal>
 {/* Every figure here has to be one the product can back, because the buyer
     who believes it lands two clicks later on /pronosticos and checks.
     This strip used to lead with "94% Precisión promedio de pronóstico" and
     "−75% Tiempo invertido en planificación": nobody has measured either, and
     on real sessions the app shows its own users 75–89%. Advertising 94% while
     the screen says 75% is the one thing this product cannot afford to do —
     its whole argument is that it tells you the truth about your numbers.
     What replaces them is countable: the four states every product lands in
     (the semaforo), the deliveries it takes to learn a supplier's real lead time
     (MIN_LEAD_TIME_OBSERVATIONS = 3), and the catalogue size the product is
     exercised against.
     If a real average accuracy ever gets measured across customers, it belongs
     here — with the number the app actually shows. */}
 {[
 { value: '4', label: L.strip.models },
 { value: '3', label: L.strip.deliveries },
 { value: '5K+', label: L.strip.skus },
 { value: 'CSV', label: L.strip.csv },
 ].map(({ value, label }) => (
 <div key={label} className="strip-cell">
 <div className="strip-value">{value}</div>
 <div className="strip-label">{label}</div>
 </div>
 ))}
 </div>
 </div>

 {/* ── THE PROBLEM ──────────────────────────────────────────────────── */}
 <Section id="problema" alt>
 <Tag>{L.problem.tag}</Tag>
 <H2>{L.problem.title}</H2>
 <Lead>
 {L.problem.lead}
 </Lead>
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(310px, 100%), 1fr))', gap: 16 }}>
 {PROBLEMS.map(({ title, desc }) => (
 <div key={title} data-reveal className="lp-card">
 <div className="lp-bar" />
 <div className="lp-card-title">{title}</div>
 <div className="lp-card-body">{desc}</div>
 </div>
 ))}
 </div>
 </Section>

 {/* ── HOW IT WORKS ─────────────────────────────────────────────────── */}
 <Section id="solucion">
 <Tag>{L.how.tag}</Tag>
 <H2>{L.how.title}</H2>
 <Lead maxWidth={660}>
 {L.how.lead}
 </Lead>
 {/* The workflow as an animated diagram, with the model competition
     illustrated beside it (owner's team, 2026-10-01: the engine is the
     headline). Every step's claim is backed in i18n/landing.ts. */}
 <EngineFlow />

 {/* The screen guide is an opt-in deep dive (owner, 2026-10-01): a teaser
     here, the chapters in a dialog. See components/landing/ScreenGuide. */}
 <ScreenGuide tour={L.tour} manual={L.manual} lang={lang} primaryClass="btn-primary" />
 </Section>

 {/* ── THE MODELS ───────────────────────────────────────────────────── */}
 <ModelsSection />

 {/* ── HOW IT DECIDES ───────────────────────────────────────────────── */}
 <DecideSection alt={false} />

 {/* ── ABOUT US ─────────────────────────────────────────────────────── */}
 <Section id="nosotros">
 {/* The owner can replace this with the real story / team. */}
 <div style={{ maxWidth: 760 }}>
 <Tag>{L.about.tag}</Tag>
 <H2>{L.about.title}</H2>
 <p style={{ fontSize: 17, color: T.body, lineHeight: 1.75, margin: '0 0 20px' }}>
 {L.about.body1}
 </p>
 <p style={{ fontSize: 17, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.about.body2}
 </p>
 </div>
 </Section>

 {/* ── INDUSTRIES ───────────────────────────────────────────────────── */}
 <Section id="casos" alt>
 <Tag>{L.cases.tag}</Tag>
 <H2>{L.cases.title}</H2>
 <Lead>
 {L.cases.lead}
 </Lead>
 <div className="case-tabs" role="tablist">
 {CASES.map(({ label }, i) => (
 <button key={label} type="button" role="tab" aria-selected={activeCase === i} className={`case-tab${activeCase === i ? ' is-on' : ''}`} onClick={() => setActiveCase(i)}>
 {label}
 </button>
 ))}
 </div>
 <div className="split lp-card" style={{ padding: '38px 40px', borderRadius: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
 <div key={`a-${activeCase}`} className="lp-swap">
 <div className="lp-label" style={{ color: T.accent, marginBottom: 12 }}>{CASES[activeCase].label}</div>
 <div style={{ fontFamily: DISPLAY, fontSize: 24, fontWeight: 600, color: T.text, marginBottom: 16, letterSpacing: '-0.03em', lineHeight: 1.25 }}>{CASES[activeCase].title}</div>
 <div style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75 }}>{CASES[activeCase].desc}</div>
 </div>
 <div key={`b-${activeCase}`} className="lp-swap" style={{ animationDelay: '60ms' }}>
 {/* Was "Impacto típico en …" over a column of green percentages. The
     heading promised a measured outcome, so the numbers under it read as
     measurements; none of them were. It now says what the product does. */}
 <div className="lp-label" style={{ marginBottom: 10 }}>{L.cases.doesLabel}  {CASES[activeCase].label.toLowerCase()}</div>
 {CASES[activeCase].does.map(item => (
 <div key={item} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '14px 0', borderBottom: `1px solid ${T.border}` }}>
 <span style={{ flexShrink: 0, width: 6, height: 6, marginTop: 8, borderRadius: '50%', background: T.green }} />
 <span style={{ fontSize: 14, color: T.body, lineHeight: 1.65 }}>{item}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── WHAT'S INCLUDED (BENEFITS) ───────────────────────────────────── */}
 <Section>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 72, alignItems: 'start' }}>
 <div>
 <Tag>{L.benefits.tag}</Tag>
 <H2>{L.benefits.title}</H2>
 <Lead>
 {L.benefits.lead}
 </Lead>
 </div>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 20px' }}>
 {BENEFITS.map(b => (
 <div key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '11px 0', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ marginTop: 1 }}><Check /></div>
 <span style={{ fontSize: 14, color: T.body, lineHeight: 1.5 }}>{b}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── VS EXCEL ─────────────────────────────────────────────────────── */}
 <Section id="comparacion" alt>
 <Tag>{L.compare.tag}</Tag>
 <H2>{L.compare.title}</H2>
 <Lead>
 {L.compare.lead}
 </Lead>
 <Scroller minWidth={620}>
 <div className="lp-table">
 <div className="lp-table-head" style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px' }}>
 <div className="lp-label">{L.compare.head[0]}</div>
 <div className="lp-label" style={{ textAlign: 'center' }}>{L.compare.head[1]}</div>
 <div className="lp-label" style={{ textAlign: 'center', color: T.accent }}>{L.compare.head[2]}</div>
 </div>
 {COMPARE.map(({ feature, excel, stockai }) => (
 <div key={feature} className="lp-table-row" style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px' }}>
 <span style={{ fontSize: 14, color: T.body }}>{feature}</span>
 <span style={{ fontSize: 13.5, color: T.red, textAlign: 'center', fontWeight: 500 }}>{excel}</span>
 <span style={{ fontSize: 13.5, color: T.green, textAlign: 'center', fontWeight: 700 }}>{stockai}</span>
 </div>
 ))}
 </div>
 </Scroller>
 </Section>

 {/* ── WHAT YOU NEED ────────────────────────────────────────────────── */}
 <Section id="empezar">
 <Tag>{L.start.tag}</Tag>
 <H2>{L.start.title}</H2>
 <Lead maxWidth={680}>
 {L.start.lead}
 </Lead>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, alignItems: 'stretch' }}>
 <div className="lp-card lp-card-soft">
 <H3>{L.start.needTitle}</H3>
 {NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Check /></div>
 <span style={{ fontSize: 14, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 <div className="lp-card">
 <H3>{L.start.notNeedTitle}</H3>
 {NOT_NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Dash /></div>
 <span style={{ fontSize: 14, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── PRICE ────────────────────────────────────────────────────────── */}
 <PricingSection />

 {/* ── TRUST ──────────────────────────────────────────────────────────── */}
 {/* Every claim is backed by code — see the note above TrustSection. */}
 <TrustSection decideHref="#como-decide" />

 {/* ── WHAT'S INCLUDED (ROLES) ──────────────────────────────────────── */}
 <Section id="incluye">
 <Tag>{L.includes.tag}</Tag>
 <H2>{L.includes.title}</H2>
 <Lead maxWidth={700}>
 {L.includes.lead}
 </Lead>

 <H3>{L.includes.rolesTitle}</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, marginBottom: 56 }}>
 {ROLES.map(({ role, pain, gain }) => (
 <div key={role} data-reveal className="lp-card lp-card-soft">
 <div className="lp-card-title" style={{ marginBottom: 16 }}>{role}</div>
 <div className="lp-label" style={{ marginBottom: 6 }}>{L.misc.roleToday}</div>
 <div className="lp-card-body" style={{ marginBottom: 16 }}>{pain}</div>
 <div className="lp-label" style={{ color: T.accent, marginBottom: 6 }}>{L.misc.roleWith}</div>
 <div className="lp-card-body">{gain}</div>
 </div>
 ))}
 </div>

 <H3>{L.includes.itemsTitle}</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
 {INCLUDES.map(({ title, desc, isNew }) => (
 <div key={title} data-reveal className="lp-card">
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
 <span className="lp-card-title" style={{ marginBottom: 0 }}>{title}</span>
 {isNew && (
 <span style={{ fontSize: 11, fontWeight: 700, color: T.green, background: T.greenBg, border: `1px solid ${T.greenBd}`, borderRadius: 20, padding: '2px 9px' }}>{L.includes.isNew}</span>
 )}
 </div>
 <div className="lp-card-body">{desc}</div>
 </div>
 ))}
 </div>

 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.7, margin: '28px 0 0', maxWidth: 760 }}>
 {L.includes.tail}
 </p>
 </Section>

 {/* ── FOR YOUR TECHNICAL TEAM ──────────────────────────────────────── */}
 {/* The integration details for whoever the buyer forwards the link to.
     The model competition moved up into its own section (#motor) on
     2026-10-01; what stays here is ABC-XYZ, the API/MCP and the calendar. */}
 <Section id="tecnico" alt style={{ padding: '64px 0' }}>
 <Tag>{L.tech.tag}</Tag>
 <h2 className="lp-h3">{L.tech.title}</h2>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.7, margin: '0 0 22px', maxWidth: 640 }}>
 {L.tech.lead}
 </p>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
 {L.tech.items.map(({ title, desc }) => (
 <div key={title} className="lp-card" style={{ padding: '16px 18px', borderRadius: 12 }}>
 <h3 style={{ fontSize: 13.5, fontWeight: 700, color: T.text, margin: '0 0 4px' }}>{title}</h3>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.6 }}>{desc}</div>
 </div>
 ))}
 </div>
 <a href="/desarrolladores" className="trust-link" style={{ marginTop: 16 }}>{L.tech.devLink}</a>
 </Section>

 {/* ── FAQ ──────────────────────────────────────────────────────────── */}
 <FaqAccordion />

 {/* ── CLOSING BAND: the three ways forward ─────────────────────────── */}
 <FinalSection />
 </main>

 {/* ── FOOTER ───────────────────────────────────────────────────────── */}
 <Footer {...HOME} />
 </div>
 )
}

'use client'
// The landing sections that also have a subpage of their own: how it works,
// how it decides, price, trust, FAQ and the closing band. The home page and
// the subpages render these same components, so a change to the pitch lands
// in both places at once. Moved verbatim from LandingPage.tsx.
import Link from 'next/link'
import { useState } from 'react'
import { appHref } from '@/lib/siteUrls'
import { LANDING, type Titled } from '@/i18n/landing'
import { useLanguage } from '@/contexts/LanguageContext'
import { T } from '@/components/landing/theme'
import { Section, Tag, H2, H3, Lead, Check, Scroller } from '@/components/landing/primitives'
import { mailHref, waHref, CONTACT_EMAIL, CONTACT_PHONE_HREF, CONTACT_PHONE_LABEL } from '@/components/landing/contact'

function useCopy() {
  const { lang } = useLanguage()
  return { lang, L: LANDING[lang] }
}

// ── How it decides ────────────────────────────────────────────────────────────
export function DecideSection({ alt = true }: { alt?: boolean }) {
 const { L } = useCopy()
 // The four signal colours are design, not copy, so they stay here and are
 // zipped onto the translated rows by position.
 const SIGNAL_COLORS = [T.red, T.amber, T.green, T.muted]
 const SIGNALS = L.decide.signals.map((sig, i) => ({ ...sig, color: SIGNAL_COLORS[i] }))
 return (
 <Section id="como-decide" alt={alt}>
 <Tag>{L.decide.tag}</Tag>
 <H2>{L.decide.title}</H2>
 <Lead maxWidth={680}>
 {L.decide.lead}
 </Lead>

 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start', marginBottom: 32 }}>
 <div>
 <H3>{L.decide.formulaTitle}</H3>
 <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75, margin: '0 0 14px' }}>
 {L.decide.formulaBody}
 </p>
 <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75, margin: '0 0 14px' }}>
 {L.decide.formulaBody2}
 </p>
 <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.decide.configBody}
 </p>
 </div>
 <div className="lp-card" style={{ borderLeft: `3px solid ${T.accent}` }}>
 <div className="lp-label" style={{ color: T.accent, marginBottom: 10 }}>{L.misc.exampleTitle}</div>
 <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.misc.exampleBody}
 </p>
 </div>
 </div>

 <Scroller minWidth={660}>
 <div className="lp-table">
 <div className="lp-table-head" style={{ display: 'grid', gridTemplateColumns: '170px 1fr 260px' }}>
 <div className="lp-label">{L.misc.signalHead[0]}</div>
 <div className="lp-label">{L.misc.signalHead[1]}</div>
 <div className="lp-label">{L.misc.signalHead[2]}</div>
 </div>
 {SIGNALS.map(({ signal, rule, example, color }) => (
 <div key={signal} className="lp-table-row" style={{ display: 'grid', gridTemplateColumns: '170px 1fr 260px' }}>
 <span className="lp-signal" style={{ color }}><i aria-hidden />{signal}</span>
 <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.5, paddingRight: 16 }}>{rule}</span>
 <span style={{ fontSize: 13.5, color: T.muted }}>{example}</span>
 </div>
 ))}
 </div>
 </Scroller>

 <div className="lp-card" style={{ marginTop: 20 }}>
 <div className="lp-bar" />
 <div className="lp-card-title">{L.misc.leadTimeNote}</div>
 <div className="lp-card-body">
 {L.decide.leadTimeBody}
 </div>
 </div>
 </Section>
 )
}

// ── Your morning with StockAI ─────────────────────────────────────────────────
// The day in order — a real sequence, so the steps are numbered. Each step's
// backing is noted beside it in i18n/landing.ts.
export function MorningSection({ alt = false }: { alt?: boolean }) {
 const { L } = useCopy()
 const M = L.morning
 return (
 <Section id="tu-manana" alt={alt}>
 <Tag>{M.tag}</Tag>
 <H2>{M.title}</H2>
 <Lead maxWidth={680}>{M.lead}</Lead>
 <ol className="day-list">
 {M.steps.map(({ when, title, desc }, i) => (
 <li key={title} className="day-step">
 <span className="day-num" aria-hidden>{i + 1}</span>
 <div>
 <p className="day-when">{when}</p>
 <h3 className="day-title">{title}</h3>
 <p className="day-desc">{desc}</p>
 </div>
 </li>
 ))}
 </ol>
 </Section>
 )
}

// ── Every feature, grouped ────────────────────────────────────────────────────
export function FeaturesSection({ alt = false, plainChecks = false }: { alt?: boolean; plainChecks?: boolean }) {
 const { L } = useCopy()
 const F = L.features
 return (
 <Section id="funciones" alt={alt}>
 <Tag>{F.tag}</Tag>
 <H2>{F.title}</H2>
 <Lead maxWidth={700}>{F.lead}</Lead>
 <div className="feat-grid">
 {F.groups.map(({ name, items }) => (
 <section key={name} className="feat-group" aria-label={name}>
 <h3 className="feat-name">{name}</h3>
 <ul className="feat-items">
 {items.map(item => (
 <li key={item}><Check plain={plainChecks} /><span>{item}</span></li>
 ))}
 </ul>
 </section>
 ))}
 </div>
 </Section>
 )
}

// ── Price ─────────────────────────────────────────────────────────────────────
// The only commercial offer on the landing (owner, 2026-10-06): the source
// code at one price. The plans still exist in the product (entitlements/
// plans.py, billing); the landing just does not sell them any more. The props
// are kept so the pages that mount this section did not have to change.
export function PricingSection({ part = 'all' }: { calcHref?: string; showCorporate?: boolean; part?: 'all' | 'plans' | 'why' }) {
 const { L } = useCopy()
 // /precios used to mount this twice (plans, then why); the offer shows once.
 if (part === 'why') return null
 const S = L.source
 return (
 <Section id="precio" alt>
 <Tag>{S.tag}</Tag>
 <H2>{S.title}</H2>
 <Lead maxWidth={720}>{S.lead}</Lead>
 <div data-reveal className="lp-card" style={{ maxWidth: 720, marginTop: 28 }}>
 <div className="price-amount">{S.price}</div>
 <div className="lp-card-body" style={{ marginBottom: 18 }}>{S.priceNote}</div>
 <h3 className="lp-h3" style={{ marginBottom: 12 }}>{S.itemsTitle}</h3>
 <ul className="src-list">
 {S.items.map(t => (
 <li key={t}><Check plain /><span>{t}</span></li>
 ))}
 </ul>
 <p className="lp-card-body" style={{ margin: '18px 0' }}>{S.closing}</p>
 <div className="upg-foot">
 <a href={waHref(S.waPrefill)} target="_blank" rel="noopener noreferrer" className="btn-primary btn-sm">{S.cta}</a>
 <a href={mailHref(S.mailSubject)} className="btn-ghost btn-sm">{L.pricing.ctaEmail}</a>
 </div>
 </div>
 </Section>
 )
}

// ── Trust ─────────────────────────────────────────────────────────────────────
// Only claims the code backs (docs/stability.md §4.5: no unsourced figure,
// logo, testimonial or certification goes on this page). In order:
//  1. per-company isolation — every query scoped by tenant_id; forecasts
//     trained per session on the tenant's own dataset.
//  2. roles — admin / analyst / viewer; every mutating endpoint requires
//     require_analyst_or_above (backend/auth/guards.py).
//  3. encryption — SQL source passwords Fernet-encrypted
//     (backend/datasources/service.py), stored service credentials via
//     backend/service_config/crypto.py; user passwords bcrypt-hashed
//     (backend/auth/password.py).
//  4. export + erasure — GET /tenant/export (ZIP of every tenant table) and
//     DELETE /tenant (every table and file), backend/api/v1/tenant_data.py
//     and backend/tenants/data_export.py. Admin-only API, no screen yet,
//     hence "we hand you" rather than "download it yourself".
//  5. not a black box — the #como-decide section (home and /como-funciona).
// ("Nothing locked behind a payment" was item 5 until 2026-10-02: the pricing
// section just above it opens with the same promise, so it said it twice.)
// /seguridad adds one item, the 24-hour trial erasure, which restates the
// claim the closing band already makes (L.final.trialDesc).
export function TrustSection({ decideHref, ruleDesc, extra = [], moreHref }: {
  decideHref: string
  // Replaces the last item's text where the rule is not on the same page.
  ruleDesc?: string
  extra?: Titled[]
  // On the home page: the link to /seguridad, the long version.
  moreHref?: string
}) {
 const { L } = useCopy()
 const last = L.trust.items.length - 1
 const items = [
  ...L.trust.items.map((item, i) => (i === last && ruleDesc ? { ...item, desc: ruleDesc } : item)),
  ...extra,
 ]
 return (
 <Section id="confianza">
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 64, alignItems: 'start' }}>
 <div>
 <Tag>{L.trust.tag}</Tag>
 <H2>{L.trust.title}</H2>
 <p className="lp-lead" style={{ marginBottom: 0 }}>{L.trust.lead}</p>
 {moreHref && <Link href={moreHref} className="trust-link" style={{ marginTop: 16 }}>{L.trust.more}</Link>}
 </div>
 <ul className="trust-list">
 {items.map(({ title, desc }, i) => (
 <li key={title} className="trust-item">
 <h3 className="trust-title">{title}</h3>
 <p className="trust-desc">{desc}</p>
 {i === last && (
 <a href={decideHref} className="trust-link">{L.trust.decideLink}</a>
 )}
 </li>
 ))}
 </ul>
 </div>
 </Section>
 )
}

// ── FAQ, home version: an accordion beside a sticky intro ─────────────────────
export function FaqAccordion({ alt = false }: { alt?: boolean }) {
 const { L } = useCopy()
 const [openFaq, setOpenFaq] = useState<number | null>(null)
 return (
 <Section id="preguntas" alt={alt}>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 72, alignItems: 'start' }}>
 <div className="faq-side" style={{ position: 'sticky', top: 96 }}>
 <Tag>{L.faq.tag}</Tag>
 <H2>{L.faq.title}</H2>
 <p style={{ fontSize: 15.5, color: T.body, lineHeight: 1.7, margin: '0 0 24px' }}>
 {L.faq.lead}
 </p>
 <a href={mailHref()} className="cta-link" style={{ fontSize: 14, fontWeight: 600, color: T.accent, textDecoration: 'none' }}>
 {L.faq.cta}
 </a>
 <div>
 <Link href="/preguntas-frecuentes" className="trust-link" style={{ marginTop: 14 }}>{L.faq.all}</Link>
 </div>
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 0, borderTop: `1px solid ${T.border}` }}>
 {L.faq.items.map(({ q, a }, i) => (
 <div key={i} style={{ borderBottom: `1px solid ${T.border}` }}>
 <h3 style={{ margin: 0, font: 'inherit' }}>
 <button type="button" className="faq-q" aria-expanded={openFaq === i} aria-controls={`faq-a-${i}`} onClick={() => setOpenFaq(openFaq === i ? null : i)}>
 <span className="faq-q-text">{q}</span>
 <span aria-hidden className={`faq-icon${openFaq === i ? ' is-open' : ''}`}>+</span>
 </button>
 </h3>
 {/* The answer stays in the document when closed (hidden, not unmounted),
     so the FAQ text is part of the page for search engines too. */}
 <div id={`faq-a-${i}`} className="faq-a" hidden={openFaq !== i}>{a}</div>
 </div>
 ))}
 </div>
 </div>
 </Section>
 )
}

// ── Closing band: the three ways forward ──────────────────────────────────────
export function FinalSection() {
 const { L } = useCopy()
 return (
 <section id="contacto" className="final-sec" aria-labelledby="final-title">
 <div className="final-inner" data-reveal>
 <h2 id="final-title" className="lp-h2 final-title">{L.final.title}</h2>
 <p className="final-lead">{L.final.lead}</p>
 <div className="final-grid">
 <div className="final-path">
 <h3>{L.final.signupTitle}</h3>
 <p>{L.final.signupDesc}</p>
 <Link href={appHref('/signup')} className="final-btn is-main">{L.pricing.ctaSignup}</Link>
 </div>
 <div className="final-path">
 <h3>{L.final.trialTitle}</h3>
 <p>{L.final.trialDesc}</p>
 <Link href={appHref('/prueba')} className="final-btn">{L.hero.ctaTrial}</Link>
 </div>
 <div className="final-path">
 <h3>{L.final.talkTitle}</h3>
 <p>{L.final.talkDesc}</p>
 <div className="final-btns">
 <a href={waHref(L.pricing.waPrefill)} target="_blank" rel="noopener noreferrer" className="final-btn">{L.pricing.ctaWhatsapp}</a>
 <a href={mailHref(L.pricing.mailSubject)} className="final-btn">{L.pricing.ctaEmail}</a>
 </div>
 <p className="final-reach">
 {L.final.reach.split(/(\{email\}|\{phone\})/).map((part, i) =>
  part === '{email}' ? <a key={i} href={mailHref()}>{CONTACT_EMAIL}</a>
  : part === '{phone}' ? <a key={i} href={CONTACT_PHONE_HREF}>{CONTACT_PHONE_LABEL}</a>
  : part)}
 </p>
 </div>
 </div>
 <p className="final-made">{L.final.madeIn}</p>
 </div>
 </section>
 )
}

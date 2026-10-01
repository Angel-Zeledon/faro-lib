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
import { mailHref, waHref } from '@/components/landing/contact'

function useCopy() {
  const { lang } = useLanguage()
  return { lang, L: LANDING[lang] }
}

// ── How it works: the four steps ──────────────────────────────────────────────
export function HowSteps() {
 const { L } = useCopy()
 return (
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
 {L.how.steps.map(({ n, title, desc }) => (
 <div key={n} data-reveal className="lp-card lp-card-soft" style={{ display: 'flex', gap: 18, alignItems: 'flex-start' }}>
 <div className="lp-step">{n}</div>
 <div>
 <div className="lp-card-title">{title}</div>
 <div className="lp-card-body">{desc}</div>
 </div>
 </div>
 ))}
 </div>
 )
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
 <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.decide.formulaBody2}
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
 <div className="lp-table-head" style={{ display: 'grid', gridTemplateColumns: '170px 1fr 250px' }}>
 <div className="lp-label">{L.misc.signalHead[0]}</div>
 <div className="lp-label">{L.misc.signalHead[1]}</div>
 <div className="lp-label">{L.misc.signalHead[2]}</div>
 </div>
 {SIGNALS.map(({ signal, rule, example, color }) => (
 <div key={signal} className="lp-table-row" style={{ display: 'grid', gridTemplateColumns: '170px 1fr 250px' }}>
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

// ── Price ─────────────────────────────────────────────────────────────────────
// The free tier's ceilings, as advertised in L.pricing.limits. MUST match
// backend/entitlements/plans.py — a landing page promising 200 SKUs while the
// product stops at 100 turns the first real import into a broken promise.
export function PricingSection() {
 const { L } = useCopy()
 const FREE_LIMITS = L.pricing.limits
 return (
 <Section id="precio" alt>
 <Tag>{L.pricing.tag}</Tag>
 <H2>{L.pricing.title}</H2>
 <Lead maxWidth={720}>
 {L.pricing.lead}
 </Lead>
 <ul className="no-strings">
 {L.pricing.noStrings.map(t => (
 <li key={t}><Check />{t}</li>
 ))}
 </ul>

 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, maxWidth: 880, marginBottom: 20 }}>
 <div data-reveal className="lp-card price-card">
 <div className="lp-label" style={{ marginBottom: 10 }}>{L.pricing.freeLabel}</div>
 <div className="price-amount">{L.pricing.freePrice}</div>
 <div style={{ fontSize: 13.5, color: T.body, lineHeight: 1.7, marginBottom: 20 }}>
 {L.pricing.freeNote}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
 {FREE_LIMITS.map(([label, value]) => (
 <div key={label} className="price-row">
 <span>{label}</span>
 <span style={{ fontWeight: 700, color: T.text, whiteSpace: 'nowrap' }}>{value}</span>
 </div>
 ))}
 </div>
 </div>

 <div data-reveal className="lp-card price-card is-paid">
 <div className="lp-label" style={{ color: T.accent, marginBottom: 10 }}>{L.pricing.paidLabel}</div>
 <div className="price-amount">{L.pricing.paidPrice}</div>
 <div style={{ fontSize: 13.5, color: T.body, lineHeight: 1.7, marginBottom: 20 }}>
 {L.pricing.paidNote}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
 {/* Each row states what the PAID tier actually gets. This used to print one
     blanket "unlimited" for every row, which claimed an uncapped upload size
     on a tier that entitlements/plans.py bounds at 2000 MB. */}
 {FREE_LIMITS.map(([label, , paid]) => (
 <div key={label} className="price-row">
 <span>{label}</span>
 <span style={{ fontWeight: 700, color: T.accent, whiteSpace: 'nowrap' }}>{paid}</span>
 </div>
 ))}
 </div>
 </div>
 </div>

 {/* How a free account grows: a conversation, never a checkout. The steps
     are a real sequence, hence the numbers. Every CTA here leads to a person
     (WhatsApp / email) or to the free sign-up — there is no payment flow to
     link to, on purpose (CLAUDE.md: no Stripe, no checkout). */}
 <div data-reveal className="lp-card upg-card">
 <h3 className="lp-h3" style={{ marginBottom: 22 }}>{L.pricing.upgradeTitle}</h3>
 <ol className="upg-steps">
 {L.pricing.upgradeSteps.map(({ title, desc }, i) => (
 <li key={title} className="upg-step">
 <span className="lp-step" aria-hidden>{i + 1}</span>
 <div>
 <div className="lp-card-title">{title}</div>
 <div className="lp-card-body">{desc}</div>
 </div>
 </li>
 ))}
 </ol>
 <div className="upg-foot">
 <Link href={appHref('/signup')} className="btn-primary btn-sm">{L.pricing.ctaSignup}</Link>
 <a href={waHref(L.pricing.waPrefill)} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">{L.pricing.ctaWhatsapp}</a>
 <a href={mailHref(L.pricing.mailSubject)} className="btn-ghost btn-sm">{L.pricing.ctaEmail}</a>
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
//  5. no feature gates — backend/entitlements/plans.py.
//  6. not a black box — the #como-decide section (home and /como-funciona).
// /seguridad adds one item, the 24-hour trial erasure, which restates the
// claim the closing band already makes (L.final.trialDesc).
export function TrustSection({ decideHref, ruleDesc, extra = [] }: {
  decideHref: string
  // Replaces the last item's text where the rule is not on the same page.
  ruleDesc?: string
  extra?: Titled[]
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
export function FaqAccordion() {
 const { L } = useCopy()
 const [openFaq, setOpenFaq] = useState<number | null>(null)
 return (
 <Section>
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
 </div>
 </div>
 <p className="final-made">{L.final.madeIn}</p>
 </div>
 </section>
 )
}

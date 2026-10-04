'use client'
// The full plan's estimate calculator and the per-call API pricing, on
// /precios. Every number comes from pricingModel.ts (PROPOSED, pending the
// owner's confirmation); every word from `L.calc` / `L.api` in i18n/landing.ts.
//
// There is no checkout and nothing here takes a payment (CLAUDE.md). The three
// ways out of the calculator all lead to a person: an email with the estimate
// already written, WhatsApp, or a short form that composes that same email —
// the landing has no endpoint to post a lead to, and adding one is a new
// capability, so the form hands the message to the visitor's own mail app and
// says so.
import Link from 'next/link'
import { useId, useMemo, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { Section, Tag, H2, Lead, Check } from '@/components/landing/primitives'
import { mailHref, waHref } from '@/components/landing/contact'
import { CALC_RANGES, FREE_PLAN, FULL_PLAN, estimate, type CalcInput } from '@/components/landing/pricingModel'

type Key = keyof CalcInput
const KEYS: Key[] = ['skus', 'users', 'warehouses', 'apiCalls']

// ── Formatting ────────────────────────────────────────────────────────────────
// Grouping by hand: Intl's Spanish locales do not group four-digit numbers
// ("2500"), while the rest of the landing writes "2.500".
export function fmtNum(n: number, lang: 'es' | 'en') {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, lang === 'es' ? '.' : ',')
}
// Whole dollars print as "$59"; anything with cents keeps them ("$2,50" /
// "$2.50"). Rounding to the dollar would advertise the $2.50 API block as $3,
// and a total of 3 blocks as $8 instead of $7.50.
export function fmtMoney(n: number, lang: 'es' | 'en') {
  const cents = Math.round(n * 100)
  if (cents % 100 === 0) return `$${fmtNum(cents / 100, lang)}`
  const whole = Math.trunc(cents / 100)
  const frac = String(Math.abs(cents % 100)).padStart(2, '0')
  return `$${fmtNum(whole, lang)}${lang === 'es' ? ',' : '.'}${frac}`
}
export function fill(template: string, params: Record<string, string>) {
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? params[k] : m))
}

const INCLUDED: Record<Key, number> = {
  skus: FULL_PLAN.included.skus,
  users: FULL_PLAN.included.users,
  warehouses: FULL_PLAN.included.warehouses,
  apiCalls: FULL_PLAN.included.apiCallsPerMonth,
}

// ── Calculator ────────────────────────────────────────────────────────────────
export function PricingCalculator() {
  const { lang } = useLanguage()
  const L = LANDING[lang]
  const C = L.calc
  const uid = useId()

  // Text per field, so a box can be emptied while typing without snapping to 0.
  const [text, setText] = useState<Record<Key, string>>(() => ({
    skus: String(CALC_RANGES.skus.initial),
    users: String(CALC_RANGES.users.initial),
    warehouses: String(CALC_RANGES.warehouses.initial),
    apiCalls: String(CALC_RANGES.apiCalls.initial),
  }))
  const input: CalcInput = useMemo(() => {
    const read = (k: Key) => {
      const v = Number(text[k])
      return Number.isFinite(v) ? Math.max(CALC_RANGES[k].min, v) : CALC_RANGES[k].min
    }
    return { skus: read('skus'), users: read('users'), warehouses: read('warehouses'), apiCalls: read('apiCalls') }
  }, [text])
  const est = useMemo(() => estimate(input), [input])
  const [formOpen, setFormOpen] = useState(false)

  const total = fmtMoney(est.total, lang)
  const extras = est.lines.filter(l => l.blocks > 0)

  // The same summary goes into every channel, so what the visitor saw is what
  // we read.
  const summary = [
    ...KEYS.map(k => `${C.inputs[k].label}: ${fmtNum(input[k], lang)}`),
    '',
    `${C.base}: ${fmtMoney(est.base, lang)}`,
    ...extras.map(l => `${fill(C.lines[l.key], { n: fmtNum(l.extraUnits, lang) })} (${fmtMoney(l.amount, lang)})`),
    `${C.resultTitle}: ${total} ${L.pricing.perMonth}`,
    '',
    C.note,
  ].join('\n')
  const mailBody = fill(C.mailBody, { summary })

  const onForm = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const who = [
      `${C.form.name}: ${String(f.get('name') || '').trim()}`,
      `${C.form.company}: ${String(f.get('company') || '').trim()}`,
      `${C.form.phone}: ${String(f.get('phone') || '').trim()}`,
    ].join('\n')
    const note = String(f.get('message') || '').trim()
    window.location.href = mailHref(C.mailSubject, `${mailBody}\n\n${who}${note ? `\n\n${note}` : ''}`)
  }

  const includedHint = (k: Key) => {
    const add = FULL_PLAN.addOns[k]
    return fill(C.inputs[k].included, {
      n: fmtNum(INCLUDED[k], lang),
      price: fmtMoney(add.price, lang),
      per: fmtNum(add.per, lang),
    })
  }

  return (
    <Section id="calculadora">
      <Tag>{C.tag}</Tag>
      <H2>{C.title}</H2>
      <Lead maxWidth={660}>{C.lead}</Lead>

      <div className="pc-grid">
        <div className="pc-inputs">
          {KEYS.map(k => {
            const r = CALC_RANGES[k]
            const id = `${uid}-${k}`
            const hintId = `${id}-hint`
            return (
              <div key={k} className="pc-field">
                <div className="pc-field-head">
                  <label htmlFor={id} className="pc-label">{C.inputs[k].label}</label>
                  <input
                    id={id}
                    className="pc-number"
                    type="number"
                    inputMode="numeric"
                    min={r.min}
                    step={r.step}
                    value={text[k]}
                    aria-describedby={hintId}
                    onChange={e => setText(t => ({ ...t, [k]: e.target.value }))}
                  />
                </div>
                <input
                  className="pc-range"
                  type="range"
                  min={r.min}
                  max={r.max}
                  step={r.step}
                  value={Math.min(input[k], r.max)}
                  aria-label={C.inputs[k].label}
                  aria-describedby={hintId}
                  onChange={e => setText(t => ({ ...t, [k]: e.target.value }))}
                />
                <p id={hintId} className="pc-hint">{includedHint(k)}</p>
              </div>
            )
          })}
        </div>

        <div className="pc-result" aria-live="polite">
          {est.fitsFree && (
            <div className="pc-free">
              <Check />
              <div>
                <strong>{C.freeFits}</strong>
                <p>{fill(C.freeFitsNote, { n: fmtNum(FREE_PLAN.apiCallsPerDay, lang) })}</p>
              </div>
            </div>
          )}
          <div className="pc-result-label">{C.resultTitle}</div>
          {/* An operation that fits the free plan costs $0 — showing the full
              plan's base as "the estimate" would quote a price nobody owes. */}
          <div className="pc-total">
            <span className="pc-total-num">{est.fitsFree ? fmtMoney(0, lang) : total}</span>
            <span className="pc-total-per">{L.pricing.perMonth}</span>
          </div>
          {est.fitsFree && <p className="pc-alt">{fill(C.fullWouldBe, { total })}</p>}
          <dl className="pc-lines">
            <div><dt>{C.base}</dt><dd>{fmtMoney(est.base, lang)}</dd></div>
            {extras.length === 0
              ? <div className="pc-none"><dt>{C.noExtras}</dt><dd>{fmtMoney(0, lang)}</dd></div>
              : extras.map(l => (
                <div key={l.key}>
                  <dt>{fill(C.lines[l.key], { n: fmtNum(l.extraUnits, lang) })}</dt>
                  <dd>{fmtMoney(l.amount, lang)}</dd>
                </div>
              ))}
          </dl>
          <p className="pc-note">{C.note}</p>
          <div className="pc-ctas">
            <a href={mailHref(C.mailSubject, mailBody)} className="btn-primary btn-sm">{C.ctaEmail}</a>
            <a href={waHref(fill(C.waPrefill, { total }))} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">{C.ctaWhatsapp}</a>
            <button type="button" className="btn-ghost btn-sm" aria-expanded={formOpen} aria-controls={`${uid}-form`} onClick={() => setFormOpen(v => !v)}>
              {C.ctaForm}
            </button>
          </div>

          {formOpen && (
            <form id={`${uid}-form`} className="pc-form" onSubmit={onForm}>
              <h3 className="pc-form-title">{C.form.title}</h3>
              <p className="pc-form-lead">{C.form.lead}</p>
              <label>{C.form.name}<input name="name" autoComplete="name" required /></label>
              <label>{C.form.company}<input name="company" autoComplete="organization" /></label>
              <label>{C.form.phone}<input name="phone" type="tel" autoComplete="tel" inputMode="tel" /></label>
              <label>{C.form.message}<textarea name="message" rows={3} /></label>
              <button type="submit" className="btn-primary btn-sm">{C.form.submit}</button>
              <p className="pc-form-hint">{C.form.hint}</p>
            </form>
          )}
        </div>
      </div>
    </Section>
  )
}

// ── API pricing ───────────────────────────────────────────────────────────────
export function ApiPricing() {
  const { lang } = useLanguage()
  const A = LANDING[lang].api
  const params = {
    included: fmtNum(FULL_PLAN.included.apiCallsPerMonth, lang),
    price: fmtMoney(FULL_PLAN.addOns.apiCalls.price, lang),
    per: fmtNum(FULL_PLAN.addOns.apiCalls.per, lang),
    free: fmtNum(FREE_PLAN.apiCallsPerDay, lang),
  }
  return (
    <Section id="api" alt>
      <div className="split pc-api">
        <div>
          <Tag>{A.tag}</Tag>
          <H2>{A.title}</H2>
          <p className="lp-lead" style={{ marginBottom: 24 }}>{fill(A.lead, params)}</p>
          {/* /desarrolladores is built by a parallel workstream. */}
          <Link href="/desarrolladores" className="trust-link">{A.devLink}</Link>
        </div>
        <ul className="pc-api-list">
          {A.points.map(p => (
            <li key={p}><Check /><span>{fill(p, params)}</span></li>
          ))}
        </ul>
      </div>
    </Section>
  )
}

export const CALC_CSS = `
.pc-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 0.9fr); gap: 40px; align-items: start; }
.pc-inputs { display: flex; flex-direction: column; gap: 26px; }
.pc-field-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
.pc-label { font-size: 15px; font-weight: 600; color: var(--lp-text); }
.pc-number {
 width: 120px; min-height: 40px; padding: 8px 12px; border-radius: 10px; text-align: right;
 border: 1px solid var(--lp-border-strong); background: var(--lp-bg); color: var(--lp-text);
 font: 600 15px var(--font-brand), system-ui, sans-serif; font-variant-numeric: tabular-nums;
}
.pc-number:focus-visible, .pc-form input:focus-visible, .pc-form textarea:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; }
.pc-range { width: 100%; accent-color: var(--lp-accent); margin: 0; height: 28px; cursor: pointer; }
.pc-range:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; border-radius: 6px; }
.pc-hint { font-size: 13px; color: var(--lp-muted); line-height: 1.5; margin: 6px 0 0; }

.pc-result { position: sticky; top: 96px; border: 1px solid var(--lp-border-strong); border-radius: 16px; padding: 26px 26px 24px; background: var(--lp-bg2); }
.pc-free { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; margin-bottom: 18px; border-radius: 12px; background: var(--lp-green-bg); border: 1px solid var(--lp-green-bd); }
.pc-free svg { margin-top: 2px; }
.pc-free strong { font-size: 14px; color: var(--lp-text); }
.pc-free p { font-size: 13px; color: var(--lp-body); margin: 2px 0 0; line-height: 1.5; }
.pc-result-label { font-size: 13px; font-weight: 600; color: var(--lp-muted); }
.pc-total { display: flex; align-items: baseline; gap: 10px; margin: 4px 0 18px; }
.pc-total-num { font-family: var(--font-brand), system-ui, sans-serif; font-size: 48px; font-weight: 600; letter-spacing: -0.04em; color: var(--lp-text); line-height: 1; font-variant-numeric: tabular-nums; }
.pc-total-per { font-size: 15px; color: var(--lp-muted); }
.pc-alt { font-size: 13.5px; color: var(--lp-muted); margin: -8px 0 14px; }
.pc-lines { margin: 0 0 16px; }
.pc-lines > div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-top: 1px solid var(--lp-border); font-size: 14px; }
.pc-lines dt { color: var(--lp-body); }
.pc-lines dd { margin: 0; font-weight: 700; color: var(--lp-text); font-variant-numeric: tabular-nums; white-space: nowrap; }
.pc-none dt { color: var(--lp-muted); }
.pc-note { font-size: 13px; color: var(--lp-body); line-height: 1.6; margin: 0 0 18px; padding: 10px 12px; border-left: 3px solid var(--lp-accent); background: var(--lp-bg); border-radius: 0 8px 8px 0; }
.pc-ctas { display: flex; flex-wrap: wrap; gap: 8px; }

.pc-form { display: flex; flex-direction: column; gap: 12px; margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--lp-border); }
.pc-form-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; color: var(--lp-text); margin: 0; }
.pc-form-lead, .pc-form-hint { font-size: 13px; color: var(--lp-muted); line-height: 1.55; margin: 0; }
.pc-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--lp-body); }
.pc-form input, .pc-form textarea {
 font: 400 14.5px system-ui, -apple-system, Segoe UI, sans-serif; color: var(--lp-text);
 padding: 10px 12px; min-height: 44px; border-radius: 10px; border: 1px solid var(--lp-border-strong); background: var(--lp-bg);
}
.pc-form button { align-self: flex-start; }

.pc-api { display: grid; grid-template-columns: 1fr 1fr; gap: 56px; align-items: start; }
.pc-api-list { list-style: none; margin: 0; padding: 0; }
.pc-api-list li { display: flex; gap: 12px; align-items: flex-start; padding: 16px 0; border-top: 1px solid var(--lp-border); font-size: 15px; color: var(--lp-body); line-height: 1.6; }
.pc-api-list li svg { margin-top: 3px; }

@media (max-width: 900px) {
 .pc-grid, .pc-api { grid-template-columns: 1fr; gap: 32px; }
 .pc-result { position: static; }
}
@media (max-width: 760px) {
 .pc-result { padding: 22px 18px; }
 .pc-total-num { font-size: 40px; }
 .pc-ctas .btn-primary, .pc-ctas .btn-ghost, .pc-form button { width: 100%; }
}
`

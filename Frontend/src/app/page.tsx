'use client'
// The landing is inside LanguageProvider (see app/layout.tsx), so it reads the
// same `lang` the app does — a visitor who switches here stays switched after
// signing in. The copy itself lives in i18n/landing.ts, typed so the two
// languages cannot drift apart.
import Link from 'next/link'
import { appHref } from '@/lib/siteUrls'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { Wordmark } from '@/components/brand/Wordmark'
import { ScreenGuide } from '@/components/landing/ScreenGuide'

// Every colour on the page is a CSS variable, defined once in LANDING_CSS for
// the light theme and again for `[data-theme="dark"]`. The landing follows the
// same switch as the app (the `theme` key in localStorage, applied before
// first paint by app/layout.tsx), so a visitor who uses the app in dark sees
// the landing in dark too. Light stays the default — the Petróleo identity.
const T = {
 bg: 'var(--lp-bg)',
 bg2: 'var(--lp-bg2)',
 surface: 'var(--lp-surface)',
 border: 'var(--lp-border)',
 text: 'var(--lp-text)',
 body: 'var(--lp-body)',
 muted: 'var(--lp-muted)',
 dim: 'var(--lp-dim)',
 accent: 'var(--lp-accent)',
 accentBg: 'var(--lp-accent-bg)',
 accentBd: 'var(--lp-accent-bd)',
 // Semáforo: data colours, not decoration. Same values the landing always
 // used in light; the dark set is the app's own dark semáforo.
 green: 'var(--lp-green)',
 greenBg: 'var(--lp-green-bg)',
 greenBd: 'var(--lp-green-bd)',
 red: 'var(--lp-red)',
 amber: 'var(--lp-amber)',
}

const DISPLAY = 'var(--font-brand), system-ui, sans-serif'

// ── Nav ───────────────────────────────────────────────────────────────────────
// Two letters, not a dropdown with flags. A flag is a country and this is a
// language — Spanish is not Spain here, and English is not the United States.
// The choice persists through LanguageProvider, so it also follows the visitor
// into the app after they sign in.
function LangToggle({ lang, setLang }: { lang: 'es' | 'en'; setLang: (l: 'es' | 'en') => void }) {
 return (
  <div role="group" aria-label="Language" className="lp-lang">
   {(['es', 'en'] as const).map(code => (
    <button
     key={code}
     type="button"
     onClick={() => setLang(code)}
     aria-pressed={lang === code}
     className={lang === code ? 'is-on' : undefined}
    >{code.toUpperCase()}</button>
   ))}
  </div>
 )
}

function Nav() {
 const { lang, setLang } = useLanguage()
 const L = LANDING[lang]
 const NAV_LINKS = L.nav.links

 // Below 900px the inline link row does not fit and is hidden. It used to be
 // hidden with nothing in its place, so a phone had no way to reach Precios,
 // Industrias or any other section short of scrolling the whole page — 22,000px
 // of it. The button and sheet below only ever exist at that width.
 const [menuOpen, setMenuOpen] = useState(false)

 // The bar is transparent over the hero and turns to glass once the page
 // moves, so the first screen reads as one composition instead of a strip of
 // chrome on top of it.
 const [scrolled, setScrolled] = useState(false)
 useEffect(() => {
  const onScroll = () => setScrolled(window.scrollY > 8)
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
  return () => window.removeEventListener('scroll', onScroll)
 }, [])

 // A fixed sheet over a scrolling page: freeze the page while it is open, or
 // the content slides behind it under a thumb that meant to scroll the menu.
 useEffect(() => {
 if (!menuOpen) return
 const prev = document.body.style.overflow
 document.body.style.overflow = 'hidden'
 const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false) }
 window.addEventListener('keydown', onKey)
 return () => {
 document.body.style.overflow = prev
 window.removeEventListener('keydown', onKey)
 }
 }, [menuOpen])

 return (
 <header>
 <nav aria-label={L.nav.ariaLabel} className={`nav-shell${scrolled || menuOpen ? ' is-scrolled' : ''}`}>
 <a href="#top" aria-label="StockAI" style={{ display: 'flex', alignItems: 'center', textDecoration: 'none' }}>
 <Wordmark size={22} color={T.text} accent={T.accent} />
 </a>
 <div className="nav-links">
 {NAV_LINKS.map(([href, label]) => (
 <a key={href} href={href} className="nav-link">{label}</a>
 ))}
 </div>
 <div className="nav-cta">
 <LangToggle lang={lang} setLang={setLang} />
 <Link href={appHref('/login')} className="nav-tap">
 {L.nav.signIn}
 </Link>
 <Link href={appHref('/signup')} className="nav-signup">
 {L.nav.signUp}
 </Link>
 </div>

 {/* Only rendered at all below 900px — see .nav-burger in the stylesheet. */}
 <button
 type="button"
 className="nav-burger"
 aria-label={L.nav.menu}
 aria-expanded={menuOpen}
 onClick={() => setMenuOpen(v => !v)}
 >
 {menuOpen ? <X size={20} /> : <Menu size={20} />}
 </button>
 </nav>

 {menuOpen && (
 <div className="nav-sheet" onClick={() => setMenuOpen(false)}>
 <div className="nav-sheet-inner" onClick={e => e.stopPropagation()}>
 {NAV_LINKS.map(([href, label]) => (
 <a key={href} href={href} onClick={() => setMenuOpen(false)}>{label}</a>
 ))}
 <div className="nav-sheet-sep" />
 {/* On the narrowest phones the bar has no room for the language toggle,
     so it lives here too — switching language must never be lost. */}
 <div className="nav-sheet-lang"><LangToggle lang={lang} setLang={setLang} /></div>
 <Link href={appHref('/login')} onClick={() => setMenuOpen(false)}>{L.nav.signIn}</Link>
 <Link
 href={appHref('/signup')}
 onClick={() => setMenuOpen(false)}
 className="nav-sheet-cta"
 >
 {L.nav.signUp}
 </Link>
 </div>
 </div>
 )}
 </header>
 )
}

// ── Shared layout helpers ─────────────────────────────────────────────────────
function Section({ id, children, alt, className, style }: { id?: string; children: React.ReactNode; alt?: boolean; className?: string; style?: React.CSSProperties }) {
 return (
 <section id={id} className={`sec${alt ? ' sec-alt' : ''}${className ? ` ${className}` : ''}`} style={style}>
 <div className="sec-inner" data-reveal>{children}</div>
 </section>
 )
}

// Section label. Sentence case with a small beam dot — the section names are
// navigation (they match the menu), so they stay, but quietly.
function Tag({ children }: { children: React.ReactNode }) {
 return <div className="lp-tag"><span aria-hidden className="lp-tag-dot" />{children}</div>
}

function H2({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h2 className="lp-h2" style={style}>{children}</h2>
}

function Lead({ children, maxWidth = 600 }: { children: React.ReactNode; maxWidth?: number }) {
 return <p className="lp-lead" style={{ maxWidth }}>{children}</p>
}

function Check() {
 return (
 <svg width={16} height={16} viewBox="0 0 14 14" style={{ flexShrink: 0 }} aria-hidden>
 <circle cx={7} cy={7} r={7} style={{ fill: T.greenBg }} />
 <path d="M3.5 7 L6 9.5 L10.5 5" style={{ stroke: T.green }} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// Counterpart to Check() for "you do not need this" lists — same size, neutral.
function Dash() {
 return (
 <svg width={16} height={16} viewBox="0 0 14 14" style={{ flexShrink: 0 }} aria-hidden>
 <circle cx={7} cy={7} r={7} style={{ fill: T.surface }} />
 <path d="M4 7 L10 7" style={{ stroke: T.muted }} strokeWidth={1.5} fill="none" strokeLinecap="round" />
 </svg>
 )
}

// Sub-heading inside the long-form sections — one step below H2, same type scale.
function H3({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h3 className="lp-h3" style={style}>{children}</h3>
}

// Wide tables scroll inside their own box so the page body never scrolls sideways.
function Scroller({ minWidth, children }: { minWidth: number; children: React.ReactNode }) {
 return (
 <div className="lp-scroller">
 <div style={{ minWidth }}>{children}</div>
 </div>
 )
}

// ── Scroll reveal ─────────────────────────────────────────────────────────────
// Fades + lifts each [data-reveal] block the first time it enters the viewport,
// then stops watching it. Three deliberate properties:
//  · Content is visible by default in CSS. The hidden state is only ever applied
//    by this effect, so with JS off — or if IntersectionObserver is missing —
//    the page reads normally instead of being blank.
//  · Anything already on screen at mount (the hero) is skipped, so nothing
//    above the fold fades in on load.
//  · Only opacity and transform animate; neither triggers layout.
function useScrollReveal() {
 useEffect(() => {
 if (!('IntersectionObserver' in window)) return
 if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

 const armed: HTMLElement[] = []
 const seenPerParent = new Map<Element, number>()

 document.querySelectorAll<HTMLElement>('[data-reveal]').forEach(el => {
 if (el.getBoundingClientRect().top < window.innerHeight) return
 // Small stagger between siblings, capped so the last card in a row is not late.
 const parent = el.parentElement
 const index = parent ? seenPerParent.get(parent) ?? 0 : 0
 if (parent) seenPerParent.set(parent, index + 1)
 el.style.transitionDelay = `${Math.min(index, 3) * 80}ms`
 el.classList.add('reveal-armed')
 armed.push(el)
 })
 if (armed.length === 0) return

 // Still armed and not yet revealed. The sweep below walks this and nothing
 // else, so a page whose blocks have all appeared costs nothing per scroll.
 const pending: HTMLElement[] = [...armed]
 const drop = (el: HTMLElement) => {
 const at = pending.indexOf(el)
 if (at !== -1) pending.splice(at, 1)
 }

 const show = (el: HTMLElement) => el.classList.add('reveal-in')
 let observerWorks = false
 const observer = new IntersectionObserver((entries, obs) => {
 observerWorks = true
 entries.forEach(entry => {
 if (!entry.isIntersecting) return
 show(entry.target as HTMLElement)
 drop(entry.target as HTMLElement)
 obs.unobserve(entry.target) // reveal once; never re-animate on the way back up
 })
 }, {
 threshold: 0.04,
 // Bottom is pulled in slightly so a block reveals just after it enters rather
 // than the instant its first pixel shows.
 //
 // The top is expanded by the whole document, which makes "already scrolled
 // past" count as intersecting. This is not padding for looks: a fast scroll —
 // a trackpad fling, PageDown, dragging the scrollbar — can carry a short card
 // from below the viewport to above it between two frames, and an observer
 // watching only the viewport never sees it, so it stays invisible forever.
 // Two cards in the "qué incluye" grid did exactly that, reproducibly, and a
 // single fling past the whole page left twenty behind. The document's own
 // height is the largest jump that can exist, so nothing can outrun it.
 rootMargin: `${Math.ceil(document.documentElement.scrollHeight)}px 0px -6% 0px`,
 })

 armed.forEach(el => observer.observe(el))
 // Failsafe for an observer that never fires at all — not a deadline the reader
 // has to beat. It used to reveal everything unconditionally after 3s, which on
 // a page this tall meant the whole thing had already appeared while the reader
 // was still near the top, and nothing was left to animate. An observer that has
 // delivered even one entry is working, so the timer stands down.
 const failsafe = window.setTimeout(() => {
 if (!observerWorks) {
 armed.forEach(show)
 pending.length = 0
 }
 }, 3000)

 // Safety net, because the observer working is not the same as the observer
 // being right. `rootMargin` above is measured ONCE, at mount, and this page
 // is not done growing then: the tour screenshots load after it, and the
 // document goes from ~17,400px to ~19,100px. Blocks near the end are still
 // moving after the margin that was supposed to cover them was fixed, and
 // they can be left armed — permanently invisible, with no second scroll
 // event coming to correct it because the reader is already at the bottom.
 // Measured on the built page: jumping to the bottom left five blocks hidden
 // (the WhatsApp and AI-analyst cards, the scheduling and team-messaging
 // cards, and the whole FAQ), and an ordinary scroll to the end left one.
 //
 // So geometry is checked directly against the live layout, on scroll and on
 // resize, and anything whose top has passed the viewport bottom is shown
 // whatever the observer believed. It is deliberately the same `show` — the
 // transition still runs, so a block revealed this way animates like any
 // other. The listener removes itself once nothing is left armed, which on a
 // normal read happens long before the end of the page.
 let sweepQueued = false
 const sweep = () => {
 sweepQueued = false
 for (let i = pending.length - 1; i >= 0; i--) {
 if (pending[i].getBoundingClientRect().top < window.innerHeight) {
 show(pending[i])
 observer.unobserve(pending[i])
 pending.splice(i, 1)
 }
 }
 if (pending.length === 0) {
 window.removeEventListener('scroll', onScroll)
 window.removeEventListener('resize', onScroll)
 }
 }
 const onScroll = () => {
 if (sweepQueued) return
 sweepQueued = true
 requestAnimationFrame(sweep)
 }
 window.addEventListener('scroll', onScroll, { passive: true })
 window.addEventListener('resize', onScroll)

 // The document growing is its own event, and neither scroll nor resize
 // reports it. Land on the page, jump to the bottom before the screenshots
 // have loaded, and the browser holds the scroll position while the page
 // grows underneath — no scroll event, no resize event, and the blocks that
 // just moved below the fold stay armed. Watching the element whose height
 // IS the document height is what catches that.
 const grew = typeof ResizeObserver === 'undefined' ? null
 : new ResizeObserver(onScroll)
 grew?.observe(document.documentElement)

 return () => {
 observer.disconnect()
 grew?.disconnect()
 window.clearTimeout(failsafe)
 window.removeEventListener('scroll', onScroll)
 window.removeEventListener('resize', onScroll)
 }
 }, [])
}

// The free tier's ceilings, as advertised. MUST match
// backend/entitlements/plans.py — a landing page promising 200 SKUs while the
// product stops at 100 turns the first real import into a broken promise.
// E.164 without the '+', which is what wa.me expects.
const CONTACT_WHATSAPP = '50671862820'
// The one address every "email us" link on this page uses.
// TODO(owner): confirm contact email — usefaro.io is the old brand
const CONTACT_EMAIL = 'hola@usefaro.io'

const waHref = (text: string) => `https://wa.me/${CONTACT_WHATSAPP}?text=${encodeURIComponent(text)}`
const mailHref = (subject?: string) =>
  `mailto:${CONTACT_EMAIL}${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`

// A guided tour of the product, chapter by chapter — rendered by
// components/landing/ScreenGuide as an opt-in dialog, not in the main flow.
//
// The chapters are the app's OWN sidebar groups, in the app's own order: it is
// the map this reader will have five minutes after signing up, so the structure
// carries information rather than decorating. Each chapter says WHEN you use
// it, which is the question a buyer is actually asking.
//
// Captured 2026-08-23 on the seeded demo tenant, light theme, 3200x2000. Rule
// for whoever retakes them: the tenant must have DATA on every screen. An empty
// state on a landing page reads as an unfinished product. And no modal or
// tutorial overlay — two of the first batch caught one and had to be redone.

// ── Stylesheet ────────────────────────────────────────────────────────────────
// Injected with dangerouslySetInnerHTML rather than as text children: React
// escapes quotes and ampersands in a text child on the server but not on the
// client, so the two copies stopped matching and hydration failed for the
// whole page over one quoted word in a comment. As raw HTML it is passed
// through byte for byte on both sides.
//
// Motion budget. One orchestrated moment — the hero assembling on load and a
// single beam of light crossing the product frame — plus the scroll reveal
// and hover answers. Everything animates transform and opacity only, and
// prefers-reduced-motion turns all of it off (content fully visible, static).
const LANDING_CSS = `
:root, [data-theme="light"] {
 --lp-bg: #ffffff;
 --lp-bg2: #F5F7F6;
 --lp-surface: #EEF2F1;
 --lp-border: #DFE6E4;
 --lp-border-strong: #C2CFCC;
 --lp-text: #16262A;
 --lp-body: #3A4D50;
 --lp-muted: #566A6D;
 --lp-dim: #6F8285;
 --lp-accent: #0F766E;
 --lp-accent-bg: rgba(15,118,110,0.08);
 --lp-accent-bd: rgba(15,118,110,0.24);
 --lp-beam: #4CC3B5;
 --lp-cta-bg: #0C3A40;
 --lp-cta-fg: #ffffff;
 --lp-cta-hover: #0F4C53;
 --lp-nav: rgba(255,255,255,0.74);
 --lp-glass: rgba(255,255,255,0.66);
 --lp-shadow: rgba(12,58,64,0.16);
 --lp-strip: #0C3A40;
 --lp-grid: rgba(12,58,64,0.10);
 --lp-glow-a: rgba(76,195,181,0.30);
 --lp-glow-b: rgba(15,118,110,0.16);
 --lp-red: #dc2626;
 --lp-amber: #d97706;
 --lp-green: #059669;
 --lp-green-bg: #f0fdf4;
 --lp-green-bd: #a7f3d0;
}
[data-theme="dark"] {
 --lp-bg: #0A1517;
 --lp-bg2: #0D1B1E;
 --lp-surface: #152528;
 --lp-border: #1E3236;
 --lp-border-strong: #2C464B;
 --lp-text: #E3EBEA;
 --lp-body: #B2C3C1;
 --lp-muted: #93A8A6;
 --lp-dim: #7D9395;
 --lp-accent: #2BA79A;
 --lp-accent-bg: rgba(43,167,154,0.12);
 --lp-accent-bd: rgba(43,167,154,0.34);
 --lp-beam: #4CC3B5;
 --lp-cta-bg: #2BA79A;
 --lp-cta-fg: #04201D;
 --lp-cta-hover: #35BAAC;
 --lp-nav: rgba(10,21,23,0.72);
 --lp-glass: rgba(16,29,32,0.66);
 --lp-shadow: rgba(0,0,0,0.45);
 --lp-strip: #0B2E33;
 --lp-grid: rgba(227,235,234,0.07);
 --lp-glow-a: rgba(43,167,154,0.22);
 --lp-glow-b: rgba(76,195,181,0.10);
 --lp-red: #ef4444;
 --lp-amber: #f59e0b;
 --lp-green: #22c55e;
 --lp-green-bg: rgba(34,197,94,0.12);
 --lp-green-bd: rgba(34,197,94,0.32);
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--lp-bg); color: var(--lp-text); font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
html { scroll-behavior: smooth; }
.lp { overflow-x: clip; background: var(--lp-bg); color: var(--lp-text); -webkit-font-smoothing: antialiased; }
.lp ::selection { background: rgba(76,195,181,0.30); }
.lp a:focus-visible, .lp button:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 3px; border-radius: 8px; }

/* The nav is fixed, so an anchor jump parks the target under it: click Precio
   in the menu and the eyebrow and half the headline are behind the bar. The
   offset is the bar height plus a little air, and it belongs on the TARGET,
   not on the scroll — scroll-margin is the one mechanism that also fixes the
   keyboard focus jump and the browser restoring a #hash on reload. */
section[id], #demo { scroll-margin-top: 88px; }
@media (max-width: 900px) { section[id], #demo { scroll-margin-top: 76px; } }

/* ── Nav ── */
.nav-shell {
 position: fixed; top: 0; left: 0; right: 0; z-index: 100;
 display: flex; align-items: center; justify-content: space-between;
 padding: 0 48px; height: 64px;
 background: transparent; border-bottom: 1px solid transparent;
 transition: background-color 240ms ease, border-color 240ms ease, backdrop-filter 240ms ease;
}
.nav-shell.is-scrolled {
 background: var(--lp-nav);
 backdrop-filter: saturate(160%) blur(14px); -webkit-backdrop-filter: saturate(160%) blur(14px);
 border-bottom-color: var(--lp-border);
}
.nav-links { display: flex; align-items: center; gap: 4px; }
.nav-link {
 font-size: 13.5px; color: var(--lp-muted); text-decoration: none; font-weight: 500;
 padding: 8px 12px; border-radius: 8px; transition: color 160ms ease, background-color 160ms ease;
}
.nav-link:hover { color: var(--lp-text); background: var(--lp-accent-bg); }
.nav-cta { display: flex; align-items: center; gap: 14px; }
.nav-tap { font-size: 13.5px; font-weight: 600; color: var(--lp-muted); text-decoration: none; transition: color 160ms ease; }
.nav-tap:hover { color: var(--lp-text); }
.nav-signup {
 display: inline-flex; align-items: center; font-size: 13.5px; font-weight: 600;
 color: var(--lp-cta-fg); text-decoration: none; padding: 9px 18px; border-radius: 10px;
 background: var(--lp-cta-bg); transition: background-color 160ms ease, transform 160ms ease;
}
.nav-signup:hover { background: var(--lp-cta-hover); transform: translateY(-1px); }
.lp-lang { display: flex; align-items: center; gap: 2px; border: 1px solid var(--lp-border); border-radius: 9px; padding: 2px; background: var(--lp-glass); }
.lp-lang button {
 border: none; cursor: pointer; border-radius: 6px; padding: 4px 9px;
 font-size: 11.5px; font-weight: 700; letter-spacing: 0.03em;
 background: transparent; color: var(--lp-muted); transition: background-color 160ms ease, color 160ms ease;
}
.lp-lang button.is-on { background: var(--lp-text); color: var(--lp-bg); }

/* The mobile menu button. Hidden above 900px, where the inline link row is
   the navigation; below it, it is the only navigation there is. */
.nav-burger { display: none; }

/* ── Buttons ── */
.btn-primary, .btn-ghost {
 position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
 padding: 13px 24px; border-radius: 12px; cursor: pointer; text-decoration: none;
 font-size: 14.5px; font-weight: 700; letter-spacing: -0.005em; white-space: nowrap;
 transition: transform 200ms cubic-bezier(0.16,1,0.3,1), background-color 160ms ease, border-color 160ms ease, color 160ms ease, box-shadow 200ms ease;
}
.btn-primary {
 border: none; color: var(--lp-cta-fg); background: var(--lp-cta-bg);
 box-shadow: 0 1px 0 rgba(255,255,255,0.12) inset, 0 8px 24px -10px var(--lp-shadow);
}
.btn-primary:hover { background: var(--lp-cta-hover); transform: translateY(-1px); box-shadow: 0 1px 0 rgba(255,255,255,0.12) inset, 0 14px 30px -12px var(--lp-shadow); }
.btn-ghost {
 color: var(--lp-text); border: 1px solid var(--lp-border-strong); background: var(--lp-glass);
 backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
}
.btn-ghost:hover { border-color: var(--lp-accent); color: var(--lp-accent); transform: translateY(-1px); }
.btn-primary:active, .btn-ghost:active { transform: translateY(0); }
.btn-sm { padding: 11px 20px; font-size: 13.5px; border-radius: 10px; }

/* ── Type ── */
.lp-h1 {
 font-family: var(--font-brand), system-ui, sans-serif;
 font-size: clamp(38px, 6.2vw, 72px); font-weight: 600; line-height: 1.02;
 letter-spacing: -0.045em; color: var(--lp-text); margin: 0 0 22px; max-width: 920px;
}
.lp-h2 {
 font-family: var(--font-brand), system-ui, sans-serif;
 font-size: clamp(28px, 3.6vw, 42px); font-weight: 600; line-height: 1.1;
 letter-spacing: -0.035em; color: var(--lp-text); margin: 0 0 16px; text-wrap: balance;
}
.lp-h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 20px; font-weight: 600; color: var(--lp-text); margin: 0 0 16px; letter-spacing: -0.02em; line-height: 1.3; }
.lp-lead { font-size: 16.5px; color: var(--lp-body); line-height: 1.7; margin: 0 0 48px; text-wrap: pretty; }
.lp-tag {
 display: inline-flex; align-items: center; gap: 8px; margin-bottom: 18px;
 padding: 5px 12px 5px 10px; border-radius: 999px;
 background: var(--lp-accent-bg); border: 1px solid var(--lp-accent-bd);
 font-size: 12.5px; font-weight: 600; color: var(--lp-accent); letter-spacing: 0;
}
.lp-tag-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--lp-beam); box-shadow: 0 0 0 3px rgba(76,195,181,0.20); }
.lp-label { font-size: 12px; font-weight: 600; color: var(--lp-dim); letter-spacing: 0.01em; }

/* ── Sections ── */
.sec { position: relative; background: var(--lp-bg); padding: 104px 0; }
.sec-alt { background: var(--lp-bg2); }
.sec-alt::before {
 content: ''; position: absolute; inset: 0 0 auto; height: 1px;
 background: linear-gradient(90deg, transparent, var(--lp-border) 20%, var(--lp-border) 80%, transparent);
}
.sec-inner { position: relative; max-width: 1120px; margin: 0 auto; padding: 0 48px; }

/* Cards. Hover lifts by a pixel or two and warms the border; the shadow
   lives on a pseudo-element so only its opacity animates. */
.lp-card {
 position: relative; background: var(--lp-bg); border: 1px solid var(--lp-border);
 border-radius: 14px; padding: 24px 26px;
 transition: transform 260ms cubic-bezier(0.16,1,0.3,1), border-color 200ms ease;
}
.lp-card::after {
 content: ''; position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
 box-shadow: 0 18px 40px -22px var(--lp-shadow); opacity: 0; transition: opacity 260ms ease;
}
.lp-card:hover { transform: translateY(-2px); border-color: var(--lp-border-strong); }
.lp-card:hover::after { opacity: 1; }
.lp-card-soft { background: var(--lp-bg2); }
.sec-alt .lp-card { background: var(--lp-bg); }
.lp-card-title { font-size: 15px; font-weight: 700; color: var(--lp-text); margin-bottom: 8px; line-height: 1.4; letter-spacing: -0.01em; }
.lp-card-body { font-size: 13.5px; color: var(--lp-body); line-height: 1.68; }
.lp-bar { width: 28px; height: 3px; border-radius: 2px; margin-bottom: 18px; background: linear-gradient(90deg, var(--lp-accent), var(--lp-beam)); }
.lp-step {
 width: 38px; height: 38px; border-radius: 11px; flex-shrink: 0;
 display: flex; align-items: center; justify-content: center;
 font-family: var(--font-brand), system-ui, sans-serif; font-size: 14px; font-weight: 700;
 color: var(--lp-accent); background: var(--lp-accent-bg); border: 1px solid var(--lp-accent-bd);
}

/* ── Hero ── */
.hero-sec { position: relative; min-height: 100vh; padding-top: 136px; background: var(--lp-bg); display: flex; flex-direction: column; align-items: center; isolation: isolate; overflow: hidden; }
/* Backdrop: a dot grid that fades out toward the edges, and two soft glows
   that drift very slowly. Radial gradients instead of filter:blur, so the
   drift is a pure compositor transform. */
.hero-bg { position: absolute; inset: 0; z-index: -1; pointer-events: none; }
.hero-grid {
 position: absolute; inset: 0;
 background-image: radial-gradient(var(--lp-grid) 1px, transparent 1px);
 background-size: 22px 22px;
 -webkit-mask-image: radial-gradient(ellipse 70% 55% at 50% 30%, black 30%, transparent 75%);
 mask-image: radial-gradient(ellipse 70% 55% at 50% 30%, black 30%, transparent 75%);
}
.hero-glow { position: absolute; border-radius: 50%; will-change: transform; }
.hero-glow-a { width: 900px; height: 700px; left: 50%; top: 22%; margin-left: -450px; background: radial-gradient(closest-side, var(--lp-glow-a), transparent); animation: lp-drift-a 22s ease-in-out infinite alternate; }
.hero-glow-b { width: 700px; height: 560px; right: -220px; top: -160px; background: radial-gradient(closest-side, var(--lp-glow-b), transparent); animation: lp-drift-b 26s ease-in-out infinite alternate; }
@keyframes lp-drift-a { from { transform: translate3d(-6%, 0, 0) scale(1); } to { transform: translate3d(6%, -4%, 0) scale(1.08); } }
@keyframes lp-drift-b { from { transform: translate3d(0, 0, 0); } to { transform: translate3d(-12%, 10%, 0); } }

.hero-inner { max-width: 1120px; width: 100%; margin: 0 auto; padding: 0 48px; }
.hero-eyebrow {
 display: inline-flex; align-items: center; gap: 9px; margin-bottom: 26px;
 padding: 6px 14px 6px 8px; border-radius: 999px;
 background: var(--lp-glass); border: 1px solid var(--lp-border);
 backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
 font-size: 13px; font-weight: 600; color: var(--lp-body);
}
.hero-eyebrow-dot { position: relative; width: 18px; height: 18px; border-radius: 50%; background: var(--lp-accent-bg); display: inline-flex; align-items: center; justify-content: center; }
.hero-eyebrow-dot::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--lp-green); }
.hero-lead { font-size: clamp(16px, 1.6vw, 19px); color: var(--lp-body); line-height: 1.65; max-width: 590px; margin: 0 0 36px; text-wrap: pretty; }
.hero-ctas { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hero-note { display: flex; align-items: center; gap: 8px; margin: 16px 0 64px; font-size: 13px; color: var(--lp-muted); line-height: 1.5; }

/* The product frame. A 1px gradient rim, a chrome bar, the real screenshot,
   a glow underneath, and one sweep of light across it after it lands. */
.hero-stage { position: relative; perspective: 1800px; }
.hero-stage::before {
 content: ''; position: absolute; left: 6%; right: 6%; top: 12%; bottom: -4%; z-index: -1;
 background: radial-gradient(closest-side, var(--lp-glow-a), transparent); border-radius: 50%;
}
.lp-frame {
 position: relative; border-radius: 18px; padding: 1px;
 background: linear-gradient(160deg, var(--lp-border-strong), var(--lp-border) 40%, rgba(76,195,181,0.55));
 box-shadow: 0 40px 90px -30px var(--lp-shadow), 0 12px 30px -18px var(--lp-shadow);
 transform-origin: 50% 0;
}
.lp-frame-in { border-radius: 17px; overflow: hidden; background: var(--lp-bg2); position: relative; }
.lp-chrome { display: flex; align-items: center; gap: 7px; padding: 12px 16px; border-bottom: 1px solid var(--lp-border); background: var(--lp-glass); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.lp-chrome i { width: 10px; height: 10px; border-radius: 50%; background: var(--lp-border-strong); display: block; }
.lp-chrome span { margin-left: 10px; font-size: 12px; color: var(--lp-dim); font-weight: 500; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.lp-frame img { display: block; width: 100%; height: auto; }
.lp-sheen { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.lp-sheen::before {
 content: ''; position: absolute; top: 0; bottom: 0; left: 0; width: 45%;
 background: linear-gradient(100deg, transparent, rgba(255,255,255,0.0) 20%, rgba(255,255,255,0.40) 50%, rgba(255,255,255,0) 80%, transparent);
 transform: translateX(-120%); opacity: 0;
}

.hero-pills { display: flex; align-items: center; gap: 10px; margin-top: 40px; padding-bottom: 80px; flex-wrap: wrap; }
.hero-pill { font-size: 12.5px; font-weight: 500; color: var(--lp-muted); padding: 5px 13px; border-radius: 999px; border: 1px solid var(--lp-border); background: var(--lp-glass); }

/* Load sequence. Short, staggered, one time. */
.lp-rise { animation: lp-rise 760ms cubic-bezier(0.16,1,0.3,1) both; }
.lp-d1 { animation-delay: 60ms; } .lp-d2 { animation-delay: 140ms; } .lp-d3 { animation-delay: 220ms; } .lp-d4 { animation-delay: 300ms; } .lp-d5 { animation-delay: 380ms; }
@keyframes lp-rise { from { opacity: 0; transform: translate3d(0, 18px, 0); } to { opacity: 1; transform: none; } }
.lp-frame.lp-land { animation: lp-land 1200ms cubic-bezier(0.16,1,0.3,1) 320ms both; }
@keyframes lp-land { from { opacity: 0; transform: translate3d(0, 48px, 0) rotateX(14deg) scale(0.96); } to { opacity: 1; transform: none; } }
.lp-land .lp-sheen::before { animation: lp-sheen 1500ms cubic-bezier(0.4,0,0.2,1) 1350ms 1 both; }
@keyframes lp-sheen { 0% { opacity: 0; transform: translateX(-120%); } 15% { opacity: 1; } 85% { opacity: 1; } 100% { opacity: 0; transform: translateX(260%); } }

/* ── Stats strip ── */
.strip-shell { position: relative; background: var(--lp-strip); padding: 48px 48px; overflow: hidden; isolation: isolate; }
.strip-shell::before {
 content: ''; position: absolute; inset: 0; z-index: -1;
 background: radial-gradient(60% 140% at 15% 0%, rgba(76,195,181,0.22), transparent 60%), radial-gradient(50% 120% at 100% 100%, rgba(15,118,110,0.35), transparent 60%);
}
.strip-shell::after { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 1px; background: linear-gradient(90deg, transparent, rgba(76,195,181,0.7), transparent); }
.strip-grid { max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: repeat(4, 1fr); }
.strip-cell { text-align: center; padding: 0 28px; border-right: 1px solid rgba(255,255,255,0.10); }
.strip-cell:last-child { border-right: none; }
.strip-value { font-family: var(--font-brand), system-ui, sans-serif; font-size: 44px; font-weight: 600; color: #fff; letter-spacing: -0.04em; margin-bottom: 8px; line-height: 1; }
.strip-label { font-size: 13px; color: rgba(231,240,239,0.66); line-height: 1.45; max-width: 26ch; margin: 0 auto; }

/* ── Tour ── */
.tour-chapter { border-top: 1px solid var(--lp-border); padding-top: 18px; margin-bottom: 44px; max-width: 640px; position: relative; }
.tour-chapter::before { content: ''; position: absolute; top: -1px; left: 0; width: 64px; height: 2px; background: linear-gradient(90deg, var(--lp-accent), var(--lp-beam)); }
.tour-shot {
 direction: ltr; position: relative; border-radius: 14px; padding: 1px; overflow: hidden;
 background: linear-gradient(160deg, var(--lp-border-strong), var(--lp-border) 50%, var(--lp-accent-bd));
 box-shadow: 0 24px 50px -30px var(--lp-shadow);
}
.tour-shot-in { border-radius: 13px; overflow: hidden; background: var(--lp-bg2); }
.tour-shot img { display: block; width: 100%; height: auto; transition: transform 700ms cubic-bezier(0.16,1,0.3,1); transform-origin: 50% 30%; }
.tour-row:hover .tour-shot img { transform: scale(1.015); }
.tour-dot { flex-shrink: 0; width: 6px; height: 6px; border-radius: 999px; background: var(--lp-accent); margin-top: 8px; }

/* ── Tables ── */
.lp-scroller { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.lp-table { border-radius: 14px; overflow: hidden; border: 1px solid var(--lp-border); background: var(--lp-bg); }
.lp-table-head { background: var(--lp-surface); padding: 13px 24px; border-bottom: 1px solid var(--lp-border); }
.lp-table-row { padding: 16px 24px; align-items: center; border-bottom: 1px solid var(--lp-border); transition: background-color 160ms ease; }
.lp-table-row:last-child { border-bottom: none; }
.lp-table-row:hover { background: var(--lp-bg2); }
.lp-signal { display: inline-flex; align-items: center; gap: 9px; font-size: 12.5px; font-weight: 800; letter-spacing: 0.02em; }
.lp-signal i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 16%, transparent); display: block; }

/* ── Industries tabs ── */
.case-tabs { display: inline-flex; gap: 4px; margin-bottom: 28px; flex-wrap: wrap; padding: 4px; border-radius: 12px; background: var(--lp-surface); border: 1px solid var(--lp-border); max-width: 100%; }
.case-tab {
 all: unset; cursor: pointer; display: inline-flex; align-items: center; padding: 8px 16px; border-radius: 9px;
 font-size: 13px; font-weight: 600; color: var(--lp-muted); transition: background-color 180ms ease, color 180ms ease, box-shadow 180ms ease;
}
.case-tab:hover { color: var(--lp-text); }
.case-tab.is-on { background: var(--lp-bg); color: var(--lp-accent); box-shadow: 0 1px 2px rgba(12,58,64,0.10), 0 0 0 1px var(--lp-border); }
.case-tab:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; }
.lp-swap { animation: lp-swap 420ms cubic-bezier(0.16,1,0.3,1) both; }
@keyframes lp-swap { from { opacity: 0; transform: translate3d(0, 8px, 0); } to { opacity: 1; transform: none; } }

/* ── Pricing ── */
.price-card { padding: 30px 28px; border-radius: 16px; }
.price-card.is-paid { border-color: var(--lp-accent-bd); background: linear-gradient(180deg, var(--lp-accent-bg), var(--lp-bg) 55%); }
.price-amount { font-family: var(--font-brand), system-ui, sans-serif; font-size: 34px; font-weight: 600; color: var(--lp-text); letter-spacing: -0.03em; margin-bottom: 6px; line-height: 1.1; }
.price-row { display: flex; justify-content: space-between; gap: 12px; font-size: 13.5px; color: var(--lp-body); border-bottom: 1px solid var(--lp-border); padding-bottom: 9px; }
.price-row:last-child { border-bottom: none; }

/* ── FAQ ── */
.faq-q {
 all: unset; cursor: pointer; width: 100%; box-sizing: border-box; display: flex; justify-content: space-between; align-items: center;
 padding: 22px 0; gap: 16px; min-height: 44px;
}
.faq-q:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; border-radius: 6px; }
.faq-q-text { font-size: 15px; font-weight: 600; color: var(--lp-text); line-height: 1.45; text-align: left; transition: color 160ms ease; }
.faq-q:hover .faq-q-text { color: var(--lp-accent); }
.faq-icon {
 flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%; background: var(--lp-surface); border: 1px solid var(--lp-border);
 display: flex; align-items: center; justify-content: center; font-size: 17px; color: var(--lp-muted); line-height: 1;
 transition: transform 320ms cubic-bezier(0.16,1,0.3,1), background-color 200ms ease, color 200ms ease;
}
.faq-icon.is-open { transform: rotate(45deg); background: var(--lp-accent-bg); color: var(--lp-accent); }
.faq-a { font-size: 14.5px; color: var(--lp-body); line-height: 1.72; padding-bottom: 22px; max-width: 64ch; animation: lp-swap 360ms cubic-bezier(0.16,1,0.3,1) both; }

/* ── Footer ── */
.foot-head { font-size: 13px; font-weight: 700; color: var(--lp-text); margin-bottom: 14px; }
.foot-link { display: block; font-size: 13.5px; color: var(--lp-muted); text-decoration: none; margin-bottom: 10px; transition: color 160ms ease; width: fit-content; max-width: 100%; }
.foot-link:hover { color: var(--lp-accent); }

/* Scroll reveal. The resting state is visible; useScrollReveal adds
   .reveal-armed only after it confirms it can also remove it. */
.reveal-armed { opacity: 0; transform: translate3d(0, 22px, 0); }
.reveal-in {
 opacity: 1; transform: none;
 transition: opacity 720ms cubic-bezier(0.16, 1, 0.3, 1), transform 820ms cubic-bezier(0.16, 1, 0.3, 1);
}

/* Narrow viewports: collapse the fixed grid columns instead of overflowing.
   Nothing here changes colour, type or shadow — only how many columns fit. */
@media (max-width: 1024px) {
 .nav-shell { padding: 0 28px; }
 .nav-links { gap: 0; margin: 0 12px; }
 .nav-link { padding: 8px 8px; font-size: 13px; }
 .nav-cta { gap: 10px; }
}
@media (max-width: 900px) {
 .nav-links { display: none; }
 .nav-shell { padding: 0 20px; height: 60px; }
 .grid-2, .grid-3 { grid-template-columns: 1fr !important; }
 .split { grid-template-columns: 1fr !important; gap: 32px !important; }
 .faq-side { position: static !important; }

 .nav-burger {
 display: flex; align-items: center; justify-content: center;
 width: 44px; height: 44px; margin-right: -10px;
 background: none; border: none; padding: 0; cursor: pointer;
 color: var(--lp-text); border-radius: 8px;
 }
 .nav-burger:focus-visible { outline: 2px solid var(--lp-text); outline-offset: 2px; }

 /* Both auth actions in a 20px gutter is tight, and the sheet carries both
    anyway, so only the primary one stays up here — signing up should not
    require opening a menu first.
    With the link row hidden, space-between is left with three children and
    strands the button in the middle of the bar, which reads as a mistake.
    Pushing it right parks it beside the menu button, where it belongs. */
 .nav-cta .nav-tap { display: none; }
 .nav-cta { margin-left: auto; margin-right: 10px; gap: 10px; }

 .nav-sheet {
 position: fixed; inset: 60px 0 0; z-index: 99;
 background: rgba(10, 21, 23, 0.35);
 backdrop-filter: blur(2px); -webkit-backdrop-filter: blur(2px);
 animation: nav-sheet-in 180ms ease-out both;
 }
 .nav-sheet-inner {
 background: var(--lp-bg); border-bottom: 1px solid var(--lp-border);
 padding: 8px 20px 20px;
 display: flex; flex-direction: column;
 box-shadow: 0 18px 40px -24px rgba(10,21,23,0.45);
 animation: nav-sheet-slide 260ms cubic-bezier(0.16, 1, 0.3, 1) both;
 }
 .nav-sheet-inner a {
 display: flex; align-items: center; min-height: 48px;
 font-size: 15px; font-weight: 500; color: var(--lp-body); text-decoration: none;
 border-bottom: 1px solid var(--lp-border);
 }
 .nav-sheet-inner a:last-child { border-bottom: none; }
 .nav-sheet-sep { height: 12px; }
 .nav-sheet-lang { display: flex; align-items: center; min-height: 48px; border-bottom: 1px solid var(--lp-border); }
 .nav-sheet-lang .lp-lang button { min-height: 44px; min-width: 52px; }
 .nav-sheet-inner a.nav-sheet-cta {
 justify-content: center; margin-top: 12px; border-bottom: none;
 background: var(--lp-cta-bg); color: var(--lp-cta-fg); font-weight: 700; border-radius: 12px;
 }
 .tour-row { grid-template-columns: 1fr !important; direction: ltr !important; gap: 22px !important; margin-bottom: 48px !important; }
 .strip-grid { grid-template-columns: repeat(2, 1fr); row-gap: 32px; }
 .strip-cell { border-right: none; padding: 0 12px; }
}
@keyframes nav-sheet-in { from { opacity: 0 } to { opacity: 1 } }
@keyframes nav-sheet-slide { from { transform: translate3d(0, -10px, 0); opacity: 0.6 } to { transform: none; opacity: 1 } }

@media (max-width: 760px) {
 .sec-inner, .hero-inner { padding: 0 20px; }
 .strip-shell { padding: 40px 20px; }
 .strip-value { font-size: 36px; }
 .card-pad, .lp-card { padding: 22px 20px !important; }
 .footer-shell { padding: 44px 20px !important; }
 .footer-grid { grid-template-columns: 1fr 1fr !important; gap: 28px !important; }
 .footer-bottom { flex-direction: column; align-items: flex-start !important; gap: 8px; }
 .lp-lead { font-size: 15.5px; margin-bottom: 36px; }
 .hero-glow-a { width: 560px; height: 520px; margin-left: -280px; }

 /* 104px of air above and below every section is a desktop rhythm. Stacked
    into one column on a phone it turned the page into 22,000px — roughly
    29 screens — and the gaps read as the page having ended. */
 .sec { padding: 60px 0; }

 /* The hero reserves a full viewport plus a nav offset, which on a short
    phone screen pushes the first real section below two swipes of mostly
    empty space. */
 .hero-sec { min-height: 0; padding-top: 100px; }
 .hero-note { margin-bottom: 44px; align-items: flex-start; }
 .hero-pills { padding-bottom: 56px; margin-top: 28px; }
 .lp-frame, .lp-frame-in { border-radius: 12px; }

 /* Anything tappable clears 44px. These are 36px chips and 17-20px inline
    links today — fine with a cursor, a coin toss with a thumb. */
 .btn-primary, .btn-ghost { min-height: 50px; padding: 14px 20px; width: 100%; white-space: normal; text-align: center; }
 .hero-ctas { flex-direction: column; align-items: stretch; }
 .case-tabs { display: flex; }
 .case-tab { min-height: 44px; box-sizing: border-box; }
 .lp-lang button { min-height: 40px; min-width: 42px; }
 .nav-signup { min-height: 44px; padding: 0 14px; }
 .foot-link { display: flex; align-items: center; min-height: 44px; margin-bottom: 0; }
 .cta-link { min-height: 44px; display: inline-flex; align-items: center; }
 /* Links that sit inside flowing paragraph text are left alone on purpose:
    padding them to 44px would tear holes in the line spacing around them,
    and the paragraph itself is the target the reader is already aiming at. */
}
@media (max-width: 380px) {
 .nav-cta .lp-lang { display: none; }
}

/* ── Pricing: promises and how it grows ── */
.no-strings { list-style: none; margin: -24px 0 36px; padding: 0; display: flex; flex-wrap: wrap; gap: 10px 22px; }
.no-strings li { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: var(--lp-text); }
.upg-card { max-width: 880px; padding: 30px 32px; border-radius: 16px; }
.upg-steps { list-style: none; margin: 0 0 26px; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; position: relative; }
/* The thread between the three numbers: it is a sequence, so it reads as one. */
.upg-steps::before { content: ''; position: absolute; top: 19px; left: 38px; right: 12%; height: 1px; background: linear-gradient(90deg, var(--lp-accent-bd), var(--lp-border)); }
.upg-step { display: flex; flex-direction: column; gap: 14px; position: relative; }
.upg-step .lp-step { background: var(--lp-bg); position: relative; }
.sec-alt .upg-step .lp-step { background: var(--lp-bg); }
.upg-foot { display: flex; flex-wrap: wrap; gap: 10px; padding-top: 22px; border-top: 1px solid var(--lp-border); }

/* ── Trust ── */
.trust-list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 40px; }
.trust-item { padding: 22px 0 24px; border-top: 1px solid var(--lp-border); position: relative; }
.trust-item::before { content: ''; position: absolute; top: -1px; left: 0; width: 28px; height: 2px; background: linear-gradient(90deg, var(--lp-accent), var(--lp-beam)); }
.trust-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 0 0 8px; line-height: 1.35; }
.trust-desc { font-size: 14px; color: var(--lp-body); line-height: 1.68; margin: 0; }
.trust-link { display: inline-flex; align-items: center; min-height: 32px; margin-top: 8px; font-size: 13.5px; font-weight: 600; color: var(--lp-accent); text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }

/* ── Closing band ── */
.final-sec { position: relative; background: var(--lp-strip); padding: 96px 0 88px; overflow: hidden; isolation: isolate; scroll-margin-top: 88px; }
.final-sec::before {
 content: ''; position: absolute; inset: 0; z-index: -1;
 background: radial-gradient(55% 120% at 0% 0%, rgba(76,195,181,0.24), transparent 60%), radial-gradient(45% 110% at 100% 100%, rgba(15,118,110,0.40), transparent 60%);
}
.final-sec::after { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 1px; background: linear-gradient(90deg, transparent, rgba(76,195,181,0.7), transparent); }
.final-inner { max-width: 1120px; margin: 0 auto; padding: 0 48px; }
.final-title { color: #fff; max-width: 18ch; }
.final-lead { font-size: 16.5px; color: rgba(231,240,239,0.74); line-height: 1.65; margin: 0 0 44px; max-width: 56ch; }
.final-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0; border-top: 1px solid rgba(255,255,255,0.14); }
.final-path { padding: 28px 28px 8px 0; display: flex; flex-direction: column; align-items: flex-start; }
.final-path + .final-path { padding-left: 28px; border-left: 1px solid rgba(255,255,255,0.10); }
.final-path h3 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 20px; font-weight: 600; letter-spacing: -0.02em; color: #fff; margin: 0 0 10px; }
.final-path p { font-size: 14px; color: rgba(231,240,239,0.72); line-height: 1.68; margin: 0 0 22px; flex: 1; }
.final-btns { display: flex; flex-wrap: wrap; gap: 8px; }
.final-btn {
 display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 18px; border-radius: 11px;
 font-size: 13.5px; font-weight: 700; text-decoration: none; color: #fff;
 border: 1px solid rgba(255,255,255,0.28); background: rgba(255,255,255,0.04);
 transition: background-color 160ms ease, border-color 160ms ease, transform 200ms cubic-bezier(0.16,1,0.3,1);
}
.final-btn:hover { border-color: #4CC3B5; background: rgba(76,195,181,0.12); transform: translateY(-1px); }
.final-btn.is-main { background: #fff; color: #0C3A40; border-color: #fff; }
.final-btn.is-main:hover { background: #E3F4F1; }
.lp .final-btn:focus-visible { outline-color: #4CC3B5; }
.final-made { margin: 40px 0 0; font-size: 13px; color: rgba(231,240,239,0.6); }

@media (max-width: 900px) {
 .trust-list { grid-template-columns: 1fr; }
 .upg-steps { grid-template-columns: 1fr; gap: 18px; }
 .upg-steps::before { top: 19px; bottom: 19px; left: 19px; right: auto; width: 1px; height: auto; background: linear-gradient(180deg, var(--lp-accent-bd), var(--lp-border)); }
 .upg-step { flex-direction: row; }
 .final-grid { grid-template-columns: 1fr; }
 .final-path, .final-path + .final-path { padding: 24px 0 8px; border-left: none; }
 .final-path + .final-path { border-top: 1px solid rgba(255,255,255,0.10); }
}
@media (max-width: 760px) {
 .final-sec { padding: 64px 0 56px; }
 .final-inner { padding: 0 20px; }
 .upg-card { padding: 22px 20px !important; }
 .upg-foot .btn-primary, .upg-foot .btn-ghost { width: 100%; }
 .final-btn, .final-btns { width: 100%; }
 .no-strings { margin-top: -16px; }
}

/* Reduced motion: everything visible and still. */
@media (prefers-reduced-motion: reduce) {
 html { scroll-behavior: auto; }
 .reveal-armed, .reveal-in { opacity: 1 !important; transform: none !important; transition: none !important; }
 .lp-rise, .lp-frame.lp-land, .lp-swap, .faq-a, .hero-glow, .nav-sheet, .nav-sheet-inner { animation: none !important; }
 .lp-sheen { display: none; }
 .lp-card, .lp-card::after, .btn-primary, .btn-ghost, .nav-signup, .tour-shot img, .faq-icon, .final-btn { transition: none !important; }
 .final-btn:hover { transform: none !important; }
 .lp-card:hover, .btn-primary:hover, .btn-ghost:hover, .nav-signup:hover, .tour-row:hover .tour-shot img { transform: none !important; }
}
`

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LandingPage() {
  const { lang } = useLanguage()
  const L = LANDING[lang]

  // Same names the render code already used, now sourced from the active
  // language. Nothing below this line had to change.
  const FREE_LIMITS = L.pricing.limits
  const PROBLEMS   = L.problem.items
  const STEPS      = L.how.steps
  const CASES      = L.cases.items
  const BENEFITS   = L.benefits.items
  const COMPARE    = L.compare.rows
  const ROLES      = L.includes.roles
  const INCLUDES   = L.includes.items
  const NEED       = L.start.need
  const NOT_NEED   = L.start.notNeed
  const FAQS       = L.faq.items
  // The hero frame IS the first screen of the tour, so it follows the
  // language with everything else instead of pointing at a fixed file.
  const HERO_SHOT  = L.tour.chapters[0].screens[0]
  // The four signal colours are design, not copy, so they stay here and are
  // zipped onto the translated rows by position.
  const SIGNAL_COLORS = [T.red, T.amber, T.green, T.muted]
  const SIGNALS = L.decide.signals.map((sig, i) => ({ ...sig, color: SIGNAL_COLORS[i] }))

 const [activeCase, setActiveCase] = useState(0)
 const [openFaq, setOpenFaq] = useState<number | null>(null)
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
 <style dangerouslySetInnerHTML={{ __html: LANDING_CSS }} />

 <Nav />

 <main>

 {/* ── HERO ─────────────────────────────────────────────────────────── */}
 <section className="hero-sec">
 <div className="hero-bg" aria-hidden>
 <div className="hero-glow hero-glow-a" />
 <div className="hero-glow hero-glow-b" />
 <div className="hero-grid" />
 </div>
 <div className="hero-inner">

 <div className="hero-eyebrow lp-rise">
 <span className="hero-eyebrow-dot" aria-hidden />
 {L.hero.eyebrow}
 </div>

 <h1 className="lp-h1 lp-rise lp-d1">
 {L.hero.title1}{' '}
 <br />
 {L.hero.title2}
 </h1>

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
 <div className="lp-sheen" aria-hidden />
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
 <Lead>
 {L.how.lead}
 </Lead>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
 {STEPS.map(({ n, title, desc }) => (
 <div key={n} data-reveal className="lp-card lp-card-soft" style={{ display: 'flex', gap: 18, alignItems: 'flex-start' }}>
 <div className="lp-step">{n}</div>
 <div>
 <div className="lp-card-title">{title}</div>
 <div className="lp-card-body">{desc}</div>
 </div>
 </div>
 ))}
 </div>

 {/* The screen guide is an opt-in deep dive (owner, 2026-10-01): a teaser
     here, the chapters in a dialog. See components/landing/ScreenGuide. */}
 <ScreenGuide tour={L.tour} manual={L.manual} lang={lang} primaryClass="btn-primary" />
 </Section>

 {/* ── HOW IT DECIDES ───────────────────────────────────────────────── */}
 <Section id="como-decide" alt>
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

 {/* ── TRUST ──────────────────────────────────────────────────────────── */}
 {/* Only claims the code backs (docs/stability.md §4.5: no unsourced figure,
     logo, testimonial or certification goes on this page). In order:
      1. per-company isolation — every query scoped by tenant_id; forecasts
         trained per session on the tenant's own dataset.
      2. roles — admin / analyst / viewer; every mutating endpoint requires
         require_analyst_or_above (backend/auth/guards.py).
      3. encryption — SQL source passwords Fernet-encrypted
         (backend/datasources/service.py), stored service credentials via
         backend/service_config/crypto.py; user passwords bcrypt-hashed
         (backend/auth/password.py).
      4. export + erasure — GET /tenant/export (ZIP of every tenant table) and
         DELETE /tenant (every table and file), backend/api/v1/tenant_data.py
         and backend/tenants/data_export.py. Admin-only API, no screen yet,
         hence "we hand you" rather than "download it yourself".
      5. no feature gates — backend/entitlements/plans.py.
      6. not a black box — the #como-decide section on this page. */}
 <Section id="confianza">
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 64, alignItems: 'start' }}>
 <div>
 <Tag>{L.trust.tag}</Tag>
 <H2>{L.trust.title}</H2>
 <p className="lp-lead" style={{ marginBottom: 0 }}>{L.trust.lead}</p>
 </div>
 <ul className="trust-list">
 {L.trust.items.map(({ title, desc }, i) => (
 <li key={title} className="trust-item">
 <h3 className="trust-title">{title}</h3>
 <p className="trust-desc">{desc}</p>
 {i === L.trust.items.length - 1 && (
 <a href="#como-decide" className="trust-link">{L.trust.decideLink}</a>
 )}
 </li>
 ))}
 </ul>
 </div>
 </Section>

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
 {/* The only place the page names the machinery (model competition,
     backtesting, ABC-XYZ, API/MCP). Kept compact and below the fold on
     purpose: the landing speaks to the buyer (owner's call, 2026-09-30). */}
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
 </Section>

 {/* ── FAQ ──────────────────────────────────────────────────────────── */}
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
 {FAQS.map(({ q, a }, i) => (
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

 {/* ── CLOSING BAND: the three ways forward ─────────────────────────── */}
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
 </main>

 {/* ── FOOTER ───────────────────────────────────────────────────────── */}
 <footer className="footer-shell" style={{ background: T.bg2, borderTop: `1px solid ${T.border}`, padding: '56px 48px 40px' }}>
 <div style={{ maxWidth: 1120, margin: '0 auto' }}>
 <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 40, marginBottom: 40 }}>
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
 <Wordmark size={20} color={T.text} accent={T.accent} />
 </div>
 <p style={{ fontSize: 13.5, color: T.muted, lineHeight: 1.6, margin: 0, maxWidth: 34 + 'ch' }}>
 {L.footer.tagline}
 </p>
 </div>
 <div>
 <div className="foot-head">{L.footer.product}</div>
 {L.footerLinks.product.map(([href, label]) => (
 <a key={href} href={href} className="foot-link">{label}</a>
 ))}
 </div>
 <div>
 <div className="foot-head">{L.footer.company}</div>
 {L.footerLinks.company.map(([href, label]) => (
 <a key={label} href={href} className="foot-link">{label}</a>
 ))}
 </div>
 <div style={{ minWidth: 0 }}>
 <div className="foot-head">{L.footer.contact}</div>
 <a href="mailto:angel.zeledon.fernandez@gmail.com" className="foot-link" style={{ wordBreak: 'break-word' }}>angel.zeledon.fernandez@gmail.com</a>
 <a href="tel:+50671862820" className="foot-link">+506 7186 2820</a>
 </div>
 </div>
 <div className="footer-bottom" style={{ borderTop: `1px solid ${T.border}`, paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
 <div style={{ fontSize: 12.5, color: T.dim }}>{L.footer.rights}</div>
 <div style={{ fontSize: 12.5, color: T.dim }}>{L.footer.madeIn}</div>
 </div>
 </div>
 </footer>
 </div>
 )
}

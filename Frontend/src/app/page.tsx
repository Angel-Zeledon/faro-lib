'use client'
// The landing is inside LanguageProvider (see app/layout.tsx), so it reads the
// same `lang` the app does — a visitor who switches here stays switched after
// signing in. The copy itself lives in i18n/landing.ts, typed so the two
// languages cannot drift apart.
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'

const T = {
 bg: '#ffffff',
 bg2: '#f8fafc',
 surface: '#f1f5f9',
 border: '#e2e8f0',
 text: '#0f172a',
 body: '#334155',
 muted: '#64748b',
 dim: '#94a3b8',
 accent: '#1d4ed8',
 accentBg: '#eff6ff',
 accentBd: '#bfdbfe',
 green: '#059669',
 greenBg: '#f0fdf4',
 greenBd: '#a7f3d0',
 red: '#dc2626',
 amber: '#d97706',
 amberBg: '#fffbeb',
 amberBd: '#fde68a',
}

// ── Nav ───────────────────────────────────────────────────────────────────────
// Two letters, not a dropdown with flags. A flag is a country and this is a
// language — Spanish is not Spain here, and English is not the United States.
// The choice persists through LanguageProvider, so it also follows the visitor
// into the app after they sign in.
function LangToggle({ lang, setLang }: { lang: 'es' | 'en'; setLang: (l: 'es' | 'en') => void }) {
 return (
  <div role="group" aria-label="Language" style={{ display: 'flex', alignItems: 'center', gap: 2, border: `1px solid ${T.border}`, borderRadius: 7, padding: 2 }}>
   {(['es', 'en'] as const).map(code => (
    <button
     key={code}
     type="button"
     onClick={() => setLang(code)}
     aria-pressed={lang === code}
     style={{
      border: 'none', cursor: 'pointer', borderRadius: 5,
      padding: '4px 9px', fontSize: 11.5, fontWeight: 700, letterSpacing: '0.03em',
      background: lang === code ? T.text : 'transparent',
      color: lang === code ? '#fff' : T.muted,
     }}
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
 <>
 <nav className="nav-shell" style={{
 position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
 background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(10px)',
 borderBottom: `1px solid ${T.border}`,
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 padding: '0 48px', height: 60,
 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
 <div style={{ width: 30, height: 30, borderRadius: 7, background: T.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 900, color: '#fff' }}>F</div>
 <span style={{ fontSize: 16, fontWeight: 800, color: T.text, letterSpacing: '-0.03em' }}>Faro</span>
 </div>
 <div className="nav-links" style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
 {NAV_LINKS.map(([href, label]) => (
 <a key={href} href={href} style={{ fontSize: 13, color: T.muted, textDecoration: 'none', fontWeight: 500, transition: 'color 0.15s' }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div className="nav-cta" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
 <LangToggle lang={lang} setLang={setLang} />
 <Link href="/login" className="nav-tap" style={{ fontSize: 13, fontWeight: 600, color: T.muted, textDecoration: 'none' }}>
 {L.nav.signIn}
 </Link>
 <Link href="/signup" className="nav-signup" style={{ display: 'inline-flex', alignItems: 'center', fontSize: 13, fontWeight: 600, color: '#fff', textDecoration: 'none', padding: '8px 18px', borderRadius: 7, background: T.text }}>
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
 <Link href="/login" onClick={() => setMenuOpen(false)}>{L.nav.signIn}</Link>
 <Link
 href="/signup"
 onClick={() => setMenuOpen(false)}
 className="nav-sheet-cta"
 >
 {L.nav.signUp}
 </Link>
 </div>
 </div>
 )}
 </>
 )
}

// ── Shared layout helpers ─────────────────────────────────────────────────────
function Section({ id, children, alt, style }: { id?: string; children: React.ReactNode; alt?: boolean; style?: React.CSSProperties }) {
 return (
 <section id={id} className="sec" style={{ background: alt ? T.bg2 : T.bg, padding: '88px 0', ...style }}>
 <div className="sec-inner" data-reveal style={{ maxWidth: 1100, margin: '0 auto', padding: '0 48px' }}>{children}</div>
 </section>
 )
}

function Tag({ children }: { children: React.ReactNode }) {
 return (
 <div style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 20, marginBottom: 18, background: T.accentBg, border: `1px solid ${T.accentBd}`, fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 {children}
 </div>
 )
}

function H2({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h2 style={{ fontSize: 34, fontWeight: 800, color: T.text, margin: '0 0 14px', letterSpacing: '-0.035em', lineHeight: 1.2, ...style }}>{children}</h2>
}

function Lead({ children, maxWidth = 600 }: { children: React.ReactNode; maxWidth?: number }) {
 return <p style={{ fontSize: 16, color: T.body, lineHeight: 1.7, margin: '0 0 44px', maxWidth }}>{children}</p>
}

function Check() {
 return (
 <svg width={14} height={14} viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
 <circle cx={7} cy={7} r={7} fill={T.greenBg} />
 <path d="M3.5 7 L6 9.5 L10.5 5" stroke={T.green} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// Counterpart to Check() for "you do not need this" lists — same size, neutral.
function Dash() {
 return (
 <svg width={14} height={14} viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
 <circle cx={7} cy={7} r={7} fill={T.surface} />
 <path d="M4 7 L10 7" stroke={T.muted} strokeWidth={1.5} fill="none" strokeLinecap="round" />
 </svg>
 )
}

// Sub-heading inside the long-form sections — one step below H2, same type scale.
function H3({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h3 style={{ fontSize: 19, fontWeight: 800, color: T.text, margin: '0 0 16px', letterSpacing: '-0.02em', lineHeight: 1.3, ...style }}>{children}</h3>
}

// Wide tables scroll inside their own box so the page body never scrolls sideways.
function Scroller({ minWidth, children }: { minWidth: number; children: React.ReactNode }) {
 return (
 <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
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
 el.style.transitionDelay = `${Math.min(index, 3) * 70}ms`
 el.classList.add('reveal-armed')
 armed.push(el)
 })
 if (armed.length === 0) return

 const show = (el: HTMLElement) => el.classList.add('reveal-in')
 let observerWorks = false
 const observer = new IntersectionObserver((entries, obs) => {
 observerWorks = true
 entries.forEach(entry => {
 if (!entry.isIntersecting) return
 show(entry.target as HTMLElement)
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
 if (!observerWorks) armed.forEach(show)
 }, 3000)

 return () => {
 observer.disconnect()
 window.clearTimeout(failsafe)
 }
 }, [])
}

// The free tier's ceilings, as advertised. MUST match
// backend/entitlements/plans.py — a landing page promising 200 SKUs while the
// product stops at 100 turns the first real import into a broken promise.
// E.164 without the '+', which is what wa.me expects.
const CONTACT_WHATSAPP = '50671862820'

// A guided tour of the product, chapter by chapter.
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
interface TourScreen {
  img:   string
  name:  string
  does:  string
  finds: string[]
  alt:   string
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LandingPage() {
  const { lang, setLang } = useLanguage()
  const L = LANDING[lang]

  // Same names the render code already used, now sourced from the active
  // language. Nothing below this line had to change.
  const NAV_LINKS  = L.nav.links
  const FREE_LIMITS = L.pricing.limits
  const PROBLEMS   = L.problem.items
  const STEPS      = L.how.steps
  const CASES      = L.cases.items
  const BENEFITS   = L.benefits
  const COMPARE    = L.compare.rows
  const ROLES      = L.includes.roles
  const INCLUDES   = L.includes.items
  const NEED       = L.start.need
  const NOT_NEED   = L.start.notNeed
  const FAQS       = L.faq.items
  const TOUR       = L.tour.chapters
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
 // −30–50%", "merma −25–40%" and twelve more. Faro has never measured a single
 // one: there is no customer outcome study, no before/after dataset, nothing in
 // the repo that produces them. They were written to look like a case study.
 // What replaces them is what the product actually DOES for that operation,
 // each item checkable against code (signal thresholds and the 3-reception rule
 // in backend/inventory/service.py, the 30-day donor floor in the transfer
 // service, the BOM explosion in backend/inventory/bom_service.py).
 // If real customer outcomes ever get measured, they belong here — with the
 // customer, the period and the baseline named. A percentage with no source
 // does not go back in.

 // ── Content for the long-form sections ───────────────────────────────────────
 // Every number here is checkable against code: signal thresholds and the
 // 3-reception rule from backend/inventory/service.py.

 return (
 <>
 <style>{`
 * { box-sizing: border-box; }
 body { margin: 0; background: ${T.bg}; color: ${T.text}; font-family: system-ui, -apple-system, sans-serif; }
 html { scroll-behavior: smooth; }

 /* The nav is fixed, so an anchor jump parks the target under it: click Precio
    in the menu and the eyebrow and half the headline are behind the bar. The
    offset is the bar height plus a little air, and it belongs on the TARGET,
    not on the scroll — scroll-margin is the one mechanism that also fixes the
    keyboard focus jump and the browser restoring a #hash on reload. */
 section[id], #demo { scroll-margin-top: 88px; }
 @media (max-width: 900px) { section[id], #demo { scroll-margin-top: 76px; } }
 .btn-primary {
 display: inline-flex; align-items: center; gap: 7px;
 padding: 12px 24px; border-radius: 8px; border: none; cursor: pointer;
 font-size: 14px; font-weight: 700; color: #fff; text-decoration: none;
 background: ${T.text}; transition: background 0.15s;
 }
 .btn-primary:hover { background: #1e293b; }
 .btn-ghost {
 display: inline-flex; align-items: center; gap: 7px;
 padding: 12px 24px; border-radius: 8px; cursor: pointer;
 font-size: 14px; font-weight: 600; color: ${T.body}; text-decoration: none;
 border: 1px solid ${T.border}; background: transparent; transition: border-color 0.15s, color 0.15s;
 }
 .btn-ghost:hover { border-color: ${T.muted}; color: ${T.text}; }
 input:focus, textarea:focus, select:focus { border-color: ${T.accent} !important; box-shadow: 0 0 0 3px ${T.accentBg} !important; }

 /* Scroll reveal. The resting state is visible; useScrollReveal adds
    .reveal-armed only after it confirms it can also remove it. */
 .reveal-armed { opacity: 0; transform: translateY(12px); }
 .reveal-in {
 opacity: 1; transform: none;
 transition: opacity 380ms cubic-bezier(0.16, 1, 0.3, 1), transform 380ms cubic-bezier(0.16, 1, 0.3, 1);
 }
 @media (prefers-reduced-motion: reduce) {
 .reveal-armed, .reveal-in { opacity: 1 !important; transform: none !important; transition: none !important; }
 }

 /* The mobile menu button. Hidden above 900px, where the inline link row is
    the navigation; below it, it is the only navigation there is. */
 .nav-burger { display: none; }

 /* Narrow viewports: collapse the fixed grid columns instead of overflowing.
    Nothing here changes colour, type or shadow — only how many columns fit. */
 @media (max-width: 900px) {
 /* These two override inline styles on the nav, hence !important. */
 .nav-links { display: none !important; }
 .nav-shell { padding: 0 20px !important; }
 .grid-2, .grid-3 { grid-template-columns: 1fr !important; }
 .split { grid-template-columns: 1fr !important; gap: 32px !important; }
 .faq-side { position: static !important; }

 .nav-burger {
 display: flex; align-items: center; justify-content: center;
 width: 44px; height: 44px; margin-right: -10px;
 background: none; border: none; padding: 0; cursor: pointer;
 color: ${T.text}; border-radius: 8px;
 }
 .nav-burger:focus-visible { outline: 2px solid ${T.text}; outline-offset: 2px; }

 /* Both auth actions in a 20px gutter is tight, and the sheet carries both
    anyway, so only the primary one stays up here — signing up should not
    require opening a menu first.
    With the link row hidden, space-between is left with three children and
    strands the button in the middle of the bar, which reads as a mistake.
    Pushing it right parks it beside the menu button, where it belongs.

    NOTE: no double quotes and no ampersands anywhere inside this style
    block, comments included. The server escapes them into HTML entities and
    the client renders them raw, so the two copies of this stylesheet stop
    matching and hydration fails for the whole page. One quoted word in a CSS
    comment was enough to do it. */
 .nav-cta .nav-tap { display: none !important; }
 .nav-cta { margin-left: auto; margin-right: 10px; }

 .nav-sheet {
 position: fixed; inset: 60px 0 0; z-index: 99;
 background: rgba(15, 23, 42, 0.35);
 backdrop-filter: blur(2px);
 animation: nav-sheet-in 160ms ease-out both;
 }
 .nav-sheet-inner {
 background: #fff; border-bottom: 1px solid ${T.border};
 padding: 8px 20px 20px;
 display: flex; flex-direction: column;
 box-shadow: 0 18px 40px -24px rgba(15,23,42,0.35);
 animation: nav-sheet-slide 200ms cubic-bezier(0.16, 1, 0.3, 1) both;
 }
 .nav-sheet-inner a {
 display: flex; align-items: center; min-height: 48px;
 font-size: 15px; font-weight: 500; color: ${T.body}; text-decoration: none;
 border-bottom: 1px solid ${T.border};
 }
 .nav-sheet-inner a:last-child { border-bottom: none; }
 .nav-sheet-sep { height: 12px; }
 .nav-sheet-inner a.nav-sheet-cta {
 justify-content: center; margin-top: 12px; border-bottom: none;
 background: ${T.text}; color: #fff; font-weight: 700; border-radius: 9px;
 }
 }
 @keyframes nav-sheet-in { from { opacity: 0 } to { opacity: 1 } }
 @keyframes nav-sheet-slide { from { transform: translateY(-8px) } to { transform: none } }
 @media (prefers-reduced-motion: reduce) {
 .nav-sheet, .nav-sheet-inner { animation: none !important; }
 }
 @media (max-width: 760px) {
 .sec-inner, .hero-inner { padding: 0 20px !important; }
 .strip-shell { padding: 32px 20px !important; }
 .strip-grid { grid-template-columns: repeat(2, 1fr) !important; row-gap: 28px; }
 .strip-cell { border-right: none !important; padding: 0 12px !important; }
 .hero-h1 { font-size: 34px !important; }
 .card-pad { padding: 24px 20px !important; }
 .footer-shell { padding: 40px 20px !important; }
 .footer-grid { grid-template-columns: 1fr 1fr !important; gap: 28px !important; }
 .footer-bottom { flex-direction: column; align-items: flex-start !important; gap: 8px; }

 /* 88px of air above and below every section is a desktop rhythm. Stacked
    into one column on a phone it turned the page into 22,000px — roughly
    29 screens — and the gaps read as the page having ended. */
 .sec { padding: 52px 0 !important; }

 /* The hero reserves a full viewport plus a 120px nav offset, which on a
    short phone screen pushes the first real section below two swipes of
    mostly empty space. */
 .hero-sec { min-height: 0 !important; padding-top: 96px !important; }

 /* Anything tappable clears 44px. These are 36px chips and 17-20px inline
    links today — fine with a cursor, a coin toss with a thumb. */
 .btn-primary, .btn-ghost { min-height: 48px; padding: 14px 22px; width: 100%; justify-content: center; }
 .case-tab { min-height: 44px !important; }
 /* These carry an inline display:block, so the override has to say so. */
 .foot-link { display: flex !important; align-items: center; min-height: 44px; margin-bottom: 0 !important; }
 .nav-signup, .cta-link { min-height: 44px; }
 .cta-link { display: inline-flex; align-items: center; }
 /* Links that sit inside flowing paragraph text are left alone on purpose:
    padding them to 44px would tear holes in the line spacing around them,
    and the paragraph itself is the target the reader is already aiming at. */
 }
 `}</style>

 <Nav />

 {/* ── HERO ─────────────────────────────────────────────────────────── */}
 <section className="hero-sec" style={{ minHeight: '100vh', paddingTop: 120, background: T.bg, display: 'flex', flexDirection: 'column', alignItems: 'center', borderBottom: `1px solid ${T.border}` }}>
 <div className="hero-inner" style={{ maxWidth: 1100, width: '100%', margin: '0 auto', padding: '0 48px' }}>

 <div style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 20, marginBottom: 24, background: T.greenBg, border: `1px solid ${T.greenBd}`, fontSize: 11, fontWeight: 700, color: T.green, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 {L.hero.eyebrow}
 </div>

 <h1 className="hero-h1" style={{ fontSize: 56, fontWeight: 900, color: T.text, margin: '0 0 18px', letterSpacing: '-0.05em', lineHeight: 1.1, maxWidth: 720 }}>
 {L.hero.title1}
 <br />
 {L.hero.title2}
 </h1>

 <p style={{ fontSize: 18, color: T.body, lineHeight: 1.65, maxWidth: 560, margin: '0 0 36px' }}>
 {L.hero.lead}
 </p>

 <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 60 }}>
 <Link href="/signup?demo=1" className="btn-primary">{L.hero.cta}</Link>
 </div>

 {/* Framed real product screenshot */}
 <div id="demo" style={{ borderRadius: 14, border: `1px solid ${T.border}`, background: T.bg2, boxShadow: '0 24px 60px rgba(15,23,42,0.14)', overflow: 'hidden' }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '11px 16px', borderBottom: `1px solid ${T.border}`, background: T.bg }}>
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#f87171' }} />
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#fbbf24' }} />
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#34d399' }} />
 <span style={{ marginLeft: 12, fontSize: 11.5, color: T.dim, fontWeight: 500 }}>{L.hero.frame}</span>
 </div>
 <img src={HERO_SHOT.img} alt={HERO_SHOT.alt} style={{ display: 'block', width: '100%', height: 'auto' }} />
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 36, paddingBottom: 72, flexWrap: 'wrap' }}>
 <div style={{ fontSize: 12, color: T.dim }}>{L.misc.industriesLabel}</div>
 {L.heroPills.map(s => (
 <span key={s} style={{ fontSize: 12, fontWeight: 500, color: T.muted, padding: '4px 12px', borderRadius: 20, border: `1px solid ${T.border}` }}>{s}</span>
 ))}
 </div>
 </div>
 </section>

 {/* ── STATS STRIP ──────────────────────────────────────────────────── */}
 <div className="strip-shell" style={{ background: T.text, padding: '40px 48px' }}>
 <div className="strip-grid" data-reveal style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0 }}>
 {/* Every figure here has to be one the product can back, because the buyer
     who believes it lands two clicks later on /pronosticos and checks.
     This strip used to lead with "94% Precisión promedio de pronóstico" and
     "−75% Tiempo invertido en planificación": nobody has measured either, and
     on real sessions the app shows its own users 75–89%. Advertising 94% while
     the screen says 75% is the one thing this product cannot afford to do —
     its whole argument is that it tells you the truth about your numbers.
     What replaces them is countable: how many models compete per SKU
     (MODEL_ORDER), the deliveries it takes to learn a supplier's real lead time
     (MIN_LEAD_TIME_OBSERVATIONS = 3), and the catalogue size the product is
     exercised against.
     If a real average accuracy ever gets measured across customers, it belongs
     here — with the number the app actually shows. */}
 {[
 { value: '9', label: L.strip.models },
 { value: '3', label: L.strip.deliveries },
 { value: '5K+', label: L.strip.skus },
 { value: 'CSV', label: L.strip.csv },
 ].map(({ value, label }, i) => (
 <div key={label} className="strip-cell" style={{ textAlign: 'center', padding: '0 32px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.1)' : 'none' }}>
 <div style={{ fontSize: 36, fontWeight: 900, color: '#fff', letterSpacing: '-0.04em', marginBottom: 6 }}>{value}</div>
 <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', lineHeight: 1.4 }}>{label}</div>
 </div>
 ))}
 </div>
 </div>

 {/* ── EL PROBLEMA ──────────────────────────────────────────────────── */}
 <Section id="problema" alt>
 <Tag>{L.problem.tag}</Tag>
 <H2>{L.problem.title}</H2>
 <Lead>
 {L.problem.lead}
 </Lead>
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))', gap: 16 }}>
 {PROBLEMS.map(({ title, desc }) => (
 <div key={title} data-reveal className="card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ width: 32, height: 3, background: T.accent, borderRadius: 2, marginBottom: 16 }} />
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8, lineHeight: 1.4 }}>{title}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{desc}</div>
 </div>
 ))}
 </div>
 </Section>

 {/* ── CÓMO FUNCIONA ────────────────────────────────────────────────── */}
 <Section id="solucion">
 <Tag>{L.how.tag}</Tag>
 <H2>{L.how.title}</H2>
 <Lead>
 {L.how.lead}
 </Lead>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 20 }}>
 {STEPS.map(({ n, title, desc }) => (
 <div key={n} data-reveal className="card-pad" style={{ display: 'flex', gap: 18, alignItems: 'flex-start', background: T.bg2, borderRadius: 10, padding: '22px 24px', border: `1px solid ${T.border}` }}>
 <div style={{ width: 36, height: 36, borderRadius: 8, flexShrink: 0, background: T.accentBg, border: `1px solid ${T.accentBd}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 900, color: T.accent, fontFamily: 'monospace' }}>{n}</div>
 <div>
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 6 }}>{title}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{desc}</div>
 </div>
 </div>
 ))}
 </div>

 {/* The guided tour: its own heading, so the reader knows a long stretch of
     screens is starting and is not still inside "how it works". */}
 <div style={{ marginTop: 88, maxWidth: 720 }}>
  <h3 style={{ fontSize: 30, fontWeight: 800, color: T.text, letterSpacing: '-0.025em', margin: '0 0 12px', lineHeight: 1.2 }}>
   {L.tour.title}
  </h3>
  <p style={{ fontSize: 15, color: T.body, lineHeight: 1.7, margin: 0 }}>{L.tour.lead}</p>
 </div>

 {/* One row per screen: the capture on one side, what it does on the other.
     Sides alternate for rhythm and collapse to one column on narrow viewports
     (`tour-row`, in globals.css). */}
 {TOUR.map(({ chapter, when, screens }) => (
  <div key={chapter} style={{ marginTop: 64 }}>
   <div style={{ borderTop: `2px solid ${T.text}`, paddingTop: 14, marginBottom: 40, maxWidth: 620 }}>
    <h3 style={{ fontSize: 22, fontWeight: 800, color: T.text, letterSpacing: '-0.02em', margin: '0 0 6px' }}>
     {chapter}
    </h3>
    <p style={{ fontSize: 14, color: T.muted, margin: 0, lineHeight: 1.6 }}>{when}</p>
   </div>

   {screens.map(({ img, name, does, finds, alt }, i) => (
    <div
     key={img}
     data-reveal
     className="tour-row"
     style={{
      display: 'grid',
      gridTemplateColumns: '1.25fr 1fr',
      gap: 44,
      alignItems: 'center',
      marginBottom: 56,
      direction: i % 2 === 1 ? 'rtl' : 'ltr',
     }}
    >
     <div style={{ direction: 'ltr', borderRadius: 12, border: `1px solid ${T.border}`, overflow: 'hidden', boxShadow: '0 14px 40px rgba(15,23,42,0.11)' }}>
      <img src={img} alt={alt} loading="lazy" style={{ display: 'block', width: '100%', height: 'auto' }} />
     </div>
     <div style={{ direction: 'ltr' }}>
      <h4 style={{ fontSize: 19, fontWeight: 700, color: T.text, letterSpacing: '-0.01em', margin: '0 0 10px' }}>
       {name}
      </h4>
      <p style={{ fontSize: 14.5, color: T.body, lineHeight: 1.7, margin: '0 0 16px' }}>{does}</p>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 9 }}>
       {finds.map((f) => (
        <li key={f} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13.5, color: T.body, lineHeight: 1.6 }}>
         <span aria-hidden style={{ flexShrink: 0, width: 5, height: 5, borderRadius: 999, background: T.accent, marginTop: 8 }} />
         {f}
        </li>
       ))}
      </ul>
     </div>
    </div>
   ))}
  </div>
 ))}

 {/* The manual closes the tour: the visitor has just scrolled nineteen
     screens, and this is where wanting the whole thing on paper happens.
     The file follows the language — `faro-manual-es.pdf` / `-en.pdf`, both
     built by `backend/scripts/build_manual.py` from `docs/manual/`. */}
 <div data-reveal style={{ marginTop: 80, background: T.bg2, border: `1px solid ${T.border}`, borderRadius: 12, padding: '34px 36px', display: 'flex', gap: 32, alignItems: 'center', flexWrap: 'wrap' }}>
  <div style={{ flex: '1 1 380px', minWidth: 0 }}>
   <div style={{ fontSize: 20, fontWeight: 800, color: T.text, marginBottom: 8, letterSpacing: '-0.015em' }}>{L.manual.title}</div>
   <p style={{ fontSize: 14, color: T.body, lineHeight: 1.7, margin: 0 }}>{L.manual.body}</p>
  </div>
  <div style={{ flexShrink: 0 }}>
   <a
    href={`/faro-manual-${lang}.pdf`}
    download
    style={{ display: 'inline-block', background: T.accent, color: '#fff', fontSize: 14, fontWeight: 700, padding: '13px 24px', borderRadius: 9, textDecoration: 'none' }}
   >
    {L.manual.cta}
   </a>
   <div style={{ fontSize: 12, color: T.muted, marginTop: 9 }}>{L.manual.note}</div>
  </div>
 </div>
 </Section>

 {/* ── CÓMO DECIDE ──────────────────────────────────────────────────── */}
 <Section id="como-decide" alt>
 <Tag>{L.decide.tag}</Tag>
 <H2>{L.decide.title}</H2>
 <Lead maxWidth={680}>
 {L.decide.lead}
 </Lead>

 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start', marginBottom: 32 }}>
 <div>
 <H3>{L.decide.formulaTitle}</H3>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: '0 0 14px' }}>
 {L.decide.formulaBody}
 </p>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.decide.formulaBody2}
 </p>
 </div>
 <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>{L.misc.exampleTitle}</div>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.misc.exampleBody}
 </p>
 </div>
 </div>

 <Scroller minWidth={660}>
 <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.border}` }}>
 <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 250px', background: T.surface, padding: '12px 24px', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{L.misc.signalHead[0]}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{L.misc.signalHead[1]}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{L.misc.signalHead[2]}</div>
 </div>
 {SIGNALS.map(({ signal, rule, example, color }, i) => (
 <div key={signal} style={{ display: 'grid', gridTemplateColumns: '160px 1fr 250px', padding: '15px 24px', alignItems: 'center', background: i % 2 === 0 ? T.bg : T.bg2, borderBottom: i < SIGNALS.length - 1 ? `1px solid ${T.border}` : 'none' }}>
 <span style={{ fontSize: 12, fontWeight: 800, color, letterSpacing: '0.02em' }}>{signal}</span>
 <span style={{ fontSize: 13, color: T.body, lineHeight: 1.5, paddingRight: 16 }}>{rule}</span>
 <span style={{ fontSize: 13, color: T.muted }}>{example}</span>
 </div>
 ))}
 </div>
 </Scroller>

 <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px', marginTop: 20 }}>
 <div style={{ width: 32, height: 3, background: T.accent, borderRadius: 2, marginBottom: 16 }} />
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8 }}>{L.misc.leadTimeNote}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7 }}>
 {L.decide.leadTimeBody}
 </div>
 </div>
 </Section>

 {/* ── NOSOTROS ─────────────────────────────────────────────────────── */}
 <Section id="nosotros">
 {/* TODO: el dueño puede personalizar la historia/equipo real aquí */}
 <div style={{ maxWidth: 760 }}>
 <Tag>{L.about.tag}</Tag>
 <H2>{L.about.title}</H2>
 <p style={{ fontSize: 16, color: T.body, lineHeight: 1.75, margin: '0 0 20px' }}>
 {L.about.body1}
 </p>
 <p style={{ fontSize: 16, color: T.body, lineHeight: 1.75, margin: 0 }}>
 {L.about.body2}
 </p>
 </div>
 </Section>

 {/* ── INDUSTRIAS ───────────────────────────────────────────────────── */}
 <Section id="casos" alt>
 <Tag>{L.cases.tag}</Tag>
 <H2>{L.cases.title}</H2>
 <Lead>
 {L.cases.lead}
 </Lead>
 <div style={{ display: 'flex', gap: 8, marginBottom: 28, flexWrap: 'wrap' }}>
 {CASES.map(({ label }, i) => (
 <button key={label} className="case-tab" onClick={() => setActiveCase(i)} style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', padding: '7px 16px', borderRadius: 7, fontSize: 13, fontWeight: 600, background: activeCase === i ? T.accentBg : T.bg, border: `1px solid ${activeCase === i ? T.accentBd : T.border}`, color: activeCase === i ? T.accent : T.muted, transition: 'all 0.15s' }}>
 {label}
 </button>
 ))}
 </div>
 <div className="split card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 12, padding: '36px 40px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
 <div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>{CASES[activeCase].label}</div>
 <div style={{ fontSize: 22, fontWeight: 800, color: T.text, marginBottom: 16, letterSpacing: '-0.03em', lineHeight: 1.25 }}>{CASES[activeCase].title}</div>
 <div style={{ fontSize: 14, color: T.body, lineHeight: 1.75 }}>{CASES[activeCase].desc}</div>
 </div>
 <div>
 {/* Was "Impacto típico en …" over a column of green percentages. The
     heading promised a measured outcome, so the numbers under it read as
     measurements; none of them were. It now says what the product does. */}
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16 }}>{L.cases.doesLabel}  {CASES[activeCase].label.toLowerCase()}</div>
 {CASES[activeCase].does.map(item => (
 <div key={item} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '14px 0', borderBottom: `1px solid ${T.border}` }}>
 <span style={{ flexShrink: 0, width: 6, height: 6, marginTop: 7, borderRadius: '50%', background: T.green }} />
 <span style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{item}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── LO QUE INCLUYE ───────────────────────────────────────────────── */}
 <Section>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 72, alignItems: 'start' }}>
 <div>
 <Tag>{L.includes.tag}</Tag>
 <H2>{L.includes.title}</H2>
 <Lead>
 {L.includes.lead}
 </Lead>
 </div>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
 {BENEFITS.map(b => (
 <div key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '10px 0' }}>
 <Check />
 <span style={{ fontSize: 13, color: T.body, lineHeight: 1.5 }}>{b}</span>
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
 <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.border}` }}>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', background: T.surface, padding: '12px 24px', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Capacidad</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Excel</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Faro</div>
 </div>
 {COMPARE.map(({ feature, excel, faro }, i) => (
 <div key={feature} style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', padding: '15px 24px', alignItems: 'center', background: i % 2 === 0 ? T.bg : T.bg2, borderBottom: i < COMPARE.length - 1 ? `1px solid ${T.border}` : 'none' }}>
 <span style={{ fontSize: 13, color: T.body }}>{feature}</span>
 <span style={{ fontSize: 13, color: T.red, textAlign: 'center', fontWeight: 500 }}>{excel}</span>
 <span style={{ fontSize: 13, color: T.green, textAlign: 'center', fontWeight: 700 }}>{faro}</span>
 </div>
 ))}
 </div>
 </Scroller>
 </Section>

 {/* ── QUÉ NECESITAS ────────────────────────────────────────────────── */}
 <Section id="empezar">
 <Tag>{L.start.tag}</Tag>
 <H2>{L.start.title}</H2>
 <Lead maxWidth={680}>
 {L.start.lead}
 </Lead>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
 <div>
 <H3>{L.start.needTitle}</H3>
 {NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Check /></div>
 <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 <div>
 <H3>{L.start.notNeedTitle}</H3>
 {NOT_NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Dash /></div>
 <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── PRECIO ───────────────────────────────────────────────────────── */}
 <Section id="precio">
 <Tag>{L.pricing.tag}</Tag>
 <H2>{L.pricing.title}</H2>
 <Lead maxWidth={720}>
 {L.pricing.lead}
 </Lead>

 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, maxWidth: 860, marginBottom: 28 }}>
 <div data-reveal className="card-pad" style={{ background: T.bg2, border: `1px solid ${T.border}`, borderRadius: 12, padding: '28px 26px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.muted, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>{L.pricing.freeLabel}</div>
 <div style={{ fontSize: 26, fontWeight: 800, color: T.text, letterSpacing: '-0.02em', marginBottom: 4 }}>{L.pricing.freePrice}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7, marginBottom: 18 }}>
 {L.pricing.freeNote}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
 {FREE_LIMITS.map(([label, value]) => (
 <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, color: T.body, borderBottom: `1px solid ${T.border}`, paddingBottom: 8 }}>
 <span>{label}</span>
 <span style={{ fontWeight: 700, color: T.text, whiteSpace: 'nowrap' }}>{value}</span>
 </div>
 ))}
 </div>
 </div>

 <div data-reveal className="card-pad" style={{ background: T.bg2, border: `1px solid ${T.accentBd}`, borderRadius: 12, padding: '28px 26px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>{L.pricing.paidLabel}</div>
 <div style={{ fontSize: 26, fontWeight: 800, color: T.text, letterSpacing: '-0.02em', marginBottom: 4 }}>{L.pricing.paidPrice}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7, marginBottom: 18 }}>
 {L.pricing.paidNote}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
 {FREE_LIMITS.map(([label]) => (
 <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, color: T.body, borderBottom: `1px solid ${T.border}`, paddingBottom: 8 }}>
 <span>{label}</span>
 <span style={{ fontWeight: 700, color: T.accent, whiteSpace: 'nowrap' }}>{L.pricing.unlimited}</span>
 </div>
 ))}
 </div>
 </div>
 </div>

 <div data-reveal className="card-pad" style={{ background: T.bg2, border: `1px solid ${T.border}`, borderRadius: 12, padding: '28px 32px', maxWidth: 860 }}>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.75, marginBottom: 20 }}>
 {L.pricing.closing}
 </div>
 <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
 <Link href="/signup" style={{
 display: 'inline-block', padding: '11px 20px', borderRadius: 8,
 fontSize: 13, fontWeight: 700, textDecoration: 'none',
 background: T.text, color: '#fff',
 }}>{L.pricing.ctaSignup}</Link>
 <a href={`https://wa.me/${CONTACT_WHATSAPP}?text=${encodeURIComponent('Hola, quiero ampliar los limites de Faro.')}`} target="_blank" rel="noopener noreferrer" style={{
 display: 'inline-block', padding: '11px 20px', borderRadius: 8,
 fontSize: 13, fontWeight: 700, textDecoration: 'none',
 background: T.bg, color: T.text, border: `1px solid ${T.border}`,
 }}>{L.pricing.ctaWhatsapp}</a>
 <a href="mailto:hola@usefaro.io?subject=Faro%20%E2%80%94%20quiero%20una%20cotizaci%C3%B3n" style={{
 display: 'inline-block', padding: '11px 20px', borderRadius: 8,
 fontSize: 13, fontWeight: 700, textDecoration: 'none',
 background: T.bg, color: T.text, border: `1px solid ${T.border}`,
 }}>{L.pricing.ctaEmail}</a>
 </div>
 </div>
 </Section>

 {/* ── QUÉ INCLUYE ──────────────────────────────────────────────────── */}
 <Section id="incluye">
 <Tag>{L.includes.tag}</Tag>
 <H2>{L.includes.title}</H2>
 <Lead maxWidth={700}>
 {L.includes.lead}
 </Lead>

 <H3>{L.includes.rolesTitle}</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, marginBottom: 48 }}>
 {ROLES.map(({ role, pain, gain }) => (
 <div key={role} data-reveal className="card-pad" style={{ background: T.bg2, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 14 }}>{role}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>{L.misc.roleToday}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65, marginBottom: 14 }}>{pain}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>{L.misc.roleWith}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{gain}</div>
 </div>
 ))}
 </div>

 <H3>{L.includes.itemsTitle}</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
 {INCLUDES.map(({ title, desc, isNew }) => (
 <div key={title} data-reveal className="card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>{title}</span>
 {isNew && (
 <span style={{ fontSize: 10, fontWeight: 700, color: T.green, background: T.greenBg, border: `1px solid ${T.greenBd}`, borderRadius: 20, padding: '2px 9px', textTransform: 'uppercase', letterSpacing: '0.07em' }}>{L.includes.isNew}</span>
 )}
 </div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7 }}>{desc}</div>
 </div>
 ))}
 </div>

 <p style={{ fontSize: 13.5, color: T.body, lineHeight: 1.7, margin: '28px 0 0', maxWidth: 760 }}>
 {L.includes.tail}
 </p>
 </Section>

 {/* ── FAQ ──────────────────────────────────────────────────────────── */}
 <Section alt>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 72, alignItems: 'start' }}>
 <div className="faq-side" style={{ position: 'sticky', top: 80 }}>
 <Tag>{L.faq.tag}</Tag>
 <H2>{L.faq.title}</H2>
 <p style={{ fontSize: 15, color: T.body, lineHeight: 1.7, margin: '0 0 24px' }}>
 {L.faq.lead}
 </p>
 <a href="mailto:hola@usefaro.io" className="cta-link" style={{ fontSize: 13, fontWeight: 600, color: T.accent, textDecoration: 'none' }}>
 Escribir al equipo →
 </a>
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
 {FAQS.map(({ q, a }, i) => (
 <div key={i} style={{ borderBottom: `1px solid ${T.border}` }}>
 <button onClick={() => setOpenFaq(openFaq === i ? null : i)} style={{
 all: 'unset', cursor: 'pointer', width: '100%', display: 'flex',
 justifyContent: 'space-between', alignItems: 'center',
 padding: '20px 0', gap: 16,
 }}>
 <span style={{ fontSize: 14, fontWeight: 600, color: T.text, lineHeight: 1.4, textAlign: 'left' }}>{q}</span>
 <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: '50%', background: T.surface, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, color: T.muted, fontWeight: 400, lineHeight: 1, transition: 'transform 0.2s', transform: openFaq === i ? 'rotate(45deg)' : 'none' }}>+</span>
 </button>
 {openFaq === i && (
 <div style={{ fontSize: 14, color: T.body, lineHeight: 1.7, paddingBottom: 20 }}>{a}</div>
 )}
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── FOOTER ───────────────────────────────────────────────────────── */}
 <footer className="footer-shell" style={{ background: T.bg2, borderTop: `1px solid ${T.border}`, padding: '40px 48px' }}>
 <div style={{ maxWidth: 1100, margin: '0 auto' }}>
 <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 40, marginBottom: 40 }}>
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
 <div style={{ width: 26, height: 26, borderRadius: 6, background: T.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 900, color: '#fff' }}>F</div>
 <span style={{ fontSize: 15, fontWeight: 800, color: T.text, letterSpacing: '-0.02em' }}>Faro</span>
 </div>
 <p style={{ fontSize: 13, color: T.muted, lineHeight: 1.6, margin: 0 }}>
 {L.footer.tagline}
 </p>
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>{L.footer.product}</div>
 {L.footerLinks.product.map(([href, label]) => (
 <a key={href} href={href} className="foot-link" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 10 }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>{L.footer.company}</div>
 {L.footerLinks.company.map(([href, label]) => (
 <a key={label} href={href} className="foot-link" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 10 }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>{L.footer.contact}</div>
 <a href="mailto:angel.zeledon.fernandez@gmail.com" className="foot-link" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 8, wordBreak: 'break-word' }}
 onMouseEnter={e => (e.currentTarget.style.color = T.accent)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >angel.zeledon.fernandez@gmail.com</a>
 <a href="tel:+50671862820" className="foot-link" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none' }}
 onMouseEnter={e => (e.currentTarget.style.color = T.accent)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >+506 7186 2820</a>
 </div>
 </div>
 <div className="footer-bottom" style={{ borderTop: `1px solid ${T.border}`, paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
 <div style={{ fontSize: 12, color: T.dim }}>{L.footer.rights}</div>
 <div style={{ fontSize: 12, color: T.dim }}>{L.footer.madeIn}</div>
 </div>
 </div>
 </footer>
 </>
 )
}

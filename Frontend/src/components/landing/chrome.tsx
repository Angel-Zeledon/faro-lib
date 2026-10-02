'use client'
// The landing's chrome — nav bar, mobile sheet and footer — shared by the home
// page and the public subpages. Moved from LandingPage.tsx; on the home page it
// renders exactly as before.
//
// Links in the catalogue are `#anchor` (a home-page section) or `/slug` (a
// subpage). On the home page an anchor stays an anchor; anywhere else it
// becomes `/#anchor`, unless that page renders the same section itself
// (`localAnchors`) — the closing band is on every page, so "Contacto" never
// leaves the page it is clicked on.
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { appHref } from '@/lib/siteUrls'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { LEGAL } from '@/i18n/legal'
import { DOCS_CHROME } from '@/i18n/docs/chrome'
import { LEGAL_HUB_PATH, LEGAL_ORDER, LEGAL_PATHS } from '@/components/landing/legalPaths'
import { Wordmark } from '@/components/brand/Wordmark'
import { T } from '@/components/landing/theme'
import { CONTACT_EMAIL, CONTACT_PHONE_HREF, CONTACT_PHONE_LABEL } from '@/components/landing/contact'

export interface ChromeProps {
  // True only on `/`. Decides whether `#anchor` links stay on the page.
  onHome: boolean
  // Section ids this (non-home) page renders itself.
  localAnchors?: string[]
}

export function landingHref(href: string, { onHome, localAnchors = [] }: ChromeProps): string {
  if (!href.startsWith('#') || onHome) return href
  return localAnchors.includes(href.slice(1)) ? href : `/${href}`
}

// A link to a subpage goes through Next's router (no full reload); anchors and
// `/#anchor` stay plain anchors so the browser handles the jump.
function LandingLink({ href, className, onClick, children, current }: {
  href: string; className?: string; onClick?: () => void; children: React.ReactNode; current?: boolean
}) {
  if (href.startsWith('/') && !href.startsWith('/#')) {
    return (
      <Link href={href} className={className} onClick={onClick} aria-current={current ? 'page' : undefined}>
        {children}
      </Link>
    )
  }
  return <a href={href} className={className} onClick={onClick}>{children}</a>
}

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

export function Nav(props: ChromeProps) {
 const { lang, setLang } = useLanguage()
 const pathname = usePathname()
 const L = LANDING[lang]
 const NAV_LINKS = L.nav.links.map(([href, label]) => [landingHref(href, props), label] as const)

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
 <a href={props.onHome ? '#top' : '/'} aria-label="StockAI" style={{ display: 'flex', alignItems: 'center', textDecoration: 'none' }}>
 <Wordmark size={22} color={T.text} accent={T.accent} />
 </a>
 <div className="nav-links">
 {NAV_LINKS.map(([href, label]) => (
 <LandingLink key={href} href={href} className="nav-link" current={href === pathname}>{label}</LandingLink>
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
 <LandingLink key={href} href={href} onClick={() => setMenuOpen(false)} current={href === pathname}>{label}</LandingLink>
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

export function Footer(props: ChromeProps) {
 const { lang } = useLanguage()
 const L = LANDING[lang]
 return (
 <footer className="footer-shell" style={{ background: T.bg2, borderTop: `1px solid ${T.border}`, padding: '56px 48px 40px' }}>
 <div style={{ maxWidth: 1120, margin: '0 auto' }}>
 <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr 1fr', gap: 40, marginBottom: 40 }}>
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
 <LandingLink key={href} href={landingHref(href, props)} className="foot-link">{label}</LandingLink>
 ))}
 </div>
 <div>
 <div className="foot-head">{L.footer.company}</div>
 {L.footerLinks.company.map(([href, label]) => (
 <LandingLink key={label} href={landingHref(href, props)} className="foot-link">{label}</LandingLink>
 ))}
 </div>
 <div>
 <div className="foot-head">{DOCS_CHROME[lang].footerHead}</div>
 {DOCS_CHROME[lang].footerLinks.map(([href, label]) => (
 <LandingLink key={href} href={href} className="foot-link">{label}</LandingLink>
 ))}
 </div>
 <div>
 <div className="foot-head">{LEGAL[lang].footerHead}</div>
 {LEGAL_ORDER.map(k => (
 <LandingLink key={k} href={LEGAL_PATHS[k]} className="foot-link">{LEGAL[lang].docs[k].label}</LandingLink>
 ))}
 <LandingLink href={LEGAL_HUB_PATH} className="foot-link">{LEGAL[lang].hub.allLink}</LandingLink>
 </div>
 <div style={{ minWidth: 0 }}>
 <div className="foot-head">{L.footer.contact}</div>
 <a href={`mailto:${CONTACT_EMAIL}`} className="foot-link" style={{ wordBreak: 'break-word' }}>{CONTACT_EMAIL}</a>
 <a href={CONTACT_PHONE_HREF} className="foot-link">{CONTACT_PHONE_LABEL}</a>
 </div>
 </div>
 <div className="footer-bottom" style={{ borderTop: `1px solid ${T.border}`, paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
 <div style={{ fontSize: 12.5, color: T.dim }}>{L.footer.rights}</div>
 <div style={{ fontSize: 12.5, color: T.dim }}>{L.footer.madeIn}</div>
 </div>
 </div>
 </footer>
 )
}

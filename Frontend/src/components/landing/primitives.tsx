'use client'
// Layout primitives shared by the landing and its public subpages: the
// section shell, its type steps, the tick/dash marks, the table scroller and
// the scroll reveal. Moved verbatim from LandingPage.tsx.
import { useEffect } from 'react'
import { T, LANDING_CSS } from '@/components/landing/theme'

// The stylesheet, injected with dangerouslySetInnerHTML (see the note above
// LANDING_CSS for why it is not a text child).
export function LandingStyles() {
 return <style dangerouslySetInnerHTML={{ __html: LANDING_CSS }} />
}

// ── Shared layout helpers ─────────────────────────────────────────────────────
export function Section({ id, children, alt, className, style }: { id?: string; children: React.ReactNode; alt?: boolean; className?: string; style?: React.CSSProperties }) {
 return (
 <section id={id} className={`sec${alt ? ' sec-alt' : ''}${className ? ` ${className}` : ''}`} style={style}>
 <div className="sec-inner" data-reveal>{children}</div>
 </section>
 )
}

// Section label. Sentence case with a small beam dot — the section names are
// navigation (they match the menu), so they stay, but quietly.
export function Tag({ children }: { children: React.ReactNode }) {
 return <div className="lp-tag"><span aria-hidden className="lp-tag-dot" />{children}</div>
}

export function H2({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h2 className="lp-h2" style={style}>{children}</h2>
}

export function Lead({ children, maxWidth = 600 }: { children: React.ReactNode; maxWidth?: number }) {
 return <p className="lp-lead" style={{ maxWidth }}>{children}</p>
}

export function Check() {
 return (
 <svg width={16} height={16} viewBox="0 0 14 14" style={{ flexShrink: 0 }} aria-hidden>
 <circle cx={7} cy={7} r={7} style={{ fill: T.greenBg }} />
 <path d="M3.5 7 L6 9.5 L10.5 5" style={{ stroke: T.green }} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// Counterpart to Check() for "you do not need this" lists — same size, neutral.
export function Dash() {
 return (
 <svg width={16} height={16} viewBox="0 0 14 14" style={{ flexShrink: 0 }} aria-hidden>
 <circle cx={7} cy={7} r={7} style={{ fill: T.surface }} />
 <path d="M4 7 L10 7" style={{ stroke: T.muted }} strokeWidth={1.5} fill="none" strokeLinecap="round" />
 </svg>
 )
}

// Sub-heading inside the long-form sections — one step below H2, same type scale.
export function H3({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h3 className="lp-h3" style={style}>{children}</h3>
}

// Wide tables scroll inside their own box so the page body never scrolls sideways.
export function Scroller({ minWidth, children }: { minWidth: number; children: React.ReactNode }) {
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
export function useScrollReveal() {
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

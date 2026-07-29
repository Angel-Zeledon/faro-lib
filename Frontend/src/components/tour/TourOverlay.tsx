'use client'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { X, ArrowLeft, ArrowRight } from 'lucide-react'
import { useTour } from '@/contexts/TourContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import type { TourDefinition, TourStep } from './types'

/** Copy for a tour comes from the tour's own module — see types.ts on why.
 *  Falls back to Spanish, then to the key itself, so a missing translation
 *  shows readable text instead of a raw identifier. */
function copyOf(tour: TourDefinition, lang: string, key: string): string {
  const table = lang === 'en' ? tour.copy.en : tour.copy.es
  return table[key] ?? tour.copy.es[key] ?? key
}

const CARD_W = 340
// An un-anchored step is a title card for the whole screen, not a pointer at
// one control. Rendered small and floating it read as a tooltip that had lost
// its target and was covering content at random; at this width, centred, it
// reads as something deliberate.
const PANEL_W = 460
const PAD = 8          // breathing room between the cut-out and the element
const GAP = 12         // between the cut-out and the card

interface Rect { top: number; left: number; width: number; height: number }

/** Resolve a step's anchor to a viewport rect, or null when it is not on
 *  screen. Null is a normal outcome, not an error: see types.ts. */
function anchorRect(step: TourStep): Rect | null {
  if (!step.anchor) return null
  const el = document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(step.anchor)}"]`)
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width === 0 && r.height === 0) return null
  return { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 }
}

export default function TourOverlay() {
  const { active, stepIndex, next, back, stop } = useTour()
  const { t, lang } = useLanguage()
  const narrow = useIsNarrow()
  const [rect, setRect] = useState<Rect | null>(null)
  const [nudgeUp, setNudgeUp] = useState(0)
  const cardRef = useRef<HTMLDivElement>(null)

  const step = active?.steps[stepIndex]

  // Measure after paint, and again on resize/scroll — the anchor moves when
  // the page reflows, and a spotlight that lags behind the thing it is
  // pointing at is worse than none.
  useLayoutEffect(() => {
    if (!step) { setRect(null); return }
    const measure = () => {
      const r = anchorRect(step)
      setRect(r)
      if (r) {
        const el = document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(step.anchor!)}"]`)
        // 'nearest' so a step already in view does not jump the page.
        el?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      }
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [step])

  // A step's body is a short paragraph, so the card's height is not knowable
  // until it renders. Placing it below a low anchor could push Next and Back
  // off the bottom of the screen — the tour became unusable exactly on the
  // steps that explain the most. Measure once painted and lift it back inside.
  useLayoutEffect(() => {
    setNudgeUp(0)
  }, [step])

  useLayoutEffect(() => {
    const el = cardRef.current
    if (!el || nudgeUp !== 0) return
    const overflow = el.getBoundingClientRect().bottom - (window.innerHeight - 12)
    if (overflow > 0) setNudgeUp(overflow)
  }, [step, rect, nudgeUp, narrow])

  // Keyboard: Escape leaves, arrows navigate. A tour is optional by
  // definition, so leaving must always be one key away.
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape')     { e.preventDefault(); stop() }
      if (e.key === 'ArrowRight') { e.preventDefault(); next() }
      if (e.key === 'ArrowLeft')  { e.preventDefault(); back() }
    }
    window.addEventListener('keydown', onKey)
    cardRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [active, next, back, stop])

  if (!active || !step) return null

  const total = active.steps.length
  const isLast = stepIndex === total - 1

  // Card position. On a phone it is a bottom sheet: there is no room to place
  // a 320px card beside anything, and a sheet keeps the highlighted element
  // visible above it.
  let cardStyle: React.CSSProperties
  if (narrow || !rect) {
    cardStyle = narrow
      ? { left: 12, right: 12, bottom: 12, width: 'auto' }
      : { left: '50%', top: '50%', transform: 'translate(-50%, -50%)', width: PANEL_W }
  } else {
    const below = rect.top + rect.height + GAP
    const roomBelow  = window.innerHeight - below > 200
    const roomRight  = window.innerWidth - (rect.left + rect.width) > CARD_W + 24
    // A whole-panel anchor can be taller than the viewport, leaving no room
    // above or below without covering the very thing being pointed at. Beside
    // it is the only honest placement in that case.
    const tall = rect.height > window.innerHeight * 0.55

    if (tall && roomRight) {
      cardStyle = {
        left: rect.left + rect.width + GAP,
        top: Math.max(12, Math.min(rect.top, window.innerHeight - 240)),
        width: CARD_W,
      }
    } else {
      const left = Math.min(
        Math.max(12, rect.left + rect.width / 2 - CARD_W / 2),
        window.innerWidth - CARD_W - 12,
      )
      cardStyle = roomBelow
        ? { top: below, left, width: CARD_W }
        : { top: Math.max(12, rect.top - GAP - 200), left, width: CARD_W }
    }
  }

  // Four rects around the cut-out rather than an SVG mask: it needs no extra
  // element in the DOM, survives any border-radius, and leaves the highlighted
  // element fully visible and un-tinted.
  const shade = 'rgba(8, 20, 22, 0.55)'
  const panels: React.CSSProperties[] = rect && !narrow ? [
    { top: 0, left: 0, right: 0, height: Math.max(0, rect.top) },
    { top: rect.top + rect.height, left: 0, right: 0, bottom: 0 },
    { top: rect.top, left: 0, width: Math.max(0, rect.left), height: rect.height },
    { top: rect.top, left: rect.left + rect.width, right: 0, height: rect.height },
  ] : [{ inset: 0 }]

  return (
    <div role="dialog" aria-modal="true" aria-label={copyOf(active, lang, active.name)} style={{ position: 'fixed', inset: 0, zIndex: 9000 }}>
      {panels.map((p, i) => (
        <div key={i} onClick={stop} style={{ position: 'fixed', background: shade, ...p }} />
      ))}

      {rect && !narrow && (
        <div style={{
          position: 'fixed', pointerEvents: 'none',
          top: rect.top, left: rect.left, width: rect.width, height: rect.height,
          borderRadius: 10, boxShadow: '0 0 0 2px var(--accent)',
        }} />
      )}

      <div
        ref={cardRef}
        tabIndex={-1}
        className="modal-panel-enter"
        style={{
          position: 'fixed', ...cardStyle,
          ...(nudgeUp > 0 && typeof cardStyle.top === 'number'
            ? { top: Math.max(12, (cardStyle.top as number) - nudgeUp) }
            : null),
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 12, padding: '16px 18px', outline: 'none',
          boxShadow: '0 24px 60px -20px rgba(0,0,0,0.5)',
          // Never taller than the screen, whatever the copy says.
          maxHeight: 'calc(100vh - 24px)',
          display: 'flex', flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 6 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', flex: 1 }}>
            {copyOf(active, lang, step.title)}
          </div>
          <button
            onClick={stop}
            aria-label={t('tour.close')}
            style={{ all: 'unset', cursor: 'pointer', color: 'var(--dim)', padding: 2 }}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>

        {/* Steps explain WHY a thing exists, so bodies run to a short
            paragraph. Capped and scrollable rather than allowed to grow off a
            laptop screen, which would push the buttons out of reach. */}
        <p style={{
          margin: '0 0 14px', fontSize: 12.5, lineHeight: 1.65, color: 'var(--muted)',
          overflowY: 'auto', minHeight: 0, whiteSpace: 'pre-line',
        }}>
          {copyOf(active, lang, step.body)}
        </p>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--dim)', fontVariantNumeric: 'tabular-nums' }}>
            {stepIndex + 1} / {total}
          </span>
          <div style={{ flex: 1 }} />
          {stepIndex > 0 && (
            <button
              onClick={back}
              className="btn"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '8px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                background: 'transparent', color: 'var(--text)',
                border: '1px solid var(--border)', cursor: 'pointer',
              }}
            >
              <ArrowLeft size={13} aria-hidden="true" /> {t('tour.back')}
            </button>
          )}
          <button
            onClick={next}
            className="btn"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              padding: '8px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
              background: 'var(--accent)', color: '#fff', border: 'none', cursor: 'pointer',
            }}
          >
            {isLast ? t('tour.finish') : t('tour.next')}
            {!isLast && <ArrowRight size={13} aria-hidden="true" />}
          </button>
        </div>
      </div>
    </div>
  )
}

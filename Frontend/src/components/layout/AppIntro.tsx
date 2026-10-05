'use client'
import { useEffect, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { BrandMark } from '@/components/brand/BrandMark'
import { TIPS, randomTipIndex } from '@/lib/tips'

/**
 * A short branded entrance when the app opens: the mark, a bar that fills and
 * one tip, then it fades away on its own (~1.6 s, pure CSS, see globals.css
 * "App entrance"). The screen underneath renders and loads its data meanwhile,
 * so this costs no waiting on top of what the page already needs.
 *
 * Once per browser session — a navigation inside the app never replays it —
 * and again after each sign-in (the login screen clears the flag). Skipped for
 * people who asked their system for reduced motion.
 */
export const INTRO_SEEN_KEY = 'stockai_intro_seen'
const TOTAL_MS = 1700

export default function AppIntro() {
  const { t } = useLanguage()
  const [show, setShow] = useState(false)
  const [tip] = useState(() => TIPS[randomTipIndex()])

  useEffect(() => {
    try {
      if (sessionStorage.getItem(INTRO_SEEN_KEY) === '1') return
      sessionStorage.setItem(INTRO_SEEN_KEY, '1')
    } catch {
      return // storage blocked: skip the flourish rather than replay it on every page
    }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    setShow(true)
  }, [])

  // Its own effect, keyed on `show`: in React's development double-mount the
  // effect above runs twice and the second run returns early (the flag is
  // already set), so a timer started there would be cancelled and never
  // restarted, leaving the overlay mounted forever.
  useEffect(() => {
    if (!show) return
    // While the intro covers the screen, the sidebar's unfold waits behind it
    // (globals.css `.intro-playing .sb-unfold`) and plays as the curtain lifts.
    // A little before the end, so the two overlap instead of queueing.
    document.documentElement.classList.add('intro-playing')
    const lift = setTimeout(() => document.documentElement.classList.remove('intro-playing'), TOTAL_MS - 450)
    const id = setTimeout(() => setShow(false), TOTAL_MS)
    return () => {
      clearTimeout(lift); clearTimeout(id)
      document.documentElement.classList.remove('intro-playing')
    }
  }, [show])

  if (!show) return null

  return (
    <div className="app-intro" aria-hidden="true">
      <div className="app-intro-mark">
        <BrandMark size={96} outline />
      </div>
      <div className="app-intro-bar"><span /></div>
      <p className="app-intro-tip">{t(`tips.${tip.n}.body`)}</p>
    </div>
  )
}

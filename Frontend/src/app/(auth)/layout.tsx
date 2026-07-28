'use client'
import { useEffect, useRef } from 'react'
import { usePathname } from 'next/navigation'
import { AmbientScene, BeaconGlyph } from '@/components/auth/AmbientScene'

// Persistent stage for the auth screens. Because this layout wraps both
// routes, React keeps the ambient canvas and the wordmark MOUNTED across a
// /login ↔ /signup navigation — the beam never restarts its swing, the
// particles never jump back to their start positions, and only the card
// itself re-enters. That continuity is the whole point of putting the scene
// here rather than inside each page.
//
// Scoped to /login and /signup on purpose: the other (auth) routes
// (verify-email, forgot-password, reset-password) still use the dark
// treatment, and dropping them onto this white canvas would break them.
const SCENE_ROUTES = ['/login', '/signup']

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const markRef = useRef<HTMLDivElement>(null)
  const showScene = SCENE_ROUTES.some(p => pathname === p || pathname.startsWith(p + '/'))

  // Wordmark parallax — a couple of pixels against the cursor, damped harder
  // than the particle layer so the fixed UI never feels loose.
  useEffect(() => {
    if (!showScene) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let frame = 0
    function onMove(e: PointerEvent) {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const el = markRef.current
        if (!el) return
        const dx = (e.clientX / window.innerWidth  - 0.5) * 2
        const dy = (e.clientY / window.innerHeight - 0.5) * 2
        el.style.transform = `translate3d(${(dx * 3.5).toFixed(2)}px, ${(dy * 2.5).toFixed(2)}px, 0)`
      })
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [showScene])

  if (!showScene) return <>{children}</>

  return (
    <>
      {/* `spin` is global (globals.css). It used to be declared here, which
          meant the spinners on /verify-email and /reset-password froze
          whenever this layout took its early `!showScene` return. */}
      <style>{`
        body { margin: 0; }
        .auth-field label { transition: color 0.22s ease; }
        .auth-field:focus-within label { color: #0a0a0a; }
      `}</style>

      {/* position:fixed — ConditionalShell wraps auth routes in a flex box with
          no width:100%, so an in-flow child would shrink to its content
          instead of covering the viewport. */}
      <div style={{ position: 'fixed', inset: 0, overflow: 'hidden', background: '#fff' }}>
        <AmbientScene />

        {/* Wordmark — persistent across both routes, so it holds still while
            the card beneath it changes. */}
        <div
          ref={markRef}
          style={{
            position: 'absolute', top: 'clamp(28px, 4.5vw, 52px)', left: 'clamp(28px, 5vw, 64px)',
            display: 'flex', alignItems: 'center', gap: 9, zIndex: 2,
            transition: 'transform 1.2s cubic-bezier(0.16, 1, 0.3, 1)',
            animation: 'auth-fade-in 0.9s ease-out both',
          }}
        >
          <BeaconGlyph size={17} />
          <span style={{ fontSize: 14, fontWeight: 600, color: '#0a0a0a', letterSpacing: '-0.01em' }}>
            Faro
          </span>
        </div>

        <div style={{ position: 'relative', height: '100%', zIndex: 1 }}>
          {children}
        </div>
      </div>
    </>
  )
}

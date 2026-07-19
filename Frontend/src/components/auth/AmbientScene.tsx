'use client'
import { useEffect, useRef } from 'react'

// Ambient canvas behind the auth screens. Mounted once by (auth)/layout.tsx
// so it survives a /login ↔ /signup navigation uninterrupted.
//
// Design constraint learned the hard way: on a white page every "atmospheric"
// layer has to be painted in DARK pixels, so stacking several of them at
// 2-4% opacity doesn't read as depth — it reads as a dirty grey wash. So
// there is exactly one soft wash here, and everything else is crisp: stroked
// rings, hairline arcs, 2px dots. Sharp marks stay elegant at low opacity in
// a way that blurred ones never do.
//
// Coordinates are authored constants, never Math.random() — random values
// would differ between the server and client render and trip a hydration
// mismatch.

// Palette — deliberately two temperatures and nothing else: a WARM lamp
// (amber) against a COOL sea and sky (the app's indigo, from globals.css).
// Everything the beacon emits is warm; everything it falls on is cool. That
// single rule is what keeps a multi-element scene from turning into soup.
// The lighthouse itself stays pure black.
const C = {
  ink:    '9,9,11',        // the tower, and any true-black detail
  cool:   '99,102,241',    // indigo — sky, sea, far details
  coolDeep: '67,76,128',   // slate-indigo — nearer water lines
  warm:   '245,158,11',    // amber — lamp, rings, reflection
}

// Scene geometry, all in % of the viewport. The tower's base sits ON the
// horizon and the rings leave its actual lamp room, so LAMP.y is DERIVED from
// the other two rather than hand-tuned — hand-tuning it is what left the
// tower hovering above the horizon line.
//
// In the SVG viewBox the lamp centre is at y≈32 of 132, i.e. 24.2% down, so
// the lamp sits 75.8% of the tower's height above its base.
const HORIZON  = 79   // % from top
const TOWER_H  = 29   // % of viewport height
const LAMP     = { x: 79, y: HORIZON - 0.758 * TOWER_H }

// Soft cloud bands — wide, flat ellipses, cool-tinted. Only two, high in the
// frame, so the sky has weight without competing with the beacon.
const CLOUDS = [
  { top: 11, left: 52, w: 30, h: 4.5, o: 0.055, dur: 70 },
  { top: 22, left: 78, w: 22, h: 3.4, o: 0.045, dur: 95 },
  { top: 6,  left: 88, w: 16, h: 2.6, o: 0.035, dur: 80 },
]

// Three birds, far off. Tiny open chevrons — at this scale they read as
// distance markers, not as illustration.
const BIRDS = [
  { top: 30, left: 60, size: 9,  o: 0.22, dur: 6.5, delay: 0 },
  { top: 33, left: 64, size: 7,  o: 0.18, dur: 7.2, delay: 0.6 },
  { top: 27, left: 66, size: 6,  o: 0.15, dur: 6.8, delay: 1.2 },
]

const RING_PERIOD_S = 14
const RINGS = [0, 1, 2, 3, 4]   // staggered by RING_PERIOD_S / RINGS.length

// Sea — hairlines below the horizon. Spacing widens and opacity falls with
// depth, which is what produces the sense of perspective; `inset` narrows the
// near lines so the water reads as a receding plane, not a stack of rules.
const SEA = [
  { top: 0.4,  inset: 30, opacity: 0.11, dur: 19, anim: 'a' },
  { top: 1.3,  inset: 24, opacity: 0.10, dur: 23, anim: 'b' },
  { top: 2.5,  inset: 19, opacity: 0.09, dur: 17, anim: 'a' },
  { top: 4.1,  inset: 14, opacity: 0.08, dur: 26, anim: 'b' },
  { top: 6.2,  inset: 10, opacity: 0.07, dur: 21, anim: 'a' },
  { top: 8.8,  inset: 6,  opacity: 0.06, dur: 29, anim: 'b' },
  { top: 12.0, inset: 3,  opacity: 0.05, dur: 24, anim: 'a' },
  { top: 15.8, inset: 0,  opacity: 0.04, dur: 31, anim: 'b' },
]

// Bearing ticks — a broken ring of marks around the lamp, longer every 90°.
const BEARINGS = Array.from({ length: 36 }, (_, i) => i * 10)

// Light motes — luminous specks lifting off the water and drifting up through
// the frame. Unlike DOTS (crisp, fixed, precise) these are soft-edged and
// travelling: a radial core with a bloom, so they read as points of LIGHT
// rather than marks on the page.
//
// `warm` is assigned by proximity to the lamp: motes on the lit side pick up
// its amber, ones further left stay cool indigo. `start` is the % up from the
// bottom they begin at, so they don't all launch off the same line.
const MOTES = [
  { left: 88, start: 4,  size: 7,  warm: true,  dur: 26, delay: 0,    v: 'a' },
  { left: 82, start: 1,  size: 4,  warm: true,  dur: 21, delay: 3.5,  v: 'b' },
  { left: 75, start: 7,  size: 9,  warm: true,  dur: 33, delay: 1.2,  v: 'c' },
  { left: 92, start: 2,  size: 5,  warm: true,  dur: 24, delay: 6.0,  v: 'b' },
  { left: 70, start: 3,  size: 6,  warm: true,  dur: 29, delay: 9.5,  v: 'a' },
  { left: 85, start: 9,  size: 3,  warm: true,  dur: 19, delay: 12.0, v: 'c' },
  { left: 96, start: 6,  size: 8,  warm: true,  dur: 31, delay: 4.8,  v: 'a' },
  { left: 64, start: 2,  size: 5,  warm: false, dur: 27, delay: 7.2,  v: 'b' },
  { left: 57, start: 5,  size: 7,  warm: false, dur: 34, delay: 2.4,  v: 'c' },
  { left: 50, start: 1,  size: 4,  warm: false, dur: 23, delay: 10.6, v: 'a' },
  { left: 44, start: 8,  size: 6,  warm: false, dur: 30, delay: 5.4,  v: 'b' },
  { left: 61, start: 4,  size: 3,  warm: false, dur: 20, delay: 14.0, v: 'c' },
  { left: 79, start: 11, size: 5,  warm: true,  dur: 28, delay: 8.1,  v: 'b' },
  { left: 68, start: 6,  size: 4,  warm: false, dur: 25, delay: 11.3, v: 'a' },
]

// Kept clear of the card column (left ~38%) so nothing ever sits behind the
// form; weighted toward the upper band, which is otherwise the emptiest part
// of the composition.
const DOTS = [
  { top: 20, left: 62, depth: 1.15, dur: 7,  delay: 0.0 },
  { top: 33, left: 74, depth: 0.60, dur: 9,  delay: 1.6 },
  { top: 14, left: 47, depth: 0.85, dur: 8,  delay: 3.1 },
  { top: 48, left: 57, depth: 1.30, dur: 10, delay: 0.7 },
  { top: 27, left: 88, depth: 0.45, dur: 7,  delay: 2.3 },
  { top: 58, left: 70, depth: 0.95, dur: 11, delay: 4.0 },
  { top: 40, left: 40, depth: 0.70, dur: 8,  delay: 1.1 },
  { top: 68, left: 52, depth: 1.10, dur: 9,  delay: 2.8 },
  { top: 10, left: 71, depth: 0.50, dur: 10, delay: 3.6 },
  { top: 76, left: 78, depth: 0.80, dur: 8,  delay: 0.4 },
  { top: 7,  left: 55, depth: 0.65, dur: 9,  delay: 5.1 },
  { top: 17, left: 83, depth: 1.05, dur: 12, delay: 1.9 },
  { top: 5,  left: 92, depth: 0.40, dur: 8,  delay: 3.4 },
  { top: 24, left: 68, depth: 0.75, dur: 10, delay: 0.9 },
  { top: 12, left: 60, depth: 1.20, dur: 11, delay: 4.6 },
  { top: 36, left: 94, depth: 0.55, dur: 9,  delay: 2.0 },
  { top: 30, left: 51, depth: 0.90, dur: 13, delay: 5.8 },
  { top: 44, left: 79, depth: 0.35, dur: 8,  delay: 1.3 },
  { top: 53, left: 44, depth: 1.00, dur: 10, delay: 3.9 },
  { top: 63, left: 90, depth: 0.70, dur: 12, delay: 0.2 },
]

/** Wordmark glyph — stroke-only, sits beside the "Faro" lockup. */
export function BeaconGlyph({ size = 17 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M9.5 21h5l-1.2-11h-2.6L9.5 21z" stroke="#0a0a0a" strokeWidth="1.35" strokeLinejoin="round" />
      <path d="M10 10h4l-.5-3.5h-3L10 10z" stroke="#0a0a0a" strokeWidth="1.35" strokeLinejoin="round" />
      <path d="M10.7 6.5h2.6L12 3.6 10.7 6.5z" stroke="#0a0a0a" strokeWidth="1.35" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * Lighthouse — small and correctly proportioned: a tower that TAPERS (wide
 * base, narrow top), a gallery ledge that overhangs, a glazed lamp room and a
 * low roof. The previous version was a uniform-width spike, which read as an
 * obelisk rather than a lighthouse.
 */
function Lighthouse() {
  return (
    <svg viewBox="0 0 72 132" width="100%" fill="none" aria-hidden="true">
      {/* Unbroken silhouette. The white banding tried earlier chopped the
          tower into segments and read as a chess piece — a clean tapered
          profile is both more elegant and more legible at this size. */}
      <path d="M24 132 L48 132 L41.5 47 L30.5 47 Z" fill="#0a0a0a" />
      {/* gallery ledge — overhangs the tower */}
      <path d="M26.5 41.5 L45.5 41.5 L44 47 L28 47 Z" fill="#0a0a0a" />
      {/* lamp room, with a single glazed opening */}
      <rect x="30" y="24" width="12" height="17.5" fill="#0a0a0a" />
      <rect x="33.4" y="29" width="5.2" height="8" fill="#fff" opacity="0.95" />
      {/* roof + finial */}
      <path d="M27.5 24 L44.5 24 L36 13.5 Z" fill="#0a0a0a" />
      <rect x="35.3" y="8.5" width="1.4" height="5" fill="#0a0a0a" />
    </svg>
  )
}

export function AmbientScene() {
  const dotsRef = useRef<HTMLDivElement>(null)

  // Cursor parallax on the dot layer only. The long ease-out on the inline
  // transition is what makes them settle back slowly instead of snapping.
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let frame = 0
    function onMove(e: PointerEvent) {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const el = dotsRef.current
        if (!el) return
        const dx = (e.clientX / window.innerWidth  - 0.5) * 2
        const dy = (e.clientY / window.innerHeight - 0.5) * 2
        el.style.transform = `translate3d(${(-dx * 10).toFixed(2)}px, ${(-dy * 8).toFixed(2)}px, 0)`
      })
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <div aria-hidden="true" style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>

      {/* ONE directional wash — light falling from the lamp across the page. */}
      <div className="auth-wash" style={{
        position: 'absolute', top: `${LAMP.y - 46}%`, left: `${LAMP.x - 46}%`,
        width: '92vmax', height: '92vmax',
        background: `radial-gradient(circle, rgba(${C.warm},0.055) 0%, rgba(${C.warm},0.020) 26%, rgba(${C.cool},0.028) 52%, transparent 70%)`,
        animation: 'auth-wash 20s ease-in-out infinite',
      }} />

      {/* Signal rings — crisp expanding hairlines from the lamp. */}
      {RINGS.map(i => (
        <div key={i} className="auth-ring auth-scenery" style={{
          position: 'absolute', top: `${LAMP.y}%`, left: `${LAMP.x}%`,
          width: '96vmax', height: '96vmax',
          border: `1px solid rgba(${C.warm},0.30)`, borderRadius: '50%',
          transform: 'translate(-50%, -50%) scale(0.06)',
          animation: `auth-ring ${RING_PERIOD_S}s cubic-bezier(0.22, 0.61, 0.36, 1) ${(i * RING_PERIOD_S) / RINGS.length}s infinite`,
        }} />
      ))}

      {/* Sky — a barely-there tone that deepens toward the horizon. Kept to a
          single stop so it never accumulates into the grey wash problem. */}
      <div className="auth-scenery" style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: `${HORIZON}%`,
        background: `linear-gradient(180deg, rgba(${C.cool},0.030) 0%, rgba(${C.cool},0.014) 46%, rgba(${C.cool},0.050) 100%)`,
      }} />

      {/* Clouds — flat, wide, cool. Blurred here is fine: a cloud is the one
          thing that SHOULD be soft, so it doesn't read as smudge. */}
      {CLOUDS.map((c, i) => (
        <div key={i} className="auth-cloud auth-scenery" style={{
          position: 'absolute', top: `${c.top}%`, left: `${c.left}%`,
          width: `${c.w}vw`, height: `${c.h}vh`,
          transform: 'translateX(-50%)',
          background: `radial-gradient(ellipse, rgba(${C.cool},${c.o}) 0%, rgba(${C.cool},${c.o * 0.5}) 45%, transparent 72%)`,
          filter: 'blur(14px)',
          animation: `auth-cloud-drift ${c.dur}s ease-in-out ${i * 6}s infinite`,
        }} />
      ))}

      {/* Birds — open chevrons, wings flexing on slightly different cycles */}
      {BIRDS.map((b, i) => (
        <svg key={i} className="auth-bird auth-scenery" viewBox="0 0 20 8" aria-hidden="true"
          style={{
            position: 'absolute', top: `${b.top}%`, left: `${b.left}%`,
            width: b.size, overflow: 'visible', opacity: b.o,
            animation: `auth-bird-glide ${b.dur * 6}s ease-in-out ${b.delay}s infinite`,
          }}>
          <path d="M1 6 Q5 1 10 5 Q15 1 19 6" fill="none"
            stroke={`rgb(${C.coolDeep})`} strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      ))}

      {/* Bearing ring — degree ticks around the lamp, longer every 90°. Reads
          as instrumentation: the beacon measuring its own sweep. */}
      <div className="auth-bearing auth-scenery" style={{
        position: 'absolute', top: `${LAMP.y}%`, left: `${LAMP.x}%`,
        width: 'min(30vmax, 380px)', height: 'min(30vmax, 380px)',
        transform: 'translate(-50%, -50%)',
        animation: 'auth-bearing 90s linear infinite',
      }}>
        {BEARINGS.map(deg => {
          const major = deg % 90 === 0
          return (
            <span key={deg} style={{
              position: 'absolute', top: 0, left: '50%', width: 1,
              height: major ? 9 : 4,
              background: `rgba(${C.cool},${major ? 0.34 : 0.18})`,
              transformOrigin: '50% min(15vmax, 190px)',
              transform: `translateX(-50%) rotate(${deg}deg)`,
            }} />
          )
        })}
      </div>

      {/* Horizon hairline — the line the towers stand on. */}
      <div className="auth-scenery" style={{
        position: 'absolute', top: `${HORIZON}%`, left: 0, right: 0, height: 1,
        background: `linear-gradient(90deg, transparent 0%, rgba(${C.coolDeep},0.16) 12%, rgba(${C.coolDeep},0.26) 62%, rgba(${C.coolDeep},0.17) 92%, transparent 100%)`,
      }} />

      {/* Sea — receding hairlines below the horizon */}
      {SEA.map((s, i) => (
        <div key={i} className="auth-swell auth-scenery" style={{
          position: 'absolute', top: `${HORIZON + s.top}%`,
          left: `${s.inset}%`, right: `${s.inset * 0.5}%`, height: 1,
          background: `linear-gradient(90deg, transparent, rgba(${C.coolDeep},${s.opacity * 1.5}) 25%, rgba(${C.coolDeep},${s.opacity * 1.5}) 75%, transparent)`,
          animation: `auth-swell-${s.anim} ${s.dur}s ease-in-out ${i * 0.7}s infinite`,
        }} />
      ))}

      {/* The lamp's reflection on the water. Masked on BOTH axes — a plain
          rectangle with a vertical gradient still shows hard left/right
          edges and reads as a grey bar rather than light on water. */}
      <div className="auth-reflection auth-scenery" style={{
        position: 'absolute', top: `${HORIZON}%`, left: `${LAMP.x}%`,
        width: 34, height: `${100 - HORIZON}%`,
        transform: 'translateX(-50%)',
        background: `linear-gradient(180deg, rgba(${C.warm},0.20) 0%, rgba(${C.warm},0.07) 28%, transparent 70%)`,
        maskImage: 'linear-gradient(90deg, transparent, #000 42%, #000 58%, transparent)',
        WebkitMaskImage: 'linear-gradient(90deg, transparent, #000 42%, #000 58%, transparent)',
        filter: 'blur(1.5px)',
        animation: 'auth-reflection 14s ease-in-out infinite',
      }} />

      {/* Crisp dots, parallaxed as one layer */}
      <div
        ref={dotsRef}
        style={{ position: 'absolute', inset: 0, transition: 'transform 1.7s cubic-bezier(0.16, 1, 0.3, 1)', willChange: 'transform' }}
      >
        {DOTS.map((d, i) => (
          <span key={i} className="auth-dot auth-drift" style={{
            position: 'absolute', top: `${d.top}%`, left: `${d.left}%`,
            width: d.depth > 1 ? 3 : 2, height: d.depth > 1 ? 3 : 2,
            borderRadius: '50%', background: i % 3 === 0 ? `rgba(${C.warm},0.85)` : `rgba(${C.cool},0.7)`,
            animation:
              `auth-dot ${d.dur}s ease-in-out ${d.delay}s infinite,` +
              `auth-drift-${(['a', 'b', 'c'] as const)[i % 3]} ${d.dur * 2.4}s ease-in-out ${d.delay}s infinite`,
          }} />
        ))}
      </div>

      {/* The lighthouse — anchored by its BASE to the horizon (bottom, not
          top, positioning), which is what keeps it standing on the line
          instead of hovering above it. */}
      <div className="auth-scenery" style={{
        position: 'absolute',
        bottom: `${100 - HORIZON}%`, left: `${LAMP.x}%`, height: `${TOWER_H}%`,
        aspectRatio: '72 / 132',
        transform: 'translateX(-50%)',
      }}>
        <Lighthouse />
      </div>

      {/* A second, far smaller tower further down the coast. Pure scale cue —
          it is what gives the horizon actual distance rather than being a
          line with one object on it. Held at 57% so it clears the widest
          card (signup) and its footer link on both screens. */}
      <div className="auth-scenery" style={{
        position: 'absolute',
        bottom: `${100 - HORIZON}%`, left: '57%', height: `${TOWER_H * 0.22}%`,
        aspectRatio: '72 / 132', transform: 'translateX(-50%)', opacity: 0.11,
      }}>
        <Lighthouse />
      </div>

      {/* A small boat out on the water — the one element with narrative in it,
          and a second scale cue below the horizon rather than on it. */}
      <div className="auth-boat auth-scenery" style={{
        position: 'absolute', top: `${HORIZON + 3.4}%`, left: '34%',
        width: 'clamp(20px, 1.9vw, 30px)',
        animation: 'auth-boat-bob 9s ease-in-out infinite',
      }}>
        <svg viewBox="0 0 40 34" width="100%" aria-hidden="true">
          {/* hull + mast + two sails */}
          <path d="M3 27 L37 27 L32 33 L8 33 Z" fill={`rgba(${C.ink},0.62)`} />
          <rect x="19.3" y="4" width="1.4" height="23" fill={`rgba(${C.ink},0.62)`} />
          <path d="M18.6 25 L18.6 6 L7 25 Z" fill={`rgba(${C.ink},0.52)`} />
          <path d="M21.4 25 L21.4 9 L31 25 Z" fill={`rgba(${C.ink},0.40)`} />
        </svg>
      </div>

      {/* Warm bloom right at the lamp — small, tight, and the only place on
          the page where colour reaches full strength. */}
      <div className="auth-lampglow auth-scenery" style={{
        position: 'absolute', top: `${LAMP.y}%`, left: `${LAMP.x}%`,
        width: 104, height: 104, transform: 'translate(-50%, -50%)',
        background: `radial-gradient(circle, rgba(${C.warm},0.30) 0%, rgba(${C.warm},0.09) 36%, transparent 66%)`,
        animation: 'auth-lampglow 7s ease-in-out infinite',
      }} />

      {/* Light motes — soft, travelling points of light. Each is a radial core
          plus a box-shadow bloom; on a white page that bloom is what sells
          them as luminous instead of just being small coloured circles. */}
      {MOTES.map((m, i) => {
        const rgb = m.warm ? C.warm : C.cool
        return (
          <span key={i} className="auth-mote" style={{
            position: 'absolute', bottom: `${m.start}%`, left: `${m.left}%`,
            width: m.size, height: m.size, borderRadius: '50%',
            background: `radial-gradient(circle, rgba(${rgb},0.85) 0%, rgba(${rgb},0.35) 45%, transparent 72%)`,
            boxShadow: `0 0 ${m.size * 2}px ${m.size * 0.5}px rgba(${rgb},0.20)`,
            animation: `auth-mote-${m.v} ${m.dur}s linear ${m.delay}s infinite`,
          }} />
        )
      })}

      {/* Contact shadow — a short ellipse where the tower meets the horizon.
          Grounds it without the full mirrored reflection, which read as a
          detached smudge. */}
      <div className="auth-scenery" style={{
        position: 'absolute', top: `${HORIZON}%`, left: `${LAMP.x}%`,
        width: 130, height: 9, transform: 'translate(-50%, -50%)',
        background: `radial-gradient(ellipse, rgba(${C.coolDeep},0.16) 0%, transparent 70%)`,
      }} />
    </div>
  )
}

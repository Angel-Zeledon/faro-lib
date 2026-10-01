'use client'
// The forecasting engine, told on the landing: the workflow as an animated
// diagram (EngineFlow) and the model competition in detail (ModelsSection).
//
// Every sentence these render comes from `L.engine` in i18n/landing.ts, where
// each claim carries a comment naming the file that backs it. The illustration
// beside the flow is drawn from a fixed formula, not from data, and its caption
// says so — it shows the SHAPE of the process (history, candidates, a winner
// with a band, a signal), never a result.
//
// Motion: one sequence per block, played once when it scrolls into view —
// the rail of the flow draws down and its steps arrive in order; the chart
// draws its history, fans out the candidates, dims the losers and leaves the
// winner with its band. Transform, opacity and stroke-dashoffset only. With
// reduced motion, or before JS runs, everything is simply there.
import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LANDING } from '@/i18n/landing'
import { Section, Tag, H2, H3, Lead, Dash } from '@/components/landing/primitives'

// ── Reveal-once hook ──────────────────────────────────────────────────────────
// `armed` is true only while JS is running, motion is allowed and the block
// has not been seen yet — the hidden starting state is never the default.
function useArmedInView<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [state, setState] = useState<'idle' | 'armed' | 'in'>('idle')
  useEffect(() => {
    const el = ref.current
    if (!el || !('IntersectionObserver' in window)) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    // Already on screen at mount (a reload halfway down): play straight away.
    setState('armed')
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        setState('in')
        io.disconnect()
      }
    }, { threshold: 0.18 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return { ref, armed: state === 'armed', played: state === 'in' }
}

// ── The illustration's geometry ───────────────────────────────────────────────
// Fixed functions, computed once. History is a weekly wave over a mild trend;
// three candidates leave "today" in different directions; the winner carries
// the wave on, inside a band that widens with the horizon.
const W = 520
const H = 280
const TODAY_X = 318
const STEP = 9

function wave(i: number) {
  return 178 - 30 * Math.sin(i / 2.1) - 12 * Math.sin(i / 5.3) - 6 * Math.sin(i * 1.9) - i * 0.6
}

function pathOf(points: [number, number][]) {
  return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

const HIST_N = Math.floor((TODAY_X - 20) / STEP)
const histPts: [number, number][] = Array.from({ length: HIST_N + 1 }, (_, i) => [20 + i * STEP, wave(i)])
const FUT_N = Math.floor((W - 24 - TODAY_X) / STEP)
const futX = (k: number) => TODAY_X + k * STEP
const startY = wave(HIST_N)

const winnerPts: [number, number][] = Array.from({ length: FUT_N + 1 }, (_, k) => [futX(k), wave(HIST_N + k)])
const candidatePaths = [
  // Flat: repeats the level and misses the cycle.
  Array.from({ length: FUT_N + 1 }, (_, k): [number, number] => [futX(k), startY - k * 0.15]),
  // Overshoots: reads the last rise as a trend.
  Array.from({ length: FUT_N + 1 }, (_, k): [number, number] => [futX(k), startY - k * 2.1 - 8 * Math.sin(k / 2.1)]),
  // Lags the cycle by half a period.
  Array.from({ length: FUT_N + 1 }, (_, k): [number, number] => [futX(k), wave(HIST_N + k + 6) + 14]),
].map(pathOf)

const bandPath = (() => {
  const upper = winnerPts.map(([x, y], k) => [x, y - 10 - k * 1.05] as [number, number])
  const lower = winnerPts.map(([x, y], k) => [x, y + 10 + k * 1.05] as [number, number]).reverse()
  return `${pathOf([...upper, ...lower])} Z`
})()

// ── The workflow ──────────────────────────────────────────────────────────────
export function EngineFlow() {
  const { lang } = useLanguage()
  const E = LANDING[lang].engine
  const flow = useArmedInView<HTMLOListElement>()
  const fig = useArmedInView<HTMLDivElement>()

  return (
    <div className="ef-grid">
      <ol
        ref={flow.ref}
        className={`ef-flow${flow.armed ? ' is-armed' : ''}${flow.played ? ' is-in' : ''}`}
        aria-label={E.flowLabel}
      >
        {E.flow.map(({ title, desc, detail }, i) => (
          <li key={title} className="ef-step" style={{ '--i': i } as React.CSSProperties}>
            <span className="ef-num" aria-hidden>{i + 1}</span>
            <div className="ef-body">
              <h3 className="ef-title">{title}</h3>
              <p className="ef-desc">{desc}</p>
              <p className="ef-detail">{detail}</p>
            </div>
          </li>
        ))}
      </ol>

      <figure className="ef-fig">
        <div
          ref={fig.ref}
          className={`ei${fig.armed ? ' is-armed' : ''}${fig.played ? ' is-in' : ''}`}
        >
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={E.illus.caption}>
            <line className="ei-axis" x1={20} y1={H - 22} x2={W - 20} y2={H - 22} />
            <path className="ei-band" d={bandPath} />
            <line className="ei-today" x1={TODAY_X} y1={18} x2={TODAY_X} y2={H - 22} />
            {/* Left of the line: the signal badge owns the top-right corner. */}
            <text className="ei-today-label" x={TODAY_X - 8} y={34} textAnchor="end">{E.illus.today}</text>
            <path className="ei-hist" d={pathOf(histPts)} pathLength={1} />
            {candidatePaths.map((d, i) => (
              <path key={i} className="ei-cand" d={d} pathLength={1} style={{ '--c': i } as React.CSSProperties} />
            ))}
            <path className="ei-win" d={pathOf(winnerPts)} pathLength={1} />
          </svg>
          <span className="ei-signal"><i aria-hidden />{E.illus.signal}</span>
        </div>
        <ul className="ei-legend" aria-hidden>
          <li><i className="k-hist" />{E.illus.history}</li>
          <li><i className="k-cand" />{E.illus.candidates}</li>
          <li><i className="k-win" />{E.illus.winner}</li>
          <li><i className="k-band" />{E.illus.band}</li>
        </ul>
        <figcaption className="ei-caption">{E.illus.caption}</figcaption>
      </figure>
    </div>
  )
}

// ── The models, in detail ─────────────────────────────────────────────────────
export function ModelsSection({ alt = true }: { alt?: boolean }) {
  const { lang } = useLanguage()
  const E = LANDING[lang].engine
  return (
    <Section id="motor" alt={alt}>
      <Tag>{E.tag}</Tag>
      <H2>{E.title}</H2>
      <Lead maxWidth={700}>{E.lead}</Lead>

      <div className="split em-top">
        <div>
          <H3>{E.routingTitle}</H3>
          <div className="lp-table">
              <div className="lp-table-head em-row">
                <div className="lp-label">{E.routingHead[0]}</div>
                <div className="lp-label">{E.routingHead[1]}</div>
              </div>
              {E.routing.map(({ pattern, when, models }) => (
                <div key={pattern} className="lp-table-row em-row">
                  <div>
                    <div className="em-pattern">{pattern}</div>
                    <div className="em-when">{when}</div>
                  </div>
                  <div className="em-models">{models}</div>
                </div>
              ))}
          </div>
          <p className="em-note">{E.routingNote}</p>
        </div>
        <div>
          <H3>{E.alwaysTitle}</H3>
          <ul className="trust-list em-always">
            {E.always.map(({ title, desc }) => (
              <li key={title} className="trust-item">
                <h4 className="trust-title">{title}</h4>
                <p className="trust-desc">{desc}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <H3 style={{ marginTop: 64 }}>{E.ideasTitle}</H3>
      <ul className="trust-list">
        {E.ideas.map(({ title, desc }) => (
          <li key={title} className="trust-item">
            <h4 className="trust-title">{title}</h4>
            <p className="trust-desc">{desc}</p>
          </li>
        ))}
      </ul>

      <div className="split em-bottom">
        <div>
          <H3>{E.assistantTitle}</H3>
          <p className="em-p">{E.assistantBody}</p>
          <ul className="em-points">
            {E.assistantPoints.map(p => <li key={p}>{p}</li>)}
          </ul>
        </div>
        <div className="em-limits">
          <H3>{E.limitsTitle}</H3>
          <ul>
            {E.limits.map(l => (
              <li key={l}><Dash /><span>{l}</span></li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  )
}

// ── Stylesheet ────────────────────────────────────────────────────────────────
// Injected by the pages that render these blocks (see LandingPage and
// Subpages), next to LANDING_CSS.
export const ENGINE_CSS = `
.ef-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr); gap: 56px; align-items: start; margin-bottom: 56px; }

/* The flow: a numbered rail. The rail is a sequence, so the numbers are real. */
.ef-flow { list-style: none; margin: 0; padding: 0; position: relative; }
.ef-flow::before {
 content: ''; position: absolute; left: 15px; top: 16px; bottom: 16px; width: 2px; border-radius: 2px;
 background: linear-gradient(180deg, var(--lp-accent), var(--lp-accent-bd));
 transform-origin: 50% 0;
}
.ef-step { position: relative; display: grid; grid-template-columns: 32px 1fr; gap: 18px; padding-bottom: 26px; }
.ef-step:last-child { padding-bottom: 0; }
.ef-num {
 position: relative; z-index: 1; width: 32px; height: 32px; border-radius: 50%;
 display: flex; align-items: center; justify-content: center;
 font-family: var(--font-brand), system-ui, sans-serif; font-size: 13.5px; font-weight: 700;
 color: var(--lp-accent); background: var(--lp-bg); border: 2px solid var(--lp-accent);
}
.sec-alt .ef-num { background: var(--lp-bg2); }
.ef-title { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 4px 0 6px; line-height: 1.35; }
.ef-desc { font-size: 14px; color: var(--lp-body); line-height: 1.68; margin: 0 0 6px; max-width: 60ch; }
.ef-detail { font-size: 13px; color: var(--lp-muted); line-height: 1.5; margin: 0; }

.ef-flow.is-armed::before { transform: scaleY(0); }
.ef-flow.is-armed .ef-num, .ef-flow.is-armed .ef-body { opacity: 0; }
.ef-flow.is-in::before { animation: ef-rail 1700ms cubic-bezier(0.65, 0, 0.35, 1) both; }
.ef-flow.is-in .ef-num { animation: ef-node 520ms cubic-bezier(0.16, 1, 0.3, 1) both; animation-delay: calc(var(--i) * 190ms); }
.ef-flow.is-in .ef-body { animation: ef-body 640ms cubic-bezier(0.16, 1, 0.3, 1) both; animation-delay: calc(80ms + var(--i) * 190ms); }
@keyframes ef-rail { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@keyframes ef-node { from { opacity: 0; transform: scale(0.6); } to { opacity: 1; transform: none; } }
@keyframes ef-body { from { opacity: 0; transform: translate3d(10px, 0, 0); } to { opacity: 1; transform: none; } }

/* The illustration. */
.ef-fig { margin: 0; position: sticky; top: 96px; }
.ei { position: relative; border: 1px solid var(--lp-border); border-radius: 14px; background: var(--lp-bg); padding: 14px 10px 6px; }
.sec-alt .ei { background: var(--lp-bg); }
.ei svg { display: block; width: 100%; height: auto; overflow: visible; }
.ei-axis { stroke: var(--lp-border-strong); stroke-width: 1; }
.ei-today { stroke: var(--lp-dim); stroke-width: 1; stroke-dasharray: 3 4; }
.ei-today-label { fill: var(--lp-muted); font-size: 14px; font-weight: 600; font-family: system-ui, sans-serif; }
.ei-hist { fill: none; stroke: var(--lp-text); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.ei-cand { fill: none; stroke: var(--lp-dim); stroke-width: 1.6; stroke-linecap: round; opacity: 0.22; }
.ei-win { fill: none; stroke: var(--lp-accent); stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }
.ei-band { fill: var(--lp-accent-bg); stroke: var(--lp-accent-bd); stroke-width: 1; }
.ei-signal {
 position: absolute; right: 14px; top: 12px; display: inline-flex; align-items: center; gap: 7px;
 font-size: 11.5px; font-weight: 800; letter-spacing: 0.02em; color: var(--lp-amber);
 padding: 5px 10px; border-radius: 999px; background: var(--lp-bg); border: 1px solid var(--lp-border);
}
.ei-signal i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: block; }
.ei-legend { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12.5px; color: var(--lp-muted); }
.ei-legend li { display: inline-flex; align-items: center; gap: 8px; }
.ei-legend i { display: block; width: 18px; height: 0; border-top: 2px solid var(--lp-text); }
.ei-legend .k-cand { border-top: 1.6px solid var(--lp-dim); opacity: 0.6; }
.ei-legend .k-win { border-top: 3px solid var(--lp-accent); }
.ei-legend .k-band { height: 10px; border: 1px solid var(--lp-accent-bd); background: var(--lp-accent-bg); border-radius: 2px; }
.ei-caption { margin-top: 8px; font-size: 12px; color: var(--lp-dim); }

.ei.is-armed .ei-hist, .ei.is-armed .ei-cand, .ei.is-armed .ei-win { stroke-dasharray: 1; stroke-dashoffset: 1; }
.ei.is-armed .ei-band, .ei.is-armed .ei-signal { opacity: 0; }
.ei.is-in .ei-hist, .ei.is-in .ei-cand, .ei.is-in .ei-win { stroke-dasharray: 1; }
.ei.is-in .ei-hist { animation: ei-draw 1100ms cubic-bezier(0.45, 0, 0.25, 1) both; }
.ei.is-in .ei-cand { animation: ei-draw 620ms cubic-bezier(0.45, 0, 0.25, 1) both, ei-dim 600ms ease both; animation-delay: calc(1100ms + var(--c) * 140ms), 2150ms; }
.ei.is-in .ei-band { animation: ei-fade 620ms ease 2350ms both; }
.ei.is-in .ei-win { animation: ei-draw 820ms cubic-bezier(0.45, 0, 0.25, 1) 2350ms both; }
.ei.is-in .ei-signal { animation: ei-pop 520ms cubic-bezier(0.16, 1, 0.3, 1) 3150ms both; }
@keyframes ei-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
@keyframes ei-dim { from { opacity: 0.85; } to { opacity: 0.22; } }
@keyframes ei-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes ei-pop { from { opacity: 0; transform: translate3d(0, 6px, 0) scale(0.94); } to { opacity: 1; transform: none; } }

/* The models section. */
.em-top { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr); gap: 48px; align-items: start; }
.em-row { display: grid; grid-template-columns: 200px 1fr; gap: 16px; }
.em-pattern { font-size: 14px; font-weight: 700; color: var(--lp-text); }
.em-when { font-size: 12.5px; color: var(--lp-muted); line-height: 1.45; margin-top: 2px; }
.em-models { font-size: 13.5px; color: var(--lp-body); line-height: 1.55; }
.em-note { font-size: 13.5px; color: var(--lp-body); line-height: 1.65; margin: 16px 0 0; max-width: 62ch; }
.em-always { grid-template-columns: 1fr !important; }
.em-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; margin-top: 64px; padding-top: 40px; border-top: 1px solid var(--lp-border); }
.em-p { font-size: 15px; color: var(--lp-body); line-height: 1.72; margin: 0 0 14px; max-width: 60ch; }
.em-points { margin: 0; padding-left: 18px; font-size: 14px; color: var(--lp-body); line-height: 1.65; }
.em-points li + li { margin-top: 6px; }
.em-limits ul { list-style: none; margin: 0; padding: 0; }
.em-limits li { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; color: var(--lp-body); line-height: 1.6; padding: 8px 0; }
.em-limits li svg { margin-top: 3px; }

@media (max-width: 900px) {
 .ef-grid { grid-template-columns: 1fr; gap: 36px; }
 .ef-fig { position: static; order: -1; }
 .em-top, .em-bottom { grid-template-columns: minmax(0, 1fr); gap: 32px; }
}
@media (max-width: 760px) {
 /* The routing table stacks: pattern, then who competes, row by row. */
 .em-row { grid-template-columns: minmax(0, 1fr); gap: 6px; }
 .lp-table-head.em-row { display: none; }
 .em-models { font-weight: 600; color: var(--lp-text); }
 .ei-signal { font-size: 10.5px; right: 10px; top: 8px; }
}
/* useArmedInView never arms under reduced motion; this is the belt to that. */
@media (prefers-reduced-motion: reduce) {
 .ef-flow::before, .ef-num, .ef-body, .ei-hist, .ei-cand, .ei-win, .ei-band, .ei-signal { animation: none !important; transform: none !important; stroke-dashoffset: 0 !important; }
}
`

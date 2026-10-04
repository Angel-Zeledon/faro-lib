// The landing's colour layer (owner, 2026-10-02: "mete más color al home").
//
// Kept apart from theme.ts on purpose: theme.ts is structure and the Petróleo
// base; this file only paints over it — tinted bands, a small harmonious
// accent palette, coloured icon tiles, gradient numbers, vivid chart series.
// Delete the import in primitives.tsx and the page is back to its quiet self.
//
// Rules this layer keeps:
//  · The insignia (#0C3A40) and the teal accent stay the anchors; the five
//    extra hues (indigo, violet, coral, gold, rose) are decoration only.
//  · The semáforo keeps its meaning: red / amber / green (and the stock
//    signal chips) are never repainted here, and no decorative surface uses
//    plain red, amber, green or blue.
//  · Static paint only — gradients, no blur filters, no animated backgrounds,
//    nothing that moves layout. The one new animation (ef-num fill) is
//    transform/opacity-free colour on an element that already animates.
//  · Text on colour clears AA: white on the dark ends of the tiles, the
//    light-theme hues are the 600-700 steps, the dark-theme ones the 300-400.

function icon(paths: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
}

// Lucide shapes (ISC), one per feature group, in the order the groups appear
// in i18n/landing.ts: purchasing, AI forecasting, inventory & warehouses,
// suppliers, finance, integrations & team.
const FEATURE_ICONS = [
  icon('<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>'),
  icon('<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/>'),
  icon('<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>'),
  icon('<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>'),
  icon('<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>'),
  icon('<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>'),
]
const FEATURE_HUES = ['teal', 'violet', 'rose', 'indigo', 'gold', 'cyan']
// The cycle every repeated block (cards, trust items) walks through.
const CYCLE = ['teal', 'indigo', 'coral', 'violet', 'gold', 'rose']

// The problem section is the one place the warm end of the palette leads.
const WARM_CYCLE = ['coral', 'gold', 'rose', 'violet', 'indigo', 'teal']

const cycleRules = (selector: string, order = CYCLE, n = order.length) =>
  order.slice(0, n).map((hue, i) => `${selector}:nth-child(${n}n+${i + 1}) { --cw: var(--cw-${hue}); }`).join('\n')

export const COLORWAY_CSS = `
:root, [data-theme="light"] {
 --cw-teal: #0F766E; --cw-indigo: #4F46E5; --cw-violet: #7C3AED; --cw-coral: #D9483F; --cw-gold: #A86A12; --cw-rose: #C2306E; --cw-cyan: #0E7490;
 --cw-mint: #2BB3A3;
 --cw-wash-teal: rgba(15,118,110,0.14); --cw-wash-indigo: rgba(79,70,229,0.11); --cw-wash-violet: rgba(124,58,237,0.09);
 --cw-wash-coral: rgba(217,72,63,0.09); --cw-wash-gold: rgba(196,132,22,0.12); --cw-wash-rose: rgba(194,48,110,0.08);
 --cw-tile-ink: #ffffff;
}
[data-theme="dark"] {
 --cw-teal: #3CC7B8; --cw-indigo: #8B87FF; --cw-violet: #B794F6; --cw-coral: #FF8A7A; --cw-gold: #F2B84B; --cw-rose: #F472B6; --cw-cyan: #67D4EC;
 --cw-mint: #5EEAD4;
 --cw-wash-teal: rgba(60,199,184,0.10); --cw-wash-indigo: rgba(139,135,255,0.09); --cw-wash-violet: rgba(183,148,246,0.08);
 --cw-wash-coral: rgba(255,138,122,0.07); --cw-wash-gold: rgba(242,184,75,0.07); --cw-wash-rose: rgba(244,114,182,0.06);
 --cw-tile-ink: #08191C;
}

/* ── Hero: a still mesh of the palette behind the dot grid ── */
.hero-bg {
 background:
  radial-gradient(46% 40% at 4% 4%, color-mix(in srgb, var(--cw-teal) 20%, transparent), transparent 72%),
  radial-gradient(40% 36% at 96% 2%, color-mix(in srgb, var(--cw-indigo) 16%, transparent), transparent 72%),
  radial-gradient(36% 32% at 84% 44%, color-mix(in srgb, var(--cw-violet) 12%, transparent), transparent 72%),
  radial-gradient(32% 28% at 10% 50%, color-mix(in srgb, var(--cw-gold) 12%, transparent), transparent 72%);
}
.hero-pill { transition: border-color 160ms ease, color 160ms ease, background-color 160ms ease; }
${CYCLE.map((hue, i) => `.hero-pills .hero-pill:nth-child(6n+${i + 2}) { --cw: var(--cw-${hue}); }`).join('\n')}
@media (hover: hover) and (pointer: fine) {
 .hero-pill:hover { border-color: var(--cw, var(--lp-accent)); color: var(--cw, var(--lp-accent)); }
}
.lp-frame { background: linear-gradient(135deg, var(--cw-teal), var(--lp-border-strong) 30%, var(--lp-border-strong) 70%, var(--cw-indigo)); }

/* ── Stats band: deep petrol with colour, each figure in its own gradient ── */
.strip-shell {
 background:
  radial-gradient(40% 120% at 0% 0%, rgba(43,179,163,0.30), transparent 70%),
  radial-gradient(40% 120% at 100% 100%, rgba(124,108,255,0.28), transparent 70%),
  radial-gradient(30% 90% at 55% 0%, rgba(242,184,75,0.12), transparent 70%),
  var(--lp-strip);
}
.strip-value { background: linear-gradient(120deg, #7FF0DF, #3CC7B8); -webkit-background-clip: text; background-clip: text; color: transparent; padding-bottom: 4px; }
.strip-cell:nth-child(2) .strip-value { background-image: linear-gradient(120deg, #C9B8FF, #F09AD0); }
.strip-cell:nth-child(3) .strip-value { background-image: linear-gradient(120deg, #FFD98A, #FF9E80); }
.strip-cell:nth-child(4) .strip-value { background-image: linear-gradient(120deg, #A9B5FF, #7FE3D6); }
.strip-label { color: rgba(231,240,239,0.78); }

/* ── Bands: every tinted section gets its own still wash ── */
.sec-alt {
 background:
  radial-gradient(48% 60% at 100% 0%, var(--cw-wash-indigo), transparent 70%),
  radial-gradient(40% 50% at 0% 100%, var(--cw-wash-teal), transparent 70%),
  var(--lp-bg2);
}
#problema.sec-alt { background:
  radial-gradient(50% 60% at 100% 0%, var(--cw-wash-coral), transparent 70%),
  radial-gradient(44% 50% at 0% 100%, var(--cw-wash-gold), transparent 70%), var(--lp-bg2); }
#motor.sec-alt { background:
  radial-gradient(50% 50% at 0% 0%, var(--cw-wash-indigo), transparent 70%),
  radial-gradient(44% 50% at 100% 60%, var(--cw-wash-violet), transparent 70%), var(--lp-bg2); }
#funciones.sec-alt { background:
  radial-gradient(36% 40% at 0% 0%, var(--cw-wash-teal), transparent 70%),
  radial-gradient(36% 40% at 100% 20%, var(--cw-wash-violet), transparent 70%),
  radial-gradient(36% 40% at 50% 100%, var(--cw-wash-gold), transparent 70%), var(--lp-bg2); }
#precio.sec-alt { background:
  radial-gradient(46% 50% at 100% 0%, var(--cw-wash-gold), transparent 70%),
  radial-gradient(40% 50% at 0% 100%, var(--cw-wash-teal), transparent 70%), var(--lp-bg2); }
#preguntas.sec-alt, #nosotros.sec-alt { background:
  radial-gradient(46% 60% at 100% 100%, var(--cw-wash-violet), transparent 70%),
  radial-gradient(36% 50% at 0% 0%, var(--cw-wash-teal), transparent 70%), var(--lp-bg2); }
.sec-alt::before { background: linear-gradient(90deg, transparent, var(--cw-teal) 25%, var(--cw-indigo) 55%, var(--cw-violet) 75%, transparent); opacity: 0.35; }

/* ── Cards: each one carries a hue, on its bar and on hover ── */
.lp-card { --cw: var(--cw-teal); }
${cycleRules('#problema .lp-card', WARM_CYCLE)}
${cycleRules('#incluye .lp-card')}
${cycleRules('#tecnico .lp-card')}
.lp-bar { width: 36px; background: linear-gradient(90deg, var(--cw), color-mix(in srgb, var(--cw) 40%, transparent)); }
@media (hover: hover) and (pointer: fine) {
 .lp-card:hover { border-color: color-mix(in srgb, var(--cw) 45%, var(--lp-border)); }
}
#problema .lp-card, #incluye .lp-card { background: linear-gradient(180deg, color-mix(in srgb, var(--cw) 7%, var(--lp-bg)), var(--lp-bg) 55%); }
#incluye .lp-card::before, #tecnico .lp-card::before { content: ''; position: absolute; top: -1px; left: 24px; width: 40px; height: 2px; border-radius: 2px; background: var(--cw); }

/* ── The engine: a rail that runs through the palette, vivid candidates.
   Prefixed with .lp: ENGINE_CSS is injected after this sheet. ── */
.lp .ef-flow::before { background: linear-gradient(180deg, var(--cw-teal), var(--cw-indigo) 55%, var(--cw-violet)); }
.lp .ef-num { --cw-step: color-mix(in srgb, var(--cw-violet) calc(var(--i, 0) * 13%), var(--cw-teal)); color: var(--cw-tile-ink); background: var(--cw-step); border-color: var(--cw-step); }
.lp .sec-alt .ef-num { background: var(--cw-step); }
.lp .ei { background: linear-gradient(180deg, var(--lp-bg), color-mix(in srgb, var(--cw-indigo) 4%, var(--lp-bg))); }
.lp .ei-cand:nth-of-type(3) { stroke: var(--cw-coral); }
.lp .ei-cand:nth-of-type(4) { stroke: var(--cw-gold); }
.lp .ei-cand:nth-of-type(5) { stroke: var(--cw-violet); }
.lp .ei-cand { stroke-width: 2; opacity: 0.5; }
.lp .ei.is-in .ei-cand { animation-name: ei-draw, cw-dim; }
@keyframes cw-dim { from { opacity: 0.95; } to { opacity: 0.5; } }
.lp .ei-win { stroke: var(--cw-teal); stroke-width: 3.2; }
.lp .ei-band { fill: color-mix(in srgb, var(--cw-mint) 18%, transparent); stroke: color-mix(in srgb, var(--cw-teal) 45%, transparent); }
.lp .ei-legend .k-cand { border-top: none; height: 3px; opacity: 1; border-radius: 2px; background: linear-gradient(90deg, var(--cw-coral) 33%, var(--cw-gold) 33% 66%, var(--cw-violet) 66%); }
.lp .ei-legend .k-win { border-top-color: var(--cw-teal); }
.lp .ei-legend .k-band { background: color-mix(in srgb, var(--cw-mint) 18%, transparent); border-color: color-mix(in srgb, var(--cw-teal) 45%, transparent); }
.lp .em-bottom { border-color: color-mix(in srgb, var(--cw-indigo) 30%, transparent);
 background: radial-gradient(60% 80% at 100% 0%, var(--cw-wash-violet), transparent 70%), linear-gradient(135deg, var(--cw-wash-teal), var(--cw-wash-indigo)); }
.tour-chapter::before { background: linear-gradient(90deg, var(--cw-teal), var(--cw-indigo), var(--cw-violet)); }

/* ── Your morning: the steps walk teal into indigo ── */
.day-num { background: linear-gradient(140deg, var(--cw-teal), var(--cw-indigo)); color: #fff; }
[data-theme="dark"] .day-num { color: var(--cw-tile-ink); }
.day-when { color: var(--cw-indigo); }
.day-list::before { background: linear-gradient(180deg, var(--cw-teal), var(--cw-indigo)) !important; opacity: 0.5; }

/* ── Features: a coloured icon tile per group ── */
.feat-group { --cw: var(--cw-teal); position: relative; padding-top: 74px; border-top: 2px solid var(--cw); }
${FEATURE_HUES.map((hue, i) => `.feat-group:nth-child(6n+${i + 1}) { --cw: var(--cw-${hue}); --cw-icon: ${FEATURE_ICONS[i]}; }`).join('\n')}
.feat-group::before { content: ''; position: absolute; top: 20px; left: 0; width: 40px; height: 40px; border-radius: 12px;
 background: linear-gradient(140deg, var(--cw), color-mix(in srgb, var(--cw) 62%, #000)); box-shadow: 0 10px 20px -12px var(--cw); }
[data-theme="dark"] .feat-group::before { background: linear-gradient(140deg, var(--cw), color-mix(in srgb, var(--cw) 70%, #fff)); }
.feat-group::after { content: ''; position: absolute; top: 30px; left: 10px; width: 20px; height: 20px; background: var(--cw-tile-ink);
 -webkit-mask: var(--cw-icon) center / contain no-repeat; mask: var(--cw-icon) center / contain no-repeat; }
.feat-items li svg circle { fill: color-mix(in srgb, var(--cw) 14%, transparent) !important; }
.feat-items li svg path { stroke: var(--cw) !important; }

/* ── Industries, comparison, pricing ── */
.case-tab.is-on { color: var(--lp-cta-fg); background: var(--lp-cta-bg); box-shadow: 0 6px 16px -10px var(--lp-shadow); }
#casos .lp-card-soft { background: radial-gradient(60% 80% at 100% 0%, var(--cw-wash-indigo), transparent 70%), var(--lp-bg2); }
.lp-table-head.cmp-row { background: linear-gradient(90deg, var(--lp-surface) 60%, color-mix(in srgb, var(--cw-teal) 16%, var(--lp-surface))); }
.lp-table-row.cmp-row { background: linear-gradient(90deg, transparent 72%, color-mix(in srgb, var(--cw-teal) 6%, transparent)); }
.lp-table-row.cmp-row:hover { background: linear-gradient(90deg, var(--lp-bg2) 72%, color-mix(in srgb, var(--cw-teal) 10%, var(--lp-bg2))); }
.sec-alt .lp-card.price-card.is-paid, .lp-card.price-card.is-paid {
 background:
  radial-gradient(60% 70% at 100% 0%, rgba(124,108,255,0.30), transparent 70%),
  radial-gradient(50% 60% at 0% 100%, rgba(43,179,163,0.28), transparent 70%),
  var(--lp-strip);
}
.price-card.is-paid .price-amount { background: linear-gradient(120deg, #FFFFFF, #9FF2E4); -webkit-background-clip: text; background-clip: text; color: transparent; }
.lp-step { color: #fff; background: linear-gradient(140deg, var(--cw-teal), var(--cw-indigo)); border-color: transparent; }
[data-theme="dark"] .lp-step { color: var(--cw-tile-ink); }
.upg-step .lp-step, .sec-alt .upg-step .lp-step { background: linear-gradient(140deg, var(--cw-teal), var(--cw-indigo)); }
.no-strings li svg circle { fill: var(--cw-wash-teal) !important; }

/* ── Trust and the models' lists: the bar over each item cycles ── */
.trust-item { --cw: var(--cw-teal); }
${cycleRules('.trust-item')}
.trust-item::before { width: 36px; background: linear-gradient(90deg, var(--cw), color-mix(in srgb, var(--cw) 30%, transparent)); }

/* ── Closing band ── */
.final-sec {
 background:
  radial-gradient(40% 70% at 0% 0%, rgba(43,179,163,0.28), transparent 70%),
  radial-gradient(40% 70% at 100% 100%, rgba(124,108,255,0.26), transparent 70%),
  radial-gradient(30% 50% at 70% 0%, rgba(240,154,208,0.10), transparent 70%),
  var(--lp-strip);
}
.final-btn:hover { border-color: #7FF0DF; }

/* ── Links and FAQ answer the pointer in colour ── */
.faq-icon.is-open { background: linear-gradient(140deg, var(--cw-teal), var(--cw-indigo)); color: #fff; border-color: transparent; }
[data-theme="dark"] .faq-icon.is-open { color: var(--cw-tile-ink); }
.lp-more, .trust-link { text-decoration-color: color-mix(in srgb, var(--cw-indigo) 60%, transparent); }
.lp-more:hover, .trust-link:hover { color: var(--cw-indigo); }
`

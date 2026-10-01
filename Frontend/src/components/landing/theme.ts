// The landing's design tokens and stylesheet, shared by the home page and the
// public subpages (/precios, /como-funciona, /preguntas-frecuentes, /seguridad).
// Moved here verbatim from LandingPage.tsx so every page paints from one source.

// Every colour on the page is a CSS variable, defined once in LANDING_CSS for
// the light theme and again for `[data-theme="dark"]`. The landing follows the
// same switch as the app (the `theme` key in localStorage, applied before
// first paint by app/layout.tsx), so a visitor who uses the app in dark sees
// the landing in dark too. Light stays the default — the Petróleo identity.
export const T = {
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

export const DISPLAY = 'var(--font-brand), system-ui, sans-serif'

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
export const LANDING_CSS = `
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
/* The page you are on, on the subpages. The home page has no current link. */
.nav-link[aria-current="page"] { color: var(--lp-text); }
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

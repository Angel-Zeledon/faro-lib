// The help center's stylesheet. It paints with the landing's tokens
// (components/landing/theme.ts, `--lp-*`), so light/dark and the Petróleo
// palette follow the landing without a second definition. The only colours
// defined here are the stock signals, which are data: they match the app's
// semáforo, not the landing's accent.
export const DOCS_CSS = `
:root, [data-theme="light"] {
 --dc-now: #b91c1c; --dc-now-bg: rgba(185,28,28,0.08); --dc-now-bd: rgba(185,28,28,0.28);
 --dc-soon: #a16207; --dc-soon-bg: rgba(161,98,7,0.09); --dc-soon-bd: rgba(161,98,7,0.30);
 --dc-ok: #15803d; --dc-ok-bg: rgba(21,128,61,0.08); --dc-ok-bd: rgba(21,128,61,0.28);
 --dc-over: #1d4ed8; --dc-over-bg: rgba(29,78,216,0.08); --dc-over-bd: rgba(29,78,216,0.28);
 --dc-none: #566A6D; --dc-none-bg: rgba(86,106,109,0.08); --dc-none-bd: rgba(86,106,109,0.28);
 --dc-warn-bg: rgba(217,119,6,0.07); --dc-warn-bd: rgba(217,119,6,0.32);
 --dc-code-bg: #F1F5F4;
}
[data-theme="dark"] {
 --dc-now: #D07878; --dc-now-bg: rgba(248,113,113,0.10); --dc-now-bd: rgba(248,113,113,0.30);
 --dc-soon: #C99A3E; --dc-soon-bg: rgba(251,191,36,0.10); --dc-soon-bd: rgba(251,191,36,0.30);
 --dc-ok: #4ade80; --dc-ok-bg: rgba(74,222,128,0.10); --dc-ok-bd: rgba(74,222,128,0.28);
 --dc-over: #93b4ff; --dc-over-bg: rgba(147,180,255,0.10); --dc-over-bd: rgba(147,180,255,0.30);
 --dc-none: #93A8A6; --dc-none-bg: rgba(147,168,166,0.10); --dc-none-bd: rgba(147,168,166,0.28);
 --dc-warn-bg: rgba(183,121,31,0.08); --dc-warn-bd: rgba(183,121,31,0.30);
 --dc-code-bg: #10201F;
}

.dc { background: var(--lp-bg); color: var(--lp-text); min-height: 100vh; }
.dc-frame {
 max-width: 1360px; margin: 0 auto; padding: 64px 32px 0;
 display: grid; grid-template-columns: 252px minmax(0, 1fr) 212px; gap: 0 48px; align-items: start;
}
.dc-frame.is-index { grid-template-columns: 252px minmax(0, 1fr); }

/* ── Sidebar ── */
.dc-side {
 position: sticky; top: 64px; height: calc(100vh - 64px); overflow-y: auto; overscroll-behavior: contain;
 padding: 28px 8px 40px 0; border-right: 1px solid var(--lp-border);
 scrollbar-width: thin;
}
.dc-search-btn {
 width: 100%; display: flex; align-items: center; gap: 10px; margin: 0 0 22px; padding: 9px 12px;
 border-radius: 10px; border: 1px solid var(--lp-border); background: var(--lp-bg2);
 color: var(--lp-muted); font: inherit; font-size: 14px; cursor: pointer; text-align: left;
 transition: border-color 140ms ease, color 140ms ease;
}
.dc-search-btn:hover { border-color: var(--lp-border-strong); color: var(--lp-text); }
.dc-search-btn kbd {
 margin-left: auto; font-family: inherit; font-size: 11.5px; color: var(--lp-dim);
 border: 1px solid var(--lp-border); border-radius: 6px; padding: 1px 6px; background: var(--lp-bg);
}
.dc-nav-home { display: block; font-size: 14px; font-weight: 600; color: var(--lp-text); text-decoration: none; padding: 6px 12px; margin-bottom: 10px; border-radius: 8px; }
.dc-nav-home:hover { background: var(--lp-bg2); }
.dc-nav-home[aria-current="page"] { color: var(--lp-accent); }
.dc-sec { margin-bottom: 4px; }
.dc-sec-btn {
 width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px;
 padding: 8px 12px; border: 0; background: none; border-radius: 8px; cursor: pointer;
 font: inherit; font-size: 13.5px; font-weight: 650; color: var(--lp-text); text-align: left;
}
.dc-sec-btn:hover { background: var(--lp-bg2); }
.dc-sec-btn svg { flex-shrink: 0; color: var(--lp-dim); transition: transform 160ms var(--lp-ease); }
.dc-sec-btn[aria-expanded="true"] svg { transform: rotate(90deg); }
.dc-sec ul { list-style: none; margin: 2px 0 10px 12px; padding: 0; border-left: 1px solid var(--lp-border); }
.dc-sec a {
 display: block; padding: 6px 10px 6px 14px; margin-left: -1px; border-left: 2px solid transparent;
 font-size: 14px; line-height: 1.4; color: var(--lp-muted); text-decoration: none;
 transition: color 120ms ease, border-color 120ms ease;
}
.dc-sec a:hover { color: var(--lp-text); }
.dc-sec a[aria-current="page"] {
 color: var(--lp-text); font-weight: 600;
 border-image: linear-gradient(180deg, var(--lp-accent), var(--lp-beam)) 1;
}

/* ── Article ── */
.dc-main { min-width: 0; padding: 36px 0 72px; }
.dc-crumbs ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 0 0 18px; padding: 0; font-size: 13px; color: var(--lp-muted); }
.dc-crumbs li + li::before { content: '/'; margin-right: 6px; color: var(--lp-dim); }
.dc-crumbs a { color: var(--lp-muted); text-decoration: none; }
.dc-crumbs a:hover { color: var(--lp-accent); }
.dc-crumbs [aria-current] { color: var(--lp-text); font-weight: 600; }
.dc-article { max-width: 46rem; }
.dc-h1 {
 font-family: var(--font-brand), system-ui, sans-serif; font-size: clamp(30px, 3.6vw, 42px); font-weight: 600;
 line-height: 1.1; letter-spacing: -0.035em; color: var(--lp-text); margin: 0 0 14px; text-wrap: balance;
}
.dc-lead { font-size: 18px; line-height: 1.6; color: var(--lp-body); margin: 0 0 32px; text-wrap: pretty; max-width: 40em; }
.dc-article p, .dc-article li, .dc-article dd { font-size: 16px; line-height: 1.72; color: var(--lp-body); text-wrap: pretty; }
.dc-article p { margin: 0 0 16px; }
.dc-article h2 {
 font-family: var(--font-brand), system-ui, sans-serif; font-size: 24px; font-weight: 600; letter-spacing: -0.02em;
 line-height: 1.25; color: var(--lp-text); margin: 44px 0 14px; padding-top: 22px; border-top: 1px solid var(--lp-border);
 scroll-margin-top: 88px;
}
.dc-article h3 {
 font-family: var(--font-brand), system-ui, sans-serif; font-size: 18.5px; font-weight: 600; letter-spacing: -0.01em;
 line-height: 1.35; color: var(--lp-text); margin: 30px 0 10px; scroll-margin-top: 88px;
}
.dc-anchor { color: inherit; text-decoration: none; }
.dc-anchor:hover { color: var(--lp-accent); }
.dc-article a:not(.dc-anchor) { color: var(--lp-accent); text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
.dc-article strong { color: var(--lp-text); font-weight: 600; }
.dc-article code {
 font-family: var(--font-code, ui-monospace), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.86em;
 background: var(--dc-code-bg); border: 1px solid var(--lp-border); border-radius: 6px; padding: 1px 5px; color: var(--lp-text);
 overflow-wrap: anywhere;
}
/* Tailwind's preflight strips list markers app-wide; prose lists need them. */
.dc-article ul, .dc-article ol { margin: 0 0 18px; padding-left: 22px; }
.dc-article ul { list-style: disc; }
.dc-article ol { list-style: decimal; }
.dc-article li { margin-bottom: 6px; }
.dc-article li::marker { color: var(--lp-dim); }

.dc-steps { counter-reset: dcstep; list-style: none; padding-left: 0 !important; }
.dc-steps > li { counter-increment: dcstep; position: relative; padding-left: 40px; margin-bottom: 12px; }
.dc-steps > li::before {
 content: counter(dcstep); position: absolute; left: 0; top: 2px; width: 26px; height: 26px; border-radius: 50%;
 display: grid; place-items: center; font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
 color: var(--lp-accent); background: var(--lp-accent-bg); border: 1px solid var(--lp-accent-bd);
}

.dc-dl { margin: 0 0 22px; border-top: 1px solid var(--lp-border); }
.dc-dl > div { display: grid; grid-template-columns: minmax(150px, 30%) 1fr; gap: 6px 20px; padding: 12px 0; border-bottom: 1px solid var(--lp-border); }
.dc-dl dt { font-size: 15px; font-weight: 600; color: var(--lp-text); line-height: 1.5; }
.dc-dl dd { margin: 0; font-size: 15px !important; line-height: 1.65 !important; }

.dc-note { margin: 0 0 22px; padding: 14px 18px; border-radius: 12px; border: 1px solid var(--lp-accent-bd); background: var(--lp-accent-bg); }
.dc-note p { margin: 0 0 6px !important; font-size: 15px !important; }
.dc-note p:last-child { margin-bottom: 0 !important; }
.dc-note-title { font-weight: 650; color: var(--lp-text) !important; }
.dc-note-warn { border-color: var(--dc-warn-bd); background: var(--dc-warn-bg); }

.dc-shot { margin: 8px 0 28px; }
.dc-shot img {
 display: block; width: 100%; height: auto; border-radius: 12px; border: 1px solid var(--lp-border);
 box-shadow: 0 18px 40px -28px var(--lp-shadow); background: var(--lp-bg2);
}
.dc-shot figcaption { font-size: 13.5px; color: var(--lp-muted); margin-top: 10px; line-height: 1.5; }

.dc-table-wrap { overflow-x: auto; margin: 0 0 24px; border: 1px solid var(--lp-border); border-radius: 12px; }
.dc-table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
.dc-table th { text-align: left; font-weight: 650; color: var(--lp-text); background: var(--lp-bg2); padding: 10px 14px; border-bottom: 1px solid var(--lp-border); white-space: nowrap; }
.dc-table td { padding: 10px 14px; border-bottom: 1px solid var(--lp-border); color: var(--lp-body); line-height: 1.55; vertical-align: top; }
.dc-table tr:last-child td { border-bottom: 0; }

.dc-code {
 margin: 0 0 22px; padding: 14px 16px; border-radius: 12px; overflow-x: auto;
 background: var(--dc-code-bg); border: 1px solid var(--lp-border);
 font-family: var(--font-code, ui-monospace), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13.5px; line-height: 1.6; color: var(--lp-text);
}
.dc-code code { background: none; border: 0; padding: 0; font-size: inherit; }

.dc-signals { list-style: none; padding: 0 !important; margin: 0 0 24px !important; display: grid; gap: 10px; }
.dc-sig { display: grid; grid-template-columns: 158px 1fr; gap: 16px; align-items: start; padding: 14px 16px; border-radius: 12px; border: 1px solid var(--lp-border); margin: 0 !important; }
.dc-sig-chip {
 display: inline-flex; align-items: center; gap: 8px; justify-self: start; padding: 4px 11px; border-radius: 999px;
 font-size: 12.5px; font-weight: 700; letter-spacing: 0.01em; white-space: nowrap; border: 1px solid;
}
.dc-sig-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.dc-sig-text { font-size: 15px; line-height: 1.6; color: var(--lp-body); }
.dc-sig-now .dc-sig-chip { color: var(--dc-now); background: var(--dc-now-bg); border-color: var(--dc-now-bd); }
.dc-sig-soon .dc-sig-chip { color: var(--dc-soon); background: var(--dc-soon-bg); border-color: var(--dc-soon-bd); }
.dc-sig-ok .dc-sig-chip { color: var(--dc-ok); background: var(--dc-ok-bg); border-color: var(--dc-ok-bd); }
.dc-sig-over .dc-sig-chip { color: var(--dc-over); background: var(--dc-over-bg); border-color: var(--dc-over-bd); }
.dc-sig-none .dc-sig-chip { color: var(--dc-none); background: var(--dc-none-bg); border-color: var(--dc-none-bd); }

.dc-release { display: grid; grid-template-columns: 112px 1fr; gap: 20px; padding: 22px 0; border-top: 1px solid var(--lp-border); }
.dc-release-date { font-size: 13.5px; font-weight: 650; color: var(--lp-accent); font-variant-numeric: tabular-nums; padding-top: 3px; }
.dc-release h3 { margin: 0 0 10px !important; scroll-margin-top: 88px; }

/* ── Prev / next ── */
.dc-pager { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 56px; max-width: 46rem; }
.dc-pager a {
 display: block; padding: 14px 18px; border-radius: 12px; border: 1px solid var(--lp-border); text-decoration: none;
 transition: border-color 140ms ease;
}
.dc-pager a:hover { border-color: var(--lp-accent); }
.dc-pager span { display: block; font-size: 12.5px; color: var(--lp-muted); margin-bottom: 4px; }
.dc-pager strong { display: block; font-size: 15.5px; font-weight: 600; color: var(--lp-text); line-height: 1.4; }
.dc-pager .is-next { text-align: right; grid-column: 2; }

/* ── On this page ── */
.dc-toc { position: sticky; top: 64px; padding: 36px 0 40px; max-height: calc(100vh - 64px); overflow-y: auto; font-size: 13.5px; }
.dc-toc-head { font-size: 13px; font-weight: 700; color: var(--lp-text); margin: 0 0 10px; }
.dc-toc ol { list-style: none; margin: 0; padding: 0; border-left: 1px solid var(--lp-border); }
.dc-toc a {
 display: block; padding: 5px 0 5px 14px; margin-left: -1px; border-left: 2px solid transparent;
 color: var(--lp-muted); text-decoration: none; line-height: 1.4; transition: color 120ms ease, border-color 120ms ease;
}
.dc-toc .is-l3 a { padding-left: 26px; font-size: 13px; }
.dc-toc a:hover { color: var(--lp-text); }
.dc-toc a[aria-current="true"] { color: var(--lp-text); border-left-color: var(--lp-accent); font-weight: 600; }
.dc-toc-fold { display: none; }

/* ── Index ── */
.dc-index-head { max-width: 46rem; }
.dc-big-search {
 display: flex; align-items: center; gap: 12px; width: 100%; max-width: 34rem; margin: 4px 0 40px; padding: 14px 18px;
 border-radius: 14px; border: 1px solid var(--lp-border-strong); background: var(--lp-bg);
 font: inherit; font-size: 16px; color: var(--lp-muted); cursor: pointer; text-align: left;
 box-shadow: 0 14px 34px -26px var(--lp-shadow); transition: border-color 140ms ease;
}
.dc-big-search:hover { border-color: var(--lp-accent); }
.dc-big-search kbd { margin-left: auto; font-family: inherit; font-size: 12px; color: var(--lp-dim); border: 1px solid var(--lp-border); border-radius: 6px; padding: 2px 7px; }
.dc-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 40px; max-width: 64rem; }
.dc-section-card { padding: 22px 0 18px; border-top: 1px solid var(--lp-border); scroll-margin-top: 88px; }
.dc-section-card h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 19px; font-weight: 600; letter-spacing: -0.015em; color: var(--lp-text); margin: 0 0 4px; }
.dc-section-card > p { font-size: 14.5px; color: var(--lp-muted); line-height: 1.55; margin: 0 0 10px; }
.dc-section-card ul { list-style: none; margin: 0; padding: 0; }
.dc-section-card li a { display: inline-block; padding: 4px 0; font-size: 15px; color: var(--lp-accent); text-decoration: none; }
.dc-section-card li a:hover { text-decoration: underline; text-underline-offset: 3px; }
.dc-extras { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; max-width: 64rem; margin-top: 40px; }
.dc-extra { padding: 20px 22px; border-radius: 14px; background: var(--lp-bg2); border: 1px solid var(--lp-border); }
.dc-extra h2 { font-family: var(--font-brand), system-ui, sans-serif; font-size: 17px; font-weight: 600; color: var(--lp-text); margin: 0 0 6px; }
.dc-extra p { font-size: 14.5px; color: var(--lp-body); line-height: 1.55; margin: 0 0 12px; }
.dc-extra a { font-size: 14.5px; font-weight: 600; color: var(--lp-accent); text-decoration: none; }
.dc-extra a:hover { text-decoration: underline; text-underline-offset: 3px; }

/* ── Search dialog ── */
.dc-search-layer { position: fixed; inset: 0; z-index: 300; background: rgba(6,20,22,0.42); display: flex; justify-content: center; align-items: flex-start; padding: 10vh 16px 16px; }
.dc-search-box {
 width: 100%; max-width: 640px; max-height: 76vh; display: flex; flex-direction: column; overflow: hidden;
 background: var(--lp-bg); border: 1px solid var(--lp-border-strong); border-radius: 16px; box-shadow: 0 30px 80px -30px rgba(0,0,0,0.5);
}
.dc-search-field { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--lp-border); color: var(--lp-muted); }
.dc-search-field input { flex: 1; min-width: 0; border: 0; outline: none; background: none; font: inherit; font-size: 16px; color: var(--lp-text); }
.dc-search-close { border: 0; background: none; color: var(--lp-muted); cursor: pointer; padding: 6px; border-radius: 8px; display: grid; place-items: center; }
.dc-search-close:hover { background: var(--lp-bg2); color: var(--lp-text); }
.dc-search-list { list-style: none; margin: 0; padding: 8px; overflow-y: auto; }
.dc-search-list a { display: block; padding: 10px 12px; border-radius: 10px; text-decoration: none; }
.dc-search-list a[aria-selected="true"], .dc-search-list a:hover { background: var(--lp-accent-bg); }
.dc-hit-path { display: block; font-size: 12px; color: var(--lp-dim); margin-bottom: 2px; }
.dc-hit-title { display: block; font-size: 15px; font-weight: 600; color: var(--lp-text); line-height: 1.4; }
.dc-hit-snip { display: block; font-size: 13.5px; color: var(--lp-muted); line-height: 1.5; margin-top: 3px; }
.dc-search-list mark { background: rgba(76,195,181,0.30); color: inherit; border-radius: 3px; padding: 0 1px; }
.dc-search-msg { padding: 22px 18px; font-size: 14.5px; color: var(--lp-muted); line-height: 1.55; margin: 0; }

/* ── Phone bar + sheet ── */
.dc-bar { display: none; }
.dc-sheet { display: none; }

.dc a:focus-visible, .dc button:focus-visible { outline: 2px solid var(--lp-accent); outline-offset: 2px; border-radius: 8px; }

@media (max-width: 1180px) {
 .dc-frame { grid-template-columns: 236px minmax(0, 1fr); gap: 0 40px; }
 .dc-toc { display: none; }
 .dc-toc-fold { display: block; margin: 0 0 28px; border: 1px solid var(--lp-border); border-radius: 12px; max-width: 46rem; }
 .dc-toc-fold summary { cursor: pointer; padding: 12px 16px; font-size: 14px; font-weight: 600; color: var(--lp-text); list-style-position: inside; }
 .dc-toc-fold ol { list-style: none; margin: 0; padding: 0 16px 12px; }
 .dc-toc-fold li a { display: block; padding: 6px 0; font-size: 14px; color: var(--lp-muted); text-decoration: none; }
 .dc-toc-fold li.is-l3 a { padding-left: 14px; }
}
@media (max-width: 900px) {
 .dc-frame, .dc-frame.is-index { grid-template-columns: minmax(0, 1fr); padding: 64px 16px 0; }
 .dc-side { display: none; }
 .dc-bar {
  display: flex; gap: 8px; position: sticky; top: 64px; z-index: 50; margin: 0 -16px; padding: 10px 16px;
  background: var(--lp-nav); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid var(--lp-border);
 }
 .dc-bar button {
  display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 14px; border-radius: 10px;
  border: 1px solid var(--lp-border); background: var(--lp-bg); color: var(--lp-text); font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
 }
 .dc-bar button:first-child { flex: 1; min-width: 0; }
 .dc-bar button:first-child span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .dc-sheet { display: block; position: fixed; inset: 0; z-index: 250; background: rgba(6,20,22,0.42); }
 .dc-sheet-panel {
  position: absolute; top: 0; bottom: 0; left: 0; width: min(88vw, 340px); background: var(--lp-bg);
  overflow-y: auto; padding: 16px 12px 32px; box-shadow: 20px 0 60px -30px rgba(0,0,0,0.5);
 }
 .dc-sheet-head { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 12px 12px; font-size: 15px; font-weight: 700; color: var(--lp-text); }
 .dc-sheet .dc-sec a, .dc-sheet .dc-sec-btn, .dc-sheet .dc-nav-home { min-height: 44px; display: flex; align-items: center; }
 .dc-main { padding-top: 20px; }
 .dc-sections, .dc-extras { grid-template-columns: minmax(0, 1fr); }
 .dc-dl > div { grid-template-columns: minmax(0, 1fr); gap: 2px; }
 .dc-sig { grid-template-columns: minmax(0, 1fr); gap: 8px; }
 .dc-release { grid-template-columns: minmax(0, 1fr); gap: 6px; }
 .dc-pager { grid-template-columns: minmax(0, 1fr); }
 .dc-pager .is-next { grid-column: 1; }
 .dc-crumbs a { display: inline-flex; align-items: center; min-height: 32px; }
 .dc-section-card li a, .dc-toc-fold li a { min-height: 40px; display: flex; align-items: center; }
 .dc-search-layer { padding: 0; align-items: stretch; }
 .dc-search-box { max-width: none; max-height: none; border-radius: 0; border: 0; }
}
/* The landing's nav is 60px tall from 600px down (theme.ts). */
@media (max-width: 600px) {
 .dc-frame, .dc-frame.is-index { padding-top: 60px; }
 .dc-bar { top: 60px; }
}
@media (prefers-reduced-motion: reduce) {
 .dc * { transition: none !important; }
}
`

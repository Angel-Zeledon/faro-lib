import { ImageResponse } from 'next/og'

/**
 * /og-image.png — the link-preview image every public page names in its
 * `openGraph` / `twitter` metadata (and the JSON-LD `logo`/`image`).
 *
 * It used to be a static PNG in `public/` with its words baked in: "Qué pedir
 * hoy, cuánto y a qué proveedor" and "Compras de inventario para
 * distribuidores", plus a "Plan gratis para siempre" pill the landing no longer
 * offers. On 2026-10-11 the owner repositioned StockAI as a SALES FORECASTING
 * product, and a shared link kept announcing an inventory app. Drawing it here
 * keeps the words in code, next to the rest of the positioning, so the next
 * change is an edit and not a design file nobody can find.
 *
 * Same URL as before on purpose: every metadata export points at it, and
 * social platforms cache by URL (they have to be asked to re-scrape).
 *
 * Edge runtime: the Node build of next/og cannot load its own bundled font
 * on Windows (ERR_INVALID_URL on a `file:` path), so the image could not be
 * checked locally; the edge build fetches it as a module asset and works on
 * every platform. The wordmark is fetched the same way. The response is
 * marked cacheable for a day, so it is drawn once, not per crawler.
 *
 * Spanish, like the rest of the server-rendered head (primary market). The
 * chart is an illustration with no numbers on it — not data.
 */
export const runtime = 'edge'

const W = 1200
const H = 630

// Brand tokens (identidad "Petróleo"): insignia, its deep shade, the accent.
const PETROL = '#0C3A40'
const PETROL_DEEP = '#08272B'
const ACCENT = '#4CC3B5'
const INK = '#F4F8F7'
const MUTED = '#A9C4C1'

// An invented history and forecast, only to draw the shape of the idea.
const HISTORY = [52, 58, 49, 63, 60, 71, 66, 74, 69, 80, 76, 84]
const FORECAST = [84, 88, 86, 93, 91, 97]

function toBase64(buf: ArrayBuffer): string {
  let bin = ''
  const bytes = new Uint8Array(buf)
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

function chartPaths() {
  const cw = 420
  const ch = 210
  const all = [...HISTORY, ...FORECAST.slice(1)]
  const n = all.length
  const min = 40
  const max = 110
  const x = (i: number) => (i / (n - 1)) * cw
  const y = (v: number) => ch - ((v - min) / (max - min)) * ch
  const hist = HISTORY.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const start = HISTORY.length - 1
  const fc = FORECAST.map((v, i) => `${i ? 'L' : 'M'}${x(start + i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  // The likely-range band widens with the horizon, as a real one does.
  const upper = FORECAST.map((v, i) => [x(start + i), y(v + 3 + i * 2.6)] as const)
  const lower = FORECAST.map((v, i) => [x(start + i), y(v - 3 - i * 2.6)] as const).reverse()
  const band = [...upper, ...lower].map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ') + ' Z'
  return { cw, ch, hist, fc, band, todayX: x(start) }
}

export async function GET() {
  const logo = await fetch(new URL('../../../public/brand/stockai-logo-on-dark.png', import.meta.url)).then(r => r.arrayBuffer())
  const logoSrc = `data:image/png;base64,${toBase64(logo)}`
  const { cw, ch, hist, fc, band, todayX } = chartPaths()

  return new ImageResponse(
    (
      <div
        style={{
          width: W, height: H, display: 'flex', position: 'relative',
          background: `linear-gradient(135deg, ${PETROL} 0%, ${PETROL} 45%, ${PETROL_DEEP} 100%)`,
          fontFamily: 'sans-serif', color: INK,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', padding: '58px 0 0 70px', width: 600 }}>
          {/* 451x168 source, on the same petrol it is drawn on */}
          <img src={logoSrc} width={150} height={56} style={{ marginLeft: -10 }} alt="" />
          <div style={{ display: 'flex', flexDirection: 'column', marginTop: 34, fontSize: 54, lineHeight: 1.1, letterSpacing: -1 }}>
            <span>Predicción de ventas</span>
            <span>con IA, producto</span>
            <span>por producto.</span>
          </div>
          <div style={{ display: 'flex', marginTop: 26, fontSize: 24, lineHeight: 1.4, color: MUTED }}>
            Modelos que compiten sobre tu historial. Con ese pronóstico, sabes qué pedir.
          </div>
          <div
            style={{
              display: 'flex', alignSelf: 'flex-start', marginTop: 30, padding: '10px 18px',
              border: '1px solid rgba(255,255,255,0.22)', borderRadius: 999,
              fontSize: 20, color: '#CFE7E2', background: 'rgba(255,255,255,0.06)',
            }}
          >
            9 modelos compiten por cada producto
          </div>
        </div>

        <div
          style={{
            position: 'absolute', right: 64, top: 150, width: 480, height: 330,
            display: 'flex', flexDirection: 'column', padding: '22px 30px',
            borderRadius: 22, border: '1px solid rgba(255,255,255,0.14)',
            background: 'rgba(255,255,255,0.06)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 18, color: MUTED }}>
            <span>Pronóstico por producto</span>
            <span style={{ color: ACCENT }}>Rango probable</span>
          </div>
          <svg width={cw} height={ch} viewBox={`0 0 ${cw} ${ch}`} style={{ marginTop: 26 }}>
            <path d={band} fill={ACCENT} fillOpacity={0.18} />
            <line x1={todayX} y1={0} x2={todayX} y2={ch} stroke="rgba(255,255,255,0.28)" strokeWidth={2} strokeDasharray="5 6" />
            <path d={hist} fill="none" stroke={INK} strokeWidth={4} strokeLinejoin="round" strokeLinecap="round" />
            <path d={fc} fill="none" stroke={ACCENT} strokeWidth={4} strokeDasharray="10 8" strokeLinecap="round" />
          </svg>
          <div style={{ display: 'flex', position: 'relative', justifyContent: 'space-between', marginTop: 14, fontSize: 16, color: MUTED }}>
            <span>Historial</span>
            <span style={{ position: 'absolute', left: todayX - 16 }}>Hoy</span>
            <span>Pronóstico</span>
          </div>
        </div>
      </div>
    ),
    { width: W, height: H, headers: { 'Cache-Control': 'public, max-age=86400, s-maxage=86400' } },
  )
}

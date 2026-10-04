/** @type {import('next').NextConfig} */
// Defaults to the port CLAUDE.md tells you to start the backend on. It used to
// say 8000, so a fresh clone followed the documented command and got a proxy
// pointing somewhere nothing was listening.
//
// 127.0.0.1, not localhost, on purpose: Node resolves localhost to ::1 first
// and uvicorn binds IPv4 only, so `localhost` yields ECONNREFUSED ::1 and Next
// answers 500 with an empty body — which reads like a broken backend rather
// than a name that resolved to the wrong family.
//
// A `Frontend/.env.local` (gitignored, per-machine) overrides this and BEATS a
// shell `BACKEND_URL=...`, so check that file before believing either.
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8010'
const isDev = process.env.NODE_ENV === 'development'

// One id per build, shared by the bundle (NEXT_PUBLIC_BUILD_ID is inlined into
// the client code) and by Next's own build id. The running server answers
// /build-id with the id it was built with; an open tab compares that to the
// one it was loaded with and reloads when they differ (lib/pwa.ts). Set
// BUILD_ID (or GIT_SHA) in the image build to make it reproducible.
const BUILD_ID = process.env.BUILD_ID || process.env.GIT_SHA || Date.now().toString(36)
// Next evaluates this file in more than one process during a build; written back
// to the environment, the worker processes it spawns inherit the same id.
process.env.BUILD_ID = BUILD_ID

const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${isDev ? ' ws://localhost:* wss://localhost:*' : ''}`,
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join('; ')

const SECURITY_HEADERS = [
  { key: 'Content-Security-Policy',   value: CSP },
  { key: 'X-Frame-Options',           value: 'DENY' },
  { key: 'X-Content-Type-Options',    value: 'nosniff' },
  { key: 'Referrer-Policy',           value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy',        value: 'camera=(), microphone=(), geolocation=()' },
]

const nextConfig = {
  // Self-contained server bundle for the Docker image (node server.js, no
  // node_modules). Ignored by `next dev`. NOTE: rewrites() is evaluated at
  // BUILD time, so the production image must be built with BACKEND_URL set
  // to the in-network API address (the Dockerfile defaults it to the compose
  // service name).
  output: 'standalone',
  generateBuildId: async () => BUILD_ID,
  env: {
    NEXT_PUBLIC_APP_VERSION: process.env.npm_package_version ?? '1.0.0',
    NEXT_PUBLIC_BUILD_ID: BUILD_ID,
  },
  async rewrites() {
    return [
      // The versioned public base an integration is told to use
      // (`https://<domain>/api/v1/...`, see /desarrolladores and /api). Without
      // this rule it fell into the one below and became `/api/v1/v1/...` — a
      // 404 on every documented URL. First, so it wins; the app's own calls
      // never start with `v1/`, so they are unaffected.
      {
        source: '/api/v1/:path*',
        destination: `${BACKEND_URL}/api/v1/:path*`,
      },
      {
        source: '/api/:path*',
        destination: `${BACKEND_URL}/api/v1/:path*`,
      },
    ]
  },
  // The routes were renamed so each one says what its screen is, and so the
  // set stops being half Spanish and half English. Every old URL still works:
  // people bookmark these and paste them to each other, and a 404 on a link a
  // colleague sent an hour ago is a worse bug than the naming ever was.
  //
  // Permanent (308), because the old names are not coming back. Auth routes are
  // deliberately absent — /verify-email and /reset-password are already sitting
  // in people's inboxes and must resolve exactly as sent.
  async redirects() {
    const MOVED = [
      ['/hoy',                          '/compras'],
      ['/skus',                         '/pronosticos'],
      ['/quick-start',                  '/ventas'],
      ['/data',                         '/archivos'],
      ['/inventory',                    '/inventario'],
      ['/inventory-setup',              '/configurar-inventario'],
      ['/inventory/suppliers',          '/proveedores'],
      ['/inventory/suppliers/scorecard', '/proveedores/scorecard'],
      ['/inventory/roi',                '/impacto'],
      ['/sessions',                     '/historial'],
      ['/analyst',                      '/asistente'],
      ['/scenarios',                    '/escenarios'],
      ['/users',                        '/usuarios'],
      ['/config',                       '/mi-cuenta'],
      ['/settings',                     '/automatizacion'],
    ]
    return MOVED.map(([source, destination]) => ({
      source, destination, permanent: true,
    }))
  },
  async headers() {
    return [
      { source: '/(.*)', headers: SECURITY_HEADERS },
      // The worker must be re-fetched on every check, or a new deploy's worker
      // is never seen by a browser that cached the old one.
      { source: '/sw.js', headers: [{ key: 'Cache-Control', value: 'no-cache, max-age=0' }] },
    ]
  },
}

export default nextConfig
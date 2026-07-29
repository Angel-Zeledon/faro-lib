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
  env: {
    NEXT_PUBLIC_APP_VERSION: process.env.npm_package_version ?? '1.0.0',
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_URL}/api/v1/:path*`,
      },
    ]
  },
  async headers() {
    return [
      { source: '/(.*)', headers: SECURITY_HEADERS },
    ]
  },
}

export default nextConfig
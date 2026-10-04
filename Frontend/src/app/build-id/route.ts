/**
 * The id of the build this server is running (see next.config.mjs). The app
 * polls it to notice that a new version was deployed under an open tab or an
 * installed PWA (lib/pwa.ts watchForNewVersion). Never cached, public, and it
 * carries nothing but the id.
 */
export const dynamic = 'force-dynamic'

export function GET() {
  return Response.json(
    { id: process.env.NEXT_PUBLIC_BUILD_ID ?? null },
    { headers: { 'Cache-Control': 'no-store, max-age=0' } },
  )
}

// Where the APP lives, as seen from the landing.
//
// A deployment can serve the landing on its own domain (stockai.es) and the
// app on a subdomain (app.stockai.es). The landing's sign-in and sign-up links
// must then leave for the app's origin: a relative `/login` would render the
// login on the landing domain, whose session storage the app never sees.
//
// NEXT_PUBLIC_APP_URL is baked at build time (Frontend/Dockerfile). Empty —
// local dev, or a single-domain deployment — keeps every link relative, which
// is exactly today's behaviour.
const APP_URL = (process.env.NEXT_PUBLIC_APP_URL || '').replace(/\/+$/, '')

/** An app path, absolute when the app has its own origin. */
export function appHref(path: string): string {
  return `${APP_URL}${path}`
}

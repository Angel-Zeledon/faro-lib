// The landing's content pages: the industry pages and their hub, the Excel
// comparison, "how it is calculated", integrations and the changelog. A plain
// module (no 'use client') so the server wrappers, the sitemap, the app shell
// and the auth guard can all read it.
//
// Every URL here must ALSO appear in app/sitemap.ts, app/robots.ts and the
// `@landing` matcher of deploy/Caddyfile.split (anything not listed there is
// redirected to the app host). `npm run check:landing-routes` fails when one
// is missing; run it after adding a page.
import type { IndustryKey } from '@/i18n/landingContent'

export type { IndustryKey }
export type ContentKey = 'excel' | 'method' | 'integrations' | 'changelog'

export const INDUSTRIES_HUB_PATH = '/industrias'

export const INDUSTRY_SLUGS: Record<IndustryKey, string> = {
  consumer: 'consumo-masivo',
  hardware: 'ferreteria',
  pharmacy: 'farmacia',
  autoparts: 'autopartes',
  retail: 'retail',
}

export const INDUSTRY_ORDER: IndustryKey[] = ['consumer', 'hardware', 'pharmacy', 'autoparts', 'retail']

// Written out in full (not built from the slugs) so scripts/check-landing-routes.mjs
// can read them as plain text; the script also checks each ends in its slug.
export const INDUSTRY_PATHS: Record<IndustryKey, string> = {
  consumer: '/industrias/consumo-masivo',
  hardware: '/industrias/ferreteria',
  pharmacy: '/industrias/farmacia',
  autoparts: '/industrias/autopartes',
  retail: '/industrias/retail',
}

export const CONTENT_PATHS: Record<ContentKey, string> = {
  excel: '/stockai-vs-excel',
  method: '/como-se-calcula',
  integrations: '/integraciones',
  changelog: '/novedades',
}

export const CONTENT_ORDER: ContentKey[] = ['method', 'excel', 'integrations', 'changelog']

// Every public content URL, for the sitemap, the shell and the auth guard.
export const CONTENT_PUBLIC_PATHS: string[] = [
  INDUSTRIES_HUB_PATH,
  ...INDUSTRY_ORDER.map(k => INDUSTRY_PATHS[k]),
  ...CONTENT_ORDER.map(k => CONTENT_PATHS[k]),
]

// The public legal documents' URLs. A plain module (no 'use client') so the
// server wrappers, the app shell and the signup form can all read it.
//
// Not part of SUBPAGE_PATHS on purpose: those are the marketing subpages
// (they share the sales closing band and the "keep reading" grid); these are
// reading documents with their own shell (components/landing/Legal.tsx).
// app/robots.ts, app/sitemap.ts and deploy/Caddyfile.split must list every
// slug in LEGAL_PUBLIC_PATHS for the landing domain to serve and index them.
export type LegalKey =
  | 'privacy' | 'terms' | 'cookies' | 'notice'
  | 'acceptableUse' | 'dpa' | 'ai' | 'disclosure' | 'accessibility' | 'commercial'

export const LEGAL_PATHS: Record<LegalKey, string> = {
  privacy: '/privacidad',
  terms: '/terminos',
  cookies: '/cookies',
  notice: '/aviso-legal',
  acceptableUse: '/uso-aceptable',
  dpa: '/procesamiento-de-datos',
  ai: '/ia',
  disclosure: '/divulgacion-responsable',
  accessibility: '/accesibilidad',
  commercial: '/condiciones-comerciales',
}

// The page that lists every document above, grouped.
export const LEGAL_HUB_PATH = '/legal'

// Every public legal URL, for the shell and the auth guard.
export const LEGAL_PUBLIC_PATHS: string[] = [...Object.values(LEGAL_PATHS), LEGAL_HUB_PATH]

// The four everybody is pointed at (footers, the app's legal links, "other
// documents"). The rest are reached from the hub.
export type MainLegalKey = 'terms' | 'privacy' | 'cookies' | 'notice'
export const LEGAL_ORDER: MainLegalKey[] = ['terms', 'privacy', 'cookies', 'notice']

// How the hub groups the documents. Every LegalKey appears exactly once.
export type LegalGroup = 'core' | 'use' | 'business' | 'security'
export const LEGAL_GROUPS: { id: LegalGroup; keys: LegalKey[] }[] = [
  { id: 'core', keys: ['terms', 'privacy', 'cookies', 'notice'] },
  { id: 'use', keys: ['acceptableUse', 'ai', 'accessibility'] },
  { id: 'business', keys: ['commercial', 'dpa'] },
  { id: 'security', keys: ['disclosure'] },
]

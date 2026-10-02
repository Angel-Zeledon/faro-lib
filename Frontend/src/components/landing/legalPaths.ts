// The public legal documents' URLs. A plain module (no 'use client') so the
// server wrappers, the app shell and the signup form can all read it.
//
// Not part of SUBPAGE_PATHS on purpose: those are the marketing subpages
// (they share the sales closing band and the "keep reading" grid); these are
// reading documents with their own shell (components/landing/Legal.tsx).
// app/robots.ts, app/sitemap.ts and deploy/Caddyfile.split must list these
// same four slugs for the landing domain to serve and index them.
export type LegalKey = 'privacy' | 'terms' | 'cookies' | 'notice'

export const LEGAL_PATHS: Record<LegalKey, string> = {
  privacy: '/privacidad',
  terms: '/terminos',
  cookies: '/cookies',
  notice: '/aviso-legal',
}

// Order wherever the four are listed together (footers, "other documents").
export const LEGAL_ORDER: LegalKey[] = ['terms', 'privacy', 'cookies', 'notice']

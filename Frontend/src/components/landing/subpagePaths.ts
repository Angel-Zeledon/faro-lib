// The public subpages' URLs. A plain module (no 'use client') so the server
// wrappers and StructuredData can read it too. app/robots.ts, app/sitemap.ts
// and deploy/Caddyfile.split list the same four slugs.
export type SubpageKey = 'pricing' | 'how' | 'faq' | 'security'

export const SUBPAGE_PATHS: Record<SubpageKey, string> = {
  pricing: '/precios',
  how: '/como-funciona',
  faq: '/preguntas-frecuentes',
  security: '/seguridad',
}

// Menu order, wherever the four are listed together.
export const SUBPAGE_ORDER: SubpageKey[] = ['how', 'pricing', 'faq', 'security']

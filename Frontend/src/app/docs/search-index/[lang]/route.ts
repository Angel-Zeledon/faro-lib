import { NextResponse } from 'next/server'
import { buildSearchIndex } from '@/i18n/docs/index'
import type { Lang } from '@/i18n/translations'

// The help center's search index, one per language, built from the same typed
// pages the routes render and frozen at build time. Fetched by
// components/docs/DocsSearch the first time a reader opens search.
export const dynamic = 'force-static'
export const dynamicParams = false

const LANGS: Lang[] = ['es', 'en']

export function generateStaticParams() {
  return LANGS.map(lang => ({ lang }))
}

export function GET(_req: Request, { params }: { params: { lang: string } }) {
  if (!LANGS.includes(params.lang as Lang)) {
    return NextResponse.json({ detail: 'Not Found' }, { status: 404 })
  }
  return NextResponse.json(buildSearchIndex(params.lang as Lang), {
    headers: { 'Cache-Control': 'public, max-age=3600' },
  })
}

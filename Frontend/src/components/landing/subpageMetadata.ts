import type { Metadata } from 'next'
import { alternatesOf, isEnglishPath } from '@/components/landing/sitePaths'

// The link-preview card per language: the Spanish one says "Qué pedir hoy…",
// the English one "What to order today…".
export const OG_IMAGE: Record<'es' | 'en', string> = { es: '/og-image.png', en: '/og-image-en.png' }

// Search metadata for a public page. The language comes from the path: under
// /en it is the English page, anywhere else the Spanish one. Each page names
// itself as canonical and both language versions as hreflang alternates
// (x-default is the Spanish one, the primary market).
//
// `robots` opts the page back in: the root layout keeps every app route out of
// search, and a subpage that forgets this is invisible to the crawler it was
// built for.
export function subpageMetadata(path: string, title: string, description: string, imageAlt?: string): Metadata {
  const lang = isEnglishPath(path) ? 'en' : 'es'
  const pair = alternatesOf(path)
  const image = OG_IMAGE[lang]
  return {
    title: { absolute: title },
    description,
    alternates: {
      canonical: path,
      ...(pair ? { languages: { es: pair.es, en: pair.en, 'x-default': pair.es } } : {}),
    },
    robots: { index: true, follow: true },
    openGraph: {
      type: 'website',
      url: path,
      siteName: 'StockAI',
      title,
      description,
      locale: lang === 'en' ? 'en_US' : 'es_LA',
      alternateLocale: [lang === 'en' ? 'es_LA' : 'en_US'],
      images: [{ url: image, width: 1200, height: 630, alt: imageAlt ?? title }],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: [image],
    },
  }
}

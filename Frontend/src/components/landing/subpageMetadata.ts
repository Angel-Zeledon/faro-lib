import type { Metadata } from 'next'

// Search metadata for a public subpage. Spanish, like the home page's: it is
// the primary market and what a crawler reads before any script runs.
//
// `robots` opts the page back in: the root layout keeps every app route out of
// search, and a subpage that forgets this is invisible to the crawler it was
// built for.
export function subpageMetadata(path: string, title: string, description: string): Metadata {
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: path },
    robots: { index: true, follow: true },
    openGraph: {
      type: 'website',
      url: path,
      siteName: 'StockAI',
      title,
      description,
      locale: 'es_LA',
      alternateLocale: ['en_US'],
      images: [{ url: '/og-image.png', width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: ['/og-image.png'],
    },
  }
}

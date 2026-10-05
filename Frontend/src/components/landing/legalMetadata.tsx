import { LEGAL } from '@/i18n/legal'
import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { LEGAL_HUB_PATH, LEGAL_PATHS, type LegalKey } from '@/components/landing/legalPaths'

// Server-side head for a legal document: search metadata and schema.org data,
// read from the same catalogue the page renders, in Spanish like every other
// server-rendered head on the landing. Indexable on purpose — a privacy policy
// a search engine cannot find is one a buyer's procurement team cannot either.

export function legalMetadata(key: LegalKey) {
  const doc = LEGAL.es.docs[key]
  return subpageMetadata(LEGAL_PATHS[key], `StockAI | ${doc.title}`, doc.intro)
}

export function LegalStructuredData({ doc: key }: { doc: LegalKey }) {
  const doc = LEGAL.es.docs[key]
  const url = `${SITE_URL}${LEGAL_PATHS[key]}`
  return (
    <JsonLd
      graph={[
        {
          '@type': 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: doc.title,
          description: doc.intro,
          inLanguage: 'es',
          dateModified: '2026-10-02',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING.es.pages.home, item: `${SITE_URL}/` },
            { '@type': 'ListItem', position: 2, name: doc.label, item: url },
          ],
        },
      ]}
    />
  )
}

// The /legal hub: the same head, from the catalogue's `hub` copy.
export function legalHubMetadata() {
  const hub = LEGAL.es.hub
  return subpageMetadata(LEGAL_HUB_PATH, `StockAI | ${hub.title}`, hub.intro)
}

export function LegalHubStructuredData() {
  const hub = LEGAL.es.hub
  const url = `${SITE_URL}${LEGAL_HUB_PATH}`
  return (
    <JsonLd
      graph={[
        {
          '@type': 'CollectionPage',
          '@id': `${url}#webpage`,
          url,
          name: hub.title,
          description: hub.intro,
          inLanguage: 'es',
          dateModified: '2026-10-02',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING.es.pages.home, item: `${SITE_URL}/` },
            { '@type': 'ListItem', position: 2, name: hub.label, item: url },
          ],
        },
      ]}
    />
  )
}

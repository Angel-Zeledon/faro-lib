import { LEGAL } from '@/i18n/legal'
import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { LEGAL_HUB_PATH, LEGAL_PATHS, type LegalKey } from '@/components/landing/legalPaths'
import { EN_HOME_PATH, LEGAL_HUB_PATH_EN, LEGAL_PATHS_EN } from '@/components/landing/sitePaths'
import type { Lang } from '@/i18n/translations'

// Server-side head for a legal document: search metadata and schema.org data,
// read from the same catalogue the page renders, in Spanish like every other
// server-rendered head on the landing. Indexable on purpose — a privacy policy
// a search engine cannot find is one a buyer's procurement team cannot either.
// `lang` picks the English document and its /en URL.

const homeUrl = (lang: Lang) => (lang === 'en' ? `${SITE_URL}${EN_HOME_PATH}` : `${SITE_URL}/`)
const docPath = (key: LegalKey, lang: Lang) => (lang === 'en' ? LEGAL_PATHS_EN : LEGAL_PATHS)[key]
const hubPath = (lang: Lang) => (lang === 'en' ? LEGAL_HUB_PATH_EN : LEGAL_HUB_PATH)

export function legalMetadata(key: LegalKey, lang: Lang = 'es') {
  const doc = LEGAL[lang].docs[key]
  return subpageMetadata(docPath(key, lang), `StockAI | ${doc.title}`, doc.intro)
}

export function LegalStructuredData({ doc: key, lang = 'es' }: { doc: LegalKey; lang?: Lang }) {
  const doc = LEGAL[lang].docs[key]
  const url = `${SITE_URL}${docPath(key, lang)}`
  return (
    <JsonLd
      graph={[
        {
          '@type': 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: doc.title,
          description: doc.intro,
          inLanguage: lang,
          dateModified: '2026-10-02',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING[lang].pages.home, item: homeUrl(lang) },
            { '@type': 'ListItem', position: 2, name: doc.label, item: url },
          ],
        },
      ]}
    />
  )
}

// The /legal hub: the same head, from the catalogue's `hub` copy.
export function legalHubMetadata(lang: Lang = 'es') {
  const hub = LEGAL[lang].hub
  return subpageMetadata(hubPath(lang), `StockAI | ${hub.title}`, hub.intro)
}

export function LegalHubStructuredData({ lang = 'es' }: { lang?: Lang }) {
  const hub = LEGAL[lang].hub
  const url = `${SITE_URL}${hubPath(lang)}`
  return (
    <JsonLd
      graph={[
        {
          '@type': 'CollectionPage',
          '@id': `${url}#webpage`,
          url,
          name: hub.title,
          description: hub.intro,
          inLanguage: lang,
          dateModified: '2026-10-02',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING[lang].pages.home, item: homeUrl(lang) },
            { '@type': 'ListItem', position: 2, name: hub.label, item: url },
          ],
        },
      ]}
    />
  )
}

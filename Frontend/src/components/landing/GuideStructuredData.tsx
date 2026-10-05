import { LANDING } from '@/i18n/landing'
import { GUIDES, type GuideKey } from '@/i18n/landingGuides'
import { SITE_URL } from '@/lib/siteUrls'
import { JsonLd } from '@/components/landing/StructuredData'
import { GUIDE_PATHS } from '@/components/landing/contentPaths'

// Server side of a guide: search metadata and schema.org data, read from the
// same catalogue the page renders (Spanish, like every server-rendered head on
// the landing). The FAQPage is the page's visible FAQ, item for item.
export function guideMetadataArgs(key: GuideKey): [string, string, string] {
  const g = GUIDES.es.items[key]
  return [GUIDE_PATHS[key], g.metaTitle, g.metaDesc]
}

export function GuideStructuredData({ guide }: { guide: GuideKey }) {
  const g = GUIDES.es.items[guide]
  const url = `${SITE_URL}${GUIDE_PATHS[guide]}`
  return (
    <JsonLd
      graph={[
        {
          '@type': guide === 'about' ? 'AboutPage' : 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: g.title,
          description: g.metaDesc,
          inLanguage: 'es',
          isPartOf: { '@id': `${SITE_URL}/#website` },
          ...(guide === 'about' ? { about: { '@id': `${SITE_URL}/#organization` } } : {}),
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING.es.pages.home, item: `${SITE_URL}/` },
            { '@type': 'ListItem', position: 2, name: g.label, item: url },
          ],
        },
        {
          '@type': 'FAQPage',
          mainEntity: g.faq.map(f => ({
            '@type': 'Question',
            name: f.q,
            acceptedAnswer: { '@type': 'Answer', text: f.a },
          })),
        },
      ]}
    />
  )
}

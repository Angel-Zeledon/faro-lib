import { LANDING } from '@/i18n/landing'
import { GUIDES, type GuideKey } from '@/i18n/landingGuides'
import { SITE_URL } from '@/lib/siteUrls'
import { JsonLd } from '@/components/landing/StructuredData'
import { GUIDE_PATHS } from '@/components/landing/contentPaths'
import { EN_HOME_PATH, GUIDE_PATHS_EN } from '@/components/landing/sitePaths'
import type { Lang } from '@/i18n/translations'

// Server side of a guide: search metadata and schema.org data, read from the
// same catalogue the page renders (Spanish, like every server-rendered head on
// the landing). The FAQPage is the page's visible FAQ, item for item.
export function guideMetadataArgs(key: GuideKey, lang: Lang = 'es'): [string, string, string] {
  const g = GUIDES[lang].items[key]
  return [(lang === 'en' ? GUIDE_PATHS_EN : GUIDE_PATHS)[key], g.metaTitle, g.metaDesc]
}

export function GuideStructuredData({ guide, lang = 'es' }: { guide: GuideKey; lang?: Lang }) {
  const g = GUIDES[lang].items[guide]
  const url = `${SITE_URL}${(lang === 'en' ? GUIDE_PATHS_EN : GUIDE_PATHS)[guide]}`
  const home = lang === 'en' ? `${SITE_URL}${EN_HOME_PATH}` : `${SITE_URL}/`
  return (
    <JsonLd
      graph={[
        {
          '@type': guide === 'about' ? 'AboutPage' : 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: g.title,
          description: g.metaDesc,
          inLanguage: lang,
          isPartOf: { '@id': `${SITE_URL}/#website` },
          ...(guide === 'about' ? { about: { '@id': `${SITE_URL}/#organization` } } : {}),
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: LANDING[lang].pages.home, item: home },
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

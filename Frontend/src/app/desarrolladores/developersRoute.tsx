import type { Metadata } from 'next'
import { JetBrains_Mono } from 'next/font/google'
import DevelopersPage from '@/app/desarrolladores/DevelopersPage'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { DEVELOPERS_PATH, DEVELOPERS_PATH_EN, EN_HOME_PATH } from '@/components/landing/sitePaths'
import { DEVELOPERS } from '@/i18n/developers'
import { SITE_META } from '@/i18n/siteMeta'
import type { Lang } from '@/i18n/translations'
import { SITE_URL } from '@/lib/siteUrls'

// The API reference's server side in either language: /desarrolladores
// (Spanish) and /en/developers (English). The page body is a client component
// that follows the language of the URL.

/** The code face for every sample on the page — self-hosted by Next at build
 *  time like the brand face in app/layout.tsx, loaded only on these routes. */
const codeFont = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  variable: '--font-code',
  display: 'swap',
})

const pathOf = (lang: Lang) => (lang === 'en' ? DEVELOPERS_PATH_EN : DEVELOPERS_PATH)

export function developersMetadata(lang: Lang): Metadata {
  const M = SITE_META[lang].developers
  return subpageMetadata(pathOf(lang), M.title, M.description)
}

export function DevelopersRoute({ lang }: { lang: Lang }) {
  const D = DEVELOPERS[lang]
  const url = `${SITE_URL}${pathOf(lang)}`
  return (
    <>
      <JsonLd
        graph={[
          {
            '@type': 'WebPage',
            '@id': `${url}#webpage`,
            url,
            name: D.title,
            description: D.intro,
            inLanguage: lang,
            isPartOf: { '@id': `${SITE_URL}/#website` },
          },
          {
            '@type': 'BreadcrumbList',
            itemListElement: [
              { '@type': 'ListItem', position: 1, name: D.home, item: lang === 'en' ? `${SITE_URL}${EN_HOME_PATH}` : `${SITE_URL}/` },
              { '@type': 'ListItem', position: 2, name: D.label, item: url },
            ],
          },
        ]}
      />
      <div className={codeFont.variable}>
        <DevelopersPage />
      </div>
    </>
  )
}

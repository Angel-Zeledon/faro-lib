import DocsShell from '@/components/docs/DocsShell'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { DOCS_CHROME } from '@/i18n/docs/chrome'
import { DOCS_BASE } from '@/i18n/docs/tree'
import { navTitles } from '@/app/docs/navTitles'
import { SITE_URL } from '@/lib/siteUrls'

// /docs — the help center's front page: every section and its pages, the
// search, the PDF manual and the API reference.
const C = DOCS_CHROME.es

export const metadata = subpageMetadata(DOCS_BASE, C.metaTitle, C.intro)

export default function Page() {
  const url = `${SITE_URL}${DOCS_BASE}`
  return (
    <>
      <JsonLd
        graph={[
          {
            '@type': 'CollectionPage',
            '@id': `${url}#webpage`,
            url,
            name: C.title,
            description: C.intro,
            inLanguage: 'es',
            isPartOf: { '@id': `${SITE_URL}/#website` },
          },
          {
            '@type': 'BreadcrumbList',
            itemListElement: [
              { '@type': 'ListItem', position: 1, name: C.home, item: `${SITE_URL}/` },
              { '@type': 'ListItem', position: 2, name: C.label, item: url },
            ],
          },
        ]}
      />
      <DocsShell titles={navTitles()} />
    </>
  )
}

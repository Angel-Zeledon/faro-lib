import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'
import { JsonLd } from '@/components/landing/StructuredData'

// schema.org data for the content pages, rendered on the server in Spanish like
// the rest of the landing's structured data. Only what the page itself says:
// the page, its breadcrumb and (on the industry pages) its parent hub. No
// ratings, no reviews, no offers beyond what /precios already publishes.
export function ContentStructuredData({ path, name, description, parent }: {
  path: string
  name: string
  description: string
  /** The hub this page sits under, when it is not directly under Inicio. */
  parent?: { path: string; name: string }
}) {
  const url = `${SITE_URL}${path}`
  const trail = [
    { name: LANDING.es.pages.home, item: `${SITE_URL}/` },
    ...(parent ? [{ name: parent.name, item: `${SITE_URL}${parent.path}` }] : []),
    { name, item: url },
  ]
  return (
    <JsonLd
      graph={[
        {
          '@type': 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name,
          description,
          inLanguage: 'es',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: trail.map((t, i) => ({
            '@type': 'ListItem',
            position: i + 1,
            name: t.name,
            item: t.item,
          })),
        },
      ]}
    />
  )
}

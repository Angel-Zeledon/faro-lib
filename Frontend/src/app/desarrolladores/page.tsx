import DevelopersPage from './DevelopersPage'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { DEVELOPERS } from '@/i18n/developers'
import { SITE_URL } from '@/lib/siteUrls'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata and structured data
// live here, in Spanish like the other public subpages.
const PATH = '/desarrolladores'
const D = DEVELOPERS.es

export const metadata = subpageMetadata(
  PATH,
  'API de StockAI — Documentación para desarrolladores',
  'Conecta tu ERP, POS o sistema propio a StockAI: autenticación con API key, claves de lectura y ' +
  'escritura, límites, cobro por llamada y la referencia completa de endpoints con ejemplos curl.',
)

export default function Page() {
  const url = `${SITE_URL}${PATH}`
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
            inLanguage: 'es',
            isPartOf: { '@id': `${SITE_URL}/#website` },
          },
          {
            '@type': 'BreadcrumbList',
            itemListElement: [
              { '@type': 'ListItem', position: 1, name: D.home, item: `${SITE_URL}/` },
              { '@type': 'ListItem', position: 2, name: D.label, item: url },
            ],
          },
        ]}
      />
      <DevelopersPage />
    </>
  )
}

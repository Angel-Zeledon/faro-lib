import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'

// schema.org data for the landing, rendered on the server so a crawler reads
// it without running the page's JavaScript. Spanish, like the server-rendered
// title: it is the primary market and the language a crawler sees first.
//
// Only what the page itself already says. The FAQ is read from the same
// catalogue the page renders, so the two cannot disagree; the offer is the free
// plan at 0 because that is the one price that exists — the full plan has no
// list price (it is a conversation), so it is not published as one.
export default function StructuredData() {
  const L = LANDING.es
  const graph = [
    {
      '@type': 'Organization',
      '@id': `${SITE_URL}/#organization`,
      name: 'StockAI',
      url: `${SITE_URL}/`,
      logo: `${SITE_URL}/og-image.png`,
      areaServed: 'Latin America',
    },
    {
      '@type': 'WebSite',
      '@id': `${SITE_URL}/#website`,
      url: `${SITE_URL}/`,
      name: 'StockAI',
      inLanguage: ['es', 'en'],
      publisher: { '@id': `${SITE_URL}/#organization` },
    },
    {
      '@type': 'SoftwareApplication',
      name: 'StockAI',
      applicationCategory: 'BusinessApplication',
      applicationSubCategory: 'Inventory management',
      operatingSystem: 'Web',
      url: `${SITE_URL}/`,
      description: L.hero.lead,
      image: `${SITE_URL}/og-image.png`,
      publisher: { '@id': `${SITE_URL}/#organization` },
      offers: {
        '@type': 'Offer',
        name: L.pricing.freeLabel,
        price: '0',
        priceCurrency: 'USD',
        description: L.pricing.freeNote,
      },
    },
    {
      '@type': 'FAQPage',
      mainEntity: L.faq.items.map(f => ({
        '@type': 'Question',
        name: f.q,
        acceptedAnswer: { '@type': 'Answer', text: f.a },
      })),
    },
  ]

  return (
    <script
      type="application/ld+json"
      // `<` escaped so no string in the catalogue can close the script tag.
      dangerouslySetInnerHTML={{
        __html: JSON.stringify({ '@context': 'https://schema.org', '@graph': graph })
          .replace(/</g, '\\u003c'),
      }}
    />
  )
}

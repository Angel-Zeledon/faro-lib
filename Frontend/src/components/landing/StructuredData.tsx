import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'
import { SUBPAGE_PATHS, type SubpageKey } from '@/components/landing/subpagePaths'
import { CORPORATE_PLAN, CURRENCY } from '@/components/landing/pricingModel'

// schema.org data for the landing and its subpages, rendered on the server so
// a crawler reads it without running the page's JavaScript. Spanish, like the
// server-rendered title: it is the primary market and the language a crawler
// sees first.
//
// Only what the pages themselves already say. The FAQ is read from the same
// catalogue the page renders, so the two cannot disagree; the offer is the free
// plan at 0 because that is the one price that exists — the full plan has no
// list price (it is a conversation), so it is not published as one. The
// corporate band (owner, 2026-10-05) is published as what it is: a starting
// price (`minPrice`) on an annual contract, quoted in a conversation.

const L = LANDING.es

// A catalogue href (`#anchor` on the home page, or `/slug`) as an absolute URL.
const absolute = (href: string) => (href.startsWith('#') ? `${SITE_URL}/${href}` : `${SITE_URL}${href}`)

export function JsonLd({ graph }: { graph: object[] }) {
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

// Inicio → <subpage>, for the subpage's own head.
export function breadcrumbFor(page: SubpageKey) {
  return {
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: L.pages.home, item: `${SITE_URL}/` },
      { '@type': 'ListItem', position: 2, name: L.pages[page].label, item: `${SITE_URL}${SUBPAGE_PATHS[page]}` },
    ],
  }
}

// One subpage's head: the breadcrumb, the page itself as part of the site,
// and whatever else that page carries (the FAQ page adds FAQPage).
export function SubpageStructuredData({ page, extra = [] }: { page: SubpageKey; extra?: object[] }) {
  const url = `${SITE_URL}${SUBPAGE_PATHS[page]}`
  return (
    <JsonLd
      graph={[
        {
          '@type': 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: L.pages[page].title,
          description: L.pages[page].intro,
          inLanguage: 'es',
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        breadcrumbFor(page),
        ...extra,
      ]}
    />
  )
}

// The FAQ as FAQPage. Published once, on /preguntas-frecuentes — the page
// where every answer is visible — rather than on every page that lists them.
export function faqPageGraph() {
  return {
    '@type': 'FAQPage',
    mainEntity: L.faq.items.map(f => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  }
}

export default function StructuredData() {
  // The site's sections, named the way its own menu names them: the menu's
  // entries (two of which are now real pages, /como-funciona and /precios;
  // the rest anchors that live only on the home page), plus the two subpages
  // reached from the footer. Google builds sitelinks from URLs it can name;
  // this hands it the names and the URLs instead of leaving it to guess.
  const sections: [string, string][] = [
    ...L.nav.links,
    [SUBPAGE_PATHS.faq, L.pages.faq.label],
    [SUBPAGE_PATHS.security, L.pages.security.label],
  ]
  const graph = [
    {
      '@type': 'Organization',
      '@id': `${SITE_URL}/#organization`,
      name: 'StockAI',
      // How people actually type the brand: with a space, and as the domain.
      // Naming the variants here is what lets a search for "stock ai" resolve
      // to this entity instead of to a generic phrase.
      alternateName: ['Stock AI', 'StockAI.es'],
      url: `${SITE_URL}/`,
      logo: `${SITE_URL}/og-image.png`,
      areaServed: 'Latin America',
      contactPoint: {
        '@type': 'ContactPoint',
        contactType: 'sales',
        email: 'contacto@stockai.es',
        availableLanguage: ['es', 'en'],
      },
    },
    {
      '@type': 'WebSite',
      '@id': `${SITE_URL}/#website`,
      url: `${SITE_URL}/`,
      name: 'StockAI',
      alternateName: ['Stock AI', 'StockAI.es'],
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
      // The visible "Funciones" section of the home page, item for item.
      featureList: L.features.groups.flatMap(g => g.items),
      image: `${SITE_URL}/og-image.png`,
      publisher: { '@id': `${SITE_URL}/#organization` },
      offers: [
        {
          '@type': 'Offer',
          name: L.pricing.freeLabel,
          price: '0',
          priceCurrency: 'USD',
          description: L.pricing.freeNote,
          url: `${SITE_URL}${SUBPAGE_PATHS.pricing}`,
        },
        {
          '@type': 'Offer',
          name: L.pricing.corporate.label,
          priceCurrency: CURRENCY,
          priceSpecification: {
            '@type': 'UnitPriceSpecification',
            minPrice: String(CORPORATE_PLAN.baseMonthly),
            priceCurrency: CURRENCY,
            billingDuration: 12,
            unitCode: 'MON',
            referenceQuantity: { '@type': 'QuantitativeValue', value: 1, unitCode: 'MON' },
          },
          description: L.pricing.corporate.lead,
          url: `${SITE_URL}${SUBPAGE_PATHS.pricing}`,
        },
      ],
    },
    {
      '@type': 'WebPage',
      '@id': `${SITE_URL}/#webpage`,
      url: `${SITE_URL}/`,
      name: 'StockAI',
      isPartOf: { '@id': `${SITE_URL}/#website` },
      hasPart: sections.map(([href, label]) => ({
        '@type': 'WebPageElement',
        name: label,
        url: absolute(href),
      })),
    },
    {
      '@type': 'ItemList',
      name: 'StockAI',
      itemListElement: sections.map(([href, label], i) => ({
        '@type': 'SiteNavigationElement',
        position: i + 1,
        name: label,
        url: absolute(href),
      })),
    },
  ]

  return <JsonLd graph={graph} />
}

import { LANDING } from '@/i18n/landing'
import { SITE_URL } from '@/lib/siteUrls'
import { SUBPAGE_PATHS, type SubpageKey } from '@/components/landing/subpagePaths'
import { localizeHref } from '@/components/landing/sitePaths'
import { OG_IMAGE } from '@/components/landing/subpageMetadata'
import type { Lang } from '@/i18n/translations'

// schema.org data for the landing and its subpages, rendered on the server so
// a crawler reads it without running the page's JavaScript. Spanish, like the
// server-rendered title: it is the primary market and the language a crawler
// sees first. Every function takes the page's language: an English page (/en/…)
// publishes the same data from the English catalogue, with English URLs.
//
// Only what the pages themselves already say. The FAQ is read from the same
// catalogue the page renders, so the two cannot disagree. The one offer is the
// source code at a single one-time price (owner, 2026-10-06), the same figure
// as L.source.price on the page.

// A catalogue href (`#anchor` on the home page, or `/slug`) as an absolute URL
// on the page's language version of the site.
const absolute = (href: string, lang: Lang = 'es') =>
  `${SITE_URL}${localizeHref(href.startsWith('#') ? `/${href}` : href, lang)}`

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
export function breadcrumbFor(page: SubpageKey, lang: Lang = 'es') {
  const L = LANDING[lang]
  return {
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: L.pages.home, item: absolute('/', lang) },
      { '@type': 'ListItem', position: 2, name: L.pages[page].label, item: absolute(SUBPAGE_PATHS[page], lang) },
    ],
  }
}

// One subpage's head: the breadcrumb, the page itself as part of the site,
// and whatever else that page carries (the FAQ page adds FAQPage).
export function SubpageStructuredData({ page, extra = [], lang = 'es' }: { page: SubpageKey; extra?: object[]; lang?: Lang }) {
  const L = LANDING[lang]
  const url = absolute(SUBPAGE_PATHS[page], lang)
  return (
    <JsonLd
      graph={[
        {
          '@type': 'WebPage',
          '@id': `${url}#webpage`,
          url,
          name: L.pages[page].title,
          description: L.pages[page].intro,
          inLanguage: lang,
          isPartOf: { '@id': `${SITE_URL}/#website` },
        },
        breadcrumbFor(page, lang),
        ...extra,
      ]}
    />
  )
}

// The FAQ as FAQPage. Published once, on /preguntas-frecuentes — the page
// where every answer is visible — rather than on every page that lists them.
export function faqPageGraph(lang: Lang = 'es') {
  const L = LANDING[lang]
  return {
    '@type': 'FAQPage',
    mainEntity: L.faq.items.map(f => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  }
}

export default function StructuredData({ lang = 'es' }: { lang?: Lang }) {
  const L = LANDING[lang]
  // The site-wide entities keep one @id (and the Spanish home as their URL):
  // the English home is the same organization and the same website.
  const home = absolute('/', lang)
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
      // A square mark: search engines build the site icon and the knowledge
      // panel logo from this, and the 1200x630 social card crops badly.
      logo: `${SITE_URL}/brand/favicon-192.png`,
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
      applicationSubCategory: 'Sales forecasting',
      operatingSystem: 'Web',
      url: home,
      description: L.hero.lead,
      // The visible "Funciones" section of the home page, item for item.
      featureList: L.features.groups.flatMap(g => g.items),
      image: `${SITE_URL}${OG_IMAGE[lang]}`,
      publisher: { '@id': `${SITE_URL}/#organization` },
      offers: [
        {
          '@type': 'Offer',
          name: L.source.title,
          price: '14999',
          priceCurrency: 'USD',
          description: L.source.lead,
          url: absolute(SUBPAGE_PATHS.pricing, lang),
        },
      ],
    },
    {
      '@type': 'WebPage',
      '@id': `${home}#webpage`,
      url: home,
      name: 'StockAI',
      ...(lang === 'en' ? { inLanguage: 'en' } : {}),
      isPartOf: { '@id': `${SITE_URL}/#website` },
      hasPart: sections.map(([href, label]) => ({
        '@type': 'WebPageElement',
        name: label,
        url: absolute(href, lang),
      })),
    },
    {
      '@type': 'ItemList',
      name: 'StockAI',
      itemListElement: sections.map(([href, label], i) => ({
        '@type': 'SiteNavigationElement',
        position: i + 1,
        name: label,
        url: absolute(href, lang),
      })),
    },
  ]

  return <JsonLd graph={graph} />
}

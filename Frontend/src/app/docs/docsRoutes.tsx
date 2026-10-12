import type { Metadata } from 'next'
import DocsShell from '@/components/docs/DocsShell'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { DOCS_BASE_EN, EN_HOME_PATH, docHrefFor } from '@/components/landing/sitePaths'
import { DOCS, docHeadings, neighbours } from '@/i18n/docs/index'
import { DOCS_CHROME } from '@/i18n/docs/chrome'
import { DOC_TREE, DOCS_BASE, sectionOf, type DocPageId } from '@/i18n/docs/tree'
import type { Lang } from '@/i18n/translations'
import { navTitles } from '@/app/docs/navTitles'
import { SITE_URL } from '@/lib/siteUrls'

// The help center's two routes, in either language: /docs and /docs/<id> in
// Spanish, /en/docs and /en/docs/<English id> in English. The page files only
// pick the language; the head, the schema.org data and the body come from here,
// so the two versions cannot drift apart.

const baseOf = (lang: Lang) => (lang === 'en' ? DOCS_BASE_EN : DOCS_BASE)
const homeOf = (lang: Lang) => (lang === 'en' ? `${SITE_URL}${EN_HOME_PATH}` : `${SITE_URL}/`)

export function docsHomeMetadata(lang: Lang): Metadata {
  const C = DOCS_CHROME[lang]
  return subpageMetadata(baseOf(lang), C.metaTitle, C.intro)
}

export function DocsHomeRoute({ lang }: { lang: Lang }) {
  const C = DOCS_CHROME[lang]
  const url = `${SITE_URL}${baseOf(lang)}`
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
            inLanguage: lang,
            isPartOf: { '@id': `${SITE_URL}/#website` },
          },
          {
            '@type': 'BreadcrumbList',
            itemListElement: [
              { '@type': 'ListItem', position: 1, name: C.home, item: homeOf(lang) },
              { '@type': 'ListItem', position: 2, name: C.label, item: url },
            ],
          },
        ]}
      />
      <DocsShell titles={navTitles()} />
    </>
  )
}

export function docPageMetadata(id: DocPageId, lang: Lang): Metadata {
  const page = DOCS[lang][id]
  return subpageMetadata(docHrefFor(id, lang), `${DOCS_CHROME[lang].metaPrefix}${page.title}`, page.description)
}

export function DocPageRoute({ id, lang }: { id: DocPageId; lang: Lang }) {
  const es = DOCS.es[id]
  const en = DOCS.en[id]
  const doc = DOCS[lang][id]
  const section = sectionOf(id)
  const single = DOC_TREE.find(s => s.slug === section)!.pages.length === 0
  const url = `${SITE_URL}${docHrefFor(id, lang)}`
  const base = `${SITE_URL}${baseOf(lang)}`
  const C = DOCS_CHROME[lang]

  const crumbs = [
    { name: C.home, item: homeOf(lang) },
    { name: C.label, item: base },
    ...(single ? [] : [{ name: C.sections[section].title, item: `${base}#${section}` }]),
    { name: doc.title, item: url },
  ]

  return (
    <>
      <JsonLd
        graph={[
          {
            '@type': 'TechArticle',
            '@id': `${url}#article`,
            url,
            headline: doc.title,
            description: doc.description,
            inLanguage: lang,
            isPartOf: { '@id': `${SITE_URL}/#website` },
            about: { '@type': 'SoftwareApplication', name: 'StockAI' },
          },
          {
            '@type': 'BreadcrumbList',
            itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.item })),
          },
        ]}
      />
      <DocsShell
        titles={navTitles()}
        page={{
          id,
          content: { es, en },
          headings: { es: docHeadings(es), en: docHeadings(en) },
          ...neighbours(id),
        }}
      />
    </>
  )
}

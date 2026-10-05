import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import DocsShell from '@/components/docs/DocsShell'
import { JsonLd } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { DOCS, docHeadings, neighbours } from '@/i18n/docs/index'
import { DOCS_CHROME } from '@/i18n/docs/chrome'
import { DOC_ORDER, DOC_TREE, DOCS_BASE, docHref, isDocPageId, sectionOf, type DocPageId } from '@/i18n/docs/tree'
import { navTitles } from '@/app/docs/navTitles'
import { SITE_URL } from '@/lib/siteUrls'

// One help-center page, rendered on the server: its metadata, its schema.org
// breadcrumb and every word of its body are in the HTML a crawler reads. The
// body is the client shell (it follows the visitor's language and theme), so
// the page arrives in both languages and the HTML carries the Spanish one.

export const dynamicParams = false

export function generateStaticParams() {
  return DOC_ORDER.map(id => ({ slug: id.split('/') }))
}

function resolve(slug: string[]): DocPageId | null {
  const id = slug.join('/')
  return isDocPageId(id) ? id : null
}

export function generateMetadata({ params }: { params: { slug: string[] } }): Metadata {
  const id = resolve(params.slug)
  if (!id) return {}
  const page = DOCS.es[id]
  return subpageMetadata(docHref(id), `StockAI ayuda: ${page.title}`, page.description)
}

export default function Page({ params }: { params: { slug: string[] } }) {
  const id = resolve(params.slug)
  if (!id) notFound()
  const es = DOCS.es[id]
  const en = DOCS.en[id]
  const section = sectionOf(id)
  const single = DOC_TREE.find(s => s.slug === section)!.pages.length === 0
  const url = `${SITE_URL}${docHref(id)}`
  const C = DOCS_CHROME.es

  const crumbs = [
    { name: C.home, item: `${SITE_URL}/` },
    { name: C.label, item: `${SITE_URL}${DOCS_BASE}` },
    ...(single ? [] : [{ name: C.sections[section].title, item: `${SITE_URL}${DOCS_BASE}#${section}` }]),
    { name: es.title, item: url },
  ]

  return (
    <>
      <JsonLd
        graph={[
          {
            '@type': 'TechArticle',
            '@id': `${url}#article`,
            url,
            headline: es.title,
            description: es.description,
            inLanguage: 'es',
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

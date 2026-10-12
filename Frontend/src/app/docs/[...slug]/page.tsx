import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import { DocPageRoute, docPageMetadata } from '@/app/docs/docsRoutes'
import { DOC_ORDER, isDocPageId, type DocPageId } from '@/i18n/docs/tree'

// One help-center page, rendered on the server: its metadata, its schema.org
// breadcrumb and every word of its body are in the HTML a crawler reads, in
// Spanish. The English version lives at /en/docs/<English id>
// (app/en/docs/[...slug]/page.tsx).

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
  return docPageMetadata(id, 'es')
}

export default function Page({ params }: { params: { slug: string[] } }) {
  const id = resolve(params.slug)
  if (!id) notFound()
  return <DocPageRoute id={id} lang="es" />
}

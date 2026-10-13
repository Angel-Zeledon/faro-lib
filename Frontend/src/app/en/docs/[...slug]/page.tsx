import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import { DocPageRoute, docPageMetadata } from '@/app/docs/docsRoutes'
import { DOC_IDS_EN, docIdFromEn } from '@/components/landing/sitePaths'
import { DOC_ORDER } from '@/i18n/docs/tree'

// One help-center page in English, at /en/docs/<English id> (Spanish:
// /docs/<id>). Only the ids in DOC_IDS_EN exist; anything else is a 404.

export const dynamicParams = false

export function generateStaticParams() {
  return DOC_ORDER.map(id => ({ slug: DOC_IDS_EN[id].split('/') }))
}

export function generateMetadata({ params }: { params: { slug: string[] } }): Metadata {
  const id = docIdFromEn(params.slug.join('/'))
  if (!id) return {}
  return docPageMetadata(id, 'en')
}

export default function Page({ params }: { params: { slug: string[] } }) {
  const id = docIdFromEn(params.slug.join('/'))
  if (!id) notFound()
  return <DocPageRoute id={id} lang="en" />
}

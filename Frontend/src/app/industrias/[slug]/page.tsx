import { notFound } from 'next/navigation'
import { IndustryPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import {
  INDUSTRIES_HUB_PATH, INDUSTRY_ORDER, INDUSTRY_PATHS, INDUSTRY_SLUGS, type IndustryKey,
} from '@/components/landing/contentPaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

// One page per industry, each at its own URL (/industrias/<slug>). Only the
// slugs in INDUSTRY_SLUGS exist; anything else is a 404, never a blank page.
export const dynamicParams = false

export function generateStaticParams() {
  return INDUSTRY_ORDER.map(k => ({ slug: INDUSTRY_SLUGS[k] }))
}

function keyFor(slug: string): IndustryKey | null {
  return INDUSTRY_ORDER.find(k => INDUSTRY_SLUGS[k] === slug) ?? null
}

export function generateMetadata({ params }: { params: { slug: string } }) {
  const key = keyFor(params.slug)
  if (!key) return {}
  const I = LANDING_CONTENT.es.industries.items[key]
  return subpageMetadata(INDUSTRY_PATHS[key], I.metaTitle, I.metaDesc)
}

export default function Page({ params }: { params: { slug: string } }) {
  const key = keyFor(params.slug)
  if (!key) notFound()
  const I = LANDING_CONTENT.es.industries.items[key]
  return (
    <>
      <ContentStructuredData
        path={INDUSTRY_PATHS[key]}
        name={I.label}
        description={I.metaDesc}
        parent={{ path: INDUSTRIES_HUB_PATH, name: LANDING_CONTENT.es.industries.hub.label }}
      />
      <IndustryPage industry={key} />
    </>
  )
}

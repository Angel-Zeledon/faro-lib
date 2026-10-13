import { notFound } from 'next/navigation'
import { IndustryPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { INDUSTRY_ORDER, type IndustryKey } from '@/components/landing/contentPaths'
import { INDUSTRIES_HUB_PATH_EN, INDUSTRY_PATHS_EN, INDUSTRY_SLUGS_EN } from '@/components/landing/sitePaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

// One industry page in English (/en/industries/<slug>). Only the slugs in
// INDUSTRY_SLUGS_EN exist; anything else is a 404, never a blank page.
export const dynamicParams = false

export function generateStaticParams() {
  return INDUSTRY_ORDER.map(k => ({ slug: INDUSTRY_SLUGS_EN[k] }))
}

function keyFor(slug: string): IndustryKey | null {
  return INDUSTRY_ORDER.find(k => INDUSTRY_SLUGS_EN[k] === slug) ?? null
}

export function generateMetadata({ params }: { params: { slug: string } }) {
  const key = keyFor(params.slug)
  if (!key) return {}
  const I = LANDING_CONTENT.en.industries.items[key]
  return subpageMetadata(INDUSTRY_PATHS_EN[key], I.metaTitle, I.metaDesc)
}

export default function Page({ params }: { params: { slug: string } }) {
  const key = keyFor(params.slug)
  if (!key) notFound()
  const I = LANDING_CONTENT.en.industries.items[key]
  return (
    <>
      <ContentStructuredData
        path={INDUSTRY_PATHS_EN[key]}
        name={I.label}
        description={I.metaDesc}
        parent={{ path: INDUSTRIES_HUB_PATH_EN, name: LANDING_CONTENT.en.industries.hub.label }}
      />
      <IndustryPage industry={key} />
    </>
  )
}

import { IndustriesHubPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { INDUSTRIES_HUB_PATH_EN } from '@/components/landing/sitePaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

// /en/industries — the industries hub in English (Spanish: /industrias).
const H = LANDING_CONTENT.en.industries.hub

export const metadata = subpageMetadata(INDUSTRIES_HUB_PATH_EN, H.metaTitle, H.metaDesc)

export default function Page() {
  return (
    <>
      <ContentStructuredData path={INDUSTRIES_HUB_PATH_EN} name={H.label} description={H.metaDesc} />
      <IndustriesHubPage />
    </>
  )
}

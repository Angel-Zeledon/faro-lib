import { IndustriesHubPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { INDUSTRIES_HUB_PATH } from '@/components/landing/contentPaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

const H = LANDING_CONTENT.es.industries.hub

export const metadata = subpageMetadata(INDUSTRIES_HUB_PATH, H.metaTitle, H.metaDesc)

export default function Page() {
  return (
    <>
      <ContentStructuredData path={INDUSTRIES_HUB_PATH} name={H.label} description={H.metaDesc} />
      <IndustriesHubPage />
    </>
  )
}

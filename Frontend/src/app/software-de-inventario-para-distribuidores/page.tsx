import { GuidePage } from '@/components/landing/GuidePages'
import { GuideStructuredData, guideMetadataArgs } from '@/components/landing/GuideStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the body is a client component (it follows the visitor's
// language and theme), so its search metadata and structured data live here.
export const metadata = subpageMetadata(...guideMetadataArgs('distributors'))

export default function Page() {
  return (
    <>
      <GuideStructuredData guide="distributors" />
      <GuidePage guide="distributors" />
    </>
  )
}

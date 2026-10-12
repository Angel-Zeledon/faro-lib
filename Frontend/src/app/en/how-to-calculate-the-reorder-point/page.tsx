import { GuidePage } from '@/components/landing/GuidePages'
import { GuideStructuredData, guideMetadataArgs } from '@/components/landing/GuideStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// The English version of a guide (Spanish: GUIDE_PATHS.reorderPoint). The body
// follows the language of the URL.
export const metadata = subpageMetadata(...guideMetadataArgs('reorderPoint', 'en'))

export default function Page() {
  return (
    <>
      <GuideStructuredData guide="reorderPoint" lang="en" />
      <GuidePage guide="reorderPoint" />
    </>
  )
}

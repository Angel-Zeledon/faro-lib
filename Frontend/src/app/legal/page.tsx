import { LegalHub } from '@/components/landing/Legal'
import { LegalHubStructuredData, legalHubMetadata } from '@/components/landing/legalMetadata'

// Server wrapper: the hub is a client component (it follows the visitor's
// language and theme), so its search metadata lives here.
export const metadata = legalHubMetadata()

export default function Page() {
  return (
    <>
      <LegalHubStructuredData />
      <LegalHub />
    </>
  )
}

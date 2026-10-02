import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// Server wrapper: the document is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = legalMetadata('cookies')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="cookies" />
      <LegalPage doc="cookies" />
    </>
  )
}

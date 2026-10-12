import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.acceptableUse).
export const metadata = legalMetadata('acceptableUse', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="acceptableUse" lang="en" />
      <LegalPage doc="acceptableUse" />
    </>
  )
}

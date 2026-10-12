import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.commercial).
export const metadata = legalMetadata('commercial', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="commercial" lang="en" />
      <LegalPage doc="commercial" />
    </>
  )
}

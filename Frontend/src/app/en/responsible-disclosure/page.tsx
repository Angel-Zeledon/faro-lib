import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.disclosure).
export const metadata = legalMetadata('disclosure', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="disclosure" lang="en" />
      <LegalPage doc="disclosure" />
    </>
  )
}

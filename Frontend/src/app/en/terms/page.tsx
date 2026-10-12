import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.terms).
export const metadata = legalMetadata('terms', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="terms" lang="en" />
      <LegalPage doc="terms" />
    </>
  )
}

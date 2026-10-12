import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.dpa).
export const metadata = legalMetadata('dpa', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="dpa" lang="en" />
      <LegalPage doc="dpa" />
    </>
  )
}

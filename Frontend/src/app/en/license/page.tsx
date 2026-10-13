import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.license).
export const metadata = legalMetadata('license', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="license" lang="en" />
      <LegalPage doc="license" />
    </>
  )
}

import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.accessibility).
export const metadata = legalMetadata('accessibility', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="accessibility" lang="en" />
      <LegalPage doc="accessibility" />
    </>
  )
}

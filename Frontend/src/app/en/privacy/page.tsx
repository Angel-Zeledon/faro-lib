import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.privacy).
export const metadata = legalMetadata('privacy', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="privacy" lang="en" />
      <LegalPage doc="privacy" />
    </>
  )
}

import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.notice).
export const metadata = legalMetadata('notice', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="notice" lang="en" />
      <LegalPage doc="notice" />
    </>
  )
}

import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.cookies).
export const metadata = legalMetadata('cookies', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="cookies" lang="en" />
      <LegalPage doc="cookies" />
    </>
  )
}

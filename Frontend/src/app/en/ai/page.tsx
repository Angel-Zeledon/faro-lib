import { LegalPage } from '@/components/landing/Legal'
import { LegalStructuredData, legalMetadata } from '@/components/landing/legalMetadata'

// The English version of a legal document (Spanish: LEGAL_PATHS.ai).
export const metadata = legalMetadata('ai', 'en')

export default function Page() {
  return (
    <>
      <LegalStructuredData doc="ai" lang="en" />
      <LegalPage doc="ai" />
    </>
  )
}

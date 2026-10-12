import { LegalHub } from '@/components/landing/Legal'
import { LegalHubStructuredData, legalHubMetadata } from '@/components/landing/legalMetadata'

// /en/legal — every legal document, grouped, in English (Spanish: /legal).
export const metadata = legalHubMetadata('en')

export default function Page() {
  return (
    <>
      <LegalHubStructuredData lang="en" />
      <LegalHub />
    </>
  )
}

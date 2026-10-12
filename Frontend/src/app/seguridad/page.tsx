import { SecurityPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { SUBPAGE_PATHS } from '@/components/landing/subpagePaths'
import { SITE_META } from '@/i18n/siteMeta'

// Server wrapper: the page body is a client component (it follows the
// language of the URL and the visitor's theme), so its search metadata lives here.
// The same page in the other language: /en/security.
const M = SITE_META.es.subpages.security

export const metadata = subpageMetadata(SUBPAGE_PATHS.security, M.title, M.description)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="security" />
      <SecurityPage />
    </>
  )
}

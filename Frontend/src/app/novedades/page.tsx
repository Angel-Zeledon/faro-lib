import { ChangelogPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { CONTENT_PATHS } from '@/components/landing/contentPaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata and structured data
// live here, in Spanish like the other public pages.
const PATH = CONTENT_PATHS.changelog
const C = LANDING_CONTENT.es.changelog

export const metadata = subpageMetadata(PATH, C.metaTitle, C.metaDesc)

export default function Page() {
  return (
    <>
      <ContentStructuredData path={PATH} name={C.label} description={C.metaDesc} />
      <ChangelogPage />
    </>
  )
}

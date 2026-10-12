import { ExcelPage } from '@/components/landing/ContentPages'
import { ContentStructuredData } from '@/components/landing/ContentStructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { CONTENT_PATHS_EN } from '@/components/landing/sitePaths'
import { LANDING_CONTENT } from '@/i18n/landingContent'

// The English version of a content page (the Spanish one is at
// CONTENT_PATHS.excel). The body follows the language of the URL.
const PATH = CONTENT_PATHS_EN.excel
const C = LANDING_CONTENT.en.excel

export const metadata = subpageMetadata(PATH, C.metaTitle, C.metaDesc)

export default function Page() {
  return (
    <>
      <ContentStructuredData path={PATH} name={C.label} description={C.metaDesc} />
      <ExcelPage />
    </>
  )
}

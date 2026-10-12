import { FaqPage } from '@/components/landing/Subpages'
import { SubpageStructuredData, faqPageGraph } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'
import { SUBPAGE_PATHS } from '@/components/landing/subpagePaths'
import { SITE_META } from '@/i18n/siteMeta'

// Server wrapper: the page body is a client component (it follows the
// language of the URL and the visitor's theme), so its search metadata lives here.
// The FAQPage data is published here and only here — the page where every
// answer is visible.
// The same page in the other language: /en/faq.
const M = SITE_META.es.subpages.faq

export const metadata = subpageMetadata(SUBPAGE_PATHS.faq, M.title, M.description)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="faq" extra={[faqPageGraph()]} />
      <FaqPage />
    </>
  )
}

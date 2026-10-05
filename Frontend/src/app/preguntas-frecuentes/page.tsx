import { FaqPage } from '@/components/landing/Subpages'
import { SubpageStructuredData, faqPageGraph } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here. The
// FAQPage data is published here and only here — the page where every answer
// is visible.
export const metadata = subpageMetadata(
  '/preguntas-frecuentes',
  'StockAI preguntas frecuentes: datos, plan gratis y bodegas',
  'Qué datos necesitas, cómo se amplía el plan gratis, si sirve con varias bodegas, ' +
  'cómo aprende el plazo de cada proveedor y quién ve tus datos en StockAI.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="faq" extra={[faqPageGraph()]} />
      <FaqPage />
    </>
  )
}

import { HowItWorksPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = subpageMetadata(
  '/como-funciona',
  'StockAI: cómo funciona, de tus ventas al pronóstico y a la compra',
  'Sube tus ventas en CSV o Excel: los modelos compiten por pronosticar cada producto y, con ese ' +
  'pronóstico, StockAI te dice qué pedir. Los pasos, la regla del semáforo y cada pantalla con capturas reales.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="how" />
      <HowItWorksPage />
    </>
  )
}

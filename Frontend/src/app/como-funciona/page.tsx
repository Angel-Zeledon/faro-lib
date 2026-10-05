import { HowItWorksPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = subpageMetadata(
  '/como-funciona',
  'StockAI: cómo funciona, de tus ventas a la orden de compra',
  'Sube tus ventas en CSV o Excel y StockAI te dice qué pedir hoy y cuánto. ' +
  'Los cuatro pasos, la regla del semáforo y cada pantalla con capturas reales.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="how" />
      <HowItWorksPage />
    </>
  )
}

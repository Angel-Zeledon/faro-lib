import { PricingPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = subpageMetadata(
  '/precios',
  'StockAI precios: plan gratis para siempre, sin tarjeta',
  'StockAI es gratis para siempre y con todas las funciones: 100 productos, 2 usuarios y 1 bodega. ' +
  '¿Poco? Escríbenos y lo ampliamos, sin tarjeta ni checkout.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="pricing" />
      <PricingPage />
    </>
  )
}

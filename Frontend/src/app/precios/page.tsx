import { PricingPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = subpageMetadata(
  '/precios',
  'StockAI precio: código fuente por $14.999 USD, pago único',
  'StockAI se vende solo como código fuente: $14.999 USD en un pago único, para instalarlo en tu propia ' +
  'infraestructura. Incluye todas las funciones del producto, con API, servidor MCP y bot de WhatsApp.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="pricing" />
      <PricingPage />
    </>
  )
}

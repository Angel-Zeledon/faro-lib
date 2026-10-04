import { SecurityPage } from '@/components/landing/Subpages'
import { SubpageStructuredData } from '@/components/landing/StructuredData'
import { subpageMetadata } from '@/components/landing/subpageMetadata'

// Server wrapper: the page body is a client component (it follows the
// visitor's language and theme), so its search metadata lives here.
export const metadata = subpageMetadata(
  '/seguridad',
  'Seguridad y privacidad de tus datos — StockAI',
  'Cada empresa ve solo sus datos, permisos por rol, credenciales cifradas y borrado completo si te vas. ' +
  'Así cuida StockAI tus ventas y tu inventario.',
)

export default function Page() {
  return (
    <>
      <SubpageStructuredData page="security" />
      <SecurityPage />
    </>
  )
}

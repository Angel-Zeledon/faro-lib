import { DevelopersRoute, developersMetadata } from '@/app/desarrolladores/developersRoute'

// Server wrapper: the page body is a client component (it follows the language
// of the URL and the visitor's theme), so its search metadata and structured
// data live here. /en/developers is the English version.
export const metadata = developersMetadata('es')

export default function Page() {
  return <DevelopersRoute lang="es" />
}

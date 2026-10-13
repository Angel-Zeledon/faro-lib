import { DocsHomeRoute, docsHomeMetadata } from '@/app/docs/docsRoutes'

// /docs — the help center's front page: every section and its pages, the
// search, the PDF manual and the API reference. /en/docs is the same page in
// English (app/en/docs/page.tsx).
export const metadata = docsHomeMetadata('es')

export default function Page() {
  return <DocsHomeRoute lang="es" />
}

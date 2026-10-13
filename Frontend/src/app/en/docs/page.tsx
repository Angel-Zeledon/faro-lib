import { DocsHomeRoute, docsHomeMetadata } from '@/app/docs/docsRoutes'

// /en/docs — the help center's front page in English (Spanish: /docs).
export const metadata = docsHomeMetadata('en')

export default function Page() {
  return <DocsHomeRoute lang="en" />
}

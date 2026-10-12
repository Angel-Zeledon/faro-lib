import { DevelopersRoute, developersMetadata } from '@/app/desarrolladores/developersRoute'

// /en/developers — the API reference in English (Spanish: /desarrolladores).
export const metadata = developersMetadata('en')

export default function Page() {
  return <DevelopersRoute lang="en" />
}

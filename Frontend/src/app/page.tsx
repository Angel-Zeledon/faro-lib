import { LandingHome, homeMetadata } from '@/components/landing/homeRoute'

// The landing is a client component (it follows the language of the URL and
// the visitor's theme), so its search metadata lives in this server wrapper —
// the only place Next lets a route declare it. /en is the English version.
export const metadata = homeMetadata('es')

export default function Home() {
  return <LandingHome lang="es" />
}

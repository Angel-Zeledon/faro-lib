import { renderOgCard } from '@/components/landing/ogCard'

// /og-image-en.png — the English link-preview card, named by every /en page
// (the Spanish one is /og-image.png). Drawn in components/landing/ogCard.tsx.
export const runtime = 'edge'

export function GET() {
  return renderOgCard('en')
}

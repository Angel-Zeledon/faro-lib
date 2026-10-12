import { renderOgCard } from '@/components/landing/ogCard'

// /og-image.png — the Spanish link-preview card (the English one is
// /og-image-en.png). Drawn in components/landing/ogCard.tsx; see the reasons
// for the edge runtime there.
export const runtime = 'edge'

export function GET() {
  return renderOgCard('es')
}

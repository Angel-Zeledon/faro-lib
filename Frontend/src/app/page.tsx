import type { Metadata } from 'next'
import LandingPage from '@/components/landing/LandingPage'
import StructuredData from '@/components/landing/StructuredData'

// The landing is a client component (it follows the visitor's language and
// theme), so its search metadata lives in this server wrapper — the only place
// Next lets a route declare it. Spanish, like the rest of the server-rendered
// head: it is the primary market and what a crawler sees before any script.
const TITLE = 'StockAI — Qué pedir hoy, cuánto y a qué proveedor'
const DESCRIPTION =
  'Compras de inventario para distribuidores y mayoristas de Latinoamérica: ' +
  'StockAI lee tus ventas y tu inventario y cada mañana te dice qué productos se van ' +
  'a quebrar, cuánto pedir y a quién. Plan gratis para siempre, sin tarjeta.'

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  keywords: [
    'inventario', 'compras', 'reabastecimiento', 'pronóstico de demanda',
    'distribuidores', 'mayoristas', 'punto de reorden', 'quiebre de stock',
    'sobrestock', 'órdenes de compra', 'Costa Rica', 'Latinoamérica',
  ],
  alternates: { canonical: '/' },
  // Opts back in: the root layout keeps every app route out of search.
  robots: { index: true, follow: true },
  openGraph: {
    type: 'website',
    url: '/',
    siteName: 'StockAI',
    title: TITLE,
    description: DESCRIPTION,
    locale: 'es_LA',
    alternateLocale: ['en_US'],
    images: [{ url: '/og-image.png', width: 1200, height: 630, alt: 'StockAI: qué pedir hoy, cuánto y a qué proveedor' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: TITLE,
    description: DESCRIPTION,
    images: ['/og-image.png'],
  },
}

export default function Home() {
  return (
    <>
      <StructuredData />
      <LandingPage />
    </>
  )
}

import type { Metadata } from 'next'
import LandingPage from '@/components/landing/LandingPage'
import StructuredData from '@/components/landing/StructuredData'

// The landing is a client component (it follows the visitor's language and
// theme), so its search metadata lives in this server wrapper — the only place
// Next lets a route declare it. Spanish, like the rest of the server-rendered
// head: it is the primary market and what a crawler sees before any script.
const TITLE = 'StockAI: predicción de ventas con IA, producto por producto'
const DESCRIPTION =
  'StockAI (Stock AI) pronostica la venta de cada producto: modelos de IA compiten sobre tu ' +
  'historial y se prueban contra tu pasado. Con ese pronóstico, sabes qué pedir.'

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  keywords: [
    'predicción de ventas', 'pronóstico de ventas', 'pronóstico de demanda',
    'forecasting', 'demanda comprometida', 'escenarios de demanda', 'S&OP',
    'distribuidores', 'mayoristas', 'compras', 'quiebre de stock', 'Costa Rica', 'Latinoamérica',
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
    images: [{ url: '/og-image.png', width: 1200, height: 630, alt: 'StockAI: predicción de ventas con IA, producto por producto' }],
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

import type { MetadataRoute } from 'next'

// Served as /manifest.webmanifest: what makes StockAI installable as an app
// (home screen on a phone, its own window on a desktop). It belongs to the
// app's origin — installed, it opens straight on the purchasing panel, the
// screen a buyer opens every morning.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: '/compras',
    name: 'StockAI',
    short_name: 'StockAI',
    description: 'Qué pedir hoy, cuánto y a qué proveedor.',
    lang: 'es',
    start_url: '/compras',
    scope: '/',
    display: 'standalone',
    orientation: 'any',
    background_color: '#0C3A40',
    theme_color: '#0C3A40',
    categories: ['business', 'productivity'],
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
      { src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [
      { name: 'Panel de compras', url: '/compras', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
      { name: 'Pedidos', url: '/pedidos', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
      { name: 'Inventario', url: '/inventario', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
    ],
  }
}

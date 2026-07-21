import type { Metadata, Viewport } from 'next'
import './globals.css'
import ConditionalShell from '@/components/layout/ConditionalShell'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { LanguageProvider } from '@/contexts/LanguageContext'

export const metadata: Metadata = {
  title: 'Faro — Inventario Inteligente',
  description: 'Plataforma de inventario inteligente para distribuidores y mayoristas',
  manifest: '/manifest.json',
  icons: {
    icon: '/icon.svg',
    apple: '/icon.svg',
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#6366f1',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>
        <ThemeProvider>
          <LanguageProvider>
            <ConditionalShell>{children}</ConditionalShell>
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}

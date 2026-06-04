import type { Metadata } from 'next'
import './globals.css'
import ConditionalShell from '@/components/layout/ConditionalShell'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { LanguageProvider } from '@/contexts/LanguageContext'

export const metadata: Metadata = {
  title: 'Faro — Inventario Inteligente',
  description: 'Plataforma de inventario inteligente para distribuidores y mayoristas',
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

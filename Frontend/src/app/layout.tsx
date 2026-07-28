import type { Metadata, Viewport } from 'next'
import './globals.css'
import ConditionalShell from '@/components/layout/ConditionalShell'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { LanguageProvider } from '@/contexts/LanguageContext'

export const metadata: Metadata = {
  title: 'Faro — Inventario Inteligente',
  description: 'Plataforma de inventario inteligente para distribuidores y mayoristas',
}

/**
 * Without this tag a mobile browser renders the page into a ~980px virtual
 * viewport and then zooms out, so `/hoy`'s narrow view would never trigger and
 * every screen would arrive as unreadably small desktop. `maximumScale` is
 * deliberately absent: blocking pinch-zoom on a warehouse floor, where someone
 * may be reading a SKU in bad light, is an accessibility failure.
 */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

/**
 * Applies the saved theme BEFORE the first paint.
 *
 * `ThemeProvider` can only set `data-theme` in a `useEffect`, which runs after
 * hydration — so a user who chose dark got a flash of the light default on
 * every page load. This has to be a blocking inline script in `<head>`: any
 * deferred or bundled script is already too late, and the flash is precisely
 * the interval before JS modules run.
 *
 * Silently falls back to the light default if localStorage throws, which it
 * does in some privacy modes.
 */
const NO_FLASH = `try{document.documentElement.setAttribute('data-theme',localStorage.getItem('theme')||'light')}catch(e){}`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
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

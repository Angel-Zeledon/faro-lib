import type { Metadata, Viewport } from 'next'
import { Space_Grotesk } from 'next/font/google'
import './globals.css'
import ConditionalShell from '@/components/layout/ConditionalShell'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { LanguageProvider } from '@/contexts/LanguageContext'
import { SITE_URL } from '@/lib/siteUrls'

/**
 * Rendered on the server, where the user's language is unknowable — it lives
 * in this browser's localStorage. So these are the *default* language's
 * strings (Spanish, matching `LanguageProvider`'s initial state), which is
 * also what a crawler in the primary market should see. `LanguageContext`
 * corrects the title and description after hydration for an English user;
 * the two must be kept in step with `app.title` / `app.description` in
 * `i18n/translations.ts`.
 */
export const metadata: Metadata = {
  // Resolves every relative URL in page metadata (og:image, canonical) against
  // the landing's public origin — see lib/siteUrls.ts.
  metadataBase: new URL(SITE_URL),
  title: 'StockAI — Inventario Inteligente',
  description: 'Plataforma de inventario inteligente para distribuidores y mayoristas',
  applicationName: 'StockAI',
  // Every route but the landing is the signed-in app or a sign-in form, so the
  // default is to stay out of search results; app/page.tsx opts back in.
  // robots.ts says the same thing to crawlers that read robots.txt first.
  robots: { index: false, follow: false },
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
  // The installed app's title bar and the phone's status bar (manifest.ts).
  themeColor: '#0C3A40',
  // Lets the page reach the screen edges on notched phones so the mobile tab
  // bar and header can pad themselves with env(safe-area-inset-*) instead of
  // leaving browser-painted bands (components/mobile/MobileTabBar.tsx).
  viewportFit: 'cover',
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
/** The wordmark's face (`components/brand/Wordmark.tsx`) — self-hosted by
 *  Next at build time, so no request leaves for Google at runtime. */
const brandFont = Space_Grotesk({
  subsets: ['latin'],
  weight: ['600', '700'],
  variable: '--font-brand',
  display: 'swap',
})

const NO_FLASH =`try{document.documentElement.setAttribute('data-theme',localStorage.getItem('theme')||'light')}catch(e){}`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={brandFont.variable}>
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

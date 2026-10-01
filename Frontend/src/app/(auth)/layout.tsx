'use client'
import { usePathname } from 'next/navigation'
import { Moon, Sun } from 'lucide-react'
import { Wordmark } from '@/components/brand/Wordmark'
import { AuthPanel } from '@/components/auth/AuthPanel'
import { TipsPanel } from '@/components/auth/TipsPanel'
import { useTheme } from '@/contexts/ThemeContext'
import { useLanguage } from '@/contexts/LanguageContext'

// Persistent stage for /login, /signup and /prueba (the trial account): the form on the left, the product's
// own morning list on the right. Because this layout wraps both routes, the
// panel stays MOUNTED across a /login ↔ /signup navigation and only the form
// changes.
//
// It replaced a lighthouse scene (the product used to be called Faro). A
// buyer opening the login should see what the app does for them, not a
// metaphor for a name it no longer has.
//
// Follows the app theme: every colour on the form side is an `--a-*` token
// (globals.css, "Auth split") redefined under [data-theme="dark"]. The theme
// and language can be switched from here, before signing in — the same two
// choices the app offers inside, stored the same way.
//
// Scoped to /login and /signup on purpose: verify-email, forgot-password and
// reset-password keep their own full-screen treatment.
const SCENE_ROUTES = ['/login', '/signup', '/prueba']

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { theme, toggle } = useTheme()
  const { lang, setLang, t } = useLanguage()
  const showScene = SCENE_ROUTES.some(p => pathname === p || pathname.startsWith(p + '/'))

  if (!showScene) return <>{children}</>

  const dark = theme === 'dark'

  return (
    // position:fixed — ConditionalShell wraps auth routes in a flex box with no
    // width:100%, so an in-flow child would shrink to its content instead of
    // covering the viewport.
    <div className="auth-split">
      <div className="auth-split-form">
        <header className="auth-split-head">
          <Wordmark size={21} color="var(--a-ink)" accent="var(--a-accent)" />
          <div className="auth-split-tools">
            <button
              type="button" className="auth-tool"
              onClick={() => setLang(lang === 'es' ? 'en' : 'es')}
              aria-label={t('auth.switch_language')}
            >
              {lang === 'es' ? 'EN' : 'ES'}
            </button>
            <button
              type="button" className="auth-tool"
              onClick={toggle}
              aria-label={dark ? t('auth.theme_light') : t('auth.theme_dark')}
            >
              {dark ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          </div>
        </header>
        {/* Only this column scrolls: a form taller than a short phone screen
            must stay reachable (on a 640px phone the signup button once sat
            at y=792 with no way to scroll to it). */}
        <main className="auth-split-body">{children}</main>
        <footer className="auth-split-foot">© {new Date().getFullYear()} StockAI</footer>
      </div>
      {/* Signing in is for people who already have an account: they get tips
          for using it. Signup and the trial keep the pitch. */}
      {pathname === '/login' ? <TipsPanel /> : <AuthPanel />}
    </div>
  )
}

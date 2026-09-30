'use client'
import { usePathname } from 'next/navigation'
import { Wordmark } from '@/components/brand/Wordmark'
import { AuthPanel } from '@/components/auth/AuthPanel'

// Persistent stage for /login and /signup: the form on the left, the product's
// own morning list on the right. Because this layout wraps both routes, the
// panel stays MOUNTED across a /login ↔ /signup navigation and only the form
// changes.
//
// It replaced a lighthouse scene (the product used to be called Faro). A
// buyer opening the login should see what the app does for them, not a
// metaphor for a name it no longer has.
//
// Scoped to /login and /signup on purpose: verify-email, forgot-password and
// reset-password keep their own full-screen treatment.
const SCENE_ROUTES = ['/login', '/signup']

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const showScene = SCENE_ROUTES.some(p => pathname === p || pathname.startsWith(p + '/'))

  if (!showScene) return <>{children}</>

  return (
    // position:fixed — ConditionalShell wraps auth routes in a flex box with no
    // width:100%, so an in-flow child would shrink to its content instead of
    // covering the viewport.
    <div className="auth-split">
      <div className="auth-split-form">
        <header className="auth-split-mark">
          <Wordmark size={20} color="#0a0a0a" accent="#0F766E" />
        </header>
        {/* Only this column scrolls: a form taller than a short phone screen
            must stay reachable (on a 640px phone the signup button once sat
            at y=792 with no way to scroll to it). */}
        <main className="auth-split-body">{children}</main>
      </div>
      <AuthPanel />
    </div>
  )
}

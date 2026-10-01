'use client'
import { usePathname } from 'next/navigation'
import AppShell from './AppShell'
import { SUBPAGE_PATHS } from '@/components/landing/subpagePaths'

const AUTH_PATHS    = ['/login', '/signup', '/verify-email', '/forgot-password', '/reset-password', '/prueba']
// The landing and its public subpages: no app shell, no sign-in.
const LANDING_PATHS = ['/', ...Object.values(SUBPAGE_PATHS)]

export default function ConditionalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const isAuth    = AUTH_PATHS.some(p => pathname === p || pathname.startsWith(p + '/'))
  const isLanding = LANDING_PATHS.includes(pathname)

  if (isLanding) return <>{children}</>

  if (isAuth) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        // Follows the theme. This was a hardcoded near-black, so with light as
        // the default a user went from a light login straight into a black
        // reset-password screen. /login and /signup paint their own canvas
        // over this in (auth)/layout.tsx, so they are unaffected either way.
        background: 'var(--bg)',
      }}>
        {children}
      </div>
    )
  }

  return <AppShell>{children}</AppShell>
}

'use client'
import { usePathname } from 'next/navigation'
import AppShell from './AppShell'

const AUTH_PATHS    = ['/login', '/signup', '/verify-email', '/forgot-password', '/reset-password']
const LANDING_PATHS = ['/']

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
        background: '#08090d',
      }}>
        {children}
      </div>
    )
  }

  return <AppShell>{children}</AppShell>
}

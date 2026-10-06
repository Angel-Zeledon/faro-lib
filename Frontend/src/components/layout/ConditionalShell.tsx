'use client'
import { usePathname } from 'next/navigation'
import AppShell from './AppShell'
import { SUBPAGE_PATHS } from '@/components/landing/subpagePaths'
import { LEGAL_PUBLIC_PATHS } from '@/components/landing/legalPaths'
import { CONTENT_PUBLIC_PATHS } from '@/components/landing/contentPaths'
import StorageNotice from '@/components/legal/StorageNotice'
import { FeedbackProvider } from '@/components/feedback/FeedbackDialog'

const AUTH_PATHS    = ['/login', '/signup', '/verify-email', '/forgot-password', '/reset-password', '/prueba', '/auth/callback']
// The landing and its public subpages: no app shell, no sign-in.
// /desarrolladores: the API reference, a landing page with its own chrome.
// The legal documents are public too: they must open for somebody who has no
// account yet, and from inside the app without leaving it for a sign-in wall.
const LANDING_PATHS = ['/', ...Object.values(SUBPAGE_PATHS), ...LEGAL_PUBLIC_PATHS, ...CONTENT_PUBLIC_PATHS, '/desarrolladores']

export default function ConditionalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const isAuth    = AUTH_PATHS.some(p => pathname === p || pathname.startsWith(p + '/'))
  // The help center is a tree (/docs, /docs/<section>/<page>), so it is
  // matched by prefix rather than listed page by page.
  // A supplier answering a purchase order from the link in a message: no app
  // shell, no sign-in. The trailing slash matters — `/proveedores` is the
  // signed-in suppliers screen and must keep its shell.
  const isSupplierPortal = pathname.startsWith('/proveedor/') || pathname.startsWith('/cliente/') || pathname.startsWith('/aprobar/')
  // The unsubscribe page of a scheduled report: same, a link in an email.
  const isReportUnsubscribe = pathname.startsWith('/reportes-programados/baja')
  // (/aprobar/<token>, the approval decision page, is the same kind of page: a
  // link in a message, no shell, no sign-in.)
  const isLanding = LANDING_PATHS.includes(pathname) || pathname === '/docs' || pathname.startsWith('/docs/')
    || isSupplierPortal || isReportUnsubscribe

  // The one-time notice about browser storage rides on the landing and the
  // app shells; the sign-in screens keep it off their submit buttons (see
  // StorageNotice).
  // The feedback dialog wraps everything, the error screens included: it is how
  // an error anywhere in the app reaches us.
  return (
    <FeedbackProvider>
      <Shell isAuth={isAuth} isLanding={isLanding}>{children}</Shell>
      <StorageNotice suppress={isAuth || isSupplierPortal || isReportUnsubscribe} />
    </FeedbackProvider>
  )
}

function Shell({ isAuth, isLanding, children }: {
  isAuth: boolean; isLanding: boolean; children: React.ReactNode
}) {
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

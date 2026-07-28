'use client'
import { useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { isAuthenticated, tryRefresh } from '@/lib/auth'
import Spinner from '@/components/ui/Spinner'

const PUBLIC_PATHS = ['/login', '/signup', '/verify-email', '/forgot-password', '/reset-password']

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router   = useRouter()
  const pathname = usePathname()
  const [ready,  setReady] = useState(false)

  useEffect(() => {
    const isPublic = PUBLIC_PATHS.some(p => pathname.startsWith(p))
    if (isPublic) {
      setReady(true)
      return
    }
    if (!isAuthenticated()) {
      // Expired access token ≠ logged out: renew silently with the refresh
      // token before falling back to /login.
      let cancelled = false
      tryRefresh().then(ok => {
        if (cancelled) return
        if (ok) setReady(true)
        else router.replace('/login')
      })
      return () => { cancelled = true }
    }
    setReady(true)
  }, [pathname, router])

  if (!ready) {
    return (
      <div style={{
        height: '100vh', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        // Follows the theme: this is the very first paint of every guarded
        // route, so a hardcoded near-black here was a black flash before
        // every screen for a light-theme user.
        background: 'var(--bg)',
      }}>
        <Spinner size={20} />
      </div>
    )
  }

  return <>{children}</>
}

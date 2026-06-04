'use client'
import { useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { isAuthenticated } from '@/lib/auth'
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
      router.replace('/login')
      return
    }
    setReady(true)
  }, [pathname, router])

  if (!ready) {
    return (
      <div style={{
        height: '100vh', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: '#08090d',
      }}>
        <Spinner size={20} />
      </div>
    )
  }

  return <>{children}</>
}

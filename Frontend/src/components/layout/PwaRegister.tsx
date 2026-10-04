'use client'
import { useEffect, useRef } from 'react'
import { usePathname } from 'next/navigation'
import { registerServiceWorker, watchForNewVersion, applyNewVersion } from '@/lib/pwa'

/**
 * Mounted once by AppShell: registers the service worker and keeps the running
 * app on the latest deploy. A new build is applied while the tab is hidden, or
 * at the person's next navigation — never over a screen they are working on.
 */
export function PwaRegister() {
  const path = usePathname()
  const pending = useRef(false)
  const lastPath = useRef(path)

  useEffect(() => {
    registerServiceWorker()
    return watchForNewVersion(p => { pending.current = p })
  }, [])

  useEffect(() => {
    if (lastPath.current !== path) {
      lastPath.current = path
      if (pending.current) applyNewVersion(window.location.pathname + window.location.search)
    }
  }, [path])

  return null
}

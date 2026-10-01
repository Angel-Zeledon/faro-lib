'use client'
import { useEffect, useState } from 'react'

/**
 * Installing StockAI as an app (manifest: app/manifest.ts, worker: public/sw.js).
 *
 * Chrome, Edge and Android fire `beforeinstallprompt` once, early, and only to
 * whoever is listening at that moment — so the listener is attached when this
 * module first loads (AppShell mounts PwaRegister) and the event is kept here
 * until the user presses "Install". Safari on iPhone never fires it: there the
 * only way is Share → Add to Home Screen, which the button explains instead.
 */
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

let deferred: InstallPromptEvent | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach(fn => fn())

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()           // keep the browser's mini-infobar out; we offer our own button
    deferred = e as InstallPromptEvent
    notify()
  })
  window.addEventListener('appinstalled', () => { deferred = null; notify() })
}

function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(display-mode: standalone)').matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true
}

function isIos(): boolean {
  if (typeof navigator === 'undefined') return false
  return /iphone|ipad|ipod/i.test(navigator.userAgent)
}

export type InstallMode = 'prompt' | 'ios' | null

/** How this browser can install the app right now, or null when it cannot (or already is). */
export function useInstall(): { mode: InstallMode; install: () => Promise<boolean> } {
  const [, force] = useState(0)
  useEffect(() => {
    const fn = () => force(n => n + 1)
    listeners.add(fn)
    return () => { listeners.delete(fn) }
  }, [])

  let mode: InstallMode = null
  if (!isStandalone()) mode = deferred ? 'prompt' : isIos() ? 'ios' : null

  async function install(): Promise<boolean> {
    if (!deferred) return false
    const ev = deferred
    await ev.prompt()
    const { outcome } = await ev.userChoice
    deferred = null
    notify()
    return outcome === 'accepted'
  }

  return { mode, install }
}

/** Registers the service worker. Production only: in development it would
 *  cache the dev server's unhashed bundles and serve stale code. */
export function registerServiceWorker(): void {
  if (process.env.NODE_ENV !== 'production') return
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
    // Not installable then, and nothing else changes: the app works without it.
  })
}

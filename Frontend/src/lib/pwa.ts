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

// ── New version ───────────────────────────────────────────────────────────────
//
// An installed PWA (or a tab left open for days) keeps running the JavaScript
// it loaded, however many times production is redeployed. The server answers
// /build-id with the build it runs; when that differs from the build this page
// was loaded with, the page reloads — never in the middle of what the person is
// doing: while the tab is hidden, or at their next navigation.

const BUILD_ID = process.env.NEXT_PUBLIC_BUILD_ID
const CHECK_EVERY_MS = 5 * 60 * 1000
const RELOAD_GUARD_KEY = 'stockai_reloaded_for'

async function serverBuildId(): Promise<string | null> {
  try {
    const res = await fetch('/build-id', { cache: 'no-store' })
    if (!res.ok) return null
    const body = await res.json() as { id?: string | null }
    return body.id ?? null
  } catch {
    return null   // offline or mid-deploy: try again at the next check
  }
}

/** True when the server runs a different build than this page. */
export async function newVersionAvailable(): Promise<boolean> {
  if (!BUILD_ID) return false
  const live = await serverBuildId()
  return !!live && live !== BUILD_ID
}

/** Reload to pick the new build up. Not twice within two minutes, so a server
 *  that answers with an id the bundle can never match (a cached shell, a
 *  half-rolled deploy) cannot cause a reload loop. */
export function applyNewVersion(path?: string): void {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) ?? 0)
    if (Date.now() - last < 2 * 60 * 1000) return
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()))
  } catch { /* storage blocked: the 5-minute check interval is the only brake */ }
  navigator.serviceWorker?.getRegistration().then(reg => reg?.update()).catch(() => {})
  if (path) window.location.assign(path)
  else window.location.reload()
}

/**
 * Starts watching for a new deploy. `onPending` is told when one is waiting, so
 * the caller can apply it at the next navigation; the watcher itself applies it
 * as soon as the tab is hidden. Returns the cleanup.
 */
export function watchForNewVersion(onPending: (pending: boolean) => void): () => void {
  if (process.env.NODE_ENV !== 'production' || !BUILD_ID || typeof document === 'undefined') return () => {}
  let pending = false

  async function check() {
    if (pending) return
    if (await newVersionAvailable()) {
      pending = true
      onPending(true)
      if (document.hidden) applyNewVersion()
    }
  }

  const onVisibility = () => {
    if (document.hidden) { if (pending) applyNewVersion() }
    else void check()
  }
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('online', check)
  const timer = window.setInterval(check, CHECK_EVERY_MS)
  void check()
  return () => {
    document.removeEventListener('visibilitychange', onVisibility)
    window.removeEventListener('online', check)
    window.clearInterval(timer)
  }
}

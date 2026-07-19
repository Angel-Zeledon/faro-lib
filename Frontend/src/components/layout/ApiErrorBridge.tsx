'use client'
/**
 * Wires the api layer's error interceptor to the toast system (feature 2.6).
 *
 * `lib/api.ts` is not a React module, so it cannot reach `useToast` or the
 * translation dictionary. It raises a classified `ApiError` and calls the
 * notifier registered here; this component owns the translation and the toast.
 * Mounted once inside AppShell, below ToastProvider and LanguageProvider.
 */
import { useEffect, useRef } from 'react'
import { setApiErrorNotifier, type ApiError } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { useErrorCopy } from '@/components/ui/States'

// A screen that fires several requests at once (the daily dashboard pulls
// briefing + suppliers + overdue POs together) would otherwise stack four
// identical "the server failed" toasts. Collapse repeats of the same kind for
// a short window.
const DEDUPE_MS = 4000

export default function ApiErrorBridge() {
  const { addToast } = useToast()
  const errorCopy = useErrorCopy()
  const lastSeen = useRef<Map<string, number>>(new Map())

  // Kept in a ref so the effect can register the notifier once instead of
  // re-registering on every render of the provider tree.
  const handler = useRef<(err: ApiError) => void>(() => {})
  handler.current = (err: ApiError) => {
    const now = Date.now()
    const key = `${err.kind}:${err.status}`
    const previous = lastSeen.current.get(key)
    if (previous !== undefined && now - previous < DEDUPE_MS) return
    lastSeen.current.set(key, now)

    const { title, body, detail } = errorCopy(err)
    addToast(title, detail || body, 'error')
  }

  useEffect(() => setApiErrorNotifier(err => handler.current(err)), [])

  return null
}

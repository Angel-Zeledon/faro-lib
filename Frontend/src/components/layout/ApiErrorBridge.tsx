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
import { useUpgradePrompt } from '@/components/limits/UpgradeDialog'
import { useBugReport } from '@/lib/bugReport'
import { useLanguage } from '@/contexts/LanguageContext'

// A screen that fires several requests at once (the daily dashboard pulls
// briefing + suppliers + overdue POs together) would otherwise stack four
// identical "the server failed" toasts. Collapse repeats of the same kind for
// a short window.
const DEDUPE_MS = 4000

export default function ApiErrorBridge() {
  const { addToast } = useToast()
  const errorCopy = useErrorCopy()
  const openUpgrade = useUpgradePrompt()
  const reportBug = useBugReport()
  const { t } = useLanguage()
  const lastSeen = useRef<Map<string, number>>(new Map())

  // Kept in a ref so the effect can register the notifier once instead of
  // re-registering on every render of the provider tree.
  const handler = useRef<(err: ApiError) => void>(() => {})
  handler.current = (err: ApiError) => {
    // Running out of room is not a failure to report — it is the one moment
    // the product has something to say. A toast would announce the wall and
    // leave the user to find us; the dialog names what they hit and offers the
    // three ways to ask for more. It bypasses the dedupe map on purpose: this
    // never stacks, because it replaces rather than accumulates.
    if (err.code === 'PLAN_LIMIT_REACHED') {
      const limit = err.params?.limit
      openUpgrade(typeof limit === 'string' ? limit : null)
      return
    }
    const now = Date.now()
    const key = `${err.kind}:${err.status}`
    const previous = lastSeen.current.get(key)
    if (previous !== undefined && now - previous < DEDUPE_MS) return
    lastSeen.current.set(key, now)

    const { title, body, detail } = errorCopy(err)
    // A failure on our side gets a way to tell us, prefilled. One the user can
    // fix (a validation message, a limit, a lost connection) does not: the
    // toast already says what to do, and a report would be noise.
    const ours = err.kind === 'server'
    addToast(title, detail || body, 'error', ours ? {
      duration: 9000,
      action: {
        label: t('bugreport.action'), kind: 'report',
        onClick: () => reportBug({ code: err.code || `HTTP ${err.status}`, detail: detail || body }),
      },
    } : undefined)
  }

  useEffect(() => setApiErrorNotifier(err => handler.current(err)), [])

  return null
}

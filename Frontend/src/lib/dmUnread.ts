'use client'
import { useEffect, useState } from 'react'
import { getDmUnreadCount } from '@/lib/api'

/**
 * Unread direct-message count, shared by every indicator that shows it.
 *
 * The desktop top bar, the mobile overflow menu, the mobile "Más" tab and the
 * "Más" sheet all show the same number. Each polling on its own would mean up
 * to four requests every 30 s for one integer, and four numbers that can
 * disagree for a poll interval. So there is one poller, started by the first
 * subscriber and stopped when the last one unmounts.
 *
 * Silent by design: a failed poll keeps the last known value and never toasts.
 */
const POLL_MS = 30000

let unread = 0
let timer: ReturnType<typeof setInterval> | null = null
const subscribers = new Set<(n: number) => void>()

function poll() {
  getDmUnreadCount()
    .then(d => {
      unread = d.unread
      subscribers.forEach(fn => fn(unread))
    })
    .catch(() => { /* keep the last value; see header */ })
}

export function useDmUnread(): number {
  const [n, setN] = useState(unread)
  useEffect(() => {
    subscribers.add(setN)
    setN(unread)
    if (!timer) {
      poll()
      timer = setInterval(poll, POLL_MS)
    }
    return () => {
      subscribers.delete(setN)
      if (subscribers.size === 0 && timer) {
        clearInterval(timer)
        timer = null
      }
    }
  }, [])
  return n
}

/** Re-reads the count now instead of on the next tick — for a screen that
 *  just marked messages read. No-op while nothing is subscribed. */
export function refreshDmUnread(): void {
  if (subscribers.size > 0) poll()
}

/**
 * Client for the unsubscribe link at the foot of every scheduled report
 * (`/reportes-programados/baja?token=...`).
 *
 * Like the supplier portal client, deliberately NOT built on `request()`: the
 * person has no session and must never be sent to a login screen, and the link
 * is the credential (never cached, never sent as a referrer).
 *
 * Opening the page only DESCRIBES the link (GET); nothing changes until the
 * person presses the button (POST), so a mail scanner that prefetches links
 * cannot unsubscribe anyone.
 */
export type UnsubscribeFailure = 'invalid' | 'network' | 'server'

export class UnsubscribeError extends Error {
  readonly kind: UnsubscribeFailure
  constructor(kind: UnsubscribeFailure) {
    super(kind)
    this.name = 'UnsubscribeError'
    this.kind = kind
  }
}

async function call<T>(init: RequestInit, query = ''): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/public/report-unsubscribe${query}`, {
      ...init, cache: 'no-store', referrerPolicy: 'no-referrer', credentials: 'omit',
    })
  } catch {
    throw new UnsubscribeError('network')
  }
  let body: { data?: T } | null = null
  try { body = await res.json() } catch { /* empty or non-JSON */ }
  if (!res.ok) throw new UnsubscribeError(res.status === 400 || res.status === 422 ? 'invalid' : 'server')
  return body?.data as T
}

export const describeUnsubscribeLink = (token: string) =>
  call<{ valid: boolean; schedule_name: string | null; already_unsubscribed: boolean }>(
    { method: 'GET' }, `?token=${encodeURIComponent(token)}`)

export const confirmUnsubscribe = (token: string) =>
  call<{ unsubscribed: boolean; already: boolean; schedule_name: string | null }>(
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })

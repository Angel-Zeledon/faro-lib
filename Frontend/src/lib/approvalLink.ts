/**
 * Client for the approval decision page (`/aprobar/<token>`).
 *
 * Deliberately NOT built on `request()` in api.ts: that one attaches the signed-in
 * user's Bearer token, redirects to /login on a 401 and raises the app's global
 * error toast. An approver opening a link from an email may not be signed in, must
 * never be sent to a login screen, and the page owns every message it shows.
 *
 * Every failure is reduced to a small `kind`, because the backend deliberately
 * answers one identical 404 for any bad link (used, expired, revoked, unknown):
 * the page must not try to tell them apart, and cannot.
 */

export interface ApprovalLinkLine {
  sku: string
  name: string
  quantity: number
  supplier: string | null
}

export interface ApprovalLinkCurrency {
  code: string
  symbol: string
  locale: string
  decimals: number
}

/** What the page may show: the order's total and its lines, no unit costs. */
export interface ApprovalLinkView {
  reference: string
  buyer: string | null
  amount: number
  currency: ApprovalLinkCurrency
  requested_by_name: string | null
  requested_at: string | null
  note: string | null
  warehouse: string | null
  suppliers: string[]
  line_count: number
  lines: ApprovalLinkLine[]
  can_approve: boolean
  can_reject: boolean
  approver_name: string | null
  expires_at: string | null
  comment_max: number
}

export type ApprovalLinkFailureKind =
  | 'invalid'    // 404: not valid, used, expired or revoked: indistinguishable on purpose
  | 'busy'       // 429: too many requests from this connection
  | 'too_large'  // 413
  | 'rejected'   // 403 / 409 / 422: the decision itself was refused (see `code`)
  | 'network'    // the request never completed
  | 'server'

export class ApprovalLinkError extends Error {
  readonly kind: ApprovalLinkFailureKind
  readonly code: string
  constructor(kind: ApprovalLinkFailureKind, code = '') {
    super(kind)
    this.name = 'ApprovalLinkError'
    this.kind = kind
    this.code = code
  }
}

function kindFor(status: number): ApprovalLinkFailureKind {
  if (status === 404) return 'invalid'
  if (status === 429) return 'busy'
  if (status === 413) return 'too_large'
  if (status === 403 || status === 409 || status === 422) return 'rejected'
  return 'server'
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/approval-links/${path}`, {
      ...init,
      // The link is the credential: never cached, never sent as a referrer.
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      credentials: 'omit',
    })
  } catch {
    throw new ApprovalLinkError('network')
  }
  let body: unknown = null
  try { body = await res.json() } catch { /* an empty or non-JSON body */ }
  if (!res.ok) {
    const b = (body ?? {}) as { error_code?: unknown }
    throw new ApprovalLinkError(kindFor(res.status), typeof b.error_code === 'string' ? b.error_code : '')
  }
  return ((body as { data: T } | null)?.data) as T
}

/** GET only reads: a prefetch of this URL decides nothing. */
export const fetchApprovalLink = (token: string) =>
  call<ApprovalLinkView>(encodeURIComponent(token))

/** Deciding is always an explicit POST, made from a button on the page. */
export const decideApprovalLink = (token: string, decision: 'approved' | 'rejected', comment: string) =>
  call<{ decided: boolean; decision: 'approved' | 'rejected'; reference: string }>(
    `${encodeURIComponent(token)}/decision`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, comment: comment.trim() || null }),
    },
  )

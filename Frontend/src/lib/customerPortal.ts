/**
 * Client for the customer's page (`/cliente/<token>`).
 *
 * Deliberately NOT built on `request()` in api.ts, for the same reasons as
 * supplierPortal.ts: no Bearer token, no redirect to /login on a 401, no global
 * error toast. A customer has no account and the page owns every message.
 *
 * Every failure is reduced to a small `kind`: the backend answers one identical
 * 404 for any bad link, so the page cannot (and must not) tell a mistyped link
 * from an expired or revoked one.
 */
import type { CustomerPortalAnswer, CustomerPortalPublicView } from './types'

export type CustomerPortalFailure =
  | 'invalid'    // 404 on the link: unknown, expired or revoked, indistinguishable on purpose
  | 'closed'     // 409: the commitment is already closed
  | 'busy'       // 429
  | 'too_large'  // 413
  | 'rejected'   // 422 (or a 404 on the commitment): the answer itself was refused
  | 'network'
  | 'server'

export class CustomerPortalError extends Error {
  readonly kind: CustomerPortalFailure
  readonly code: string
  constructor(kind: CustomerPortalFailure, code = '') {
    super(kind)
    this.name = 'CustomerPortalError'
    this.kind = kind
    this.code = code
  }
}

function kindFor(status: number, code: string): CustomerPortalFailure {
  if (status === 404) return code === 'customer_portal_commitment_not_found' ? 'rejected' : 'invalid'
  if (status === 409) return 'closed'
  if (status === 429) return 'busy'
  if (status === 413) return 'too_large'
  if (status === 422) return 'rejected'
  return 'server'
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/customer-portal/public/${path}`, {
      ...init,
      // The link is the credential: never cached, never sent as a referrer.
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      credentials: 'omit',
    })
  } catch {
    throw new CustomerPortalError('network')
  }
  let body: unknown = null
  try { body = await res.json() } catch { /* an empty or non-JSON body */ }
  if (!res.ok) {
    const code = (body as { error_code?: unknown } | null)?.error_code
    const c = typeof code === 'string' ? code : ''
    throw new CustomerPortalError(kindFor(res.status, c), c)
  }
  return ((body as { data: T } | null)?.data) as T
}

export const fetchCustomerPortal = (token: string) =>
  call<CustomerPortalPublicView>(encodeURIComponent(token))

export const answerCustomerPortal = (
  token: string, commitmentId: string, response: CustomerPortalAnswer, comment?: string,
) =>
  call<{ recorded: boolean; duplicate: boolean; response: CustomerPortalAnswer }>(
    `${encodeURIComponent(token)}/respond`,
    {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ commitment_id: commitmentId, response, ...(comment ? { comment } : {}) }),
    },
  )

/**
 * Client for the supplier confirmation page (`/proveedor/<token>`).
 *
 * Deliberately NOT built on `request()` in api.ts: that one attaches the signed-in
 * user's Bearer token, redirects to /login on a 401 and raises the app's global
 * error toast. A supplier has no account, must never be sent to a login screen,
 * and the page owns every message it shows.
 *
 * Every failure is reduced to a small `kind`, because the backend deliberately
 * answers one identical 404 for any bad link — the page must not try to tell a
 * mistyped link from an expired one, and cannot.
 */
import type { SupplierPortalAnswer, SupplierPortalView } from './types'

export type PortalFailureKind =
  | 'invalid'    // 404: not valid, expired, revoked or cancelled — indistinguishable on purpose
  | 'locked'     // 409: already answered; the buyer must reopen it
  | 'busy'       // 429: too many requests from this connection
  | 'too_large'  // 413
  | 'rejected'   // 422: the answer itself was refused (a field is wrong)
  | 'network'    // the request never completed
  | 'server'

export class PortalError extends Error {
  readonly kind: PortalFailureKind
  readonly code: string
  readonly params: Record<string, unknown>
  constructor(kind: PortalFailureKind, code = '', params: Record<string, unknown> = {}) {
    super(kind)
    this.name = 'PortalError'
    this.kind = kind
    this.code = code
    this.params = params
  }
}

function kindFor(status: number): PortalFailureKind {
  if (status === 404) return 'invalid'
  if (status === 409) return 'locked'
  if (status === 429) return 'busy'
  if (status === 413) return 'too_large'
  if (status === 422) return 'rejected'
  return 'server'
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/supplier-portal/${path}`, {
      ...init,
      // The link is the credential: never cached, never sent as a referrer.
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      credentials: 'omit',
    })
  } catch {
    throw new PortalError('network')
  }
  let body: unknown = null
  try { body = await res.json() } catch { /* an empty or non-JSON body */ }
  if (!res.ok) {
    const b = (body ?? {}) as { error_code?: unknown; error_params?: unknown }
    throw new PortalError(
      kindFor(res.status),
      typeof b.error_code === 'string' ? b.error_code : '',
      b.error_params && typeof b.error_params === 'object'
        ? (b.error_params as Record<string, unknown>) : {},
    )
  }
  return ((body as { data: T } | null)?.data) as T
}

export const fetchSupplierPortal = (token: string) =>
  call<SupplierPortalView>(encodeURIComponent(token))

export const submitSupplierPortal = (token: string, lines: SupplierPortalAnswer[]) =>
  call<{ submitted: boolean; counts: Record<'confirmed' | 'changed' | 'declined', number> }>(
    `${encodeURIComponent(token)}/confirm`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lines }) },
  )

const TOKEN_KEY   = 'fp_access_token'
const REFRESH_KEY = 'fp_refresh_token'
const USER_KEY    = 'fp_user'
// Per tab (sessionStorage): the fingerprint of a shared access token the server
// refused while this tab had no way to renew it. See `endSession`.
const REJECTED_KEY = 'fp_rejected_token'
// Same-origin bus a tab WITHOUT a refresh token uses to ask a tab that has one
// to renew the shared access token. The refresh token itself never travels.
const CHANNEL_NAME = 'fp_auth'
// Web Locks name: two tabs that both hold a refresh token answer one request
// with one network call, not two.
const LOCK_NAME = 'fp_auth_refresh'
// How long a tab without a refresh token waits for another tab to renew, and
// the short grace after which it re-reads the shared token once more before
// giving up (a throttled background tab may answer late).
const PEER_WAIT_MS  = 4000
const PEER_GRACE_MS = 600

export interface AuthUser {
  id:        string
  email:     string
  full_name: string | null
  role:      string
  tenant_id: string
}

export function getToken():   string | null { return localStorage.getItem(TOKEN_KEY) }
// Refresh token lives in sessionStorage — cleared when the browser tab closes,
// not accessible to scripts in other tabs, and doesn't persist across sessions.
export function getRefresh(): string | null { return sessionStorage.getItem(REFRESH_KEY) }
export function getUser():    AuthUser | null {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') }
  catch { return null }
}

export function setAuth(accessToken: string, refreshToken: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY,  accessToken)
  sessionStorage.setItem(REFRESH_KEY, refreshToken)
  sessionStorage.removeItem(REJECTED_KEY)
  localStorage.setItem(USER_KEY,   JSON.stringify(user))
}

/**
 * Update the cached identity after the server accepted a change to it.
 *
 * `getUser()` re-parses localStorage on every call, so it hands back a FRESH
 * object each time. /mi-cuenta saved a new display name, then did
 * `if (me) me.full_name = name` — mutating a throwaway parse — and showed a
 * green "Guardado" next to the OLD name. `fp_user` is written in exactly one
 * other place (`setAuth`, called only from the login screen), so the stale
 * name survived reloads, the sidebar footer and the /compras greeting until
 * the user logged out and back in. The save had worked; nothing on screen
 * ever admitted it.
 */
export function patchUser(patch: Partial<AuthUser>): AuthUser | null {
  const current = getUser()
  if (!current) return null
  const next = { ...current, ...patch }
  try { localStorage.setItem(USER_KEY, JSON.stringify(next)) } catch { /* quota/private mode */ }
  return next
}

// Bumped by clearAuth. A refresh that was already in flight when the session
// ended captured its refresh token beforehand, so it used to finish and write
// the renewed access token back — resurrecting the session AFTER logout. That
// is how a password reset landed the next person inside the previous user's
// admin workspace: the tokens were cleared, then a pending refresh restored
// them, and /login waved the "still authenticated" visitor into the app.
let _authEpoch = 0

export function clearAuth(): void {
  _authEpoch++
  localStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(REFRESH_KEY)
  sessionStorage.removeItem(REJECTED_KEY)
  localStorage.removeItem(USER_KEY)
}

// The access token is shared by every tab on this origin; the REFRESH token is
// per-tab (sessionStorage). So ending the session in one tab used to be undone
// by any other tab still open: its next request 401'd on the missing token, it
// refreshed with its own copy, and wrote a working token back for everybody.
// A warehouse PC with two tabs open had no way to actually log out. When
// another tab clears the token, this tab must surrender its refresh token too.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e: StorageEvent) => {
    if (e.storageArea !== window.localStorage) return
    // Only the removal matters. A fresh login also fires here (newValue set),
    // and that must not tear the new session down.
    if (e.key === TOKEN_KEY && e.newValue === null) {
      _authEpoch++
      sessionStorage.removeItem(REFRESH_KEY)
    }
  })
}

// Only the tail of the signature: enough to tell two tokens apart, and the
// token itself never needs to be copied anywhere else.
function fingerprint(token: string | null): string | null {
  return token ? token.slice(-16) : null
}

// Clock check only, with a 30s buffer: tokens about to expire fail in-flight requests.
function tokenUsable(token: string | null): boolean {
  if (!token) return false
  try {
    // Decode JWT payload (no signature check — backend validates on every request)
    const payload = JSON.parse(atob(token.split('.')[1]))
    if (!payload.sub || !payload.tenant_id || !payload.exp) return false
    return payload.exp > (Date.now() / 1000) + 30
  } catch {
    return false
  }
}

export function isAuthenticated(): boolean {
  const token = getToken()
  if (!tokenUsable(token)) return false
  // The server already refused this exact token while this tab could not
  // renew it: it must not wave /login straight back into the app (a loop).
  return sessionStorage.getItem(REJECTED_KEY) !== fingerprint(token)
}

// ── Silent session renewal ────────────────────────────────────────────────────
// Access tokens live 15 min; the refresh token (sessionStorage, this tab only)
// lets us renew without kicking the user back to /login mid-task.
// Single-flight: concurrent 401s share one refresh request.
//
// A tab that did not do the login itself (a link opened in a new tab, a
// duplicated tab, window.open) shares the access token but has NO refresh
// token. When the access token expired, that tab used to give up and its
// clearAuth() logged EVERY tab out — including one in the middle of a long
// training run or an unsaved form. Such a tab now asks the tabs that hold a
// refresh token to renew for it over a BroadcastChannel; the refresh token
// never leaves the tab that owns it.
let _refreshing: Promise<boolean> | null = null

type AuthMessage =
  | { type: 'refresh_request'; stale: string | null }
  | { type: 'refreshed' }

const _refreshedWaiters = new Set<() => void>()

function openChannel(): BroadcastChannel | null {
  if (typeof window === 'undefined' || typeof BroadcastChannel === 'undefined') return null
  try {
    const ch = new BroadcastChannel(CHANNEL_NAME)
    ch.addEventListener('message', (e: MessageEvent) => onPeerMessage(e.data as AuthMessage))
    return ch
  } catch {
    return null
  }
}

// Opened at load, not lazily: a tab holding a refresh token must already be
// listening when another tab asks.
const _channel: BroadcastChannel | null = openChannel()

function post(msg: AuthMessage): void {
  try { _channel?.postMessage(msg) } catch { /* channel closed */ }
}

function onPeerMessage(msg: AuthMessage): void {
  if (!msg || typeof msg !== 'object') return
  if (msg.type === 'refreshed') {
    Array.from(_refreshedWaiters).forEach(w => w())
  } else if (msg.type === 'refresh_request') {
    answerPeer(msg.stale)
  }
}

function answerPeer(stale: string | null): void {
  // No refresh token (never had one, or surrendered it when another tab logged
  // out): nothing to answer with. Staying silent lets the asker time out.
  if (!getRefresh()) return
  const run = async (): Promise<void> => {
    if (!getRefresh()) return
    // Already renewed (by this tab a moment ago, or by another answerer that
    // held the lock first): say so without another network call.
    const current = getToken()
    if (fingerprint(current) !== stale && tokenUsable(current)) {
      post({ type: 'refreshed' })
      return
    }
    // tryRefresh broadcasts 'refreshed' itself on success.
    await tryRefresh()
  }
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  if (locks && typeof locks.request === 'function') {
    locks.request(LOCK_NAME, run).catch(() => { /* lock manager unavailable */ })
  } else {
    void run()
  }
}

async function refreshWithOwnToken(refresh: string): Promise<boolean> {
  const epoch = _authEpoch
  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    })
    if (!res.ok) return false
    const json = await res.json().catch(() => null)
    const token: string | undefined = json?.data?.access_token
    if (!token) return false
    // The session ended while this was in flight. Writing the token now would
    // undo the logout, so drop the renewal on the floor.
    if (epoch !== _authEpoch) return false
    localStorage.setItem(TOKEN_KEY, token)
    post({ type: 'refreshed' })
    return true
  } catch {
    return false
  }
}

function askPeers(staleToken: string | null): Promise<boolean> {
  const epoch = _authEpoch
  const stale = fingerprint(staleToken)
  const renewed = (): boolean => {
    const t = getToken()
    return fingerprint(t) !== stale && tokenUsable(t)
  }
  return new Promise<boolean>(resolve => {
    let settled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const settle = (): void => {
      if (settled) return
      settled = true
      if (timer !== undefined) clearTimeout(timer)
      _refreshedWaiters.delete(onRefreshed)
      window.removeEventListener('storage', onStorage)
      // A logout anywhere while we waited bumps the epoch: stay logged out.
      resolve(epoch === _authEpoch && renewed())
    }
    const onRefreshed = (): void => { if (renewed()) settle() }
    const onStorage = (e: StorageEvent): void => {
      if (e.storageArea !== window.localStorage || e.key !== TOKEN_KEY) return
      if (e.newValue === null || renewed()) settle()
    }
    _refreshedWaiters.add(onRefreshed)
    window.addEventListener('storage', onStorage)
    timer = setTimeout(() => { timer = setTimeout(settle, PEER_GRACE_MS) }, PEER_WAIT_MS)
    post({ type: 'refresh_request', stale })
  })
}

/**
 * Renew the shared access token. `rejectedToken` is the token the caller just
 * got a 401 with: if another tab already replaced it with a usable one, there
 * is nothing to renew and the caller can simply retry.
 */
export function tryRefresh(rejectedToken?: string | null): Promise<boolean> {
  if (_refreshing) return _refreshing
  const current = getToken()
  if (rejectedToken !== undefined && current !== rejectedToken && tokenUsable(current)) {
    return Promise.resolve(true)
  }
  const refresh = getRefresh()
  let attempt: Promise<boolean>
  if (refresh) attempt = refreshWithOwnToken(refresh)
  else if (_channel) attempt = askPeers(rejectedToken !== undefined ? rejectedToken : current)
  else return Promise.resolve(false)   // no BroadcastChannel: nobody to ask

  _refreshing = attempt.finally(() => { _refreshing = null })
  return _refreshing
}

/**
 * The session could not be renewed from this tab. Returns nothing; the caller
 * redirects to /login.
 *
 * A tab that held the refresh token (the server refused it) or that has no way
 * to ask other tabs ends the session for every tab, as it always did. A tab
 * WITHOUT a refresh token must not: another tab may still hold one and renew
 * the shared token (it may just be throttled in the background), and wiping
 * localStorage would log that tab out mid-task. It only records, for itself,
 * that this exact token was refused, so /login does not bounce it back in.
 */
export function endSession(rejectedToken: string | null): void {
  if (getRefresh() || !_channel) {
    clearAuth()
    return
  }
  const fp = fingerprint(rejectedToken)
  if (fp) sessionStorage.setItem(REJECTED_KEY, fp)
}

const TOKEN_KEY   = 'fp_access_token'
const REFRESH_KEY = 'fp_refresh_token'
const USER_KEY    = 'fp_user'

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

export function isAuthenticated(): boolean {
  const token = getToken()
  if (!token) return false
  try {
    // Decode JWT payload (no signature check — backend validates on every request)
    const payload = JSON.parse(atob(token.split('.')[1]))
    if (!payload.sub || !payload.tenant_id || !payload.exp) return false
    // 30s buffer: tokens about to expire fail in-flight requests
    return payload.exp > (Date.now() / 1000) + 30
  } catch {
    return false
  }
}

// ── Silent session renewal ────────────────────────────────────────────────────
// Access tokens live 15 min; the refresh token (sessionStorage, this tab only)
// lets us renew without kicking the user back to /login mid-task.
// Single-flight: concurrent 401s share one refresh request.
let _refreshing: Promise<boolean> | null = null

export function tryRefresh(): Promise<boolean> {
  if (_refreshing) return _refreshing
  const refresh = getRefresh()
  if (!refresh) return Promise.resolve(false)

  const epoch = _authEpoch
  _refreshing = (async () => {
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
      return true
    } catch {
      return false
    } finally {
      _refreshing = null
    }
  })()
  return _refreshing
}

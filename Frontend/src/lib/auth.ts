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

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
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

import type { UserPreferences } from './types'

/**
 * Write a display preference (language, theme) to the signed-in account.
 *
 * The UI contexts own the immediate, local effect — that must not wait on a
 * round-trip. This is the other half: without it the choice lived only in
 * `localStorage`, and the next screen that called `getPreferences()` (that is,
 * /mi-cuenta, on mount) overwrote it with the server's untouched value and
 * flipped the app back mid-session.
 *
 * Imported lazily so the contexts do not pull the API client — and therefore
 * the auth layer — into every page that only needs to render text.
 *
 * Deliberately best-effort and silent: the user's choice HAS taken effect
 * locally, and a toast on every language toggle would be noise. It is not a
 * silent failure in the sense that matters here — nothing the user asked for
 * was reported as done when it was not; only the cross-device copy is missing,
 * and the next successful toggle fixes it. The console line is there so the
 * case is observable when someone goes looking.
 */
export async function persistPreference(
  patch: Partial<Pick<UserPreferences, 'language' | 'theme'>>,
): Promise<void> {
  try {
    const { getToken } = await import('./auth')
    // Signed-out screens (landing, login) render inside the same providers.
    if (!getToken()) return
    const { updatePreferences } = await import('./api')
    await updatePreferences(patch)
  } catch (e) {
    console.warn('[preferences] could not be saved to the account', e)
  }
}

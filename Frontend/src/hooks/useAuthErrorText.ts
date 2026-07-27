'use client'
import { isApiError } from '@/lib/api'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * Error copy for the unauthenticated screens (/login, /signup, /forgot-password,
 * /reset-password, /verify-email).
 *
 * These endpoints answer with a stable `error_code` (`invalid_credentials`,
 * `reset_code_invalid`, …) which `useErrorDetail` turns into the localized
 * `errors.<code>` sentence, and a 422 still rebuilds from Pydantic's field
 * errors. What is different here is the LAST resort: `ApiError.detail` is the
 * backend's English prose, and on a login form that English is the whole
 * message the user gets. So when there is neither a code nor a field error,
 * these screens show their own localized fallback rather than the raw detail.
 *
 * @param fallbackKey i18n key describing what failed, in this screen's terms.
 */
export function useAuthErrorText() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  return (err: unknown, fallbackKey: string): string => {
    if (isApiError(err) && !err.code && err.fieldErrors.length === 0) return t(fallbackKey)
    return errorDetail(err) || t(fallbackKey)
  }
}

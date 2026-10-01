'use client'
import { useCallback } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { getUser } from '@/lib/auth'

/**
 * "Report this error": opens the person's own mail client on a message to us,
 * already addressed, with a subject and a template — what they were doing on
 * top, the technical facts we need underneath.
 *
 * A mailto rather than a form on our server on purpose: the moment somebody
 * needs to report a failure is exactly when our server may be the thing that
 * failed, and a mail client does not depend on it.
 */
export const SUPPORT_EMAIL = 'contacto@stockai.es'

export interface BugReportFacts {
  /** A stable error code, an HTTP status, or a React error digest. */
  code?: string
  /** What the screen showed, already translated. */
  detail?: string
}

export function bugReportHref(subject: string, body: string): string {
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}

/** Builds the mail and opens it. Usable from any client component. */
export function useBugReport() {
  const { t } = useLanguage()
  return useCallback((facts: BugReportFacts = {}) => {
    const user = getUser()
    const technical = [
      `${t('bugreport.field_page')}: ${window.location.href}`,
      facts.code && `${t('bugreport.field_code')}: ${facts.code}`,
      facts.detail && `${t('bugreport.field_detail')}: ${facts.detail}`,
      `${t('bugreport.field_when')}: ${new Date().toISOString()}`,
      user?.email && `${t('bugreport.field_account')}: ${user.email}`,
      `${t('bugreport.field_browser')}: ${navigator.userAgent}`,
    ].filter(Boolean)
    const lines = [
      t('bugreport.body_greeting'),
      '',
      t('bugreport.body_what'),
      '',
      '',
      `— ${t('bugreport.body_technical')} —`,
      ...technical,
    ]
    const subject = facts.code
      ? t('bugreport.subject_code', { code: facts.code })
      : t('bugreport.subject')
    window.location.href = bugReportHref(subject, lines.join('\n'))
  }, [t])
}

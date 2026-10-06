import { getUser } from '@/lib/auth'

/**
 * The signed-out fallback of "Send feedback": the person's own mail client opens
 * on a message to us, already addressed, with what they were doing on top and
 * the technical facts underneath.
 *
 * Signed in, the feedback dialog (`components/feedback/`) is used instead; it
 * needs a session to send with. This stays for the one case with no session —
 * an error on the landing or a sign-in screen — where a mail client is also the
 * one channel that does not depend on our server being the thing that failed.
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

type Translate = (key: string, params?: Record<string, unknown>) => string

/** Builds the mail and opens it. */
export function mailFallback(t: Translate, facts: BugReportFacts = {}): void {
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
}

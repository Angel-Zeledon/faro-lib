'use client'
import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { Monitor, X, ArrowRight } from 'lucide-react'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * "Esta vista está pensada para computadora."
 *
 * Only `/hoy` has a screen built for a phone. Every other page is a dense
 * table laid out with fixed inline widths, and on a 390px viewport it does not
 * degrade — it breaks. Saying so costs one line and is the honest move; the
 * alternative is letting someone in a warehouse wrestle a table that was never
 * going to work, and conclude the product is broken.
 *
 * It names the one screen that DOES work, so the notice is a way out rather
 * than an apology, and it is dismissible for the session — a warning that
 * cannot be silenced becomes furniture people stop reading.
 */

/** Routes with a real narrow-screen implementation. */
const MOBILE_READY = ['/hoy']

const DISMISS_KEY = 'fp_mobile_notice_dismissed'

export default function DesktopOnlyNotice() {
  const narrow = useIsNarrow()
  const path   = usePathname()
  const { t }  = useLanguage()
  const [dismissed, setDismissed] = useState(true)

  // Read after mount, same reason as `useIsNarrow`: sessionStorage does not
  // exist while the page is being rendered on the server.
  useEffect(() => {
    setDismissed(sessionStorage.getItem(DISMISS_KEY) === '1')
  }, [])

  if (!narrow || dismissed) return null
  if (MOBILE_READY.some(p => path === p || path.startsWith(`${p}/`))) return null

  function dismiss() {
    sessionStorage.setItem(DISMISS_KEY, '1')
    setDismissed(true)
  }

  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        margin: '0 0 14px', padding: '11px 12px', borderRadius: 10,
        background: 'var(--surface-2)', border: '1px dashed var(--border)',
      }}
    >
      <Monitor size={16} color="var(--muted)" style={{ flexShrink: 0, marginTop: 2 }} aria-hidden="true" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--text)', lineHeight: 1.5 }}>
          {t('mobile.desktop_only_notice') === 'mobile.desktop_only_notice'
            ? 'This screen is designed for a computer. On a phone the table will not fit.'
            : t('mobile.desktop_only_notice')}
        </div>
        <Link
          href="/hoy"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 6,
            fontSize: 12, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none',
          }}
        >
          {t('mobile.desktop_only_cta') === 'mobile.desktop_only_cta'
            ? 'Go to what to order today'
            : t('mobile.desktop_only_cta')}
          <ArrowRight size={12} aria-hidden="true" />
        </Link>
      </div>
      <button
        onClick={dismiss}
        aria-label={t('common.close')}
        style={{
          all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
          width: 32, height: 32, flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--dim)',
        }}
      >
        <X size={15} aria-hidden="true" />
      </button>
    </div>
  )
}

'use client'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useLanguage } from '@/contexts/LanguageContext'
import { noticePending, recordNoticeSeen } from '@/lib/consent'
import { LEGAL_PATHS } from '@/components/landing/legalPaths'

/**
 * The first-visit notice about browser storage: one sentence, a link to
 * /cookies, and a way to close it. Remembered in localStorage
 * (`lib/consent.ts`), so it appears once per browser.
 *
 * It informs; it does not ask. Everything StockAI stores is necessary or
 * functional, which needs no consent, so there is no "accept all" here —
 * `lib/consent.ts` explains what changes if that ever stops being true.
 *
 * Placement: bottom-left card on a wide screen, a strip with a margin on a
 * phone. Inside the app on a phone the bottom tab bar owns the last 56px, so
 * the notice sits above it rather than over the navigation.
 *
 * `suppress` keeps it off the sign-in and sign-up screens: there it covered
 * the submit button on a phone (measured at 360px), and /signup and /prueba
 * already link the privacy policy beside the button. It shows on the next
 * screen instead — still before anything optional could ever be stored.
 */
export default function StorageNotice({ suppress = false }: { suppress?: boolean }) {
  const { t } = useLanguage()
  const pathname = usePathname()
  // Hidden until mounted: the server cannot read localStorage, and rendering
  // it first and removing it after would flash it at everyone who closed it.
  const [open, setOpen] = useState(false)
  const [lift, setLift] = useState(0)

  useEffect(() => {
    if (noticePending()) setOpen(true)
  }, [])

  useEffect(() => {
    if (!open || suppress) return
    const measure = () => {
      const bar = document.querySelector('[data-mobile-tabbar]')
      setLift(bar ? bar.getBoundingClientRect().height : 0)
    }
    measure()
    window.addEventListener('resize', measure)
    // The tab bar mounts with the app shell, possibly after this does (a
    // navigation from the landing into the app keeps this component mounted).
    const ids = [150, 600, 1500].map(ms => window.setTimeout(measure, ms))
    return () => { window.removeEventListener('resize', measure); ids.forEach(window.clearTimeout) }
  }, [open, suppress, pathname])

  if (!open || suppress) return null

  function close() {
    recordNoticeSeen()
    setOpen(false)
  }

  return (
    <div
      role="region"
      aria-label={t('legal.notice_label')}
      className="storage-notice"
      style={{ bottom: `calc(${lift}px + 16px)` }}
    >
      <style dangerouslySetInnerHTML={{ __html: NOTICE_CSS }} />
      <p>
        {t('legal.notice_text')}{' '}
        <Link href={LEGAL_PATHS.cookies} onClick={close}>{t('legal.notice_more')}</Link>
      </p>
      <button type="button" onClick={close} className="storage-notice-ok">
        {t('legal.notice_ok')}
      </button>
    </div>
  )
}

const NOTICE_CSS = `
.storage-notice {
 position: fixed; left: 16px; z-index: 70; box-sizing: border-box;
 width: min(400px, calc(100vw - 32px));
 display: flex; align-items: center; gap: 14px;
 padding: 10px 10px 10px 16px; border-radius: 14px;
 background: var(--surface); color: var(--text); border: 1px solid var(--border);
 box-shadow: 0 12px 32px -12px rgba(9, 30, 33, 0.35);
 animation: storage-notice-in 320ms cubic-bezier(0.16, 1, 0.3, 1) both;
}
.storage-notice p { flex: 1; min-width: 0; margin: 0; font-size: 13.5px; line-height: 1.5; color: var(--text); }
.storage-notice a { color: var(--accent); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; white-space: nowrap; }
.storage-notice-ok {
 flex-shrink: 0; cursor: pointer; font: inherit; font-size: 13px; font-weight: 600;
 padding: 0 14px; min-height: 36px; border-radius: 9px; border: 1px solid var(--border);
 background: transparent; color: var(--text);
}
.storage-notice-ok:hover { border-color: var(--accent); color: var(--accent); }
.storage-notice button:focus-visible, .storage-notice a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 760px) {
 .storage-notice { left: 12px; width: calc(100vw - 24px); }
 .storage-notice-ok { min-height: 44px; }
}
@keyframes storage-notice-in { from { opacity: 0; transform: translateY(8px) } to { opacity: 1; transform: none } }
@media (prefers-reduced-motion: reduce) { .storage-notice { animation: none; } }
`

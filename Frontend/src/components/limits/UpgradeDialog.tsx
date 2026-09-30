'use client'
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
} from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useEntitlements } from '@/lib/entitlements'
import { requestUpgrade } from '@/lib/api'
import Button from '@/components/ui/Button'

/**
 * The one commercial surface in StockAI.
 *
 * There is no checkout and no feature gate: both tiers ship every screen. When
 * a free tenant runs out of room, this is what they get — what they hit, the
 * fact that nothing is locked, and three ways to reach us. It is opened from
 * two places: automatically by `ApiErrorBridge` when the backend answers
 * PLAN_LIMIT_REACHED, and by hand from the usage panel in Mi cuenta.
 *
 * The in-app form is deliberately last of the three. WhatsApp and email put the
 * customer in a conversation immediately; the form is for the person who does
 * not want to leave the page, and it still ends up as a row we read by hand.
 */

type OpenFn = (limitKey?: string | null) => void

const UpgradeContext = createContext<OpenFn>(() => {})

export const useUpgradePrompt = (): OpenFn => useContext(UpgradeContext)

export function UpgradeProvider({ children }: { children: React.ReactNode }) {
  const [limitKey, setLimitKey] = useState<string | null | undefined>(undefined)
  const [open, setOpen] = useState(false)

  const show = useCallback<OpenFn>((key) => {
    setLimitKey(key ?? null)
    setOpen(true)
  }, [])

  return (
    <UpgradeContext.Provider value={show}>
      {children}
      {open && <UpgradePanel limitKey={limitKey ?? null} onClose={() => setOpen(false)} />}
    </UpgradeContext.Provider>
  )
}

function UpgradePanel({ limitKey, onClose }: { limitKey: string | null; onClose: () => void }) {
  const { t } = useLanguage()
  const { ent } = useEntitlements()
  const [message, setMessage] = useState('')
  const [contact, setContact] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const panelRef = useRef<HTMLDivElement | null>(null)
  const returnFocusTo = useRef<HTMLElement | null>(null)

  // Same contract as ConfirmDialog: Escape closes, Tab cannot leave, focus goes
  // back where it came from. `aria-modal` without a trap is a claim the
  // component does not honour.
  useEffect(() => {
    returnFocusTo.current = document.activeElement as HTMLElement | null
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
      if (e.key !== 'Tab' || !panelRef.current) return
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      returnFocusTo.current?.focus?.()
    }
  }, [onClose])

  const limit = limitKey ? ent?.limits?.[limitKey] : undefined
  const used = limitKey ? ent?.usage?.[limitKey] : undefined
  const whatsapp = ent?.contact?.whatsapp || ''
  const email = ent?.contact?.email || ''

  async function submit() {
    setState('sending')
    try {
      await requestUpgrade({ limit_key: limitKey, message, contact })
      setState('sent')
    } catch {
      // The api layer already toasted the failure; this line is what keeps the
      // user from believing the ask left when it did not, and points them at
      // the two channels that do not depend on our server.
      setState('error')
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('limits.dialog.title')}
      className="modal-backdrop-enter"
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(3px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
      onClick={onClose}
    >
      <div
        ref={panelRef}
        onClick={(e) => e.stopPropagation()}
        className="modal-panel-enter"
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 14, padding: 24, width: '100%', maxWidth: 480,
          maxHeight: '86vh', overflowY: 'auto',
          boxShadow: '0 24px 60px -20px rgba(0,0,0,0.5)',
        }}
      >
        <h3 style={{ margin: '0 0 8px', fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>
          {t('limits.dialog.title')}
        </h3>

        {limitKey && limit != null && used != null && (
          <p style={{
            margin: '0 0 10px', fontSize: 13.5, fontWeight: 600, color: 'var(--text)',
          }}>
            {t('limits.dialog.reached', {
              name: t(`limits.name.${limitKey}`), current: used, max: limit,
            })}
          </p>
        )}

        <p style={{ margin: '0 0 18px', fontSize: 13, lineHeight: 1.6, color: 'var(--dim)' }}>
          {t('limits.dialog.explain')}
        </p>

        <ContactButtons whatsapp={whatsapp} email={email} t={t} />

        {state === 'sent' ? (
          <p style={{
            margin: '18px 0 0', fontSize: 13, lineHeight: 1.55, color: 'var(--text)',
            background: 'var(--surface-2)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '12px 14px',
          }}>
            {t('limits.form.sent')}
          </p>
        ) : (
          <div style={{ marginTop: 18, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <p style={{ margin: '0 0 10px', fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
              {t('limits.form.title')}
            </p>
            <label style={LABEL}>{t('limits.form.message_label')}</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={t('limits.form.message_placeholder')}
              rows={3}
              style={{ ...FIELD, resize: 'vertical', fontFamily: 'inherit' }}
            />
            <label style={{ ...LABEL, marginTop: 12 }}>{t('limits.form.contact_label')}</label>
            <input
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              placeholder={t('limits.form.contact_placeholder')}
              style={FIELD}
            />
            {state === 'error' && (
              <p style={{ margin: '10px 0 0', fontSize: 12.5, color: '#ef4444' }}>
                {t('limits.form.error')}
              </p>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 16 }}>
              <Button variant="ghost" onClick={onClose}>{t('limits.dialog.close')}</Button>
              <Button
                variant="primary"
                loading={state === 'sending'}
                onClick={submit}
              >
                {state === 'sending' ? t('limits.form.sending') : t('limits.form.submit')}
              </Button>
            </div>
          </div>
        )}

        {state === 'sent' && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <Button variant="primary" onClick={onClose}>{t('limits.dialog.close')}</Button>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * The two channels that do not depend on our server staying up. A channel with
 * no address configured is not rendered at all — a WhatsApp button that opens
 * `wa.me/` with no number is worse than no button.
 */
export function ContactButtons({ whatsapp, email, t }: {
  whatsapp: string; email: string; t: (k: string, p?: Record<string, unknown>) => string
}) {
  const prefill = t('limits.contact.prefill')
  const subject = t('limits.contact.subject')
  if (!whatsapp && !email) return null
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
      {whatsapp && (
        <a
          href={`https://wa.me/${whatsapp}?text=${encodeURIComponent(prefill)}`}
          target="_blank"
          rel="noopener noreferrer"
          style={LINK_BUTTON}
        >
          {t('limits.contact.whatsapp')}
        </a>
      )}
      {email && (
        <a
          href={`mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(prefill)}`}
          style={{ ...LINK_BUTTON, background: 'var(--surface-2)', color: 'var(--text)' }}
        >
          {t('limits.contact.email')}
        </a>
      )}
    </div>
  )
}

const LINK_BUTTON: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 7,
  padding: '9px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
  background: 'var(--accent)', color: '#fff', border: '1px solid var(--border)',
  textDecoration: 'none',
}

const LABEL: React.CSSProperties = {
  display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--muted)',
  marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.3,
}

const FIELD: React.CSSProperties = {
  width: '100%', padding: '9px 12px', borderRadius: 8, fontSize: 13,
  background: 'var(--surface-2)', border: '1px solid var(--border)',
  color: 'var(--text)', outline: 'none',
}

'use client'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * App-wide styled confirmation dialog, replacing the browser's native
 * `window.confirm`. The hook returns a promise so the call site stays a
 * near-drop-in for the old blocking pattern:
 *
 *   const confirm = useConfirm()
 *   if (!(await confirm({ title, message, danger: true }))) return
 *
 * Themed via CSS vars so it matches light/dark like the rest of the app.
 *
 * Reach for this only when the action is genuinely irreversible — deleting a
 * dataset or a session, cancelling an order that already went to the supplier,
 * revoking a key someone may be authenticating with. For anything the user can
 * get back, `useToast().undoable(...)` is the better trade: the change happens
 * at once and a "Deshacer" toast holds the irreversible half until the window
 * closes. A modal in front of a reversible action buys nothing and gets
 * click-through-ed within a week.
 */

export interface ConfirmOptions {
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<ConfirmFn>(async () => false)

export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext)
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const { t } = useLanguage()
  const [opts, setOpts] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((v: boolean) => void) | null>(null)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const returnFocusTo = useRef<HTMLElement | null>(null)

  /** Resolve the pending promise once, then forget it. */
  const settle = useCallback((result: boolean) => {
    const pending = resolver.current
    resolver.current = null          // never resolve the same promise twice
    pending?.(result)
  }, [])

  const confirm = useCallback<ConfirmFn>((o) => {
    // A second confirm() arriving while one was open used to OVERWRITE this
    // ref, and the first promise then never settled: its `await` hung forever,
    // so the caller's action was abandoned with no message, no spinner and no
    // error. It failed safe — nothing was written — but it failed invisibly,
    // which is worse to diagnose than a crash. Reachable in practice because
    // the dialog had no focus trap: you could tab out to the page behind and
    // press another button. Settling the old one as `false` keeps the promise
    // contract intact — the abandoned question means "no".
    settle(false)
    const answered = new Promise<boolean>((resolve) => { resolver.current = resolve })
    setOpts(o)
    return answered
  }, [settle])

  const close = useCallback((result: boolean) => {
    settle(result)
    setOpts(null)
  }, [settle])

  // Escape closes, Tab cannot leave, and focus goes back where it came from.
  // `aria-modal` was a claim the component did not honour: without a trap the
  // dialog is modal to the mouse and porous to the keyboard, which is how the
  // stranded-promise bug above was reached in the first place.
  useEffect(() => {
    if (!opts) return
    returnFocusTo.current = document.activeElement as HTMLElement | null

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        close(false)                 // dismissing a question means "no"
        return
      }
      if (e.key !== 'Tab' || !panelRef.current) return
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    // Capture phase: a page that stops propagation on keydown must not be able
    // to swallow Escape and leave the user shut inside the dialog.
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      returnFocusTo.current?.focus?.()
    }
  }, [opts, close])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && (
        // This dialog is reserved for the irreversible, and it appeared with
        // no transition at all. 200ms of backdrop plus 140ms of panel is the
        // beat that turns "a box appeared" into "this is a door" — the one
        // place in the app where a moment of friction is the point. There is
        // deliberately no exit animation on either button: the decision is
        // already made, and anything after it is stolen time.
        <div
          role="dialog"
          aria-modal="true"
          aria-label={opts.title}
          className="modal-backdrop-enter"
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(3px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
          }}
          onClick={() => close(false)}
        >
          <div
            ref={panelRef}
            onClick={(e) => e.stopPropagation()}
            className="modal-panel-enter"
            style={{
              background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 14, padding: 24, width: '100%', maxWidth: 420,
              boxShadow: '0 24px 60px -20px rgba(0,0,0,0.5)',
            }}
          >
            <h3 style={{ margin: '0 0 8px', fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>
              {opts.title}
            </h3>
            {opts.message && (
              <p style={{ margin: '0 0 20px', fontSize: 13.5, lineHeight: 1.55, color: 'var(--dim)' }}>
                {opts.message}
              </p>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => close(false)}
                style={{
                  padding: '9px 16px', borderRadius: 9, cursor: 'pointer',
                  background: 'transparent', border: '1px solid var(--border)',
                  color: 'var(--text)', fontSize: 13, fontWeight: 600,
                }}
              >
                {opts.cancelLabel || t('common.cancel')}
              </button>
              <button
                autoFocus
                onClick={() => close(true)}
                style={{
                  padding: '9px 16px', borderRadius: 9, cursor: 'pointer', border: 'none',
                  background: opts.danger ? '#dc2626' : 'var(--accent)',
                  color: '#fff', fontSize: 13, fontWeight: 600,
                }}
              >
                {opts.confirmLabel || t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  )
}

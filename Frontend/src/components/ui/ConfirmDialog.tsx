'use client'
import { createContext, useCallback, useContext, useRef, useState } from 'react'
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
  const resolver = useRef<(v: boolean) => void>(() => {})

  const confirm = useCallback<ConfirmFn>((o) => {
    setOpts(o)
    return new Promise<boolean>((resolve) => { resolver.current = resolve })
  }, [])

  const close = useCallback((result: boolean) => {
    resolver.current(result)
    setOpts(null)
  }, [])

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

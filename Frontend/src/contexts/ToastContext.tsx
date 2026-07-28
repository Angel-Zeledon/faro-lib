'use client'
import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react'

export type ToastType = 'success' | 'error' | 'info'

/**
 * Inline button rendered on the right of a toast — the "Deshacer" slot.
 * `label` is already translated: this context never touches i18n so it can
 * stay usable from anywhere, including non-React call paths.
 */
export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastOptions {
  /** ms before the toast auto-closes. Default 4500; undo toasts use 5500. */
  duration?: number
  action?: ToastAction
  /**
   * Runs when the toast leaves *without* its action having been used — the
   * timeout expiring, or the user closing it by hand. Deferred work lives
   * here, so "the user did not press Deshacer" is what actually commits it.
   */
  onExpire?: () => void
}

export interface ToastItem {
  id: string
  title: string
  message: string
  type: ToastType
  /** Present when the toast carries an action; the handler lives in a ref. */
  actionLabel?: string
  exiting?: boolean
}

/**
 * A reversible action, expressed as the three things it needs:
 *
 *   apply()   the state change, applied immediately (optimistic)
 *   revert()  the exact inverse, run if the user presses Deshacer
 *   commit()  the part that cannot be taken back — deferred until the undo
 *             window closes, so pressing Deshacer means it never happened
 *
 * Deferring `commit` rather than calling an inverse endpoint is what makes the
 * undo honest: there is no second round-trip that can fail and leave the user
 * staring at a row they already saw disappear. The one trade-off is that
 * closing the tab inside the window drops the commit — the deletion simply
 * does not happen, which is the safe direction to fail in.
 */
export interface UndoableOptions {
  title: string
  message?: string
  /** Translated label for the undo button, e.g. t('common.undo'). */
  undoLabel: string
  type?: ToastType
  /** Undo window in ms. Default 5500. */
  duration?: number
  apply: () => void
  revert: () => void
  commit?: () => Promise<unknown> | unknown
  /** Called if `commit` rejects. `revert` has already run by then. */
  onCommitError?: (e: unknown) => void
}

interface ToastCtx {
  toasts: ToastItem[]
  /** Returns the toast id, so a caller can dismiss it early. */
  addToast: (title: string, message: string, type?: ToastType, options?: ToastOptions) => string
  /** Closes the toast; any deferred `onExpire` commits now. */
  dismiss: (id: string) => void
  /** Runs the toast's action and cancels its deferred `onExpire`. */
  runAction: (id: string) => void
  undoable: (opts: UndoableOptions) => void
}

const ToastContext = createContext<ToastCtx>({
  toasts: [],
  addToast: () => '',
  dismiss: () => {},
  runAction: () => {},
  undoable: () => {},
})

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timers   = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const expiries = useRef<Map<string, () => void>>(new Map())
  const actions  = useRef<Map<string, () => void>>(new Map())

  // Visual removal only — never fires the deferred work.
  const remove = useCallback((id: string) => {
    setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t))
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 320)
    const timer = timers.current.get(id)
    if (timer) { clearTimeout(timer); timers.current.delete(id) }
    actions.current.delete(id)
  }, [])

  const dismiss = useCallback((id: string) => {
    const onExpire = expiries.current.get(id)
    expiries.current.delete(id)
    remove(id)
    onExpire?.()
  }, [remove])

  const runAction = useCallback((id: string) => {
    const fn = actions.current.get(id)
    // Dropping the expiry first is the whole point: the deferred commit must
    // not fire behind an undo the user just asked for.
    expiries.current.delete(id)
    remove(id)
    fn?.()
  }, [remove])

  const addToast = useCallback((
    title: string,
    message: string,
    type: ToastType = 'info',
    options?: ToastOptions,
  ) => {
    const id = `t_${Date.now()}_${Math.random().toString(36).slice(2)}`
    if (options?.action) actions.current.set(id, options.action.onClick)
    if (options?.onExpire) expiries.current.set(id, options.onExpire)
    setToasts(prev => [...prev.slice(-4), { id, title, message, type, actionLabel: options?.action?.label }])
    timers.current.set(id, setTimeout(() => dismiss(id), options?.duration ?? 4500))
    return id
  }, [dismiss])

  const undoable = useCallback((o: UndoableOptions) => {
    o.apply()
    let undone = false
    addToast(o.title, o.message ?? '', o.type ?? 'info', {
      duration: o.duration ?? 5500,
      action: {
        label: o.undoLabel,
        onClick: () => { undone = true; o.revert() },
      },
      onExpire: () => {
        if (undone || !o.commit) return
        Promise.resolve()
          .then(o.commit)
          .catch(e => { o.revert(); o.onCommitError?.(e) })
      },
    })
  }, [addToast])

  // Ctrl/Cmd-Z triggers the newest undo still on screen. Ignored while the
  // caret is in a field, where the browser's own undo is what the user means.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'z' && e.key !== 'Z') return
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
      const el = e.target as HTMLElement | null
      const tag = el?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return
      const newest = [...toasts].reverse().find(t => !t.exiting && t.actionLabel && actions.current.has(t.id))
      if (!newest) return
      e.preventDefault()
      runAction(newest.id)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toasts, runAction])

  return (
    <ToastContext.Provider value={{ toasts, addToast, dismiss, runAction, undoable }}>
      {children}
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

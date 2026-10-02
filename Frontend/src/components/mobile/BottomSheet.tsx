'use client'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * A native-feeling bottom sheet: slides up from the bottom edge over a dimmed
 * page, and is dismissed by dragging it down, tapping the dim, the × or Esc.
 *
 * ```tsx
 * const [open, setOpen] = useState(false)
 * <BottomSheet
 *   open={open}
 *   onClose={() => setOpen(false)}
 *   title={t('po.reception_title')}
 *   footer={<button className="btn-primary" onClick={save}>{t('common.save')}</button>}
 * >
 *   …content (scrolls inside the sheet when tall)…
 * </BottomSheet>
 * ```
 *
 * Accessibility, all built in: rendered in a portal on <body>, `role="dialog"`
 * + `aria-modal` + `aria-labelledby` (the title), focus moves into the sheet on
 * open, Tab/Shift+Tab are trapped inside it, Esc closes, and focus returns to
 * whatever opened it. The page underneath does not scroll while it is open.
 *
 * Motion: transform/opacity only (no layout), 260ms on the shared easing, and
 * no animation at all under `prefers-reduced-motion` (globals.css).
 *
 * Drag to close: the grab handle and header start a drag; releasing past 25%
 * of the sheet's height (or flicking down fast) closes it, otherwise it springs
 * back. Content below the header scrolls normally — it never starts a drag, so
 * a long list inside the sheet scrolls instead of fighting the gesture.
 */
export interface BottomSheetProps {
  open: boolean
  onClose: () => void
  /** Shown in the header and used as the dialog's accessible name. */
  title: React.ReactNode
  children: React.ReactNode
  /** Pinned under the scrolling content — the sheet's primary action(s). */
  footer?: React.ReactNode
  /** Max height of the sheet; content scrolls past it. Default 88dvh. */
  maxHeight?: string
  /** Element to focus on open instead of the first focusable one. */
  initialFocusRef?: React.RefObject<HTMLElement>
  /** Hide the × in the header (drag, dim and Esc still close). */
  hideCloseButton?: boolean
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
const EXIT_MS = 220

export default function BottomSheet({
  open, onClose, title, children, footer, maxHeight = '88dvh', initialFocusRef, hideCloseButton,
}: BottomSheetProps) {
  const { t } = useLanguage()
  const titleId = useId()
  const sheetRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // Stay mounted through the exit animation.
  const [mounted, setMounted] = useState(open)
  const [shown, setShown] = useState(false)
  const [drag, setDrag] = useState(0)
  const dragState = useRef<{ startY: number; lastY: number; lastT: number; v: number } | null>(null)

  useEffect(() => {
    if (open) {
      setMounted(true)
      // Next frame, so the enter transition has a starting point.
      const id = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)))
      return () => cancelAnimationFrame(id)
    }
    setShown(false)
    const id = setTimeout(() => setMounted(false), EXIT_MS)
    return () => clearTimeout(id)
  }, [open])

  // Focus in, trap, Esc, scroll lock, focus back out.
  // Runs once the sheet is actually in the DOM (`mounted`), not on the render
  // where `open` flips — on that one the portal has not rendered yet.
  useEffect(() => {
    if (!open || !mounted) return
    openerRef.current = document.activeElement as HTMLElement | null
    const raf = requestAnimationFrame(() => {
      const target = initialFocusRef?.current
        ?? sheetRef.current?.querySelector<HTMLElement>(FOCUSABLE.split(', ').map(f => `[data-sheet-body] ${f}`).join(', '))
        ?? sheetRef.current
      target?.focus({ preventScroll: true })
    })
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onCloseRef.current(); return }
      if (e.key !== 'Tab' || !sheetRef.current) return
      const items = Array.from(sheetRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter(el => el.offsetParent !== null)
      if (!items.length) { e.preventDefault(); return }
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
      openerRef.current?.focus?.({ preventScroll: true })
    }
  }, [open, mounted, initialFocusRef])

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    // A tap on the × must stay a tap.
    if ((e.target as HTMLElement).closest('button')) return
    dragState.current = { startY: e.clientY, lastY: e.clientY, lastT: performance.now(), v: 0 }
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }, [])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const s = dragState.current
    if (!s) return
    const now = performance.now()
    s.v = (e.clientY - s.lastY) / Math.max(1, now - s.lastT)
    s.lastY = e.clientY
    s.lastT = now
    setDrag(Math.max(0, e.clientY - s.startY))
  }, [])

  const onPointerUp = useCallback(() => {
    const s = dragState.current
    dragState.current = null
    if (!s) return
    const h = sheetRef.current?.offsetHeight ?? 400
    const dy = s.lastY - s.startY
    if (dy > h * 0.25 || (s.v > 0.6 && dy > 24)) onCloseRef.current()
    setDrag(0)
  }, [])

  if (!mounted || typeof document === 'undefined') return null

  const dragging = dragState.current !== null && drag > 0

  return createPortal(
    <div className="bottom-sheet-root" style={{ position: 'fixed', inset: 0, zIndex: 1000 }}>
      <div
        aria-hidden="true"
        onClick={() => onCloseRef.current()}
        className="bottom-sheet-motion"
        style={{
          position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.45)',
          opacity: shown ? Math.max(0.2, 1 - drag / 400) : 0,
          transition: dragging ? 'none' : `opacity ${EXIT_MS}ms var(--ease-out)`,
        }}
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="bottom-sheet-motion"
        style={{
          position: 'absolute', left: 0, right: 0, bottom: 0,
          maxHeight, display: 'flex', flexDirection: 'column',
          background: 'var(--surface)', color: 'var(--text)',
          borderRadius: '18px 18px 0 0', boxShadow: '0 -12px 40px rgba(0,0,0,0.28)',
          outline: 'none',
          transform: shown ? `translateY(${drag}px)` : 'translateY(100%)',
          transition: dragging ? 'none' : 'transform var(--dur-4) var(--ease-out)',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          // Never wider than a comfortable reading column on a tablet.
          marginInline: 'auto', width: '100%', maxWidth: 640,
        }}
      >
        {/* Drag zone: handle + header. */}
        <div
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          style={{ touchAction: 'none', flexShrink: 0, cursor: 'grab' }}
        >
          <div aria-hidden="true" title={t('mobile.sheet_drag_hint')} style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 2px' }}>
            <span style={{ width: 40, height: 5, borderRadius: 3, background: 'var(--border-strong, var(--border))' }} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 8px 6px 20px', minHeight: 48 }}>
            <h2 id={titleId} style={{ flex: 1, minWidth: 0, margin: 0, fontSize: 17, fontWeight: 700, letterSpacing: '-0.01em' }}>
              {title}
            </h2>
            {!hideCloseButton && (
              <button
                type="button"
                onClick={() => onCloseRef.current()}
                aria-label={t('common.close')}
                style={{
                  all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
                  width: 44, height: 44, borderRadius: 22, flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
                }}
              >
                <X size={20} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
        <div data-sheet-body="" style={{ flex: 1, minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', padding: '0 16px 16px' }}>
          {children}
        </div>
        {footer && (
          <div style={{ flexShrink: 0, padding: '12px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 10 }}>
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

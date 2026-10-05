'use client'
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Highlighter, PenLine, RotateCcw, Square, Trash2 } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { encodeUnderLimit } from '@/lib/captureScreen'

/**
 * The screenshot preview with a small markup tool: draw, highlight, and black
 * out rectangles to hide sensitive data. Undo and clear.
 *
 * Black-out is not a layer: `exportImage()` flattens everything onto one bitmap
 * and that bitmap is the only thing that is ever sent, so a covered area is
 * gone from the pixels, not merely hidden under a shape.
 *
 * Marking is pointer-only (mouse, touch, pen) and entirely optional; a person
 * who cannot draw sends the plain screenshot or unticks it on the dialog.
 */

type Tool = 'pen' | 'highlight' | 'redact'

interface Op {
  tool: Tool
  color: string
  /** pen/highlight: the stroke; redact: the two corners. */
  points: Array<[number, number]>
}

const COLORS: Array<{ id: string; value: string; key: string }> = [
  { id: 'red', value: '#E5484D', key: 'feedback.color_red' },
  { id: 'yellow', value: '#F5C400', key: 'feedback.color_yellow' },
  { id: 'blue', value: '#2F6FED', key: 'feedback.color_blue' },
  { id: 'green', value: '#2FA84F', key: 'feedback.color_green' },
]

export interface ScreenshotEditorHandle {
  /** The flattened image as a data URL that fits the server's cap, or null. */
  exportImage: () => string | null
}

const ScreenshotEditor = forwardRef<ScreenshotEditorHandle, {
  src: string
  disabled?: boolean
}>(function ScreenshotEditor({ src, disabled }, ref) {
  const { t } = useLanguage()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const baseRef = useRef<HTMLImageElement | null>(null)
  const opsRef = useRef<Op[]>([])
  const currentRef = useRef<Op | null>(null)
  const [tool, setTool] = useState<Tool>('highlight')
  const [color, setColor] = useState(COLORS[0].value)
  const [count, setCount] = useState(0)
  const [ready, setReady] = useState(false)

  const paint = useCallback(() => {
    const canvas = canvasRef.current
    const base = baseRef.current
    if (!canvas || !base) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.globalAlpha = 1
    ctx.drawImage(base, 0, 0, canvas.width, canvas.height)
    const unit = canvas.width / 1000
    const all = currentRef.current ? [...opsRef.current, currentRef.current] : opsRef.current
    for (const op of all) {
      if (op.tool === 'redact') {
        const [a, b] = [op.points[0], op.points[op.points.length - 1]]
        ctx.globalAlpha = 1
        ctx.fillStyle = '#000000'
        ctx.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]))
        continue
      }
      ctx.beginPath()
      op.points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)))
      if (op.points.length === 1) ctx.lineTo(op.points[0][0] + 0.01, op.points[0][1])
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.strokeStyle = op.color
      ctx.lineWidth = (op.tool === 'highlight' ? 22 : 4) * unit
      // One stroke per op, so overlap inside a single highlight stays even.
      ctx.globalAlpha = op.tool === 'highlight' ? 0.38 : 1
      ctx.stroke()
    }
    ctx.globalAlpha = 1
  }, [])

  useEffect(() => {
    const img = new Image()
    img.onload = () => {
      baseRef.current = img
      const canvas = canvasRef.current
      if (canvas) { canvas.width = img.naturalWidth; canvas.height = img.naturalHeight }
      setReady(true)
      paint()
    }
    img.src = src
  }, [src, paint])

  useImperativeHandle(ref, () => ({
    exportImage: () => {
      const canvas = canvasRef.current
      if (!canvas || !baseRef.current) return null
      currentRef.current = null
      paint()
      return encodeUnderLimit(canvas)
    },
  }), [paint])

  const toCanvas = (e: React.PointerEvent<HTMLCanvasElement>): [number, number] => {
    const canvas = canvasRef.current!
    const r = canvas.getBoundingClientRect()
    return [
      ((e.clientX - r.left) / r.width) * canvas.width,
      ((e.clientY - r.top) / r.height) * canvas.height,
    ]
  }

  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (disabled || !ready) return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    currentRef.current = { tool, color, points: [toCanvas(e)] }
    paint()
  }
  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cur = currentRef.current
    if (!cur) return
    const p = toCanvas(e)
    if (cur.tool === 'redact') cur.points = [cur.points[0], p]
    else cur.points.push(p)
    paint()
  }
  const onUp = () => {
    const cur = currentRef.current
    currentRef.current = null
    if (!cur) return
    // A redact click with no drag would be a zero-size box: drop it.
    const keep = cur.tool !== 'redact'
      || (cur.points.length > 1
        && Math.abs(cur.points[0][0] - cur.points[1][0]) > 2
        && Math.abs(cur.points[0][1] - cur.points[1][1]) > 2)
    if (keep) { opsRef.current = [...opsRef.current, cur]; setCount(opsRef.current.length) }
    paint()
  }

  const undo = () => { opsRef.current = opsRef.current.slice(0, -1); setCount(opsRef.current.length); paint() }
  const clear = () => { opsRef.current = []; setCount(0); paint() }

  const toolBtn = (id: Tool, label: string, icon: React.ReactNode) => (
    <button
      type="button"
      onClick={() => setTool(id)}
      aria-pressed={tool === id}
      disabled={disabled}
      className="tap-feedback"
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'default' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 6, minHeight: 36, padding: '0 10px',
        borderRadius: 8, fontSize: 12.5, fontWeight: 600,
        background: tool === id ? 'var(--accent-dim)' : 'var(--surface-2)',
        color: tool === id ? 'var(--accent)' : 'var(--text)',
        border: `1px solid ${tool === id ? 'var(--accent)' : 'var(--border)'}`,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {icon}{label}
    </button>
  )

  const small: React.CSSProperties = {
    all: 'unset', boxSizing: 'border-box', cursor: 'pointer', minHeight: 36, padding: '0 10px',
    display: 'inline-flex', alignItems: 'center', gap: 6, borderRadius: 8, fontSize: 12.5,
    color: 'var(--muted)', border: '1px solid var(--border)', background: 'var(--surface-2)',
  }

  return (
    <div>
      <div role="group" aria-label={t('feedback.tools_label')}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        {toolBtn('highlight', t('feedback.tool_highlight'), <Highlighter size={15} aria-hidden="true" />)}
        {toolBtn('pen', t('feedback.tool_pen'), <PenLine size={15} aria-hidden="true" />)}
        {toolBtn('redact', t('feedback.tool_redact'), <Square size={15} fill="currentColor" aria-hidden="true" />)}
        {tool !== 'redact' && (
          <span role="radiogroup" aria-label={t('feedback.tools_label')} style={{ display: 'inline-flex', gap: 6, marginLeft: 4 }}>
            {COLORS.map(c => (
              <button
                key={c.id} type="button" role="radio" aria-checked={color === c.value}
                aria-label={t(c.key)} title={t(c.key)} disabled={disabled}
                onClick={() => setColor(c.value)}
                style={{
                  all: 'unset', boxSizing: 'border-box', cursor: disabled ? 'default' : 'pointer',
                  width: 28, height: 28, borderRadius: '50%', background: c.value,
                  border: color === c.value ? '3px solid var(--text)' : '2px solid var(--border)',
                }}
              />
            ))}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <button type="button" onClick={undo} disabled={disabled || count === 0}
          style={{ ...small, opacity: count === 0 || disabled ? 0.45 : 1 }}>
          <RotateCcw size={14} aria-hidden="true" />{t('feedback.undo')}
        </button>
        <button type="button" onClick={clear} disabled={disabled || count === 0}
          style={{ ...small, opacity: count === 0 || disabled ? 0.45 : 1 }}>
          <Trash2 size={14} aria-hidden="true" />{t('feedback.clear')}
        </button>
      </div>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={t('feedback.canvas_label')}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        style={{
          display: 'block', width: 'auto', height: 'auto', maxWidth: '100%', maxHeight: '48dvh',
          margin: '0 auto', borderRadius: 8, border: '1px solid var(--border)',
          // Drawing must not scroll the sheet underneath on a phone.
          touchAction: 'none', cursor: disabled ? 'default' : tool === 'redact' ? 'crosshair' : 'crosshair',
          opacity: disabled ? 0.45 : 1, background: 'var(--surface-2)',
        }}
      />
    </div>
  )
})

export default ScreenshotEditor

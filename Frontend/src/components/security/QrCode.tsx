'use client'
import { useMemo } from 'react'
import { qrMatrix, qrPath } from '@/lib/qr'

/**
 * A QR code drawn in the browser (src/lib/qr.ts). Never an image from a
 * service: the authenticator secret inside it must not leave this page.
 * Always dark-on-white, even in dark mode: readers need the light quiet zone.
 * Renders nothing when the text is too long to encode, and the caller shows
 * the key to type by hand instead.
 */
export default function QrCode({ value, size = 188, label }: { value: string; size?: number; label: string }) {
  const drawn = useMemo(() => {
    try {
      return qrPath(qrMatrix(value))
    } catch {
      return null
    }
  }, [value])
  if (!drawn) return null
  return (
    <svg
      role="img" aria-label={label}
      width={size} height={size} viewBox={`0 0 ${drawn.size} ${drawn.size}`}
      shapeRendering="crispEdges"
      style={{ display: 'block', borderRadius: 8, background: '#fff', flexShrink: 0 }}
    >
      <rect width={drawn.size} height={drawn.size} fill="#fff" />
      <path d={drawn.d} fill="#000" />
    </svg>
  )
}

'use client'
import './mobileForms.css'

/**
 * Wraps a phone screen (or a BottomSheet body) whose fields must be typeable:
 * every input/select/textarea inside gets 16px text (no iOS zoom-on-focus) and
 * a 44px minimum height. Phones only — the rules sit under the 768px media
 * query, so wrapping desktop markup changes nothing there.
 */
export default function MobileFormScope({ children, style }: {
  children: React.ReactNode
  style?: React.CSSProperties
}) {
  return <div className="m-form" style={style}>{children}</div>
}

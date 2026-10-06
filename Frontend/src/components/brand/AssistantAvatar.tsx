'use client'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * The assistant's face in a conversation: a small flat robot, hand-drawn on the
 * 24px grid with the same 1.5px round-cap stroke the app's lucide icons use.
 *
 * Rounded-square head, two ear nubs, a short antenna that bends a little and
 * ends in a round tip, two plain eyes and a one-line mouth. No sparkles, no
 * headset, no circuit lines.
 *
 * Colour: strokes and eyes are `currentColor` (the wrapper defaults to the
 * theme accent, so it follows light and dark), the head is the same colour at a
 * soft tint, and the antenna tip carries the brand's light teal.
 */
export function AssistantAvatar({
  size = 22, label, decorative = false,
}: {
  size?: number
  /** Overrides the default accessible name. */
  label?: string
  /** Set when text next to the avatar already says who is speaking. */
  decorative?: boolean
}) {
  const { t } = useLanguage()
  const name = label ?? t('analyst.avatar_label')
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : name}
      aria-hidden={decorative ? true : undefined}
      focusable="false"
      style={{ flexShrink: 0, color: 'var(--accent)' }}
      stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"
    >
      {/* antenna: a short stalk that leans, with a round tip */}
      <path d="M12 7.2V5.4c0-.9.6-1.5 1.6-1.9" />
      <circle cx="14.6" cy="3.2" r="1.35" fill="#5EB8AE" />
      {/* ear nubs */}
      <path d="M3.6 12.2v3" />
      <path d="M20.4 12.2v3" />
      {/* head */}
      <rect x="5.2" y="7.2" width="13.6" height="12.4" rx="4.2" fill="currentColor" fillOpacity={0.13} />
      {/* eyes */}
      <circle cx="9.4" cy="12.4" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="14.6" cy="12.4" r="1.05" fill="currentColor" stroke="none" />
      {/* mouth */}
      <path d="M9.8 16.1c1.4.9 3 .9 4.4 0" />
    </svg>
  )
}

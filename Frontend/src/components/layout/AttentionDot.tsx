'use client'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * A small neutral count next to a navigation entry: something is waiting there.
 * Takes its colour from the entry's own text, so it reads as part of the label
 * rather than as an alert, and renders nothing at zero.
 */
export default function AttentionDot({ count, ml }: { count: number; ml?: number | string }) {
  const { t } = useLanguage()
  if (count <= 0) return null
  return (
    <span
      role="img"
      aria-label={t('attention.nav_dot_aria', { n: count })}
      style={{
        marginLeft: ml, flexShrink: 0, boxSizing: 'border-box',
        minWidth: 16, height: 16, padding: '0 4px', borderRadius: 8,
        border: '1px solid color-mix(in srgb, currentColor 30%, transparent)',
        fontSize: 10, fontWeight: 600, lineHeight: 1, fontVariantNumeric: 'tabular-nums',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

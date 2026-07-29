'use client'
import { HelpCircle } from 'lucide-react'
import { useTour } from '@/contexts/TourContext'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * The "view tutorial" affordance in the top bar.
 *
 * Renders nothing on screens with no tour, so it is never a control that does
 * nothing. It is deliberately quiet — a tutorial the user did not ask for is
 * an interruption, and this is how they ask.
 */
export default function TourLauncher() {
  const { available, active, start } = useTour()
  const { t } = useLanguage()

  if (!available || active) return null

  return (
    <button
      onClick={() => start()}
      title={t('tour.launch')}
      aria-label={`${t('tour.launch')}: ${t(available.nameKey)}`}
      className="btn"
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, borderRadius: 8, cursor: 'pointer',
        background: 'transparent', border: '1px solid var(--border)',
        color: 'var(--dim)',
      }}
    >
      <HelpCircle size={15} aria-hidden="true" />
    </button>
  )
}

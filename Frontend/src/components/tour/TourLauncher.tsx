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
  const { available, active, start, stop } = useTour()
  const { t } = useLanguage()

  if (!available) return null

  // Stays mounted while the tour runs, as a lit toggle that closes it. It used
  // to unmount, which meant the control you had just pressed vanished — the
  // top bar lost an item mid-interaction and there was no way back to the
  // tour except the small × on the card.
  const running = active?.id === available.id

  return (
    <button
      onClick={() => (running ? stop() : start())}
      title={running ? t('tour.close') : t('tour.launch')}
      aria-label={running ? t('tour.close') : `${t('tour.launch')}: ${t(available.nameKey)}`}
      aria-pressed={running}
      className="btn"
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, borderRadius: 8, cursor: 'pointer',
        background: running ? 'color-mix(in srgb, var(--accent) 16%, transparent)' : 'transparent',
        border: `1px solid ${running ? 'var(--accent)' : 'var(--border)'}`,
        color: running ? 'var(--accent)' : 'var(--dim)',
        // Above the tour's own shade, so it never looks disabled behind it.
        position: 'relative', zIndex: 9100,
      }}
    >
      <HelpCircle size={15} aria-hidden="true" />
    </button>
  )
}

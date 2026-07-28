'use client'
import { Menu } from 'lucide-react'
import { useSidebar } from '@/contexts/SidebarContext'
import { useLanguage } from '@/contexts/LanguageContext'

/**
 * Opens the sidebar drawer on narrow viewports.
 *
 * It sits beside `TopBar` rather than inside it: the bar is a shared component
 * several screens read from, and the whole point of this change is to add a
 * mobile view without editing the desktop chrome. The button is 44px so a
 * thumb hits it, and it inherits the bar's surface + border so the two read as
 * one strip.
 */
export default function MobileNavButton() {
  const { openDrawer } = useSidebar()
  const { t } = useLanguage()
  const label = t('sidebar.expand')

  return (
    <button
      onClick={openDrawer}
      aria-label={label}
      title={label}
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: 'pointer',
        width: 44, height: 52, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--surface)',
        borderBottom: '1px solid var(--border)',
        color: 'var(--text)',
      }}
    >
      <Menu size={20} aria-hidden="true" />
    </button>
  )
}

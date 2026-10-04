'use client'
/**
 * Unread direct-messages indicator for the top bar.
 *
 * Reads GET /messages/unread-count through the one shared poller in
 * lib/dmUnread (silent — a failed poll must not toast), so the desktop badge
 * and the mobile tab bar never show two different numbers.
 * It used to render nothing at all for plans without team_messaging; there is
 * one plan now, and every tenant has the screen this points at.
 */
import Link from 'next/link'
import { MessageSquare } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useDmUnread } from '@/lib/dmUnread'

export default function MessagesBadge() {
  const { t } = useLanguage()
  const unread = useDmUnread()

  return (
    <Link
      href="/mensajes"
      title={t('messages.page_title')}
      aria-label={t('messages.page_title')}
      style={{
        position: 'relative', display: 'flex', alignItems: 'center',
        color: 'var(--muted)', textDecoration: 'none',
      }}
    >
      <MessageSquare size={16} />
      {unread > 0 && (
        <span style={{
          position: 'absolute', top: -6, right: -8,
          minWidth: 15, height: 15, borderRadius: 8, padding: '0 4px',
          background: 'var(--accent)', color: '#fff',
          fontSize: 9.5, fontWeight: 700,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  )
}

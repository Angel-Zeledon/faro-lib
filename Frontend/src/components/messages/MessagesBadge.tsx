'use client'
/**
 * Unread direct-messages indicator for the top bar.
 *
 * Polls GET /messages/unread-count (silent — a failed poll must not toast).
 * It used to render nothing at all for plans without team_messaging; there is
 * one plan now, and every tenant has the screen this points at.
 */
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { MessageSquare } from 'lucide-react'
import { getDmUnreadCount } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'

const POLL_MS = 30000

export default function MessagesBadge() {
  const { t } = useLanguage()
  const [unread, setUnread] = useState(0)

  useEffect(() => {
    let cancelled = false
    const poll = () =>
      getDmUnreadCount()
        .then(d => { if (!cancelled) setUnread(d.unread) })
        .catch(() => {})
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])


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

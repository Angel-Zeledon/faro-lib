'use client'
/**
 * `/quick-start` and `/data` are one thing in the user's head — "get my sales
 * into Faro" — and they used to be two separate sidebar entries, which taught a
 * new user that they were two different places.
 *
 * The two routes stay exactly as they are (CLAUDE.md keeps route names out of
 * scope: users have shared these URLs). Only the navigation collapses: one
 * sidebar item, and this strip at the top of both pages so moving between them
 * is a tab switch instead of a trip back to the nav.
 */
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Upload, Database } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'

const TABS = [
  { href: '/quick-start', labelKey: 'nav.quick_start', Icon: Upload },
  { href: '/data',        labelKey: 'data.page_title', Icon: Database },
]

export default function DataTabs({ style }: { style?: React.CSSProperties }) {
  const path  = usePathname()
  const { t } = useLanguage()

  return (
    <nav
      aria-label={t('group.data')}
      style={{
        display: 'flex', alignItems: 'flex-end', gap: 2,
        borderBottom: '1px solid var(--border)',
        ...style,
      }}
    >
      {TABS.map(({ href, labelKey, Icon }) => {
        const active = path === href
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            style={{
              display: 'flex', alignItems: 'center', gap: 7,
              padding: '9px 14px',
              fontSize: 13, fontWeight: active ? 700 : 500,
              textDecoration: 'none',
              color: active ? 'var(--accent)' : 'var(--muted)',
              borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
              marginBottom: -1,
              transition: 'color 0.15s, border-color 0.15s',
            }}
          >
            <Icon size={14} strokeWidth={active ? 2.2 : 1.8} />
            {t(labelKey)}
          </Link>
        )
      })}
    </nav>
  )
}

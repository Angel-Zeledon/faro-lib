'use client'
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import { LEGAL_ORDER, LEGAL_PATHS, type LegalKey } from '@/components/landing/legalPaths'

const LABEL: Record<LegalKey, string> = {
  terms: 'legal.terms',
  privacy: 'legal.privacy',
  cookies: 'legal.cookies',
  notice: 'legal.notice',
}

/**
 * The four legal documents as links, for the app's own chrome.
 *
 * `compact` is the desktop sidebar footer: one quiet line of small text,
 * sized for a cursor. The default is for a phone (the "Más" sheet), where
 * every link is a 44px target.
 */
export default function LegalLinks({ compact, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const { t } = useLanguage()
  if (compact) {
    return (
      <nav aria-label={t('legal.group')} style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 10px' }}>
        {LEGAL_ORDER.map(k => (
          <Link
            key={k} href={LEGAL_PATHS[k]} onClick={onNavigate}
            style={{ fontSize: 11, color: 'var(--sidebar-dim)', textDecoration: 'none' }}
          >
            {t(LABEL[k])}
          </Link>
        ))}
      </nav>
    )
  }
  return (
    <nav aria-label={t('legal.group')} style={{ display: 'flex', flexWrap: 'wrap', gap: '0 4px', padding: '0 4px' }}>
      {LEGAL_ORDER.map(k => (
        <Link
          key={k} href={LEGAL_PATHS[k]} onClick={onNavigate}
          className="tap-feedback"
          style={{
            display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 8px',
            borderRadius: 8, fontSize: 14, color: 'var(--muted)', textDecoration: 'none',
          }}
        >
          {t(LABEL[k])}
        </Link>
      ))}
    </nav>
  )
}

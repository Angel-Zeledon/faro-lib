'use client'
import Link from 'next/link'
import { useLanguage } from '@/contexts/LanguageContext'
import { LEGAL_HUB_PATH, LEGAL_ORDER, LEGAL_PATHS, type MainLegalKey } from '@/components/landing/legalPaths'

const LABEL: Record<MainLegalKey, string> = {
  terms: 'legal.terms',
  privacy: 'legal.privacy',
  cookies: 'legal.cookies',
  notice: 'legal.notice',
}

/**
 * The four main legal documents as links, plus one to the /legal hub that
 * lists the rest, for the app's own chrome.
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
            key={k} href={LEGAL_PATHS[k]} onClick={onNavigate} target="_blank" rel="noopener" prefetch={false}
            style={{ fontSize: 11, color: 'var(--sidebar-dim)', textDecoration: 'none' }}
          >
            {t(LABEL[k])}
          </Link>
        ))}
        <Link
          href={LEGAL_HUB_PATH} onClick={onNavigate} target="_blank" rel="noopener" prefetch={false}
          style={{ fontSize: 11, color: 'var(--sidebar-dim)', textDecoration: 'none' }}
        >
          {t('legal.all')}
        </Link>
      </nav>
    )
  }
  return (
    <nav aria-label={t('legal.group')} style={{ display: 'flex', flexWrap: 'wrap', gap: '0 4px', padding: '0 4px' }}>
      {LEGAL_ORDER.map(k => (
        <Link
          key={k} href={LEGAL_PATHS[k]} onClick={onNavigate} target="_blank" rel="noopener" prefetch={false}
          className="tap-feedback"
          style={{
            display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 8px',
            borderRadius: 8, fontSize: 14, color: 'var(--muted)', textDecoration: 'none',
          }}
        >
          {t(LABEL[k])}
        </Link>
      ))}
      <Link
        href={LEGAL_HUB_PATH} onClick={onNavigate} target="_blank" rel="noopener" prefetch={false}
        className="tap-feedback"
        style={{
          display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 8px',
          borderRadius: 8, fontSize: 14, color: 'var(--muted)', textDecoration: 'none',
        }}
      >
        {t('legal.all')}
      </Link>
    </nav>
  )
}

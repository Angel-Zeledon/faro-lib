'use client'
import { Fragment } from 'react'
import { useLanguage } from '@/contexts/LanguageContext'
import { LEGAL_PATHS } from '@/components/landing/legalPaths'

/**
 * A catalogue sentence with `{terms}` and `{privacy}` slots, rendered with the
 * two documents as real links. The slots keep the sentence reorderable by a
 * translator instead of gluing three fragments together in code.
 *
 * Links open in a new tab: the person reading this is half-way through a form
 * (signup, trial) and must not lose what they typed to read the terms.
 */
export default function TermsSentence({ templateKey, linkStyle }: {
  templateKey: string
  linkStyle?: React.CSSProperties
}) {
  const { t } = useLanguage()
  const links: Record<string, [string, string]> = {
    terms: [LEGAL_PATHS.terms, t('auth.terms_link')],
    privacy: [LEGAL_PATHS.privacy, t('auth.privacy_link')],
  }
  const parts = t(templateKey).split(/\{(terms|privacy)\}/)
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>
        const [href, label] = links[part]
        return (
          <a key={i} href={href} target="_blank" rel="noopener" style={linkStyle}>
            {label}
          </a>
        )
      })}
    </>
  )
}

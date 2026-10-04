// The help center's inline markup — **bold**, `code`, [label](href) — turned
// into elements. Deliberately tiny: the pages are typed data, not Markdown,
// and three marks are all they use (src/i18n/docs/types.ts).
import Link from 'next/link'
import { Fragment, type ReactNode } from 'react'
import { appHref } from '@/lib/siteUrls'

const TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g

function DocLink({ href, children }: { href: string; children: ReactNode }) {
  // `app:/route` is a screen of the app, which may live on another origin.
  if (href.startsWith('app:')) {
    return <a href={appHref(href.slice(4))}>{children}</a>
  }
  if (/^https?:\/\//.test(href) || href.startsWith('mailto:')) {
    return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  }
  // Downloads (the manual, the CSV template) are files, not routes.
  if (/\.(pdf|csv|png)$/.test(href)) {
    return <a href={href}>{children}</a>
  }
  return <Link href={href}>{children}</Link>
}

export function Inline({ text }: { text: string }) {
  const parts = text.split(TOKEN)
  return (
    <>
      {parts.map((part, i) => {
        if (!part) return null
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return <strong key={i}><Inline text={part.slice(2, -2)} /></strong>
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return <code key={i}>{part.slice(1, -1)}</code>
        }
        const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
        if (link) return <DocLink key={i} href={link[2]}>{link[1]}</DocLink>
        return <Fragment key={i}>{part}</Fragment>
      })}
    </>
  )
}

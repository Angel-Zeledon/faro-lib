import type { Metadata } from 'next'

/**
 * The unsubscribe page is reached by a private link in an email, so its URL is
 * a credential: keep it out of search results and out of the Referer header.
 */
export const metadata: Metadata = {
  title: 'StockAI',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
}

export default function UnsubscribeLayout({ children }: { children: React.ReactNode }) {
  return children
}

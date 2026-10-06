import type { Metadata } from 'next'

/**
 * The approval decision page is reached by a private link, so the URL is a
 * credential: keep it out of search results and out of the Referer header of any
 * request the page makes. (The network layer in lib/approvalLink.ts also sends
 * `referrerPolicy: 'no-referrer'`; this covers navigations away from the page.)
 */
export const metadata: Metadata = {
  title: 'StockAI',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
}

export default function ApprovalLinkLayout({ children }: { children: React.ReactNode }) {
  return children
}

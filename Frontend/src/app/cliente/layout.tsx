import type { Metadata } from 'next'

/**
 * The customer's page is reached by a private link, so the URL is a credential:
 * keep it out of search results and out of the Referer header of any request
 * the page makes (lib/customerPortal.ts also sends `referrerPolicy: 'no-referrer'`;
 * this covers navigations away from the page).
 */
export const metadata: Metadata = {
  title: 'StockAI',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
}

export default function CustomerPortalLayout({ children }: { children: React.ReactNode }) {
  return children
}

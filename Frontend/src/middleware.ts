import { NextResponse, type NextRequest } from 'next/server'

// The English pages of the public site (/en and below) are the only requests
// this runs on. It tells the root layout, through a request header, to render
// <html lang="en"> — the layout cannot read the URL itself. Nothing else is
// done here: no redirects, no Accept-Language sniffing (a shared /precios stays
// Spanish and a shared /en/pricing stays English), no auth.
// Read by app/layout.tsx under the same name.
const SITE_LANG_HEADER = 'x-site-lang'

export function middleware(request: NextRequest) {
  const headers = new Headers(request.headers)
  headers.set(SITE_LANG_HEADER, 'en')
  return NextResponse.next({ request: { headers } })
}

export const config = {
  matcher: ['/en', '/en/:path*'],
}

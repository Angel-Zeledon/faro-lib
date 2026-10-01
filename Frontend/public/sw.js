// StockAI service worker — what lets the installed app open fast and say
// something useful when there is no connection. Registered only inside the app
// (components/layout/PwaRegister.tsx), never on the landing.
//
// Deliberately narrow:
// * /_next/static/ is cached forever: those files are content-hashed, so a new
//   deploy ships new URLs and an old cache can never serve stale code.
// * /api/ is NEVER cached. A purchasing decision made on yesterday's stock
//   because a cache answered is worse than an error saying you are offline.
// * Page navigations go to the network; only when it fails is the offline page
//   shown.
const VERSION = 'stockai-v1'
const STATIC = `${VERSION}-static`
const OFFLINE_URL = '/offline.html'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC).then((c) => c.addAll([OFFLINE_URL, '/icons/icon-192.png'])),
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)))
    return
  }

  if (url.pathname.startsWith('/_next/static/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(
      caches.open(STATIC).then(async (cache) => {
        const hit = await cache.match(req)
        if (hit) return hit
        const res = await fetch(req)
        if (res.ok) cache.put(req, res.clone())
        return res
      }),
    )
  }
})

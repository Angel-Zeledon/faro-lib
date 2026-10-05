import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/siteUrls'

// Served as /robots.txt. Only the landing is meant to be found: every other
// route is the signed-in app (a crawler would only ever see its login redirect)
// or a sign-in form.
//
// An allow-list rather than a list of app routes to hide: the app gains screens
// and nobody will remember to come back here, and a new screen must not become
// crawlable by default. Google and Bing honour `$` and `*`, and the most
// specific rule wins, so `/` itself, the screenshots and the manual stay open
// under the blanket `Disallow: /`.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: [
        '/$', '/precios$', '/como-funciona$', '/preguntas-frecuentes$', '/seguridad$', '/desarrolladores$', '/industrias$', '/industrias/consumo-masivo$', '/industrias/ferreteria$', '/industrias/farmacia$', '/industrias/autopartes$', '/industrias/retail$', '/stockai-vs-excel$', '/como-se-calcula$', '/que-es-stockai$', '/software-de-inventario-para-distribuidores$', '/como-calcular-el-punto-de-reorden$', '/stock-de-seguridad$', '/pronostico-de-demanda-para-compras$','/integraciones$', '/novedades$', '/privacidad$', '/terminos$', '/cookies$', '/aviso-legal$', '/legal$', '/uso-aceptable$', '/procesamiento-de-datos$', '/ia$', '/login$', '/signup$', '/prueba$', '/divulgacion-responsable$', '/accesibilidad$', '/condiciones-comerciales$', '/licencia$', '/.well-known/', '/google*.html$', '/docs',
        '/sitemap.xml$', '/_next/', '/*.png', '/*.jpg', '/*.webp', '/*.svg', '/*.pdf'],
      disallow: ['/'],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}

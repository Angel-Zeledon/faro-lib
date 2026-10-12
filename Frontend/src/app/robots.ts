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
        // The English site (components/landing/sitePaths.ts), page by page.
        '/en$', '/en/pricing$', '/en/how-it-works$', '/en/faq$', '/en/security$', '/en/developers$', '/en/industries$', '/en/industries/consumer-goods$', '/en/industries/hardware$', '/en/industries/pharmacy$', '/en/industries/auto-parts$', '/en/industries/retail$', '/en/stockai-vs-excel$', '/en/how-it-is-calculated$', '/en/integrations$', '/en/whats-new$', '/en/what-is-stockai$', '/en/inventory-software-for-distributors$', '/en/how-to-calculate-the-reorder-point$', '/en/safety-stock$', '/en/demand-forecasting-for-purchasing$', '/en/privacy$', '/en/terms$', '/en/cookies$', '/en/legal-notice$', '/en/acceptable-use$', '/en/data-processing$', '/en/ai$', '/en/responsible-disclosure$', '/en/accessibility$', '/en/commercial-terms$', '/en/license$', '/en/legal$', '/en/docs',
        '/sitemap.xml$', '/_next/', '/*.png', '/*.jpg', '/*.webp', '/*.svg', '/*.pdf'],
      disallow: ['/'],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}

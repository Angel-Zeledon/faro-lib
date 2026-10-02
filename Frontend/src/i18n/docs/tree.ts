// The help center's map: sections, pages, their order and their URLs. A plain
// module (no 'use client', no copy) so the route, the sitemap and the search
// index can all read it. Slugs are Spanish, like every other public URL on the
// landing, and they are permanent: a slug is a link somebody has shared.
//
// Every page lives at /docs/<section>/<page>, except a section with a single
// page, which lives at /docs/<section> (Novedades).

export const DOCS_BASE = '/docs'

export const DOC_TREE = [
  {
    slug: 'primeros-pasos',
    pages: ['que-es-stockai', 'crear-tu-cuenta', 'subir-tus-ventas', 'configurar-inventario', 'tu-primera-orden', 'instalar-la-app'],
  },
  {
    slug: 'uso-diario',
    pages: ['panel-de-compras', 'pedidos', 'inventario', 'bodegas-y-traslados', 'proveedores', 'mensajes', 'alertas-diarias'],
  },
  {
    slug: 'analisis',
    pages: ['pronosticos', 'impacto', 'escenarios', 'historial', 'actividad'],
  },
  {
    slug: 'conceptos',
    pages: ['semaforo', 'punto-de-reorden', 'stock-de-seguridad', 'tiempo-de-entrega-aprendido', 'como-compiten-los-modelos', 'granularidad', 'en-camino'],
  },
  {
    slug: 'asistente',
    pages: ['asistente-ia', 'asistente-por-whatsapp', 'privacidad-del-asistente'],
  },
  {
    slug: 'administracion',
    pages: ['usuarios-y-roles', 'mi-cuenta', 'automatizacion', 'instalacion', 'limites-del-plan'],
  },
  {
    slug: 'integraciones',
    pages: ['api', 'mcp', 'fuentes-sql', 'whatsapp', 'correo'],
  },
  {
    slug: 'solucion-de-problemas',
    pages: ['archivo-rechazado', 'sin-datos', 'cuenta-y-acceso'],
  },
  {
    slug: 'novedades',
    pages: [],
  },
] as const

type Tree = typeof DOC_TREE
export type DocSectionSlug = Tree[number]['slug']

/** The page ids of one section: `<section>/<page>`, or `<section>` alone for a
 *  single-page section. */
export type DocPageIdOf<S extends DocSectionSlug> =
  Extract<Tree[number], { slug: S }>['pages'] extends readonly []
    ? S
    : `${S}/${Extract<Tree[number], { slug: S }>['pages'][number]}`

export type DocPageId = { [S in DocSectionSlug]: DocPageIdOf<S> }[DocSectionSlug]

/** Every page in reading order — the order of the sidebar and of prev/next. */
export const DOC_ORDER: DocPageId[] = DOC_TREE.flatMap(s =>
  s.pages.length === 0 ? [s.slug as DocPageId] : s.pages.map(p => `${s.slug}/${p}` as DocPageId),
)

export function docHref(id: DocPageId): string {
  return `${DOCS_BASE}/${id}`
}

export function sectionOf(id: DocPageId): DocSectionSlug {
  return id.split('/')[0] as DocSectionSlug
}

export function isDocPageId(s: string): s is DocPageId {
  return (DOC_ORDER as string[]).includes(s)
}

// The help center's own interface strings — the frame around the pages, not
// the pages. Both languages behind one interface, like LANDING: a missing
// translation does not build.
import type { Lang } from '@/i18n/translations'
import type { DocPageId, DocSectionSlug } from '@/i18n/docs/tree'

export interface DocsChromeCopy {
  home: string
  label: string
  title: string
  intro: string
  metaTitle: string
  sections: Record<DocSectionSlug, { title: string; blurb: string }>
  search: {
    open: string
    placeholder: string
    empty: string
    loading: string
    failed: string
    close: string
    results: string
  }
  onThisPage: string
  prev: string
  next: string
  menu: string
  closeMenu: string
  breadcrumb: string
  sidebarLabel: string
  popular: string
  popularPages: DocPageId[]
  manualTitle: string
  manualBody: string
  manualCta: string
  apiTitle: string
  apiBody: string
  apiCta: string
  openApp: string
  footerHead: string
  footerLinks: [string, string][]
  shotNote: string
}

export const DOCS_CHROME: Record<Lang, DocsChromeCopy> = {
  es: {
    home: 'Inicio',
    label: 'Centro de ayuda',
    title: 'Centro de ayuda de StockAI',
    intro:
      'Cómo usar StockAI de punta a punta: subir tus ventas, leer el semáforo, generar y recibir órdenes de compra, ' +
      'y entender de dónde sale cada número que te mostramos.',
    metaTitle: 'StockAI centro de ayuda: guías de uso paso a paso',
    sections: {
      'primeros-pasos': { title: 'Primeros pasos', blurb: 'De cuenta nueva a tu primer semáforo y tu primera orden de compra.' },
      'uso-diario': { title: 'Uso diario', blurb: 'Las pantallas de todos los días: compras, pedidos, inventario y proveedores.' },
      analisis: { title: 'Análisis', blurb: 'Pronósticos, escenarios, impacto e historial: para entender antes de comprar.' },
      conceptos: { title: 'Conceptos', blurb: 'El semáforo, el punto de reorden, el stock de seguridad y cómo compiten los modelos.' },
      asistente: { title: 'Asistente IA', blurb: 'Qué responde, qué datos lee y qué pasa con ellos.' },
      administracion: { title: 'Administración', blurb: 'Usuarios, roles, tu cuenta, automatización y los límites del plan gratis.' },
      integraciones: { title: 'Integraciones', blurb: 'API, MCP, fuentes SQL, WhatsApp y correo.' },
      'solucion-de-problemas': { title: 'Solución de problemas', blurb: 'Qué significa cada aviso y qué hacer para resolverlo.' },
      novedades: { title: 'Novedades', blurb: 'Lo que cambió en StockAI, de lo más reciente a lo más antiguo.' },
    },
    search: {
      open: 'Buscar en la ayuda',
      placeholder: 'Busca una pantalla, un aviso o un concepto…',
      empty: 'Nada coincide con esa búsqueda. Prueba con otra palabra, por ejemplo «semáforo» o «recibir».',
      loading: 'Cargando el índice…',
      failed: 'No se pudo cargar el índice de búsqueda. Recarga la página para intentarlo de nuevo.',
      close: 'Cerrar la búsqueda',
      results: 'Resultados',
    },
    onThisPage: 'En esta página',
    prev: 'Anterior',
    next: 'Siguiente',
    menu: 'Secciones de la ayuda',
    closeMenu: 'Cerrar las secciones',
    breadcrumb: 'Ruta de navegación',
    sidebarLabel: 'Secciones de la documentación',
    popular: 'Lo más consultado',
    popularPages: ['conceptos/semaforo', 'primeros-pasos/subir-tus-ventas', 'uso-diario/pedidos', 'solucion-de-problemas/sin-datos'],
    manualTitle: 'Manual en PDF',
    manualBody: 'La guía pantalla por pantalla, para imprimir o leer sin conexión.',
    manualCta: 'Descargar el manual',
    apiTitle: 'Referencia de la API',
    apiBody: 'Cada endpoint con ejemplos, para conectar tu ERP o tu sistema propio.',
    apiCta: 'Abrir la referencia',
    openApp: 'Abrir StockAI',
    footerHead: 'Ayuda',
    footerLinks: [
      ['/docs', 'Centro de ayuda'],
      ['/docs/primeros-pasos/que-es-stockai', 'Primeros pasos'],
      ['/docs/conceptos/semaforo', 'El semáforo'],
      ['/docs/solucion-de-problemas/archivo-rechazado', 'Solución de problemas'],
      ['/docs/novedades', 'Novedades'],
    ],
    shotNote: 'Captura de la pantalla real',
  },
  en: {
    home: 'Home',
    label: 'Help center',
    title: 'StockAI help center',
    intro:
      'How to use StockAI end to end: upload your sales, read the stock signal, create and receive purchase orders, ' +
      'and understand where every number we show you comes from.',
    metaTitle: 'Help center — StockAI',
    sections: {
      'primeros-pasos': { title: 'Getting started', blurb: 'From a new account to your first stock signal and your first purchase order.' },
      'uso-diario': { title: 'Everyday use', blurb: 'The screens you open every day: purchasing, orders, inventory and suppliers.' },
      analisis: { title: 'Analysis', blurb: 'Forecasts, scenarios, impact and history: to understand before you buy.' },
      conceptos: { title: 'Concepts', blurb: 'The stock signal, the reorder point, safety stock and how the models compete.' },
      asistente: { title: 'AI assistant', blurb: 'What it answers, which data it reads and what happens to that data.' },
      administracion: { title: 'Administration', blurb: 'Users, roles, your account, automation and the free plan limits.' },
      integraciones: { title: 'Integrations', blurb: 'API, MCP, SQL sources, WhatsApp and email.' },
      'solucion-de-problemas': { title: 'Troubleshooting', blurb: 'What each notice means and what to do about it.' },
      novedades: { title: "What's new", blurb: 'What changed in StockAI, newest first.' },
    },
    search: {
      open: 'Search the help center',
      placeholder: 'Search for a screen, a notice or a concept…',
      empty: 'Nothing matches that search. Try another word, for example "signal" or "receive".',
      loading: 'Loading the index…',
      failed: 'The search index could not be loaded. Reload the page to try again.',
      close: 'Close search',
      results: 'Results',
    },
    onThisPage: 'On this page',
    prev: 'Previous',
    next: 'Next',
    menu: 'Help sections',
    closeMenu: 'Close the sections',
    breadcrumb: 'Breadcrumb',
    sidebarLabel: 'Documentation sections',
    popular: 'Most read',
    popularPages: ['conceptos/semaforo', 'primeros-pasos/subir-tus-ventas', 'uso-diario/pedidos', 'solucion-de-problemas/sin-datos'],
    manualTitle: 'PDF manual',
    manualBody: 'The screen-by-screen guide, to print or read offline.',
    manualCta: 'Download the manual',
    apiTitle: 'API reference',
    apiBody: 'Every endpoint with examples, to connect your ERP or your own system.',
    apiCta: 'Open the reference',
    openApp: 'Open StockAI',
    footerHead: 'Help',
    footerLinks: [
      ['/docs', 'Help center'],
      ['/docs/primeros-pasos/que-es-stockai', 'Getting started'],
      ['/docs/conceptos/semaforo', 'The stock signal'],
      ['/docs/solucion-de-problemas/archivo-rechazado', 'Troubleshooting'],
      ['/docs/novedades', "What's new"],
    ],
    shotNote: 'Screenshot of the real screen',
  },
}

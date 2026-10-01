/**
 * Landing copy, in both languages.
 *
 * Why this is NOT in `translations.ts`: that catalogue is the APP's interface —
 * 3.000 short keys, flat, looked up by string at runtime. Marketing prose is a
 * different material. It comes in paragraphs, it belongs to whoever owns the
 * pitch rather than to whoever builds screens, and a typo in a key there fails
 * silently by echoing the key back. Here the shape is a TYPE, so a missing or
 * misspelled field is a compile error and the two languages physically cannot
 * drift apart — the guarantee `check_parity.py` has to run a script to give.
 *
 * The Spanish is the original, word for word. The English is a translation of
 * the pitch, not of the sentences: it keeps every claim exactly as narrow as
 * the Spanish makes it. Nothing here may promise something the product does not
 * do — the fifteen invented result percentages that used to sit in `CASES` were
 * removed on 2026-08-23 for exactly that reason, and they do not come back in
 * either language.
 *
 * Audience (owner's call, 2026-09-30): the page speaks to the BUYER — the
 * person who decides purchases at a distributor — in outcomes: what to order
 * today, how much, from whom, what money is stuck, what arrives when. Model
 * competition, backtesting, ABC-XYZ and the API/MCP are named only in `tech`,
 * the compact section near the bottom. Keep new copy on that side of the line.
 */
import type { Lang } from './translations'

export interface Titled { title: string; desc: string }
export interface Numbered { n: string; title: string; desc: string }
export interface Case { label: string; title: string; desc: string; does: string[] }
export interface Compare { feature: string; excel: string; stockai: string }
export interface Role { role: string; pain: string; gain: string }
export interface Include { title: string; desc: string; isNew: boolean }
export interface Signal { signal: string; rule: string; example: string }
export interface Faq { q: string; a: string }
export interface TourScreen { img: string; name: string; does: string; finds: string[]; alt: string }
export interface TourChapter { chapter: string; when: string; screens: TourScreen[] }

export interface LandingCopy {
  nav: { links: [string, string][]; signIn: string; signUp: string; menu: string }
  heroPills: string[]
  footerLinks: { product: [string, string][]; company: [string, string][] }
  // `ctaTrial` leads to /prueba: a throwaway account (temporary username and
  // password, 24 hours) for a visitor who wants to look before signing up.
  // `trialNote` is the one line under the buttons that says what that means.
  hero: { eyebrow: string; title1: string; title2: string; lead: string; cta: string; ctaTrial: string; trialNote: string; frame: string }
  strip: { models: string; deliveries: string; skus: string; csv: string }
  problem: { tag: string; title: string; lead: string; items: Titled[] }
  how: { tag: string; title: string; lead: string; steps: Numbered[] }
  decide: { tag: string; title: string; lead: string; formulaTitle: string; formulaBody: string; formulaBody2: string; leadTimeBody: string; signals: Signal[] }
  about: { tag: string; title: string; body1: string; body2: string }
  cases: { tag: string; title: string; lead: string; doesLabel: string; items: Case[] }
  compare: { tag: string; title: string; lead: string; head: [string, string, string]; rows: Compare[] }
  start: { tag: string; title: string; lead: string; needTitle: string; need: string[]; notNeedTitle: string; notNeed: string[] }
  pricing: {
    tag: string; title: string; lead: string
    freeLabel: string; freePrice: string; freeNote: string
    paidLabel: string; paidPrice: string; paidNote: string
    // [label, what the free tier gets, what the paid tier gets]. The paid
    // column used to render `unlimited` for every row, which told a paying
    // customer their upload size was uncapped when entitlements/plans.py
    // bounds it at 2000 MB — the one ceiling that survives on a paid tenant,
    // because an upload is read into memory before it is anything else.
    // Carrying both values per row makes the type refuse a row that does not
    // say what each tier actually gets.
    limits: [string, string, string][]
    closing: string; ctaSignup: string; ctaWhatsapp: string; ctaEmail: string
  }
  benefits: { tag: string; title: string; lead: string; items: string[] }
  includes:{ tag: string; title: string; lead: string; rolesTitle: string; roles: Role[]; itemsTitle: string; items: Include[]; isNew: string; tail: string; tailLink: string }
  // The one place the technical capabilities are named. Deliberately below the
  // fold and compact: the page speaks to the buyer (owner's call, 2026-09-30);
  // this block exists for whoever the buyer forwards the link to.
  tech: { tag: string; title: string; lead: string; items: Titled[] }
  tour: { title: string; lead: string; chapters: TourChapter[] }
  manual: { title: string; body: string; cta: string; note: string }
  faq: { tag: string; title: string; lead: string; cta: string; items: Faq[] }
  misc: {
    signalHead: [string, string, string]
    roleToday: string; roleWith: string
    exampleTitle: string; exampleBody: string
    leadTimeNote: string
    industriesLabel: string
  }
  footer: { tagline: string; product: string; company: string; contact: string; rights: string; madeIn: string }
}

/* Two capture sets, one per language: the tour shows the app running in the
   language the visitor is reading. `shots('en')` resolves to `-en.png`, which
   is a real run of the product with the interface in English and English demo
   data — not the Spanish screens with an English caption. Both sets live in
   `Frontend/public`; if you add a screen, add BOTH files or the English tour
   silently 404s an image. */
const SCREEN_KEYS = [
  'panel', 'inventory', 'pedidos', 'mensajes',
  'ventas', 'configurar', 'proveedores', 'scorecard',
  'forecast', 'pattern', 'escenarios', 'impacto', 'asistente', 'historial',
  'usuarios', 'cuenta', 'automatizacion', 'api',
] as const

type ScreenKey = (typeof SCREEN_KEYS)[number]

function shots(lang: Lang): Record<ScreenKey, string> {
  const suffix = lang === 'en' ? '-en' : ''
  return Object.fromEntries(
    SCREEN_KEYS.map(k => [k, `/shot-${k}${suffix}.png`]),
  ) as Record<ScreenKey, string>
}

const SHOTS = shots('es')
const SHOTS_EN = shots('en')

const es: LandingCopy = {
  nav: {
    links: [
      ['#problema', 'El problema'],
      ['#solucion', 'Cómo funciona'],
      ['#casos', 'Industrias'],
      ['#incluye', 'Qué incluye'],
      ['#precio', 'Precio'],
      ['#empezar', 'Contacto'],
    ],
    signIn: 'Iniciar sesión',
    signUp: 'Crear cuenta',
    menu: 'Menú',
  },
  heroPills: ['Distribución', 'Retail', 'Manufactura', 'Mayoristas', 'E-commerce'],
  footerLinks: {
    product: [['#solucion', 'Cómo funciona'], ['#casos', 'Industrias'], ['#incluye', 'Qué incluye'], ['#precio', 'Precio'], ['#comparacion', 'vs Excel']],
    company: [['#problema', 'El problema'], ['#nosotros', 'Nosotros'], ['mailto:hola@usefaro.io', 'Contacto']],
  },
  hero: {
    eyebrow: 'Para quien decide las compras',
    title1: 'Qué pedir hoy, cuánto',
    title2: 'y a qué proveedor.',
    lead: 'StockAI lee tus ventas y tu inventario y cada mañana te dice qué productos se van a quebrar, cuántas unidades pedir de cada uno y a quién, cuánto dinero tienes parado en lo que no rota y qué pedidos vienen en camino.',
    cta: 'Empezar gratis con datos de ejemplo',
    ctaTrial: 'Probar sin registrarme',
    trialNote: 'Cuenta de prueba al instante: usuario y contraseña temporales, 24 horas, sin tarjeta.',
    frame: 'StockAI · Panel de compras',
  },
  strip: {
    models: 'Estados por producto: pedir ya, pedir pronto, ok o sobrestock',
    deliveries: 'Entregas para aprender el plazo real de un proveedor',
    skus: 'Productos por catálogo con los que se pone a prueba',
    csv: 'Lo único que necesitas para empezar',
  },
  problem: {
    tag: 'El problema',
    title: 'El inventario mal planificado tiene un costo concreto.',
    lead: 'La mayoría de empresas toma decisiones de compra con Excel, intuición acumulada y el criterio del comprador de turno. Eso funciona hasta cierto punto — y ese punto llega antes de lo que parece.',
    items: [
      { title: 'Ruptura de stock en temporadas clave', desc: 'En retail y distribución, un quiebre durante temporada alta no es solo una venta perdida — el cliente va a la competencia y no regresa. La demanda no espera al próximo ciclo de reposición.' },
      { title: 'Capital atrapado en sobreinventario', desc: 'Para mayoristas y manufactureros, el exceso de inventario ocupa bodega, consume línea de crédito y en categorías perecederas o de moda, termina en pérdida directa por liquidación.' },
      { title: 'Compras reactivas en lugar de planificadas', desc: 'Comprar cuando el inventario ya está crítico obliga a aceptar condiciones desfavorables: precios spot, fletes de emergencia y tiempos de entrega fuera del ciclo normal.' },
      { title: 'Conocimiento concentrado en una sola persona', desc: 'El comprador más experimentado lleva en la cabeza la estacionalidad, los ciclos del proveedor y las anomalías históricas de cada producto. Ese conocimiento no está en ningún sistema.' },
      { title: 'Una hoja de cálculo no alcanza para todo el catálogo', desc: 'Con pocos productos, revisar fila por fila funciona. Cuando el catálogo llega a cientos o miles de códigos, la mayoría se termina pidiendo por costumbre: lo mismo del mes pasado, más un poco.' },
    ],
  },
  how: {
    tag: 'Cómo funciona',
    title: 'De tu historial de ventas a la orden de compra.',
    lead: 'Subes lo que ya tienes y StockAI te devuelve la lista de compras. No configuras nada estadístico y no necesitas un analista.',
    steps: [
      { n: '01', title: 'Sube tus ventas y tu inventario', desc: 'Un archivo CSV o Excel, tal como sale de tu sistema. StockAI reconoce solo cuál columna es la fecha, cuál el producto y cuál la cantidad vendida.' },
      { n: '02', title: 'StockAI aprende cómo se vende cada producto', desc: 'Temporadas, quincenas, tendencia y altibajos, producto por producto. Tú no configuras nada por código.' },
      { n: '03', title: 'Te dice qué pedir hoy y cuánto', desc: 'Cada producto queda en un estado — PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK — con la cantidad sugerida calculada contra el plazo de su proveedor.' },
      { n: '04', title: 'Pides, recibes y el sistema aprende', desc: 'La orden sale armada por proveedor. Cuando registras la llegada, StockAI anota cuánto tardó de verdad y lo usa para la siguiente.' },
    ],
  },
  decide: {
    tag: 'La regla, sin misterio',
    title: 'Cómo decide StockAI que un producto está en rojo.',
    lead: 'Ninguna recomendación sale de una caja negra. Todo el semáforo se apoya en una sola cuenta, y la puedes hacer a mano para comprobar que da lo mismo.',
    formulaTitle: 'La cuenta',
    formulaBody: 'Cobertura = existencias ÷ demanda diaria pronosticada. Eso te da cuántos días aguantas si no llega nada más. Esa cifra se compara contra el plazo de tu proveedor: los días que tarda en entregarte desde que le pasas la orden.',
    formulaBody2: 'La lógica es la que ya usas de cabeza, solo que aplicada a los miles de códigos que no alcanzas a revisar: si aguantas menos de lo que tarda en llegar, vas tarde.',
    leadTimeBody: 'El plazo de entrega es la mitad de la cuenta, así que conviene que sea el real. Cada vez que registras una recepción, StockAI guarda cuántos días pasaron de verdad — y a partir de la tercera empieza a planificar con ese promedio en lugar del que te prometieron.',
    signals: [
      { signal: 'PEDIR YA', rule: 'La cobertura no llega ni a la mitad del plazo del proveedor', example: 'Menos de 7,5 días · menos de 150 unidades' },
      { signal: 'PEDIR PRONTO', rule: 'La cobertura es menor a 1,2 veces el plazo', example: 'Entre 7,5 y 18 días · 150 a 360 unidades' },
      { signal: 'OK', rule: 'La cobertura va de 1,2 a 3 veces el plazo', example: 'Entre 18 y 45 días · 360 a 900 unidades' },
      { signal: 'SOBRESTOCK', rule: 'La cobertura es de 3 veces el plazo o más', example: '45 días o más · más de 900 unidades' },
    ],
  },
  about: {
    tag: 'Nosotros',
    title: 'Construido para quien decide las compras.',
    body1: 'StockAI nace para que los distribuidores, comercios y mayoristas de Latinoamérica dejen de comprar inventario a ciegas. La mayoría compra con Excel e intuición porque las herramientas de pronóstico se hicieron para grandes empresas con equipos de datos — no para una operación que maneja miles de productos con un equipo pequeño.',
    body2: 'StockAI toma el historial de ventas que ya tienes (un CSV o Excel) y lo convierte en decisiones concretas: qué pedir, cuánto, a quién y cuándo. Sin que necesites un analista. Hecho en Costa Rica, pensado para la realidad de las PyMEs de la región.',
  },
  cases: {
    tag: 'Industrias',
    title: 'Diseñado para operaciones reales.',
    lead: 'El problema de inventario no es el mismo en un mayorista que en un retailer o en una planta de producción. StockAI se adapta a las características de cada operación.',
    doesLabel: 'Lo que StockAI hace en',
    items: [
      {
        label: 'Retail',
        title: 'Gestión de inventario por tienda y categoría',
        desc: 'Un retailer con varios puntos de venta enfrenta una demanda distinta en cada ubicación, categorías con temporadas diferentes y un ciclo de reposición que no puede fallar. StockAI calcula qué pedir para cada tienda y cada producto, avisa cuando la venta de algo cambia de rumbo y te deja preparar las temporadas altas con tiempo.',
        does: [
          'Semáforo por producto y por tienda: PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK, medido contra el plazo de cada proveedor.',
          'Avisa cuando la venta de un producto cambia de rumbo, para que no sigas pidiendo lo mismo del mes pasado.',
          'Recálculo programado — cada lunes, todos los días o el primero de mes — sin que nadie tenga que lanzarlo.',
        ],
      },
      {
        label: 'Distribuidores',
        title: 'Reposición optimizada y menos emergencias',
        desc: 'Los distribuidores trabajan con márgenes ajustados, proveedores con plazos variables y clientes que no toleran faltantes. El error de inventario se paga caro: un cliente insatisfecho migra. StockAI calcula el punto de reorden correcto para cada producto según su velocidad de venta real y el lead time del proveedor, reduciendo las compras de emergencia.',
        does: [
          'Punto de reorden por producto, con el plazo real del proveedor: a partir de la tercera recepción registrada deja de usar el prometido.',
          'La orden de compra sale armada por proveedor, con cantidad sugerida y el motivo de cada línea.',
          'Resumen diario de los productos que entran en riesgo, al correo y al WhatsApp de quien decide.',
        ],
      },
      {
        label: 'Mayoristas',
        title: 'Balance de inventario entre bodegas',
        desc: 'Los mayoristas compran en volumen para obtener mejores precios, pero esa ventaja desaparece cuando el inventario no rota o está mal distribuido. StockAI identifica qué productos tienen exceso antes de que llegue la fecha de vencimiento o se vuelvan obsoletos, y señala qué referencias priorizar en la siguiente orden.',
        does: [
          'SOBRESTOCK se marca apenas la cobertura pasa de tres veces el plazo del proveedor, no cuando ya toca liquidar.',
          'Traslado entre bodegas en lugar de compra — y solo se propone si a la bodega que presta le quedan al menos 30 días de cobertura.',
          'Escalas de precio por volumen: cuánto falta para el siguiente escalón y cuánto ahorras al llegar.',
        ],
      },
      {
        label: 'Manufactura',
        title: 'Planificación de producción y materias primas',
        desc: 'Una línea de producción parada por falta de material tiene un costo que va mucho más allá del material: horas hombre perdidas, penalizaciones por entrega tardía y clientes que pierden confianza. StockAI convierte el pronóstico de demanda del producto terminado en un plan de requerimientos de materias primas, considerando tiempos de producción y plazos de proveedores.',
        does: [
          'Lista de materiales por producto terminado: la venta esperada se convierte en cuánto comprar de componentes, materias primas y empaque.',
          'Los faltantes de material se ven contra el plan, no el día que la línea se detiene.',
          'El plazo de cada proveedor de insumos se corrige solo con cada recepción que registras.',
        ],
      },
      {
        label: 'E-commerce',
        title: 'Preparación para picos de demanda',
        desc: 'En e-commerce, llegar sin inventario a un Black Friday o campaña de descuentos es dejar dinero sobre la mesa. Llegar con demasiado significa capital atrapado y liquidación a pérdida. StockAI analiza el comportamiento histórico durante eventos promocionales y genera estimaciones para los próximos picos con tiempo suficiente para hacer pedidos.',
        does: [
          'Simulador de escenarios: duplicar la demanda de una categoría, marcar una promoción o atrasar a un proveedor, y comparar contra la base sin tocar nada real.',
          'Las temporadas de cada producto se detectan solas, sin configurar nada por código.',
          'Te señala en qué productos — los que más venden y más varían — conviene el colchón de seguridad antes del pico, en vez de repartirlo parejo.',
        ],
      },
    ],
  },
  compare: {
    tag: 'Comparación',
    title: 'Excel vs StockAI.',
    lead: 'Excel sirve para anotar, no para decirte qué pedir. Funciona con unos pocos productos. El problema aparece cuando el negocio crece y la hoja deja de alcanzar.',
    head: ['Lo que necesitas saber', 'Excel', 'StockAI'],
    rows: [
      { feature: 'Qué pedir hoy', excel: 'Fila por fila', stockai: 'Lista por urgencia' },
      { feature: 'Cuánto pedir de cada producto', excel: 'A criterio', stockai: 'Cantidad sugerida' },
      { feature: 'Productos que alcanzas a revisar', excel: 'Decenas', stockai: 'Todo el catálogo' },
      { feature: 'Cuánto tarda de verdad cada proveedor', excel: 'De memoria', stockai: 'Medido' },
      { feature: 'Aviso de lo que se va a quebrar', excel: 'No disponible', stockai: 'Diario' },
      { feature: 'Dinero parado en sobrestock', excel: 'Difícil de ver', stockai: 'Marcado' },
      { feature: 'Orden de compra por proveedor', excel: 'A mano', stockai: 'Armada' },
      { feature: 'Temporadas de cada producto', excel: 'A mano', stockai: 'Automáticas' },
    ],
  },
  start: {
    tag: 'Antes de empezar',
    title: 'Qué necesitas para arrancar — y qué no.',
    lead: 'La razón más común por la que una herramienta así se queda sin usar no es el precio: es descubrir, tres semanas después, que hacía falta un proyecto de datos antes de poder abrirla. Esta es la lista completa, para que la revises ahora.',
    needTitle: 'Lo que sí necesitas',
    need: [
      'Un archivo CSV o Excel con tu historial de ventas.',
      'Tres columnas como mínimo: fecha, código de producto y cantidad vendida.',
      'Idealmente 12 meses o más, para que se alcance a ver la estacionalidad completa.',
      'Las existencias actuales, para que el semáforo tenga contra qué comparar.',
      'El plazo de entrega aproximado de cada proveedor. Aproximado basta: se corrige solo.',
    ],
    notNeedTitle: 'Lo que no necesitas',
    notNeed: [
      'No necesitas conectar tu ERP para empezar: exportas de tu sistema y subes el archivo.',
      'No necesitas configurar un modelo por producto ni saber qué es una serie de tiempo.',
      'No necesitas una persona de tecnología dedicada.',
      'No necesitas el catálogo limpio ni completo: los productos con poco historial no frenan la corrida — quedan fuera del pronóstico y el resto se entrena igual.',
      'No necesitas un mínimo de productos. Funciona igual con 80 códigos que con 4.000.',
    ],
  },
  pricing: {
    tag: 'Precio',
    title: 'Gratis de verdad. Y con todo adentro.',
    lead: 'No hay funciones que se desbloqueen pagando. El semáforo, las órdenes de compra, las bodegas, el optimizador, el simulador, las alertas, el analista y la API vienen completos desde el primer día — también en el plan gratis. Lo único que cambia al pagar es cuánto cabe.',
    freeLabel: 'Gratis',
    freePrice: '$0',
    freeNote: 'Para siempre, sin tarjeta. Suficiente para que un negocio chico opere de verdad.',
    paidLabel: 'Completo',
    paidPrice: 'Hablemos',
    paidNote: 'El precio se arma sobre tu operación: cuántos productos mueves, cuántas bodegas y qué tan seguido recalculas. No hay checkout — escríbenos y lo vemos con números tuyos.',
    limits: [
      ['Productos (SKUs)', '100', 'Sin límite'],
      ['Usuarios', '2', 'Sin límite'],
      ['Bodegas', '1', 'Sin límite'],
      ['Pronósticos guardados', '3', 'Sin límite'],
      ['Llaves de API', '1', 'Sin límite'],
      ['Tamaño de archivo', '25 MB', '2 GB'],
    ],
    closing: 'Empieza gratis hoy. Cuando te quede corto — un catálogo que creció, una segunda bodega, un tercero en el equipo — escríbenos y lo ampliamos. Te respondemos en menos de 24 horas.',
    ctaSignup: 'Crear mi cuenta gratis',
    ctaWhatsapp: 'Escríbenos por WhatsApp',
    ctaEmail: 'Escríbenos por correo',
  },
  benefits: {
    tag: 'Cada mañana',
    title: 'Lo que sabes al abrir StockAI.',
    lead: 'Sin armar reportes ni cruzar hojas de cálculo. Está en pantalla cuando llegas, y el resumen de lo urgente ya te llegó al correo y al WhatsApp.',
    items: [
      'Qué pedir hoy, ordenado por urgencia',
      'Cuántas unidades pedir de cada producto',
      'La orden de compra armada por proveedor',
      'Qué pedidos vienen en camino y cuáles ya debían haber llegado',
      'Cuánto tarda de verdad cada proveedor, medido en tus recepciones',
      'Cuánto dinero está parado en productos que no rotan',
      'Qué productos entran en riesgo, por correo y WhatsApp',
      'Qué pasa si una promoción duplica la venta, antes de comprometer el dinero',
      'Todo el catálogo, no una muestra — se pone a prueba con más de 5.000 productos',
      'Los reportes en Excel y PDF, listos para compartir',
    ],
  },
  includes: {
    tag: 'Qué incluye',
    title: 'Lo que resuelve, en detalle.',
    lead: 'Dos problemas aparecen apenas la operación crece: no poder mirar todos los productos, y tener el inventario repartido en varios lugares. Esto es lo que StockAI pone del lado de ambos — y no hay que activar nada, viene incluido.',
    rolesTitle: 'A quién le resuelve algo, y qué',
    roles: [
      {
        role: 'Dueño o gerente general',
        pain: 'Te enteras del quiebre cuando te llama el vendedor, y del sobrestock cuando ves cuánta plata hay parada en bodega.',
        gain: 'Un resumen diario de los productos en rojo, al correo y al WhatsApp. Y un simulador para probar «¿qué pasa si la promoción duplica la venta de esta categoría?» antes de comprometer el dinero.',
      },
      {
        role: 'Encargado de compras',
        pain: 'Revisas miles de códigos en una hoja de cálculo y terminas comprando por costumbre: lo mismo del mes pasado, más un poco.',
        gain: 'La lista llega ordenada por urgencia, con la cantidad sugerida por proveedor. El optimizador arma el pedido tomando en cuenta lo que cuesta tener inventario parado, lo que cuesta quedarse sin producto y el flete fijo del camión.',
      },
      {
        role: 'Jefe de bodega',
        pain: 'Anotas las recepciones en un cuaderno, y nadie en la empresa sabe cuánto tarda de verdad cada proveedor.',
        gain: 'Cada recepción que registras se vuelve dato. A partir de la tercera entrega de un proveedor, StockAI deja de usar el plazo que te prometieron y empieza a usar el que cumplen.',
      },
      {
        role: 'Administración y finanzas',
        pain: 'Sabes cuánto vale el inventario, pero no cuánto de eso es capital atrapado en productos que no rotan.',
        gain: 'StockAI separa lo que mueve tu venta de lo que solo ocupa espacio, y los reportes que exportas a Excel y PDF salen con esa marca en cada producto.',
      },
    ],
    itemsTitle: 'Qué incluye, concretamente',
    items: [
      { title: 'Dónde poner el colchón de seguridad', desc: 'StockAI separa los productos que concentran el 80 % de tu venta de la cola larga, y los de venta estable de los erráticos. Donde se juntan mucha venta y demanda impredecible es donde conviene el stock de seguridad, en vez de repartirlo parejo en todo el catálogo.', isNew: false },
      { title: 'Mover entre bodegas antes de comprar', desc: 'Las ubicaciones que necesites, con rutas entre ellas: días de tránsito y costo. Cuando un producto está corto en una bodega y sobrado en otra, StockAI propone mover en vez de comprar — y solo lo propone si a la bodega que presta le quedan al menos 30 días de cobertura.', isNew: false },
      { title: 'El pedido más barato, no el más chico', desc: 'Arma el pedido buscando el menor costo total, no la menor cantidad de unidades: suma el costo de mantener inventario, la penalización por quedarse sin producto, el costo de compra, el costo por unidad transferida y el costo fijo del envío, que se paga una sola vez aunque el camión lleve veinte productos.', isNew: false },
      { title: 'Prueba la decisión antes de pagarla', desc: 'Hasta 50 reglas por escenario: multiplicar la demanda, marcar una promoción, atrasar a un proveedor o cambiar el stock de seguridad, filtrando por producto, categoría, proveedor o rango de fechas. Compara el escenario contra la base sin tocar nada de lo real, y lo puedes guardar para volver a correrlo.', isNew: false },
      { title: 'Lo urgente llega a tu teléfono', desc: 'El mismo resumen diario de productos en riesgo que llega por correo, ahora al teléfono de quien decide. Cada persona vincula y verifica su propio número desde su configuración.', isNew: false },
      { title: 'Pregúntale a tu inventario', desc: 'Preguntas en español sobre tus propios datos — «¿por qué subió la demanda de esta categoría?», «¿qué proveedores me están atrasando?» — y cada respuesta viene marcada con de dónde salió, para que sepas cuándo se apoya en tus datos y cuándo no.', isNew: false },
      { title: 'La lista se actualiza sola', desc: 'En vez de acordarte de recalcular, lo dejas corriendo solo: cada lunes a las 6, todos los días, solo días hábiles, cada hora o el primero de cada mes. La pantalla te muestra cuándo corrió, cuándo vuelve a correr y si falló.', isNew: false },
      { title: 'Tu equipo habla al lado del inventario', desc: 'Conversaciones uno a uno entre las personas de tu empresa, dentro de StockAI, al lado del inventario del que están hablando. Si la otra persona no está conectada, le llega un aviso a su WhatsApp para que no se pierda el mensaje.', isNew: true },
    ],
    isNew: 'Nuevo',
    tail: 'Todo lo anterior va además de la base: el semáforo, las órdenes de compra, las recepciones que aprenden el plazo del proveedor, los reportes y las alertas por correo — todo eso también en el plan gratis. ',
    tailLink: 'Hablemos del precio →',
  },
  manual: {
    title: 'O léelo entero, con calma.',
    body: 'El manual de usuario cubre las diecinueve pantallas con el mismo detalle: para qué sirve cada una, qué significa cada dato, cómo hacer las cosas concretas y lo que suele confundir. Es el mismo producto de estas capturas, no una versión resumida.',
    cta: 'Descargar el manual (PDF)',
    note: 'PDF · español · 59 páginas',
  },
  tech: {
    tag: 'Para tu equipo técnico',
    title: 'Lo que hay debajo, en corto.',
    lead: 'Nada de esto hace falta para comprar mejor. Está aquí para quien quiera revisarlo.',
    items: [
      { title: 'Modelos que compiten por producto', desc: 'Nueve modelos se prueban sobre el historial de cada producto y se queda el que menos se equivoca.' },
      { title: 'Precisión y backtesting por modelo', desc: 'El error de cada modelo, medido sobre tu propio historial y visible en la pantalla de pronóstico.' },
      { title: 'Clasificación ABC-XYZ', desc: 'ABC por peso en la venta, XYZ por estabilidad de la demanda. Es lo que ubica el colchón de seguridad.' },
      { title: 'API pública y servidor MCP', desc: 'API REST con llaves por empresa, y un servidor MCP de solo lectura para asistentes de IA. Mismas llaves y mismos límites.' },
    ],
  },
  tour: {
    title: 'Pantalla por pantalla.',
    lead: 'Capturas reales de la aplicación con datos dentro. Los capítulos son los mismos grupos del menú de StockAI, en el mismo orden: es el mapa que vas a tener cinco minutos después de entrar.',
    chapters: [
      {
        chapter: 'Operación diaria',
        when: 'Lo que abres cada mañana, antes del café.',
        screens: [
          { img: SHOTS.panel, name: 'Panel de compras', does: 'Lo primero que ves al entrar. Reúne en una pantalla lo único que hay que decidir hoy: qué está por quebrarse, qué pedidos vienen en camino y por dónde empezar.', finds: ['Cuántos productos están en riesgo hoy y cuántos esta semana', 'Un resumen escrito con los riesgos, las oportunidades y las acciones del día', 'Los pedidos que ya debían haber llegado, para registrar la entrada'], alt: 'Panel de compras de StockAI: indicadores de SKUs monitoreados, riesgo, precisión y valor de inventario, y un resumen ejecutivo con riesgos, oportunidades y acciones para hoy.' },
          { img: SHOTS.inventory, name: 'Inventario', does: 'Todo tu catálogo en cuatro estados, y para cada producto la cantidad a pedir ya calculada contra el plazo real de su proveedor. No hay que interpretar nada.', finds: ['El semáforo: PEDIR YA, PEDIR PRONTO, OK y SOBRESTOCK', 'Una pestaña por bodega, y la vista consolidada', 'La orden de compra lista para exportar en CSV o PDF'], alt: 'Tabla de inventario de StockAI con semáforo de colores por SKU y bodega, la cantidad a pedir y el proveedor de cada producto.' },
          { img: SHOTS.pedidos, name: 'Pedidos', does: 'Las órdenes que generaste, desde que salen hasta que llegan. Cada recepción que registras le enseña a StockAI cuánto tarda de verdad ese proveedor.', finds: ['Órdenes en camino, parciales y recibidas, con su valor', 'Enviar el pedido al proveedor por WhatsApp o correo', 'Registrar la llegada, completa o parcial'], alt: 'Pantalla de pedidos de StockAI con órdenes de compra en camino, parciales y recibidas, con envío por WhatsApp y registro de llegada.' },
          { img: SHOTS.mensajes, name: 'Mensajes', does: 'Conversaciones uno a uno dentro de StockAI, sobre los productos que ambos están viendo. La decisión y la conversación no viven en dos aplicaciones distintas.', finds: ['Un hilo por persona del equipo', 'Aviso por WhatsApp o SMS cuando llega un mensaje'], alt: 'Mensajería interna de StockAI: una conversación entre el comprador y la analista sobre un producto en riesgo de quiebre.' },
        ],
      },
      {
        chapter: 'Tus datos',
        when: 'Lo que preparas una vez, y ajustas cuando cambia algo.',
        screens: [
          { img: SHOTS.ventas, name: 'Mis ventas', does: 'Subes el archivo que ya tienes. Con fecha, producto y cantidad basta — StockAI revisa el archivo antes de entrenar y te dice qué encontró.', finds: ['CSV o Excel, tal como sale de tu sistema', 'Hasta cuándo quieres planificar y con qué nivel de detalle', 'El calendario de tu país, para que las quincenas y feriados cuenten'], alt: 'Pantalla de carga de ventas de StockAI: el archivo, el horizonte de planificación, el nivel de detalle y el país.' },
          { img: SHOTS.configurar, name: 'Configurar inventario', does: 'Las tres cosas que StockAI necesita saber de cada producto: cuánto tienes, cuánto cuesta y cuánto tarda en llegar. Te dice exactamente qué pasa si falta alguna.', finds: ['Cargar el stock a mano o desde el archivo de tu sistema', 'Reglas por proveedor o por categoría, en vez de producto por producto', 'Qué productos quedan fuera del semáforo y por qué'], alt: 'Pantalla de configuración de inventario de StockAI, explicando qué pasa si falta el stock, el costo o los días de entrega de un producto.' },
          { img: SHOTS.proveedores, name: 'Proveedores', does: 'La ficha de cada proveedor: cómo contactarlo, cuánto dice que tarda y en cuántas entregas va StockAI para aprender cuánto tarda de verdad.', finds: ['Plazo declarado, términos de pago y datos de contacto', 'Escalas de precio por volumen, para saber cuándo conviene subir la orden', 'El avance del aprendizaje del plazo, entrega por entrega'], alt: 'Lista de proveedores de StockAI con sus plazos de entrega, términos de pago y el estado del aprendizaje del plazo real.' },
          { img: SHOTS.scorecard, name: 'Scorecard de proveedores', does: 'Lo que dijeron contra lo que hicieron. StockAI compara el plazo declarado con el que midió en tus propias recepciones, y planifica con el segundo.', finds: ['Plazo real aprendido, frente al declarado', 'Porcentaje de entregas a tiempo y qué tan completas llegaron', 'Cuánto le has comprado a cada uno'], alt: 'Scorecard de proveedores de StockAI comparando el plazo declarado contra el plazo real aprendido de las recepciones registradas.' },
        ],
      },
      {
        chapter: 'Análisis',
        when: 'Cuando quieres entender el porqué, o probar una decisión antes de tomarla.',
        screens: [
          { img: SHOTS.forecast, name: 'Pronóstico por producto', does: 'Cuánto esperas vender de cada producto y en qué rango, para saber cuánto confiar en la cantidad sugerida antes de pedir.', finds: ['Histórico, pronóstico y el rango de venta probable', 'Qué tanto se equivoca el pronóstico, para saber cuánto confiar', 'Diario, semanal, mensual o trimestral, según cómo compres'], alt: 'Gráfico de pronóstico por SKU de StockAI: ventas históricas, pronóstico y el rango de venta probable, con la comparación entre modelos.' },
          { img: SHOTS.pattern, name: 'Cómo se vende cada producto', does: 'Separa lo que de verdad está creciendo de lo que es solo el patrón de la semana repitiéndose. Es la diferencia entre una tendencia y un lunes.', finds: ['La venta con el sube y baja de siempre ya descontado', 'Cuánto del movimiento explica el día de la semana', 'El promedio por día, para ver dónde está el pico'], alt: 'Pantalla de StockAI que separa la tendencia real de un producto del patrón que se repite cada semana.' },
          { img: SHOTS.escenarios, name: 'Simulador de escenarios', does: '¿Qué pasa si vendes 40% más, si tu proveedor se atrasa una semana, o si haces promoción en diciembre? Lo ves antes de comprometerte, producto por producto.', finds: ['El plan actual y el del escenario, lado a lado', 'Qué productos cambian de estado y cuánto cambia la orden', 'Escenarios guardados, para volver a correrlos'], alt: 'Simulador de escenarios de StockAI comparando el plan actual contra un escenario de mayor demanda, producto por producto.' },
          { img: SHOTS.impacto, name: 'Impacto', does: 'Qué hiciste con StockAI este mes, con las cifras que salen de tus propios registros. No estima ahorros ni cuenta quiebres evitados, porque eso no se puede medir con certeza.', finds: ['Cuántas órdenes generaste y cuántas recomendaciones seguiste', 'De dónde sale cada número, dicho sin adornos', 'El mismo resumen te llega por correo el primer día del mes'], alt: 'Pantalla de impacto de StockAI con el resumen mensual de lo que se hizo con la herramienta y de dónde sale cada cifra.' },
          { img: SHOTS.asistente, name: 'Analista IA', does: 'Preguntas en español sobre tu propio inventario y te responde con tus cifras. No es un chatbot genérico: lee el mismo semáforo que ves en pantalla.', finds: ['Cuántos productos hay en cada estado, y cuáles son', 'Qué revisar esta semana y a qué proveedor contactar primero', 'Preguntas sugeridas, si no sabes por dónde empezar'], alt: 'Conversación con el analista de IA de StockAI respondiendo sobre los productos en riesgo y el capital inmovilizado, con cifras del propio inventario.' },
          { img: SHOTS.historial, name: 'Historial', does: 'Cada vez que subes ventas nuevas queda una sesión. Puedes volver a cualquiera, compararlas y ver con cuál está calculando el semáforo hoy.', finds: ['Todas tus corridas, con su fecha y su granularidad', 'Cuál es la sesión activa'], alt: 'Historial de sesiones de pronóstico de StockAI, con la sesión activa y las anteriores.' },
        ],
      },
      {
        chapter: 'Tu cuenta y tu equipo',
        when: 'Lo que tocas de vez en cuando.',
        screens: [
          { img: SHOTS.usuarios, name: 'Equipo y permisos', does: 'Invitas a tu gente con el permiso que le corresponde. Quien solo mira, solo mira: los tres roles son los mismos que respeta la API.', finds: ['Administrador, analista y solo lectura', 'Invitación por correo, sin que tengas que inventar contraseñas'], alt: 'Pantalla de usuarios de StockAI con los roles administrador, analista y solo lectura.' },
          { img: SHOTS.cuenta, name: 'Mi cuenta', does: 'Tu perfil, la moneda en la que quieres ver tus cifras, el idioma, la zona horaria — y cuánto espacio te queda en tu plan.', finds: ['Uso contra cada límite, para verlo venir antes de topar', 'Moneda y zona horaria, que afectan a todo lo demás', 'Tu historial de actividad en la cuenta'], alt: 'Pantalla de cuenta de StockAI mostrando el plan, el uso contra cada límite y las formas de contactarnos para ampliarlo.' },
          { img: SHOTS.automatizacion, name: 'Automatización', does: 'Para que StockAI recalcule solo. Programas cada cuánto y a qué hora, y generas las llaves que usa tu propio sistema para entrar.', finds: ['Recálculo programado: cada lunes, todos los días o el primero de mes', 'Llaves de API, que se muestran una sola vez'], alt: 'Pantalla de automatización de StockAI con las llaves de API y los recálculos programados.' },
          { img: SHOTS.api, name: 'API pública', does: 'Tu sistema empuja los datos y se lleva la decisión, sin que nadie abra StockAI. La documentación viene dentro, con tus propios datos para probar.', finds: ['Los endpoints que tu ERP necesita, con ejemplos listos para copiar', 'Autenticación, límites y el formato de las respuestas'], alt: 'Documentación de la API pública de StockAI con la URL base, la autenticación, los límites y los endpoints.' },
        ],
      },
    ],
  },
  faq: {
    tag: 'Preguntas frecuentes',
    title: 'Respuestas a las dudas más comunes.',
    lead: 'Si tienes alguna pregunta que no está aquí, escríbenos directamente. Respondemos en menos de 24 horas.',
    cta: 'Escríbenos →',
    items: [
      { q: '¿Necesito conocimientos estadísticos o de programación para usar StockAI?', a: 'No. StockAI está hecho para quien compra: abres la pantalla y ves qué pedir hoy, cuánto y a qué proveedor. No hay modelos que configurar ni código. Subes tus datos y el resto lo hace el sistema.' },
      { q: '¿En qué formato debo tener mis datos de ventas?', a: 'StockAI acepta archivos Excel (.xlsx) y CSV. El archivo debe tener al menos una columna de fecha, una columna de identificador del producto (SKU o nombre) y una columna de cantidad vendida. El sistema detecta automáticamente qué columna es cuál.' },
      { q: '¿Qué pasa si tengo productos con muy pocas ventas históricas o datos incompletos?', a: 'StockAI necesita al menos 20 períodos de historial por producto para entrenarlo. Los que no llegan a ese mínimo quedan fuera del pronóstico: no se les inventa una proyección. Antes de correr nada, la revisión del archivo te dice cuántos productos están por debajo del umbral, y si ninguno lo alcanza el archivo se detiene con la explicación en pantalla en vez de producir un resultado vacío. Esos productos siguen apareciendo en tu inventario marcados SIN DATOS — sin señal ni cantidad sugerida — para que la decisión sea tuya y no de un número inventado.' },
      { q: '¿Mis datos están seguros? ¿Quién tiene acceso a ellos?', a: 'Los datos que subes a StockAI son exclusivamente tuyos: no se comparten con terceros ni se usan para entrenar modelos de otras empresas — cada pronóstico se entrena únicamente con el historial de tu propia cuenta. Cada consulta va filtrada por empresa y el acceso se controla por rol: administrador, analista o solo lectura. Las credenciales de tus integraciones — el usuario y la contraseña de tu base de datos — se guardan cifradas. Tus archivos de ventas y los modelos entrenados se guardan en el servidor de StockAI, en una carpeta separada por empresa; el cifrado del disco depende del servidor donde corre, no lo hace la aplicación. Y la eliminación es completa de verdad: borra cada tabla y cada archivo asociado a tu cuenta, no solo el registro principal.' },
      { q: '¿Cuánto tiempo toma empezar a usar StockAI en mi empresa?', a: 'En la mayoría de casos, menos de un día. Si tienes un archivo de ventas histórico, puedes subirlo y ver tu primera lista de qué pedir en menos de una hora. Para integraciones con ERP o sistemas propios, el tiempo varía según la complejidad.' },
      { q: '¿Se puede integrar con nuestro ERP o sistema de inventario actual?', a: 'La carga normal es por archivo: exportas de tu sistema y subes el CSV o Excel. También puedes conectar StockAI directamente a tu base de datos Postgres o MySQL y traer las ventas con una consulta, sin archivos de por medio. Una integración a medida con tu ERP la armamos con nuestro equipo técnico sobre tu operación, caso por caso — escríbenos y lo vemos.' },
      { q: '¿Cada cuánto se actualiza lo que me dice que pida?', a: 'Cada vez que cargas ventas nuevas. Puedes lanzarlo tú al subir el archivo del mes, o dejar el recálculo programado para que corra solo: cada lunes a las 6, todos los días, solo días hábiles, cada hora o el primero de cada mes.' },
      { q: '¿StockAI sirve si tengo más de una bodega?', a: 'Sí. El plan gratis trae una bodega; ampliando el plan no hay tope de ubicaciones. Defines rutas entre bodegas con los días de tránsito y el costo. Cuando un producto está corto en una bodega y sobrado en otra, StockAI sugiere mover en lugar de comprar, y solo lo sugiere si a la bodega que presta le quedan al menos 30 días de cobertura.' },
      { q: '¿De dónde saca StockAI el plazo de entrega de cada proveedor?', a: 'Al principio, del que escribes tú en la ficha del proveedor. Cada vez que registras una recepción, StockAI guarda cuántos días pasaron de verdad entre la orden y la entrega. A partir de la tercera recepción de ese proveedor empieza a usar el promedio real en lugar del plazo declarado, y te muestra cuál de los dos está usando.' },
      { q: '¿Qué tan grande puede ser el archivo de ventas que subo?', a: 'En el plan gratis, hasta 25 MB por archivo. Pasando al plan completo, hasta 2 GB. Para dimensionarlo: 3 años de historial con 5.000 productos y venta diaria son unos 5 millones de filas, del orden de 200 MB en CSV — dentro del plan completo.' },
    ],
  },
  misc: {
    signalHead: ['Señal', 'Cuándo aparece', 'En el ejemplo'],
    roleToday: 'Hoy',
    roleWith: 'Con StockAI',
    exampleTitle: 'Un ejemplo',
    exampleBody: 'Vendes 20 unidades al día de un producto y tu proveedor tarda 15 días en entregar. Con esos dos números, los cuatro estados del semáforo quedan en cantidades concretas — las de la tabla de abajo. Cambia cualquiera de los dos y los cortes se mueven solos, producto por producto.',
    industriesLabel: 'Industrias:',
    leadTimeNote: 'Y el plazo no es el que te prometieron, es el que cumplen',
  },
  footer: {
    tagline: 'Qué pedir, cuánto y a quién — para distribuidores, retail y manufactura.',
    product: 'Producto',
    company: 'Empresa',
    contact: 'Contacto',
    rights: '© 2026 StockAI. Todos los derechos reservados.',
    madeIn: 'Hecho en Costa Rica',
  },
}

const en: LandingCopy = {
  nav: {
    links: [
      ['#problema', 'The problem'],
      ['#solucion', 'How it works'],
      ['#casos', 'Industries'],
      ['#incluye', "What's included"],
      ['#precio', 'Pricing'],
      ['#empezar', 'Contact'],
    ],
    signIn: 'Sign in',
    signUp: 'Create account',
    menu: 'Menu',
  },
  heroPills: ['Distribution', 'Retail', 'Manufacturing', 'Wholesale', 'E-commerce'],
  footerLinks: {
    product: [['#solucion', 'How it works'], ['#casos', 'Industries'], ['#incluye', "What's included"], ['#precio', 'Pricing'], ['#comparacion', 'vs Excel']],
    company: [['#problema', 'The problem'], ['#nosotros', 'About us'], ['mailto:hola@usefaro.io', 'Contact']],
  },
  hero: {
    eyebrow: 'For the person who decides the buying',
    title1: 'What to order today, how much,',
    title2: 'and from which supplier.',
    lead: 'StockAI reads your sales and your stock, and every morning tells you which products are about to run out, how many units of each to order and from whom, how much money is sitting in what does not turn, and which orders are on their way.',
    cta: 'Start free with sample data',
    ctaTrial: 'Try it without signing up',
    trialNote: 'Instant trial account: temporary username and password, 24 hours, no card.',
    frame: 'StockAI · Purchasing dashboard',
  },
  strip: {
    models: 'States per product: order now, order soon, ok or overstock',
    deliveries: 'Deliveries to learn a supplier’s real lead time',
    skus: 'Products per catalogue it is tested against',
    csv: 'All you need to start',
  },
  problem: {
    tag: 'The problem',
    title: 'Badly planned inventory has a concrete cost.',
    lead: 'Most companies buy on a spreadsheet, accumulated instinct and whichever buyer is on duty. That works up to a point — and the point arrives sooner than it looks.',
    items: [
      { title: 'Stockouts in the season that matters', desc: 'In retail and distribution, running out during peak season is not just a lost sale — the customer goes to the competition and does not come back. Demand does not wait for your next replenishment cycle.' },
      { title: 'Capital trapped in overstock', desc: 'For wholesalers and manufacturers, excess inventory fills the warehouse, eats the credit line, and in perishable or seasonal categories ends as a straight loss at clearance.' },
      { title: 'Reactive buying instead of planned buying', desc: 'Ordering once stock is already critical means accepting bad terms: spot prices, emergency freight and lead times outside the normal cycle.' },
      { title: 'Knowledge held by one person', desc: 'Your most experienced buyer carries the seasonality, the supplier cycles and every product’s history in their head. None of that is in a system.' },
      { title: 'A spreadsheet cannot cover the whole catalogue', desc: 'With a few products, checking row by row works. Once the catalogue reaches hundreds or thousands of codes, most of it ends up ordered out of habit: the same as last month, plus a bit.' },
    ],
  },
  how: {
    tag: 'How it works',
    title: 'From your sales history to the purchase order.',
    lead: 'You upload what you already have and StockAI hands back the buying list. Nothing statistical to set up, no analyst needed.',
    steps: [
      { n: '01', title: 'Upload your sales and your stock', desc: 'A CSV or Excel file, exactly as it comes out of your system. StockAI works out on its own which column is the date, the product and the quantity sold.' },
      { n: '02', title: 'StockAI learns how each product sells', desc: 'Seasons, paydays, trend and swings, product by product. Nothing for you to configure per code.' },
      { n: '03', title: 'It tells you what to order today, and how much', desc: 'Every product lands in one state — ORDER NOW, ORDER SOON, OK or OVERSTOCK — with the suggested quantity worked out against its supplier’s lead time.' },
      { n: '04', title: 'You order, you receive, it learns', desc: 'The order comes out grouped by supplier. When you record the arrival, StockAI notes how long it really took and uses that for the next one.' },
    ],
  },
  decide: {
    tag: 'The rule, no mystery',
    title: 'How StockAI decides a product is in the red.',
    lead: 'No recommendation comes out of a black box. The whole signal rests on one piece of arithmetic, and you can do it by hand to check it gives the same answer.',
    formulaTitle: 'The arithmetic',
    formulaBody: 'Coverage = stock on hand ÷ forecast daily demand. That tells you how many days you last if nothing else arrives. The figure is compared against your supplier lead time: the days they take to deliver once you place the order.',
    formulaBody2: 'It is the logic you already run in your head, only applied to the thousands of codes you never get to review: if you last less than they take to arrive, you are already late.',
    leadTimeBody: 'The lead time is half the arithmetic, so it had better be the real one. Every time you record a reception, StockAI stores how many days actually passed — and from the third one it plans with that average instead of the one you were promised.',
    signals: [
      { signal: 'ORDER NOW', rule: 'Coverage does not even reach half the supplier’s lead time', example: 'Under 7.5 days · under 150 units' },
      { signal: 'ORDER SOON', rule: 'Coverage is under 1.2× the lead time', example: '7.5 to 18 days · 150 to 360 units' },
      { signal: 'OK', rule: 'Coverage runs from 1.2× to 3× the lead time', example: '18 to 45 days · 360 to 900 units' },
      { signal: 'OVERSTOCK', rule: 'Coverage is 3× the lead time or more', example: '45 days or more · over 900 units' },
    ],
  },
  about: {
    tag: 'About us',
    title: 'Built for the person who decides the buying.',
    body1: 'StockAI exists so distributors, shops and wholesalers across Latin America stop buying inventory blind. Most of them buy on a spreadsheet and instinct because forecasting tools were built for large companies with data teams — not for an operation handling thousands of products with a small one.',
    body2: 'StockAI takes the sales history you already have (a CSV or Excel) and turns it into concrete decisions: what to order, how much, from whom and when. No analyst required. Made in Costa Rica, built for how small and mid-sized companies in the region actually work.',
  },
  cases: {
    tag: 'Industries',
    title: 'Designed for real operations.',
    lead: 'The inventory problem is not the same for a wholesaler as for a retailer or a production plant. StockAI adapts to how each one works.',
    doesLabel: 'What StockAI does in',
    items: [
      {
        label: 'Retail',
        title: 'Inventory by store and by category',
        desc: 'A retailer with several points of sale faces different demand at each location, categories with different seasons, and a replenishment cycle that cannot fail. StockAI works out what to order for every store and every product, flags when something’s sales change direction, and lets you get ready for peak seasons in time.',
        does: [
          'A signal per product and per store: ORDER NOW, ORDER SOON, OK or OVERSTOCK, measured against each supplier’s lead time.',
          'It flags when a product’s sales change direction, so you stop reordering last month’s quantity.',
          'Scheduled recalculation — every Monday, every day, or the first of the month — with nobody having to launch it.',
        ],
      },
      {
        label: 'Distributors',
        title: 'Optimised replenishment, fewer emergencies',
        desc: 'Distributors work on thin margins, with suppliers whose lead times move and customers who do not tolerate shortages. An inventory mistake is expensive: an unhappy customer leaves. StockAI works out the right reorder point for each product from its real sales velocity and the supplier’s lead time, cutting emergency purchases.',
        does: [
          'A reorder point per product, using the supplier’s real lead time: from the third recorded delivery it stops using the promised one.',
          'The purchase order comes out grouped by supplier, with a suggested quantity and the reason for every line.',
          'A daily summary of the products moving into risk, to the inbox and the WhatsApp of whoever decides.',
        ],
      },
      {
        label: 'Wholesalers',
        title: 'Balancing inventory across warehouses',
        desc: 'Wholesalers buy in volume to get a better price, and that advantage disappears the moment inventory stops turning or sits in the wrong place. StockAI flags which products are in excess before they hit their expiry date or go obsolete, and which references to prioritise on the next order.',
        does: [
          'OVERSTOCK is flagged as soon as coverage passes three times the supplier’s lead time — not when it is already clearance time.',
          'Transfer between warehouses instead of buying — and only suggested if the lending warehouse keeps at least 30 days of coverage.',
          'Volume price breaks: how far the next tier is, and what you save by reaching it.',
        ],
      },
      {
        label: 'Manufacturing',
        title: 'Production and raw-material planning',
        desc: 'A line stopped for want of material costs far more than the material: lost labour hours, late-delivery penalties and customers who stop trusting you. StockAI turns the finished-goods demand forecast into a raw-material requirement plan, accounting for production times and supplier lead times.',
        does: [
          'A bill of materials per finished product: expected sales become how much to buy of components, raw materials and packaging.',
          'Material shortfalls show up against the plan, not on the day the line stops.',
          'Each input supplier’s lead time corrects itself with every delivery you record.',
        ],
      },
      {
        label: 'E-commerce',
        title: 'Getting ready for demand peaks',
        desc: 'In e-commerce, reaching Black Friday without stock is money left on the table. Reaching it with too much is trapped capital and a clearance at a loss. StockAI reads how your products behaved during past promotional events and estimates the next peaks with enough time to actually place the orders.',
        does: [
          'Scenario simulator: double a category’s demand, mark a promotion or delay a supplier, and compare against the base without touching anything real.',
          'Each product’s seasons are detected on their own, with nothing to configure per code.',
          'It points out which products — the ones that sell most and swing most — deserve the safety buffer before the peak, instead of spreading it evenly.',
        ],
      },
    ],
  },
  compare: {
    tag: 'Comparison',
    title: 'Excel vs StockAI.',
    lead: 'Excel is for writing things down, not for telling you what to order. It works for a handful of products. The problem shows up when the business grows and the spreadsheet stops keeping up.',
    head: ['What you need to know', 'Excel', 'StockAI'],
    rows: [
      { feature: 'What to order today', excel: 'Row by row', stockai: 'List by urgency' },
      { feature: 'How much of each product', excel: 'Judgement call', stockai: 'Suggested quantity' },
      { feature: 'Products you get to review', excel: 'Dozens', stockai: 'The whole catalogue' },
      { feature: 'How long each supplier really takes', excel: 'From memory', stockai: 'Measured' },
      { feature: 'Warning of what is about to run out', excel: 'Not available', stockai: 'Daily' },
      { feature: 'Money stuck in overstock', excel: 'Hard to see', stockai: 'Flagged' },
      { feature: 'Purchase order per supplier', excel: 'By hand', stockai: 'Built for you' },
      { feature: 'Each product’s seasons', excel: 'By hand', stockai: 'Automatic' },
    ],
  },
  start: {
    tag: 'Before you start',
    title: 'What you need to get going — and what you do not.',
    lead: 'The most common reason a tool like this ends up unused is not the price: it is finding out, three weeks in, that a data project had to happen first. Here is the whole list, so you can check it now.',
    needTitle: 'What you do need',
    need: [
      'A CSV or Excel file with your sales history.',
      'Three columns at minimum: date, product code and quantity sold.',
      'Ideally 12 months or more, so a full season is visible.',
      'Your current stock on hand, so the signal has something to compare against.',
      'Roughly how long each supplier takes. Roughly is enough: it corrects itself.',
    ],
    notNeedTitle: 'What you do not need',
    notNeed: [
      'You do not need to connect your ERP to start: export from your system and upload the file.',
      'You do not need to configure a model per product, or know what a time series is.',
      'You do not need a dedicated technical person.',
      'You do not need a clean or complete catalogue: products with little history do not stop the run — they stay out of the forecast and the rest trains anyway.',
      'You do not need a minimum number of products. It works the same with 80 codes as with 4,000.',
    ],
  },
  pricing: {
    tag: 'Pricing',
    title: 'Actually free. And everything is in it.',
    lead: 'There are no features that unlock by paying. The signal, purchase orders, warehouses, the optimiser, the simulator, the alerts, the analyst and the API are all complete from day one — on the free plan too. The only thing paying changes is how much fits.',
    freeLabel: 'Free',
    freePrice: '$0',
    freeNote: 'Forever, no card. Enough for a small operation to genuinely run on it.',
    paidLabel: 'Full',
    paidPrice: "Let's talk",
    paidNote: 'The price is built around your operation: how many products you move, how many warehouses, how often you recalculate. There is no checkout — write to us and we work it out with your numbers.',
    limits: [
      ['Products (SKUs)', '100', 'Unlimited'],
      ['Users', '2', 'Unlimited'],
      ['Warehouses', '1', 'Unlimited'],
      ['Saved forecasts', '3', 'Unlimited'],
      ['API keys', '1', 'Unlimited'],
      ['File size', '25 MB', '2 GB'],
    ],
    closing: 'Start free today. When you outgrow it — a catalogue that grew, a second warehouse, a third person on the team — write to us and we lift it. We answer within 24 hours.',
    ctaSignup: 'Create my free account',
    ctaWhatsapp: 'Message us on WhatsApp',
    ctaEmail: 'Email us',
  },
  benefits: {
    tag: 'Every morning',
    title: 'What you know when you open StockAI.',
    lead: 'No reports to build, no spreadsheets to cross-check. It is on screen when you arrive, and the summary of what is urgent has already reached your inbox and WhatsApp.',
    items: [
      'What to order today, sorted by urgency',
      'How many units of each product to order',
      'The purchase order, grouped by supplier',
      'Which orders are on their way, and which should have arrived already',
      'How long each supplier really takes, measured on your receptions',
      'How much money is sitting in products that do not turn',
      'Which products are moving into risk, by email and WhatsApp',
      'What happens if a promotion doubles sales, before committing the money',
      'The whole catalogue, not a sample — tested with over 5,000 products',
      'Reports in Excel and PDF, ready to share',
    ],
  },
  includes: {
    tag: "What's included",
    title: 'What it solves, in detail.',
    lead: 'Two problems appear as soon as an operation grows: not being able to look at every product, and having inventory spread across several places. This is what StockAI puts on both sides — and nothing has to be switched on, it comes included.',
    rolesTitle: 'Who it solves something for, and what',
    roles: [
      {
        role: 'Owner or general manager',
        pain: 'You find out about the stockout when the salesperson calls, and about the overstock when you see how much money is sitting in the warehouse.',
        gain: 'A daily summary of the products in the red, by email and WhatsApp. Plus a simulator to test "what happens if the promotion doubles this category’s sales?" before committing the money.',
      },
      {
        role: 'Purchasing manager',
        pain: 'You scan thousands of codes in a spreadsheet and end up buying out of habit: the same as last month, plus a bit.',
        gain: 'The list arrives ordered by urgency, with the suggested quantity per supplier. The optimiser builds the order weighing what it costs to hold inventory, what it costs to run out, and the fixed freight of the truck.',
      },
      {
        role: 'Warehouse lead',
        pain: 'You write receptions in a notebook, and nobody in the company knows how long each supplier really takes.',
        gain: 'Every reception you record becomes data. From a supplier’s third delivery, StockAI stops using the lead time they promised and starts using the one they keep.',
      },
      {
        role: 'Finance and admin',
        pain: 'You know what the inventory is worth, but not how much of it is capital trapped in products that do not turn.',
        gain: 'StockAI separates what drives your sales from what only takes up space, and the reports you export to Excel and PDF carry that mark on every product.',
      },
    ],
    itemsTitle: 'What is included, concretely',
    items: [
      { title: 'Where the safety buffer goes', desc: 'StockAI separates the products that make up 80% of your sales from the long tail, and steady sellers from erratic ones. Where high sales meet unpredictable demand is where safety stock belongs, instead of spreading it evenly across the catalogue.', isNew: false },
      { title: 'Move between warehouses before buying', desc: 'As many locations as you need, with routes between them: transit days and cost. When a product is short in one warehouse and long in another, StockAI proposes moving instead of buying — and only proposes it if the lending warehouse keeps at least 30 days of coverage.', isNew: false },
      { title: 'The cheapest order, not the smallest', desc: 'It builds the order for the lowest total cost, not the fewest units: holding cost, the penalty for running out, purchase cost, the per-unit transfer cost and the fixed shipping cost, which is paid once even if the truck carries twenty products.', isNew: false },
      { title: 'Test the decision before you pay for it', desc: 'Up to 50 rules per scenario: multiply demand, mark a promotion, delay a supplier or change safety stock, filtering by product, category, supplier or date range. It compares the scenario against the baseline without touching anything real, and you can save it to run again.', isNew: false },
      { title: 'What is urgent reaches your phone', desc: 'The same daily summary of at-risk products that goes out by email, now to the phone of whoever decides. Each person links and verifies their own number from their settings.', isNew: false },
      { title: 'Ask your inventory', desc: 'You ask about your own data in plain language — "why did demand for this category go up?", "which suppliers are running late?" — and every answer is marked with where it came from, so you know when it is standing on your data and when it is not.', isNew: false },
      { title: 'The list updates itself', desc: 'Instead of remembering to recalculate, you leave it running: every Monday at 6, every day, weekdays only, hourly, or the first of each month. The screen shows when it ran, when it runs next, and whether it failed.', isNew: false },
      { title: 'Your team talks next to the inventory', desc: 'One-to-one conversations between the people in your company, inside StockAI, next to the inventory they are talking about. If the other person is not connected, a heads-up reaches their WhatsApp so the message is not missed.', isNew: true },
    ],
    isNew: 'New',
    tail: 'All of that comes on top of the base: the signal, purchase orders, receptions that learn your supplier’s lead time, the reports and the email alerts — all of it on the free plan too. ',
    tailLink: "Let's talk about pricing →",
  },
  manual: {
    title: 'Or read the whole thing, unhurried.',
    body: 'The user manual covers all nineteen screens at the same depth: what each one is for, what every figure means, how to do the concrete things, and what tends to confuse people. Same product as these captures, not a shortened version.',
    cta: 'Download the manual (PDF)',
    note: 'PDF · English · 59 pages',
  },
  tech: {
    tag: 'For your technical team',
    title: 'What is underneath, briefly.',
    lead: 'None of this is needed to buy better. It is here for whoever wants to check it.',
    items: [
      { title: 'Models competing per product', desc: 'Nine models are tried on each product’s history, and the one that is least wrong is kept.' },
      { title: 'Accuracy and backtesting per model', desc: 'Each model’s error, measured on your own history and visible on the forecast screen.' },
      { title: 'ABC-XYZ classification', desc: 'ABC by weight in sales, XYZ by how steady demand is. It is what places the safety buffer.' },
      { title: 'Public API and MCP server', desc: 'A REST API with per-company keys, and a read-only MCP server for AI assistants. Same keys, same limits.' },
    ],
  },
  tour: {
    title: 'Screen by screen.',
    lead: 'Real captures of the running app, with data in them. The chapters are the same groups as StockAI’s own menu, in the same order: it is the map you will have five minutes after signing in.',
    chapters: [
      {
        chapter: 'Daily operation',
        when: 'What you open every morning, before the coffee.',
        screens: [
          { img: SHOTS_EN.panel, name: 'Purchasing dashboard', does: 'The first thing you see. It gathers on one screen the only thing you have to decide today: what is about to run out, which orders are on their way, and where to start.', finds: ['How many products are at risk today, and how many this week', 'A written summary with the day’s risks, opportunities and actions', 'The orders that should already have arrived, ready to be received'], alt: 'StockAI purchasing dashboard: monitored SKUs, risk, accuracy and inventory value, with an executive summary of risks, opportunities and actions for today.' },
          { img: SHOTS_EN.inventory, name: 'Inventory', does: 'Your whole catalogue in four states, and for each product the quantity to order already worked out against its supplier’s real lead time. Nothing to interpret.', finds: ['The signal: ORDER NOW, ORDER SOON, OK and OVERSTOCK', 'A tab per warehouse, plus the consolidated view', 'The purchase order ready to export as CSV or PDF'], alt: 'StockAI inventory table with a colour signal per SKU and warehouse, the quantity to order and each product’s supplier.' },
          { img: SHOTS_EN.pedidos, name: 'Orders', does: 'The orders you generated, from the moment they leave to the moment they land. Every reception you record teaches StockAI how long that supplier really takes.', finds: ['Orders in transit, partial and received, with their value', 'Send the order to the supplier by WhatsApp or email', 'Record the arrival, complete or partial'], alt: 'StockAI orders screen with purchase orders in transit, partial and received, with WhatsApp sending and arrival recording.' },
          { img: SHOTS_EN.mensajes, name: 'Messages', does: 'One-to-one conversations inside StockAI, about the products you are both looking at. The decision and the conversation do not live in two different apps.', finds: ['One thread per person on the team', 'A WhatsApp or SMS heads-up when a message arrives'], alt: 'StockAI internal messaging: a conversation between the buyer and the analyst about a product at risk of running out.' },
        ],
      },
      {
        chapter: 'Your data',
        when: 'What you set up once, and adjust when something changes.',
        screens: [
          { img: SHOTS_EN.ventas, name: 'My sales', does: 'You upload the file you already have. Date, product and quantity is enough — StockAI reviews the file before training and tells you what it found.', finds: ['CSV or Excel, exactly as it comes out of your system', 'How far ahead you want to plan, and at what grain', 'Your country’s calendar, so paydays and holidays count'], alt: 'StockAI sales upload screen: the file, the planning horizon, the level of detail and the country.' },
          { img: SHOTS_EN.configurar, name: 'Inventory setup', does: 'The three things StockAI needs to know about each product: how much you have, what it costs and how long it takes to arrive. It tells you exactly what happens if one is missing.', finds: ['Load stock by hand or from your system’s file', 'Rules per supplier or category, instead of product by product', 'Which products stay out of the signal, and why'], alt: 'StockAI inventory setup screen, explaining what happens when a product is missing its stock, cost or lead time.' },
          { img: SHOTS_EN.proveedores, name: 'Suppliers', does: 'Each supplier’s card: how to reach them, how long they say they take, and how many deliveries in StockAI is towards learning how long they really take.', finds: ['Declared lead time, payment terms and contact details', 'Volume price breaks, to know when a bigger order pays off', 'The progress of the lead-time learning, delivery by delivery'], alt: 'StockAI supplier list with lead times, payment terms and the state of the real lead-time learning.' },
          { img: SHOTS_EN.scorecard, name: 'Supplier scorecard', does: 'What they said against what they did. StockAI compares the declared lead time with the one it measured in your own receptions, and plans with the second.', finds: ['Real learned lead time, against the declared one', 'On-time percentage and how complete the deliveries were', 'How much you have bought from each'], alt: 'StockAI supplier scorecard comparing declared lead time against the real one learned from recorded receptions.' },
        ],
      },
      {
        chapter: 'Analysis',
        when: 'When you want to understand why, or test a decision before making it.',
        screens: [
          { img: SHOTS_EN.forecast, name: 'Forecast per product', does: 'How much you can expect to sell of each product and within what range, so you know how far to trust the suggested quantity before ordering.', finds: ['History, forecast and the likely sales range', 'How far off the forecast tends to be, so you know how much to trust it', 'Daily, weekly, monthly or quarterly, matching how you buy'], alt: 'StockAI per-SKU forecast chart: sales history, forecast and the likely sales range, with the model comparison.' },
          { img: SHOTS_EN.pattern, name: 'How each product sells', does: 'It separates what is genuinely growing from the weekly pattern repeating itself. That is the difference between a trend and a Monday.', finds: ['Sales with the usual up-and-down already taken out', 'How much of the movement the day of the week explains', 'The average per day, to see where the peak is'], alt: 'StockAI screen separating a product’s real trend from the pattern that repeats every week.' },
          { img: SHOTS_EN.escenarios, name: 'Scenario simulator', does: 'What happens if you sell 40% more, if your supplier runs a week late, or if you promote in December? You see it before committing, product by product.', finds: ['The current plan and the scenario, side by side', 'Which products change state and how much the order changes', 'Saved scenarios, to run them again'], alt: 'StockAI scenario simulator comparing the current plan against a higher-demand scenario, product by product.' },
          { img: SHOTS_EN.impacto, name: 'Impact', does: 'What you did with StockAI this month, from figures that come out of your own records. It does not estimate savings or count avoided stockouts, because those cannot be measured with certainty.', finds: ['How many orders you generated and how many recommendations you followed', 'Where every number comes from, said plainly', 'The same summary reaches your inbox on the first of the month'], alt: 'StockAI impact screen with the monthly summary of what was done with the tool and where each figure comes from.' },
          { img: SHOTS_EN.asistente, name: 'AI analyst', does: 'You ask about your own inventory in plain language and it answers with your figures. Not a generic chatbot: it reads the same signal you see on screen.', finds: ['How many products are in each state, and which ones', 'What to review this week and which supplier to call first', 'Suggested questions, if you do not know where to start'], alt: 'A conversation with StockAI’s AI analyst about at-risk products and trapped capital, using figures from the inventory itself.' },
          { img: SHOTS_EN.historial, name: 'History', does: 'Every time you upload new sales a session is kept. You can go back to any of them, compare them, and see which one the signal is being calculated from today.', finds: ['All your runs, with their date and grain', 'Which session is the active one'], alt: 'StockAI forecast session history, with the active session and the previous ones.' },
        ],
      },
      {
        chapter: 'Your account and your team',
        when: 'What you touch every once in a while.',
        screens: [
          { img: SHOTS_EN.usuarios, name: 'Team and permissions', does: 'You invite your people with the permission that fits. Whoever only looks, only looks: the three roles are the same ones the API respects.', finds: ['Administrator, analyst and read-only', 'Invitation by email, with no passwords for you to invent'], alt: 'StockAI users screen with the administrator, analyst and read-only roles.' },
          { img: SHOTS_EN.cuenta, name: 'My account', does: 'Your profile, the currency your figures are shown in, the language, the time zone — and how much room is left on your plan.', finds: ['Usage against each limit, so you see it coming before you hit it', 'Currency and time zone, which affect everything else', 'Your activity history on the account'], alt: 'StockAI account screen showing the plan, usage against each limit and the ways to contact us to lift it.' },
          { img: SHOTS_EN.automatizacion, name: 'Automation', does: 'So StockAI recalculates on its own. You schedule how often and at what hour, and generate the keys your own system uses to get in.', finds: ['Scheduled recalculation: every Monday, every day or the first of the month', 'API keys, shown exactly once'], alt: 'StockAI automation screen with API keys and scheduled recalculations.' },
          { img: SHOTS_EN.api, name: 'Public API', does: 'Your system pushes the data and takes the decision away, with nobody opening StockAI. The documentation lives inside, with your own data to try it.', finds: ['The endpoints your ERP needs, with examples ready to copy', 'Authentication, limits and the response format'], alt: 'StockAI public API documentation with the base URL, authentication, limits and endpoints.' },
        ],
      },
    ],
  },
  faq: {
    tag: 'Frequently asked',
    title: 'Answers to the most common questions.',
    lead: 'If you have a question that is not here, write to us directly. We answer within 24 hours.',
    cta: 'Write to us →',
    items: [
      { q: 'Do I need statistics or programming knowledge to use StockAI?', a: 'No. StockAI is built for the person who buys: you open the screen and see what to order today, how much and from which supplier. There are no models to configure and no code. You upload your data and the system does the rest.' },
      { q: 'What format does my sales data need to be in?', a: 'StockAI accepts Excel (.xlsx) and CSV. The file needs at least a date column, a product identifier column (SKU or name) and a quantity-sold column. The system works out which column is which.' },
      { q: 'What if I have products with very little sales history, or incomplete data?', a: 'StockAI needs at least 20 periods of history per product to train it. Products below that minimum stay out of the forecast: no projection is invented for them. Before anything runs, the file review tells you how many products are under the threshold, and if none of them clears it the file is stopped with the explanation on screen instead of producing an empty result. Those products still appear in your inventory marked NO DATA — no signal and no suggested quantity — so the decision is yours and not an invented number’s.' },
      { q: 'Is my data safe? Who has access to it?', a: 'The data you upload to StockAI is exclusively yours: it is not shared with third parties and it is not used to train models for other companies — every forecast is trained only on your own account’s history. Every query is filtered by company and access is controlled by role: administrator, analyst or read-only. Your integration credentials — the user and password of your database — are stored encrypted. Your sales files and trained models are kept on StockAI’s server, in a folder separated per company; disk encryption is a property of the server it runs on, not something the application does. And deletion is genuinely complete: it removes every table and every file tied to your account, not just the main record.' },
      { q: 'How long does it take to start using StockAI in my company?', a: 'In most cases, under a day. If you have a historical sales file you can upload it and see your first list of what to order in under an hour. For ERP or in-house system integrations, the time depends on the complexity.' },
      { q: 'Can it integrate with our current ERP or inventory system?', a: 'The normal path is by file: export from your system and upload the CSV or Excel. You can also connect StockAI straight to your Postgres or MySQL database and pull sales with a query, with no file in between. A custom ERP integration is something we build with our technical team around your operation, case by case — write to us and we will look at it.' },
      { q: 'How often does what it tells me to order get updated?', a: 'Every time you load new sales. You can launch it yourself when you upload the month’s file, or leave the scheduled recalculation running: every Monday at 6, every day, weekdays only, hourly, or the first of each month.' },
      { q: 'Is StockAI useful if I have more than one warehouse?', a: 'Yes. The free plan comes with one warehouse; on the full plan there is no cap on locations. You define routes between warehouses with transit days and cost. When a product is short in one warehouse and long in another, StockAI suggests moving instead of buying, and only suggests it if the lending warehouse keeps at least 30 days of coverage.' },
      { q: 'Where does StockAI get each supplier’s lead time from?', a: 'At first, from the one you type on the supplier’s card. Every time you record a reception, StockAI stores how many days actually passed between the order and the delivery. From that supplier’s third reception it starts using the real average instead of the declared lead time, and shows you which of the two it is using.' },
      { q: 'How large can the sales file I upload be?', a: 'On the free plan, up to 25 MB per file. Moving to the full plan lifts that to 2 GB. For scale: 3 years of history with 5,000 products selling daily is around 5 million rows, on the order of 200 MB as CSV — within the full plan.' },
    ],
  },
  misc: {
    signalHead: ['Signal', 'When it appears', 'In the example'],
    roleToday: 'Today',
    roleWith: 'With StockAI',
    exampleTitle: 'An example',
    exampleBody: 'You sell 20 units a day of a product and your supplier takes 15 days to deliver. With those two numbers, the four signal states become concrete quantities — the ones in the table below. Change either number and the cut-offs move on their own, product by product.',
    industriesLabel: 'Industries:',
    leadTimeNote: 'And the lead time is not the one they promised, it is the one they keep',
  },
  footer: {
    tagline: 'What to order, how much and from whom — for distributors, retail and manufacturing.',
    product: 'Product',
    company: 'Company',
    contact: 'Contact',
    rights: '© 2026 StockAI. All rights reserved.',
    madeIn: 'Made in Costa Rica',
  },
}

export const LANDING: Record<Lang, LandingCopy> = { es, en }

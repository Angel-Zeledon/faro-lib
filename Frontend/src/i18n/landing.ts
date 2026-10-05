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
 * Audience: the page speaks to the BUYER — the person who decides purchases at
 * a distributor — in outcomes: what to order today, how much, from whom.
 * Since 2026-10-01 (owner's team) the forecasting engine is ALSO the headline:
 * `engine` explains, specifically, what the models do and how the workflow
 * runs. That is not licence for decoration: every sentence in `engine` names a
 * mechanism that exists in ForecastingCore or the decision layer (the file
 * that backs each one is noted beside it), and the limits are stated next to
 * the claims. No accuracy percentage, no "powered by AI" filler.
 *
 * Voice since 2026-10-02 (owner): more of a seller — confident, enthusiastic
 * about the AI engine, benefit first, premium. The enthusiasm is in the TONE,
 * never in the facts: every new sentence still names something the code does,
 * and the honest limits moved to the FAQ ("What doesn't StockAI do?") rather
 * than disappearing.
 */
import type { Lang } from './translations'

export interface Titled { title: string; desc: string }
export interface Case { label: string; title: string; desc: string; does: string[] }
export interface Compare { feature: string; excel: string; gut: string; stockai: string }
// One moment of the buyer's day. A real sequence, so it is numbered.
export interface MorningStep { when: string; title: string; desc: string }
export interface FeatureGroup { name: string; items: string[] }
export interface Signal { signal: string; rule: string; example: string }
export interface Faq { q: string; a: string }
export interface TourScreen { img: string; name: string; does: string; finds: string[]; alt: string }
export interface TourChapter { chapter: string; when: string; screens: TourScreen[] }
// A public subpage (/precios, /como-funciona, /preguntas-frecuentes,
// /seguridad). `label` is its name in menus and breadcrumbs. `title` (the H1)
// and `intro` MUST differ from the home-page section the subpage deepens: two
// URLs opening with the same heading and paragraph read to a search engine as
// one page published twice.
export interface Subpage { label: string; title: string; intro: string }

export interface LandingCopy {
  // ariaLabel names the <nav> landmark for screen readers. A link's href is
  // either `#anchor` (a section of the home page) or `/slug` (a subpage);
  // components/landing/chrome.tsx resolves both from wherever it is rendered.
  nav: { links: [string, string][]; signIn: string; signUp: string; menu: string; ariaLabel: string }
  heroPills: string[]
  footerLinks: { product: [string, string][]; company: [string, string][] }
  // `ctaTrial` leads to /prueba: a throwaway account (temporary username and
  // password, 24 hours) for a visitor who wants to look before signing up.
  // `trialNote` is the one line under the buttons that says what that means.
  hero: { eyebrow: string; title1: string; title2: string; lead: string; cta: string; ctaTrial: string; trialNote: string; frame: string }
  // Every figure in the strip is countable in the code: 9 models
  // (training/router.py + the global model), 4 signal states, 3 receptions to
  // learn a lead time (MIN_LEAD_TIME_OBSERVATIONS), 5,000+ products exercised.
  strip: { models: string; states: string; deliveries: string; skus: string }
  problem: { tag: string; title: string; lead: string; items: Titled[] }
  // `more` links to /como-funciona, the long version of this section.
  how: { tag: string; title: string; lead: string; more: string }
  // "Your morning with StockAI": the daily flow, in order. Every step names a
  // screen or a channel that exists (see the backing beside each one).
  morning: { tag: string; title: string; lead: string; steps: MorningStep[] }
  // Every capability, grouped the way a buyer thinks about them. Each item is
  // a restatement of one made elsewhere on the page or in the screen guide,
  // so nothing here is promised that the page does not already back. Also
  // published as SoftwareApplication.featureList (StructuredData.tsx).
  features: { tag: string; title: string; lead: string; groups: FeatureGroup[] }
  // The forecasting engine. `flow` is the workflow, in order (a real
  // sequence, so it is numbered on the page). `illus` labels the animated
  // illustration beside it — an illustration, never data, and it says so.
  engine: {
    flowLabel: string
    flow: { title: string; desc: string; detail: string }[]
    illus: { caption: string; history: string; today: string; candidates: string; winner: string; band: string; signal: string }
    tag: string; title: string; lead: string
    routingTitle: string; routingHead: [string, string]
    routing: { pattern: string; when: string; models: string }[]
    routingNote: string
    alwaysTitle: string; always: Titled[]
    ideasTitle: string; ideas: Titled[]
    assistantTitle: string; assistantBody: string; assistantPoints: string[]
    // What the engine does NOT promise moved out of this section on
    // 2026-10-02 (owner): it now lives in the FAQ, as "What doesn't StockAI
    // do?" — same three limits, same wording discipline.
  }
  // `configBody` says what the buyer can tune. Backed by
  // backend/inventory/service.py (_calc_signal, the reorder point built from
  // the service level and the supplier's review period) and
  // inventory/stock_defaults_service.py (service-level rules by supplier,
  // category or company). The 0.5x and 3x multipliers are configurable
  // per company, supplier or category since 2026-10-02 (backend/inventory/
  // signal_thresholds.py, the 'Reglas del semáforo' panel).
  decide: { tag: string; title: string; lead: string; formulaTitle: string; formulaBody: string; formulaBody2: string; configBody: string; leadTimeBody: string; signals: Signal[] }
  about: { tag: string; title: string; body1: string; body2: string }
  cases: { tag: string; title: string; lead: string; doesLabel: string; items: Case[] }
  compare: { tag: string; title: string; lead: string; head: [string, string, string, string]; rows: Compare[] }
  start: { tag: string; title: string; lead: string; needTitle: string; need: string[]; notNeedTitle: string; notNeed: string[] }
  pricing: {
    tag: string; title: string; lead: string
    // What the price is weighed against. Qualitative on purpose: no figure
    // for a stockout's cost or a saving is ever invented here (stability.md §4.5).
    valueTitle: string; value: Titled[]
    freeLabel: string; freePrice: string; freeNote: string
    paidLabel: string; paidNote: string
    // [label, what the free tier gets, what the paid tier gets]. The paid
    // column used to render `unlimited` for every row, which told a paying
    // customer their upload size was uncapped when entitlements/plans.py
    // bounds it at 2000 MB — the one ceiling that survives on a paid tenant,
    // because an upload is read into memory before it is anything else.
    // Carrying both values per row makes the type refuse a row that does not
    // say what each tier actually gets.
    limits: [string, string, string][]
    closing: string; ctaSignup: string; ctaWhatsapp: string; ctaEmail: string
    // How a free tenant becomes a paid one: a conversation, never a checkout
    // (backend/entitlements/plans.py — `tenants.tier` is set by hand). The
    // steps are a real sequence, so they are numbered.
    upgradeTitle: string; upgradeSteps: Titled[]
    // Short, checkable promises shown as ticks. Each must stay true: no card
    // is ever asked for, there is no checkout, and no feature is gated.
    noStrings: string[]
    // Prefilled text of the WhatsApp message and the email subject line.
    waPrefill: string; mailSubject: string
    // The full plan's "from" price. `{price}` is filled from
    // components/landing/pricingModel.ts — no price is ever written here.
    paidFrom: string; perMonth: string; calcLink: string
    // The corporate band (owner, 2026-10-05): a position for large accounts,
    // never a different product. `{price}` in `from` is filled from
    // CORPORATE_PLAN in components/landing/pricingModel.ts. Only things that
    // exist today may be listed in `items`; what is still being built goes in
    // `pending`, worded as not yet available.
    corporate: {
      label: string; from: string; perMonth: string; billing: string
      lead: string; itemsTitle: string; items: string[]
      pending: string; footnote: string
      waPrefill: string; mailSubject: string
    }
  }
  // The estimate calculator on /precios. Every figure comes from
  // pricingModel.ts; these strings only carry `{n}`/`{price}` placeholders.
  calc: {
    tag: string; title: string; lead: string
    inputs: Record<'skus' | 'users' | 'warehouses' | 'apiCalls', { label: string; included: string }>
    resultTitle: string; base: string; noExtras: string
    lines: Record<'skus' | 'users' | 'warehouses' | 'apiCalls', string>
    freeFits: string; freeFitsNote: string
    // Under the $0 when everything fits the free plan; `{total}` is the full plan's price.
    fullWouldBe: string
    note: string
    ctaEmail: string; ctaWhatsapp: string; ctaForm: string
    mailSubject: string; mailBody: string; waPrefill: string
    form: { title: string; lead: string; name: string; company: string; phone: string; message: string; submit: string; hint: string }
  }
  // Per-call API pricing, explained. `{n}` placeholders come from pricingModel.ts.
  api: { tag: string; title: string; lead: string; points: string[]; devLink: string }
  // Trust block. Every item is a property of the code, not a promise of
  // service — see the comment beside the block in app/page.tsx for the file
  // that backs each one. No figures, logos, testimonials or certifications.
  trust: { tag: string; title: string; lead: string; items: Titled[]; decideLink: string; more: string }
  // The closing band: the three ways forward, side by side.
  final: {
    title: string; lead: string
    signupTitle: string; signupDesc: string
    trialTitle: string; trialDesc: string
    talkTitle: string; talkDesc: string
    // `{email}` and `{phone}` are filled from components/landing/contact.ts.
    reach: string
    madeIn: string
  }
  // The screen guide is an opt-in deep dive, not part of the main read: the
  // page shows a teaser (`teaser*`) and the chapters open in a dialog.
  // `count` takes {n} screens and {c} chapters, both computed from `chapters`.
  tour: {
    title: string; lead: string
    teaserTitle: string; teaserLead: string; count: string
    open: string; close: string; chaptersNav: string
    chapters: TourChapter[]
  }
  manual: { title: string; body: string; cta: string; note: string }
  // `all` links the home accordion to /preguntas-frecuentes.
  faq: { tag: string; title: string; lead: string; cta: string; all: string; items: Faq[] }
  misc: {
    signalHead: [string, string, string]
    roleToday: string; roleWith: string
    exampleTitle: string; exampleBody: string
    leadTimeNote: string
    industriesLabel: string
  }
  footer: { tagline: string; product: string; company: string; contact: string; rights: string; madeIn: string }
  // The public subpages. Every claim here restates one already on the home
  // page (verified against code in commit 06f2955) — nothing new is promised.
  pages: {
    home: string; breadcrumb: string; onThisPage: string; related: string
    pricing: Subpage
    how: Subpage
    faq: Subpage
    // `ruleDesc` replaces the last trust item's text on /seguridad, where the
    // rule is not on "this page" but one link away.
    security: Subpage & { trialTitle: string; trialDesc: string; ruleDesc: string }
  }
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
      ['/como-funciona', 'Cómo funciona'],
      ['#motor', 'Los modelos'],
      ['/industrias', 'Industrias'],
      ['/precios', 'Precio'],
      ['/desarrolladores', 'API'],
      ['/docs', 'Ayuda'],
      ['#contacto', 'Contacto'],
    ],
    signIn: 'Iniciar sesión',
    signUp: 'Crear cuenta',
    menu: 'Menú',
    ariaLabel: 'Navegación principal',
  },
  heroPills: ['Distribución', 'Retail', 'Manufactura', 'Mayoristas', 'E-commerce'],
  footerLinks: {
    product: [['/como-funciona', 'Cómo funciona'], ['/como-se-calcula', 'Cómo se calcula'], ['#motor', 'Los modelos'], ['#funciones', 'Funciones'], ['/industrias', 'Industrias'], ['/stockai-vs-excel', 'StockAI vs. Excel'], ['/integraciones', 'Integraciones'], ['/precios', 'Precio'], ['/desarrolladores', 'API para desarrolladores'], ['/novedades', 'Novedades']],
    company: [['#problema', 'El problema'], ['#nosotros', 'Nosotros'], ['/seguridad', 'Seguridad'], ['/preguntas-frecuentes', 'Preguntas frecuentes'], ['#contacto', 'Contacto']],
  },
  hero: {
    eyebrow: 'Software de inventario con inteligencia artificial, para distribuidores y mayoristas',
    title1: 'Tu propio equipo de científicos de datos,',
    title2: 'decidiendo contigo qué comprar.',
    // Nine models: router.py ROUTING_TABLE (8) + the global model (global_trainer.py).
    lead: 'StockAI entrena una inteligencia artificial para cada uno de tus productos: hasta nueve modelos compiten sobre tu propio historial y se queda el que menos te cuesta equivocarse. Cada mañana te dice qué se va a quebrar, cuántas unidades pedir, a qué proveedor, y cuánto dinero tienes parado en lo que no rota.',
    cta: 'Empieza gratis con datos de ejemplo',
    ctaTrial: 'Probar sin registrarme',
    trialNote: 'Cuenta de prueba al instante: usuario y contraseña temporales, 24 horas, sin tarjeta.',
    frame: 'StockAI · Panel de compras',
  },
  strip: {
    models: 'Modelos de IA y estadística compitiendo por cada producto',
    states: 'Estados claros por producto: pedir ya, pedir pronto, ok o sobrestock',
    deliveries: 'Entregas y ya conoce el plazo real de tu proveedor',
    skus: 'Productos por catálogo con los que se pone a prueba',
  },
  problem: {
    tag: 'El problema',
    title: 'Cada quiebre y cada pedido de más te cuestan dinero de verdad.',
    lead: 'La mayoría de empresas toma decisiones de compra con Excel, intuición acumulada y el criterio del comprador de turno. Eso funciona hasta cierto punto — y ese punto llega antes de lo que parece.',
    items: [
      { title: 'Ruptura de stock en temporadas clave', desc: 'En retail y distribución, un quiebre durante temporada alta no es solo una venta perdida — el cliente va a la competencia y no regresa. La demanda no espera al próximo ciclo de reposición.' },
      { title: 'Capital atrapado en sobreinventario', desc: 'Para mayoristas y manufactureros, el exceso de inventario ocupa bodega, consume línea de crédito y en categorías perecederas o de moda, termina en pérdida directa por liquidación.' },
      { title: 'Compras reactivas en lugar de planificadas', desc: 'Comprar cuando el inventario ya está crítico obliga a aceptar condiciones desfavorables: precios spot, fletes de emergencia y tiempos de entrega fuera del ciclo normal.' },
    ],
  },
  how: {
    tag: 'Cómo funciona',
    title: 'Pronóstico de demanda con IA: subes tus ventas y la IA hace el resto.',
    lead: 'De tu archivo de ventas a la orden de compra en ocho pasos, y tú solo haces dos: subir el archivo y registrar lo que llega. Todo lo demás lo trabaja StockAI, producto por producto, y cada paso deja algo que puedes revisar en pantalla.',
    more: 'Ver el recorrido completo, pantalla por pantalla',
  },
  morning: {
    tag: 'Tu mañana con StockAI',
    title: 'De la alerta a la orden enviada, antes del segundo café.',
    lead: 'Así se ve un día de compras cuando la IA ya hizo el trabajo pesado durante la noche. Cada paso es una pantalla o un canal que existe hoy en StockAI.',
    steps: [
      // Daily alert loop: backend/workers/worker.py; channels: notifications/email.py, whatsapp.py
      { when: 'Antes de que llegues', title: 'Lo urgente ya te está esperando', desc: 'El resumen diario de los productos que entraron en riesgo llega a tu correo y a tu WhatsApp. Si dejaste el recálculo programado, la lista ya está al día con las ventas de ayer.' },
      // Dashboard screen (tour: panel)
      { when: 'Al abrir el panel', title: 'Una pantalla, lo único que hay que decidir', desc: 'Cuántos productos están en riesgo hoy y cuántos esta semana, un resumen escrito con los riesgos, las oportunidades y las acciones del día, y los pedidos que ya debían haber llegado.' },
      // Inventory screen + backend/inventory/service.py (incoming in the quantity)
      { when: 'En inventario', title: 'Qué pedir, y cuánto, ya calculado', desc: 'Todo tu catálogo en PEDIR YA, PEDIR PRONTO, OK y SOBRESTOCK. La cantidad sugerida ya descuenta lo que viene en camino y está medida contra el plazo real de cada proveedor.' },
      // Purchase orders: grouped by supplier, sent by email/WhatsApp, CSV/PDF export
      { when: 'En pedidos', title: 'La orden sale armada por proveedor', desc: 'Con el motivo de cada línea. La revisas, la ajustas y la envías por WhatsApp o por correo, o la exportas en PDF para tu sistema.' },
      // Receptions: MIN_LEAD_TIME_OBSERVATIONS = 3 in backend/inventory/service.py
      { when: 'Cuando llega la mercadería', title: 'Cada recepción enseña algo', desc: 'Registras la llegada, completa o parcial, y StockAI anota cuántos días tardó de verdad. Desde la tercera entrega, ese proveedor se planifica con su plazo real.' },
      // AI analyst (web) and WhatsApp share backend/assistant/ — read-only tools;
      // the WhatsApp write tools are suspended (backend/whatsapp/agent.py)
      { when: 'Cuando surge una duda', title: 'Le preguntas a tu analista de IA', desc: 'En la aplicación o por WhatsApp: qué está en rojo, qué pedidos siguen pendientes, cómo viene el pronóstico. Responde con las cifras de tu propia cuenta y te lleva a la pantalla donde se hace cada cosa: aprobar y recibir órdenes sigue siendo tuyo, en la aplicación.' },
    ],
  },
  features: {
    tag: 'Funciones',
    title: 'Un software de inventario y compras completo, también en el plan gratis.',
    lead: 'Todo lo que hace StockAI, agrupado como lo piensa quien compra.',
    groups: [
      { name: 'Compras', items: [
        'Lista de qué pedir hoy, ordenada por urgencia',
        'Cantidad sugerida por producto, descontando lo que ya viene en camino',
        'Órdenes de compra armadas por proveedor, con el motivo de cada línea',
        'Resumen diario de los productos en riesgo, por correo y WhatsApp',
        'Envío al proveedor por WhatsApp o correo; exportación en CSV o PDF',
        'Optimizador del pedido de menor costo total, con el flete fijo incluido',
        'Escalas de precio por volumen: cuánto falta para el siguiente escalón',
      ] },
      { name: 'Pronóstico con IA', items: [
        'Competencia de modelos por producto, probada contra tu propio pasado',
        'Rango de venta probable alrededor de cada pronóstico',
        'Los días de quiebre no se aprenden como falta de demanda',
        'Feriados y días del mes de tu país como variables del modelo',
        'Diario, semanal, mensual o trimestral',
        'Simulador de escenarios: promociones, atrasos y más demanda, hasta 50 reglas por escenario',
        'Recálculo programado: cada lunes, a diario, en días hábiles, cada hora o el primero de mes',
      ] },
      { name: 'Inventario y bodegas', items: [
        'Semáforo por producto: PEDIR YA, PEDIR PRONTO, OK y SOBRESTOCK',
        'Varias bodegas, con vista por bodega y consolidada',
        'Traslados entre bodegas antes de comprar',
        'Clasificación ABC-XYZ para ubicar el stock de seguridad',
        'Lista de materiales para manufactura',
      ] },
      { name: 'Proveedores', items: [
        'Plazo de entrega real, aprendido desde la tercera recepción',
        'Scorecard: plazo declarado contra el real, entregas a tiempo y completas',
        'Ciclo de pedido por proveedor dentro del punto de reorden',
        'Reglas de nivel de servicio y plazo por proveedor o por categoría',
      ] },
      { name: 'Finanzas y capital', items: [
        'Dinero parado en productos que no rotan, a la vista',
        'Sobrestock marcado antes de que toque liquidar',
        'Reportes en Excel y PDF, listos para compartir',
        'Resumen mensual con cifras de tus propios registros',
        'Tus cifras en la moneda de tu empresa',
      ] },
      { name: 'Integraciones y equipo', items: [
        'Carga por CSV o Excel, tal como sale de tu sistema',
        'Conexión directa a tu base de datos Postgres o MySQL',
        // backend/api/public_surface.py: every tenant-scoped action a key can hold the role for
        'API REST con las acciones de la aplicación, para tu propio sistema',
        'Servidor MCP de solo lectura para asistentes de IA',
        'Roles de administrador, analista y solo lectura',
        'Mensajería interna, con aviso por WhatsApp',
      ] },
    ],
  },
  engine: {
    flowLabel: 'El flujo de StockAI, paso a paso',
    flow: [
      // Canonical column detection: ForecastingCore/forecasting_core/data/canonical.py
      { title: 'Tus ventas', desc: 'Un CSV o Excel tal como sale de tu sistema. StockAI reconoce cuál columna es la fecha, cuál el producto y cuál la cantidad vendida.', detail: 'Fecha, producto y cantidad: con eso basta' },
      // Pre-training gate: data/gate.py; censored demand: data/censoring.py
      { title: 'Revisión y limpieza', desc: 'Antes de entrenar se revisa cada producto: fechas que faltan, valores atípicos, historial insuficiente. Si el archivo daría un pronóstico equivocado, no corre hasta que elijas cómo corregirlo: rellenar huecos con cero e interpolarlos dicen cosas distintas sobre lo que pasó.', detail: 'Con tu inventario histórico, los días sin producto dejan de contar como días sin demanda' },
      // Series classification: data/quality.py; routing: training/router.py
      { title: 'Competencia de modelos por producto', desc: 'Cada producto se clasifica por cómo se vende (estable, estacional, intermitente o volátil) y compiten los modelos que mejor manejan ese patrón, de entre nueve disponibles: modelos de árboles con gradient boosting, modelos estadísticos clásicos y una red neuronal.', detail: 'LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, LSTM y un modelo global' },
      // Walk-forward validation: training/trainer.py; champion: evaluation/metrics.py, pipelines/pipeline.py
      { title: 'Prueba contra el pasado', desc: 'A cada modelo se le esconde el tramo final de tu historial y se le pide pronosticarlo, en varios cortes. Gana el que menos cuesta equivocarse, y tiene que superar a dos pronósticos ingenuos.', detail: 'Quedarse corto pesa tres veces más que sobrar' },
      // Serving refit: training/trainer.py (_serving_model); band: inference/predictor.py
      { title: 'Pronóstico con rango', desc: 'El ganador se vuelve a entrenar con todo tu historial, incluido lo más reciente, y proyecta la demanda con un rango probable alrededor.', detail: 'Diario, semanal, mensual o trimestral' },
      // Signal thresholds and safety stock: backend/inventory/service.py
      { title: 'Semáforo y cantidad', desc: 'La demanda pronosticada se cruza con tus existencias y con el plazo de tu proveedor. Cada producto queda en PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK, con la cantidad sugerida.', detail: 'La regla está publicada y se puede hacer a mano' },
      { title: 'Orden de compra', desc: 'La orden sale armada por proveedor, con el motivo de cada línea. La revisas, la ajustas y la envías por correo o WhatsApp.', detail: 'Exportable en CSV o PDF' },
      // Learned lead time: backend/inventory/service.py (MIN_LEAD_TIME_OBSERVATIONS = 3)
      { title: 'Recepción que enseña el plazo', desc: 'Cuando registras la llegada, StockAI anota cuántos días tardó de verdad. A partir de la tercera entrega de un proveedor planifica con ese plazo, no con el prometido.', detail: 'Y ese plazo vuelve al paso del semáforo' },
    ],
    illus: {
      caption: 'Ilustración del proceso para un producto. No son datos reales.',
      history: 'Historial',
      today: 'Hoy',
      candidates: 'Candidatos',
      winner: 'Ganador',
      band: 'Rango probable',
      signal: 'PEDIR PRONTO',
    },
    tag: 'Los modelos',
    title: 'Una competencia de IA por cada producto. Gana el que menos te cuesta.',
    lead: 'Un producto que se vende todos los días no se pronostica igual que uno que pasa semanas sin moverse. Por eso StockAI no usa un solo modelo para todo el catálogo: pone a competir modelos de machine learning, una red neuronal y modelos estadísticos clásicos sobre la historia de cada producto, y planifica con el ganador. Es el trabajo que haría un equipo de ciencia de datos, corriendo solo sobre todo tu catálogo.',
    routingTitle: 'Quién compite, según cómo se vende',
    routingHead: ['Cómo se vende', 'Modelos que compiten'],
    // ROUTING_TABLE in ForecastingCore/forecasting_core/training/router.py;
    // thresholds in data/quality.py (classify_series).
    routing: [
      { pattern: 'Estable', when: 'Venta regular, sin un ciclo marcado', models: 'LightGBM, XGBoost, ARIMA, SARIMAX, LSTM' },
      { pattern: 'Estacional', when: 'Se repite un patrón: la semana, la quincena, la temporada', models: 'Prophet, ETS, LightGBM, SARIMAX, LSTM' },
      { pattern: 'Intermitente', when: 'Muchos periodos sin una sola venta', models: 'Croston, ETS' },
      { pattern: 'Volátil', when: 'Altibajos grandes de un periodo al otro', models: 'LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX' },
      { pattern: 'Historial corto', when: 'Menos de 30 periodos', models: 'Todos los que tengas activos, sin filtrar' },
    ],
    routingNote: 'Un producto puede tener dos patrones a la vez, estacional y volátil por ejemplo, y entonces compiten los de ambos. La clasificación solo acota los modelos que tienes activos: nunca entrena uno que no elegiste.',
    alwaysTitle: 'En todas las competencias',
    always: [
      // UNIVERSAL_MODELS = {"global_lgbm"} in training/router.py; training/global_trainer.py
      { title: 'Un modelo global, entrenado con todo tu catálogo', desc: 'Compite en todos los productos. Donde más aporta es en los de poca historia o venta intermitente: aprende de cómo se comporta el resto de tu catálogo, algo que ningún modelo de un solo producto puede hacer.' },
      // Baselines kept out of the champion race: pipelines/pipeline.py (_select_champions)
      { title: 'Dos pronósticos ingenuos como vara', desc: 'Repetir el último valor y repetir la temporada anterior. No pueden ganar, pero sí medir: si ningún modelo los supera en un producto, la corrida te lo avisa en lugar de esconderlo.' },
    ],
    ideasTitle: 'Lo que hace distinto a este motor de IA',
    ideas: [
      // CHAMPION_METRIC_ORDER = ("cost_horizon", ...) and DEFAULT_STOCKOUT_MULTIPLIER = 3.0, evaluation/metrics.py
      { title: 'Gana el que menos cuesta equivocarse', desc: 'Quedarte sin producto te hace perder ventas; que te sobre te inmoviliza dinero. No cuestan lo mismo, así que el ganador se elige con un costo en el que quedarse corto pesa tres veces más que sobrar, medido sobre todo el horizonte que vas a comprar.' },
      // data/censoring.py, wired in pipelines/pipeline.py
      { title: 'Un quiebre no se confunde con falta de demanda', desc: 'Si un producto se agotó, sus ventas en cero no dicen que nadie lo quería. Con tu inventario histórico, StockAI marca esos días y estima lo que se habría vendido, para no aprender el quiebre como una caída y volver a pedir de menos.' },
      // training/trainer.py (_serving_model): graded at the cut, served from a refit on everything
      { title: 'Probado con fechas que no vio, entrenado con todas', desc: 'La competencia se juzga sobre un tramo que el modelo nunca vio. Una vez elegido, el ganador se reentrena con el historial completo, para que tus últimas semanas de venta entren en el pronóstico con el que compras.' },
      // data/gate.py and the 20-period minimum in data/quality.py
      { title: 'Si el archivo no alcanza, no se inventa un número', desc: 'Un producto sin historia suficiente queda fuera del pronóstico y aparece como SIN DATOS, sin cantidad sugerida. Y un archivo que produciría un pronóstico equivocado se detiene antes de entrenar, con la explicación en pantalla.' },
    ],
    assistantTitle: 'Y un analista de IA al que le preguntas en español',
    assistantBody: 'Pregúntale a tu inventario como le preguntarías a tu mejor analista: «¿qué proveedor me está atrasando?», «¿por qué subió esta categoría?». Responde sobre tu propia cuenta, leyendo el mismo semáforo y los mismos pedidos que ves en pantalla, y cada respuesta indica de dónde salió.',
    assistantPoints: [
      'Redacta y explica; las cantidades a pedir las calcula el motor de pronóstico, no el chat.',
      'Usa un modelo de lenguaje externo. Si no está disponible, la pantalla lo dice antes de que escribas.',
    ],
  },
  decide: {
    tag: 'La regla, sin misterio',
    title: 'Punto de reorden y semáforo: cómo decide StockAI cuándo comprar.',
    lead: 'La IA pronostica; la decisión de comprar se apoya en una regla a la vista. Ninguna recomendación sale de una caja negra: puedes hacer la cuenta a mano y comprobar que da lo mismo.',
    formulaTitle: 'La cuenta',
    formulaBody: 'Cobertura = existencias ÷ demanda diaria pronosticada: cuántos días aguantas si no llega nada más. Esa cifra se compara contra dos referencias. El plazo de tu proveedor, los días que tarda en entregarte desde que le pasas la orden. Y tu punto de reorden: lo que vas a vender mientras esperas ese pedido, más un colchón de seguridad.',
    formulaBody2: 'La lógica es la que ya usas de cabeza, solo que aplicada a los miles de códigos que no alcanzas a revisar: si lo que tienes no alcanza a cubrir la espera, es hora de pedir.',
    configBody: 'El colchón lo decides tú: sale del nivel de servicio que eliges, para toda la empresa, por proveedor, por categoría o por producto. Y si a un proveedor le pides cada cierto tiempo, ese intervalo también entra en el punto de reorden. Y los cortes también son tuyos: «pedir ya» por debajo de la mitad del plazo y «sobrestock» desde tres veces el plazo vienen de fábrica, pero los cambias para toda la empresa, por proveedor o por categoría, y antes de guardar ves cuántos productos cambiarían de señal.',
    leadTimeBody: 'El plazo de entrega es media cuenta, así que conviene que sea el real. Cada vez que registras una recepción, StockAI guarda cuántos días pasaron de verdad — y a partir de la tercera empieza a planificar con ese promedio en lugar del que te prometieron.',
    // backend/inventory/service.py::_calc_signal. Example: 20 units/day,
    // 15-day lead time, 5-day safety stock => reorder point 20 days (400 u.);
    // overstock at max(3 x 15, 2 x 20) = 45 days (900 u.).
    signals: [
      { signal: 'PEDIR YA', rule: 'La cobertura no llega ni a la mitad del plazo del proveedor: ya vas tarde', example: 'Menos de 7,5 días · menos de 150 unidades' },
      { signal: 'PEDIR PRONTO', rule: 'La cobertura ya está en tu punto de reorden o por debajo: si no pides ahora, el pedido no llega antes de que se acabe', example: 'De 7,5 a 20 días · 150 a 400 unidades' },
      { signal: 'OK', rule: 'La cobertura pasa del punto de reorden, sin llegar al umbral de sobrestock', example: 'Más de 20 y menos de 45 días · 400 a 900 unidades' },
      { signal: 'SOBRESTOCK', rule: 'La cobertura llega a tres veces el plazo, o al doble del punto de reorden si eso es más', example: '45 días o más · 900 unidades o más' },
    ],
  },
  about: {
    tag: 'Nosotros',
    title: 'Hecho en Costa Rica, para quien decide las compras en Latinoamérica.',
    body1: 'StockAI nace para que los distribuidores, comercios y mayoristas de Latinoamérica dejen de comprar inventario a ciegas. La mayoría compra con Excel e intuición porque las herramientas de pronóstico se hicieron para grandes empresas con equipos de datos — no para una operación que maneja miles de productos con un equipo pequeño.',
    body2: 'StockAI toma el historial de ventas que ya tienes (un CSV o Excel) y lo convierte en decisiones concretas: qué pedir, cuánto, a quién y cuándo. Sin que necesites un analista. Hecho en Costa Rica, pensado para la realidad de las PyMEs de la región.',
  },
  cases: {
    tag: 'Industrias',
    title: 'Hecho para operaciones reales, la tuya incluida.',
    lead: 'El problema de inventario no es el mismo en un mayorista que en un retailer o en una planta de producción. StockAI adapta el control de inventario y la gestión de compras a cómo trabaja cada una.',
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
          'SOBRESTOCK se marca apenas la cobertura llega a tres veces el plazo del proveedor (o al doble del punto de reorden, si es más), no cuando ya toca liquidar.',
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
    title: 'Excel anota. La intuición recuerda. StockAI te dice qué pedir.',
    lead: 'Excel sirve para anotar y la experiencia del comprador vale oro, pero ninguno de los dos alcanza a mirar miles de productos todos los días. El problema aparece cuando el negocio crece y la hoja y la memoria dejan de alcanzar.',
    head: ['Lo que necesitas saber', 'Excel', 'A ojo', 'StockAI'],
    rows: [
      { feature: 'Qué pedir hoy', excel: 'Fila por fila', gut: 'Lo que se recuerde', stockai: 'Lista por urgencia' },
      { feature: 'Cuánto pedir de cada producto', excel: 'A criterio', gut: 'Lo del mes pasado', stockai: 'Cantidad sugerida' },
      { feature: 'Productos que alcanzas a revisar', excel: 'Decenas', gut: 'Los de siempre', stockai: 'Todo el catálogo' },
      { feature: 'Cuánto tarda de verdad cada proveedor', excel: 'De memoria', gut: 'Lo que prometió', stockai: 'Medido' },
      { feature: 'Aviso de lo que se va a quebrar', excel: 'No disponible', gut: 'Cuando llama el cliente', stockai: 'Diario' },
      { feature: 'Dinero parado en sobrestock', excel: 'Difícil de ver', gut: 'Invisible', stockai: 'Marcado' },
      { feature: 'Orden de compra por proveedor', excel: 'A mano', gut: 'A mano', stockai: 'Armada' },
      { feature: 'Temporadas de cada producto', excel: 'A mano', gut: 'De memoria', stockai: 'Automáticas' },
      { feature: 'Probar una promoción antes de comprar', excel: 'Fórmulas a mano', gut: 'No se puede', stockai: 'Simulador' },
      { feature: 'Si el comprador se va de la empresa', excel: 'Queda la hoja', gut: 'Se va con él', stockai: 'Queda en el sistema' },
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
    title: 'Un motor de IA completo. Desde el primer día, también gratis.',
    lead: 'No hay funciones que se desbloqueen pagando. El pronóstico con IA, el semáforo, las órdenes de compra, las bodegas, el optimizador, el simulador, las alertas, el analista y la API vienen completos desde el primer día — también en el plan gratis. Cuando tu operación crece, el plan Completo le da todo el espacio que necesita.',
    valueTitle: 'Contra qué se mide el precio',
    value: [
      { title: 'Un quiebre que no pasa', desc: 'La venta que no pierdes, el cliente que no se va a la competencia y el flete de emergencia que no pagas. StockAI te avisa antes, producto por producto, todos los días.' },
      { title: 'Un pedido de más que no haces', desc: 'Capital que no se queda meses parado en bodega ni termina en liquidación. El sobrestock se marca antes de que se convierta en pérdida.' },
      { title: 'Las horas de tu comprador', desc: 'La lista llega ordenada por urgencia y la orden sale armada por proveedor. Tu equipo deja de revisar fila por fila y se dedica a negociar.' },
    ],
    freeLabel: 'Gratis',
    freePrice: '$0',
    freeNote: 'Para siempre, sin tarjeta. Todas las funciones, con techos pensados para un negocio chico.',
    paidLabel: 'Completo',
    paidNote: 'Para la operación que ya creció. La base mensual incluye {skus} productos, {users} usuarios y {warehouses} bodegas; lo que pase de ahí se suma por bloques. No hay checkout: escríbenos y lo cerramos con tus números.',
    limits: [
      ['Productos (SKUs)', '100', 'Sin límite'],
      ['Usuarios', '2', 'Sin límite'],
      ['Bodegas', '1', 'Sin límite'],
      ['Pronósticos guardados', '3', 'Sin límite'],
      ['Llaves de API', '1', 'Sin límite'],
      ['Llamadas a la API', '500 al día', 'Sin límite'],
      ['Tamaño de archivo', '25 MB', '2 GB'],
    ],
    closing: 'Empieza gratis hoy. Cuando te quede corto — un catálogo que creció, una segunda bodega, un tercero en el equipo — escríbenos y lo ampliamos. Te respondemos en menos de 24 horas.',
    ctaSignup: 'Crear mi cuenta gratis',
    ctaWhatsapp: 'Escríbenos por WhatsApp',
    ctaEmail: 'Escríbenos por correo',
    upgradeTitle: 'Cómo se amplía',
    upgradeSteps: [
      { title: 'Crea tu cuenta gratis', desc: 'Con todas las funciones y sin tarjeta. El plan gratis no vence.' },
      { title: 'Escríbenos cuando te quede corto', desc: 'Por WhatsApp o por correo, cuando el catálogo, las bodegas o el equipo ya no caben. Te respondemos en menos de 24 horas.' },
      { title: 'Lo ampliamos con tus números', desc: 'Acordamos el precio sobre tu operación y ampliamos tu misma cuenta: sin migrar datos ni empezar de cero.' },
    ],
    noStrings: ['Sin tarjeta', 'Sin checkout', 'Sin funciones bloqueadas'],
    waPrefill: 'Hola, quiero ampliar los límites de StockAI.',
    mailSubject: 'StockAI — quiero una cotización',
    paidFrom: 'Desde {price}',
    perMonth: 'al mes',
    calcLink: 'Calcula tu estimado',
    corporate: {
      label: 'Corporativo',
      from: 'Desde {price}',
      perMonth: 'al mes',
      billing: 'Contrato anual',
      lead: 'Para empresas que piden y compran con meses o años de anticipación, con muchas bodegas y muchos usuarios. No es otro producto: tiene las mismas funciones que los demás planes, con topes más amplios que se acuerdan en la cotización.',
      itemsTitle: 'Qué incluye',
      items: [
        'Topes más amplios, acordados en la cotización',
        'Muchas bodegas y muchos usuarios, con flujo de aprobación de pedidos y registro de auditoría',
        'Inicio de sesión único (SSO) de tu empresa, conteo físico de inventario, ingreso de datos por correo y API',
        'Acompañamiento en el arranque y una persona de contacto con nombre',
      ],
      pending: 'Los pedidos comprometidos de tus clientes, sumados al pronóstico, están en construcción y todavía no están disponibles.',
      footnote: 'El precio de partida es una referencia: la cotización final se acuerda contigo, hablando.',
      waPrefill: 'Hola, quiero cotizar el plan corporativo de StockAI.',
      mailSubject: 'StockAI — cotización corporativa',
    },
  },
  calc: {
    tag: 'Calculadora',
    title: 'Cuánto te costaría el plan completo.',
    lead: 'Pon los números de tu operación y te mostramos un estimado mensual con el desglose. Es el punto de partida de la conversación, no una factura.',
    inputs: {
      skus: { label: 'Productos (SKUs)', included: 'La base incluye {n}; luego {price} por cada {per} más.' },
      users: { label: 'Usuarios', included: 'La base incluye {n}; luego {price} por cada usuario más.' },
      warehouses: { label: 'Bodegas', included: 'La base incluye {n}; luego {price} por cada bodega más.' },
      apiCalls: { label: 'Llamadas a la API al mes', included: 'La base incluye {n}; luego {price} por cada {per} más.' },
    },
    resultTitle: 'Estimado mensual',
    base: 'Plan completo, base',
    noExtras: 'Todo cabe en la base',
    lines: {
      skus: 'Productos sobre la base: {n}',
      users: 'Usuarios sobre la base: {n}',
      warehouses: 'Bodegas sobre la base: {n}',
      apiCalls: 'Llamadas sobre la base: {n}',
    },
    freeFits: 'Esto cabe en el plan gratis',
    fullWouldBe: 'Con estos números, el plan completo serían {total} al mes. Este es su desglose:',
    freeFitsNote: 'Con estos números no pagas nada, y el plan gratis no vence. El límite de la API gratis es por día: {n} llamadas.',
    note: 'Estimado. El precio final lo acordamos contigo; no hay checkout ni tarjeta.',
    ctaEmail: 'Enviar este estimado por correo',
    ctaWhatsapp: 'Conversarlo por WhatsApp',
    ctaForm: 'Prefiero que me contacten',
    mailSubject: 'StockAI — estimado del plan completo',
    mailBody: 'Hola, armé este estimado en la calculadora de StockAI:\n\n{summary}\n\nMe gustaría conversarlo.',
    waPrefill: 'Hola, armé un estimado de {total} al mes en la calculadora de StockAI y me gustaría conversarlo.',
    form: {
      title: 'Déjanos tus datos',
      lead: 'Con esto se arma un correo con tu estimado, listo para enviar desde tu propia cuenta.',
      name: 'Nombre',
      company: 'Empresa',
      phone: 'Teléfono',
      message: 'Algo que debamos saber (opcional)',
      submit: 'Preparar el correo',
      hint: 'Se abre tu aplicación de correo con todo escrito. Nada se envía hasta que tú lo mandes.',
    },
  },
  api: {
    tag: 'API',
    title: 'La API se cobra por llamada.',
    lead: 'Tu sistema puede subir ventas y existencias y llevarse el semáforo sin que nadie abra StockAI. En el plan completo cada mes incluye {included} llamadas; pasado eso, {price} por cada {per} más.',
    points: [
      'Lecturas y escrituras cuentan igual: una llamada es una llamada.',
      'El plan gratis incluye {free} llamadas al día, con la misma API completa.',
      'El servidor MCP para asistentes de IA usa las mismas llaves y cuenta contra el mismo límite.',
    ],
    devLink: 'Ver la documentación para desarrolladores',
  },
  trust: {
    tag: 'Confianza',
    title: 'Tus datos son tuyos. Y las reglas, a la vista.',
    lead: 'Lo que conviene saber antes de subir tu primer archivo.',
    items: [
      { title: 'Cada empresa ve solo lo suyo', desc: 'Cada consulta va filtrada por empresa. Tus ventas, tu inventario y tus proveedores no se cruzan con los de nadie, y tus pronósticos se entrenan solo con tu propio historial.' },
      { title: 'Cada quien con su permiso', desc: 'Administrador, analista o solo lectura. Quien solo mira, solo mira: no puede crear pedidos ni cambiar datos.' },
      { title: 'Credenciales cifradas', desc: 'Lo que conectas — como el acceso a tu base de datos — se guarda cifrado. Las contraseñas de tu equipo se guardan con hash bcrypt: nadie puede leerlas, tampoco nosotros.' },
      { title: 'Te llevas tus datos, o los borramos del todo', desc: 'Si un día te vas, te entregamos todos tus datos en un archivo ZIP y borramos la cuenta completa: cada tabla y cada archivo, no solo el registro principal.' },
      { title: 'No es una caja negra', desc: 'La regla que pone un producto en rojo está publicada en esta página, y la puedes hacer a mano para comprobar que da lo mismo.' },
    ],
    decideLink: 'Ver la regla',
    more: 'Seguridad y privacidad, en detalle',
  },
  final: {
    title: 'Pon tu propia IA a trabajar en tus compras. Hoy.',
    lead: 'Tres formas de empezar, según cuánto quieras comprometer ahora. Las tres llegan al mismo producto completo.',
    signupTitle: 'Crea tu cuenta gratis',
    signupDesc: 'Para siempre y con todas las funciones. Sube tu archivo de ventas y ve tu primera lista de qué pedir.',
    trialTitle: 'Mira antes de dar tu correo',
    trialDesc: 'Una cuenta de prueba al instante, con datos de ejemplo. Dura 24 horas y después se borra.',
    talkTitle: 'Habla con nosotros',
    talkDesc: 'Si tu operación ya no cabe en el plan gratis, o quieres verlo con tus datos y acompañado. Respondemos en menos de 24 horas.',
    reach: 'Ventas y contacto: {email}. Teléfono: {phone}.',
    madeIn: 'Hecho en Costa Rica para distribuidores de Latinoamérica.',
  },
  manual: {
    title: 'O léelo entero, con calma.',
    body: 'El manual de usuario cubre las diecinueve pantallas con el mismo detalle: para qué sirve cada una, qué significa cada dato, cómo hacer las cosas concretas y lo que suele confundir. Es el mismo producto de estas capturas, no una versión resumida.',
    cta: 'Descargar el manual (PDF)',
    note: 'PDF · español · 59 páginas',
  },
  tour: {
    title: 'Pantalla por pantalla.',
    lead: 'Capturas reales de la aplicación con datos dentro. Los capítulos son los mismos grupos del menú de StockAI, en el mismo orden: es el mapa que vas a tener cinco minutos después de entrar.',
    teaserTitle: '¿Quieres ver cada pantalla por dentro?',
    teaserLead: 'Una guía aparte, para cuando quieras profundizar: capturas reales de la aplicación con datos, ordenadas igual que su menú, con lo que hace cada pantalla y lo que vas a encontrar en ella.',
    count: '{n} pantallas en {c} capítulos',
    open: 'Ver la guía de pantallas',
    close: 'Cerrar la guía',
    chaptersNav: 'Capítulos de la guía',
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
    all: 'Todas las preguntas, con su respuesta abierta',
    items: [
      { q: '¿Necesito conocimientos estadísticos o de programación para usar StockAI?', a: 'No. StockAI está hecho para quien compra: abres la pantalla y ves qué pedir hoy, cuánto y a qué proveedor. No hay modelos que configurar ni código. Subes tus datos y el resto lo hace el sistema.' },
      // Backed by the engine flow above: router.py, global_trainer.py,
      // evaluation/metrics.py (cost with stockout x3), trainer.py (walk-forward).
      { q: '¿Qué tiene de inteligencia artificial?', a: 'El pronóstico. Para cada producto compiten modelos de machine learning — árboles con gradient boosting (LightGBM y XGBoost), una red neuronal (LSTM) y un modelo global que aprende de todo tu catálogo a la vez — junto a modelos estadísticos clásicos como ARIMA, SARIMAX, Prophet, ETS y Croston. Cada uno se pone a prueba contra tramos de tu propio historial que no vio, y gana el que menos cuesta equivocarse, con un costo en el que quedarse sin producto pesa tres veces más que sobrar. Además, el analista de IA responde preguntas en español sobre tu inventario. Lo que decide el color del semáforo, en cambio, es una regla publicada que puedes revisar a mano.' },
      // backend/inventory/service.py: reorder point = demand over (lead time
      // + review period) + safety stock; the safety stock grows with demand
      // spread and with the supplier's lead-time variability (_safety_stock).
      { q: '¿Qué es el punto de reorden y cómo lo calcula StockAI?', a: 'Es el nivel de existencias en el que conviene pedir: lo que vas a vender mientras esperas que llegue el pedido, más un colchón de seguridad. StockAI lo calcula para cada producto con la demanda que pronostica la IA, el plazo real del proveedor y, si le pides cada cierto tiempo, ese intervalo también. El colchón crece cuando la venta del producto es más variable y cuando el proveedor es irregular en sus entregas, y su tamaño lo decide el nivel de servicio que eliges: para toda la empresa, por proveedor, por categoría o por producto. Cuando la cobertura llega al punto de reorden, el producto pasa a PEDIR PRONTO.' },
      // _calc_recommended: order-up-to against the inventory position (stock + incoming), MOQ.
      { q: '¿Cómo decide StockAI cuánto pedir?', a: 'Mira tu posición de inventario — lo que tienes más lo que ya viene en camino, sea de un proveedor o de un traslado entre bodegas — y sugiere lo que falta para cubrir el plazo del proveedor con su colchón de seguridad, respetando el mínimo de compra. Así no te pide otra vez lo que ya ordenaste. La cantidad es una sugerencia: en la orden la puedes ajustar antes de enviarla.' },
      // backend/whatsapp/tools.py: semaphore_status, list_pending_pos,
      // forecast_summary; read-only since the shared assistant core (backend/assistant/).
      { q: '¿Puedo usar StockAI desde WhatsApp?', a: 'Sí, para lo del día a día. Cada persona vincula y verifica su número, y desde ahí puede preguntar qué productos están en rojo, qué pedidos siguen pendientes y cómo viene el pronóstico, con las cifras de su propia cuenta. El asistente solo consulta: para aprobar o recibir una orden te indica la pantalla de la aplicación donde se hace. El resumen diario de productos en riesgo y las órdenes a proveedores también salen por WhatsApp.' },
      { q: '¿StockAI reemplaza a mi ERP?', a: 'No, lo complementa. Tu ERP registra lo que pasó: ventas, existencias, compras. StockAI lee esa historia, la exportes en un archivo o la traiga directo de tu base de datos, y te dice qué comprar, cuánto y a quién. Sigues facturando y contabilizando donde siempre.' },
      { q: '¿Cómo paso del plan gratis al completo?', a: 'Escribiéndonos, por WhatsApp o por correo. No hay checkout ni se pide tarjeta. En la página de precios hay una calculadora que te da un estimado antes de escribirnos; después conversamos sobre tu operación — cuántos productos, bodegas y personas —, acordamos el precio y ampliamos los límites en tu misma cuenta, con tus datos tal como están. Mientras tanto el plan gratis sigue funcionando con todas las funciones; no vence.' },
      { q: '¿En qué formato debo tener mis datos de ventas?', a: 'StockAI acepta archivos Excel (.xlsx) y CSV. El archivo debe tener al menos una columna de fecha, una columna de identificador del producto (SKU o nombre) y una columna de cantidad vendida. El sistema detecta automáticamente qué columna es cuál.' },
      { q: '¿Qué pasa si tengo productos con muy pocas ventas históricas o datos incompletos?', a: 'StockAI necesita al menos 20 períodos de historial por producto para entrenarlo. Los que no llegan a ese mínimo quedan fuera del pronóstico: no se les inventa una proyección. Antes de correr nada, la revisión del archivo te dice cuántos productos están por debajo del umbral, y si ninguno lo alcanza el archivo se detiene con la explicación en pantalla en vez de producir un resultado vacío. Esos productos siguen apareciendo en tu inventario marcados SIN DATOS — sin señal ni cantidad sugerida — para que la decisión sea tuya y no de un número inventado.' },
      { q: '¿Mis datos están seguros? ¿Quién tiene acceso a ellos?', a: 'Los datos que subes a StockAI son exclusivamente tuyos: no se venden ni se usan para entrenar modelos de otras empresas — cada pronóstico se entrena únicamente con el historial de tu propia cuenta, en nuestro servidor. La única excepción es el asistente de IA: cuando le haces una pregunta, esa pregunta y los datos de tu cuenta que necesita para responderla se envían a un proveedor externo de modelos de lenguaje (DeepSeek) para redactar la respuesta. Fuera de eso, tus datos solo salen cuando tú lo pides: una orden de compra que envías por correo o WhatsApp, o una alerta que elegiste recibir. Cada consulta va filtrada por empresa y el acceso se controla por rol: administrador, analista o solo lectura. Las credenciales de tus integraciones — el usuario y la contraseña de tu base de datos — se guardan cifradas. Tus archivos de ventas y los modelos entrenados se guardan en el servidor de StockAI, en una carpeta separada por empresa; el cifrado del disco depende del servidor donde corre, no lo hace la aplicación. Y la eliminación es completa de verdad: borra cada tabla y cada archivo asociado a tu cuenta, no solo el registro principal.' },
      { q: '¿Cuánto tiempo toma empezar a usar StockAI en mi empresa?', a: 'En la mayoría de casos, menos de un día. Si tienes un archivo de ventas histórico, puedes subirlo y ver tu primera lista de qué pedir en menos de una hora. Para integraciones con ERP o sistemas propios, el tiempo varía según la complejidad.' },
      { q: '¿Se puede integrar con nuestro ERP o sistema de inventario actual?', a: 'La carga normal es por archivo: exportas de tu sistema y subes el CSV o Excel. También puedes conectar StockAI directamente a tu base de datos Postgres o MySQL y traer las ventas con una consulta, sin archivos de por medio. Una integración a medida con tu ERP la armamos con nuestro equipo técnico sobre tu operación, caso por caso — escríbenos y lo vemos.' },
      { q: '¿Cada cuánto se actualiza lo que me dice que pida?', a: 'Cada vez que cargas ventas nuevas. Puedes lanzarlo tú al subir el archivo del mes, o dejar el recálculo programado para que corra solo: cada lunes a las 6, todos los días, solo días hábiles, cada hora o el primero de cada mes.' },
      { q: '¿StockAI sirve si tengo más de una bodega?', a: 'Sí. El plan gratis trae una bodega; ampliando el plan no hay tope de ubicaciones. Defines rutas entre bodegas con los días de tránsito y el costo. Cuando un producto está corto en una bodega y sobrado en otra, StockAI sugiere mover en lugar de comprar, y solo lo sugiere si a la bodega que presta le quedan al menos 30 días de cobertura.' },
      { q: '¿De dónde saca StockAI el plazo de entrega de cada proveedor?', a: 'Al principio, del que escribes tú en la ficha del proveedor. Cada vez que registras una recepción, StockAI guarda cuántos días pasaron de verdad entre la orden y la entrega. A partir de la tercera recepción de ese proveedor empieza a usar el promedio real en lugar del plazo declarado, y te muestra cuál de los dos está usando.' },
      { q: '¿Qué tan grande puede ser el archivo de ventas que subo?', a: 'En el plan gratis, hasta 25 MB por archivo. Pasando al plan completo, hasta 2 GB. Para dimensionarlo: 3 años de historial con 5.000 productos y venta diaria son unos 5 millones de filas, del orden de 200 MB en CSV — dentro del plan completo.' },
      // Moved here from the models section on 2026-10-02 (owner): the same
      // three limits that used to sit under "Lo que no te vamos a prometer",
      // plus the one the purchase-order flow already makes true (the buyer
      // reviews and sends every order). Kept LAST on purpose.
      { q: '¿Qué no hace StockAI?', a: 'Preferimos decírtelo antes de que subas tu primer archivo. No publicamos un porcentaje de precisión en esta página: el error de cada producto se mide sobre tu propio historial y lo ves en la pantalla de pronóstico. El rango del pronóstico no es una garantía: es la franja donde probablemente caerá la venta, y sirve para saber cuánto confiar en la cantidad sugerida. No adivina lo que nunca pasó: una promoción nueva o un cliente grande nuevo se prueban en el simulador de escenarios, no en el historial. Y no compra por ti: te sugiere qué, cuánto y a quién, y la orden la revisas, la ajustas y la envías tú.' },
    ],
  },
  misc: {
    signalHead: ['Señal', 'Cuándo aparece', 'En el ejemplo'],
    roleToday: 'Hoy',
    roleWith: 'Con StockAI',
    exampleTitle: 'Un ejemplo',
    exampleBody: 'Vendes 20 unidades al día de un producto, tu proveedor tarda 15 días en entregar y tu colchón de seguridad equivale a 5 días de venta. Tu punto de reorden queda en 20 días: 400 unidades. Con eso, los cuatro estados del semáforo quedan en cantidades concretas — las de la tabla de abajo. Cambia cualquiera de los números y los cortes se mueven solos, producto por producto.',
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
  pages: {
    home: 'Inicio',
    breadcrumb: 'Estás en',
    onThisPage: 'En esta página',
    related: 'Sigue leyendo',
    pricing: {
      label: 'Precios',
      title: 'Precios de StockAI: gratis para empezar, premium cuando creces.',
      intro: 'El plan gratis es para siempre y trae todas las funciones; lo único que tiene son techos de tamaño. Aquí están esos techos, lo que cambia al ampliarlos y cómo se hace: conversando con nosotros, sin tarjeta y sin checkout.',
    },
    how: {
      label: 'Cómo funciona',
      title: 'Cómo funciona StockAI: los pasos, la regla y cada pantalla.',
      intro: 'Primero, el recorrido completo, paso a paso, y cómo compiten los modelos de IA por cada producto. Después, la cuenta exacta con la que un producto pasa a rojo, para que la compruebes a mano. Y al final, la aplicación pantalla por pantalla, con capturas reales y datos dentro.',
    },
    faq: {
      label: 'Preguntas frecuentes',
      title: 'Preguntas frecuentes sobre StockAI.',
      intro: 'Lo que suelen preguntar los equipos de compras antes de subir su primer archivo: qué datos hacen falta, cómo se amplía el plan gratis, dónde quedan tus datos y cómo aprende el plazo de cada proveedor.',
    },
    security: {
      label: 'Seguridad',
      title: 'Seguridad y privacidad de tus datos en StockAI.',
      intro: 'Antes de subir tus ventas conviene saber quién puede verlas, qué se guarda cifrado y qué pasa con todo si un día te vas. Esto es lo que hace la aplicación hoy.',
      trialTitle: 'La cuenta de prueba se borra sola',
      trialDesc: 'Si pruebas sin registrarte, la cuenta temporal trae datos de ejemplo, dura 24 horas y después se borra.',
      ruleDesc: 'La regla que pone un producto en rojo está publicada en «Cómo funciona», y la puedes hacer a mano para comprobar que da lo mismo.',
    },
  },
}

const en: LandingCopy = {
  nav: {
    links: [
      ['/como-funciona', 'How it works'],
      ['#motor', 'The models'],
      ['/industrias', 'Industries'],
      ['/precios', 'Pricing'],
      ['/desarrolladores', 'API'],
      ['/docs', 'Help'],
      ['#contacto', 'Contact'],
    ],
    signIn: 'Sign in',
    signUp: 'Create account',
    menu: 'Menu',
    ariaLabel: 'Main navigation',
  },
  heroPills: ['Distribution', 'Retail', 'Manufacturing', 'Wholesale', 'E-commerce'],
  footerLinks: {
    product: [['/como-funciona', 'How it works'], ['/como-se-calcula', 'How it is calculated'], ['#motor', 'The models'], ['#funciones', 'Features'], ['/industrias', 'Industries'], ['/stockai-vs-excel', 'StockAI vs. Excel'], ['/integraciones', 'Integrations'], ['/precios', 'Pricing'], ['/desarrolladores', 'Developer API'], ['/novedades', 'What’s new']],
    company: [['#problema', 'The problem'], ['#nosotros', 'About us'], ['/seguridad', 'Security'], ['/preguntas-frecuentes', 'FAQ'], ['#contacto', 'Contact']],
  },
  hero: {
    eyebrow: 'Inventory software with forecasting AI, for distributors and wholesalers',
    title1: 'Your own team of data scientists,',
    title2: 'deciding with you what to buy.',
    lead: 'StockAI trains an artificial intelligence for every one of your products: up to nine models compete on your own history, and the one whose mistakes cost you least is kept. Every morning it tells you what is about to run out, how many units to order, from which supplier, and how much money is sitting in what does not turn.',
    cta: 'Start free with sample data',
    ctaTrial: 'Try it without signing up',
    trialNote: 'Instant trial account: temporary username and password, 24 hours, no card.',
    frame: 'StockAI · Purchasing dashboard',
  },
  strip: {
    models: 'AI and statistical models competing for every product',
    states: 'Clear states per product: order now, order soon, ok or overstock',
    deliveries: 'Deliveries and it knows your supplier’s real lead time',
    skus: 'Products per catalogue it is tested against',
  },
  problem: {
    tag: 'The problem',
    title: 'Every stockout and every over-order costs you real money.',
    lead: 'Most companies buy on a spreadsheet, accumulated instinct and whichever buyer is on duty. That works up to a point — and the point arrives sooner than it looks.',
    items: [
      { title: 'Stockouts in the season that matters', desc: 'In retail and distribution, running out during peak season is not just a lost sale — the customer goes to the competition and does not come back. Demand does not wait for your next replenishment cycle.' },
      { title: 'Capital trapped in overstock', desc: 'For wholesalers and manufacturers, excess inventory fills the warehouse, eats the credit line, and in perishable or seasonal categories ends as a straight loss at clearance.' },
      { title: 'Reactive buying instead of planned buying', desc: 'Ordering once stock is already critical means accepting bad terms: spot prices, emergency freight and lead times outside the normal cycle.' },
    ],
  },
  how: {
    tag: 'How it works',
    title: 'AI demand forecasting: you upload your sales, the AI does the rest.',
    lead: 'From your sales file to the purchase order in eight steps, and you only do two: upload the file and record what arrives. StockAI works through everything else, product by product, and every step leaves something you can check on screen.',
    more: 'See the whole journey, screen by screen',
  },
  morning: {
    tag: 'Your morning with StockAI',
    title: 'From the alert to the order sent, before your second coffee.',
    lead: 'This is what a buying day looks like when the AI has already done the heavy lifting overnight. Every step is a screen or a channel that exists in StockAI today.',
    steps: [
      { when: 'Before you arrive', title: 'What is urgent is already waiting', desc: 'The daily summary of products that moved into risk reaches your inbox and your WhatsApp. If you left the scheduled recalculation on, the list is already up to date with yesterday’s sales.' },
      { when: 'On the dashboard', title: 'One screen, the only thing to decide', desc: 'How many products are at risk today and this week, a written summary of the day’s risks, opportunities and actions, and the orders that should already have arrived.' },
      { when: 'In inventory', title: 'What to order, and how much, already worked out', desc: 'Your whole catalogue in ORDER NOW, ORDER SOON, OK and OVERSTOCK. The suggested quantity already accounts for what is on its way and is measured against each supplier’s real lead time.' },
      { when: 'In orders', title: 'The order comes out grouped by supplier', desc: 'With the reason for every line. You review it, adjust it and send it by WhatsApp or email, or export it as a PDF for your system.' },
      { when: 'When the goods arrive', title: 'Every reception teaches it something', desc: 'You record the arrival, complete or partial, and StockAI notes how many days it really took. From the third delivery, that supplier is planned with its real lead time.' },
      { when: 'When a question comes up', title: 'You ask your AI analyst', desc: 'In the app or on WhatsApp: what is in the red, which orders are still pending, how the forecast looks. It answers with your own account’s figures and points you to the screen where each thing is done: approving and receiving orders stays yours, in the app.' },
    ],
  },
  features: {
    tag: 'Features',
    title: 'Complete inventory and purchasing software, on the free plan too.',
    lead: 'Everything StockAI does, grouped the way a buyer thinks about it.',
    groups: [
      { name: 'Purchasing', items: [
        'A list of what to order today, sorted by urgency',
        'A suggested quantity per product, net of what is already on its way',
        'Purchase orders grouped by supplier, with the reason for every line',
        'A daily summary of the products at risk, by email and WhatsApp',
        'Sent to the supplier by WhatsApp or email; exported as CSV or PDF',
        'An optimiser for the lowest-total-cost order, fixed freight included',
        'Volume price breaks: how far the next tier is',
      ] },
      { name: 'AI forecasting', items: [
        'A model competition per product, tested against your own past',
        'A likely sales range around every forecast',
        'Stockout days are not learned as missing demand',
        'Your country’s holidays and days of the month as model variables',
        'Daily, weekly, monthly or quarterly',
        'Scenario simulator: promotions, delays and higher demand, up to 50 rules per scenario',
        'Scheduled recalculation: every Monday, daily, on business days, hourly or on the 1st of the month',
      ] },
      { name: 'Inventory and warehouses', items: [
        'A signal per product: ORDER NOW, ORDER SOON, OK and OVERSTOCK',
        'Several warehouses, each on its own and consolidated',
        'Transfers between warehouses before buying',
        'ABC-XYZ classification to place the safety stock',
        'Bills of materials for manufacturing',
      ] },
      { name: 'Suppliers', items: [
        'The real lead time, learned from the third reception',
        'Scorecard: declared against real lead time, on-time and complete deliveries',
        'Each supplier’s order cycle inside the reorder point',
        'Service-level and lead-time rules per supplier or category',
      ] },
      { name: 'Finance and capital', items: [
        'Money sitting in products that do not turn, in plain view',
        'Overstock flagged before it is clearance time',
        'Reports in Excel and PDF, ready to share',
        'A monthly summary built from your own records',
        'Your figures in your company’s currency',
      ] },
      { name: 'Integrations and team', items: [
        'Upload by CSV or Excel, exactly as it leaves your system',
        'A direct connection to your Postgres or MySQL database',
        'A REST API with the app’s actions, for your own system',
        'A read-only MCP server for AI assistants',
        'Administrator, analyst and read-only roles',
        'Internal messaging, with a WhatsApp heads-up',
      ] },
    ],
  },
  engine: {
    flowLabel: 'The StockAI workflow, step by step',
    flow: [
      { title: 'Your sales', desc: 'A CSV or Excel file exactly as it comes out of your system. StockAI works out which column is the date, the product and the quantity sold.', detail: 'Date, product and quantity: that is enough' },
      { title: 'Review and clean-up', desc: 'Before training, every product is checked: missing dates, outliers, too little history. If the file would produce a wrong forecast it does not run until you choose how to fix it, because filling gaps with zero and interpolating them say different things about what happened.', detail: 'With your stock history, days with nothing on the shelf stop counting as days with no demand' },
      { title: 'A model competition per product', desc: 'Each product is classified by how it sells (steady, seasonal, intermittent or volatile) and the models that handle that pattern best compete, out of nine available: gradient-boosted trees, classical statistical models and a neural network.', detail: 'LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, LSTM and a global model' },
      { title: 'Tested against the past', desc: 'Each model has the last stretch of your history hidden from it and is asked to forecast it, over several cut-offs. The one whose mistakes cost least wins, and it has to beat two naive forecasts.', detail: 'Falling short weighs three times more than overshooting' },
      { title: 'Forecast with a range', desc: 'The winner is trained again on your whole history, the latest weeks included, and projects demand with a likely range around it.', detail: 'Daily, weekly, monthly or quarterly' },
      { title: 'Signal and quantity', desc: 'Forecast demand is set against your stock on hand and your supplier’s lead time. Every product lands on ORDER NOW, ORDER SOON, OK or OVERSTOCK, with a suggested quantity.', detail: 'The rule is published and can be done by hand' },
      { title: 'Purchase order', desc: 'The order comes out grouped by supplier, with the reason for every line. You review it, adjust it and send it by email or WhatsApp.', detail: 'Exportable as CSV or PDF' },
      { title: 'A reception that teaches the lead time', desc: 'When you record an arrival, StockAI notes how many days it really took. From a supplier’s third delivery it plans with that lead time, not the promised one.', detail: 'And that lead time feeds back into the signal' },
    ],
    illus: {
      caption: 'An illustration of the process for one product. Not real data.',
      history: 'History',
      today: 'Today',
      candidates: 'Candidates',
      winner: 'Winner',
      band: 'Likely range',
      signal: 'ORDER SOON',
    },
    tag: 'The models',
    title: 'An AI competition for every product. The cheapest mistake wins.',
    lead: 'A product that sells every day is not forecast the way one that sits for weeks without moving is. So StockAI does not run one model over the whole catalogue: it puts machine-learning models, a neural network and classical statistical models in competition on each product’s history, and plans with the winner. It is the work a data-science team would do, running on its own across your whole catalogue.',
    routingTitle: 'Who competes, depending on how it sells',
    routingHead: ['How it sells', 'Models that compete'],
    routing: [
      { pattern: 'Steady', when: 'Regular sales, no marked cycle', models: 'LightGBM, XGBoost, ARIMA, SARIMAX, LSTM' },
      { pattern: 'Seasonal', when: 'A pattern repeats: the week, payday, the season', models: 'Prophet, ETS, LightGBM, SARIMAX, LSTM' },
      { pattern: 'Intermittent', when: 'Many periods without a single sale', models: 'Croston, ETS' },
      { pattern: 'Volatile', when: 'Large swings from one period to the next', models: 'LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX' },
      { pattern: 'Short history', when: 'Fewer than 30 periods', models: 'Every model you have active, unfiltered' },
    ],
    routingNote: 'A product can carry two patterns at once, seasonal and volatile for instance, and then the models for both compete. The classification only narrows the models you have active: it never trains one you did not choose.',
    alwaysTitle: 'In every competition',
    always: [
      { title: 'A global model, trained on your whole catalogue', desc: 'It competes on every product. It helps most on those with little history or intermittent sales: it learns from how the rest of your catalogue behaves, which no single-product model can do.' },
      { title: 'Two naive forecasts as the yardstick', desc: 'Repeat the last value, and repeat last season. They cannot win, but they do measure: if no model beats them on a product, the run tells you instead of hiding it.' },
    ],
    ideasTitle: 'What makes this AI engine different',
    ideas: [
      { title: 'The winner is the one whose mistakes cost least', desc: 'Running out loses you sales; overstock ties up cash. They do not cost the same, so the winner is chosen on a cost where falling short weighs three times more than overshooting, measured across the whole horizon you are buying for.' },
      { title: 'A stockout is not mistaken for no demand', desc: 'If a product ran out, its zero sales do not say nobody wanted it. With your stock history, StockAI marks those days and estimates what would have sold, so it does not learn the stockout as a slump and under-order again.' },
      { title: 'Tested on dates it never saw, trained on all of them', desc: 'The competition is judged on a stretch the model never saw. Once chosen, the winner is retrained on the full history, so your latest weeks of sales are in the forecast you buy from.' },
      { title: 'If the file is not enough, no number is invented', desc: 'A product without enough history stays out of the forecast and shows as NO DATA, with no suggested quantity. And a file that would produce a wrong forecast is stopped before training, with the explanation on screen.' },
    ],
    assistantTitle: 'And an AI analyst you can just ask',
    assistantBody: 'Ask your inventory what you would ask your best analyst: "which supplier is running late?", "why did this category go up?". It answers about your own account, reading the same signal and the same orders you see on screen, and every answer says where it came from.',
    assistantPoints: [
      'It writes and explains; order quantities are computed by the forecasting engine, not the chat.',
      'It uses an external language model. If that is unavailable, the screen says so before you type.',
    ],
  },
  decide: {
    tag: 'The rule, no mystery',
    title: 'Reorder point and signal: how StockAI decides when to buy.',
    lead: 'The AI forecasts; the decision to buy rests on a rule in plain sight. No recommendation comes out of a black box: you can do the arithmetic by hand and check it gives the same answer.',
    formulaTitle: 'The arithmetic',
    formulaBody: 'Coverage = stock on hand ÷ forecast daily demand: how many days you last if nothing else arrives. That figure is compared against two references. Your supplier’s lead time, the days they take to deliver once you place the order. And your reorder point: what you will sell while you wait for that order, plus a safety buffer.',
    formulaBody2: 'It is the logic you already run in your head, only applied to the thousands of codes you never get to review: if what you have cannot cover the wait, it is time to order.',
    configBody: 'You decide the buffer: it comes from the service level you choose, for the whole company, per supplier, per category or per product. And if you order from a supplier on a fixed cycle, that interval goes into the reorder point too. And the cut-offs are yours too: “order now” below half the lead time and “overstock” from three times the lead time come as defaults, but you change them for the whole company, per supplier or per category, and before saving you see how many products would change signal.',
    leadTimeBody: 'The lead time is half the arithmetic, so it had better be the real one. Every time you record a reception, StockAI stores how many days actually passed — and from the third one it plans with that average instead of the one you were promised.',
    signals: [
      { signal: 'ORDER NOW', rule: 'Coverage does not even reach half the supplier’s lead time: you are already late', example: 'Under 7.5 days · under 150 units' },
      { signal: 'ORDER SOON', rule: 'Coverage is at or below your reorder point: order now or the shipment lands after the shelf is empty', example: '7.5 to 20 days · 150 to 400 units' },
      { signal: 'OK', rule: 'Coverage is past the reorder point, short of the overstock threshold', example: 'Over 20 and under 45 days · 400 to 900 units' },
      { signal: 'OVERSTOCK', rule: 'Coverage reaches three times the lead time, or twice the reorder point if that is more', example: '45 days or more · 900 units or more' },
    ],
  },
  about: {
    tag: 'About us',
    title: 'Made in Costa Rica, for the people who decide the buying across Latin America.',
    body1: 'StockAI exists so distributors, shops and wholesalers across Latin America stop buying inventory blind. Most of them buy on a spreadsheet and instinct because forecasting tools were built for large companies with data teams — not for an operation handling thousands of products with a small one.',
    body2: 'StockAI takes the sales history you already have (a CSV or Excel) and turns it into concrete decisions: what to order, how much, from whom and when. No analyst required. Made in Costa Rica, built for how small and mid-sized companies in the region actually work.',
  },
  cases: {
    tag: 'Industries',
    title: 'Built for real operations, yours included.',
    lead: 'The inventory problem is not the same for a wholesaler as for a retailer or a production plant. StockAI fits inventory control and purchasing to how each one works.',
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
          'OVERSTOCK is flagged as soon as coverage reaches three times the supplier’s lead time (or twice the reorder point, if that is more) — not when it is already clearance time.',
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
    title: 'Excel takes notes. Instinct remembers. StockAI tells you what to order.',
    lead: 'Excel is for writing things down and an experienced buyer is worth gold, but neither can look at thousands of products every day. The problem shows up when the business grows and the spreadsheet and the memory stop keeping up.',
    head: ['What you need to know', 'Excel', 'Gut feeling', 'StockAI'],
    rows: [
      { feature: 'What to order today', excel: 'Row by row', gut: 'Whatever comes to mind', stockai: 'List by urgency' },
      { feature: 'How much of each product', excel: 'Judgement call', gut: 'Last month’s amount', stockai: 'Suggested quantity' },
      { feature: 'Products you get to review', excel: 'Dozens', gut: 'The usual ones', stockai: 'The whole catalogue' },
      { feature: 'How long each supplier really takes', excel: 'From memory', gut: 'What they promised', stockai: 'Measured' },
      { feature: 'Warning of what is about to run out', excel: 'Not available', gut: 'When the customer calls', stockai: 'Daily' },
      { feature: 'Money stuck in overstock', excel: 'Hard to see', gut: 'Invisible', stockai: 'Flagged' },
      { feature: 'Purchase order per supplier', excel: 'By hand', gut: 'By hand', stockai: 'Built for you' },
      { feature: 'Each product’s seasons', excel: 'By hand', gut: 'From memory', stockai: 'Automatic' },
      { feature: 'Testing a promotion before buying', excel: 'Hand-built formulas', gut: 'Not possible', stockai: 'Simulator' },
      { feature: 'If the buyer leaves the company', excel: 'The sheet stays', gut: 'It leaves with them', stockai: 'It stays in the system' },
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
    title: 'A complete AI engine. From day one, free plan included.',
    lead: 'There are no features that unlock by paying. AI forecasting, the signal, purchase orders, warehouses, the optimiser, the simulator, the alerts, the analyst and the API are all complete from day one — on the free plan too. When your operation grows, the Full plan gives it all the room it needs.',
    valueTitle: 'What the price is weighed against',
    value: [
      { title: 'A stockout that never happens', desc: 'The sale you keep, the customer who does not go to the competition and the emergency freight you do not pay. StockAI warns you first, product by product, every day.' },
      { title: 'An over-order you never place', desc: 'Capital that does not sit in the warehouse for months or end up in a clearance sale. Overstock is flagged before it becomes a loss.' },
      { title: 'Your buyer’s hours', desc: 'The list arrives sorted by urgency and the order comes out grouped by supplier. Your team stops checking row by row and gets on with negotiating.' },
    ],
    freeLabel: 'Free',
    freePrice: '$0',
    freeNote: 'Forever, no card. Every feature, with ceilings sized for a small operation.',
    paidLabel: 'Full',
    paidNote: 'For the operation that has grown. The monthly base includes {skus} products, {users} users and {warehouses} warehouses; anything past that is added in blocks. No checkout: write to us and we settle it with your numbers.',
    limits: [
      ['Products (SKUs)', '100', 'Unlimited'],
      ['Users', '2', 'Unlimited'],
      ['Warehouses', '1', 'Unlimited'],
      ['Saved forecasts', '3', 'Unlimited'],
      ['API keys', '1', 'Unlimited'],
      ['API calls', '500 a day', 'Unlimited'],
      ['File size', '25 MB', '2 GB'],
    ],
    closing: 'Start free today. When you outgrow it — a catalogue that grew, a second warehouse, a third person on the team — write to us and we lift it. We answer within 24 hours.',
    ctaSignup: 'Create my free account',
    ctaWhatsapp: 'Message us on WhatsApp',
    ctaEmail: 'Email us',
    upgradeTitle: 'How it grows',
    upgradeSteps: [
      { title: 'Create your free account', desc: 'Every feature, no card. The free plan does not expire.' },
      { title: 'Write to us when you outgrow it', desc: 'On WhatsApp or by email, when the catalogue, the warehouses or the team no longer fit. We answer within 24 hours.' },
      { title: 'We lift it, with your numbers', desc: 'We agree the price around your operation and lift the limits on the same account: no data to migrate, no starting over.' },
    ],
    noStrings: ['No card', 'No checkout', 'No locked features'],
    waPrefill: 'Hi, I would like to lift my StockAI limits.',
    mailSubject: 'StockAI — I would like a quote',
    paidFrom: 'From {price}',
    perMonth: 'a month',
    calcLink: 'Work out your estimate',
    corporate: {
      label: 'Corporate',
      from: 'From {price}',
      perMonth: 'a month',
      billing: 'Annual contract',
      lead: 'For companies that order and buy months or years ahead, with many warehouses and many users. It is not a different product: it has the same features as every other plan, with larger ceilings agreed in the quote.',
      itemsTitle: 'What it includes',
      items: [
        'Larger ceilings, agreed in the quote',
        'Many warehouses and many users, with a purchase-order approval workflow and an audit trail',
        "Your company's single sign-on (SSO), physical stock counts, data intake by e-mail and the API",
        'Onboarding support and a named contact person',
      ],
      pending: "Your customers' committed orders, layered on top of the forecast, are being built and are not available yet.",
      footnote: 'The starting price is a reference: the final quote is agreed with you, in a conversation.',
      waPrefill: 'Hi, I would like a quote for the StockAI corporate plan.',
      mailSubject: 'StockAI — corporate quote',
    },
  },
  calc: {
    tag: 'Calculator',
    title: 'What the full plan would cost you.',
    lead: 'Put in your operation’s numbers and we show you a monthly estimate with the breakdown. It is where the conversation starts, not an invoice.',
    inputs: {
      skus: { label: 'Products (SKUs)', included: 'The base includes {n}; then {price} per {per} more.' },
      users: { label: 'Users', included: 'The base includes {n}; then {price} per extra user.' },
      warehouses: { label: 'Warehouses', included: 'The base includes {n}; then {price} per extra warehouse.' },
      apiCalls: { label: 'API calls per month', included: 'The base includes {n}; then {price} per {per} more.' },
    },
    resultTitle: 'Monthly estimate',
    base: 'Full plan, base',
    noExtras: 'Everything fits in the base',
    lines: {
      skus: 'Products over the base: {n}',
      users: 'Users over the base: {n}',
      warehouses: 'Warehouses over the base: {n}',
      apiCalls: 'Calls over the base: {n}',
    },
    freeFits: 'This fits the free plan',
    fullWouldBe: 'With these numbers the full plan would be {total} a month. Its breakdown:',
    freeFitsNote: 'With these numbers you pay nothing, and the free plan does not expire. The free API limit is per day: {n} calls.',
    note: 'An estimate. We agree the final price with you; there is no checkout and no card.',
    ctaEmail: 'Email this estimate',
    ctaWhatsapp: 'Talk it over on WhatsApp',
    ctaForm: 'I would rather you contact me',
    mailSubject: 'StockAI — full plan estimate',
    mailBody: 'Hi, I put this estimate together in the StockAI calculator:\n\n{summary}\n\nI would like to talk it over.',
    waPrefill: 'Hi, I worked out an estimate of {total} a month in the StockAI calculator and would like to talk it over.',
    form: {
      title: 'Leave us your details',
      lead: 'This builds an email with your estimate, ready to send from your own account.',
      name: 'Name',
      company: 'Company',
      phone: 'Phone',
      message: 'Anything we should know (optional)',
      submit: 'Prepare the email',
      hint: 'Your email app opens with everything written. Nothing is sent until you send it.',
    },
  },
  api: {
    tag: 'API',
    title: 'The API is priced per call.',
    lead: 'Your system can push sales and stock and take the signal back without anyone opening StockAI. On the full plan every month includes {included} calls; past that, {price} per {per} more.',
    points: [
      'Reads and writes count the same: a call is a call.',
      'The free plan includes {free} calls a day, with the same complete API.',
      'The MCP server for AI assistants uses the same keys and counts against the same limit.',
    ],
    devLink: 'Read the developer documentation',
  },
  trust: {
    tag: 'Trust',
    title: 'Your data is yours. And the rules are in plain sight.',
    lead: 'What is worth knowing before you upload your first file.',
    items: [
      { title: 'Each company sees only its own', desc: 'Every query is filtered by company. Your sales, stock and suppliers never mix with anyone else’s, and your forecasts are trained only on your own history.' },
      { title: 'Everyone with their own permission', desc: 'Administrator, analyst or read-only. Whoever only looks, only looks: they cannot create orders or change data.' },
      { title: 'Encrypted credentials', desc: 'What you connect — such as access to your database — is stored encrypted. Your team’s passwords are stored as bcrypt hashes: nobody can read them, us included.' },
      { title: 'Take your data with you, or have it fully erased', desc: 'If you ever leave, we hand you all your data in a ZIP file and erase the whole account: every table and every file, not just the main record.' },
      { title: 'Not a black box', desc: 'The rule that puts a product in the red is published on this page, and you can do it by hand to check it gives the same answer.' },
    ],
    decideLink: 'See the rule',
    more: 'Security and privacy, in detail',
  },
  final: {
    title: 'Put your own AI to work on your buying. Today.',
    lead: 'Three ways to start, depending on how much you want to commit right now. All three lead to the same complete product.',
    signupTitle: 'Create your free account',
    signupDesc: 'Forever, with every feature. Upload your sales file and see your first list of what to order.',
    trialTitle: 'Look before giving your email',
    trialDesc: 'An instant trial account with sample data. It lasts 24 hours and is then erased.',
    talkTitle: 'Talk to us',
    talkDesc: 'If your operation no longer fits the free plan, or you want to see it with your data and someone beside you. We answer within 24 hours.',
    reach: 'Sales and contact: {email}. Phone: {phone}.',
    madeIn: 'Made in Costa Rica for distributors across Latin America.',
  },
  manual: {
    title: 'Or read the whole thing, unhurried.',
    body: 'The user manual covers all nineteen screens at the same depth: what each one is for, what every figure means, how to do the concrete things, and what tends to confuse people. Same product as these captures, not a shortened version.',
    cta: 'Download the manual (PDF)',
    note: 'PDF · English · 59 pages',
  },
  tour: {
    title: 'Screen by screen.',
    lead: 'Real captures of the running app, with data in them. The chapters are the same groups as StockAI’s own menu, in the same order: it is the map you will have five minutes after signing in.',
    teaserTitle: 'Want to see every screen from the inside?',
    teaserLead: 'A separate guide, for when you want to go deeper: real captures of the app with data in it, ordered like its own menu, with what each screen does and what you will find there.',
    count: '{n} screens in {c} chapters',
    open: 'Open the screen guide',
    close: 'Close the guide',
    chaptersNav: 'Guide chapters',
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
    all: 'Every question, with its answer open',
    items: [
      { q: 'Do I need statistics or programming knowledge to use StockAI?', a: 'No. StockAI is built for the person who buys: you open the screen and see what to order today, how much and from which supplier. There are no models to configure and no code. You upload your data and the system does the rest.' },
      { q: 'What is actually AI about it?', a: 'The forecast. For every product, machine-learning models — gradient-boosted trees (LightGBM and XGBoost), a neural network (LSTM) and a global model that learns from your whole catalogue at once — compete alongside classical statistical models such as ARIMA, SARIMAX, Prophet, ETS and Croston. Each one is tested against stretches of your own history it never saw, and the winner is the one whose mistakes cost least, on a cost where running out weighs three times more than overshooting. On top of that, the AI analyst answers questions about your inventory in plain language. What sets the colour of the signal, on the other hand, is a published rule you can check by hand.' },
      { q: 'What is the reorder point, and how does StockAI work it out?', a: 'It is the stock level at which it is time to order: what you will sell while you wait for the order to arrive, plus a safety buffer. StockAI works it out for every product from the demand the AI forecasts, the supplier’s real lead time and, if you order from them on a fixed cycle, that interval too. The buffer grows when the product’s sales are more variable and when the supplier is irregular in its deliveries, and its size is set by the service level you choose: for the whole company, per supplier, per category or per product. When coverage reaches the reorder point, the product moves to ORDER SOON.' },
      { q: 'How does StockAI decide how much to order?', a: 'It looks at your inventory position — what you have plus what is already on its way, whether from a supplier or a transfer between warehouses — and suggests what is missing to cover the supplier’s lead time with its safety buffer, respecting the minimum order quantity. So it never asks you to order again what you already ordered. The quantity is a suggestion: you can adjust it on the order before sending it.' },
      { q: 'Can I use StockAI from WhatsApp?', a: 'Yes, for the day-to-day. Each person links and verifies their number, and from there they can ask which products are in the red, which orders are still pending and how the forecast looks, with their own account’s figures. The assistant only reads: to approve or receive an order it points you to the screen in the app where that is done. The daily summary of at-risk products and orders to suppliers go out by WhatsApp too.' },
      { q: 'Does StockAI replace my ERP?', a: 'No, it complements it. Your ERP records what happened: sales, stock, purchases. StockAI reads that history, whether you export it as a file or it pulls it straight from your database, and tells you what to buy, how much and from whom. You keep invoicing and accounting where you always have.' },
      { q: 'How do I move from the free plan to the full one?', a: 'By writing to us, on WhatsApp or by email. There is no checkout and no card is asked for. The pricing page has a calculator that gives you an estimate before you write; then we talk about your operation (how many products, warehouses and people), agree the price and lift the limits on the same account, with your data exactly as it is. Meanwhile the free plan keeps working with every feature; it does not expire.' },
      { q: 'What format does my sales data need to be in?', a: 'StockAI accepts Excel (.xlsx) and CSV. The file needs at least a date column, a product identifier column (SKU or name) and a quantity-sold column. The system works out which column is which.' },
      { q: 'What if I have products with very little sales history, or incomplete data?', a: 'StockAI needs at least 20 periods of history per product to train it. Products below that minimum stay out of the forecast: no projection is invented for them. Before anything runs, the file review tells you how many products are under the threshold, and if none of them clears it the file is stopped with the explanation on screen instead of producing an empty result. Those products still appear in your inventory marked NO DATA — no signal and no suggested quantity — so the decision is yours and not an invented number’s.' },
      { q: 'Is my data safe? Who has access to it?', a: 'The data you upload to StockAI is exclusively yours: it is not sold and it is not used to train models for other companies — every forecast is trained only on your own account’s history, on our server. The one exception is the AI assistant: when you ask it something, that question and the account data it needs to answer are sent to an external language-model provider (DeepSeek) to write the reply. Beyond that, your data only leaves when you ask it to: a purchase order you send by email or WhatsApp, or an alert you chose to receive. Every query is filtered by company and access is controlled by role: administrator, analyst or read-only. Your integration credentials — the user and password of your database — are stored encrypted. Your sales files and trained models are kept on StockAI’s server, in a folder separated per company; disk encryption is a property of the server it runs on, not something the application does. And deletion is genuinely complete: it removes every table and every file tied to your account, not just the main record.' },
      { q: 'How long does it take to start using StockAI in my company?', a: 'In most cases, under a day. If you have a historical sales file you can upload it and see your first list of what to order in under an hour. For ERP or in-house system integrations, the time depends on the complexity.' },
      { q: 'Can it integrate with our current ERP or inventory system?', a: 'The normal path is by file: export from your system and upload the CSV or Excel. You can also connect StockAI straight to your Postgres or MySQL database and pull sales with a query, with no file in between. A custom ERP integration is something we build with our technical team around your operation, case by case — write to us and we will look at it.' },
      { q: 'How often does what it tells me to order get updated?', a: 'Every time you load new sales. You can launch it yourself when you upload the month’s file, or leave the scheduled recalculation running: every Monday at 6, every day, weekdays only, hourly, or the first of each month.' },
      { q: 'Is StockAI useful if I have more than one warehouse?', a: 'Yes. The free plan comes with one warehouse; on the full plan there is no cap on locations. You define routes between warehouses with transit days and cost. When a product is short in one warehouse and long in another, StockAI suggests moving instead of buying, and only suggests it if the lending warehouse keeps at least 30 days of coverage.' },
      { q: 'Where does StockAI get each supplier’s lead time from?', a: 'At first, from the one you type on the supplier’s card. Every time you record a reception, StockAI stores how many days actually passed between the order and the delivery. From that supplier’s third reception it starts using the real average instead of the declared lead time, and shows you which of the two it is using.' },
      { q: 'How large can the sales file I upload be?', a: 'On the free plan, up to 25 MB per file. Moving to the full plan lifts that to 2 GB. For scale: 3 years of history with 5,000 products selling daily is around 5 million rows, on the order of 200 MB as CSV — within the full plan.' },
      { q: 'What doesn’t StockAI do?', a: 'We would rather tell you before you upload your first file. We do not publish an accuracy percentage on this page: each product’s error is measured on your own history and shown on the forecast screen. The forecast range is not a guarantee: it is where sales will probably fall, and it tells you how far to trust the suggested quantity. It does not guess what never happened: a new promotion or a big new customer is tested in the scenario simulator, not in the history. And it does not buy for you: it suggests what, how much and from whom, and you review, adjust and send the order yourself.' },
    ],
  },
  misc: {
    signalHead: ['Signal', 'When it appears', 'In the example'],
    roleToday: 'Today',
    roleWith: 'With StockAI',
    exampleTitle: 'An example',
    exampleBody: 'You sell 20 units a day of a product, your supplier takes 15 days to deliver and your safety buffer is worth 5 days of sales. Your reorder point lands at 20 days: 400 units. With that, the four signal states become concrete quantities — the ones in the table below. Change any of the numbers and the cut-offs move on their own, product by product.',
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
  pages: {
    home: 'Home',
    breadcrumb: 'You are here',
    onThisPage: 'On this page',
    related: 'Keep reading',
    pricing: {
      label: 'Pricing',
      title: 'StockAI pricing: free to start, premium as you grow.',
      intro: 'The free plan is forever and has every feature; all it has are size ceilings. Here are those ceilings, what changes when they are lifted and how that happens: by talking to us, with no card and no checkout.',
    },
    how: {
      label: 'How it works',
      title: 'How StockAI works: the steps, the rule and every screen.',
      intro: 'First, the whole journey, step by step, and how the AI models compete for every product. Then the exact calculation that turns a product red, so you can check it by hand. And finally, the application screen by screen, with real captures and data inside.',
    },
    faq: {
      label: 'FAQ',
      title: 'Frequently asked questions about StockAI.',
      intro: 'What purchasing teams usually ask before uploading their first file: what data is needed, how the free plan is lifted, where your data lives and how each supplier’s lead time is learned.',
    },
    security: {
      label: 'Security',
      title: 'Security and privacy of your data in StockAI.',
      intro: 'Before you upload your sales it is worth knowing who can see them, what is stored encrypted and what happens to all of it if you ever leave. This is what the application does today.',
      trialTitle: 'The trial account erases itself',
      trialDesc: 'If you try it without signing up, the temporary account comes with sample data, lasts 24 hours and is then erased.',
      ruleDesc: 'The rule that puts a product in the red is published under “How it works”, and you can do it by hand to check it gives the same answer.',
    },
  },
}

export const LANDING: Record<Lang, LandingCopy> = { es, en }

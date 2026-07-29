'use client'
import Link from 'next/link'
import { useEffect, useState } from 'react'

const T = {
 bg: '#ffffff',
 bg2: '#f8fafc',
 surface: '#f1f5f9',
 border: '#e2e8f0',
 text: '#0f172a',
 body: '#334155',
 muted: '#64748b',
 dim: '#94a3b8',
 accent: '#1d4ed8',
 accentBg: '#eff6ff',
 accentBd: '#bfdbfe',
 green: '#059669',
 greenBg: '#f0fdf4',
 greenBd: '#a7f3d0',
 red: '#dc2626',
 amber: '#d97706',
 amberBg: '#fffbeb',
 amberBd: '#fde68a',
}

// ── Nav ───────────────────────────────────────────────────────────────────────
function Nav() {
 return (
 <nav className="nav-shell" style={{
 position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
 background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(10px)',
 borderBottom: `1px solid ${T.border}`,
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 padding: '0 48px', height: 60,
 }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
 <div style={{ width: 30, height: 30, borderRadius: 7, background: T.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 900, color: '#fff' }}>F</div>
 <span style={{ fontSize: 16, fontWeight: 800, color: T.text, letterSpacing: '-0.03em' }}>Faro</span>
 </div>
 <div className="nav-links" style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
 {[
 ['#problema', 'El problema'],
 ['#solucion', 'Cómo funciona'],
 ['#casos', 'Industrias'],
 ['#prices', 'Precios'],
 ['#que-plan', 'Qué plan'],
 ['mailto:hola@usefaro.io', 'Contacto'],
 ].map(([href, label]) => (
 <a key={href} href={href} style={{ fontSize: 13, color: T.muted, textDecoration: 'none', fontWeight: 500, transition: 'color 0.15s' }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
 <Link href="/login" style={{ fontSize: 13, fontWeight: 600, color: T.muted, textDecoration: 'none' }}>
 Iniciar sesión
 </Link>
 <Link href="/signup" style={{ fontSize: 13, fontWeight: 600, color: '#fff', textDecoration: 'none', padding: '8px 18px', borderRadius: 7, background: T.text }}>
 Crear cuenta
 </Link>
 </div>
 </nav>
 )
}

// ── Shared layout helpers ─────────────────────────────────────────────────────
function Section({ id, children, alt, style }: { id?: string; children: React.ReactNode; alt?: boolean; style?: React.CSSProperties }) {
 return (
 <section id={id} style={{ background: alt ? T.bg2 : T.bg, padding: '88px 0', ...style }}>
 <div className="sec-inner" data-reveal style={{ maxWidth: 1100, margin: '0 auto', padding: '0 48px' }}>{children}</div>
 </section>
 )
}

function Tag({ children }: { children: React.ReactNode }) {
 return (
 <div style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 20, marginBottom: 18, background: T.accentBg, border: `1px solid ${T.accentBd}`, fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 {children}
 </div>
 )
}

function H2({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h2 style={{ fontSize: 34, fontWeight: 800, color: T.text, margin: '0 0 14px', letterSpacing: '-0.035em', lineHeight: 1.2, ...style }}>{children}</h2>
}

function Lead({ children, maxWidth = 600 }: { children: React.ReactNode; maxWidth?: number }) {
 return <p style={{ fontSize: 16, color: T.body, lineHeight: 1.7, margin: '0 0 44px', maxWidth }}>{children}</p>
}

function Check() {
 return (
 <svg width={14} height={14} viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
 <circle cx={7} cy={7} r={7} fill={T.greenBg} />
 <path d="M3.5 7 L6 9.5 L10.5 5" stroke={T.green} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 )
}

// Counterpart to Check() for "you do not need this" lists — same size, neutral.
function Dash() {
 return (
 <svg width={14} height={14} viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
 <circle cx={7} cy={7} r={7} fill={T.surface} />
 <path d="M4 7 L10 7" stroke={T.muted} strokeWidth={1.5} fill="none" strokeLinecap="round" />
 </svg>
 )
}

// Sub-heading inside the long-form sections — one step below H2, same type scale.
function H3({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
 return <h3 style={{ fontSize: 19, fontWeight: 800, color: T.text, margin: '0 0 16px', letterSpacing: '-0.02em', lineHeight: 1.3, ...style }}>{children}</h3>
}

// Wide tables scroll inside their own box so the page body never scrolls sideways.
function Scroller({ minWidth, children }: { minWidth: number; children: React.ReactNode }) {
 return (
 <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
 <div style={{ minWidth }}>{children}</div>
 </div>
 )
}

// ── Scroll reveal ─────────────────────────────────────────────────────────────
// Fades + lifts each [data-reveal] block the first time it enters the viewport,
// then stops watching it. Three deliberate properties:
//  · Content is visible by default in CSS. The hidden state is only ever applied
//    by this effect, so with JS off — or if IntersectionObserver is missing —
//    the page reads normally instead of being blank.
//  · Anything already on screen at mount (the hero) is skipped, so nothing
//    above the fold fades in on load.
//  · Only opacity and transform animate; neither triggers layout.
function useScrollReveal() {
 useEffect(() => {
 if (!('IntersectionObserver' in window)) return
 if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

 const armed: HTMLElement[] = []
 const seenPerParent = new Map<Element, number>()

 document.querySelectorAll<HTMLElement>('[data-reveal]').forEach(el => {
 if (el.getBoundingClientRect().top < window.innerHeight) return
 // Small stagger between siblings, capped so the last card in a row is not late.
 const parent = el.parentElement
 const index = parent ? seenPerParent.get(parent) ?? 0 : 0
 if (parent) seenPerParent.set(parent, index + 1)
 el.style.transitionDelay = `${Math.min(index, 3) * 70}ms`
 el.classList.add('reveal-armed')
 armed.push(el)
 })
 if (armed.length === 0) return

 const show = (el: HTMLElement) => el.classList.add('reveal-in')
 let observerWorks = false
 const observer = new IntersectionObserver((entries, obs) => {
 observerWorks = true
 entries.forEach(entry => {
 if (!entry.isIntersecting) return
 show(entry.target as HTMLElement)
 obs.unobserve(entry.target) // reveal once; never re-animate on the way back up
 })
 }, {
 threshold: 0.04,
 // Bottom is pulled in slightly so a block reveals just after it enters rather
 // than the instant its first pixel shows.
 //
 // The top is expanded by the whole document, which makes "already scrolled
 // past" count as intersecting. This is not padding for looks: a fast scroll —
 // a trackpad fling, PageDown, dragging the scrollbar — can carry a short card
 // from below the viewport to above it between two frames, and an observer
 // watching only the viewport never sees it, so it stays invisible forever.
 // Two cards in the Profesional grid did exactly that, reproducibly, and a
 // single fling past the whole page left twenty behind. The document's own
 // height is the largest jump that can exist, so nothing can outrun it.
 rootMargin: `${Math.ceil(document.documentElement.scrollHeight)}px 0px -6% 0px`,
 })

 armed.forEach(el => observer.observe(el))
 // Failsafe for an observer that never fires at all — not a deadline the reader
 // has to beat. It used to reveal everything unconditionally after 3s, which on
 // a page this tall meant the whole thing had already appeared while the reader
 // was still near the top, and nothing was left to animate. An observer that has
 // delivered even one entry is working, so the timer stands down.
 const failsafe = window.setTimeout(() => {
 if (!observerWorks) armed.forEach(show)
 }, 3000)

 return () => {
 observer.disconnect()
 window.clearTimeout(failsafe)
 }
 }, [])
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LandingPage() {
 const [activeCase, setActiveCase] = useState(0)
 const [openFaq, setOpenFaq] = useState<number | null>(null)
 useScrollReveal()

 const PROBLEMS = [
 { title: 'Ruptura de stock en temporadas clave', desc: 'En retail y distribución, un quiebre durante temporada alta no es solo una venta perdida — el cliente va a la competencia y no regresa. La demanda no espera al próximo ciclo de reposición.' },
 { title: 'Capital atrapado en sobreinventario', desc: 'Para mayoristas y manufactureros, el exceso de inventario ocupa bodega, consume línea de crédito y en categorías perecederas o de moda, termina en pérdida directa por liquidación.' },
 { title: 'Compras reactivas en lugar de planificadas', desc: 'Comprar cuando el inventario ya está crítico obliga a aceptar condiciones desfavorables: precios spot, fletes de emergencia y tiempos de entrega fuera del ciclo normal.' },
 { title: 'Conocimiento concentrado en una sola persona', desc: 'El comprador más experimentado lleva en la cabeza la estacionalidad, los ciclos del proveedor y las anomalías históricas de cada producto. Ese conocimiento no está en ningún sistema.' },
 { title: 'Forecasts manuales que no escalan', desc: 'Un analista puede mantener 30 SKUs en Excel con rigor razonable. Con 300 productos los modelos se simplifican. Con 3,000 SKUs, la mayoría se administra por intuición.' },
 ]

 const STEPS = [
 { n: '01', title: 'Carga tu historial de ventas', desc: 'Sube un archivo CSV o Excel con tus ventas. El sistema identifica automáticamente las columnas de fecha, producto y cantidad vendida.' },
 { n: '02', title: 'Análisis automático por producto', desc: 'Faro detecta la tendencia, estacionalidad y variabilidad de cada SKU de forma independiente. Sin configuración manual por producto.' },
 { n: '03', title: 'Pronóstico con intervalos de confianza', desc: 'Genera proyecciones de demanda para cada producto con rangos alto y bajo. Identifica qué SKUs tienen demanda predecible y cuáles son volátiles.' },
 { n: '04', title: 'Recomendaciones de compra', desc: 'El sistema calcula cuánto pedir, cuándo pedir y qué productos están en riesgo de quiebre según el plazo de entrega de cada proveedor.' },
 ]

 const CASES = [
 {
 label: 'Retail',
 title: 'Gestión de inventario por tienda y categoría',
 desc: 'Un retailer con múltiples puntos de venta enfrenta patrones de demanda distintos por ubicación, categorías con estacionalidades diferentes y un ciclo de reposición que no puede fallar. Faro genera pronósticos individuales por tienda y por SKU, detecta cambios en la tendencia de venta y permite planificar con anticipación las temporadas de alta demanda.',
 metrics: [
 { metric: 'Reducción de quiebres de stock', value: '20–35%' },
 { metric: 'Reducción de sobreinventario', value: '15–25%' },
 { metric: 'Tiempo en planificación de compras', value: '−70%' },
 ],
 },
 {
 label: 'Distribuidores',
 title: 'Reposición optimizada y menos emergencias',
 desc: 'Los distribuidores trabajan con márgenes ajustados, proveedores con plazos variables y clientes que no toleran faltantes. El error de inventario se paga caro: un cliente insatisfecho migra. Faro calcula el punto de reorden correcto para cada producto según su velocidad de venta real y el lead time del proveedor, reduciendo las compras de emergencia.',
 metrics: [
 { metric: 'Reducción de compras de emergencia', value: '30–50%' },
 { metric: 'Nivel de servicio (fill rate)', value: '+8–15 pp' },
 { metric: 'Tiempo de ciclo de compra', value: '−60%' },
 ],
 },
 {
 label: 'Mayoristas',
 title: 'Balance de inventario entre bodegas',
 desc: 'Los mayoristas compran en volumen para obtener mejores precios, pero esa ventaja desaparece cuando el inventario no rota o está mal distribuido. Faro identifica qué productos tienen exceso antes de que llegue la fecha de vencimiento o se vuelvan obsoletos, y señala qué referencias priorizar en la siguiente orden.',
 metrics: [
 { metric: 'Reducción de merma por vencimiento', value: '25–40%' },
 { metric: 'Rotación de inventario', value: '+10–20%' },
 { metric: 'Reducción de sobreinventario', value: '15–30%' },
 ],
 },
 {
 label: 'Manufactura',
 title: 'Planificación de producción y materias primas',
 desc: 'Una línea de producción parada por falta de material tiene un costo que va mucho más allá del material: horas hombre perdidas, penalizaciones por entrega tardía y clientes que pierden confianza. Faro convierte el pronóstico de demanda del producto terminado en un plan de requerimientos de materias primas, considerando tiempos de producción y plazos de proveedores.',
 metrics: [
 { metric: 'Reducción de paros por falta de material', value: '40–60%' },
 { metric: 'Eficiencia de planificación de compras', value: '+25%' },
 { metric: 'Reducción de inventario de seguridad', value: '15–20%' },
 ],
 },
 {
 label: 'E-commerce',
 title: 'Preparación para picos de demanda',
 desc: 'En e-commerce, llegar sin inventario a un Black Friday o campaña de descuentos es dejar dinero sobre la mesa. Llegar con demasiado significa capital atrapado y liquidación a pérdida. Faro analiza el comportamiento histórico durante eventos promocionales y genera estimaciones para los próximos picos con tiempo suficiente para hacer pedidos.',
 metrics: [
 { metric: 'Preparación para picos estacionales', value: '+30%' },
 { metric: 'Reducción de liquidaciones post-temporada', value: '20–35%' },
 { metric: 'Tasa de faltantes en eventos clave', value: '−45%' },
 ],
 },
 ]

 const BENEFITS = [
 'Pronóstico por SKU y por familia de productos',
 'Proyecciones semanales, mensuales y por temporada',
 'Alertas de productos en riesgo de quiebre',
 'Métricas de precisión y backtesting por modelo',
 'Cantidad recomendada por orden de compra',
 'Clasificación automática ABC-XYZ (desde Profesional)',
 'Detección de cambios y anomalías de demanda',
 'Exportación a Excel y PDF',
 'Escala desde 50 hasta 50,000 SKUs',
 'Actualización automática con nuevas ventas',
 ]

 const COMPARE = [
 { feature: 'Tiempo para generar pronóstico', excel: 'Días', faro: 'Minutos' },
 { feature: 'Cantidad de SKUs manejables', excel: 'Decenas', faro: 'Miles' },
 { feature: 'Actualización del modelo', excel: 'Manual', faro: 'Automática' },
 { feature: 'Detección de estacionalidad', excel: 'Manual', faro: 'Automática' },
 { feature: 'Alertas de riesgo de quiebre', excel: 'No disponible', faro: 'Incluido' },
 { feature: 'Precisión optimizada por SKU', excel: 'Depende del analista', faro: 'Sí' },
 { feature: 'Trazabilidad y auditoría', excel: 'Difícil', faro: 'Incluido' },
 { feature: 'Escala sin costo de mantenimiento', excel: 'No', faro: 'Sí' },
 ]

 // Limits below mirror backend/entitlements/plans.py — keep the two in sync.
 //
 // PRE-LAUNCH CHECKLIST — verify these advertised capabilities work before deploying this page:
 // · API keys — can be created today but authenticate nothing; no endpoint accepts one.
 // · Integraciones (Alegra, Siigo) — page exists but is hidden from the nav pending a pricing decision.
 // · Webhooks — real, but a single POST with no retry, for two events (job.completed / job.failed).
 // · Búsqueda en documentos — backend and API client exist, but no upload UI is wired to them.
 // · Recetas de materiales (BOM) — backend only; no screen and no nav entry.
 const PLANS = [
 {
 name: 'Starter',
 price: 99 as number | null,
 priceLabel: null as string | null,
 priceHint: null as string | null,
 desc: 'Para una operación de una bodega que hoy decide sus compras en Excel.',
 skus: 'Hasta 1.000 SKUs · 2 usuarios · 1 bodega',
 features: ['Semáforo de inventario por producto', 'Órdenes de compra con cantidad sugerida', 'Recepciones que aprenden el plazo real del proveedor', 'Ficha de proveedores y reportes', 'Exportación a Excel y PDF', 'Alertas por correo, todos los días', 'Archivos de hasta 200 MB · 20 entrenamientos guardados'],
 cta: 'Empezar gratis',
 ctaHref: '/signup?demo=1',
 highlight: false,
 },
 {
 name: 'Profesional',
 price: 649 as number | null,
 priceLabel: null as string | null,
 priceHint: null as string | null,
 desc: 'Para quien ya tiene varias bodegas y más catálogo del que una persona puede vigilar.',
 skus: 'Hasta 5.000 SKUs · 10 usuarios · 5 ubicaciones',
 features: ['Todo lo del plan Starter', 'Clasificación ABC-XYZ automática', 'Multibodega con sugerencias de transferencia', 'Optimizador de compra por costo', 'Simulador de escenarios', 'Alertas por WhatsApp', 'Analista con IA y búsqueda en tus documentos', 'Recálculo programado', 'Mensajes de equipo — nuevo', 'Archivos de hasta 500 MB · 100 entrenamientos guardados', 'Soporte prioritario'],
 cta: 'Empezar gratis',
 ctaHref: '/signup?demo=1',
 highlight: true,
 },
 {
 name: 'Empresarial',
 price: null as number | null,
 priceLabel: 'Personalizado',
 priceHint: 'Desde $1.990/mes',
 desc: 'Para operaciones sin techo de catálogo, o que producen en lugar de solo revender.',
 skus: 'SKUs, usuarios y ubicaciones ilimitados',
 features: ['Todo lo del plan Profesional', 'Recetas de materiales (BOM) para quien ensambla o produce', 'Acceso API y webhooks para conectar tus sistemas', 'Integraciones con Alegra y Siigo', 'Integración a medida (ERP, WMS, BI)', 'Archivos de hasta 2 GB', 'Onboarding con equipo técnico dedicado', 'SLA de disponibilidad garantizado', 'Gerente de cuenta asignado'],
 cta: 'Hablar con el equipo',
 ctaHref: 'mailto:hola@usefaro.io?subject=Faro%20%E2%80%94%20activar%20plan%20Empresarial',
 highlight: false,
 },
 ]

 // ── Content for the long-form sections ───────────────────────────────────────
 // Every number here is checkable against code: limits from entitlements/plans.py,
 // signal thresholds and the 3-reception rule from backend/inventory/service.py.

 const PLAN_FIT = [
 {
 plan: 'Starter',
 headline: 'Una bodega y un catálogo que todavía cabe en la cabeza de una persona',
 who: 'Una ferretería, un minisúper, una farmacia o una distribuidora chica: un local o una bodega, entre 300 y 1.000 códigos activos, y dos personas que tocan el sistema — casi siempre quien compra y quien recibe la mercadería.',
 why: 'Con una sola bodega no tienes que decidir dónde poner cada producto, y con menos de 1.000 códigos todavía reconoces la mayoría por nombre. Lo que no puedes hacer es revisarlos todos cada semana. Para eso está Starter: el semáforo marca cuáles se van a quedar sin existencias antes de que alcance a llegar el próximo pedido, y te lo manda por correo todos los días sin que tengas que entrar a buscarlo.',
 outgrow: 'Abres una segunda bodega, pasas de 1.000 códigos activos, o necesitas que entre una tercera persona al sistema.',
 note: null as string | null,
 },
 {
 plan: 'Profesional',
 headline: 'Varias ubicaciones y más catálogo del que alguien puede vigilar a mano',
 who: 'Una distribuidora de consumo masivo, una cadena de 3 a 5 tiendas, un mayorista de abarrotes o de repuestos: entre 1.000 y 5.000 códigos, hasta 5 bodegas, y un equipo de compras, bodega y administración que hoy se coordina por WhatsApp y correo.',
 why: 'Pasando los 1.500 códigos ya no puedes mirarlos uno por uno, así que necesitas que el sistema te diga en cuáles vale la pena gastar atención: eso hace la clasificación ABC-XYZ. Y con más de una bodega la pregunta cambia de fondo — deja de ser “¿cuánto compro?” y pasa a ser “¿compro, o muevo lo que ya tengo en la otra bodega?”. Esa segunda pregunta es la que te ahorra plata, y es la que responde Profesional.',
 outgrow: 'Pasas de 5.000 códigos o de 10 usuarios, necesitas más de 5 bodegas, o ensamblas y produces en vez de solo revender.',
 note: null as string | null,
 },
 {
 plan: 'Empresarial',
 headline: 'Sin techo de catálogo, o produciendo en lugar de solo revender',
 who: 'Una cadena con decenas de puntos de venta, un distribuidor nacional con catálogo de cinco cifras, o una planta que arma producto terminado a partir de materias primas.',
 why: 'Aquí desaparecen los topes: códigos, usuarios y ubicaciones ilimitados, y archivos de hasta 2 GB para historiales largos. Se suman las recetas de materiales (BOM), que convierten el pronóstico del producto terminado en el requerimiento de cada insumo, y la conexión con los sistemas que ya usas por API, webhooks e integraciones.',
 outgrow: null as string | null,
 note: 'No hay un escalón siguiente: el precio se arma sobre tu operación. Escríbenos y lo vemos con números tuyos.',
 },
 ]

 const PLAN_LIMITS = [
 { label: 'Códigos de producto (SKUs)', starter: '1.000', pro: '5.000', ent: 'Ilimitado' },
 { label: 'Usuarios', starter: '2', pro: '10', ent: 'Ilimitado' },
 { label: 'Bodegas o ubicaciones', starter: '1', pro: '5', ent: 'Ilimitado' },
 { label: 'Entrenamientos guardados', starter: '20', pro: '100', ent: 'Ilimitado' },
 { label: 'Tamaño máximo por archivo', starter: '200 MB', pro: '500 MB', ent: '2 GB' },
 ]

 const PRO_ROLES = [
 {
 role: 'Dueño o gerente general',
 pain: 'Te enteras del quiebre cuando te llama el vendedor, y del sobrestock cuando ves cuánta plata hay parada en bodega.',
 gain: 'Un resumen diario de los productos en rojo, al correo y al WhatsApp. Y un simulador para probar “¿qué pasa si la promoción duplica la venta de esta categoría?” antes de comprometer el dinero.',
 },
 {
 role: 'Encargado de compras',
 pain: 'Revisas miles de códigos en una hoja de cálculo y terminas comprando por costumbre: lo mismo del mes pasado, más un poco.',
 gain: 'La lista llega ordenada por urgencia, con la cantidad sugerida por proveedor. El optimizador arma el pedido tomando en cuenta lo que cuesta tener inventario parado, lo que cuesta quedarse sin producto y el flete fijo del camión.',
 },
 {
 role: 'Jefe de bodega',
 pain: 'Anotas las recepciones en un cuaderno, y nadie en la empresa sabe cuánto tarda de verdad cada proveedor.',
 gain: 'Cada recepción que registras se vuelve dato. A partir de la tercera entrega de un proveedor, Faro deja de usar el plazo que te prometieron y empieza a usar el que cumplen.',
 },
 {
 role: 'Administración y finanzas',
 pain: 'Sabes cuánto vale el inventario, pero no cuánto de eso es capital atrapado en productos que no rotan.',
 gain: 'La clasificación ABC-XYZ separa lo que mueve tu venta de lo que solo ocupa espacio, y los reportes que exportas a Excel y PDF salen con esa marca en cada producto.',
 },
 ]

 const PRO_INCLUDES = [
 { title: 'Clasificación ABC-XYZ', desc: 'A son los productos que concentran el 80 % de tu venta; C es la cola larga. X es demanda estable, Z es errática. Un producto AZ vende mucho y de forma impredecible: ahí conviene el colchón de seguridad, en vez de repartirlo parejo en todo el catálogo.', isNew: false },
 { title: 'Multibodega y transferencias', desc: 'Hasta 5 ubicaciones, con rutas entre ellas: días de tránsito y costo. Cuando un producto está corto en una bodega y sobrado en otra, Faro propone mover en vez de comprar — y solo lo propone si a la bodega que presta le quedan al menos 30 días de cobertura.', isNew: false },
 { title: 'Optimizador de compra por costo', desc: 'Arma el pedido buscando el menor costo total, no la menor cantidad de unidades: suma el costo de mantener inventario, la penalización por quedarse sin producto, el costo de compra, el costo por unidad transferida y el costo fijo del envío, que se paga una sola vez aunque el camión lleve veinte productos.', isNew: false },
 { title: 'Simulador de escenarios', desc: 'Hasta 50 reglas por escenario: multiplicar la demanda, marcar una promoción, atrasar a un proveedor o cambiar el stock de seguridad, filtrando por producto, categoría, proveedor o rango de fechas. Compara el escenario contra la base sin tocar nada de lo real, y lo puedes guardar para volver a correrlo.', isNew: false },
 { title: 'Alertas por WhatsApp', desc: 'El mismo resumen diario de productos en riesgo que llega por correo, ahora al teléfono de quien decide. Cada persona vincula y verifica su propio número desde su configuración.', isNew: false },
 { title: 'Analista con IA', desc: 'Preguntas en español sobre tus propios datos — “¿por qué subió la demanda de esta categoría?”, “¿qué proveedores me están atrasando?” — y cada respuesta viene marcada con de dónde salió, para que sepas cuándo se apoya en tus datos y cuándo no.', isNew: false },
 { title: 'Búsqueda en tus documentos', desc: 'Subes catálogos, listas de precios o contratos de proveedor y el analista los consulta al responder. Tus documentos quedan aislados a tu empresa.', isNew: false },
 { title: 'Recálculo programado', desc: 'En vez de acordarte de reentrenar, lo dejas corriendo solo: cada lunes a las 6, todos los días, solo días hábiles, cada hora o el primero de cada mes. La pantalla te muestra cuándo corrió, cuándo vuelve a correr y si falló.', isNew: false },
 { title: 'Mensajes de equipo', desc: 'Conversaciones uno a uno entre las personas de tu empresa, dentro de Faro, al lado del inventario del que están hablando. Si la otra persona no está conectada, le llega un aviso a su WhatsApp para que no se pierda el mensaje.', isNew: true },
 ]

 const SIGNALS = [
 { signal: 'PEDIR YA', rule: 'La cobertura no llega ni a la mitad del plazo del proveedor', example: 'Menos de 7,5 días · menos de 150 unidades', color: T.red },
 { signal: 'PEDIR PRONTO', rule: 'La cobertura es menor a 1,2 veces el plazo', example: 'Entre 7,5 y 18 días · 150 a 360 unidades', color: T.amber },
 { signal: 'OK', rule: 'La cobertura va de 1,2 a 3 veces el plazo', example: 'Entre 18 y 45 días · 360 a 900 unidades', color: T.green },
 { signal: 'SOBRESTOCK', rule: 'La cobertura es de 3 veces el plazo o más', example: '45 días o más · más de 900 unidades', color: T.muted },
 ]

 const NEED = [
 'Un archivo CSV o Excel con tu historial de ventas.',
 'Tres columnas como mínimo: fecha, código de producto y cantidad vendida.',
 'Idealmente 12 meses o más, para que se alcance a ver la estacionalidad completa.',
 'Las existencias actuales, para que el semáforo tenga contra qué comparar.',
 'El plazo de entrega aproximado de cada proveedor. Aproximado basta: se corrige solo.',
 ]

 const NOT_NEED = [
 'No necesitas conectar tu ERP para empezar: exportas de tu sistema y subes el archivo.',
 'No necesitas configurar un modelo por producto ni saber qué es una serie de tiempo.',
 'No necesitas una persona de tecnología dedicada.',
 'No necesitas el catálogo limpio ni completo: los productos con poco historial se marcan como de alta incertidumbre en vez de quedar fuera.',
 'No necesitas un mínimo de productos. Funciona igual con 80 códigos que con 4.000.',
 ]

 const FAQS = [
 {
 q: '¿Necesito conocimientos estadísticos o de programación para usar Faro?',
 a: 'No. Faro está diseñado para que cualquier persona del equipo de compras o planificación pueda usarlo. No hay configuración de modelos ni código. Solo cargas tus datos y el sistema genera los pronósticos automáticamente.',
 },
 {
 q: '¿En qué formato debo tener mis datos de ventas?',
 a: 'Faro acepta archivos Excel (.xlsx) y CSV. El archivo debe tener al menos una columna de fecha, una columna de identificador del producto (SKU o nombre) y una columna de cantidad vendida. El sistema detecta automáticamente qué columna es cuál.',
 },
 {
 q: '¿Qué pasa si tengo productos con muy pocas ventas históricas o datos incompletos?',
 a: 'Faro identifica automáticamente los SKUs con historial insuficiente y ajusta el nivel de confianza del pronóstico. Los productos con menos de 6 meses de datos se clasifican como "alta incertidumbre" y se recomiendan márgenes de seguridad mayores.',
 },
 {
 q: '¿Mis datos están seguros? ¿Quién tiene acceso a ellos?',
 a: 'Los datos que subes a Faro son exclusivamente tuyos. No se comparten con terceros ni se usan para entrenar modelos de otras empresas. La transmisión y almacenamiento están cifrados. Puedes solicitar la eliminación completa de tus datos en cualquier momento.',
 },
 {
 q: '¿Cuánto tiempo toma implementar Faro en mi empresa?',
 a: 'En la mayoría de casos, menos de un día. Si tienes un archivo de ventas histórico, puedes subir los datos y ver tus primeros pronósticos en menos de una hora. Para integraciones con ERP o sistemas propios, el tiempo varía según la complejidad.',
 },
 {
 q: '¿Se puede integrar con nuestro ERP o sistema de inventario actual?',
 a: 'Sí, en el plan Empresarial. Ahí están el acceso a la API y los webhooks para automatizar la carga de ventas y la salida de pronósticos hacia otros sistemas, las integraciones con Alegra y Siigo, y el desarrollo a medida con nuestro equipo técnico. En Starter y Profesional la carga es por archivo: exportas de tu sistema y subes el CSV o Excel.',
 },
 {
 q: '¿Con qué frecuencia se actualizan los pronósticos?',
 a: 'Cada vez que cargas ventas nuevas. En Starter lo lanzas tú cuando subes el archivo del mes. Desde Profesional puedes además dejar el recálculo programado para que corra solo: cada lunes a las 6, todos los días, solo días hábiles, cada hora o el primero de cada mes.',
 },
 {
 q: '¿Faro sirve si tengo más de una bodega?',
 a: 'Sí, desde el plan Profesional: hasta 5 ubicaciones, con rutas entre bodegas donde defines los días de tránsito y el costo. Cuando un producto está corto en una bodega y sobrado en otra, Faro sugiere mover en lugar de comprar, y solo lo sugiere si a la bodega que presta le quedan al menos 30 días de cobertura. En Empresarial las ubicaciones son ilimitadas. En Starter trabajas con una sola bodega.',
 },
 {
 q: '¿De dónde saca Faro el plazo de entrega de cada proveedor?',
 a: 'Al principio, del que escribes tú en la ficha del proveedor. Cada vez que registras una recepción, Faro guarda cuántos días pasaron de verdad entre la orden y la entrega. A partir de la tercera recepción de ese proveedor empieza a usar el promedio real en lugar del plazo declarado, y te muestra cuál de los dos está usando. Esto viene en todos los planes.',
 },
 {
 q: '¿Qué tan grande puede ser el archivo de ventas que subo?',
 a: 'Hasta 200 MB en Starter, 500 MB en Profesional y 2 GB en Empresarial. Para dimensionarlo: 3 años de historial con 5.000 productos y venta diaria son unos 5 millones de filas, del orden de 200 MB en CSV.',
 },
 ]

 return (
 <>
 <style>{`
 * { box-sizing: border-box; }
 body { margin: 0; background: ${T.bg}; color: ${T.text}; font-family: system-ui, -apple-system, sans-serif; }
 html { scroll-behavior: smooth; }
 .btn-primary {
 display: inline-flex; align-items: center; gap: 7px;
 padding: 12px 24px; border-radius: 8px; border: none; cursor: pointer;
 font-size: 14px; font-weight: 700; color: #fff; text-decoration: none;
 background: ${T.text}; transition: background 0.15s;
 }
 .btn-primary:hover { background: #1e293b; }
 .btn-ghost {
 display: inline-flex; align-items: center; gap: 7px;
 padding: 12px 24px; border-radius: 8px; cursor: pointer;
 font-size: 14px; font-weight: 600; color: ${T.body}; text-decoration: none;
 border: 1px solid ${T.border}; background: transparent; transition: border-color 0.15s, color 0.15s;
 }
 .btn-ghost:hover { border-color: ${T.muted}; color: ${T.text}; }
 input:focus, textarea:focus, select:focus { border-color: ${T.accent} !important; box-shadow: 0 0 0 3px ${T.accentBg} !important; }

 /* Scroll reveal. The resting state is visible; useScrollReveal adds
    .reveal-armed only after it confirms it can also remove it. */
 .reveal-armed { opacity: 0; transform: translateY(12px); }
 .reveal-in {
 opacity: 1; transform: none;
 transition: opacity 380ms cubic-bezier(0.16, 1, 0.3, 1), transform 380ms cubic-bezier(0.16, 1, 0.3, 1);
 }
 @media (prefers-reduced-motion: reduce) {
 .reveal-armed, .reveal-in { opacity: 1 !important; transform: none !important; transition: none !important; }
 }

 /* Narrow viewports: collapse the fixed grid columns instead of overflowing.
    Nothing here changes colour, type or shadow — only how many columns fit. */
 @media (max-width: 900px) {
 /* These two override inline styles on the nav, hence !important. */
 .nav-links { display: none !important; }
 .nav-shell { padding: 0 20px !important; }
 .grid-2, .grid-3 { grid-template-columns: 1fr !important; }
 .split { grid-template-columns: 1fr !important; gap: 32px !important; }
 .faq-side { position: static !important; }
 }
 @media (max-width: 760px) {
 .sec-inner, .hero-inner { padding: 0 20px !important; }
 .strip-shell { padding: 32px 20px !important; }
 .strip-grid { grid-template-columns: repeat(2, 1fr) !important; row-gap: 28px; }
 .strip-cell { border-right: none !important; padding: 0 12px !important; }
 .hero-h1 { font-size: 34px !important; }
 .card-pad { padding: 24px 20px !important; }
 .footer-shell { padding: 40px 20px !important; }
 .footer-grid { grid-template-columns: 1fr 1fr !important; gap: 28px !important; }
 .footer-bottom { flex-direction: column; align-items: flex-start !important; gap: 8px; }
 }
 `}</style>

 <Nav />

 {/* ── HERO ─────────────────────────────────────────────────────────── */}
 <section style={{ minHeight: '100vh', paddingTop: 120, background: T.bg, display: 'flex', flexDirection: 'column', alignItems: 'center', borderBottom: `1px solid ${T.border}` }}>
 <div className="hero-inner" style={{ maxWidth: 1100, width: '100%', margin: '0 auto', padding: '0 48px' }}>

 <div style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 20, marginBottom: 24, background: T.greenBg, border: `1px solid ${T.greenBd}`, fontSize: 11, fontWeight: 700, color: T.green, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 Para distribuidores, retail y manufactura
 </div>

 <h1 className="hero-h1" style={{ fontSize: 56, fontWeight: 900, color: T.text, margin: '0 0 18px', letterSpacing: '-0.05em', lineHeight: 1.1, maxWidth: 720 }}>
 Deja de gestionar el inventario<br />a base de intuición.
 </h1>

 <p style={{ fontSize: 18, color: T.body, lineHeight: 1.65, maxWidth: 560, margin: '0 0 36px' }}>
 Faro analiza tus ventas históricas y genera pronósticos de demanda por producto — para que sepas cuánto comprar, cuándo comprar y qué productos están en riesgo de quiebre.
 </p>

 <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 60 }}>
 <Link href="/signup?demo=1" className="btn-primary">Empezar gratis con datos de ejemplo</Link>
 </div>

 {/* Framed real product screenshot */}
 <div id="demo" style={{ borderRadius: 14, border: `1px solid ${T.border}`, background: T.bg2, boxShadow: '0 24px 60px rgba(15,23,42,0.14)', overflow: 'hidden' }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '11px 16px', borderBottom: `1px solid ${T.border}`, background: T.bg }}>
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#f87171' }} />
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#fbbf24' }} />
 <span style={{ width: 11, height: 11, borderRadius: '50%', background: '#34d399' }} />
 <span style={{ marginLeft: 12, fontSize: 11.5, color: T.dim, fontWeight: 500 }}>Faro · Panel de compras</span>
 </div>
 <img src="/shot-panel.png" alt="Panel de compras de Faro con datos reales: KPIs de SKUs, riesgo, precisión y valor de inventario, resumen ejecutivo y productos urgentes." style={{ display: 'block', width: '100%', height: 'auto' }} />
 </div>

 <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 36, paddingBottom: 72, flexWrap: 'wrap' }}>
 <div style={{ fontSize: 12, color: T.dim }}>Industrias:</div>
 {['Distribución', 'Retail', 'Manufactura', 'Mayoristas', 'E-commerce'].map(s => (
 <span key={s} style={{ fontSize: 12, fontWeight: 500, color: T.muted, padding: '4px 12px', borderRadius: 20, border: `1px solid ${T.border}` }}>{s}</span>
 ))}
 </div>
 </div>
 </section>

 {/* ── STATS STRIP ──────────────────────────────────────────────────── */}
 <div className="strip-shell" style={{ background: T.text, padding: '40px 48px' }}>
 <div className="strip-grid" data-reveal style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0 }}>
 {[
 { value: '94%', label: 'Precisión promedio de pronóstico' },
 { value: '−75%', label: 'Tiempo invertido en planificación' },
 { value: '50K+', label: 'SKUs soportados por instancia' },
 { value: '1 día', label: 'Tiempo promedio de implementación' },
 ].map(({ value, label }, i) => (
 <div key={label} className="strip-cell" style={{ textAlign: 'center', padding: '0 32px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.1)' : 'none' }}>
 <div style={{ fontSize: 36, fontWeight: 900, color: '#fff', letterSpacing: '-0.04em', marginBottom: 6 }}>{value}</div>
 <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', lineHeight: 1.4 }}>{label}</div>
 </div>
 ))}
 </div>
 </div>

 {/* ── EL PROBLEMA ──────────────────────────────────────────────────── */}
 <Section id="problema" alt>
 <Tag>El problema</Tag>
 <H2>El inventario mal planificado tiene un costo concreto.</H2>
 <Lead>
 La mayoría de empresas toma decisiones de compra con Excel, intuición acumulada y el criterio del comprador de turno.
 Eso funciona hasta cierto punto — y después, los errores se vuelven sistemáticos.
 </Lead>
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))', gap: 16 }}>
 {PROBLEMS.map(({ title, desc }) => (
 <div key={title} data-reveal className="card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ width: 32, height: 3, background: T.accent, borderRadius: 2, marginBottom: 16 }} />
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8, lineHeight: 1.4 }}>{title}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{desc}</div>
 </div>
 ))}
 </div>
 </Section>

 {/* ── CÓMO FUNCIONA ────────────────────────────────────────────────── */}
 <Section id="solucion">
 <Tag>Cómo funciona</Tag>
 <H2>De tus datos históricos a decisiones de compra.</H2>
 <Lead>Faro transforma el historial de ventas en pronósticos precisos por producto. Sin configuración estadística, sin necesitar un analista dedicado.</Lead>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 20 }}>
 {STEPS.map(({ n, title, desc }) => (
 <div key={n} data-reveal className="card-pad" style={{ display: 'flex', gap: 18, alignItems: 'flex-start', background: T.bg2, borderRadius: 10, padding: '22px 24px', border: `1px solid ${T.border}` }}>
 <div style={{ width: 36, height: 36, borderRadius: 8, flexShrink: 0, background: T.accentBg, border: `1px solid ${T.accentBd}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 900, color: T.accent, fontFamily: 'monospace' }}>{n}</div>
 <div>
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 6 }}>{title}</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{desc}</div>
 </div>
 </div>
 ))}
 </div>

 {/* Plain app samples — simple framed screenshots, not a headlined feature */}
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20, marginTop: 32 }}>
 {[
 { img: '/shot-inventory.png', caption: 'Semáforo de inventario', alt: 'Tabla de inventario de Faro con semáforo de colores: estados PEDIR YA, OK y SOBRESTOCK por SKU y bodega.' },
 { img: '/shot-forecast.png', caption: 'Pronóstico por SKU', alt: 'Gráfico de pronóstico por SKU de Faro: ventas históricas, pronóstico P50 y bandas de incertidumbre.' },
 ].map(({ img, caption, alt }) => (
 <figure key={img} data-reveal style={{ margin: 0 }}>
 <div style={{ borderRadius: 12, border: `1px solid ${T.border}`, overflow: 'hidden', boxShadow: '0 12px 32px rgba(15,23,42,0.10)' }}>
 <img src={img} alt={alt} style={{ display: 'block', width: '100%', height: 'auto' }} />
 </div>
 <figcaption style={{ fontSize: 12, color: T.dim, marginTop: 8 }}>{caption}</figcaption>
 </figure>
 ))}
 </div>
 </Section>

 {/* ── CÓMO DECIDE ──────────────────────────────────────────────────── */}
 <Section id="como-decide" alt>
 <Tag>La regla, sin misterio</Tag>
 <H2>Cómo decide Faro que un producto está en rojo.</H2>
 <Lead maxWidth={680}>
 Ninguna recomendación sale de una caja negra. Todo el semáforo se apoya en una sola cuenta, y la puedes hacer a mano para comprobar que da lo mismo.
 </Lead>

 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start', marginBottom: 32 }}>
 <div>
 <H3>La cuenta</H3>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: '0 0 14px' }}>
 <strong style={{ color: T.text }}>Cobertura = existencias ÷ demanda diaria pronosticada.</strong> Eso te da cuántos días aguantas si no llega nada más. Esa cifra se compara contra el plazo de tu proveedor: los días que tarda en entregarte desde que le pasas la orden.
 </p>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: 0 }}>
 La lógica es la que ya usas de cabeza, solo que aplicada a los miles de códigos que no alcanzas a revisar: si aguantas menos de lo que tarda en llegar el pedido, ya vas tarde.
 </p>
 </div>
 <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>Un ejemplo</div>
 <p style={{ fontSize: 14, color: T.body, lineHeight: 1.75, margin: 0 }}>
 Vendes <strong style={{ color: T.text }}>20 unidades al día</strong> de un producto y tu proveedor tarda <strong style={{ color: T.text }}>15 días</strong> en entregar. Con esos dos números, los cuatro estados del semáforo quedan en cantidades concretas — las de la tabla de abajo. Cambia cualquiera de los dos y los cortes se mueven solos, producto por producto.
 </p>
 </div>
 </div>

 <Scroller minWidth={660}>
 <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.border}` }}>
 <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 250px', background: T.surface, padding: '12px 24px', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Señal</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Cuándo aparece</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>En el ejemplo</div>
 </div>
 {SIGNALS.map(({ signal, rule, example, color }, i) => (
 <div key={signal} style={{ display: 'grid', gridTemplateColumns: '160px 1fr 250px', padding: '15px 24px', alignItems: 'center', background: i % 2 === 0 ? T.bg : T.bg2, borderBottom: i < SIGNALS.length - 1 ? `1px solid ${T.border}` : 'none' }}>
 <span style={{ fontSize: 12, fontWeight: 800, color, letterSpacing: '0.02em' }}>{signal}</span>
 <span style={{ fontSize: 13, color: T.body, lineHeight: 1.5, paddingRight: 16 }}>{rule}</span>
 <span style={{ fontSize: 13, color: T.muted }}>{example}</span>
 </div>
 ))}
 </div>
 </Scroller>

 <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px', marginTop: 20 }}>
 <div style={{ width: 32, height: 3, background: T.accent, borderRadius: 2, marginBottom: 16 }} />
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8 }}>Y el plazo no es el que te prometieron, es el que cumplen</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7 }}>
 El plazo de entrega es la mitad de la cuenta, así que conviene que sea el real. Cada vez que registras una recepción, Faro guarda cuántos días pasaron de verdad entre la orden y la entrega. A la tercera recepción de ese proveedor deja de usar el plazo que escribiste y empieza a usar el promedio observado — y te dice cuál de los dos está aplicando. Si un proveedor te dice ocho días y entrega en catorce, el semáforo se ajusta sin que tengas que reclamar primero. Esto viene en todos los planes.
 </div>
 </div>
 </Section>

 {/* ── NOSOTROS ─────────────────────────────────────────────────────── */}
 <Section id="nosotros">
 {/* TODO: el dueño puede personalizar la historia/equipo real aquí */}
 <div style={{ maxWidth: 760 }}>
 <Tag>Nosotros</Tag>
 <H2>Construido para quien decide las compras, no para científicos de datos.</H2>
 <p style={{ fontSize: 16, color: T.body, lineHeight: 1.75, margin: '0 0 20px' }}>
 Faro nace para que los distribuidores, comercios y mayoristas de Latinoamérica dejen de comprar inventario a ciegas. La mayoría opera con Excel e intuición porque las herramientas de forecasting fueron hechas para grandes empresas con equipos de datos — no para una operación que maneja miles de SKUs con un equipo pequeño.
 </p>
 <p style={{ fontSize: 16, color: T.body, lineHeight: 1.75, margin: 0 }}>
 Faro toma el historial de ventas que ya tienes (un CSV o Excel), lo convierte en pronósticos por producto y en decisiones concretas de compra, sin que necesites un analista dedicado. Hecho en Costa Rica, pensado para la realidad de las PyMEs de la región.
 </p>
 </div>
 </Section>

 {/* ── INDUSTRIAS ───────────────────────────────────────────────────── */}
 <Section id="casos" alt>
 <Tag>Industrias</Tag>
 <H2>Diseñado para operaciones reales.</H2>
 <Lead>El problema de inventario no es el mismo en un mayorista que en un retailer o en una planta de producción. Faro se adapta a las características de cada operación.</Lead>
 <div style={{ display: 'flex', gap: 8, marginBottom: 28, flexWrap: 'wrap' }}>
 {CASES.map(({ label }, i) => (
 <button key={label} onClick={() => setActiveCase(i)} style={{ all: 'unset', cursor: 'pointer', padding: '7px 16px', borderRadius: 7, fontSize: 13, fontWeight: 600, background: activeCase === i ? T.accentBg : T.bg, border: `1px solid ${activeCase === i ? T.accentBd : T.border}`, color: activeCase === i ? T.accent : T.muted, transition: 'all 0.15s' }}>
 {label}
 </button>
 ))}
 </div>
 <div className="split card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 12, padding: '36px 40px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
 <div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>{CASES[activeCase].label}</div>
 <div style={{ fontSize: 22, fontWeight: 800, color: T.text, marginBottom: 16, letterSpacing: '-0.03em', lineHeight: 1.25 }}>{CASES[activeCase].title}</div>
 <div style={{ fontSize: 14, color: T.body, lineHeight: 1.75 }}>{CASES[activeCase].desc}</div>
 </div>
 <div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 16 }}>Impacto típico en {CASES[activeCase].label.toLowerCase()}</div>
 {CASES[activeCase].metrics.map(({ metric, value }) => (
 <div key={metric} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', borderBottom: `1px solid ${T.border}` }}>
 <span style={{ fontSize: 13, color: T.body }}>{metric}</span>
 <span style={{ fontSize: 14, fontWeight: 700, color: T.green }}>{value}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── LO QUE INCLUYE ───────────────────────────────────────────────── */}
 <Section>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 72, alignItems: 'start' }}>
 <div>
 <Tag>Lo que incluye</Tag>
 <H2>Todo lo necesario para planificar con datos.</H2>
 <Lead>Sin integraciones complicadas, sin semanas de implementación, sin depender de un consultor externo. Faro funciona desde el primer archivo que cargas.</Lead>
 </div>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
 {BENEFITS.map(b => (
 <div key={b} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '10px 0' }}>
 <Check />
 <span style={{ fontSize: 13, color: T.body, lineHeight: 1.5 }}>{b}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── VS EXCEL ─────────────────────────────────────────────────────── */}
 <Section id="comparacion" alt>
 <Tag>Comparación</Tag>
 <H2>Excel vs Faro.</H2>
 <Lead>Excel es una herramienta de análisis, no un sistema de pronóstico. Funciona para unos pocos productos. El problema aparece cuando el negocio crece y los modelos manuales no escalan.</Lead>
 <Scroller minWidth={620}>
 <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.border}` }}>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', background: T.surface, padding: '12px 24px', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Capacidad</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Excel</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Faro</div>
 </div>
 {COMPARE.map(({ feature, excel, faro }, i) => (
 <div key={feature} style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', padding: '15px 24px', alignItems: 'center', background: i % 2 === 0 ? T.bg : T.bg2, borderBottom: i < COMPARE.length - 1 ? `1px solid ${T.border}` : 'none' }}>
 <span style={{ fontSize: 13, color: T.body }}>{feature}</span>
 <span style={{ fontSize: 13, color: T.red, textAlign: 'center', fontWeight: 500 }}>{excel}</span>
 <span style={{ fontSize: 13, color: T.green, textAlign: 'center', fontWeight: 700 }}>{faro}</span>
 </div>
 ))}
 </div>
 </Scroller>
 </Section>

 {/* ── QUÉ NECESITAS ────────────────────────────────────────────────── */}
 <Section id="empezar">
 <Tag>Antes de empezar</Tag>
 <H2>Qué necesitas para arrancar — y qué no.</H2>
 <Lead maxWidth={680}>
 La razón más común por la que una herramienta así se queda sin usar no es el precio: es descubrir, tres semanas después, que hacía falta un proyecto de datos antes de poder abrirla. Esta es la lista completa, para que la revises ahora.
 </Lead>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
 <div>
 <H3>Lo que sí necesitas</H3>
 {NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Check /></div>
 <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 <div>
 <H3>Lo que no necesitas</H3>
 {NOT_NEED.map(n => (
 <div key={n} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '9px 0' }}>
 <div style={{ marginTop: 3 }}><Dash /></div>
 <span style={{ fontSize: 13.5, color: T.body, lineHeight: 1.6 }}>{n}</span>
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── PRECIOS ──────────────────────────────────────────────────────── */}
 <Section id="prices">
 <Tag>Precios</Tag>
 <H2>Planes que se adaptan al tamaño de tu operación.</H2>
 <Lead maxWidth={680}>Todos los planes pronostican, generan órdenes de compra y aprenden el plazo de cada proveedor. Lo que cambia es hasta dónde escalan y qué se agrega cuando tienes varias bodegas. Si no sabes cuál te toca, <a href="#que-plan" style={{ color: T.accent, fontWeight: 600, textDecoration: 'none' }}>abajo está el detalle por tipo de operación</a>.</Lead>
 <div className="grid-3" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
 {PLANS.map(({ name, price, priceLabel, priceHint, desc, skus, features, cta, ctaHref, highlight }) => (
 <div key={name} data-reveal className="card-pad" style={{
 borderRadius: 12, padding: '32px 28px',
 background: highlight ? T.text : T.bg,
 border: `1px solid ${highlight ? T.text : T.border}`,
 display: 'flex', flexDirection: 'column', gap: 0,
 }}>
 {highlight && (
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12, background: T.accentBg, display: 'inline-block', padding: '3px 10px', borderRadius: 20, alignSelf: 'flex-start' }}>
 Más popular
 </div>
 )}
 <div style={{ fontSize: 20, fontWeight: 800, color: highlight ? '#fff' : T.text, marginBottom: 6 }}>{name}</div>
 <div style={{ fontSize: 13, color: highlight ? 'rgba(255,255,255,0.6)' : T.muted, marginBottom: 16, lineHeight: 1.5 }}>{desc}</div>
 {priceLabel ? (
 <div style={{ marginBottom: 18 }}>
 <div style={{ fontSize: 34, fontWeight: 900, color: highlight ? '#fff' : T.text, letterSpacing: '-0.04em', lineHeight: 1.1 }}>{priceLabel}</div>
 {priceHint && (
 <div style={{ fontSize: 14, fontWeight: 600, color: highlight ? 'rgba(255,255,255,0.6)' : T.muted, marginTop: 4 }}>{priceHint}</div>
 )}
 </div>
 ) : (
 <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginBottom: 18 }}>
 <span style={{ fontSize: 40, fontWeight: 900, color: highlight ? '#fff' : T.text, letterSpacing: '-0.04em' }}>${price}</span>
 <span style={{ fontSize: 14, fontWeight: 600, color: highlight ? 'rgba(255,255,255,0.6)' : T.muted }}>/mes</span>
 </div>
 )}
 <div style={{ fontSize: 12, fontWeight: 600, color: highlight ? 'rgba(255,255,255,0.5)' : T.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 20, paddingBottom: 20, borderBottom: `1px solid ${highlight ? 'rgba(255,255,255,0.1)' : T.border}` }}>
 {skus}
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 28, flex: 1 }}>
 {features.map(f => (
 <div key={f} style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
 <svg width={14} height={14} viewBox="0 0 14 14" style={{ flexShrink: 0, marginTop: 2 }}>
 <circle cx={7} cy={7} r={7} fill={highlight ? 'rgba(255,255,255,0.1)' : T.greenBg} />
 <path d="M3.5 7 L6 9.5 L10.5 5" stroke={highlight ? '#fff' : T.green} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
 </svg>
 <span style={{ fontSize: 13, color: highlight ? 'rgba(255,255,255,0.8)' : T.body, lineHeight: 1.45 }}>{f}</span>
 </div>
 ))}
 </div>
 <a href={ctaHref} style={{
 display: 'block', textAlign: 'center', padding: '11px 20px', borderRadius: 8,
 fontSize: 13, fontWeight: 700, textDecoration: 'none',
 background: highlight ? '#fff' : T.text,
 color: highlight ? T.text : '#fff',
 transition: 'opacity 0.15s',
 }}
 onMouseEnter={e => (e.currentTarget.style.opacity = '0.85')}
 onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
 >
 {cta}
 </a>
 </div>
 ))}
 </div>
 <p style={{ fontSize: 13, color: T.dim, marginTop: 24, textAlign: 'center' }}>
 Precios en USD por mes. Empieza gratis con datos de ejemplo o escríbenos a hola@usefaro.io para una cotización a medida.
 </p>
 </Section>

 {/* ── QUÉ PLAN ─────────────────────────────────────────────────────── */}
 <Section id="que-plan" alt>
 <Tag>Qué plan te sirve</Tag>
 <H2>Cuál es para ti, y por qué.</H2>
 <Lead maxWidth={700}>
 El plan no se decide por el tamaño de la empresa sino por la forma de tu inventario: cuántas bodegas mueves, cuántos códigos tienes activos y cuánta gente necesita entrar. Busca abajo el párrafo que describe tu operación.
 </Lead>
 <div className="grid-3" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
 {PLAN_FIT.map(({ plan, headline, who, why, outgrow, note }) => (
 <div key={plan} data-reveal className="card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 12, padding: '28px 26px', display: 'flex', flexDirection: 'column' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>{plan}</div>
 <div style={{ fontSize: 16, fontWeight: 800, color: T.text, marginBottom: 16, letterSpacing: '-0.02em', lineHeight: 1.35 }}>{headline}</div>

 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 7 }}>Si te reconoces en esto</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7, marginBottom: 18 }}>{who}</div>

 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 7 }}>Por qué este y no otro</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7, marginBottom: 18, flex: 1 }}>{why}</div>

 {outgrow && (
 <div style={{ background: T.amberBg, border: `1px solid ${T.amberBd}`, borderRadius: 8, padding: '12px 14px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.amber, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Se te queda corto cuando</div>
 <div style={{ fontSize: 12.5, color: T.body, lineHeight: 1.6 }}>{outgrow}</div>
 </div>
 )}
 {note && (
 <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: '12px 14px' }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.muted, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Cómo se activa</div>
 <div style={{ fontSize: 12.5, color: T.body, lineHeight: 1.6 }}>{note}</div>
 </div>
 )}
 </div>
 ))}
 </div>

 <H3 style={{ margin: '44px 0 16px' }}>Los límites, en números</H3>
 <Scroller minWidth={640}>
 <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.border}` }}>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 130px 130px 130px', background: T.surface, padding: '12px 24px', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Límite</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Starter</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Profesional</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>Empresarial</div>
 </div>
 {PLAN_LIMITS.map(({ label, starter, pro, ent }, i) => (
 <div key={label} style={{ display: 'grid', gridTemplateColumns: '1fr 130px 130px 130px', padding: '15px 24px', alignItems: 'center', background: i % 2 === 0 ? T.bg : T.bg2, borderBottom: i < PLAN_LIMITS.length - 1 ? `1px solid ${T.border}` : 'none' }}>
 <span style={{ fontSize: 13, color: T.body }}>{label}</span>
 <span style={{ fontSize: 13, color: T.muted, textAlign: 'center', fontWeight: 500 }}>{starter}</span>
 <span style={{ fontSize: 13, color: T.text, textAlign: 'center', fontWeight: 700 }}>{pro}</span>
 <span style={{ fontSize: 13, color: T.muted, textAlign: 'center', fontWeight: 500 }}>{ent}</span>
 </div>
 ))}
 </div>
 </Scroller>
 <p style={{ fontSize: 12.5, color: T.dim, lineHeight: 1.7, margin: '14px 0 0', maxWidth: 760 }}>
 Un “entrenamiento guardado” es cada corrida que dejas archivada con su configuración y sus resultados, para poder volver a ella o compararla. Las 20 de Starter alcanzan para casi dos años de recálculo mensual.
 </p>
 </Section>

 {/* ── PLAN PROFESIONAL ─────────────────────────────────────────────── */}
 <Section id="profesional">
 <Tag>Plan Profesional</Tag>
 <H2>El plan Profesional, en detalle.</H2>
 <Lead maxWidth={700}>
 Es el plan que compra la mayoría, y también el que más se malinterpreta: no es “Starter con más cupo”. Lo que agrega resuelve dos problemas que solo aparecen cuando creces — no poder mirar todos los productos, y tener el inventario repartido en varios lugares.
 </Lead>

 <H3>A quién le resuelve algo, y qué</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, marginBottom: 48 }}>
 {PRO_ROLES.map(({ role, pain, gain }) => (
 <div key={role} data-reveal className="card-pad" style={{ background: T.bg2, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 14 }}>{role}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Hoy</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65, marginBottom: 14 }}>{pain}</div>
 <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Con Faro</div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.65 }}>{gain}</div>
 </div>
 ))}
 </div>

 <H3>Qué incluye, concretamente</H3>
 <div className="grid-2" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
 {PRO_INCLUDES.map(({ title, desc, isNew }) => (
 <div key={title} data-reveal className="card-pad" style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
 <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
 <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>{title}</span>
 {isNew && (
 <span style={{ fontSize: 10, fontWeight: 700, color: T.green, background: T.greenBg, border: `1px solid ${T.greenBd}`, borderRadius: 20, padding: '2px 9px', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Nuevo</span>
 )}
 </div>
 <div style={{ fontSize: 13, color: T.body, lineHeight: 1.7 }}>{desc}</div>
 </div>
 ))}
 </div>

 <p style={{ fontSize: 13.5, color: T.body, lineHeight: 1.7, margin: '28px 0 0', maxWidth: 760 }}>
 Todo lo anterior va además de lo que ya trae Starter: el semáforo, las órdenes de compra, las recepciones que aprenden el plazo del proveedor, los reportes y las alertas por correo. <a href="#prices" style={{ color: T.accent, fontWeight: 600, textDecoration: 'none' }}>Ver el precio →</a>
 </p>
 </Section>

 {/* ── FAQ ──────────────────────────────────────────────────────────── */}
 <Section alt>
 <div className="split" style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 72, alignItems: 'start' }}>
 <div className="faq-side" style={{ position: 'sticky', top: 80 }}>
 <Tag>Preguntas frecuentes</Tag>
 <H2>Respuestas a las dudas más comunes.</H2>
 <p style={{ fontSize: 15, color: T.body, lineHeight: 1.7, margin: '0 0 24px' }}>
 Si tienes alguna pregunta que no está aquí, escríbenos directamente. Respondemos en menos de 24 horas.
 </p>
 <a href="mailto:hola@usefaro.io" style={{ fontSize: 13, fontWeight: 600, color: T.accent, textDecoration: 'none' }}>
 Escribir al equipo →
 </a>
 </div>
 <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
 {FAQS.map(({ q, a }, i) => (
 <div key={i} style={{ borderBottom: `1px solid ${T.border}` }}>
 <button onClick={() => setOpenFaq(openFaq === i ? null : i)} style={{
 all: 'unset', cursor: 'pointer', width: '100%', display: 'flex',
 justifyContent: 'space-between', alignItems: 'center',
 padding: '20px 0', gap: 16,
 }}>
 <span style={{ fontSize: 14, fontWeight: 600, color: T.text, lineHeight: 1.4, textAlign: 'left' }}>{q}</span>
 <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: '50%', background: T.surface, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, color: T.muted, fontWeight: 400, lineHeight: 1, transition: 'transform 0.2s', transform: openFaq === i ? 'rotate(45deg)' : 'none' }}>+</span>
 </button>
 {openFaq === i && (
 <div style={{ fontSize: 14, color: T.body, lineHeight: 1.7, paddingBottom: 20 }}>{a}</div>
 )}
 </div>
 ))}
 </div>
 </div>
 </Section>

 {/* ── FOOTER ───────────────────────────────────────────────────────── */}
 <footer className="footer-shell" style={{ background: T.bg2, borderTop: `1px solid ${T.border}`, padding: '40px 48px' }}>
 <div style={{ maxWidth: 1100, margin: '0 auto' }}>
 <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 40, marginBottom: 40 }}>
 <div>
 <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
 <div style={{ width: 26, height: 26, borderRadius: 6, background: T.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 900, color: '#fff' }}>F</div>
 <span style={{ fontSize: 15, fontWeight: 800, color: T.text, letterSpacing: '-0.02em' }}>Faro</span>
 </div>
 <p style={{ fontSize: 13, color: T.muted, lineHeight: 1.6, margin: 0 }}>
 Pronóstico de demanda e inteligencia de inventario para distribuidores, retail y manufactura.
 </p>
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>Producto</div>
 {[['#solucion','Cómo funciona'],['#casos','Industrias'],['#prices','Precios'],['#comparacion','vs Excel']].map(([href, label]) => (
 <a key={href} href={href} style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 10 }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>Empresa</div>
 {[['#problema','El problema'],['#nosotros','Nosotros'],['mailto:hola@usefaro.io','Contacto']].map(([href, label]) => (
 <a key={label} href={href} style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 10 }}
 onMouseEnter={e => (e.currentTarget.style.color = T.text)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >{label}</a>
 ))}
 </div>
 <div>
 <div style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>Contacto</div>
 <a href="mailto:angel.zeledon.fernandez@gmail.com" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none', marginBottom: 8 }}
 onMouseEnter={e => (e.currentTarget.style.color = T.accent)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >angel.zeledon.fernandez@gmail.com</a>
 <a href="tel:+50671862820" style={{ display: 'block', fontSize: 13, color: T.muted, textDecoration: 'none' }}
 onMouseEnter={e => (e.currentTarget.style.color = T.accent)}
 onMouseLeave={e => (e.currentTarget.style.color = T.muted)}
 >+506 7186 2820</a>
 </div>
 </div>
 <div className="footer-bottom" style={{ borderTop: `1px solid ${T.border}`, paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
 <div style={{ fontSize: 12, color: T.dim }}>© 2026 Faro. Todos los derechos reservados.</div>
 <div style={{ fontSize: 12, color: T.dim }}>Hecho en Costa Rica</div>
 </div>
 </div>
 </footer>
 </>
 )
}

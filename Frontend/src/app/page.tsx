'use client'
import Link from 'next/link'
import { useState } from 'react'

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
 <nav style={{
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
 <div style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
 {[
 ['#problema', 'El problema'],
 ['#solucion', 'Cómo funciona'],
 ['#casos', 'Industrias'],
 ['#prices', 'Precios'],
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
 <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 48px' }}>{children}</div>
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

// ── Main page ─────────────────────────────────────────────────────────────────
export default function LandingPage() {
 const [activeCase, setActiveCase] = useState(0)
 const [openFaq, setOpenFaq] = useState<number | null>(null)

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
 'Clasificación automática ABC-XYZ',
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

 const PLANS = [
 {
 name: 'Starter',
 price: 99 as number | null,
 priceLabel: null as string | null,
 priceHint: null as string | null,
 desc: 'Para operaciones pequeñas que quieren reemplazar sus modelos en Excel.',
 skus: 'Hasta 1.000 SKUs · 2 usuarios · 1 bodega',
 features: ['Pronóstico por SKU con alertas de quiebre', 'Órdenes de compra y gestión de proveedores', 'Exportación a Excel y PDF', 'Soporte por correo'],
 cta: 'Empezar gratis',
 ctaHref: '/signup?demo=1',
 highlight: false,
 },
 {
 name: 'Profesional',
 price: 649 as number | null,
 priceLabel: null as string | null,
 priceHint: null as string | null,
 desc: 'Para empresas en crecimiento con múltiples categorías o puntos de venta.',
 skus: 'Hasta 5.000 SKUs · 10 usuarios · 5 ubicaciones',
 features: ['Todo lo del plan Starter', 'Multibodega y transferencias entre bodegas', 'Recomendación de comprar vs transferir', 'Clasificación ABC-XYZ y eventos de demanda', 'Alertas por WhatsApp y analista con IA', 'Soporte prioritario'],
 cta: 'Empezar gratis',
 ctaHref: '/signup?demo=1',
 highlight: true,
 },
 {
 name: 'Empresarial',
 price: null as number | null,
 priceLabel: 'Personalizado',
 priceHint: 'Desde $1.990/mes',
 desc: 'Para grandes operaciones con necesidades específicas de integración y escala.',
 skus: 'SKUs, usuarios y ubicaciones ilimitados',
 features: ['Todo lo del plan Profesional', 'Acceso API, webhooks e integraciones', 'Integración a medida (ERP, WMS, BI)', 'Onboarding con equipo técnico dedicado', 'SLA de disponibilidad garantizado', 'Gerente de cuenta asignado'],
 cta: 'Hablar con el equipo',
 ctaHref: 'mailto:hola@usefaro.io?subject=Faro%20%E2%80%94%20activar%20plan%20Empresarial',
 highlight: false,
 },
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
 a: 'Sí. El plan Profesional incluye acceso a la API de Faro, que permite automatizar la carga de datos y la exportación de pronósticos hacia sistemas externos. Para integraciones más complejas, el plan Empresarial incluye desarrollo a medida con nuestro equipo técnico.',
 },
 {
 q: '¿Con qué frecuencia se actualizan los pronósticos?',
 a: 'Depende del plan. En Starter, los modelos se actualizan mensualmente al cargar nuevas ventas. En Profesional, la actualización puede ser semanal o automática si se conecta vía API. En Empresarial, la frecuencia de actualización se define según las necesidades de la operación.',
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
 `}</style>

 <Nav />

 {/* ── HERO ─────────────────────────────────────────────────────────── */}
 <section style={{ minHeight: '100vh', paddingTop: 120, background: T.bg, display: 'flex', flexDirection: 'column', alignItems: 'center', borderBottom: `1px solid ${T.border}` }}>
 <div style={{ maxWidth: 1100, width: '100%', margin: '0 auto', padding: '0 48px' }}>

 <div style={{ display: 'inline-block', padding: '4px 12px', borderRadius: 20, marginBottom: 24, background: T.greenBg, border: `1px solid ${T.greenBd}`, fontSize: 11, fontWeight: 700, color: T.green, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
 Para distribuidores, retail y manufactura
 </div>

 <h1 style={{ fontSize: 56, fontWeight: 900, color: T.text, margin: '0 0 18px', letterSpacing: '-0.05em', lineHeight: 1.1, maxWidth: 720 }}>
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

 <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 36, paddingBottom: 72 }}>
 <div style={{ fontSize: 12, color: T.dim }}>Industrias:</div>
 {['Distribución', 'Retail', 'Manufactura', 'Mayoristas', 'E-commerce'].map(s => (
 <span key={s} style={{ fontSize: 12, fontWeight: 500, color: T.muted, padding: '4px 12px', borderRadius: 20, border: `1px solid ${T.border}` }}>{s}</span>
 ))}
 </div>
 </div>
 </section>

 {/* ── STATS STRIP ──────────────────────────────────────────────────── */}
 <div style={{ background: T.text, padding: '40px 48px' }}>
 <div style={{ maxWidth: 1100, margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0 }}>
 {[
 { value: '94%', label: 'Precisión promedio de pronóstico' },
 { value: '−75%', label: 'Tiempo invertido en planificación' },
 { value: '50K+', label: 'SKUs soportados por instancia' },
 { value: '1 día', label: 'Tiempo promedio de implementación' },
 ].map(({ value, label }, i) => (
 <div key={label} style={{ textAlign: 'center', padding: '0 32px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.1)' : 'none' }}>
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
 <div key={title} style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 10, padding: '22px 24px' }}>
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
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 20 }}>
 {STEPS.map(({ n, title, desc }) => (
 <div key={n} style={{ display: 'flex', gap: 18, alignItems: 'flex-start', background: T.bg2, borderRadius: 10, padding: '22px 24px', border: `1px solid ${T.border}` }}>
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
 <figure key={img} style={{ margin: 0 }}>
 <div style={{ borderRadius: 12, border: `1px solid ${T.border}`, overflow: 'hidden', boxShadow: '0 12px 32px rgba(15,23,42,0.10)' }}>
 <img src={img} alt={alt} style={{ display: 'block', width: '100%', height: 'auto' }} />
 </div>
 <figcaption style={{ fontSize: 12, color: T.dim, marginTop: 8 }}>{caption}</figcaption>
 </figure>
 ))}
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
 <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 12, padding: '36px 40px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48, alignItems: 'start' }}>
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
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 72, alignItems: 'start' }}>
 <div>
 <Tag>Lo que incluye</Tag>
 <H2>Todo lo necesario para planificar con datos.</H2>
 <Lead>Sin integraciones complicadas, sin semanas de implementación, sin depender de un consultor externo. Faro funciona desde el primer archivo que cargas.</Lead>
 </div>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
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
 </Section>

 {/* ── PRECIOS ──────────────────────────────────────────────────────── */}
 <Section id="prices">
 <Tag>Precios</Tag>
 <H2>Planes que se adaptan al tamaño de tu operación.</H2>
 <Lead>Todos los planes incluyen acceso completo a las funciones de pronóstico. La diferencia está en la escala y en el nivel de soporte.</Lead>
 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
 {PLANS.map(({ name, price, priceLabel, priceHint, desc, skus, features, cta, ctaHref, highlight }) => (
 <div key={name} style={{
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

 {/* ── FAQ ──────────────────────────────────────────────────────────── */}
 <Section alt>
 <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 72, alignItems: 'start' }}>
 <div style={{ position: 'sticky', top: 80 }}>
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
 <footer style={{ background: T.bg2, borderTop: `1px solid ${T.border}`, padding: '40px 48px' }}>
 <div style={{ maxWidth: 1100, margin: '0 auto' }}>
 <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 40, marginBottom: 40 }}>
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
 <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
 <div style={{ fontSize: 12, color: T.dim }}>© 2026 Faro. Todos los derechos reservados.</div>
 <div style={{ fontSize: 12, color: T.dim }}>Hecho en Costa Rica</div>
 </div>
 </div>
 </footer>
 </>
 )
}

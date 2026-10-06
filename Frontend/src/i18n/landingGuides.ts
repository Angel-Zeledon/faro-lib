/**
 * Copy for the landing's guides: five long-form pages written for what a buyer
 * actually types into a search box ("software de inventario para
 * distribuidores", "cómo calcular el punto de reorden", "stock de seguridad",
 * "pronóstico de demanda para compras", "qué es StockAI").
 *
 * Same rules as `landing.ts` and `landingContent.ts`: typed per language, so a
 * missing translation is a compile error; NOTHING here may promise what the
 * product does not do; no customer, quote, logo, rating or result percentage;
 * and NO PRICE (prices live on /precios). Every number inside a worked example
 * is invented on purpose and said to be so; every rule quoted is the one the
 * backend applies (backend/inventory/service.py: `_calc_signal`,
 * `_safety_stock`, `_calc_recommended`; defaults in
 * backend/inventory/signal_thresholds.py and defaults.py).
 *
 * Inline links: a paragraph may contain `[text](/path)`; GuidePages.tsx turns
 * it into a Next link. Paths must be public landing routes.
 */
import type { Lang } from './translations'

export type GuideKey = 'distributors' | 'reorderPoint' | 'safetyStock' | 'forecast' | 'about'

export interface GuideSection { h: string; p: string[]; list?: string[] }

/**
 * A boxed block inside a guide: a formula, a worked example or the one thing to
 * keep. `section` is the index into `sections`; `after` is how many of that
 * section's paragraphs come before the box (0 = the box opens the section).
 * `lines` are set one per row, so a calculation reads as a calculation. The
 * numbers in an example are invented and the paragraph above says so.
 */
export type GuideCalloutKind = 'formula' | 'example' | 'takeaway'
export interface GuideCallout { kind: GuideCalloutKind; section: number; after: number; label: string; lines: string[] }
export interface GuideFaq { q: string; a: string }

export interface Guide {
  /** Name in menus, breadcrumbs and the footer. */
  label: string
  /** The H1 and its intro. */
  title: string
  intro: string
  /** What a search result shows (rendered by the server wrapper, Spanish). */
  metaTitle: string
  metaDesc: string
  sections: GuideSection[]
  /** Boxed formulas, worked examples and takeaways. Required (empty if none) so a language cannot silently skip them. */
  callouts: GuideCallout[]
  faqTitle: string
  faq: GuideFaq[]
  /** The closing call to action: a sentence above the two buttons. */
  ctaLead: string
  /** Other guides to read next (keys), shown under the CTA. */
  next: GuideKey[]
}

export interface GuidesCopy {
  /** Footer column heading and the label of the hub list on each guide. */
  footerHead: string
  /** Heading above the "keep reading" guide links. */
  nextTitle: string
  /** Heading above the links to the product pages. */
  productTitle: string
  /** Link texts to the product pages, in this order: how it works, pricing, how it is calculated. */
  productLinks: [string, string, string]
  ctaTrial: string
  ctaSignup: string
  items: Record<GuideKey, Guide>
}

const es: GuidesCopy = {
  footerHead: 'Guías',
  nextTitle: 'Otras guías',
  productTitle: 'Y para ver el producto',
  productLinks: ['Cómo funciona StockAI', 'Precio y código fuente', 'Cómo se calcula cada número'],
  ctaTrial: 'Probar sin registrarme',
  ctaSignup: 'Crear cuenta',
  items: {
    // ── (e) Brand page ───────────────────────────────────────────────────────
    about: {
      label: 'Qué es StockAI',
      title: 'Qué es StockAI (Stock AI): el software que te dice qué pedir cada mañana.',
      intro: 'StockAI, a veces escrito «Stock AI», es una aplicación web de compras de inventario para distribuidores, mayoristas y comercios. Lee tu historial de ventas y tus existencias y te dice qué productos se van a quebrar, cuánto pedir y a qué proveedor. Aquí está, en palabras simples, qué es, para quién y qué no es.',
      metaTitle: 'Qué es StockAI (Stock AI): software de compras e inventario',
      metaDesc: 'StockAI, o Stock AI, es un software que lee tus ventas y existencias y te dice qué pedir, cuánto y a qué proveedor. Qué es, para quién es y qué no hace.',
      sections: [
        {
          h: 'StockAI en una frase',
          p: [
            'StockAI es un software de planeación de compras: toma tus ventas históricas, tus existencias y el plazo de entrega de cada proveedor, y te entrega cada mañana una lista de decisiones: qué producto pedir ya, cuál pedir pronto, cuál está bien y cuál tienes de sobra.',
            'Se escribe StockAI, todo junto, y mucha gente lo busca como «stock ai» o «stockai». Es el mismo producto. El nombre junta dos ideas: stock, tu inventario, e IA, la inteligencia artificial que pronostica la demanda de cada producto.',
          ],
        },
        {
          h: 'Qué hace, paso por paso',
          p: ['El recorrido completo está en [cómo funciona StockAI](/como-funciona); este es el resumen:'],
          list: [
            'Subes tus ventas en CSV o Excel, o conectas una base de datos Postgres o MySQL. Antes de entrenar nada, la herramienta revisa el archivo y te avisa de fechas que faltan o historiales demasiado cortos.',
            'Para cada producto compiten hasta nueve modelos de pronóstico sobre tu propio historial, y se queda el que menos cuesta equivocarse. El detalle está en [cómo se calcula](/como-se-calcula).',
            'El pronóstico se cruza con tus existencias, con lo que viene en camino y con el plazo del proveedor. De ahí salen el punto de reorden, el semáforo (PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK) y la cantidad sugerida.',
            'Generas la orden de compra, la revisas, la ajustas y la envías tú. Cuando llega la mercadería registras la recepción, y StockAI va aprendiendo cuánto tarda de verdad cada proveedor.',
          ],
        },
        {
          h: 'Para quién es',
          p: [
            'Para quien compra mercadería a proveedores con un plazo de entrega: distribuidores, mayoristas, comercios, ferreterías, farmacias, casas de repuestos y fabricantes. Encaja sobre todo cuando hay cientos o miles de productos, el equipo es pequeño y las compras hoy se deciden con una hoja de Excel o con la memoria del comprador. Hay páginas por rubro en [industrias](/industrias).',
            'StockAI se vende solo como código fuente, con un pago único, para instalarlo en tu propia infraestructura. El detalle está en [precio](/precios).',
          ],
        },
        {
          h: 'Qué no es',
          p: ['Conviene decirlo de frente, antes de que subas el primer archivo:'],
          list: [
            'No es un sistema de facturación, de contabilidad ni un punto de venta. Sigues facturando donde siempre; StockAI lee esa historia.',
            'No compra solo. Sugiere qué, cuánto y a quién; la orden la revisas y la envías tú.',
            'No lleva trazabilidad por lote ni por fecha de vencimiento de cada unidad.',
            'No inventa pronósticos sin historial: un producto con menos de 20 periodos queda marcado SIN DATOS en lugar de recibir un número fabricado.',
            'No publica cifras de aciertos de marketing: el error que ves es el de tus propios datos.',
          ],
        },
        {
          h: 'Cómo empezar',
          p: [
            'La forma más rápida es probarlo: puedes abrir una cuenta de prueba al instante, sin registrarte, o crear una cuenta y subir tu archivo. Si ya tienes ventas históricas, la primera lista de qué pedir puede estar lista en menos de una hora.',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 0, after: 2, label: 'En resumen', lines: ['Entran tus ventas y tus existencias; sale una lista de qué pedir, cuánto y a qué proveedor. La orden la revisas y la envías tú.'] },
      ],
      faqTitle: 'Preguntas sobre StockAI',
      faq: [
        { q: '¿Qué es StockAI?', a: 'Es un software web de compras de inventario para distribuidores, mayoristas y comercios. Lee tus ventas y tus existencias y te dice qué pedir, cuánto y a qué proveedor.' },
        { q: '¿«Stock AI» y «StockAI» son lo mismo?', a: 'Sí. El nombre correcto es StockAI, todo junto; «stock ai» con espacio es como mucha gente lo escribe al buscarlo.' },
        { q: '¿StockAI reemplaza a mi ERP?', a: 'No, lo complementa. Tu ERP registra lo que pasó (ventas, existencias, compras); StockAI lee esa historia y te dice qué comprar. Sigues facturando y contabilizando donde siempre.' },
        { q: '¿Cuánto cuesta?', a: 'StockAI se vende solo como código fuente: $14.999 USD, en un pago único, para instalarlo en tu propia infraestructura. Escríbenos para comprarlo.' },
      ],
      ctaLead: 'Prueba StockAI con tus propios datos y mira tu primera lista de compras, con el motor completo.',
      next: ['distributors', 'reorderPoint', 'forecast'],
    },

    // ── (a) Inventory software for distributors ──────────────────────────────
    distributors: {
      label: 'Software de inventario para distribuidores',
      title: 'Software de inventario para distribuidores: qué debe resolver y cómo elegirlo.',
      intro: 'Un distribuidor no compra para consumir: compra para revender, con proveedores que tardan semanas y miles de códigos que vigilar. Estas son las preguntas que un buen software de inventario tiene que contestar cada mañana, y cómo las contesta StockAI.',
      metaTitle: 'Software de inventario para distribuidores | StockAI',
      metaDesc: 'Qué debe resolver un software de inventario para distribuidores: qué pedir, cuánto, a quién y qué sobra. Criterios para elegir y cómo lo hace StockAI.',
      sections: [
        {
          h: 'El problema real de un distribuidor',
          p: [
            'Controlar existencias no es lo difícil: casi cualquier sistema te dice cuántas unidades hay. Lo difícil es decidir la compra: cuánto pedir de cada producto, cuándo y a qué proveedor, sabiendo que el pedido tarda y que la demanda cambia. Pedir de menos deja el estante vacío y la venta se pierde; pedir de más deja dinero parado y producto que envejece.',
            'Por eso el inventario de un distribuidor se juega en la compra, no en el conteo. Un buen software no es el que guarda más datos, sino el que convierte tus ventas y tus existencias en decisiones que un comprador pueda revisar y aprobar en minutos.',
          ],
        },
        {
          h: 'Lo que debería hacer un software de inventario para distribuidores',
          p: [],
          list: [
            'Pronosticar la demanda de cada producto con su propio historial, no con un promedio general. Un producto estable y otro que se vende a saltos necesitan modelos distintos; ver [pronóstico de demanda para compras](/pronostico-de-demanda-para-compras).',
            'Usar el plazo real de cada proveedor, no el que está escrito en la ficha. StockAI lo aprende de tus recepciones: desde la tercera, el cálculo usa el promedio real.',
            'Calcular un punto de reorden por producto y avisar cuando se cruza, en lugar de esperar a que alguien lo note. La explicación está en [cómo calcular el punto de reorden](/como-calcular-el-punto-de-reorden).',
            'Dimensionar un colchón de seguridad según cuánto varía la venta y cuánto se atrasa el proveedor; ver [stock de seguridad](/stock-de-seguridad).',
            'Marcar el sobrestock, que es el otro costo de comprar a ojo, y mostrar cuánto dinero tienes parado en lo que no rota.',
            'Cerrar el ciclo: órdenes de compra, recepciones parciales o completas, y seguimiento de lo que viene en camino para no pedir dos veces lo mismo.',
            'Manejar varias bodegas y sugerir un traslado antes de comprar cuando una tiene de sobra lo que a otra le falta.',
          ],
        },
        {
          h: 'Cómo lo resuelve StockAI',
          p: [
            'Cada mañana ves una lista ordenada por urgencia con cuatro estados: PEDIR YA (menos de medio plazo de entrega de cobertura), PEDIR PRONTO (en el punto de reorden o por debajo), OK y SOBRESTOCK. No es una caja negra: la regla que decide el color es publicada y la puedes hacer a mano; está en [cómo funciona](/como-funciona) y [cómo se calcula](/como-se-calcula).',
            'La cantidad sugerida descuenta lo que ya viene en camino, de un proveedor o de un traslado entre bodegas, y respeta el mínimo de compra. La orden la ajustas y la envías tú, por correo o por WhatsApp. Hay páginas con ejemplos por rubro, como [consumo masivo, ferretería, farmacia, autopartes y retail](/industrias).',
          ],
        },
        {
          h: 'Cómo evaluar cualquier opción, incluida esta',
          p: ['Antes de elegir, pídele a cualquier proveedor de software que te muestre esto con tus datos, no con los de una demostración:'],
          list: [
            'Si el plazo de cada proveedor entra en el cálculo y se corrige solo con las entregas reales.',
            'Si el error del pronóstico se muestra por producto y sobre fechas que el modelo no vio.',
            'Qué hace con los productos con poco historial: un buen sistema los marca en vez de inventarles un número.',
            'Si explica de dónde sale cada cantidad sugerida.',
            'Si puedes llevarte tus datos y borrarlos cuando quieras.',
          ],
        },
        {
          h: 'Qué no hace StockAI',
          p: [
            'No factura, no lleva contabilidad ni es un punto de venta, y no compra por ti. Si tu necesidad principal es alguna de esas, necesitas otra herramienta; StockAI trabaja al lado de tu ERP, no en su lugar. Si hoy llevas todo en Excel, la comparación honesta está en [StockAI vs. Excel](/stockai-vs-excel).',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 0, after: 2, label: 'En resumen', lines: ['El inventario de un distribuidor se juega en la compra, no en el conteo: cuánto pedir de cada producto, cuándo y a qué proveedor.'] },
      ],
      faqTitle: 'Preguntas frecuentes sobre software de inventario para distribuidores',
      faq: [
        { q: '¿Qué diferencia hay entre controlar el inventario y planear las compras?', a: 'Controlar es saber cuánto hay; planear es decidir cuánto, cuándo y a quién pedir. StockAI se concentra en lo segundo: toma las existencias que ya registras en tu sistema y las cruza con el pronóstico y el plazo del proveedor.' },
        { q: '¿Necesito un analista de datos para usarlo?', a: 'No. Subes tus ventas y ves la lista de qué pedir; no hay modelos que configurar ni código.' },
        { q: '¿Sirve si tengo varias bodegas?', a: 'Sí. Puedes definir rutas entre bodegas con sus días de tránsito, y StockAI sugiere un traslado antes de comprar cuando una bodega tiene de sobra lo que otra necesita.' },
        { q: '¿Qué datos necesito para empezar?', a: 'Un archivo de ventas con fecha, producto y cantidad vendida, en CSV o Excel, con al menos 20 periodos de historial por producto. Las existencias actuales y el plazo de cada proveedor completan el cálculo.' },
      ],
      ctaLead: 'Sube tu historial y mira tu primera lista de compras, con el motor completo.',
      next: ['reorderPoint', 'safetyStock', 'about'],
    },

    // ── (b) Reorder point ────────────────────────────────────────────────────
    reorderPoint: {
      label: 'Cómo calcular el punto de reorden',
      title: 'Cómo calcular el punto de reorden, con un ejemplo paso a paso.',
      intro: 'El punto de reorden es el nivel de existencias en el que conviene hacer el pedido. Si lo calculas bien, el pedido llega justo antes de que se acabe el producto. Esta guía explica la fórmula, un ejemplo con números y cómo la aplica StockAI.',
      metaTitle: 'Cómo calcular el punto de reorden: fórmula y ejemplo',
      metaDesc: 'Qué es el punto de reorden, la fórmula (demanda durante el plazo más stock de seguridad), un ejemplo con números y cómo lo calcula StockAI para cada producto.',
      sections: [
        {
          h: 'Qué es el punto de reorden',
          p: [
            'Es la cantidad de existencias en la que haces un pedido nuevo. Mientras el proveedor tarda en entregar sigues vendiendo, así que el punto de reorden tiene que cubrir lo que vas a vender durante esa espera y, además, un colchón por si la venta sube o el proveedor se atrasa.',
          ],
        },
        {
          h: 'La fórmula',
          p: [
            'La demanda durante el plazo es la venta diaria promedio multiplicada por los días que tarda el proveedor. El stock de seguridad es el colchón; cómo se calcula tiene su propia guía: [stock de seguridad](/stock-de-seguridad).',
          ],
        },
        {
          h: 'Un ejemplo con números',
          p: [
            'Los números de este ejemplo son inventados, para ilustrar la regla. Un producto se vende en promedio 20 unidades por día. El proveedor tarda 9 días en entregar. El colchón de seguridad calculado es de 30 unidades. Hoy hay 150 unidades y nada en camino.',
            'La cobertura de hoy ya está por debajo del punto de reorden, así que toca pedir. La cantidad sugerida es lo que falta para cubrir el plazo con el colchón.',
          ],
        },
        {
          h: 'Cómo lo aplica StockAI',
          p: [
            'StockAI calcula el punto de reorden de cada producto con la demanda que pronostica, no con el promedio histórico plano, y con el plazo real del proveedor: el que escribes en la ficha al principio y, desde la tercera recepción registrada, el promedio de lo que de verdad tardó. Si pides cada cierto tiempo (por ejemplo, un pedido semanal), ese intervalo también se suma al plazo que hay que cubrir.',
            'El semáforo usa ese punto como frontera. En el ejemplo anterior, 7,5 días de cobertura es más que la mitad del plazo (4,5 días), así que no es PEDIR YA, pero está por debajo de 10,5 días: es PEDIR PRONTO. PEDIR YA aparece cuando la cobertura baja de medio plazo de entrega. Cerca del otro extremo, SOBRESTOCK empieza desde el mayor entre tres plazos de cobertura y dos veces el punto de reorden. Estos umbrales son los de fábrica y se pueden configurar por empresa, proveedor o categoría. Todo está en [cómo se calcula](/como-se-calcula).',
          ],
        },
        {
          h: 'Errores comunes al calcularlo',
          p: [],
          list: [
            'Usar el plazo que promete el proveedor en vez del que realmente cumple. Si tarda 12 días y dice 7, quiebras antes de que llegue el pedido.',
            'Usar un promedio de venta de todo el año en un producto estacional: en temporada alta el punto de reorden queda corto.',
            'Olvidar el colchón: sin stock de seguridad, cualquier semana mejor de lo normal te deja sin producto.',
            'Ignorar lo que ya viene en camino y pedir dos veces lo mismo.',
            'Usar una sola regla para todo el catálogo: cada producto tiene su propia variabilidad.',
          ],
        },
        {
          h: 'Hacerlo a mano o dejar que lo haga el sistema',
          p: [
            'Para diez productos estables, una hoja de cálculo alcanza. Con cientos o miles de códigos, un plazo distinto por proveedor y demanda que cambia, recalcular todo cada semana deja de ser realista. Para ese caso está StockAI, y puedes comparar los dos enfoques en [StockAI vs. Excel](/stockai-vs-excel).',
          ],
        },
      ],
      callouts: [
        { kind: 'formula', section: 1, after: 0, label: 'Fórmula', lines: ['Punto de reorden = demanda durante el plazo de entrega + stock de seguridad'] },
        { kind: 'example', section: 2, after: 1, label: 'Ejemplo resuelto', lines: ['Demanda durante el plazo: 20 × 9 = 180 unidades', 'Punto de reorden: 180 + 30 = 210 unidades', 'En días de cobertura: 210 ÷ 20 = 10,5 días', 'Cobertura de hoy: 150 ÷ 20 = 7,5 días', 'Cantidad sugerida: 180 + 30 − 150 − 0 en camino = 60 unidades'] },
      ],
      faqTitle: 'Preguntas frecuentes sobre el punto de reorden',
      faq: [
        { q: '¿Cuál es la fórmula del punto de reorden?', a: 'Demanda durante el plazo de entrega más stock de seguridad. La demanda durante el plazo es la venta diaria promedio por los días que tarda el proveedor.' },
        { q: '¿El punto de reorden es lo mismo que el stock mínimo?', a: 'No necesariamente. El stock mínimo suele ser un número fijo que alguien decidió; el punto de reorden se calcula con la venta, el plazo y la variabilidad de cada producto, y cambia cuando esas cosas cambian.' },
        { q: '¿Cada cuánto hay que recalcularlo?', a: 'Cada vez que cambie la demanda o el plazo del proveedor. En StockAI se recalcula cuando cargas ventas nuevas, o con un recálculo programado.' },
        { q: '¿Qué hace StockAI cuando se cruza el punto de reorden?', a: 'El producto pasa a PEDIR PRONTO (o a PEDIR YA si la cobertura es menor que medio plazo de entrega) y aparece con una cantidad sugerida que descuenta lo que ya viene en camino.' },
      ],
      ctaLead: 'Deja que StockAI calcule el punto de reorden de cada producto con tu historial.',
      next: ['safetyStock', 'forecast', 'distributors'],
    },

    // ── (c) Safety stock ─────────────────────────────────────────────────────
    safetyStock: {
      label: 'Stock de seguridad',
      title: 'Stock de seguridad: qué es y cómo se calcula.',
      intro: 'El stock de seguridad es el colchón de unidades que te protege cuando la venta sube más de lo esperado o el proveedor se atrasa. Sin él, el punto de reorden solo sirve en una semana perfecta. Aquí está la fórmula, un ejemplo con números y cómo decide StockAI su tamaño.',
      metaTitle: 'Stock de seguridad: qué es, fórmula y cómo se calcula',
      metaDesc: 'Qué es el stock de seguridad, la fórmula con nivel de servicio, un ejemplo con números, y cómo influyen la variabilidad de la venta y el atraso del proveedor.',
      sections: [
        {
          h: 'Qué es el stock de seguridad',
          p: [
            'Es el inventario extra que mantienes por encima de lo que esperas vender durante el plazo de entrega. Existe porque tanto la demanda como el proveedor son inciertos: puedes vender más de lo previsto, o el pedido puede llegar tarde. Es la mitad del punto de reorden: ver [cómo calcular el punto de reorden](/como-calcular-el-punto-de-reorden).',
          ],
        },
        {
          h: 'La fórmula',
          p: [
            'Aquí z depende del nivel de servicio que quieres y σ mide la incertidumbre durante el plazo de entrega. Con demanda variable y un plazo fijo, solo cuenta la variación de la venta; si además el proveedor es irregular, se suma una segunda fuente de incertidumbre y las dos se combinan en cuadratura.',
            'El nivel de servicio es la probabilidad de no quedarte sin producto durante un ciclo de reposición. Valores usuales de z: 1,28 para 90 %, 1,645 para 95 %, 2,05 para 98 % y 2,33 para 99 %. StockAI parte de 95 %.',
          ],
        },
        {
          h: 'Un ejemplo con números',
          p: [
            'Los números son inventados, para ilustrar la regla. Un producto vende 20 unidades por día con una desviación de 6 unidades por día, y el proveedor tarda 9 días. Con 95 % de nivel de servicio:',
            'Ahora supón que el proveedor es irregular: su plazo varía unos 2 días. El atraso del proveedor pesó más que la variación de la venta, y esa es la razón de medirlo en lugar de suponer que siempre cumple.',
          ],
        },
        {
          h: 'Cómo lo decide StockAI',
          p: [
            'StockAI calcula el colchón por producto, con la variabilidad de su propia venta, y suma la irregularidad del proveedor a partir de las entregas que registras. El nivel de servicio parte de 95 % y puedes cambiarlo para toda la empresa, por proveedor, por categoría o por producto: un producto crítico puede llevar 98 % y uno secundario, 90 %. El cálculo completo está descrito en [cómo se calcula](/como-se-calcula).',
            'Una advertencia honesta: en productos de venta intermitente (muchos periodos en cero, y de pronto una venta grande) una fórmula basada en la distribución normal no mantiene con precisión el nivel de servicio nominal. StockAI no imprime un porcentaje que no cumple: marca esas filas con un aviso de demanda intermitente.',
          ],
        },
        {
          h: 'Qué mueve el tamaño del colchón',
          p: [],
          list: [
            'Más variabilidad de la venta, más colchón.',
            'Un plazo más largo, más colchón, aunque crece con la raíz del plazo y no en línea recta.',
            'Un proveedor irregular, más colchón, y puede pesar más que la propia demanda.',
            'Un nivel de servicio más alto, más colchón y más dinero inmovilizado: cada punto extra cuesta más que el anterior.',
          ],
        },
        {
          h: 'El costo de pasarse y el de quedarse corto',
          p: [
            'Un colchón enorme parece seguro, pero es dinero parado y producto que envejece. Uno insuficiente produce quiebres y ventas perdidas. Por eso conviene fijar el nivel de servicio por producto según qué tanto duele quedarse sin él, y revisar el sobrestock que se acumula: [software de inventario para distribuidores](/software-de-inventario-para-distribuidores).',
          ],
        },
      ],
      callouts: [
        { kind: 'formula', section: 1, after: 0, label: 'Fórmula', lines: ['Stock de seguridad = z × σ', 'Plazo fijo: σ = desviación diaria de la venta × √(plazo en días)', 'Proveedor irregular: σ = √(plazo × desviación de la venta² + venta diaria² × desviación del plazo²)'] },
        { kind: 'example', section: 2, after: 1, label: 'Ejemplo resuelto', lines: ['Solo demanda variable: 1,645 × 6 × √9 = 1,645 × 6 × 3 ≈ 30 unidades', 'Atraso del proveedor (plazo variable de unos 2 días): 1,645 × 20 × 2 ≈ 66', 'Las dos combinadas en cuadratura: √(30² + 66²) ≈ 72 unidades'] },
      ],
      faqTitle: 'Preguntas frecuentes sobre el stock de seguridad',
      faq: [
        { q: '¿Cuál es la fórmula del stock de seguridad?', a: 'z × σ, donde z viene del nivel de servicio elegido y σ es la incertidumbre de la demanda durante el plazo de entrega, combinada con la del proveedor cuando este es irregular.' },
        { q: '¿Qué nivel de servicio debo usar?', a: 'Depende de cuánto cuesta quedarte sin el producto. StockAI parte de 95 % y te deja cambiarlo por empresa, proveedor, categoría o producto.' },
        { q: '¿Stock de seguridad y stock mínimo son lo mismo?', a: 'No. El stock mínimo suele ser un número fijo decidido a mano; el de seguridad se calcula con la variabilidad real de la venta y del plazo.' },
        { q: '¿Qué pasa con los productos que casi no se venden?', a: 'Con muchos periodos en cero la fórmula normal no cumple con precisión el nivel de servicio, y StockAI lo avisa en la fila en vez de mostrar un porcentaje que no se sostiene. Con menos de 20 periodos de historial, el producto queda en SIN DATOS.' },
      ],
      ctaLead: 'Mira el colchón de cada producto calculado con tu propio historial.',
      next: ['reorderPoint', 'forecast', 'about'],
    },

    // ── (d) Demand forecasting for purchasing ────────────────────────────────
    forecast: {
      label: 'Pronóstico de demanda para compras',
      title: 'Pronóstico de demanda para compras: de la venta pasada a la orden de compra.',
      intro: 'Pronosticar la demanda no es adivinar el futuro: es estimar cuánto se venderá de cada producto durante lo que tarda en llegar un pedido, con un margen de error a la vista. Esta guía explica cómo se usa ese pronóstico para comprar mejor, y cómo lo hace StockAI.',
      metaTitle: 'Pronóstico de demanda para compras: cómo usarlo bien',
      metaDesc: 'Cómo usar el pronóstico de demanda para decidir compras: qué se pronostica, por qué un solo método no basta, cómo se mide el error y cómo lo hace StockAI.',
      sections: [
        {
          h: 'Para qué sirve pronosticar en compras',
          p: [
            'El pronóstico es la entrada de casi todas las decisiones de compra: el punto de reorden, el stock de seguridad y la cantidad por pedir dependen de cuánto se espera vender. Un promedio plano sirve mientras la demanda es estable; falla con temporadas, tendencias, promociones o ventas que llegan a saltos.',
          ],
        },
        {
          h: 'Por qué un solo método no alcanza',
          p: [
            'No todos los productos se venden igual. Uno estable se pronostica bien con un modelo simple; uno estacional necesita un modelo que entienda el calendario; uno intermitente, con muchos periodos en cero, requiere modelos pensados para eso. Por eso StockAI clasifica cada producto por cómo se vende (estable, estacional, intermitente o volátil) y hace competir, producto por producto, hasta nueve modelos: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, una red neuronal LSTM y un modelo global que aprende de todo el catálogo.',
          ],
        },
        {
          h: 'Cómo se elige el modelo ganador',
          p: [],
          list: [
            'A cada modelo se le esconde un tramo del final de tu historial y se le pide pronosticarlo, en varios cortes. Así se mide contra datos que no vio.',
            'El ganador se elige con un costo en el que quedarse sin producto pesa tres veces más que sobrar, porque en compras un quiebre y un exceso no cuestan lo mismo.',
            'Tiene que superar a dos pronósticos ingenuos: repetir el último valor y repetir la temporada anterior. Si no los supera, no se justifica un modelo complejo.',
            'El ganador se vuelve a entrenar con todo el historial y proyecta la demanda con un rango probable alrededor, no un número único.',
          ],
        },
        {
          h: 'De la demanda pronosticada a la compra',
          p: [
            'El pronóstico no es el final. Se cruza con las existencias, con lo que viene en camino y con el plazo del proveedor para dar el punto de reorden, la señal del semáforo y la cantidad sugerida. La explicación de esa parte está en [cómo calcular el punto de reorden](/como-calcular-el-punto-de-reorden) y [stock de seguridad](/stock-de-seguridad). El recorrido completo, con capturas, está en [cómo funciona StockAI](/como-funciona).',
          ],
        },
        {
          h: 'Los límites, dichos de frente',
          p: [],
          list: [
            'Un producto con menos de 20 periodos de historial queda fuera del pronóstico y aparece como SIN DATOS: no se le inventa un número.',
            'El rango no es una garantía: es la franja donde probablemente caerá la venta.',
            'No adivina lo que nunca pasó. Una promoción nueva o un cliente grande nuevo se prueban en el simulador de escenarios.',
            'El color del semáforo lo decide una regla publicada, no el modelo: la IA pronostica y la regla decide.',
            'No compra por ti: sugiere, y la orden la envías tú.',
          ],
        },
        {
          h: 'Cómo medir si el pronóstico es bueno',
          p: [
            'Sobre fechas que el modelo no vio, producto por producto, y contra lo que de verdad se vendió después. StockAI muestra el error de cada producto, no un único porcentaje para todo el catálogo, y no publica una cifra de precisión de marketing: la que ves es la de tus datos.',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 1, after: 1, label: 'En resumen', lines: ['Ningún método sirve para todos los productos: cada producto tiene su propia competencia entre modelos, y gana el que menos cuesta equivocarse.'] },
      ],
      faqTitle: 'Preguntas frecuentes sobre el pronóstico de demanda',
      faq: [
        { q: '¿Qué historial necesito para pronosticar?', a: 'Al menos 20 periodos por producto, en un archivo CSV o Excel con fecha, producto y cantidad vendida.' },
        { q: '¿Qué modelos usa StockAI?', a: 'Hasta nueve: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, una red LSTM y un modelo global. Compiten por producto y gana el que menos cuesta equivocarse.' },
        { q: '¿Qué pasa con una promoción nueva?', a: 'El modelo no puede conocer lo que nunca ocurrió. Se prueba en el simulador de escenarios, que muestra qué cambia en la compra.' },
        { q: '¿Cada cuánto se actualiza el pronóstico?', a: 'Cada vez que cargas ventas nuevas, o con un recálculo programado: semanal, diario, solo días hábiles, cada hora o mensual.' },
      ],
      ctaLead: 'Sube tu historial y mira el pronóstico y el error de cada producto.',
      next: ['reorderPoint', 'safetyStock', 'distributors'],
    },
  },
}

const en: GuidesCopy = {
  footerHead: 'Guides',
  nextTitle: 'More guides',
  productTitle: 'And to see the product',
  productLinks: ['How StockAI works', 'Price and source code', 'How every number is calculated'],
  ctaTrial: 'Try it without signing up',
  ctaSignup: 'Create an account',
  items: {
    about: {
      label: 'What is StockAI',
      title: 'What is StockAI (Stock AI): the software that tells you what to order every morning.',
      intro: 'StockAI, sometimes written “Stock AI”, is a web application for inventory purchasing, for distributors, wholesalers and shops. It reads your sales history and your stock and tells you which products are about to run out, how much to order and from which supplier. Here is what it is, who it is for and what it is not, in plain words.',
      metaTitle: 'What is StockAI (Stock AI): purchasing and inventory software',
      metaDesc: 'StockAI, or Stock AI, reads your sales and stock and tells you what to order, how much and from which supplier. What it is, who it is for and what it does not do.',
      sections: [
        {
          h: 'StockAI in one sentence',
          p: [
            'StockAI is purchase-planning software: it takes your sales history, your stock and each supplier’s lead time, and hands you every morning a list of decisions: which product to order now, which to order soon, which is fine and which you have too much of.',
            'It is written StockAI, as one word, and many people search for it as “stock ai” or “stockai”. It is the same product. The name joins two ideas: stock, your inventory, and AI, the artificial intelligence that forecasts each product’s demand.',
          ],
        },
        {
          h: 'What it does, step by step',
          p: ['The full walkthrough is in [how StockAI works](/como-funciona); this is the summary:'],
          list: [
            'You upload your sales as CSV or Excel, or connect a Postgres or MySQL database. Before anything is trained, the tool checks the file and warns you about missing dates or histories that are too short.',
            'For each product, up to nine forecasting models compete on your own history, and the one whose mistakes cost least is kept. The detail is in [how it is calculated](/como-se-calcula).',
            'The forecast is crossed with your stock, what is on its way and the supplier’s lead time. Out come the reorder point, the stock signal (PEDIR YA, PEDIR PRONTO, OK or SOBRESTOCK) and the suggested quantity.',
            'You create the purchase order, review it, adjust it and send it yourself. When the goods arrive you log the reception, and StockAI learns how long each supplier really takes.',
          ],
        },
        {
          h: 'Who it is for',
          p: [
            'For anyone who buys goods from suppliers with a delivery time: distributors, wholesalers, shops, hardware stores, pharmacies, spare-parts houses and manufacturers. It fits best when there are hundreds or thousands of products, the team is small and purchases are decided today with an Excel sheet or the buyer’s memory. There are pages by industry under [industries](/industrias).',
            'StockAI is sold only as source code, with a one-time payment, to install on your own infrastructure. The detail is on [price](/precios).',
          ],
        },
        {
          h: 'What it is not',
          p: ['Better said up front, before you upload the first file:'],
          list: [
            'It is not an invoicing system, accounting software or a point of sale. You keep invoicing where you always did; StockAI reads that history.',
            'It does not buy by itself. It suggests what, how much and from whom; you review and send the order.',
            'It does not track lots or the expiry date of each unit.',
            'It does not invent forecasts without history: a product with fewer than 20 periods is marked SIN DATOS (no data) instead of getting a made-up number.',
            'It does not publish marketing hit-rate figures: the error you see is your own data’s.',
          ],
        },
        {
          h: 'How to start',
          p: [
            'The fastest way is to try it: you can open a trial account instantly, without signing up, or create an account and upload your file. If you already have sales history, your first what-to-order list can be ready in under an hour.',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 0, after: 2, label: 'The short version', lines: ['Your sales and stock go in; a list of what to order, how much and from which supplier comes out. You review and send the order.'] },
      ],
      faqTitle: 'Questions about StockAI',
      faq: [
        { q: 'What is StockAI?', a: 'It is web software for inventory purchasing, for distributors, wholesalers and shops. It reads your sales and stock and tells you what to order, how much and from which supplier.' },
        { q: 'Are “Stock AI” and “StockAI” the same?', a: 'Yes. The right name is StockAI, as one word; “stock ai” with a space is how many people type it when searching.' },
        { q: 'Does StockAI replace my ERP?', a: 'No, it complements it. Your ERP records what happened (sales, stock, purchases); StockAI reads that history and tells you what to buy. You keep invoicing and accounting where you always did.' },
        { q: 'How much does it cost?', a: 'StockAI is sold only as source code: $14,999 USD, in a one-time payment, to install on your own infrastructure. Write to us to buy it.' },
      ],
      ctaLead: 'Try StockAI with your own data and see your first purchase list, with the whole engine.',
      next: ['distributors', 'reorderPoint', 'forecast'],
    },

    distributors: {
      label: 'Inventory software for distributors',
      title: 'Inventory software for distributors: what it must solve and how to choose it.',
      intro: 'A distributor does not buy to consume: it buys to resell, with suppliers that take weeks and thousands of codes to watch. These are the questions good inventory software has to answer every morning, and how StockAI answers them.',
      metaTitle: 'Inventory software for distributors: what it must solve',
      metaDesc: 'What inventory software for distributors must solve: what to order, how much, from whom and what is in excess. How to choose, and how StockAI does it.',
      sections: [
        {
          h: 'A distributor’s real problem',
          p: [
            'Tracking stock is not the hard part: almost any system tells you how many units there are. The hard part is deciding the purchase: how much of each product to order, when and from which supplier, knowing the order takes time and demand changes. Ordering too little leaves the shelf empty and the sale is lost; ordering too much leaves money tied up and product ageing.',
            'That is why a distributor’s inventory is won or lost in purchasing, not in counting. Good software is not the one that stores more data, but the one that turns your sales and stock into decisions a buyer can review and approve in minutes.',
          ],
        },
        {
          h: 'What inventory software for distributors should do',
          p: [],
          list: [
            'Forecast each product’s demand from its own history, not from a general average. A stable product and one that sells in bursts need different models; see [demand forecasting for purchasing](/pronostico-de-demanda-para-compras).',
            'Use each supplier’s real lead time, not the one written on the card. StockAI learns it from your receptions: from the third one, the calculation uses the real average.',
            'Compute a reorder point per product and warn when it is crossed, instead of waiting for someone to notice. The explanation is in [how to calculate the reorder point](/como-calcular-el-punto-de-reorden).',
            'Size a safety cushion by how much sales vary and how late the supplier runs; see [safety stock](/stock-de-seguridad).',
            'Flag overstock, the other cost of buying by eye, and show how much money is tied up in what does not turn.',
            'Close the loop: purchase orders, partial or complete receptions, and tracking of what is on its way so the same thing is not ordered twice.',
            'Handle several warehouses and suggest a transfer before buying when one has spare what another lacks.',
          ],
        },
        {
          h: 'How StockAI solves it',
          p: [
            'Every morning you see a list ordered by urgency with four states: PEDIR YA (less than half a lead time of cover), PEDIR PRONTO (at or below the reorder point), OK and SOBRESTOCK (overstock). It is not a black box: the rule that decides the colour is published and you can work it out by hand; see [how it works](/como-funciona) and [how it is calculated](/como-se-calcula).',
            'The suggested quantity subtracts what is already on its way, from a supplier or a transfer between warehouses, and respects the minimum order. You adjust the order and send it yourself, by email or by WhatsApp. There are pages with examples by industry, such as [consumer goods, hardware, pharmacy, auto parts and retail](/industrias).',
          ],
        },
        {
          h: 'How to evaluate any option, this one included',
          p: ['Before choosing, ask any software vendor to show you this with your data, not a demo’s:'],
          list: [
            'Whether each supplier’s lead time enters the calculation and corrects itself from real deliveries.',
            'Whether forecast error is shown per product and on dates the model did not see.',
            'What it does with products with little history: a good system marks them instead of inventing a number.',
            'Whether it explains where each suggested quantity comes from.',
            'Whether you can take your data with you and delete it whenever you want.',
          ],
        },
        {
          h: 'What StockAI does not do',
          p: [
            'It does not invoice, keep accounts or work as a point of sale, and it does not buy for you. If your main need is one of those, you need another tool; StockAI works next to your ERP, not in its place. If today you run everything in Excel, the honest comparison is in [StockAI vs. Excel](/stockai-vs-excel).',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 0, after: 2, label: 'The short version', lines: ['A distributor’s inventory is won or lost in the purchase, not the count: how much of each product to order, when and from which supplier.'] },
      ],
      faqTitle: 'Frequently asked questions about inventory software for distributors',
      faq: [
        { q: 'What is the difference between controlling inventory and planning purchases?', a: 'Controlling is knowing how much there is; planning is deciding how much, when and from whom to order. StockAI focuses on the second: it takes the stock you already record in your system and crosses it with the forecast and the supplier’s lead time.' },
        { q: 'Do I need a data analyst to use it?', a: 'No. You upload your sales and see the what-to-order list; there are no models to configure and no code.' },
        { q: 'Does it work with several warehouses?', a: 'Yes. You can define routes between warehouses with their transit days, and StockAI suggests a transfer before buying when one warehouse has spare what another needs.' },
        { q: 'What data do I need to start?', a: 'A sales file with date, product and quantity sold, as CSV or Excel, with at least 20 periods of history per product. Current stock and each supplier’s lead time complete the calculation.' },
      ],
      ctaLead: 'Upload your history and see your first purchase list, with the whole engine.',
      next: ['reorderPoint', 'safetyStock', 'about'],
    },

    reorderPoint: {
      label: 'How to calculate the reorder point',
      title: 'How to calculate the reorder point, with a step-by-step example.',
      intro: 'The reorder point is the stock level at which you should place the order. Get it right and the order arrives just before the product runs out. This guide explains the formula, an example with numbers and how StockAI applies it.',
      metaTitle: 'How to calculate the reorder point: formula and example',
      metaDesc: 'What the reorder point is, the formula (demand during lead time plus safety stock), a worked example and how StockAI computes it for every product.',
      sections: [
        {
          h: 'What the reorder point is',
          p: [
            'It is the amount of stock at which you place a new order. While the supplier takes to deliver you keep selling, so the reorder point has to cover what you will sell during that wait and, on top, a cushion in case sales rise or the supplier is late.',
          ],
        },
        {
          h: 'The formula',
          p: [
            'Demand during lead time is average daily sales times the days the supplier takes. Safety stock is the cushion; how it is computed has its own guide: [safety stock](/stock-de-seguridad).',
          ],
        },
        {
          h: 'An example with numbers',
          p: [
            'The numbers in this example are invented, to illustrate the rule. A product sells 20 units a day on average. The supplier takes 9 days to deliver. The computed safety cushion is 30 units. Today there are 150 units and nothing on its way.',
            'Today’s cover is already below the reorder point, so it is time to order. The suggested quantity is what is missing to cover the lead time plus the cushion.',
          ],
        },
        {
          h: 'How StockAI applies it',
          p: [
            'StockAI computes each product’s reorder point from the demand it forecasts, not a flat historical average, and with the supplier’s real lead time: the one you type on the card at first and, from the third logged reception, the average of how long it really took. If you order at a fixed interval (say, a weekly order), that interval is also added to the time that has to be covered.',
            'The stock signal uses that point as its boundary. In the example above, 7.5 days of cover is more than half the lead time (4.5 days), so it is not PEDIR YA, but it is below 10.5 days: it is PEDIR PRONTO. PEDIR YA appears when cover drops below half a lead time. At the other end, SOBRESTOCK starts from the larger of three lead times of cover and twice the reorder point. These are the factory thresholds and can be configured per company, supplier or category. Everything is in [how it is calculated](/como-se-calcula).',
          ],
        },
        {
          h: 'Common mistakes when calculating it',
          p: [],
          list: [
            'Using the lead time the supplier promises instead of the one it actually meets. If it takes 12 days and says 7, you run out before the order lands.',
            'Using a whole-year sales average on a seasonal product: in high season the reorder point comes out short.',
            'Forgetting the cushion: with no safety stock, any better-than-usual week leaves you out of product.',
            'Ignoring what is already on its way and ordering the same thing twice.',
            'Using one rule for the whole catalogue: each product has its own variability.',
          ],
        },
        {
          h: 'Doing it by hand or letting the system do it',
          p: [
            'For ten stable products, a spreadsheet is enough. With hundreds or thousands of codes, a different lead time per supplier and changing demand, recalculating everything every week stops being realistic. That is the case StockAI is for, and you can compare both approaches in [StockAI vs. Excel](/stockai-vs-excel).',
          ],
        },
      ],
      callouts: [
        { kind: 'formula', section: 1, after: 0, label: 'Formula', lines: ['Reorder point = demand during lead time + safety stock'] },
        { kind: 'example', section: 2, after: 1, label: 'Worked example', lines: ['Demand during lead time: 20 × 9 = 180 units', 'Reorder point: 180 + 30 = 210 units', 'In days of cover: 210 ÷ 20 = 10.5 days', 'Today’s cover: 150 ÷ 20 = 7.5 days', 'Suggested quantity: 180 + 30 − 150 − 0 on its way = 60 units'] },
      ],
      faqTitle: 'Frequently asked questions about the reorder point',
      faq: [
        { q: 'What is the reorder point formula?', a: 'Demand during lead time plus safety stock. Demand during lead time is average daily sales times the days the supplier takes.' },
        { q: 'Is the reorder point the same as minimum stock?', a: 'Not necessarily. Minimum stock is usually a fixed number someone decided; the reorder point is computed from each product’s sales, lead time and variability, and changes when they change.' },
        { q: 'How often should it be recalculated?', a: 'Whenever demand or the supplier’s lead time changes. In StockAI it is recalculated when you upload new sales, or on a scheduled recalculation.' },
        { q: 'What does StockAI do when the reorder point is crossed?', a: 'The product moves to PEDIR PRONTO (or PEDIR YA if cover is below half a lead time) and shows up with a suggested quantity that subtracts what is already on its way.' },
      ],
      ctaLead: 'Let StockAI compute every product’s reorder point from your history.',
      next: ['safetyStock', 'forecast', 'distributors'],
    },

    safetyStock: {
      label: 'Safety stock',
      title: 'Safety stock: what it is and how it is calculated.',
      intro: 'Safety stock is the cushion of units that protects you when sales rise more than expected or the supplier is late. Without it, the reorder point only works in a perfect week. Here is the formula, an example with numbers and how StockAI decides its size.',
      metaTitle: 'Safety stock: what it is, formula and how to calculate it',
      metaDesc: 'What safety stock is, the formula with service level, a worked example, and how sales variability and supplier delays change it.',
      sections: [
        {
          h: 'What safety stock is',
          p: [
            'It is the extra inventory you keep above what you expect to sell during the lead time. It exists because both demand and the supplier are uncertain: you may sell more than planned, or the order may arrive late. It is half of the reorder point: see [how to calculate the reorder point](/como-calcular-el-punto-de-reorden).',
          ],
        },
        {
          h: 'The formula',
          p: [
            'Here z depends on the service level you want and σ measures the uncertainty during the lead time. With variable demand and a fixed lead time, only the variation in sales counts; if the supplier is also irregular, a second source of uncertainty is added and the two combine in quadrature.',
            'The service level is the probability of not running out during a replenishment cycle. Usual z values: 1.28 for 90%, 1.645 for 95%, 2.05 for 98% and 2.33 for 99%. StockAI starts at 95%.',
          ],
        },
        {
          h: 'An example with numbers',
          p: [
            'The numbers are invented, to illustrate the rule. A product sells 20 units a day with a deviation of 6 units a day, and the supplier takes 9 days. At a 95% service level:',
            'Now suppose the supplier is irregular: its lead time varies by about 2 days. The supplier’s lateness weighed more than the variation in sales, which is the reason to measure it instead of assuming it always delivers on time.',
          ],
        },
        {
          h: 'How StockAI decides it',
          p: [
            'StockAI computes the cushion per product, from the variability of its own sales, and adds the supplier’s irregularity from the deliveries you log. The service level starts at 95% and you can change it for the whole company, per supplier, per category or per product: a critical product can carry 98% and a secondary one 90%. The full calculation is described in [how it is calculated](/como-se-calcula).',
            'An honest warning: on intermittent-demand products (many zero periods, then a sudden large sale) a formula based on the normal distribution does not hold the nominal service level precisely. StockAI does not print a percentage it does not keep: it marks those rows with an intermittent-demand notice.',
          ],
        },
        {
          h: 'What moves the size of the cushion',
          p: [],
          list: [
            'More variability in sales, more cushion.',
            'A longer lead time, more cushion, though it grows with the square root of the lead time, not in a straight line.',
            'An irregular supplier, more cushion, and it can weigh more than demand itself.',
            'A higher service level, more cushion and more money tied up: each extra point costs more than the last.',
          ],
        },
        {
          h: 'The cost of overshooting and of falling short',
          p: [
            'A huge cushion looks safe, but it is money tied up and ageing product. An insufficient one produces stockouts and lost sales. That is why the service level should be set per product by how much running out hurts, and the overstock that builds up should be reviewed: [inventory software for distributors](/software-de-inventario-para-distribuidores).',
          ],
        },
      ],
      callouts: [
        { kind: 'formula', section: 1, after: 0, label: 'Formula', lines: ['Safety stock = z × σ', 'Fixed lead time: σ = daily standard deviation of sales × √(lead time in days)', 'Irregular supplier: σ = √(lead time × sales deviation² + daily sales² × lead-time deviation²)'] },
        { kind: 'example', section: 2, after: 1, label: 'Worked example', lines: ['Variable demand only: 1.645 × 6 × √9 = 1.645 × 6 × 3 ≈ 30 units', 'Supplier lateness (lead time varying by about 2 days): 1.645 × 20 × 2 ≈ 66', 'Both combined in quadrature: √(30² + 66²) ≈ 72 units'] },
      ],
      faqTitle: 'Frequently asked questions about safety stock',
      faq: [
        { q: 'What is the safety stock formula?', a: 'z × σ, where z comes from the chosen service level and σ is the uncertainty of demand during the lead time, combined with the supplier’s when the supplier is irregular.' },
        { q: 'What service level should I use?', a: 'It depends on what running out costs you. StockAI starts at 95% and lets you change it per company, supplier, category or product.' },
        { q: 'Are safety stock and minimum stock the same?', a: 'No. Minimum stock is usually a fixed number decided by hand; safety stock is computed from the real variability of sales and lead time.' },
        { q: 'What about products that hardly sell?', a: 'With many zero periods the normal formula does not hold the service level precisely, and StockAI says so on the row instead of showing a percentage that does not hold. With fewer than 20 periods of history, the product is marked SIN DATOS (no data).' },
      ],
      ctaLead: 'See each product’s cushion computed from your own history.',
      next: ['reorderPoint', 'forecast', 'about'],
    },

    forecast: {
      label: 'Demand forecasting for purchasing',
      title: 'Demand forecasting for purchasing: from past sales to the purchase order.',
      intro: 'Forecasting demand is not guessing the future: it is estimating how much of each product will sell during the time an order takes to arrive, with the margin of error in view. This guide explains how that forecast is used to buy better, and how StockAI does it.',
      metaTitle: 'Demand forecasting for purchasing: how to use it well',
      metaDesc: 'How to use demand forecasting to decide purchases: what is forecast, why one method is not enough, how error is measured and how StockAI does it.',
      sections: [
        {
          h: 'What forecasting is for in purchasing',
          p: [
            'The forecast is the input to almost every purchasing decision: the reorder point, the safety stock and the quantity to order all depend on how much is expected to sell. A flat average works while demand is stable; it fails with seasons, trends, promotions or sales that come in bursts.',
          ],
        },
        {
          h: 'Why one method is not enough',
          p: [
            'Not every product sells the same way. A stable one forecasts well with a simple model; a seasonal one needs a model that understands the calendar; an intermittent one, with many zero periods, needs models built for that. That is why StockAI classifies each product by how it sells (stable, seasonal, intermittent or volatile) and makes up to nine models compete, product by product: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, an LSTM neural network and a global model that learns from the whole catalogue.',
          ],
        },
        {
          h: 'How the winning model is chosen',
          p: [],
          list: [
            'Each model has a stretch from the end of your history hidden and is asked to forecast it, in several cuts. That measures it against data it did not see.',
            'The winner is chosen with a cost in which running out weighs three times more than having extra, because in purchasing a stockout and an excess do not cost the same.',
            'It has to beat two naive forecasts: repeating the last value and repeating the previous season. If it does not, a complex model is not justified.',
            'The winner is retrained on the full history and projects demand with a probable range around it, not a single number.',
          ],
        },
        {
          h: 'From forecast demand to the purchase',
          p: [
            'The forecast is not the end. It is crossed with stock, what is on its way and the supplier’s lead time to give the reorder point, the stock signal and the suggested quantity. That part is explained in [how to calculate the reorder point](/como-calcular-el-punto-de-reorden) and [safety stock](/stock-de-seguridad). The full walkthrough, with screenshots, is in [how StockAI works](/como-funciona).',
          ],
        },
        {
          h: 'The limits, said up front',
          p: [],
          list: [
            'A product with fewer than 20 periods of history is left out of the forecast and shown as SIN DATOS (no data): no number is invented for it.',
            'The range is not a guarantee: it is the band where sales will probably fall.',
            'It cannot guess what never happened. A new promotion or a new large customer is tried in the scenario simulator.',
            'The colour of the stock signal is decided by a published rule, not the model: AI forecasts and the rule decides.',
            'It does not buy for you: it suggests, and you send the order.',
          ],
        },
        {
          h: 'How to measure whether a forecast is good',
          p: [
            'On dates the model did not see, product by product, and against what actually sold afterwards. StockAI shows each product’s error, not one percentage for the whole catalogue, and does not publish a marketing accuracy figure: the one you see is your data’s.',
          ],
        },
      ],
      callouts: [
        { kind: 'takeaway', section: 1, after: 1, label: 'The short version', lines: ['No single method fits every product: each product gets its own competition between models, and the one that is least costly to be wrong with wins.'] },
      ],
      faqTitle: 'Frequently asked questions about demand forecasting',
      faq: [
        { q: 'What history do I need to forecast?', a: 'At least 20 periods per product, in a CSV or Excel file with date, product and quantity sold.' },
        { q: 'Which models does StockAI use?', a: 'Up to nine: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, an LSTM network and a global model. They compete per product and the one whose mistakes cost least wins.' },
        { q: 'What about a new promotion?', a: 'The model cannot know what never happened. It is tried in the scenario simulator, which shows what changes in the purchase.' },
        { q: 'How often is the forecast updated?', a: 'Whenever you upload new sales, or on a scheduled recalculation: weekly, daily, business days only, hourly or monthly.' },
      ],
      ctaLead: 'Upload your history and see each product’s forecast and error.',
      next: ['reorderPoint', 'safetyStock', 'distributors'],
    },
  },
}

export const GUIDES: Record<Lang, GuidesCopy> = { es, en }

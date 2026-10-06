/**
 * Copy for the landing's content pages, in both languages: the industry pages,
 * "StockAI vs. an Excel spreadsheet", "How it is calculated", "Integrations
 * and data", the changelog, and the two blocks added to the home page.
 *
 * Same rules as `landing.ts` (read its header): typed, so a missing translation
 * is a compile error; the English is a translation of the pitch, not of the
 * sentences; and NOTHING here may promise what the product does not do. No
 * customer, quote, logo, statistic, award or result percentage appears in this
 * file and none may be added. Every claim names a mechanism that exists, and
 * the file that backs it is noted beside it.
 *
 * Why a sibling file and not `landing.ts`: that one is 1,300 lines of home
 * page; these pages are a second body of prose with its own shape.
 *
 * The sample tables are INVENTED, on purpose and labelled as such. Each row was
 * worked out by hand with the published rule (`_calc_signal` in
 * backend/inventory/service.py): PEDIR YA under half the lead time of cover;
 * PEDIR PRONTO up to the reorder point (lead time + safety cushion, in days);
 * SOBRESTOCK from the larger of 3 x lead time and 2 x the reorder point; OK in
 * between. Change a number and recompute the row, or the example lies.
 */
import type { Lang } from './translations'

export interface Titled { title: string; desc: string }

export interface PageHead {
  /** Name in menus, breadcrumbs and "keep reading" lists. */
  label: string
  /** The H1 and its intro. */
  title: string
  intro: string
  /** What a search result shows (rendered by the server wrapper, Spanish). */
  metaTitle: string
  metaDesc: string
}

// ── Industry pages ───────────────────────────────────────────────────────────
export type IndustryKey = 'consumer' | 'hardware' | 'pharmacy' | 'autoparts' | 'retail'

/** One invented row of a sample morning. `signal` indexes the four signals
 *  (0 PEDIR YA, 1 PEDIR PRONTO, 2 OK, 3 SOBRESTOCK). */
export interface SampleRow {
  product: string
  daily: string
  stock: string
  lead: string
  reorder: string
  cover: string
  signal: 0 | 1 | 2 | 3
}

export interface Industry extends PageHead {
  /** One line on the hub card. */
  short: string
  problemsTitle: string
  problems: Titled[]
  howTitle: string
  how: Titled[]
  sampleTitle: string
  sampleLead: string
  sampleRows: SampleRow[]
  sampleReading: string
  limitsTitle: string
  limits: string[]
}

// ── Generic page blocks ──────────────────────────────────────────────────────
export interface ExcelRow { need: string; excel: string; stockai: string }

export interface ContentCopy {
  common: {
    industriesLabel: string
    breadcrumbIndustries: string
    related: string
    sampleBadge: string
    sampleHead: [string, string, string, string, string, string, string]
    sampleDisclaimer: string
    readingLabel: string
    ctaTrial: string
    ctaSignup: string
    ctaRule: string
    ctaMethod: string
    seeIndustry: string
    morePages: string
  }
  industries: {
    hub: PageHead & { lead: string; commonTitle: string; common: Titled[]; calendarNote: string }
    items: Record<IndustryKey, Industry>
  }
  excel: PageHead & {
    fairTitle: string; fair: Titled[]
    breaksTitle: string; breaks: Titled[]
    tableTitle: string; tableHead: [string, string, string]; rows: ExcelRow[]
    keepTitle: string; keep: string[]
    bridgeTitle: string; bridge: string
  }
  method: PageHead & {
    stepsTitle: string; steps: Titled[]
    leadTitle: string; leadBody: string[]
    accuracyTitle: string; accuracy: Titled[]
    limitsTitle: string; limits: string[]
    docsNote: string
  }
  integrations: PageHead & {
    nowTitle: string; now: (Titled & { tag: string })[]
    formatsTitle: string; formats: string[]
    soonTitle: string; soonLead: string; soon: Titled[]; soonNote: string
    customTitle: string; customBody: string
    devLink: string; docsLink: string
  }
  changelog: PageHead & {
    note: string
    entries: { date: string; title: string; items: string[] }[]
  }
  home: {
    tourTag: string; tourTitle: string; tourLead: string; tourMore: string
    audienceTag: string; audienceTitle: string; audienceLead: string
    forTitle: string; for: string[]
    notForTitle: string; notFor: string[]
    exploreTitle: string
    // Links from the home sections to the pages that deepen them.
    casesMore: string; compareMore: string; methodMore: string
  }
}

const es: ContentCopy = {
  common: {
    industriesLabel: 'Industrias',
    breadcrumbIndustries: 'Industrias',
    related: 'Sigue leyendo',
    sampleBadge: 'Datos de ejemplo',
    sampleHead: ['Producto', 'Venta por día', 'Existencias', 'Plazo del proveedor', 'Punto de reorden', 'Cobertura', 'Señal'],
    sampleDisclaimer: 'Datos de ejemplo, inventados para ilustrar la regla: no son de un cliente ni un resultado medido. La cobertura es existencias ÷ venta por día; el punto de reorden es el plazo más el colchón de seguridad, en días.',
    readingLabel: 'Cómo leer el ejemplo',
    ctaTrial: 'Probar sin registrarme',
    ctaSignup: 'Crear cuenta',
    ctaRule: 'Ver la regla completa',
    ctaMethod: 'Cómo se calcula',
    seeIndustry: 'Ver la página',
    morePages: 'Otras industrias',
  },
  industries: {
    hub: {
      label: 'Industrias',
      title: 'StockAI según el tipo de negocio que compra inventario.',
      intro: 'Cada rubro compra distinto: plazos largos o cortos, miles de códigos o cientos, ventas parejas o a saltos. Estas páginas cuentan el problema de cada uno y qué parte de StockAI lo resuelve, sin promesas de resultados.',
      metaTitle: 'StockAI por industria: inventario y compras para tu negocio',
      metaDesc: 'Cómo aplica StockAI a consumo masivo, ferretería, farmacia, autopartes y retail: señales de compra, plazos reales de proveedor, mínimos de compra y temporadas.',
      lead: 'Elige el rubro más parecido al tuyo. Si el tuyo no está, la lógica es la misma: ventas históricas, existencias y el plazo de cada proveedor.',
      commonTitle: 'Lo que es igual en todos',
      common: [
        { title: 'La misma regla a la vista', desc: 'Cobertura contra el plazo del proveedor y el punto de reorden: PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK. No cambia por industria; lo que cambia son tus números.' },
        { title: 'Plazos que se corrigen solos', desc: 'Cada recepción que registras enseña cuánto tarda de verdad el proveedor. Desde la tercera, StockAI planifica con ese plazo y no con el prometido.' },
        { title: 'Una orden armada por proveedor', desc: 'Con la cantidad sugerida, el mínimo de compra respetado y el motivo de cada línea. La revisas, la ajustas y la envías tú.' },
      ],
      calendarNote: 'Temporadas: StockAI trae un calendario comercial de diez países (Costa Rica, Colombia, México, Perú, Chile, Argentina, Ecuador, Guatemala, Panamá y República Dominicana: quincenas, aguinaldo o primas, Semana Santa, Día de la Madre, temporada escolar, Black Friday, Navidad). Los multiplicadores son un punto de partida que tú editas, no cifras ajustadas a tus ventas.',
    },
    items: {
      consumer: {
        label: 'Consumo masivo',
        short: 'Abarrotes, bebidas y productos de rotación diaria con miles de códigos.',
        metaTitle: 'StockAI para distribuidores de consumo masivo y abarrotes',
        metaDesc: 'Qué pedir hoy, cuánto y a quién en un distribuidor de abarrotes: plazo real por proveedor, quincenas y temporadas, y sobrestock marcado a tiempo.',
        title: 'Inventario para distribuidores de consumo masivo y abarrotes.',
        intro: 'Miles de códigos, rotación diaria y proveedores que entregan cuando pueden. El comprador revisa lo que recuerda; lo demás se quiebra o se acumula. Así aplica StockAI a ese día a día.',
        problemsTitle: 'El día a día de este rubro',
        problems: [
          { title: 'Demasiados códigos para revisarlos a ojo', desc: 'Con miles de referencias, el comprador mira las que siempre mira. Las demás se descubren cuando el cliente llama a reclamar el faltante.' },
          { title: 'La quincena mueve la venta', desc: 'La demanda sube cerca del día de pago y baja después. Pedir con el promedio del mes deja corto el pico y sobrado el valle.' },
          { title: 'El plazo prometido no es el que cumplen', desc: 'Un proveedor dice siete días y entrega en diez. Si planificas con el prometido, el quiebre llega antes que la mercadería.' },
        ],
        howTitle: 'Qué parte de StockAI aplica',
        how: [
          { title: 'Semáforo sobre todo el catálogo', desc: 'Cada producto queda en PEDIR YA, PEDIR PRONTO, OK o SOBRESTOCK, ordenado por urgencia. Revisas la lista de arriba hacia abajo, no producto por producto.' },
          { title: 'Quincenas y temporadas en el modelo', desc: 'Los días de pago y los feriados de tu país entran al pronóstico como variables, y las temporadas de cada producto se detectan solas.' },
          { title: 'Plazo real por proveedor', desc: 'Registras cada recepción, completa o parcial. A partir de la tercera, el punto de reorden usa los días que tardó de verdad, y el scorecard te muestra declarado contra real.' },
          { title: 'Mínimos y escalas de precio', desc: 'La cantidad sugerida respeta el mínimo de compra del proveedor y, si cargaste escalas por volumen, te dice cuánto falta para el siguiente escalón.' },
          { title: 'Sobrestock antes de liquidar', desc: 'Lo que cubre tres veces el plazo (o más) se marca SOBRESTOCK, con el dinero que tiene parado a la vista.' },
        ],
        sampleTitle: 'Una mañana de ejemplo',
        sampleLead: 'Cuatro productos de un distribuidor imaginario, tal como los ordena StockAI.',
        sampleRows: [
          { product: 'Aceite 900 ml (caja)', daily: '12', stock: '30', lead: '7 días', reorder: '10 días', cover: '2,5 días', signal: 0 },
          { product: 'Arroz 1 kg (saco)', daily: '18', stock: '110', lead: '7 días', reorder: '10 días', cover: '6,1 días', signal: 1 },
          { product: 'Atún en lata (caja)', daily: '40', stock: '700', lead: '10 días', reorder: '14 días', cover: '17,5 días', signal: 2 },
          { product: 'Galletas surtidas (caja)', daily: '9', stock: '600', lead: '8 días', reorder: '11 días', cover: '66,7 días', signal: 3 },
        ],
        sampleReading: 'El aceite alcanza para 2,5 días y el proveedor tarda 7: ya vas tarde, es PEDIR YA. El arroz aguanta 6,1 días, bajo su punto de reorden de 10: toca pedir ahora. Las galletas cubren más de dos meses con un proveedor que tarda 8 días: es dinero parado, SOBRESTOCK.',
        limitsTitle: 'Lo que conviene saber',
        limits: [
          'StockAI no factura ni lleva tu contabilidad: lee tus ventas y te dice qué comprar.',
          'No lleva lotes ni fechas de vencimiento por unidad. Sí puedes registrar la merma por vencimiento, con su costo.',
          'Los productos con menos de 20 periodos de historial quedan fuera del pronóstico y aparecen como SIN DATOS, sin cantidad inventada.',
        ],
      },
      hardware: {
        label: 'Ferretería y materiales',
        short: 'Catálogo largo, cola de productos que casi no se mueven y obra que no espera.',
        metaTitle: 'StockAI para ferreterías y materiales de construcción',
        metaDesc: 'Inventario y compras para ferreterías: productos de venta intermitente, plazos reales de proveedor, mínimos de compra y dinero parado en lo que no rota.',
        title: 'Inventario para ferreterías y distribuidores de materiales.',
        intro: 'Un catálogo larguísimo, pocos productos que se venden todos los días y una cola enorme que se vende de vez en cuando. Hay que tener el tornillo que el maestro pide hoy sin llenar la bodega de lo que nunca sale.',
        problemsTitle: 'El día a día de este rubro',
        problems: [
          { title: 'La cola larga de venta intermitente', desc: 'Un producto que se vende una vez cada dos semanas no se pronostica como el cemento. Con promedios simples, o pides de más o te quedas sin él justo cuando lo piden.' },
          { title: 'Faltante en el producto de obra', desc: 'Cuando falta el cable, el cemento o la tubería, el cliente no espera: compra todo el pedido en otro lado.' },
          { title: 'Dinero dormido en la bodega', desc: 'Lo que se compró por volumen o por un buen precio y no rotó ocupa espacio y capital, y casi nadie lo ve en una hoja.' },
        ],
        howTitle: 'Qué parte de StockAI aplica',
        how: [
          { title: 'Modelos para venta intermitente', desc: 'Los productos con muchos periodos sin una venta compiten con modelos hechos para ese patrón (Croston, ETS), no con los de venta pareja.' },
          { title: 'Capital parado, a la vista', desc: 'Una vista de dinero inmovilizado ordena los productos por cuánto tiempo llevan sin moverse y cuánto cuestan, el peor primero.' },
          { title: 'Mínimos y escalas por volumen', desc: 'La orden respeta el mínimo de compra y, con escalas de precio cargadas, compara en dinero si subir al siguiente escalón conviene o solo inmoviliza capital.' },
          { title: 'Clasificación ABC-XYZ', desc: 'Separa lo que importa por valor y por regularidad de venta, para que el colchón de seguridad vaya donde hace falta y no se reparta parejo.' },
          { title: 'Lista de materiales', desc: 'Si armas kits o productos terminados, la venta esperada se traduce en cuánto comprar de cada componente.' },
        ],
        sampleTitle: 'Una mañana de ejemplo',
        sampleLead: 'Cuatro productos de una ferretería imaginaria, tal como los ordena StockAI.',
        sampleRows: [
          { product: 'Cemento 50 kg (saco)', daily: '30', stock: '60', lead: '5 días', reorder: '7 días', cover: '2,0 días', signal: 0 },
          { product: 'Cable THHN 12 (rollo)', daily: '4', stock: '36', lead: '12 días', reorder: '16 días', cover: '9,0 días', signal: 1 },
          { product: 'Pintura látex (galón)', daily: '6', stock: '150', lead: '10 días', reorder: '14 días', cover: '25,0 días', signal: 2 },
          { product: 'Llave de paso 1/2 in', daily: '0,5', stock: '120', lead: '15 días', reorder: '20 días', cover: '240 días', signal: 3 },
        ],
        sampleReading: 'El cemento tiene 2 días de cobertura y su proveedor tarda 5: PEDIR YA. El cable está por debajo de su punto de reorden de 16 días: PEDIR PRONTO. La llave de paso se vende media unidad por día y hay 240 días de existencias: SOBRESTOCK, capital que no se mueve.',
        limitsTitle: 'Lo que conviene saber',
        limits: [
          'Un producto nuevo, sin historial, no tiene pronóstico: aparece como SIN DATOS hasta juntar al menos 20 periodos de venta.',
          'No predice lo que nunca pasó. Una obra grande que aún no cierras se prueba en el simulador de escenarios, no en el historial.',
          'StockAI no es tu punto de venta ni tu sistema de facturación.',
        ],
      },
      pharmacy: {
        label: 'Farmacia y salud',
        short: 'Faltante que se nota de inmediato y producto que no conviene dejar envejecer.',
        metaTitle: 'StockAI para farmacias y distribuidores de salud: compras',
        metaDesc: 'Compras para farmacias y distribuidoras de salud: pedir antes del quiebre, vigilar sobrestock y registrar la merma por vencimiento según cada droguería.',
        title: 'Inventario para farmacias y distribuidores de productos de salud.',
        intro: 'Quedarse sin el producto que el cliente vino a buscar se nota de inmediato, y llenar la bodega de lo que se vence es dinero perdido. StockAI no reemplaza tu sistema regulatorio: te dice cuándo y cuánto pedir.',
        problemsTitle: 'El día a día de este rubro',
        problems: [
          { title: 'El faltante tiene cara', desc: 'Un cliente que no encuentra su medicamento habitual va a otra farmacia. Los productos de rotación constante no pueden quebrarse.' },
          { title: 'Lo que se compra de más, envejece', desc: 'El exceso de inventario no solo ocupa espacio: en productos con fecha de vencimiento se vuelve pérdida directa.' },
          { title: 'Estacionalidad que cambia el pedido', desc: 'Las enfermedades de temporada, los cambios de clima y el regreso a clases mueven categorías enteras.' },
        ],
        howTitle: 'Qué parte de StockAI aplica',
        how: [
          { title: 'Alerta antes del quiebre', desc: 'Un resumen diario por correo (y por WhatsApp) lista los productos que entraron en riesgo, para pedir antes de que falten.' },
          { title: 'Plazo real de cada droguería', desc: 'Cada recepción registrada enseña cuántos días tarda de verdad cada proveedor. Un plazo corto y confiable permite cubrir con poco inventario.' },
          { title: 'Sobrestock marcado temprano', desc: 'Lo que cubre varias veces el plazo se marca SOBRESTOCK antes de que el producto envejezca, para dejar de reponerlo.' },
          { title: 'Merma por vencimiento registrada', desc: 'Cuando algo se vence, lo registras con su motivo y su costo, y el inventario baja por el mismo camino que cualquier otra salida.' },
          { title: 'Temporadas y calendario del país', desc: 'El pronóstico incluye los feriados y las fechas comerciales de tu país, y detecta la estacionalidad propia de cada producto.' },
        ],
        sampleTitle: 'Una mañana de ejemplo',
        sampleLead: 'Cuatro productos de una farmacia imaginaria, tal como los ordena StockAI.',
        sampleRows: [
          { product: 'Loratadina 10 mg (caja)', daily: '14', stock: '20', lead: '3 días', reorder: '5 días', cover: '1,4 días', signal: 0 },
          { product: 'Suero oral (sobre)', daily: '9', stock: '32', lead: '4 días', reorder: '6 días', cover: '3,6 días', signal: 1 },
          { product: 'Ibuprofeno 400 mg (caja)', daily: '20', stock: '160', lead: '3 días', reorder: '5 días', cover: '8,0 días', signal: 2 },
          { product: 'Crema antimicótica (tubo)', daily: '1,5', stock: '150', lead: '5 días', reorder: '7 días', cover: '100 días', signal: 3 },
        ],
        sampleReading: 'La loratadina cubre 1,4 días con un proveedor que tarda 3: PEDIR YA. La crema cubre 100 días y su proveedor entrega en 5: SOBRESTOCK, el producto que más conviene no volver a pedir hasta que baje.',
        limitsTitle: 'Lo que conviene saber',
        limits: [
          'StockAI no lleva lotes ni fechas de vencimiento por unidad, ni trazabilidad regulatoria: es una herramienta de compras, no un sistema de farmacia.',
          'No maneja recetas, controlados ni facturación.',
          'La merma por vencimiento se registra por producto y cantidad, con su costo; la fecha de cada lote vive en tu sistema.',
        ],
      },
      autoparts: {
        label: 'Repuestos y autopartes',
        short: 'Muchas referencias, piezas de venta esporádica y proveedores con plazos largos.',
        metaTitle: 'StockAI para repuestos y autopartes: inventario y compras',
        metaDesc: 'Inventario y compras para casas de repuestos: referencias de venta intermitente, plazos de importación aprendidos de cada recepción y alertas de sobrestock.',
        title: 'Inventario para casas de repuestos y autopartes.',
        intro: 'Decenas de miles de referencias, piezas que se venden dos o tres veces al año y proveedores que importan con plazos largos. Tener la pieza cuando el taller la pide, sin acumular la que nadie busca.',
        problemsTitle: 'El día a día de este rubro',
        problems: [
          { title: 'Plazos largos, errores caros', desc: 'Con un proveedor que tarda semanas, pedir tarde significa semanas sin la pieza. El plazo real importa más que en un rubro de reposición rápida.' },
          { title: 'La referencia que se vende dos veces al año', desc: 'El promedio diario de una pieza esporádica engaña: casi siempre es cero y de pronto no. Hay que pronosticarla con un modelo pensado para eso.' },
          { title: 'Piezas de modelos que ya no circulan', desc: 'Lo que se compró para un modelo de vehículo que dejó de venderse se queda años en la bodega.' },
        ],
        howTitle: 'Qué parte de StockAI aplica',
        how: [
          { title: 'Plazo de importación aprendido', desc: 'Empiezas con el plazo que declara el proveedor; con cada recepción registrada, StockAI aprende el real y lo usa desde la tercera entrega.' },
          { title: 'Venta intermitente, modelo propio', desc: 'Las referencias con muchos periodos sin venta compiten con Croston y ETS, y el modelo global aprende de cómo se comporta el resto de tu catálogo.' },
          { title: 'ABC-XYZ para el colchón', desc: 'El stock de seguridad va a las piezas que más pesan y menos se pueden predecir, en vez de repartirse igual a todo.' },
          { title: 'Varias bodegas y traslados', desc: 'Si tienes más de una bodega, StockAI propone mover antes de comprar, y solo si a la que presta le quedan al menos 30 días de cobertura.' },
          { title: 'Orden de menor costo total', desc: 'El optimizador del pedido incluye el flete fijo y los mínimos de compra para proponer la combinación de menor costo total.' },
        ],
        sampleTitle: 'Una mañana de ejemplo',
        sampleLead: 'Cuatro referencias de una casa de repuestos imaginaria, tal como las ordena StockAI.',
        sampleRows: [
          { product: 'Filtro de aceite (modelo común)', daily: '16', stock: '40', lead: '6 días', reorder: '9 días', cover: '2,5 días', signal: 0 },
          { product: 'Pastillas de freno delanteras', daily: '8', stock: '60', lead: '10 días', reorder: '14 días', cover: '7,5 días', signal: 1 },
          { product: 'Banda de distribución', daily: '3', stock: '90', lead: '14 días', reorder: '19 días', cover: '30,0 días', signal: 2 },
          { product: 'Rótula (modelo descontinuado)', daily: '0,2', stock: '40', lead: '21 días', reorder: '28 días', cover: '200 días', signal: 3 },
        ],
        sampleReading: 'El filtro cubre 2,5 días con un proveedor que tarda 6: PEDIR YA. Las pastillas, con 7,5 días de cobertura y un plazo de 10, ya están por debajo del punto de reorden de 14. La rótula tiene 200 días de existencias para una pieza que casi no se mueve: SOBRESTOCK.',
        limitsTitle: 'Lo que conviene saber',
        limits: [
          'StockAI no trae un catálogo de equivalencias ni compatibilidad por modelo de vehículo: trabaja con tus códigos.',
          'Una referencia con menos de 20 periodos de historial queda fuera del pronóstico y aparece como SIN DATOS.',
          'El rango del pronóstico no es una garantía: sirve para saber cuánto confiar en la cantidad sugerida.',
        ],
      },
      retail: {
        label: 'Retail multi-sucursal',
        short: 'Varias tiendas, una demanda distinta en cada una y mercadería que se puede mover entre ellas.',
        metaTitle: 'StockAI para retail con varias sucursales y bodegas',
        metaDesc: 'Inventario por tienda y consolidado, traslados entre sucursales antes de comprar, temporadas y promociones simuladas: StockAI para retail con varias bodegas.',
        title: 'Inventario para retail con varias sucursales.',
        intro: 'El mismo producto sobra en una tienda y falta en otra. Comprar para las tres sin mirar dónde está cada unidad es la forma más cara de resolverlo.',
        problemsTitle: 'El día a día de este rubro',
        problems: [
          { title: 'Cada tienda vende distinto', desc: 'La demanda de un producto cambia por ubicación, por barrio y por temporada. Un pedido parejo para todas deja corta a una y sobrada a otra.' },
          { title: 'Comprar lo que ya tienes en otra tienda', desc: 'Sin una vista consolidada, un faltante en una sucursal se resuelve con una compra nueva mientras otra tiene de sobra.' },
          { title: 'Campañas y temporadas', desc: 'Black Friday, Navidad, el regreso a clases: llegar sin inventario es perder la venta y llegar con demasiado, liquidar.' },
        ],
        howTitle: 'Qué parte de StockAI aplica',
        how: [
          { title: 'Vista por bodega y consolidada', desc: 'Cada tienda o bodega tiene su pestaña y su semáforo; también ves el total.' },
          { title: 'Traslado antes de compra', desc: 'Defines rutas con días de tránsito y costo. Si un producto falta en una tienda y sobra en otra, StockAI sugiere mover, y solo si a la que presta le quedan al menos 30 días de cobertura.' },
          { title: 'Promociones y escenarios', desc: 'En el simulador marcas una promoción, duplicas la demanda de una categoría o atrasas a un proveedor, y comparas contra el plan actual sin tocar nada real.' },
          { title: 'Calendario comercial', desc: 'Las fechas de diez países de Latinoamérica vienen cargadas (quincenas, aguinaldos y primas, Black Friday, Navidad, temporada escolar) y puedes editarlas.' },
          { title: 'Recálculo programado', desc: 'Cada lunes, a diario, en días hábiles o el primero del mes, sin que nadie lo lance a mano.' },
        ],
        sampleTitle: 'Una mañana de ejemplo',
        sampleLead: 'El mismo producto en tres tiendas de un retail imaginario. Plazo de 12 días y punto de reorden de 16 días en las tres.',
        sampleRows: [
          { product: 'Camiseta básica, Tienda Centro', daily: '10', stock: '25', lead: '12 días', reorder: '16 días', cover: '2,5 días', signal: 0 },
          { product: 'Camiseta básica, Tienda Sur', daily: '6', stock: '120', lead: '12 días', reorder: '16 días', cover: '20,0 días', signal: 2 },
          { product: 'Camiseta básica, Tienda Norte', daily: '4', stock: '260', lead: '12 días', reorder: '16 días', cover: '65,0 días', signal: 3 },
        ],
        sampleReading: 'Centro se queda sin producto en 2,5 días; Norte tiene para más de dos meses. Antes de comprar, StockAI propone un traslado desde Norte, que después de prestar conserva al menos 30 días de cobertura (120 unidades) y por eso puede ceder hasta 140. Sur está en OK y no presta: se quedaría por debajo de los 30 días.',
        limitsTitle: 'Lo que conviene saber',
        limits: [
          'StockAI no es un punto de venta ni un sistema de e-commerce: lee tus ventas por archivo, por base de datos o por API.',
          'Una promoción nueva no está en el historial: se prueba en el simulador, que es una estimación y no una garantía.',
          'La cantidad que se traslada es una sugerencia; la decisión y el movimiento los haces tú.',
        ],
      },
    },
  },
  excel: {
    label: 'StockAI vs. Excel',
    title: 'StockAI contra una planilla de Excel, sin adornos.',
    intro: 'Excel es la herramienta con la que casi todos empezamos a comprar, y para un catálogo chico sigue siendo una gran opción. Esta página dice qué hace bien, dónde deja de alcanzar y qué cambia si pasas a StockAI.',
    metaTitle: 'StockAI vs. Excel: compras e inventario, comparación honesta',
    metaDesc: 'Qué hace bien Excel para controlar inventario, dónde se rompe (plazos de proveedor, muchos productos, promociones, varias bodegas) y qué cambia con StockAI.',
    fairTitle: 'Lo que Excel hace bien',
    fair: [
      { title: 'Es flexible y lo conoces', desc: 'Armas la hoja como piensas, sin aprender nada nuevo y sin depender de nadie. Para pocos productos, es difícil de superar.' },
      { title: 'Cuesta poco y es tuyo', desc: 'Está en tu computadora, no hay que contratar nada, y el archivo es tuyo para siempre.' },
      { title: 'Sirve para anotar y para ver rápido', desc: 'Una tabla dinámica responde en un minuto cuánto vendiste de qué. Para mirar el pasado, es excelente.' },
      { title: 'Con 30 o 50 productos, alcanza', desc: 'Si el catálogo es chico y las ventas son estables, una buena hoja puede ser todo lo que necesitas.' },
    ],
    breaksTitle: 'Dónde deja de alcanzar',
    breaks: [
      { title: 'El quiebre depende del plazo, y la hoja no lo sabe', desc: 'Que un producto tenga stock hoy no dice si llega el próximo pedido antes de que se acabe. Para saberlo hay que cruzar, producto por producto, la venta esperada con los días que tarda ese proveedor. En una hoja se hace a mano, y a mano se hace con pocos.' },
      { title: 'Muchos productos', desc: 'Más allá de unas decenas, nadie revisa fila por fila cada mañana. Se revisan los de siempre, y el faltante aparece en el que nadie estaba mirando.' },
      { title: 'Promociones y temporadas', desc: 'Probar qué pasa si duplicas la demanda en diciembre son fórmulas nuevas cada vez. Y la quincena, la Semana Santa o el Día de la Madre se recuerdan de memoria, o no se recuerdan.' },
      { title: 'Varias bodegas', desc: 'Una hoja por bodega, o una hoja enorme, y nadie ve en un solo lugar que a una le sobra lo que a otra le falta.' },
      { title: 'El plazo del proveedor cambia y nadie lo anota', desc: 'La columna de plazo se escribe una vez y queda así. Medirlo en cada entrega exige una disciplina que casi nunca sobrevive a un mes ocupado.' },
      { title: 'La hoja depende de una persona', desc: 'Las fórmulas, las excepciones y los criterios viven en la cabeza de quien armó el archivo. Si se va, queda la hoja sin el criterio.' },
    ],
    tableTitle: 'Lado a lado',
    tableHead: ['Lo que necesitas', 'Planilla de Excel', 'StockAI'],
    rows: [
      { need: 'Anotar y mirar el pasado', excel: 'Muy bien', stockai: 'Sube tu archivo; ves ventas y pronóstico por producto' },
      { need: 'Saber qué pedir hoy', excel: 'Fila por fila, con fórmulas propias', stockai: 'Lista ordenada por urgencia: PEDIR YA, PEDIR PRONTO, OK, SOBRESTOCK' },
      { need: 'Cuánto pedir', excel: 'A criterio del comprador', stockai: 'Cantidad sugerida: descuenta lo que viene en camino y respeta el mínimo de compra' },
      { need: 'Plazo real de cada proveedor', excel: 'Una columna que escribes tú', stockai: 'Medido en cada recepción; se usa desde la tercera' },
      { need: 'Muchos productos a la vez', excel: 'Decenas con atención', stockai: 'Todo el catálogo, todos los días' },
      { need: 'Productos que se venden de vez en cuando', excel: 'Un promedio que engaña', stockai: 'Modelos propios para venta intermitente' },
      { need: 'Probar una promoción o un atraso', excel: 'Fórmulas nuevas cada vez', stockai: 'Simulador de escenarios, contra el plan actual' },
      { need: 'Varias bodegas', excel: 'Una hoja por bodega', stockai: 'Vista por bodega y consolidada; traslado antes de compra' },
      { need: 'Avisarte antes del quiebre', excel: 'No lo hace', stockai: 'Resumen diario por correo; por WhatsApp' },
      { need: 'Libertad total para armar lo que quieras', excel: 'Total', stockai: 'Acotada a compras e inventario, a propósito' },
    ],
    keepTitle: 'Lo que no cambia',
    keep: [
      'Sigues usando Excel para lo que Excel hace bien: StockAI acepta tu archivo tal como sale de tu sistema, en CSV o Excel.',
      'No necesitas migrar nada para probar: subes el archivo de ventas y ves la primera lista de qué pedir.',
      'Los reportes de StockAI se exportan a Excel y PDF, para compartirlos con quien trabaja en hojas.',
    ],
    bridgeTitle: 'Cuándo conviene quedarse en Excel',
    bridge: 'Si tienes pocos productos, ventas estables, un proveedor casi único y tiempo para revisar la hoja cada mañana, no necesitas StockAI todavía. StockAI está para cuando la hoja empieza a quedarse corta.',
  },
  method: {
    label: 'Cómo se calcula',
    title: 'Cómo se calcula lo que StockAI te dice que pidas.',
    intro: 'Sin cajas negras: los modelos compiten sobre tu propio historial, gana el que menos te cuesta equivocarse, el plazo de tu proveedor se aprende de tus recepciones y la regla del semáforo se puede hacer a mano. Esta página lo cuenta en lenguaje llano.',
    metaTitle: 'StockAI: cómo calcula qué pedir, plazos reales y precisión',
    metaDesc: 'Cómo pronostica StockAI: modelos que compiten sobre tu historial, el mejor por producto, plazos aprendidos de tus recepciones y el error siempre a la vista.',
    stepsTitle: 'De tu archivo a la cantidad sugerida',
    steps: [
      { title: '1. Se revisa tu archivo', desc: 'Antes de entrenar se mira cada producto: fechas que faltan, valores atípicos, historial insuficiente. Si el archivo daría un pronóstico equivocado, no corre hasta que elijas cómo corregirlo.' },
      { title: '2. Cada producto se clasifica por cómo se vende', desc: 'Estable, estacional, intermitente o volátil. Con eso se elige qué modelos compiten: no se entrena uno que tú no hayas activado, ni uno que no encaje con el patrón.' },
      { title: '3. Los modelos compiten sobre tu historial', desc: 'Hasta nueve modelos: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, una red neuronal LSTM y un modelo global que aprende de todo tu catálogo. A cada uno se le esconde un tramo del final de tu historial y se le pide pronosticarlo, en varios cortes.' },
      { title: '4. Gana el que menos cuesta equivocarse', desc: 'El ganador se elige con un costo en el que quedarse sin producto pesa tres veces más que sobrar. Y tiene que superar a dos pronósticos ingenuos (repetir el último valor y repetir la temporada anterior).' },
      { title: '5. El ganador se vuelve a entrenar con todo', desc: 'Se reentrena con el historial completo, incluidas tus últimas semanas, y proyecta la demanda con un rango probable alrededor.' },
      { title: '6. Se cruza con tus existencias y tu proveedor', desc: 'La demanda pronosticada, las existencias, lo que viene en camino y el plazo del proveedor dan la cobertura, el punto de reorden, la señal y la cantidad sugerida.' },
    ],
    leadTitle: 'Cómo se aprende el plazo de cada proveedor',
    leadBody: [
      'Al principio usas el plazo que escribes en la ficha del proveedor. Cada vez que registras una recepción, completa o parcial, StockAI guarda cuántos días pasaron de verdad entre la orden y la entrega.',
      'A partir de la tercera recepción de ese proveedor, el punto de reorden usa el promedio real en lugar del declarado, y la pantalla te muestra cuál de los dos está usando. El colchón de seguridad también crece cuando el proveedor es irregular en sus entregas.',
    ],
    accuracyTitle: 'Cómo se muestra la precisión',
    accuracy: [
      { title: 'El error se mide sobre fechas que el modelo no vio', desc: 'Lo que ves en la pantalla de pronóstico es qué tanto se equivocó cada producto en los tramos escondidos, no un número calculado sobre lo que ya había aprendido.' },
      { title: 'Cada producto, por separado', desc: 'No hay un porcentaje único para todo el catálogo: cada producto trae su propio error y su propio rango, para saber cuánto confiar en su cantidad sugerida.' },
      { title: 'Contra lo que de verdad se vendió', desc: 'Cuando subes ventas nuevas, puedes comparar el pronóstico que habías publicado con lo que se vendió después.' },
      { title: 'Sin un porcentaje de precisión de marketing', desc: 'No publicamos una cifra de aciertos en este sitio: dependería de tus datos, y la que ves es la tuya.' },
    ],
    limitsTitle: 'Los límites, dichos de frente',
    limits: [
      'Un producto con menos de 20 periodos de historial queda fuera del pronóstico y aparece como SIN DATOS: no se le inventa un número.',
      'El rango del pronóstico no es una garantía: es la franja donde probablemente caerá la venta.',
      'No adivina lo que nunca pasó: una promoción nueva o un cliente grande nuevo se prueban en el simulador de escenarios.',
      'Lo que decide el color del semáforo es una regla publicada, no el modelo: la IA pronostica, la regla decide.',
      'No compra por ti: sugiere qué, cuánto y a quién; la orden la revisas, la ajustas y la envías tú.',
    ],
    docsNote: 'Para el detalle técnico completo (métricas, validación, reglas de enrutamiento) hay un manual técnico y un manual de usuario en PDF.',
  },
  integrations: {
    label: 'Integraciones y datos',
    title: 'Cómo entran tus datos a StockAI y cómo salen tus decisiones.',
    intro: 'Esta es la lista de lo que existe hoy, sin promesas: carga por archivo, conexión a tu base de datos, API pública, servidor MCP de solo lectura, webhooks y WhatsApp. Al final, lo que aún no existe, marcado como tal.',
    metaTitle: 'StockAI integraciones: CSV, Excel, SQL, API, MCP y WhatsApp',
    metaDesc: 'Lo que StockAI conecta hoy: CSV o Excel, Postgres y MySQL, API pública con llaves, servidor MCP de solo lectura, webhooks y WhatsApp. Y lo que aún no existe.',
    nowTitle: 'Lo que existe hoy',
    now: [
      { tag: 'Archivos', title: 'CSV y Excel', desc: 'Exportas de tu sistema y subes el archivo tal como sale. StockAI reconoce cuál columna es la fecha, cuál el producto y cuál la cantidad. También hay importación masiva de proveedores y de órdenes de compra desde CSV o Excel.' },
      { tag: 'Base de datos', title: 'Conexión directa a tu base de datos', desc: 'Conectas StockAI a tu base de datos Postgres o MySQL y traes las ventas con una consulta, sin archivos de por medio. Las credenciales se guardan cifradas.' },
      { tag: 'API', title: 'API pública con llaves', desc: 'Tu sistema puede subir ventas y existencias y llevarse el semáforo sin que nadie abra StockAI. Las llaves tienen alcance de lectura o de escritura, cada llamada se mide, y la documentación trae ejemplos reales.' },
      { tag: 'Asistentes de IA', title: 'Servidor MCP de solo lectura', desc: 'Un cliente de IA compatible con MCP puede consultar tu inventario con la misma llave de API y el mismo límite. Son cinco herramientas y todas solo leen: ninguna escribe ni aprueba nada.' },
      { tag: 'Eventos', title: 'Webhooks', desc: 'StockAI avisa a una dirección HTTPS tuya cuando un pronóstico termina o falla (job.completed y job.failed), para que tu sistema reaccione sin consultar cada rato.' },
      { tag: 'Mensajería', title: 'WhatsApp y correo', desc: 'El resumen diario de productos en riesgo y las órdenes a proveedores salen por correo, y por WhatsApp. Con WhatsApp, cada persona vincula su número y puede preguntarle al asistente cómo va su inventario; el asistente solo consulta.' },
      { tag: 'Reportes', title: 'Excel y PDF', desc: 'Las órdenes de compra se exportan en CSV o PDF, y los reportes en Excel y PDF, para tu sistema o para quien trabaja en hojas.' },
    ],
    formatsTitle: 'Datos que StockAI necesita',
    formats: [
      'Historial de ventas: fecha, código de producto y cantidad vendida como mínimo.',
      'Existencias actuales, para que el semáforo tenga contra qué comparar.',
      'Plazo de entrega aproximado de cada proveedor: aproximado basta, se corrige solo.',
      'Costo y mínimo de compra, si quieres que la orden los respete y que el dinero parado se vea.',
    ],
    soonTitle: 'Próximamente',
    soonLead: 'Estas dos cosas están en conversación y no tienen fecha. Se listan para ser transparentes, no como promesa.',
    soon: [
      { title: 'Portal para proveedores', desc: 'Un acceso para que tus proveedores confirmen órdenes y fechas de entrega sin escribirte. No existe todavía.' },
      { title: 'Conectores sin código', desc: 'Conexiones listas con herramientas de automatización, para quien no quiere programar sobre la API. No existe todavía.' },
    ],
    soonNote: 'Hoy no hay conectores listos con ERPs ni con sistemas contables. Si tu operación necesita una integración a medida, la armamos con nuestro equipo técnico, caso por caso.',
    customTitle: '¿Necesitas una integración a medida?',
    customBody: 'Cuéntanos qué sistema usas y qué datos quieres mover. Respondemos en menos de 24 horas.',
    devLink: 'Documentación de la API',
    docsLink: 'Centro de ayuda',
  },
  changelog: {
    label: 'Novedades',
    title: 'Novedades: lo que cambió en StockAI.',
    intro: 'Un resumen de lo que se terminó en las últimas semanas, con la fecha en que quedó en el código. Solo lo que existe; sin anuncios de lo que viene.',
    metaTitle: 'StockAI novedades: mejoras y cambios recientes del producto',
    metaDesc: 'Registro fechado de las mejoras recientes de StockAI: pronóstico, inventario, API, MCP, cuentas de prueba, aplicación móvil y centro de ayuda.',
    note: 'Las fechas son las del cambio en el código; puede que una mejora llegue a tu cuenta algún día después.',
    entries: [
      {
        date: '2026-10-05',
        title: 'Un panel más calmado y listas más ágiles',
        items: [
          'El panel de compras se rediseñó con un tono neutro: las alertas pasaron a la campana y a marcas en cada fila, y un pliegue de «más análisis» guarda lo secundario.',
          'Inventario y proveedores cargan por páginas desde el servidor, para catálogos grandes.',
          'Seguimiento automático de la precisión del pronóstico.',
          'Avisos silenciosos para las llegadas pendientes y los datos que le faltan a un proveedor.',
          'El logo preside la barra lateral.',
        ],
      },
      {
        date: '2026-10-04',
        title: 'Pronóstico: comparar, importar, entender el avance',
        items: [
          'Comparar el pronóstico publicado con las ventas que subiste después.',
          'La comparación de sesiones dibuja todas las actualizaciones sobre un mismo eje de tiempo.',
          'Importación masiva de proveedores y de órdenes de compra desde CSV o Excel.',
          'El avance del entrenamiento se mide por producto, ponderado por costo, con un indicador en toda la aplicación y una pantalla que se puede reanudar.',
          'Dictado por micrófono en el asistente y selector de país para los teléfonos.',
          'Los errores del servidor se traducen en un solo lugar, en español e inglés.',
          'Guía de carga con ayudas visuales dentro de la aplicación.',
        ],
      },
      {
        date: '2026-10-02',
        title: 'Centro de ayuda, documentos legales y API mejor documentada',
        items: [
          'Centro de ayuda en /docs, en español e inglés, con búsqueda.',
          'Documentos legales: condiciones de uso, privacidad, cookies, aviso legal, uso aceptable, procesamiento de datos, uso de IA, divulgación responsable, accesibilidad, condiciones comerciales y licencia.',
          'La documentación de la API trae ejemplos de respuesta reales y tipos de campo para 91 endpoints de lectura.',
          'Un menú lateral de siete entradas, con Configuración como centro de lo demás.',
        ],
      },
      {
        date: '2026-10-01',
        title: 'Cuentas de prueba, API completa y aplicación móvil',
        items: [
          'Cuentas de prueba de 24 horas desde la página de inicio, con datos de ejemplo y sin registrarse.',
          'Cada acción de la aplicación se puede llamar con una llave de API, con alcance de lectura o escritura, medida y documentada.',
          'Un asistente único con las cifras de tu cuenta, en la aplicación y por WhatsApp.',
          'Cortes del semáforo configurables: cuántos plazos de cobertura marcan PEDIR YA y SOBRESTOCK, por empresa, proveedor o categoría.',
          'StockAI se instala como aplicación, y las pantallas del día a día se adaptan al celular.',
        ],
      },
      {
        date: '2026-09-30',
        title: 'Nuevo nombre, servidor MCP y trazabilidad de recomendaciones',
        items: [
          'La plataforma pasó a llamarse StockAI.',
          'Servidor MCP de solo lectura, con cinco herramientas, para asistentes de IA.',
          'Reversión de órdenes de compra y de recepciones registradas por error.',
          'Registro de recomendaciones: qué sugirió StockAI y qué se hizo.',
          'Costa Rica es el país por defecto del calendario de feriados.',
          'Aviso por producto cuando el colchón de seguridad no alcanza el nivel de servicio elegido.',
        ],
      },
    ],
  },
  home: {
    tourTag: 'La aplicación por dentro',
    tourTitle: 'Tres pantallas que abres cada mañana.',
    tourLead: 'Capturas reales de StockAI con datos de ejemplo dentro. No son maquetas.',
    tourMore: 'Ver todas las pantallas',
    audienceTag: 'Para quién es',
    audienceTitle: 'Para quién es StockAI, y para quién no.',
    audienceLead: 'Mejor saberlo antes de subir el primer archivo.',
    forTitle: 'Es para ti si',
    for: [
      'Compras mercadería a proveedores con un plazo de entrega: eres distribuidor, mayorista, comercio o fabricante.',
      'Tienes cientos o miles de productos y hoy decides las compras con una hoja de Excel o con la memoria del comprador.',
      'Tu equipo es pequeño y no tiene un analista de datos.',
      'Tienes un historial de ventas en CSV o Excel, con al menos 20 periodos por producto.',
    ],
    notForTitle: 'No es para ti si',
    notFor: [
      'Buscas un sistema de facturación, de contabilidad o un punto de venta: StockAI no factura.',
      'Necesitas trazabilidad por lote o por fecha de vencimiento de cada unidad: no la lleva.',
      'Quieres que la herramienta compre sola: sugiere, y la orden la envías tú.',
      'Tu negocio es de servicios, sin inventario que reponer.',
      'Lanzas productos nuevos y quieres un pronóstico sin historial: sin datos suficientes no se inventa un número.',
    ],
    exploreTitle: 'Y para profundizar',
    casesMore: 'Una página por industria, con una mañana de ejemplo',
    compareMore: 'La comparación completa, con lo que Excel hace bien',
    methodMore: 'Cómo se calcula, en lenguaje llano',
  },
}

const en: ContentCopy = {
  common: {
    industriesLabel: 'Industries',
    breadcrumbIndustries: 'Industries',
    related: 'Keep reading',
    sampleBadge: 'Sample data',
    sampleHead: ['Product', 'Sales per day', 'On hand', 'Supplier lead time', 'Reorder point', 'Cover', 'Signal'],
    sampleDisclaimer: 'Sample data, invented to illustrate the rule: not from a customer and not a measured result. Cover is on-hand ÷ sales per day; the reorder point is the lead time plus the safety cushion, in days.',
    readingLabel: 'How to read the example',
    ctaTrial: 'Try without signing up',
    ctaSignup: 'Create an account',
    ctaRule: 'See the full rule',
    ctaMethod: 'How it is calculated',
    seeIndustry: 'See the page',
    morePages: 'Other industries',
  },
  industries: {
    hub: {
      label: 'Industries',
      title: 'StockAI by the kind of business that buys inventory.',
      intro: 'Every trade buys differently: long or short lead times, thousands of codes or hundreds, steady sales or bursts. These pages describe each one’s problem and which part of StockAI addresses it, with no promised results.',
      metaTitle: 'StockAI by industry: consumer goods, hardware, pharmacy, auto parts and retail',
      metaDesc: 'How StockAI applies to a consumer-goods distributor, a hardware store, a pharmacy, an auto-parts business and a multi-store retailer: purchase signals, real lead times, minimums and seasons.',
      lead: 'Pick the trade closest to yours. If yours is not here the logic is the same: sales history, stock on hand and each supplier’s lead time.',
      commonTitle: 'What is the same in all of them',
      common: [
        { title: 'The same rule, in plain sight', desc: 'Cover against the supplier’s lead time and the reorder point: PEDIR YA, PEDIR PRONTO, OK or SOBRESTOCK. It does not change by industry; your numbers do.' },
        { title: 'Lead times that correct themselves', desc: 'Every reception you log teaches how long the supplier really takes. From the third one, StockAI plans with that lead time, not the promised one.' },
        { title: 'One order, built per supplier', desc: 'With the suggested quantity, the minimum order respected and the reason for each line. You review it, adjust it and send it.' },
      ],
      calendarNote: 'Seasons: StockAI ships a commercial calendar for ten countries (Costa Rica, Colombia, Mexico, Peru, Chile, Argentina, Ecuador, Guatemala, Panama and the Dominican Republic: paydays, year-end bonus, Holy Week, Mother’s Day, back to school, Black Friday, Christmas). The multipliers are a starting point you edit, not figures fitted to your sales.',
    },
    items: {
      consumer: {
        label: 'Consumer goods',
        short: 'Groceries, beverages and fast-moving products with thousands of codes.',
        metaTitle: 'Inventory software for consumer-goods and grocery distributors | StockAI',
        metaDesc: 'What to order today, how much and from whom, for grocery and consumer-goods distributors: real lead times per supplier, paydays and seasons, and overstock flagged early.',
        title: 'Inventory for consumer-goods and grocery distributors.',
        intro: 'Thousands of codes, daily turnover and suppliers who deliver when they can. The buyer reviews what they remember; the rest runs out or piles up. Here is how StockAI applies to that day.',
        problemsTitle: 'A day in this trade',
        problems: [
          { title: 'Too many codes to check by eye', desc: 'With thousands of references the buyer looks at the ones they always look at. The rest are found when the customer calls about the shortage.' },
          { title: 'Payday moves sales', desc: 'Demand rises near payday and falls after. Ordering from the monthly average leaves the peak short and the trough overstocked.' },
          { title: 'The promised lead time is not the one delivered', desc: 'A supplier says seven days and delivers in ten. Plan with the promised one and the stockout arrives before the goods do.' },
        ],
        howTitle: 'Which part of StockAI applies',
        how: [
          { title: 'A signal across the whole catalogue', desc: 'Every product lands on PEDIR YA, PEDIR PRONTO, OK or SOBRESTOCK, sorted by urgency. You read the list top to bottom, not product by product.' },
          { title: 'Paydays and seasons in the model', desc: 'Paydays and the holidays of your country enter the forecast as variables, and each product’s seasons are detected on their own.' },
          { title: 'Real lead time per supplier', desc: 'You log each reception, full or partial. From the third one, the reorder point uses how long it really took, and the scorecard shows declared against real.' },
          { title: 'Minimums and price breaks', desc: 'The suggested quantity respects the supplier’s minimum order and, if you loaded volume breaks, tells you how far the next step is.' },
          { title: 'Overstock before liquidation', desc: 'What covers three lead times (or more) is flagged SOBRESTOCK, with the money it ties up in view.' },
        ],
        sampleTitle: 'A sample morning',
        sampleLead: 'Four products of an imaginary distributor, as StockAI orders them.',
        sampleRows: [
          { product: 'Cooking oil 900 ml (case)', daily: '12', stock: '30', lead: '7 days', reorder: '10 days', cover: '2.5 days', signal: 0 },
          { product: 'Rice 1 kg (sack)', daily: '18', stock: '110', lead: '7 days', reorder: '10 days', cover: '6.1 days', signal: 1 },
          { product: 'Canned tuna (case)', daily: '40', stock: '700', lead: '10 days', reorder: '14 days', cover: '17.5 days', signal: 2 },
          { product: 'Assorted cookies (case)', daily: '9', stock: '600', lead: '8 days', reorder: '11 days', cover: '66.7 days', signal: 3 },
        ],
        sampleReading: 'The oil lasts 2.5 days and the supplier takes 7: you are already late, PEDIR YA. The rice holds 6.1 days, under its 10-day reorder point: time to order. The cookies cover over two months with a supplier who takes 8 days: money sitting still, SOBRESTOCK.',
        limitsTitle: 'Worth knowing',
        limits: [
          'StockAI does not invoice or keep your books: it reads your sales and tells you what to buy.',
          'It does not track lots or expiry dates per unit. You can log shrinkage from expiry, with its cost.',
          'Products with fewer than 20 periods of history are left out of the forecast and show as SIN DATOS, with no made-up quantity.',
        ],
      },
      hardware: {
        label: 'Hardware and materials',
        short: 'A long catalogue, a tail of products that barely move and a job site that will not wait.',
        metaTitle: 'Inventory software for hardware stores and building-materials distributors | StockAI',
        metaDesc: 'Inventory and purchasing for hardware stores and materials distributors: intermittent-demand products, real supplier lead times, minimum orders and money tied up in what does not turn over.',
        title: 'Inventory for hardware stores and materials distributors.',
        intro: 'An enormous catalogue, a few products that sell every day and a huge tail that sells now and then. You need the screw the contractor asks for today without filling the warehouse with what never leaves.',
        problemsTitle: 'A day in this trade',
        problems: [
          { title: 'The long tail of intermittent sales', desc: 'A product that sells once every two weeks is not forecast like cement. With simple averages you either overbuy or run out just when someone asks for it.' },
          { title: 'Shortages on job-site products', desc: 'When the cable, the cement or the pipe runs out the customer does not wait: they buy the whole order elsewhere.' },
          { title: 'Money asleep in the warehouse', desc: 'What was bought in volume or at a good price and did not turn over takes up space and capital, and almost nobody sees it in a spreadsheet.' },
        ],
        howTitle: 'Which part of StockAI applies',
        how: [
          { title: 'Models for intermittent sales', desc: 'Products with many periods without a sale compete with models built for that pattern (Croston, ETS), not the ones for steady sales.' },
          { title: 'Tied-up capital, in view', desc: 'A view of immobilised money ranks products by how long they have not moved and what they cost, worst first.' },
          { title: 'Minimums and volume breaks', desc: 'The order respects the minimum and, with price breaks loaded, compares in money whether stepping up pays off or only ties up capital.' },
          { title: 'ABC-XYZ classification', desc: 'Separates what matters by value and by regularity of sales, so the safety cushion goes where it is needed instead of evenly everywhere.' },
          { title: 'Bill of materials', desc: 'If you assemble kits or finished goods, expected sales turn into how much of each component to buy.' },
        ],
        sampleTitle: 'A sample morning',
        sampleLead: 'Four products of an imaginary hardware store, as StockAI orders them.',
        sampleRows: [
          { product: 'Cement 50 kg (sack)', daily: '30', stock: '60', lead: '5 days', reorder: '7 days', cover: '2.0 days', signal: 0 },
          { product: 'THHN 12 cable (roll)', daily: '4', stock: '36', lead: '12 days', reorder: '16 days', cover: '9.0 days', signal: 1 },
          { product: 'Latex paint (gallon)', daily: '6', stock: '150', lead: '10 days', reorder: '14 days', cover: '25.0 days', signal: 2 },
          { product: 'Stop valve 1/2 in', daily: '0.5', stock: '120', lead: '15 days', reorder: '20 days', cover: '240 days', signal: 3 },
        ],
        sampleReading: 'Cement has 2 days of cover and its supplier takes 5: PEDIR YA. The cable is under its 16-day reorder point: PEDIR PRONTO. The stop valve sells half a unit a day and there are 240 days of stock: SOBRESTOCK, capital that does not move.',
        limitsTitle: 'Worth knowing',
        limits: [
          'A new product with no history has no forecast: it shows as SIN DATOS until it gathers at least 20 periods of sales.',
          'It does not predict what never happened. A big job you have not closed yet is tested in the scenario simulator, not in the history.',
          'StockAI is not your point of sale or your invoicing system.',
        ],
      },
      pharmacy: {
        label: 'Pharmacy and health',
        short: 'A shortage that shows at once, and product you should not let age.',
        metaTitle: 'Inventory software for pharmacies and health-product distributors | StockAI',
        metaDesc: 'Purchasing and inventory for pharmacies and health-product distributors: order before the stockout, watch overstock and log shrinkage from expiry, with each wholesaler’s real lead time.',
        title: 'Inventory for pharmacies and health-product distributors.',
        intro: 'Running out of what the customer came for shows immediately, and filling the stockroom with what expires is lost money. StockAI does not replace your regulatory system: it tells you when and how much to order.',
        problemsTitle: 'A day in this trade',
        problems: [
          { title: 'A shortage has a face', desc: 'A customer who cannot find their usual medicine goes to another pharmacy. Steady-turnover products cannot run out.' },
          { title: 'What you overbuy ages', desc: 'Excess inventory does not just take space: on products with an expiry date it becomes direct loss.' },
          { title: 'Seasonality that changes the order', desc: 'Seasonal illness, weather changes and back to school move whole categories.' },
        ],
        howTitle: 'Which part of StockAI applies',
        how: [
          { title: 'An alert before the stockout', desc: 'A daily summary by email (and by WhatsApp) lists the products that entered risk, so you order before they run out.' },
          { title: 'Each wholesaler’s real lead time', desc: 'Every reception you log teaches how many days each supplier really takes. A short, reliable lead time lets you cover with little stock.' },
          { title: 'Overstock flagged early', desc: 'What covers several lead times is flagged SOBRESTOCK before the product ages, so you stop replenishing it.' },
          { title: 'Shrinkage from expiry, logged', desc: 'When something expires you log it with its reason and its cost, and stock drops through the same path as any other outflow.' },
          { title: 'Seasons and the country calendar', desc: 'The forecast includes the holidays and commercial dates of your country, and picks up each product’s own seasonality.' },
        ],
        sampleTitle: 'A sample morning',
        sampleLead: 'Four products of an imaginary pharmacy, as StockAI orders them.',
        sampleRows: [
          { product: 'Loratadine 10 mg (box)', daily: '14', stock: '20', lead: '3 days', reorder: '5 days', cover: '1.4 days', signal: 0 },
          { product: 'Oral rehydration (sachet)', daily: '9', stock: '32', lead: '4 days', reorder: '6 days', cover: '3.6 days', signal: 1 },
          { product: 'Ibuprofen 400 mg (box)', daily: '20', stock: '160', lead: '3 days', reorder: '5 days', cover: '8.0 days', signal: 2 },
          { product: 'Antifungal cream (tube)', daily: '1.5', stock: '150', lead: '5 days', reorder: '7 days', cover: '100 days', signal: 3 },
        ],
        sampleReading: 'Loratadine covers 1.4 days with a supplier who takes 3: PEDIR YA. The cream covers 100 days and its supplier delivers in 5: SOBRESTOCK, the product you should not reorder until it comes down.',
        limitsTitle: 'Worth knowing',
        limits: [
          'StockAI does not track lots or expiry dates per unit, nor regulatory traceability: it is a purchasing tool, not a pharmacy system.',
          'It does not handle prescriptions, controlled substances or invoicing.',
          'Shrinkage from expiry is logged by product and quantity, with its cost; each lot’s date lives in your system.',
        ],
      },
      autoparts: {
        label: 'Auto parts',
        short: 'Many references, parts that sell sporadically and suppliers with long lead times.',
        metaTitle: 'Inventory software for auto-parts and spare-parts businesses | StockAI',
        metaDesc: 'Inventory and purchasing for auto-parts houses: intermittent-demand references, import lead times learned from each reception, and overstock alerts.',
        title: 'Inventory for auto-parts businesses.',
        intro: 'Tens of thousands of references, parts that sell two or three times a year and suppliers who import with long lead times. Have the part when the workshop asks for it, without piling up the ones nobody looks for.',
        problemsTitle: 'A day in this trade',
        problems: [
          { title: 'Long lead times, costly mistakes', desc: 'With a supplier who takes weeks, ordering late means weeks without the part. The real lead time matters more here than in a fast-replenishment trade.' },
          { title: 'The reference that sells twice a year', desc: 'The daily average of a sporadic part misleads: it is almost always zero and then suddenly not. It needs a model built for that.' },
          { title: 'Parts for models no longer on the road', desc: 'What was bought for a vehicle model that stopped selling sits in the warehouse for years.' },
        ],
        howTitle: 'Which part of StockAI applies',
        how: [
          { title: 'Learned import lead time', desc: 'You start with the lead time the supplier declares; with each reception you log, StockAI learns the real one and uses it from the third delivery.' },
          { title: 'Intermittent sales, their own model', desc: 'References with many periods without a sale compete with Croston and ETS, and the global model learns from how the rest of your catalogue behaves.' },
          { title: 'ABC-XYZ for the cushion', desc: 'Safety stock goes to the parts that weigh most and are hardest to predict, instead of being spread equally.' },
          { title: 'Several warehouses and transfers', desc: 'With more than one warehouse, StockAI suggests moving before buying, and only if the lending warehouse keeps at least 30 days of cover.' },
          { title: 'Lowest-total-cost order', desc: 'The order optimiser includes fixed freight and minimum orders to propose the lowest-total-cost combination.' },
        ],
        sampleTitle: 'A sample morning',
        sampleLead: 'Four references of an imaginary parts house, as StockAI orders them.',
        sampleRows: [
          { product: 'Oil filter (common model)', daily: '16', stock: '40', lead: '6 days', reorder: '9 days', cover: '2.5 days', signal: 0 },
          { product: 'Front brake pads', daily: '8', stock: '60', lead: '10 days', reorder: '14 days', cover: '7.5 days', signal: 1 },
          { product: 'Timing belt', daily: '3', stock: '90', lead: '14 days', reorder: '19 days', cover: '30.0 days', signal: 2 },
          { product: 'Ball joint (discontinued model)', daily: '0.2', stock: '40', lead: '21 days', reorder: '28 days', cover: '200 days', signal: 3 },
        ],
        sampleReading: 'The filter covers 2.5 days with a supplier who takes 6: PEDIR YA. The pads, with 7.5 days of cover and a 10-day lead time, are already under the 14-day reorder point. The ball joint has 200 days of stock for a part that barely moves: SOBRESTOCK.',
        limitsTitle: 'Worth knowing',
        limits: [
          'StockAI does not ship a cross-reference or vehicle-fitment catalogue: it works with your codes.',
          'A reference with fewer than 20 periods of history is left out of the forecast and shows as SIN DATOS.',
          'The forecast range is not a guarantee: it tells you how far to trust the suggested quantity.',
        ],
      },
      retail: {
        label: 'Multi-store retail',
        short: 'Several stores, different demand in each, and stock you can move between them.',
        metaTitle: 'Inventory software for retail with several stores and warehouses | StockAI',
        metaDesc: 'Inventory per store and consolidated, transfers between stores before buying, seasons and simulated promotions: StockAI for retail with several warehouses.',
        title: 'Inventory for multi-store retail.',
        intro: 'The same product is in surplus in one store and short in another. Buying for all three without looking at where each unit is is the most expensive way to fix it.',
        problemsTitle: 'A day in this trade',
        problems: [
          { title: 'Every store sells differently', desc: 'A product’s demand changes by location, neighbourhood and season. An even order for all leaves one short and another overstocked.' },
          { title: 'Buying what you already have in another store', desc: 'Without a consolidated view, a shortage in one store gets a new purchase while another has plenty.' },
          { title: 'Campaigns and seasons', desc: 'Black Friday, Christmas, back to school: arriving without stock loses the sale, arriving with too much means liquidating.' },
        ],
        howTitle: 'Which part of StockAI applies',
        how: [
          { title: 'Per-warehouse and consolidated views', desc: 'Each store or warehouse has its own tab and its own signal; you also see the total.' },
          { title: 'Transfer before purchase', desc: 'You define lanes with transit days and cost. If a product is short in one store and surplus in another, StockAI suggests moving it, and only if the lending store keeps at least 30 days of cover.' },
          { title: 'Promotions and scenarios', desc: 'In the simulator you mark a promotion, double a category’s demand or delay a supplier, and compare against the current plan without touching anything real.' },
          { title: 'Commercial calendar', desc: 'The dates of ten Latin American countries come loaded (paydays, year-end bonuses, Black Friday, Christmas, back to school) and you can edit them.' },
          { title: 'Scheduled recalculation', desc: 'Every Monday, daily, on business days or on the first of the month, without anyone launching it by hand.' },
        ],
        sampleTitle: 'A sample morning',
        sampleLead: 'The same product in three stores of an imaginary retailer. A 12-day lead time and a 16-day reorder point in all three.',
        sampleRows: [
          { product: 'Basic T-shirt, Centro store', daily: '10', stock: '25', lead: '12 days', reorder: '16 days', cover: '2.5 days', signal: 0 },
          { product: 'Basic T-shirt, Sur store', daily: '6', stock: '120', lead: '12 days', reorder: '16 days', cover: '20.0 days', signal: 2 },
          { product: 'Basic T-shirt, Norte store', daily: '4', stock: '260', lead: '12 days', reorder: '16 days', cover: '65.0 days', signal: 3 },
        ],
        sampleReading: 'Centro runs out in 2.5 days; Norte has over two months. Before buying, StockAI proposes a transfer from Norte, which after lending still keeps at least 30 days of cover (120 units) and so can give up to 140. Sur is OK and does not lend: it would fall under 30 days.',
        limitsTitle: 'Worth knowing',
        limits: [
          'StockAI is not a point of sale or an e-commerce system: it reads your sales by file, by database or through the API.',
          'A new promotion is not in the history: it is tested in the simulator, which is an estimate, not a guarantee.',
          'The quantity to transfer is a suggestion; you make the decision and the movement.',
        ],
      },
    },
  },
  excel: {
    label: 'StockAI vs. Excel',
    title: 'StockAI against an Excel spreadsheet, plainly.',
    intro: 'Excel is the tool almost all of us start buying with, and for a small catalogue it is still a great option. This page says what it does well, where it stops being enough and what changes if you move to StockAI.',
    metaTitle: 'StockAI vs. an Excel spreadsheet for purchasing and inventory: an honest comparison',
    metaDesc: 'What Excel does well for inventory, where it breaks (stockouts by supplier lead time, many products, promotions, several warehouses) and what changes with StockAI.',
    fairTitle: 'What Excel does well',
    fair: [
      { title: 'It is flexible and you know it', desc: 'You build the sheet the way you think, learn nothing new and depend on nobody. For a few products it is hard to beat.' },
      { title: 'It is cheap and it is yours', desc: 'It is on your computer, there is nothing to sign up for, and the file is yours forever.' },
      { title: 'It is good for notes and a quick look', desc: 'A pivot table tells you in a minute how much of what you sold. For looking at the past it is excellent.' },
      { title: 'With 30 or 50 products it is enough', desc: 'If the catalogue is small and sales are steady, a good sheet may be all you need.' },
    ],
    breaksTitle: 'Where it stops being enough',
    breaks: [
      { title: 'A stockout depends on lead time, and the sheet does not know it', desc: 'Having stock today does not tell you whether the next order arrives before it runs out. To know, you must cross, product by product, expected sales with how long that supplier takes. In a sheet that is done by hand, and by hand it is done for a few.' },
      { title: 'Many products', desc: 'Beyond a few dozen, nobody reviews row by row every morning. You review the usual ones, and the shortage appears in the one nobody was watching.' },
      { title: 'Promotions and seasons', desc: 'Testing what happens if you double demand in December means new formulas every time. And payday, Holy Week or Mother’s Day are remembered from memory, or not at all.' },
      { title: 'Several warehouses', desc: 'One sheet per warehouse, or one huge sheet, and nobody sees in one place that one has surplus of what another lacks.' },
      { title: 'The supplier’s lead time changes and nobody writes it down', desc: 'The lead-time column is typed once and stays that way. Measuring it on every delivery takes a discipline that rarely survives a busy month.' },
      { title: 'The sheet depends on one person', desc: 'The formulas, the exceptions and the judgement live in the head of whoever built the file. If they leave, the sheet stays without the judgement.' },
    ],
    tableTitle: 'Side by side',
    tableHead: ['What you need', 'Excel spreadsheet', 'StockAI'],
    rows: [
      { need: 'Take notes and look at the past', excel: 'Very good', stockai: 'Upload your file; you see sales and forecast per product' },
      { need: 'Know what to order today', excel: 'Row by row, with your own formulas', stockai: 'A list sorted by urgency: PEDIR YA, PEDIR PRONTO, OK, SOBRESTOCK' },
      { need: 'How much to order', excel: 'At the buyer’s discretion', stockai: 'Suggested quantity: subtracts what is on its way and respects the minimum order' },
      { need: 'Each supplier’s real lead time', excel: 'A column you type', stockai: 'Measured on each reception; used from the third' },
      { need: 'Many products at once', excel: 'Dozens, with attention', stockai: 'The whole catalogue, every day' },
      { need: 'Products that sell now and then', excel: 'An average that misleads', stockai: 'Models of their own for intermittent sales' },
      { need: 'Test a promotion or a delay', excel: 'New formulas every time', stockai: 'Scenario simulator, against the current plan' },
      { need: 'Several warehouses', excel: 'One sheet per warehouse', stockai: 'Per-warehouse and consolidated views; transfer before purchase' },
      { need: 'Warn you before a stockout', excel: 'It does not', stockai: 'Daily summary by email; by WhatsApp' },
      { need: 'Total freedom to build whatever you want', excel: 'Total', stockai: 'Limited to purchasing and inventory, on purpose' },
    ],
    keepTitle: 'What does not change',
    keep: [
      'You keep using Excel for what Excel does well: StockAI accepts your file as it comes out of your system, as CSV or Excel.',
      'You need to migrate nothing to try it: upload the sales file and see the first list of what to order.',
      'StockAI reports export to Excel and PDF, to share with whoever works in spreadsheets.',
    ],
    bridgeTitle: 'When to stay with Excel',
    bridge: 'If you have few products, steady sales, one main supplier and time to review the sheet every morning, you do not need StockAI yet. StockAI is for when the sheet starts to fall short.',
  },
  method: {
    label: 'How it is calculated',
    title: 'How StockAI calculates what to order.',
    intro: 'No black boxes: models compete on your own history, the one that costs you least to be wrong wins, your supplier’s lead time is learned from your receptions, and the signal rule can be done by hand. This page explains it in plain language.',
    metaTitle: 'How StockAI calculates what to order: models, real lead times and accuracy',
    metaDesc: 'How StockAI’s forecast works: models that compete on your history, the best one per product, supplier lead times learned from your receptions, and how error is shown.',
    stepsTitle: 'From your file to the suggested quantity',
    steps: [
      { title: '1. Your file is checked', desc: 'Before training, every product is examined: missing dates, outliers, insufficient history. If the file would give a wrong forecast, it does not run until you choose how to correct it.' },
      { title: '2. Each product is classified by how it sells', desc: 'Stable, seasonal, intermittent or volatile. That decides which models compete: none you have not enabled is trained, and none that does not fit the pattern.' },
      { title: '3. Models compete on your history', desc: 'Up to nine models: LightGBM, XGBoost, ARIMA, SARIMAX, Prophet, ETS, Croston, an LSTM neural network and a global model that learns from your whole catalogue. Each has the final stretch of your history hidden and is asked to forecast it, in several cuts.' },
      { title: '4. The one that costs least to be wrong wins', desc: 'The winner is chosen with a cost where running out weighs three times more than having extra. And it has to beat two naive forecasts (repeat the last value, repeat the previous season).' },
      { title: '5. The winner is retrained on everything', desc: 'It is retrained on the full history, your latest weeks included, and projects demand with a probable range around it.' },
      { title: '6. It is crossed with your stock and your supplier', desc: 'Forecast demand, stock on hand, what is on its way and the supplier’s lead time give the cover, the reorder point, the signal and the suggested quantity.' },
    ],
    leadTitle: 'How each supplier’s lead time is learned',
    leadBody: [
      'At first you use the lead time you type on the supplier’s card. Each time you log a reception, full or partial, StockAI stores how many days really passed between the order and the delivery.',
      'From that supplier’s third reception, the reorder point uses the real average instead of the declared one, and the screen shows which of the two it is using. The safety cushion also grows when the supplier is irregular in its deliveries.',
    ],
    accuracyTitle: 'How accuracy is shown',
    accuracy: [
      { title: 'Error is measured on dates the model did not see', desc: 'What you see on the forecast screen is how far off each product was on the hidden stretches, not a number computed on what it had already learned.' },
      { title: 'Each product, separately', desc: 'There is no single percentage for the whole catalogue: each product carries its own error and its own range, so you know how far to trust its suggested quantity.' },
      { title: 'Against what actually sold', desc: 'When you upload new sales you can compare the forecast you had published with what sold afterwards.' },
      { title: 'No marketing accuracy percentage', desc: 'We do not publish a hit-rate figure on this site: it would depend on your data, and the one you see is yours.' },
    ],
    limitsTitle: 'The limits, said up front',
    limits: [
      'A product with fewer than 20 periods of history is left out of the forecast and shows as SIN DATOS: no number is invented for it.',
      'The forecast range is not a guarantee: it is the band where sales will probably fall.',
      'It does not guess what never happened: a new promotion or a new large customer is tested in the scenario simulator.',
      'What decides the signal’s colour is a published rule, not the model: AI forecasts, the rule decides.',
      'It does not buy for you: it suggests what, how much and from whom; you review, adjust and send the order.',
    ],
    docsNote: 'For the full technical detail (metrics, validation, routing rules) there is a technical manual and a user manual in PDF.',
  },
  integrations: {
    label: 'Integrations and data',
    title: 'How your data gets into StockAI and how your decisions get out.',
    intro: 'This is the list of what exists today, with no promises: file upload, a connection to your database, a public API, a read-only MCP server, webhooks and WhatsApp. At the end, what does not exist yet, marked as such.',
    metaTitle: 'StockAI integrations and data: CSV, Excel, SQL, API, MCP, webhooks and WhatsApp',
    metaDesc: 'What StockAI connects today: CSV or Excel upload, Postgres and MySQL databases, a public API with keys, a read-only MCP server, webhooks and WhatsApp. And what does not exist yet.',
    nowTitle: 'What exists today',
    now: [
      { tag: 'Files', title: 'CSV and Excel', desc: 'You export from your system and upload the file as it comes. StockAI recognises which column is the date, which the product and which the quantity sold. There is also bulk import of suppliers and purchase orders from CSV or Excel.' },
      { tag: 'Database', title: 'Direct connection to your database', desc: 'You connect StockAI to your Postgres or MySQL database and pull sales with a query, with no files in between. Credentials are stored encrypted.' },
      { tag: 'API', title: 'Public API with keys', desc: 'Your system can upload sales and stock and take the signal away without anyone opening StockAI. Keys carry a read or write scope, every call is metered, and the documentation includes real examples.' },
      { tag: 'AI assistants', title: 'Read-only MCP server', desc: 'An MCP-compatible AI client can query your inventory with the same API key and the same limit. There are five tools and all of them only read: none writes or approves anything.' },
      { tag: 'Events', title: 'Webhooks', desc: 'StockAI notifies an HTTPS address of yours when a forecast finishes or fails (job.completed and job.failed), so your system can react without polling.' },
      { tag: 'Messaging', title: 'WhatsApp and email', desc: 'The daily summary of products at risk and the orders to suppliers go out by email, and by WhatsApp. With WhatsApp, each person links their number and can ask the assistant how their inventory is doing; the assistant only reads.' },
      { tag: 'Reports', title: 'Excel and PDF', desc: 'Purchase orders export as CSV or PDF, and reports as Excel and PDF, for your system or for whoever works in spreadsheets.' },
    ],
    formatsTitle: 'The data StockAI needs',
    formats: [
      'Sales history: date, product code and quantity sold as a minimum.',
      'Current stock, so the signal has something to compare against.',
      'Approximate lead time for each supplier: approximate is enough, it corrects itself.',
      'Cost and minimum order, if you want the order to respect them and the idle money to show.',
    ],
    soonTitle: 'Coming soon',
    soonLead: 'These two things are under discussion and have no date. They are listed to be transparent, not as a promise.',
    soon: [
      { title: 'Supplier portal', desc: 'An access point so your suppliers can confirm orders and delivery dates without writing to you. It does not exist yet.' },
      { title: 'No-code connectors', desc: 'Ready-made links with automation tools, for those who do not want to program against the API. They do not exist yet.' },
    ],
    soonNote: 'Today there are no ready-made connectors for ERPs or accounting systems. If your operation needs a custom integration, we build it with our technical team, case by case.',
    customTitle: 'Need a custom integration?',
    customBody: 'Tell us which system you use and what data you want to move. We answer within 24 hours.',
    devLink: 'API documentation',
    docsLink: 'Help center',
  },
  changelog: {
    label: 'What’s new',
    title: 'What’s new: what changed in StockAI.',
    intro: 'A summary of what was finished in recent weeks, with the date it landed in the code. Only what exists; no announcements of what is coming.',
    metaTitle: 'What’s new in StockAI: recent changes',
    metaDesc: 'A dated record of StockAI’s recent improvements: forecasting, inventory, API, MCP, trial accounts, the mobile app and the help center.',
    note: 'Dates are those of the change in the code; an improvement may reach your account a few days later.',
    entries: [
      {
        date: '2026-10-05',
        title: 'A calmer panel and snappier lists',
        items: [
          'The purchasing panel was redesigned in a neutral tone: alerts moved to the bell and to marks on each row, and a “more analysis” fold holds the secondary material.',
          'Inventory and suppliers load in pages from the server, for large catalogues.',
          'Automatic tracking of forecast accuracy.',
          'Quiet notices for pending arrivals and for the data a supplier is missing.',
          'The logo heads the sidebar.',
        ],
      },
      {
        date: '2026-10-04',
        title: 'Forecasting: compare, import, understand progress',
        items: [
          'Compare the published forecast with the sales you uploaded afterwards.',
          'The session comparison draws every update on one shared time axis.',
          'Bulk import of suppliers and purchase orders from CSV or Excel.',
          'Training progress is measured per product, cost-weighted, with an indicator across the app and a screen that can be resumed.',
          'Voice dictation in the assistant and a country picker for phone numbers.',
          'Server errors are translated in one place, in Spanish and English.',
          'An upload guide with visual aids inside the app.',
        ],
      },
      {
        date: '2026-10-02',
        title: 'Help center, legal documents and a better-documented API',
        items: [
          'A help center at /docs, in Spanish and English, with search.',
          'Legal documents: terms, privacy, cookies, legal notice, acceptable use, data processing, AI use, responsible disclosure, accessibility, commercial conditions and licence.',
          'The API documentation includes real response examples and field types for 91 read endpoints.',
          'A seven-entry sidebar, with Settings as the hub for the rest.',
        ],
      },
      {
        date: '2026-10-01',
        title: 'Trial accounts, a complete API and a mobile app',
        items: [
          '24-hour trial accounts from the home page, with sample data and no sign-up.',
          'Every action in the app can be called with an API key, with a read or write scope, metered and documented.',
          'One assistant with your account’s figures, in the app and over WhatsApp.',
          'Configurable signal cut-offs: how many lead times of cover mark PEDIR YA and SOBRESTOCK, per company, supplier or category.',
          'StockAI installs as an app, and the day-to-day screens adapt to the phone.',
        ],
      },
      {
        date: '2026-09-30',
        title: 'New name, MCP server and recommendation traceability',
        items: [
          'The platform was renamed StockAI.',
          'A read-only MCP server, with five tools, for AI assistants.',
          'Reversal of purchase orders and of receptions logged by mistake.',
          'A recommendation log: what StockAI suggested and what was done.',
          'Costa Rica is the default country of the holiday calendar.',
          'A per-product notice when the safety cushion cannot keep the chosen service level.',
        ],
      },
    ],
  },
  home: {
    tourTag: 'The app, inside',
    tourTitle: 'Three screens you open every morning.',
    tourLead: 'Real StockAI captures with sample data inside. Not mock-ups.',
    tourMore: 'See every screen',
    audienceTag: 'Who it is for',
    audienceTitle: 'Who StockAI is for, and who it is not.',
    audienceLead: 'Better to know before you upload the first file.',
    forTitle: 'It is for you if',
    for: [
      'You buy goods from suppliers with a delivery lead time: you are a distributor, wholesaler, shop or manufacturer.',
      'You have hundreds or thousands of products and today decide purchases with an Excel sheet or the buyer’s memory.',
      'Your team is small and has no data analyst.',
      'You have a sales history in CSV or Excel, with at least 20 periods per product.',
    ],
    notForTitle: 'It is not for you if',
    notFor: [
      'You are looking for an invoicing system, accounting or a point of sale: StockAI does not invoice.',
      'You need traceability by lot or by each unit’s expiry date: it does not track it.',
      'You want the tool to buy by itself: it suggests, and you send the order.',
      'Your business is services, with no inventory to replenish.',
      'You launch new products and want a forecast without history: without enough data, no number is invented.',
    ],
    exploreTitle: 'And to go deeper',
    casesMore: 'One page per industry, with a sample morning',
    compareMore: 'The full comparison, including what Excel does well',
    methodMore: 'How it is calculated, in plain language',
  },
}

export const LANDING_CONTENT: Record<Lang, ContentCopy> = { es, en }

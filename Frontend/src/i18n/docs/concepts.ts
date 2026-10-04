// Help center — Conceptos: the arithmetic behind the stock signal, said in
// words. Every rule and number here is read from the code:
//   backend/inventory/service.py          _calc_signal, _calc_recommended,
//                                         _safety_stock, resolve_lead_time,
//                                         get_incoming_detail
//   backend/inventory/signal_thresholds.py defaults 0.5 / 3.0, bounds, 2x RP
//   backend/inventory/defaults.py         15 days, 95 %, MOQ 1
//   backend/sessions/planning_service.py  grain choice
//   ForecastingCore/.../evaluation/metrics.py, training/router.py
// Screen labels quoted from Frontend/src/i18n/translations.ts.
import type { DocSectionContent } from '@/i18n/docs/types'
import type { DocPageIdOf } from '@/i18n/docs/tree'

export const CONCEPTS: DocSectionContent<DocPageIdOf<'conceptos'>> = {
  es: {
    'conceptos/semaforo': {
      title: 'El semáforo de stock',
      nav: 'El semáforo',
      description:
        'Cada producto recibe una de cinco señales comparando para cuántos días de venta te alcanza el stock con lo que tarda tu proveedor en entregar. Aquí está la regla exacta y lo que puedes ajustar.',
      blocks: [
        { t: 'p', text: 'El semáforo responde una sola pregunta por producto: **¿llega el próximo pedido antes de que te quedes sin stock?** Para eso compara dos números:' },
        { t: 'ul', items: [
          '**Cobertura**: para cuánto tiempo de venta te alcanza el stock que tienes hoy, al ritmo que pronostica StockAI (incluido el efecto de cualquier evento o temporada que hayas registrado).',
          '**Tiempo de entrega**: cuánto tarda tu proveedor desde que le pides hasta que la mercadería está en tu bodega. Ver [Tiempo de entrega aprendido](/docs/conceptos/tiempo-de-entrega-aprendido).',
        ] },
        { t: 'h2', id: 'signals', text: 'Las cinco señales' },
        { t: 'signals', items: [
          { signal: 'now', label: 'Pedir YA', text: 'La cobertura es **menor que la mitad del tiempo de entrega** (valor de fábrica 0,5). Ya vas tarde: aunque pidas hoy, probablemente te quedes sin stock antes de que llegue.' },
          { signal: 'soon', label: 'Pedir pronto', text: 'La cobertura **no alcanza tu [punto de reorden](/docs/conceptos/punto-de-reorden)**: lo que vendes mientras llega el pedido más tu colchón de seguridad. Es el momento de pedir.' },
          { signal: 'ok', label: 'OK', text: 'La cobertura está por encima del punto de reorden y por debajo del umbral de sobrestock. No hay que hacer nada.' },
          { signal: 'over', label: 'Sobrestock', text: 'La cobertura llega a **3 veces el tiempo de entrega** (valor de fábrica) **o al doble de tu punto de reorden, lo que sea mayor**. También es sobrestock un producto con stock que ya no tiene demanda pronosticada.' },
          { signal: 'none', label: 'Sin datos', text: 'Falta el stock registrado o el pronóstico de ese producto. No es un error: es un producto sin configurar. Ver [Sin datos](/docs/solucion-de-problemas/sin-datos).' },
        ] },
        { t: 'p', text: 'En la base de datos y en la API las señales se guardan como `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK` y `SIN_DATOS`.' },
        { t: 'h2', id: 'edge-cases', text: 'Casos especiales' },
        { t: 'ul', items: [
          '**Estante vacío y sin demanda**: un producto sin stock al que no se le pronostica venta queda en **OK**, no en sobrestock: no hay nada que pedir ni nada parado.',
          '**Stock sin demanda**: un producto con stock al que ya no se le pronostica venta queda en **Sobrestock**. No hay venta que justifique ese inventario.',
          '**Misma cobertura, señal distinta**: 10 días de stock son cómodos con un proveedor que entrega en 5 y críticos con uno que tarda 30. El semáforo siempre compara contra el tiempo de entrega de cada producto.',
        ] },
        { t: 'h2', id: 'suggested-quantity', text: 'La señal y la cantidad sugerida' },
        { t: 'p', text: 'Solo **Pedir YA** y **Pedir pronto** llevan una cantidad a pedir. En OK, Sobrestock y Sin datos la cantidad sugerida es cero. Como «Pedir pronto» se enciende justo en el punto de reorden, una línea que pide comprar nunca sale con cero unidades.' },
        { t: 'h2', id: 'configure', text: 'Ajustar los umbrales' },
        { t: 'p', text: 'En [Configurar inventario](app:/configurar-inventario), el panel **«Reglas del semáforo»** te deja cambiar los dos múltiplos del tiempo de entrega:' },
        { t: 'table', head: ['Umbral', 'De fábrica', 'Permitido'], rows: [
          ['«Pedir YA cuando el stock alcance para menos de» … veces lo que tarda el proveedor', '0,5', 'entre 0,1 y 0,95'],
          ['«Sobrestock cuando el stock alcance para» … veces lo que tarda el proveedor, o más', '3', 'entre 1,5 y 12'],
        ] },
        { t: 'ul', items: [
          'En «Aplicar a» eliges **«Toda la empresa»** o un proveedor. Una regla de proveedor es una **excepción** que manda sobre la de la empresa para sus productos. StockAI también respeta reglas por categoría: la más específica gana (proveedor, luego categoría, luego empresa, luego los valores de fábrica).',
          'Los dos valores se guardan siempre juntos, y «Pedir YA» tiene que quedar por debajo de «Sobrestock».',
          'Antes de guardar, la pantalla te dice cuántos de tus productos cambiarían de señal y te muestra ejemplos.',
          '«Volver a los valores de fábrica» deshace tus cambios (también te dice cuántos productos cambiarían).',
          'Solo un analista o un administrador puede cambiar estas reglas. Al guardar, el semáforo, las alertas de las 8:00 y las sugerencias de compra usan los valores nuevos.',
        ] },
        { t: 'shot', key: 'configurar', alt: 'Configurar inventario, con el panel Reglas del semáforo y su vista previa de productos que cambiarían de señal' },
        { t: 'note', tone: 'info', title: '«Pedir pronto» no es configurable', text: 'Su límite es tu punto de reorden, no un múltiplo. Si quieres que te avise antes, sube el nivel de servicio del producto: eso agranda el colchón y, con él, el punto de reorden. Tampoco es configurable la regla del «doble del punto de reorden», que garantiza que la franja OK nunca quede vacía en un producto muy variable.' },
      ],
    },
    'conceptos/punto-de-reorden': {
      title: 'Punto de reorden',
      description:
        'El nivel de stock al que hay que pedir para que la mercadería llegue antes de quedarte en cero: lo que vendes mientras esperas el pedido, más tu colchón de seguridad.',
      blocks: [
        { t: 'p', text: 'El punto de reorden es el número que separa «todavía no» de «hay que pedir». Cuando tu stock baja de ahí, el producto pasa a **Pedir pronto** en el [semáforo](/docs/conceptos/semaforo).' },
        { t: 'h2', id: 'formula', text: 'Cómo se calcula' },
        { t: 'code', text: 'punto de reorden = demanda por período × (tiempo de entrega + frecuencia de pedido)\n                 + stock de seguridad' },
        { t: 'dl', items: [
          ['Demanda por período', 'Lo que el pronóstico espera que vendas por día, semana o mes, según la [granularidad](/docs/conceptos/granularidad) de tu cuenta.'],
          ['Tiempo de entrega', 'Lo que tarda el proveedor en entregar. Ver [Tiempo de entrega aprendido](/docs/conceptos/tiempo-de-entrega-aprendido).'],
          ['Frecuencia de pedido', 'El campo «Frecuencia de pedido (días)» de la ficha del proveedor: cada cuánto le haces pedidos. Si pides cada semana, pon 7. Vacío cuenta como cero.'],
          ['Stock de seguridad', 'El colchón contra la variación de la demanda y del tiempo de entrega. Ver [Stock de seguridad](/docs/conceptos/stock-de-seguridad).'],
        ] },
        { t: 'h2', id: 'review-period', text: 'Por qué entra la frecuencia de pedido' },
        { t: 'p', text: 'Si le pides a un proveedor solo una vez por semana, el pedido de hoy tiene que alcanzarte no solo hasta que llegue, sino hasta que llegue **el siguiente**. Sin ese tramo, la cantidad quedaría corta exactamente una semana de venta. Si dejas la frecuencia vacía, StockAI asume que pides sin una frecuencia fija y el cálculo usa solo el tiempo de entrega.' },
        { t: 'h2', id: 'quantity', text: 'De punto de reorden a cantidad a pedir' },
        { t: 'code', text: 'cantidad = punto de reorden − stock actual − lo que ya viene en camino\n(nunca menos de 0; si hay que pedir, nunca menos que la compra mínima)' },
        { t: 'ul', items: [
          'Se resta lo que ya está [en camino](/docs/conceptos/en-camino), para no pedir dos veces lo mismo.',
          'La compra mínima (MOQ) es un piso, no un múltiplo: si necesitas 520 y el mínimo es 500, se sugieren 520, no 1.000. De fábrica es 1.',
          'La cantidad se redondea hacia arriba a unidades enteras.',
        ] },
        { t: 'h2', id: 'example', text: 'Un ejemplo con números redondos' },
        { t: 'note', tone: 'info', title: 'Ejemplo ilustrativo', text: 'Los números son inventados para que la cuenta se lea fácil; no salen de ningún cliente.' },
        { t: 'p', text: 'Un producto vende 10 unidades por día, el proveedor tarda 15 días, le pides cada 7 días y el stock de seguridad es 40 unidades.' },
        { t: 'steps', items: [
          'Venta mientras dura la espera: 10 × (15 + 7) = 220 unidades.',
          'Punto de reorden: 220 + 40 = **260 unidades**, es decir 26 días de venta.',
          'Con 300 unidades en bodega la cobertura es 30 días: por encima de 26, el producto está en **OK**.',
          'Con 200 unidades (20 días) cae por debajo de 26: **Pedir pronto**. La cantidad sugerida es 260 − 200 = **60 unidades**, menos lo que ya venga en camino.',
          'Con 70 unidades (7 días) la cobertura es menor que la mitad de los 15 días de entrega: **Pedir YA**.',
          'Sobrestock empieza en el mayor de 3 × 15 = 45 días o 2 × 26 = 52 días: desde 520 unidades.',
        ] },
      ],
    },
    'conceptos/stock-de-seguridad': {
      title: 'Stock de seguridad y nivel de servicio',
      nav: 'Stock de seguridad',
      description:
        'El colchón que protege contra vender más de lo previsto o que el proveedor se atrase. Cuánto colchón depende del nivel de servicio que eliges: de fábrica, 95 %.',
      blocks: [
        { t: 'p', text: 'El pronóstico dice cuánto esperas vender, pero la venta real nunca cae exacta y los proveedores no siempre tardan lo mismo. El stock de seguridad es lo que pides de más para cubrir esas dos sorpresas.' },
        { t: 'h2', id: 'two-risks', text: 'Dos riesgos que se suman' },
        { t: 'ul', items: [
          '**Que la demanda varíe** durante el tiempo de entrega.',
          '**Que el tiempo de entrega varíe**, aunque la demanda sea la normal.',
        ] },
        { t: 'p', text: 'Los dos se combinan como se combinan dos variaciones independientes: no se suman directamente, se suman sus cuadrados.' },
        { t: 'code', text: 'stock de seguridad = z × √( T × σd²  +  d² × σT² )\n\nz   factor del nivel de servicio\nT   tiempo de entrega + frecuencia de pedido, en períodos\nσd  variación de la demanda por período\nd   demanda promedio por período\nσT  variación del tiempo de entrega' },
        { t: 'h2', id: 'service-level', text: 'El nivel de servicio' },
        { t: 'p', text: 'Es la probabilidad con la que quieres cubrir la demanda mientras esperas el pedido. Más nivel de servicio, más colchón, y el aumento no es lineal:' },
        { t: 'table', head: ['Nivel de servicio', 'Factor z'], rows: [
          ['90 %', '1,282'],
          ['95 % (de fábrica)', '1,645'],
          ['97 %', '1,881'],
          ['99 %', '2,326'],
        ] },
        { t: 'p', text: 'Si no lo configuraste, StockAI usa 95 % y en «Ver por qué» lo marca como estimado. Lo cambias en el editor del producto en [Inventario](app:/inventario), campo «Nivel de servicio». Para ver el efecto antes de tocar nada, usa una regla de «Inventario de seguridad» en el [simulador de escenarios](/docs/analisis/escenarios).' },
        { t: 'h2', id: 'lead-time-variability', text: 'De dónde sale la variación del tiempo de entrega' },
        { t: 'steps', items: [
          'De las recepciones reales de ese proveedor, cuando ya hay al menos 3 registradas.',
          'Si no, del campo «Variabilidad (días)» de la ficha del proveedor: si dice 15 días pero a veces llega en 18, pon 3.',
          'Si el producto no tiene proveedor ni variabilidad, ese término es cero y el colchón cubre solo la variación de la demanda.',
        ] },
        { t: 'h2', id: 'measured', text: 'Cuando el colchón se mide en vez de suponerse' },
        { t: 'p', text: 'Cuando el motor midió los errores reales del pronóstico a lo largo del tiempo de entrega, ese colchón medido reemplaza a la parte de demanda de la fórmula (la parte del tiempo de entrega se sigue sumando). La pantalla lo dice: «Este colchón sale de los errores reales… no de la fórmula estándar». Es el mismo rango que ves en el gráfico de [Pronósticos](/docs/analisis/pronosticos).' },
        { t: 'note', tone: 'warn', title: 'Productos de venta intermitente', text: 'En productos con muchos días sin ventas no podemos garantizar el nivel de servicio que eliges: medido, el colchón alcanza cerca de la mitad de las veces. El Panel de compras lo advierte junto al porcentaje en esos productos. Revisa la cantidad antes de aprobar.' },
      ],
    },
    'conceptos/tiempo-de-entrega-aprendido': {
      title: 'Tiempo de entrega aprendido',
      description:
        'StockAI aprende cuánto tarda de verdad cada proveedor a partir de las llegadas que registras, y desde la tercera recepción usa ese número en lugar del que configuraste.',
      blocks: [
        { t: 'p', text: 'Un proveedor que dice 7 días pero entrega siempre en 12 no puede seguir generando recomendaciones que suponen 7. Por eso la evidencia manda sobre lo declarado.' },
        { t: 'h2', id: 'precedence', text: 'Qué tiempo de entrega se usa' },
        { t: 'p', text: 'Para cada producto, StockAI toma el primero que exista de esta lista:' },
        { t: 'steps', items: [
          '**Aprendido** de las recepciones del proveedor (con 3 o más).',
          'El que escribiste en la **ficha del producto**.',
          'El «Tiempo de entrega (días)» de la **ficha del proveedor**.',
          'Si no hay ninguno, **15 días**, marcado como «estimado».',
        ] },
        { t: 'p', text: 'En «Ver por qué» del Panel de compras y en la fila de Inventario puedes ver de dónde salió el número de cada producto.' },
        { t: 'h2', id: 'how-it-learns', text: 'Cómo aprende' },
        { t: 'ul', items: [
          'Cada orden de compra que llega **completa** (estado «Recibida») deja una observación: los días entre la orden y la llegada de las últimas unidades. Una orden que nunca se completa no enseña nada.',
          'Con **3 observaciones** de un proveedor, el promedio redondeado a días enteros reemplaza al tiempo configurado en todos sus productos. Con menos, una sola entrega rara (un feriado, un camión varado) reescribiría todo.',
          'Si todas las entregas registradas llegaron el mismo día que se pidieron, el promedio es cero y no dice nada: StockAI lo declara inutilizable y sigue con el tiempo configurado.',
          'Si deshaces una recepción, también se borran las observaciones que esa recepción creó.',
        ] },
        { t: 'p', text: 'En [Proveedores](app:/proveedores), la columna «Aprendizaje» te dice en qué punto va cada uno: «Llevo n de 3 entregas registradas…» o «Aprendí de n entregas: tardan X días en promedio…».' },
        { t: 'h2', id: 'scorecard', text: 'Ajustar no es acusar' },
        { t: 'p', text: 'StockAI es más estricto para señalar a un proveedor que para ajustar su tiempo: le bastan 3 recepciones para aprender, pero la columna «Tendencia» del scorecard necesita al menos 6 (2 recientes y 4 de base) antes de decir que alguien viene tardando más que de costumbre. Con menos, muestra «Aún no».' },
        { t: 'note', tone: 'info', text: 'Registrar las llegadas en [Pedidos](/docs/uso-diario/pedidos) no es papeleo: es lo que sube tu stock y lo que le enseña a StockAI el tiempo real de tus proveedores.' },
      ],
    },
    'conceptos/como-compiten-los-modelos': {
      title: 'Cómo compiten los modelos',
      description:
        'Para cada producto se entrenan varios modelos de pronóstico sobre tu propio historial, se prueban sobre ventas que no vieron, y gana el que menos te cuesta equivocarse.',
      blocks: [
        { t: 'p', text: 'No hay un modelo que sirva para todo: un producto estable, uno de temporada y uno que se vende cada tanto necesitan herramientas distintas. StockAI no elige por intuición; hace competir a los candidatos y se queda con el que mejor le habría ido a tu bolsillo.' },
        { t: 'h2', id: 'candidates', text: 'Los candidatos' },
        { t: 'ul', items: [
          'StockAI tiene **nueve** modelos disponibles, entre estadísticos, de aprendizaje automático y uno global que aprende de todo tu catálogo a la vez.',
          'En una actualización normal desde Mis ventas compiten **cinco** de ellos, incluido el global, que es el que le da un pronóstico usable a un producto nuevo o con poca historia.',
          'Cada producto recibe solo los modelos que le convienen según su tipo de serie (estable, de temporada, intermitente, volátil o corta). Ese reparto solo puede achicar la lista, nunca agregar un modelo que no se eligió.',
        ] },
        { t: 'p', text: 'En pantalla los modelos se llaman «Modelo 1», «Modelo 2»…: el nombre del algoritmo no cambia ninguna decisión de compra.' },
        { t: 'h2', id: 'testing', text: 'Cómo se prueban' },
        { t: 'p', text: 'Cada modelo se evalúa **hacia adelante**: se entrena con la historia hasta un corte y se mide contra las semanas que vienen después, que no vio. Eso se repite en varios cortes sucesivos, para que un buen resultado no dependa de una sola fecha afortunada.' },
        { t: 'p', text: 'Un producto necesita al menos **20 períodos** de historia para entrar a esta competencia. Los que no llegan quedan fuera del pronóstico.' },
        { t: 'h2', id: 'cost', text: 'Gana el que menos cuesta, no el que menos falla' },
        { t: 'p', text: 'Quedarte sin producto cuesta más que tener de sobra: pierdes la venta y a veces el cliente. Por eso el error se pesa: **un faltante cuenta tres veces más que un sobrante**. El ganador es el modelo con menor costo así medido, que puede no ser el de menor error porcentual. Lo ves en la pestaña «Métricas» de [Pronósticos](/docs/analisis/pronosticos), ordenada por «Costo».' },
        { t: 'h2', id: 'references', text: 'Referencias y modelo combinado' },
        { t: 'ul', items: [
          '**Referencias**: «último valor», «temporada» y «promedio». Son la vara a superar —qué pasaría pidiendo lo mismo que la vez pasada— y nunca ganan.',
          '**Modelo combinado**: una mezcla de los modelos que compitieron. Se muestra para comparar, pero ninguna compra se calcula con él.',
        ] },
        { t: 'h2', id: 'final-fit', text: 'El pronóstico final usa toda tu historia' },
        { t: 'p', text: 'El modelo que se califica se detiene en el corte, porque se mide contra lo que viene después. El modelo que pronostica, una vez elegido, se vuelve a entrenar con **toda** tu historia, incluidas las semanas más recientes.' },
      ],
    },
    'conceptos/granularidad': {
      title: 'Granularidad: día, semana o mes',
      nav: 'Granularidad',
      description:
        'StockAI calcula tus compras por día, por semana o por mes. Elige solo el período más fino que tu historial permite, y un administrador lo puede cambiar.',
      blocks: [
        { t: 'p', text: 'La granularidad es la unidad de tiempo con la que se piensa todo: la demanda, la cobertura, el tiempo de entrega expresado en períodos y la cantidad a pedir.' },
        { t: 'h2', id: 'auto', text: 'Cómo se elige' },
        { t: 'p', text: 'Al subir ventas, en «Nivel de detalle del plan» puedes dejar «Auto (recomendado)». StockAI calcula por el **período más fino que tu historial permite**: si reportas ventas diarias, por día; si tu archivo es semanal, por semana.' },
        { t: 'p', text: 'No se elige por cuál da menos error a propósito: agrupar siempre baja el error, así que ese criterio escogería casi siempre el mes y te dejaría un plan demasiado grueso para reponer.' },
        { t: 'h2', id: 'change', text: 'Cambiarla' },
        { t: 'p', text: 'En [Mi cuenta](app:/mi-cuenta), la tarjeta **«Cada cuánto se calculan tus compras»** te explica por qué se eligió el período actual y te deja cambiarlo entre Día, Semana y Mes, solo entre los que tu historial permite. Solo un administrador puede cambiarlo, porque aplica a toda la cuenta.' },
        { t: 'p', text: 'La tarjeta dice el motivo en una frase, por ejemplo «Tus compras se calculan por semana, porque así es como reportas tus ventas». Si habías elegido un período y tu archivo más reciente ya no da para eso, StockAI pasa al que sí se puede y te lo dice: «Habías elegido…, pero tu archivo más reciente ya no da para eso…».' },
        { t: 'h2', id: 'effect', text: 'Lo que cambia' },
        { t: 'ul', items: [
          'La cobertura y el tiempo de entrega se expresan en períodos: un tiempo de entrega de 15 días son unas 2,1 semanas.',
          'Cambia las cantidades sugeridas y lo que te llega por correo y WhatsApp cada mañana, no solo lo que ves.',
          'Un producto con poca historia puede desaparecer del cálculo diario y sí aparecer en el semanal, porque necesita al menos 20 períodos.',
        ] },
        { t: 'note', tone: 'warn', text: 'No es un cambio de vista: es un cambio en el plan de toda la empresa. Avísale a tu equipo antes de hacerlo.' },
      ],
    },
    'conceptos/en-camino': {
      title: 'Qué cuenta como «en camino»',
      nav: 'En camino',
      description:
        'Lo que ya pediste y todavía no llega se descuenta de la cantidad sugerida, para que StockAI no te pida dos veces lo mismo. Aquí está qué entra en esa cuenta y qué no.',
      blocks: [
        { t: 'p', text: 'La cantidad a pedir se calcula contra tu **posición de inventario**, no solo contra el estante: lo que tienes más lo que ya viene. Sin eso, StockAI te pediría las mismas unidades cada día hasta que llegaran físicamente.' },
        { t: 'h2', id: 'what-counts', text: 'Qué cuenta' },
        { t: 'ul', items: [
          'Cada línea pedida de una orden de compra que está **En camino**, **Parcial** o **No llegó**, siempre que la orden no esté cancelada.',
          'De cada línea cuenta **lo pedido menos lo ya recibido**, nunca menos de cero. Una recepción parcial descuenta exactamente las unidades que llegaron.',
          'No importa si la orden se envió desde StockAI: una orden que descargaste en CSV y mandaste por tu cuenta también cuenta, porque quedó registrada en Pedidos.',
          'Los **traslados** entre bodegas que están en tránsito cuentan para la bodega de destino. La de origen ya los descontó al enviarlos.',
        ] },
        { t: 'h2', id: 'what-does-not', text: 'Qué deja de contar' },
        { t: 'ul', items: [
          'Una orden **cancelada** deja de contar en ese momento. Si la reabres, vuelve a contar.',
          'Una orden **Recibida** ya no está en camino: sus unidades ya están en tu stock.',
          '**Deshacer envío** no la saca: una orden que dejaste de marcar como enviada sigue contando como en camino, porque la mercadería puede venir igual.',
        ] },
        { t: 'h2', id: 'not-received', text: '«No llegó» no cierra nada' },
        { t: 'p', text: 'Marcar una orden como «No llegó» registra que la entrega no ocurrió todavía, no que la orden se canceló. Sigue contando en camino y todavía se le puede registrar la llegada. Si de verdad no va a llegar, cancélala en [Pedidos](/docs/uso-diario/pedidos).' },
        { t: 'note', tone: 'info', text: 'Si ves que StockAI sugiere poco para un producto que necesitas, revisa en Pedidos si hay una orden vieja abierta que nunca recibiste ni cancelaste: está restando unidades que quizá no vengan.' },
      ],
    },
  },
  en: {
    'conceptos/semaforo': {
      title: 'The stock signal',
      nav: 'The stock signal',
      description:
        'Each product gets one of five signals by comparing how much selling time your stock covers with how long your supplier takes to deliver. Here is the exact rule and what you can adjust.',
      blocks: [
        { t: 'p', text: 'The stock signal answers one question per product: **will the next order arrive before you run out?** It compares two numbers:' },
        { t: 'ul', items: [
          '**Cover**: how much selling time the stock you hold today lasts, at the rate StockAI forecasts (including the effect of any event or season you recorded).',
          '**Lead time**: how long your supplier takes from your order to the goods being in your warehouse. See [Learned lead time](/docs/conceptos/tiempo-de-entrega-aprendido).',
        ] },
        { t: 'h2', id: 'signals', text: 'The five signals' },
        { t: 'signals', items: [
          { signal: 'now', label: 'Order NOW', text: 'Cover is **less than half the lead time** (factory value 0.5). You are already late: even ordering today, you will probably run out before it arrives.' },
          { signal: 'soon', label: 'Order soon', text: 'Cover **does not reach your [reorder point](/docs/conceptos/punto-de-reorden)**: what you sell while the order travels plus your safety buffer. Time to order.' },
          { signal: 'ok', label: 'OK', text: 'Cover is above the reorder point and below the overstock threshold. Nothing to do.' },
          { signal: 'over', label: 'Overstock', text: 'Cover reaches **3 times the lead time** (factory value) **or twice your reorder point, whichever is larger**. A product with stock and no forecast demand is overstock too.' },
          { signal: 'none', label: 'No data', text: 'The product has no recorded stock or no forecast. Not an error: an unconfigured product. See [No data](/docs/solucion-de-problemas/sin-datos).' },
        ] },
        { t: 'p', text: 'In the database and the API the signals are stored as `PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK` and `SIN_DATOS`.' },
        { t: 'h2', id: 'edge-cases', text: 'Special cases' },
        { t: 'ul', items: [
          '**Empty shelf, no demand**: a product with no stock and no forecast sales is **OK**, not overstock: nothing to order and nothing sitting idle.',
          '**Stock, no demand**: a product holding stock with no forecast sales is **Overstock**. No sale justifies that inventory.',
          '**Same cover, different signal**: 10 days of stock is comfortable with a supplier who delivers in 5 and critical with one who takes 30. The signal always compares against each product\'s own lead time.',
        ] },
        { t: 'h2', id: 'suggested-quantity', text: 'The signal and the suggested quantity' },
        { t: 'p', text: 'Only **Order NOW** and **Order soon** carry a quantity. On OK, Overstock and No data the suggested quantity is zero. Because "Order soon" lights up exactly at the reorder point, a line that tells you to buy never comes with zero units.' },
        { t: 'h2', id: 'configure', text: 'Adjusting the thresholds' },
        { t: 'p', text: 'In [Set up my inventory](app:/configurar-inventario), the **"Stock signal rules"** panel lets you change the two lead-time multiples:' },
        { t: 'table', head: ['Threshold', 'Factory value', 'Allowed'], rows: [
          ['"Order NOW when stock covers less than" … times the supplier\'s lead time', '0.5', '0.1 to 0.95'],
          ['"Overstock when stock covers" … times the supplier\'s lead time, or more', '3', '1.5 to 12'],
        ] },
        { t: 'ul', items: [
          'Under "Apply to" you pick **"The whole company"** or one supplier. A supplier rule is an **exception** that overrides the company rule for its products. StockAI also honours category rules: the most specific wins (supplier, then category, then company, then factory values).',
          'Both values are always saved together, and "Order NOW" must stay below "Overstock".',
          'Before you save, the screen tells you how many of your products would change signal and shows examples.',
          '"Back to factory values" undoes your changes (it also tells you how many products would change).',
          'Only an analyst or an admin can change these rules. Once saved, the stock signal, the 8:00 alerts and the purchase suggestions use the new values.',
        ] },
        { t: 'shot', key: 'configurar', alt: 'Set up my inventory, with the Stock signal rules panel and its preview of products that would change signal' },
        { t: 'note', tone: 'info', title: '"Order soon" is not configurable', text: 'Its boundary is your reorder point, not a multiple. If you want an earlier warning, raise the product\'s service level: that grows the buffer and, with it, the reorder point. The "twice the reorder point" rule is not configurable either; it guarantees the OK band is never empty for a very volatile product.' },
      ],
    },
    'conceptos/punto-de-reorden': {
      title: 'Reorder point',
      description:
        'The stock level at which you must order so the goods arrive before you hit zero: what you sell while you wait for the order, plus your safety buffer.',
      blocks: [
        { t: 'p', text: 'The reorder point is the number that separates "not yet" from "order now". When your stock drops below it, the product turns **Order soon** in the [stock signal](/docs/conceptos/semaforo).' },
        { t: 'h2', id: 'formula', text: 'How it is computed' },
        { t: 'code', text: 'reorder point = demand per period × (lead time + order frequency)\n              + safety stock' },
        { t: 'dl', items: [
          ['Demand per period', 'What the forecast expects you to sell per day, week or month, depending on your account\'s [granularity](/docs/conceptos/granularidad).'],
          ['Lead time', 'How long the supplier takes to deliver. See [Learned lead time](/docs/conceptos/tiempo-de-entrega-aprendido).'],
          ['Order frequency', 'The "Order frequency (days)" field on the supplier card: how often you order from them. If you order weekly, put 7. Empty counts as zero.'],
          ['Safety stock', 'The buffer against variation in demand and in lead time. See [Safety stock](/docs/conceptos/stock-de-seguridad).'],
        ] },
        { t: 'h2', id: 'review-period', text: 'Why order frequency is in there' },
        { t: 'p', text: 'If you order from a supplier only once a week, today\'s order must last not just until it arrives but until **the next one** arrives. Without that stretch the quantity would fall short by exactly one week of sales. Leave the frequency empty and StockAI assumes you order with no fixed cadence, so the calculation uses the lead time alone.' },
        { t: 'h2', id: 'quantity', text: 'From reorder point to order quantity' },
        { t: 'code', text: 'quantity = reorder point − current stock − what is already on its way\n(never below 0; when there is something to order, never below the minimum order)' },
        { t: 'ul', items: [
          'What is already [on its way](/docs/conceptos/en-camino) is subtracted, so you never order the same units twice.',
          'The minimum order quantity (MOQ) is a floor, not a multiple: if you need 520 and the minimum is 500, 520 is suggested, not 1,000. The factory value is 1.',
          'The quantity is rounded up to whole units.',
        ] },
        { t: 'h2', id: 'example', text: 'An example with round numbers' },
        { t: 'note', tone: 'info', title: 'Illustrative example', text: 'The numbers are made up so the arithmetic reads easily; they come from no customer.' },
        { t: 'p', text: 'A product sells 10 units a day, the supplier takes 15 days, you order every 7 days and the safety stock is 40 units.' },
        { t: 'steps', items: [
          'Sales during the wait: 10 × (15 + 7) = 220 units.',
          'Reorder point: 220 + 40 = **260 units**, that is 26 days of sales.',
          'With 300 units on hand, cover is 30 days: above 26, the product is **OK**.',
          'With 200 units (20 days) it falls below 26: **Order soon**. The suggested quantity is 260 − 200 = **60 units**, minus anything already on its way.',
          'With 70 units (7 days), cover is less than half of the 15-day lead time: **Order NOW**.',
          'Overstock starts at the larger of 3 × 15 = 45 days or 2 × 26 = 52 days: from 520 units.',
        ] },
      ],
    },
    'conceptos/stock-de-seguridad': {
      title: 'Safety stock and service level',
      nav: 'Safety stock',
      description:
        'The buffer that protects you against selling more than expected or a late supplier. How big it is depends on the service level you choose: 95% by default.',
      blocks: [
        { t: 'p', text: 'The forecast says how much you expect to sell, but real sales never land exactly on it and suppliers do not always take the same time. Safety stock is what you order on top to cover both surprises.' },
        { t: 'h2', id: 'two-risks', text: 'Two risks that add up' },
        { t: 'ul', items: [
          '**Demand varies** during the lead time.',
          '**The lead time varies**, even with normal demand.',
        ] },
        { t: 'p', text: 'They combine the way two independent variations do: their squares add, not the values themselves.' },
        { t: 'code', text: 'safety stock = z × √( T × σd²  +  d² × σT² )\n\nz   service-level factor\nT   lead time + order frequency, in periods\nσd  variation of demand per period\nd   average demand per period\nσT  variation of the lead time' },
        { t: 'h2', id: 'service-level', text: 'The service level' },
        { t: 'p', text: 'It is the probability with which you want to cover demand while you wait for the order. More service level means more buffer, and the increase is not linear:' },
        { t: 'table', head: ['Service level', 'z factor'], rows: [
          ['90%', '1.282'],
          ['95% (factory)', '1.645'],
          ['97%', '1.881'],
          ['99%', '2.326'],
        ] },
        { t: 'p', text: 'If you did not set one, StockAI uses 95% and marks it as estimated in "Why?". You change it in the product editor in [Inventory](app:/inventario), "Service level" field. To see the effect before touching anything, use a "Safety stock" rule in the [scenario simulator](/docs/analisis/escenarios).' },
        { t: 'h2', id: 'lead-time-variability', text: 'Where the lead-time variation comes from' },
        { t: 'steps', items: [
          'From that supplier\'s real receptions, once at least 3 are recorded.',
          'Otherwise from the "Variability (days)" field on the supplier card: if it says 15 days but sometimes arrives in 18, put 3.',
          'If the product has no supplier and no variability, that term is zero and the buffer covers demand variation only.',
        ] },
        { t: 'h2', id: 'measured', text: 'When the buffer is measured instead of assumed' },
        { t: 'p', text: 'When the engine measured the forecast\'s real errors over the lead time, that measured buffer replaces the demand part of the formula (the lead-time part is still added). The screen says so: "This cushion comes from … own errors across the lead time, not from the textbook formula". It is the same range you see on the [Forecasts](/docs/analisis/pronosticos) chart.' },
        { t: 'note', tone: 'warn', title: 'Intermittent products', text: 'For products with many days without sales we cannot guarantee the service level you choose: measured, the buffer holds about half the time. The Purchasing Panel warns about it next to the percentage on those products. Check the quantity before approving.' },
      ],
    },
    'conceptos/tiempo-de-entrega-aprendido': {
      title: 'Learned lead time',
      description:
        'StockAI learns how long each supplier really takes from the arrivals you record, and from the third reception it uses that number instead of the one you configured.',
      blocks: [
        { t: 'p', text: 'A supplier who says 7 days but always delivers in 12 cannot keep producing recommendations that assume 7. That is why evidence outranks what was declared.' },
        { t: 'h2', id: 'precedence', text: 'Which lead time is used' },
        { t: 'p', text: 'For each product StockAI takes the first one that exists from this list:' },
        { t: 'steps', items: [
          '**Learned** from the supplier\'s receptions (3 or more).',
          'The one you typed on the **product card**.',
          'The "Lead time (days)" on the **supplier card**.',
          'If there is none, **15 days**, marked as "estimated".',
        ] },
        { t: 'p', text: 'In "Why?" on the Purchasing Panel and on the Inventory row you can see where each product\'s number came from.' },
        { t: 'h2', id: 'how-it-learns', text: 'How it learns' },
        { t: 'ul', items: [
          'Every purchase order that arrives **complete** ("Received" status) leaves one observation: the days between the order and the arrival of its last units. An order that never completes teaches nothing.',
          'With **3 observations** from a supplier, their average rounded to whole days replaces the configured time on all their products. With fewer, one odd delivery (a holiday, a stranded truck) would rewrite everything.',
          'If every recorded delivery arrived the same day it was ordered, the average is zero and says nothing: StockAI declares it unusable and keeps the configured time.',
          'If you undo a reception, the observations that reception created are deleted too.',
        ] },
        { t: 'p', text: 'In [Suppliers](app:/proveedores), the "Learning" column shows where each one stands: "n of 3 deliveries recorded…" or "Learned from n deliveries: X days on average…".' },
        { t: 'h2', id: 'scorecard', text: 'Adjusting is not accusing' },
        { t: 'p', text: 'StockAI is stricter about flagging a supplier than about adjusting their time: 3 receptions are enough to learn, but the scorecard\'s "Trend" column needs at least 6 (2 recent and 4 as a baseline) before it says someone is running later than usual. With fewer it shows "Not yet".' },
        { t: 'note', tone: 'info', text: 'Recording arrivals in [Orders](/docs/uso-diario/pedidos) is not paperwork: it is what raises your stock and what teaches StockAI your suppliers\' real lead times.' },
      ],
    },
    'conceptos/como-compiten-los-modelos': {
      title: 'How the models compete',
      description:
        'For each product several forecasting models are trained on your own history, tested on sales they did not see, and the one whose mistakes cost you least wins.',
      blocks: [
        { t: 'p', text: 'No single model fits everything: a stable product, a seasonal one and one that sells now and then need different tools. StockAI does not choose by intuition; it makes the candidates compete and keeps whichever would have done best for your wallet.' },
        { t: 'h2', id: 'candidates', text: 'The candidates' },
        { t: 'ul', items: [
          'StockAI has **nine** models available: statistical ones, machine-learning ones and a global one that learns from your whole catalogue at once.',
          'In a normal update from My sales, **five** of them compete, including the global one — the one that gives a new or short-history product a usable forecast.',
          'Each product only gets the models that suit its series type (stable, seasonal, intermittent, volatile or short). That routing can only shrink the list, never add a model that was not selected.',
        ] },
        { t: 'p', text: 'On screen the models are called "Model 1", "Model 2"…: the algorithm\'s name changes no purchasing decision.' },
        { t: 'h2', id: 'testing', text: 'How they are tested' },
        { t: 'p', text: 'Each model is evaluated **walking forward**: it is trained on history up to a cut-off and measured against the weeks that follow, which it did not see. That is repeated over several successive cut-offs, so a good result does not hinge on one lucky date.' },
        { t: 'p', text: 'A product needs at least **20 periods** of history to enter this competition. Those that fall short are left out of the forecast.' },
        { t: 'h2', id: 'cost', text: 'The cheapest wins, not the least wrong' },
        { t: 'p', text: 'Running out costs more than holding extra: you lose the sale and sometimes the customer. So errors are weighted: **a shortage counts three times as much as a surplus**. The winner is the model with the lowest cost measured that way, which may not be the one with the lowest percentage error. You see it in the "Metrics" tab of [Forecasts](/docs/analisis/pronosticos), sorted by "Cost".' },
        { t: 'h2', id: 'references', text: 'References and the combined model' },
        { t: 'ul', items: [
          '**References**: "last value", "season" and "average". They are the bar to beat — what ordering the same as last time would do — and never win.',
          '**Combined model**: a blend of the models that competed. It is shown for comparison, but no purchase is computed from it.',
        ] },
        { t: 'h2', id: 'final-fit', text: 'The final forecast uses all your history' },
        { t: 'p', text: 'The model being graded stops at the cut-off, because it is scored on what comes after. The model that forecasts, once chosen, is retrained on **all** your history, including the most recent weeks.' },
      ],
    },
    'conceptos/granularidad': {
      title: 'Granularity: day, week or month',
      nav: 'Granularity',
      description:
        'StockAI computes your purchases by day, by week or by month. It picks the finest period your history supports on its own, and an admin can change it.',
      blocks: [
        { t: 'p', text: 'Granularity is the unit of time everything is reasoned in: demand, cover, the lead time expressed in periods and the quantity to order.' },
        { t: 'h2', id: 'auto', text: 'How it is chosen' },
        { t: 'p', text: 'When you upload sales you can leave "Plan detail level" on "Auto (recommended)". StockAI computes at the **finest period your history supports**: daily if you report daily sales, weekly if your file is weekly.' },
        { t: 'p', text: 'It is deliberately not chosen by which one gives the lowest error: aggregating always lowers error, so that rule would nearly always pick the month and leave you a plan too coarse to reorder against.' },
        { t: 'h2', id: 'change', text: 'Changing it' },
        { t: 'p', text: 'In [My account](app:/mi-cuenta), the **"How often your purchases are calculated"** card explains why the current period was picked and lets you switch between Day, Week and Month, only among those your history supports. Only an administrator can change it, because it applies to the whole account.' },
        { t: 'p', text: 'The card states the reason in one sentence, for example "Your purchases are calculated weekly, because that is how you report your sales". If you had chosen a period and your most recent file no longer supports it, StockAI moves to one that does and tells you: "You had chosen…, but your most recent file no longer supports it…".' },
        { t: 'h2', id: 'effect', text: 'What changes' },
        { t: 'ul', items: [
          'Cover and lead time are expressed in periods: a 15-day lead time is about 2.1 weeks.',
          'It changes the suggested quantities and what reaches you by email and WhatsApp each morning, not only what you see.',
          'A product with little history can vanish from the daily calculation and still appear in the weekly one, because it needs at least 20 periods.',
        ] },
        { t: 'note', tone: 'warn', text: 'This is not a view switch: it changes the plan for the whole company. Tell your team before you do it.' },
      ],
    },
    'conceptos/en-camino': {
      title: 'What counts as "on its way"',
      nav: 'On its way',
      description:
        'What you already ordered and has not arrived is subtracted from the suggested quantity, so StockAI never asks you to order the same units twice. Here is what goes into that count and what does not.',
      blocks: [
        { t: 'p', text: 'The order quantity is computed against your **inventory position**, not just the shelf: what you hold plus what is coming. Without it, StockAI would ask for the same units every day until they physically arrived.' },
        { t: 'h2', id: 'what-counts', text: 'What counts' },
        { t: 'ul', items: [
          'Every ordered line of a purchase order that is **On the way**, **Partial** or **Did not arrive**, as long as the order is not cancelled.',
          'Each line counts **what was ordered minus what was already received**, never below zero. A partial reception subtracts exactly the units that arrived.',
          'It does not matter whether the order was sent from StockAI: an order you downloaded as CSV and sent yourself counts too, because it was recorded in Orders.',
          '**Transfers** between warehouses that are in transit count for the destination warehouse. The origin already took them off when sending.',
        ] },
        { t: 'h2', id: 'what-does-not', text: 'What stops counting' },
        { t: 'ul', items: [
          'A **cancelled** order stops counting at that moment. Reopen it and it counts again.',
          'A **Received** order is no longer on its way: its units are already in your stock.',
          '**Undo send** does not remove it: an order you un-marked as sent still counts as on its way, because the goods may come anyway.',
        ] },
        { t: 'h2', id: 'not-received', text: '"Did not arrive" closes nothing' },
        { t: 'p', text: 'Marking an order "Did not arrive" records that the delivery has not happened yet, not that the order was cancelled. It still counts as on its way and its arrival can still be recorded. If it really is not coming, cancel it in [Orders](/docs/uso-diario/pedidos).' },
        { t: 'note', tone: 'info', text: 'If StockAI suggests little for a product you need, check Orders for an old open order you never received or cancelled: it is subtracting units that may never come.' },
      ],
    },
  },
}

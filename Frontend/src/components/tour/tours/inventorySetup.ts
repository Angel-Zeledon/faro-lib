import type { TourDefinition } from '../types'

export const inventorySetupTour: TourDefinition = {
  id: 'inventory-setup-v2',
  route: '/configurar-inventario',
  name: 'name',
  // The one screen someone lands on with nothing to do yet: two empty panels
  // and no idea that they are allowed to stop early. That is what the tour is
  // for, so it offers itself.
  autoStart: true,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'setup.import', title: 'import_title', body: 'import_body' },
    { anchor: 'setup.import', title: 'mapping_title', body: 'mapping_body' },
    { anchor: 'setup.import', title: 'counts_title', body: 'counts_body' },
    { anchor: 'setup.gaps', title: 'gaps_title', body: 'gaps_body' },
    { anchor: 'setup.gaps', title: 'fields_title', body: 'fields_body' },
    { title: 'assumptions_title', body: 'assumptions_body' },
  ],
  copy: {
    es: {
      name: 'Configurar sin configurarlo todo',
      intro_title: 'No tienes que llenar tus dos mil productos',
      intro_body: 'Para decirte cuánto pedir necesitamos saber, por producto, cuánto tienes hoy, cuánto te cuesta y cuántos días tarda tu proveedor. Pedirte eso para el catálogo entero es exactamente lo que hace que nadie termine.\n\nAsí que aquí no se ordena por nombre ni por código: se ordena por plata. Unas decenas de productos suelen llevarse la mayor parte de tu compra del mes, y con esos configurados ya compras bien.\n\nHay dos caminos y escriben lo mismo: subir tu archivo, o llenar a mano los renglones de abajo.',
      import_title: 'Primero, el archivo que tu sistema ya exporta',
      import_body: 'Nadie va a renombrar encabezados para complacernos, así que aceptamos el archivo tal como sale de tu sistema: CSV, TXT o Excel (.xlsx, .xls), con nombres de columna en español y números con coma.\n\nLo que queremos es el reporte de existencias o el maestro de productos: una fila por producto, con su código y su stock. Si además trae costo, proveedor o días de entrega, mejor — pero no hace falta que lo prepares.\n\nElegir el archivo no escribe nada todavía: primero te mostramos qué entendimos.',
      mapping_title: 'Dinos qué columna es qué',
      mapping_body: 'Al elegir el archivo aparece «Así entendimos tus columnas»: a la izquierda nuestros campos, y en cada desplegable la columna tuya que le asignamos. Casi siempre acierta; corrígelo cuando no.\n\nEl único obligatorio es Código del producto — sin él no podemos pegar la fila con tu catálogo y el botón de importar no se activa.\n\nDe los demás, los que de verdad mueven la sugerencia de compra son Stock actual, Costo, Días de entrega, Compra mínima y Proveedor. Lo que no te sirva déjalo en «No importar»: mejor vacío que mal mapeado.',
      counts_title: 'Lee el conteo antes de confirmar',
      counts_body: 'Debajo del mapeo decimos cuántas filas están listas, cuántas quedan fuera por problemas y cuántas se saltan por no traer código.\n\nEsa línea es tu control de calidad: si de 1.200 filas entran 300, algo está mal mapeado y no deberías confirmar todavía. Cambia el desplegable y los números se recalculan solos.\n\nDebajo listamos cada problema con ejemplos — fila, producto y el valor que no pudimos leer. Textos como «N/D» o «—» en la columna de stock son la causa típica.\n\nSólo el botón Importar escribe algo. Un archivo mal leído no falla: mete stock falso, y con stock falso el semáforo te miente con toda confianza.',
      gaps_title: 'La lista está ordenada por plata',
      gaps_body: 'Arriba están los productos que se llevan la mayor parte de tu compra del mes. «Vale» es cuánta plata representa cada uno en el horizonte, «% del mes» su peso, y «Acumulado» la suma corrida desde la primera fila.\n\nEsa última columna es la que te deja parar: cuando el acumulado de un renglón llega al 80%, todo lo que sigue vale junto el 20% restante. Las filas con fondo verde son las que te recomendamos completar.\n\nMira la barra con cuidado — mide plata, no filas. Puede ir en 82% con la mayoría de los renglones todavía vacíos, y eso está bien.',
      fields_title: 'Los tres datos de cada fila',
      fields_body: '«Le falta» te dice qué le hace falta a ese producto. A la derecha, en «Completar», están las tres casillas para dárselo:\n\n· Stock: unidades que tienes hoy en bodega. Ej. 340. Acepta coma o punto.\n· Costo: lo que te cuesta a TI una unidad, no el precio de venta. Ej. 1250. Ponerle el precio de venta infla la compra estimada y te desordena la lista.\n· Días de entrega: desde que pides hasta que llega. Ej. 12, no 2 «porque el bodeguero lo baja rápido».\n\nPuedes llenar sólo una y Guardar; el resto queda como estaba. El botón pasa a «Guardado» cuando el dato ya quedó.',
      assumptions_title: 'Lo que no nos des, lo suponemos',
      assumptions_body: 'Sin días de entrega asumimos 15. Sin nivel de servicio, 95%. Sin compra mínima, 1 unidad.\n\nSin costo seguimos ordenando por volumen en vez de por plata, y no podemos decirte cuánto vale la compra. Sin stock no hay cobertura que calcular.\n\nNunca lo escondemos: cada supuesto queda marcado como supuesto en el panel de compras. Pero un dato tuyo vale más que una suposición nuestra — 15 días en un proveedor que tarda 45 te deja sin producto mes y medio.\n\nPuedes irte a la mitad y volver: la barra te espera donde la dejaste.',
    },
    en: {
      name: 'Setting up without setting up everything',
      intro_title: 'You do not have to fill in two thousand products',
      intro_body: 'To tell you how much to order we need to know, per product, how much you have today, what it costs you and how many days your supplier takes. Asking you for that across the whole catalogue is exactly what makes people give up.\n\nSo nothing here is sorted by name or code: it is sorted by money. A few dozen products usually carry most of your monthly purchase, and with those configured you already buy well.\n\nThere are two routes and they write the same thing: upload your file, or fill in the rows below by hand.',
      import_title: 'First, the file your system already exports',
      import_body: 'Nobody is going to rename headers to please us, so we take the file exactly as your system produces it: CSV, TXT or Excel (.xlsx, .xls), with Spanish column names and comma decimals.\n\nWhat we want is the stock report or the product master: one row per product, with its code and its stock. If it also carries cost, supplier or lead time, better — but you do not have to prepare it.\n\nChoosing the file writes nothing yet: first we show you what we understood.',
      mapping_title: 'Tell us which column is which',
      mapping_body: 'Once you pick the file, "This is how we read your columns" appears: our fields on the left, and in each dropdown the column of yours we assigned to it. It usually gets it right; correct it when it does not.\n\nThe only required one is Product code — without it we cannot match the row to your catalogue, and the import button stays off.\n\nOf the rest, the ones that really move the purchase suggestion are Current stock, Cost, Lead time, Minimum order and Supplier. Leave anything useless on "Do not import": empty beats wrongly mapped.',
      counts_title: 'Read the counts before confirming',
      counts_body: 'Below the mapping we say how many rows are ready, how many are left out because of problems, and how many are skipped for having no code.\n\nThat line is your quality check: if 300 of 1,200 rows go in, something is mapped wrong and you should not confirm yet. Change a dropdown and the numbers recompute on their own.\n\nUnderneath we list each problem with examples — row, product and the value we could not read. Text like "N/A" or "—" in the stock column is the usual cause.\n\nOnly the Import button writes anything. A misread file does not fail: it writes wrong stock, and with wrong stock the signal lies to you with complete confidence.',
      gaps_title: 'The list is ranked by money',
      gaps_body: 'At the top are the products that carry most of this month’s purchase. "Worth" is how much money each represents over the horizon, "% of month" its weight, and "Cumulative" the running total from the first row down.\n\nThat last column is what lets you stop: once a row’s cumulative reaches 80%, everything below it is worth the remaining 20% put together. The rows on a green background are the ones we recommend filling in.\n\nRead the bar carefully — it measures money, not rows. It can sit at 82% with most lines still empty, and that is fine.',
      fields_title: 'The three figures on each row',
      fields_body: '"Missing" tells you what that product lacks. On the right, under "Fill in", are the three boxes that give it to us:\n\n· Stock: units you have in the warehouse today. E.g. 340. Comma or dot both work.\n· Cost: what one unit costs YOU, not the sale price. E.g. 1250. Typing the sale price inflates the estimated purchase and scrambles the ranking.\n· Lead time (days): from placing the order to it arriving. E.g. 12, not 2 "because the warehouse guy is quick".\n\nYou can fill in only one and hit Save; the rest stays as it was. The button turns to "Saved" once the figure has landed.',
      assumptions_title: 'Whatever you skip, we assume',
      assumptions_body: 'With no lead time we assume 15 days. With no service level, 95%. With no minimum order, 1 unit.\n\nWith no cost we keep ranking by volume instead of money, and cannot tell you what the purchase is worth. With no stock there is no coverage to calculate.\n\nWe never hide it: every assumption is flagged as an assumption on the purchasing panel. But a figure from you beats a guess from us — 15 days on a supplier who takes 45 leaves you out of stock for six weeks.\n\nYou can leave halfway and come back: the bar waits where you left it.',
    },
  },
}

"""Daily-operation section of the StockAI user manual (Spanish + English).

Content only: no imports, no logic. The PDF assembler reads SECTION.
Image keys are fixed: panel, inventory, pedidos, mensajes.
The four stock signals (PEDIR_YA, PEDIR_PRONTO, OK, SOBRESTOCK) are stored
values and stay in Spanish in both languages.
"""

SECTION = {
    "id": "operation",
    "es": {
        "title": "Operación diaria",
        "intro": (
            "Estas cuatro pantallas son la rutina de todos los días: abres el Panel de "
            "compras en la mañana para decidir qué pedir, entras a Inventario cuando "
            "necesitas revisar o corregir un producto en particular, pasas por Pedidos "
            "cuando llega la mercadería, y usas Mensajes para coordinar con tu equipo sin "
            "salir de StockAI. Las tres primeras trabajan sobre los mismos datos —el "
            "pronóstico de demanda y el stock que tienes registrado— así que lo que "
            "corriges en una se ve en las otras."
        ),
        "screens": [
            {
                "name": "Panel de compras",
                "route": "/compras",
                "image": "panel",
                "purpose": (
                    "Es la pantalla con la que empiezas el día. Toma el pronóstico de "
                    "demanda y el stock que tienes registrado, y te entrega una lista corta "
                    "de decisiones: qué productos hay que pedir hoy, cuáles pueden esperar a "
                    "esta semana, cuánto pedir de cada uno y a qué proveedor. Apruebas o "
                    "rechazas línea por línea y al final generas la orden de compra."
                ),
                "walkthrough": [
                    "Arriba a la izquierda está la insignia «Panel de compras» con el "
                    "subtítulo «Tus próximas compras, calculadas.», y debajo el saludo con "
                    "tu nombre y cuántas acciones tienes pendientes hoy.",
                    "Bajo el saludo aparece la fecha y «Datos en uso», que es el nombre de "
                    "la carga de ventas con la que se calculó todo lo que ves; a la derecha, "
                    "el indicador de qué tan frescos están esos datos.",
                    "Si tu stock o tus ventas llevan mucho tiempo sin actualizarse, antes de "
                    "cualquier otra cosa aparece un aviso: el semáforo de abajo se calculó "
                    "sobre datos viejos y no se puede presentar como confiable.",
                    "El aviso de supuestos («Estas recomendaciones usan N supuestos "
                    "nuestros») aparece cuando parte del cálculo se hizo con valores que "
                    "nunca nos diste, e incluye un enlace para ver qué configurar primero.",
                    "Después vienen los avisos de proveedores: los que se están tardando más "
                    "que de costumbre, y los pedidos cuya llegada ya se pasó de fecha "
                    "(«¿Llegaron estos pedidos?») con un botón «Registrar llegada».",
                    "La fila de indicadores muestra Total SKUs monitoreados, Riesgo hoy, "
                    "Esta semana, Precisión promedio y Valor en bodega, con una nota debajo "
                    "cuando hay productos sin conteo de stock.",
                    "«Resumen ejecutivo del día» es un párrafo que explica en palabras la "
                    "situación; tiene un botón para volver a generarlo.",
                    "Si tienes más de una bodega, antes de las compras aparecen las "
                    "sugerencias de traslado: mover stock de una bodega a otra sale gratis, "
                    "así que se propone antes que comprar.",
                    "«Urgente — actúa hoy» lista los productos con señal PEDIR_YA y «Esta "
                    "semana» los de señal PEDIR_PRONTO. Cada tarjeta trae el nombre, la "
                    "señal, el SKU, el motivo, el proveedor, la cantidad y los botones "
                    "«Aprobar» y «Rechazar».",
                    "«Ver por qué» abre el desglose de la tarjeta: cobertura actual, demanda "
                    "diaria pronosticada, tiempo de entrega del proveedor, nivel de servicio, costo "
                    "unitario, MOQ, stock actual y punto de reorden.",
                    "Al aprobar la primera línea aparece abajo la barra del carrito, con la "
                    "cantidad de productos aprobados, el total, el margen que protege el "
                    "pedido y el botón «Descargar orden de compra».",
                    "Más abajo están «Compras y transferencias sugeridas» (el plan del "
                    "optimizador para todo el horizonte), «Anticípate — picos de demanda "
                    "próximos», «Cambios en demanda», «Recomendaciones del sistema» y "
                    "«Oportunidades de capital»."
                ],
                "fields": [
                    ("Total SKUs monitoreados",
                     "Cuántos productos cubre la carga de ventas que está en uso."),
                    ("Riesgo hoy",
                     "Cuántos productos tienen señal PEDIR_YA. Muestra «—» cuando ningún "
                     "producto tiene stock registrado, porque entonces el cero no significa "
                     "«no hay riesgo» sino «no sabemos»."),
                    ("Esta semana",
                     "Cuántos productos tienen señal PEDIR_PRONTO."),
                    ("Precisión promedio",
                     "Qué tan cerca estuvo el pronóstico de las ventas reales en las pruebas, "
                     "medido sobre datos que el modelo no vio al entrenar. 85% o más es "
                     "bueno; por debajo de 70% conviene revisar la historia de ese producto."),
                    ("Valor en bodega",
                     "Stock actual por costo unitario, sumado. Muestra «—» si no registraste "
                     "el costo de ningún producto: el valor es desconocido, no cero."),
                    ("Ver por qué",
                     "Abre el desglose de la tarjeta con los seis u ocho valores sobre los "
                     "que se construyó la recomendación. Todos vienen calculados del "
                     "servidor; la pantalla no inventa ninguno."),
                    ("Cobertura actual",
                     "Cuánto te dura el stock que tienes al ritmo de venta pronosticado. La "
                     "unidad sigue el período de tu actualización (días en una actualización diaria)."),
                    ("Tiempo de entrega del proveedor",
                     "Días entre que haces el pedido y llega a tu bodega. Si aparece la "
                     "etiqueta «estimado», es el supuesto de StockAI (15 días) porque nadie lo "
                     "configuró todavía."),
                    ("Nivel de servicio",
                     "Probabilidad con la que quieres cubrir la demanda durante el tiempo de entrega. "
                     "Si no lo configuraste, StockAI usa 95% y lo marca como estimado. "
                     "Límite medido: en productos de venta intermitente (muchos días sin "
                     "ventas) el colchón cumple cerca de la mitad de las veces, no el 95%; "
                     "en esos productos el panel lo advierte junto al porcentaje."),
                    ("Costo unitario / MOQ",
                     "El costo que registraste para ese producto y la compra mínima que exige "
                     "el proveedor. Si no los diste, se marcan como estimados (la compra "
                     "mínima por defecto es 1)."),
                    ("Punto de reorden",
                     "Nivel de stock al que hay que pedir para que la mercadería llegue antes "
                     "de quedarte en cero: demanda durante el tiempo de entrega más el colchón de "
                     "seguridad."),
                    ("Pedir:",
                     "La cantidad sugerida. Puedes hacer clic sobre el número y escribir "
                     "otro; al cambiarlo la línea queda marcada como modificada y entra al "
                     "carrito."),
                    ("Proveedor:",
                     "Lista desplegable para mandar esa línea a otro proveedor antes de "
                     "generar la orden. Cambiarlo también marca la línea como modificada."),
                    ("Aprobar / Rechazar / Deshacer / Restaurar",
                     "Aprobar mete la línea al carrito; Rechazar la saca y queda registrado "
                     "como que no seguiste la recomendación; Deshacer y Restaurar revierten "
                     "cada uno de los dos."),
                    ("Este pedido protege … en ventas con … en margen",
                     "Ventas y margen de las líneas aprobadas que tienen precio de venta y "
                     "costo registrados. Las que no los tienen quedan fuera y se reportan "
                     "aparte, para no inflar ni desinflar la cifra."),
                    ("Entregar en",
                     "Bodega de destino de la orden. Solo aparece si tienes dos o más "
                     "bodegas."),
                    ("Descargar orden de compra",
                     "Baja el CSV de la orden y, al mismo tiempo, registra la orden en StockAI "
                     "para que aparezca en Pedidos y se le pueda registrar la llegada."),
                    ("Enviar a proveedores ahora",
                     "Aparece justo después de generar la orden. Manda la orden por email o "
                     "WhatsApp a cada proveedor que tenga datos de contacto en su ficha."),
                    ("Compras y transferencias sugeridas",
                     "Plan del optimizador: qué comprar y qué mover para cubrir todo tu "
                     "horizonte de planificación al menor costo total. «Convertir en OC» "
                     "vuelve una línea en orden de compra sin pasar por el carrito."),
                    ("Anticípate — picos de demanda próximos",
                     "Picos que el pronóstico ve antes de que el semáforo se ponga rojo, con "
                     "la fecha límite para pedir según el tiempo de entrega del proveedor."),
                    ("Oportunidades de capital",
                     "Cuánto dinero tienes inmovilizado en productos con cobertura excesiva, "
                     "y cuáles son."),
                    ("Última actualización / Actualizar datos",
                     "Hace cuánto se cargó lo que ves, y el botón para volver a pedirlo."),
                ],
                "tasks": [
                    ("Generar la orden de compra del día",
                     " 1. Revisa las tarjetas de «Urgente — actúa hoy» y «Esta semana». "
                     "2. En cada una, ajusta la cantidad o el proveedor si hace falta. "
                     "3. Pulsa «Aprobar» en las que vas a pedir y «Rechazar» en las que no. "
                     "4. Revisa la barra del carrito abajo: productos aprobados, total y "
                     "margen protegido. 5. Si tienes varias bodegas, elige la bodega en "
                     "«Entregar en». 6. Pulsa «Descargar orden de compra»: baja el CSV y la "
                     "orden queda registrada en Pedidos."),
                    ("Cambiar la cantidad que StockAI sugiere",
                     " 1. Haz clic sobre el número que está junto a «Pedir:». 2. Escribe la "
                     "cantidad que vas a pedir. 3. Pulsa Enter o haz clic fuera del campo. "
                     "4. La línea queda marcada como modificada y entra al carrito con tu "
                     "cantidad."),
                    ("Enviar la orden al proveedor",
                     " 1. Genera la orden con «Descargar orden de compra». 2. En el panel "
                     "«Orden de compra generada» revisa qué líneas le tocan a cada "
                     "proveedor. 3. Pulsa «Enviar a proveedores ahora». 4. Si algún proveedor "
                     "aparece omitido por falta de contacto, usa «Enviarme por WhatsApp» o "
                     "«Copiar mensaje» y reenvíaselo tú."),
                    ("Registrar la llegada de un pedido atrasado",
                     " 1. Busca el bloque «¿Llegaron estos pedidos?» arriba de la pantalla. "
                     "2. Pulsa «Registrar llegada» en la línea del proveedor. 3. En la "
                     "ventana que se abre, escribe cuánto llegó de cada producto. 4. Pulsa "
                     "«Llegó todo completo» si llegó la orden entera, o «Guardar cantidades» "
                     "si llegó parcial."),
                    ("Entender por qué se recomienda un producto",
                     " 1. En la tarjeta del producto, pulsa «Ver por qué». 2. Lee la frase de "
                     "explicación y revisa los valores del desglose. 3. Fíjate en cuáles "
                     "llevan la etiqueta «estimado»: esos son supuestos nuestros, no datos "
                     "tuyos. 4. Si alguno está mal, corrígelo en Inventario y vuelve a "
                     "cargar esta pantalla."),
                ],
                "gotchas": [
                    "«Descargar orden de compra» hace dos cosas a la vez: baja el archivo y "
                    "registra la orden. Si el registro falla verás un aviso de error: en ese "
                    "caso tienes el CSV pero la orden NO existe en StockAI, y hay que generarla "
                    "de nuevo.",
                    "«Compras y transferencias sugeridas» normalmente pide más unidades que "
                    "las tarjetas de arriba. No es una contradicción: las tarjetas responden "
                    "«qué pido hoy» y el optimizador responde «cómo cubro todo el horizonte "
                    "de planificación».",
                    "Un «Riesgo hoy» en cero no siempre significa que estés tranquilo. Si tus "
                    "productos no tienen stock registrado, el indicador muestra «—» y una "
                    "nota: no hay riesgo detectado porque no hay nada medido.",
                    "Con rol de viewer ves todas las recomendaciones pero no puedes "
                    "aprobarlas: en lugar de los botones aparece «Tu rol no puede generar "
                    "órdenes».",
                    "La etiqueta «estimado» junto a un número no es un error. Significa que "
                    "ese valor lo puso StockAI porque nadie lo configuró; en cuanto lo "
                    "registres, deja de aparecer.",
                ],
            },
            {
                "name": "Inventario",
                "route": "/inventario",
                "image": "inventory",
                "purpose": (
                    "Es la lista completa de tus productos con el semáforo de stock. Aquí "
                    "revisas producto por producto cuánta cobertura te queda, cuánto habría "
                    "que pedir y de dónde sale ese número; y aquí corriges los datos sobre "
                    "los que descansa todo lo demás: stock actual, proveedor, tiempo de entrega, "
                    "costo, precio de venta y compra mínima."
                ),
                "walkthrough": [
                    "El encabezado dice «Inventario» con el subtítulo «Semáforo de stock · "
                    "Recomendaciones de compra», y a la derecha el indicador de frescura de "
                    "los datos.",
                    "Junto a él está el selector de vistas: Tabla, Simple, Proveedor, "
                    "Actualizar stock (solo si tu rol puede editar), Plata "
                    "parada, Costos al alza, Margen que se achica, Pronóstico en plata y "
                    "Costo de ignorar.",
                    "La barra de herramientas trae el botón de recarga, la importación de "
                    "CSV, «Plantilla», «Exportar OC», «Exportar OC (editada)», PDF, y los "
                    "accesos a Impacto, Proveedores y «Registrar salida».",
                    "Si hay productos que quedaron fuera del pronóstico, aparece un aviso con "
                    "la lista y el motivo de cada uno.",
                    "Debajo va la frase de situación en lenguaje llano («N producto(s) se "
                    "agotan antes de que llegue tu próximo pedido…») y, si corresponde, el "
                    "aviso de cuántos SKUs no tienen stock registrado.",
                    "La fila de seis tarjetas —Total SKUs, Pedir YA, Pedir pronto, OK, "
                    "Sobrestock y Valor inventario— también funciona como filtro: al hacer "
                    "clic en una, la tabla muestra solo esa señal.",
                    "Si tienes dos o más bodegas, el selector de bodega va justo debajo; al "
                    "elegir una, la tabla principal se reemplaza por el semáforo de esa "
                    "bodega.",
                    "En la barra de la tabla hay un buscador por SKU, nombre o proveedor, y a "
                    "la derecha cuántos SKUs quedan tras el filtro.",
                    "La tabla lista un producto por fila: Señal, SKU / Nombre, Stock, "
                    "Tendencia, Cobertura, Dem. (LT), Cantidad a pedir, Entrega (días), MOQ, "
                    "ABC-XYZ y Valor bodega.",
                    "La flecha ▶ del inicio de la fila abre «Cómo se calculó esta "
                    "recomendación»: la resta paso a paso, desde las ventas diarias promedio "
                    "hasta la cantidad final.",
                    "Los iconos del final de la fila abren el simulador de escenarios (tiempo "
                    "de entrega, variación de demanda y stock extra) y el editor del producto, "
                    "donde se cambian nombre, categoría, stock, proveedor, tiempo de entrega, MOQ, "
                    "costo, precio de venta y nivel de servicio.",
                    "Al pie de la pantalla están «Eventos y temporadas», para registrar "
                    "Black Friday o fin de año con su multiplicador, y la leyenda que explica "
                    "las cinco señales.",
                ],
                "fields": [
                    ("Señal",
                     "El semáforo del producto, calculado comparando su cobertura con su tiempo "
                     "de entrega: PEDIR_YA por debajo de medio tiempo de entrega, PEDIR_PRONTO "
                     "mientras no alcance su punto de reorden (lo que vende mientras llega el "
                     "pedido más el colchón), OK por encima, y SOBRESTOCK de 3 tiempos de entrega "
                     "en adelante (o el doble del punto de reorden, si es mayor). El medio tiempo "
                     "y los 3 tiempos son los valores de fábrica: se cambian en «Configurar "
                     "inventario», «Reglas del semáforo», para toda la empresa o por proveedor. "
                     "Sin datos aparece cuando falta el stock o el pronóstico."),
                    ("SKU / Nombre",
                     "El código del producto, el nombre que le pusiste y su proveedor."),
                    ("Stock",
                     "Unidades en bodega hoy. Dice «Sin registro» cuando nunca se cargó ese "
                     "dato."),
                    ("Tendencia",
                     "Miniatura de cómo se movió tu stock en los últimos 14 días."),
                    ("Cobertura",
                     "Cuánto te dura el stock actual al ritmo pronosticado. El encabezado "
                     "indica la unidad, que sigue el período de tu actualización."),
                    ("Dem. (LT)",
                     "Cuánto esperas vender mientras esperas que llegue el pedido."),
                    ("Cantidad a pedir",
                     "Lo que deberías pedir hoy: demanda durante el tiempo de entrega más colchón de "
                     "seguridad, menos el stock actual y menos lo que ya viene en camino; "
                     "nunca por debajo del MOQ. Se puede editar haciendo clic sobre el "
                     "número."),
                    ("No pedir / Aún no · al bajar a N",
                     "Lo que aparece en lugar de una cantidad cuando todavía no toca pedir. "
                     "«Aún no» indica a qué nivel de stock habrá que hacerlo."),
                    ("Entrega (días)",
                     "Días que tarda el proveedor. Lleva la etiqueta «estimado» mientras sea "
                     "el supuesto de StockAI (15 días); pasa a ser aprendido cuando registras "
                     "3 recepciones de ese proveedor."),
                    ("MOQ",
                     "Compra mínima por pedido. Nunca te recomendamos menos que ese número; "
                     "por encima pedimos lo que realmente hace falta (no es un múltiplo de "
                     "caja)."),
                    ("ABC-XYZ",
                     "A/B/C es importancia en ingresos y X/Y/Z qué tan predecible es la "
                     "demanda. AZ —valor alto y demanda errática— es el más delicado."),
                    ("Valor bodega",
                     "Stock actual por costo unitario. Solo aparece si registraste el costo."),
                    ("Cómo se calculó esta recomendación",
                     "El desglose que abre la flecha ▶: ventas diarias promedio × días de "
                     "entrega, + colchón de seguridad, − stock actual, = antes de redondear, "
                     "y el redondeo al MOQ cuando aplica. Debajo, cuando hay con qué comparar, "
                     "«Por qué cambió» dice si el cambio frente a la vez anterior vino de una "
                     "actualización nueva del pronóstico o de algo que se movió en tu propio "
                     "negocio — tu stock o tu tiempo de entrega."),
                    ("Simulador de escenarios",
                     "Deslizadores de tiempo de entrega, variación de demanda y stock extra que "
                     "muestran cómo cambiaría la cantidad recomendada. No guarda nada: es "
                     "para mirar."),
                    ("Vista Simple",
                     "La misma información reducida a cuatro columnas —producto, señal, "
                     "cantidad a pedir y proveedor— para leer rápido."),
                    ("Vista Proveedor",
                     "Agrupa los productos por proveedor, con cuántos urgentes y cuántos "
                     "próximos tiene cada uno."),
                    ("Vista Actualizar stock",
                     "Tabla editable de stock, días de entrega y proveedor para corregir muchos "
                     "productos seguidos y guardarlos de una vez. Solo para roles que pueden "
                     "editar."),
                    ("Exportar OC / Exportar OC (editada)",
                     "La primera baja la orden que calcula el servidor; la segunda baja la "
                     "orden con las cantidades que tú editaste en la tabla. Ambas registran "
                     "la orden en Pedidos."),
                    ("Plantilla / CSV / PDF",
                     "«Plantilla» baja un CSV vacío con las columnas correctas; el botón CSV "
                     "importa tu inventario (sku, current_stock, lead_time_days); PDF baja el "
                     "semáforo completo como informe."),
                    ("Registrar salida",
                     "Descuenta unidades que salieron de bodega por algo distinto a una venta "
                     "—rotura, vencimiento, consumo propio, obsequio o muestra— y acumula su "
                     "costo en el resumen de mermas."),
                    ("Eventos y temporadas",
                     "Temporadas altas con un multiplicador de demanda (×1.2 a ×3.0). En "
                     "cuanto guardas el evento cambia la cantidad a pedir de los productos "
                     "que le tocan — no es solo un ejercicio del simulador. Ver «Eventos que "
                     "mueven la recomendación» más adelante en este capítulo."),
                ],
                "tasks": [
                    ("Corregir el stock de un producto",
                     " 1. Búscalo por SKU o nombre en el buscador. 2. Pulsa el icono de lápiz "
                     "al final de la fila. 3. Cambia el «Stock actual» y cualquier otro dato "
                     "que esté mal. 4. Pulsa «Guardar». 5. La señal y la cantidad a pedir se "
                     "recalculan con el dato nuevo."),
                    ("Cargar todo tu inventario de una vez",
                     " 1. Pulsa «Plantilla» y abre el archivo que se descarga. 2. Llena las "
                     "columnas sku, current_stock y lead_time_days. 3. Pulsa el botón CSV y "
                     "elige tu archivo. 4. Revisa el aviso que dice cuántas filas se "
                     "importaron de cuántas."),
                    ("Ver de dónde sale una cantidad sugerida",
                     " 1. Pulsa la flecha ▶ al inicio de la fila del producto. 2. Lee la "
                     "resta paso a paso. 3. Fíjate en el origen del tiempo de entrega que aparece "
                     "junto a los días de entrega. 4. Si quieres probar otro escenario, abre "
                     "el simulador con el icono de deslizadores."),
                    ("Registrar una merma",
                     " 1. Pulsa «Registrar salida» en la barra superior. 2. Escribe el SKU y "
                     "elige el producto. 3. Escribe cuántas unidades salieron y elige el "
                     "motivo. 4. Confirma: el stock baja de inmediato y el costo queda "
                     "registrado."),
                ],
                "gotchas": [
                    "«Sin datos» no es un error de StockAI: es un producto al que nunca le "
                    "registraste el stock. Mientras esté así queda fuera de las "
                    "recomendaciones de compra, porque cuánto pedir depende justamente de "
                    "cuánto te queda.",
                    "Una fila puede decir «Pedir pronto» y a la vez «Aún no» en la cantidad. "
                    "No se contradicen: la señal avisa que el colchón está corto, y la "
                    "cantidad dice que todavía estás por encima del punto de reorden.",
                    "Dos productos con la misma cobertura pueden tener señales distintas. El "
                    "semáforo compara la cobertura contra el tiempo de entrega de cada producto, así "
                    "que 10 días de stock son cómodos con un proveedor de 5 días y críticos "
                    "con uno de 30.",
                    "Editar la cantidad directamente en la tabla no guarda nada en el "
                    "producto: ese número solo se usa si bajas «Exportar OC (editada)».",
                    "La vista «Actualizar stock» no aparece con rol de viewer. Es un editor, "
                    "no una vista, y guardar sería rechazado de todos modos.",
                ],
            },
            {
                "name": "Plata parada",
                "route": "/inventario",
                "image": "deadcapital",
                "purpose": (
                    "Es la lista de productos cuyo stock lleva mucho tiempo sin bajar, "
                    "ordenada por cuánto dinero representan, el más caro primero. No es "
                    "lo mismo que la señal SOBRESTOCK: esa señal compara tu cobertura "
                    "contra el tiempo de entrega del producto, así que un producto puede "
                    "estar «OK» o incluso «Pedir pronto» en el semáforo y aun así ser "
                    "plata que no se mueve hace meses, si la demanda simplemente se "
                    "detuvo sin que el stock llegara a cruzar el umbral de sobrestock. "
                    "Esta vista no necesita una actualización entrenada ni un pronóstico: "
                    "mira directamente el historial real de tu stock."
                ),
                "walkthrough": [
                    "Se abre desde la vista «Plata parada» del selector de Inventario.",
                    "Arriba hay un total en dinero —solo cuenta los productos con costo "
                    "registrado— y una nota de cuántos productos parados no tienen costo "
                    "y quedan fuera de ese total.",
                    "La tabla lista un producto por fila: nombre y SKU, proveedor, stock "
                    "actual, cuánto vale (o «Sin costo») y desde cuándo no baja.",
                    "Los productos con valor conocido van primero, del más caro al más "
                    "barato; los que no tienen costo van al final, ordenados por cuántos "
                    "días llevan quietos.",
                    "Cuando el historial guardado no alcanza para haber visto caer el "
                    "stock ni una vez, la cifra de días no es una fecha exacta: aparece "
                    "como «al menos N días», el piso de lo que se sabe, no una medición "
                    "cerrada.",
                    "Si tienes una actualización activa, cada fila trae también su señal "
                    "del semáforo, para comparar las dos lecturas sin salir de la "
                    "pantalla; sin ninguna actualización activa, la señal aparece como "
                    "«Sin sesión activa».",
                ],
                "fields": [
                    ("SKU / Nombre", "El producto, su proveedor y su categoría."),
                    ("Stock actual", "Unidades que tienes hoy en bodega."),
                    ("Vale", "Stock actual por costo unitario. Si el producto no tiene "
                     "costo registrado, no se muestra un cero: dice «Sin costo — no "
                     "sabemos cuánto vale»."),
                    ("Desde cuándo no baja", "Los días desde la última vez que el stock "
                     "de ese producto cayó. Cuando el historial guardado es corto, la "
                     "cifra viene con «al menos», porque es un piso, no una fecha "
                     "exacta."),
                    ("Señal", "El semáforo actual del producto, si tienes una "
                     "actualización activa. No condiciona nada de esta lista: un "
                     "producto puede aparecer aquí con cualquier semáforo, o sin "
                     "ninguno."),
                ],
                "tasks": [
                    (
                        "Encontrar dónde tienes más plata parada",
                        " 1. Abre la vista «Plata parada» en Inventario. 2. Mira el total "
                        "de arriba: solo suma los productos con costo registrado. 3. Baja "
                        "por la tabla: ya viene ordenada de más a menos dinero. 4. Si el "
                        "producto que te interesa dice «Sin costo», cárgale el costo en "
                        "su ficha para que entre al total.",
                    ),
                ],
                "gotchas": [
                    "Esta vista reemplaza a la antigua «Inmovilizado». Aquella "
                    "valoraba en cero un producto sin costo registrado y lo mandaba "
                    "al fondo de la lista, como si no hubiera plata en riesgo; por "
                    "eso se retiró y quedó una sola lista para esta pregunta.",
                    "Un producto sin costo registrado nunca cuenta como cero. Si "
                    "contara como cero se leería como «no hay plata en riesgo aquí», "
                    "que es lo contrario de lo que significa no tener el dato: se "
                    "muestra aparte y se cuenta aparte.",
                    "«Al menos N días» no es lo mismo que «N días». Aparece cuando el "
                    "historial guardado no alcanza para haber visto caer el stock ni "
                    "una vez, y es el piso de lo que se sabe, no una medición exacta.",
                    "No hace falta tener una actualización entrenada para ver esta "
                    "lista: se arma sola con el historial real de tu stock, así que un "
                    "producto que nunca metiste a un pronóstico igual puede aparecer "
                    "aquí.",
                ],
            },
            {
                "name": "Costos al alza y margen que se achica",
                "route": "/inventario",
                "image": "costalerts",
                "purpose": (
                    "Dos vistas que leen la misma fuente: lo que de verdad pagaste en "
                    "cada recepción de mercadería, no lo que dice la ficha del "
                    "proveedor. «Costos al alza» ordena a tus proveedores por cuánto "
                    "han subido sus precios; «Margen que se achica» ordena tus "
                    "productos por cuántos puntos perdió su margen. Las dos existen "
                    "para que una subida de costo no se te pase por revisarla producto "
                    "por producto."
                ),
                "walkthrough": [
                    "Se abren desde las vistas «Costos al alza» y «Margen que se "
                    "achica» del selector de Inventario.",
                    "Las dos leen solo recepciones que de verdad llegaron: un pedido "
                    "cotizado o rechazado no cuenta como evidencia de lo que pagaste.",
                    "«Costos al alza» agrupa por proveedor: cada fila trae cuánto subió "
                    "en total y, adentro, los productos que más empujaron esa subida.",
                    "«Margen que se achica» ordena tus productos del que más perdió "
                    "margen al que menos, con el margen de antes y el de ahora lado a "
                    "lado.",
                    "Arriba de «Margen que se achica» hay un control para el mínimo de "
                    "puntos de margen perdidos que quieres ver; por defecto medio "
                    "punto, para no llenar la lista de ruido.",
                    "Un aviso fijo explica la limitación de las dos vistas: StockAI guarda "
                    "solo tu precio de venta de HOY, nunca un historial de precios, así "
                    "que el margen «de antes» se arma con el precio de hoy y el costo "
                    "de entonces — nunca puede acusar a una baja de precio, solo a una "
                    "subida de costo.",
                ],
                "fields": [
                    ("Subió", "El porcentaje que ese proveedor, o ese producto, subió "
                     "de precio en la ventana que estás mirando, comparando la primera "
                     "recepción registrada con la más reciente."),
                    ("Productos más subidos", "Dentro de un proveedor, los que más "
                     "empujaron esa subida."),
                    ("Margen antes / Margen ahora", "El margen con el costo de la "
                     "primera recepción registrada y con el de la más reciente, los dos "
                     "usando tu precio de venta de HOY. No es un margen histórico real: "
                     "es lo que habría pasado si el precio nunca hubiera cambiado."),
                    ("Puntos perdidos", "La diferencia entre el margen de antes y el "
                     "de ahora, en puntos porcentuales. Solo entran a la lista los "
                     "productos que perdieron al menos el mínimo que elegiste arriba."),
                    ("Estás vendiendo bajo el costo", "Aviso que aparece cuando el "
                     "margen de ahora es negativo: el costo ya superó el precio de "
                     "venta."),
                ],
                "tasks": [
                    (
                        "Ver qué proveedor te ha subido más el precio",
                        " 1. Abre la vista «Costos al alza». 2. La tabla ya viene "
                        "ordenada por el proveedor que más subió. 3. Abre su fila para "
                        "ver qué productos empujaron esa subida. 4. Si vas a "
                        "renegociar, esos son los productos con los que empezar.",
                    ),
                    (
                        "Encontrar el producto cuyo margen se te está yendo",
                        " 1. Abre la vista «Margen que se achica». 2. Ajusta el mínimo "
                        "de puntos si la lista es muy larga o muy corta. 3. Compara "
                        "«Margen antes» con «Margen ahora» en la fila que te interese. "
                        "4. Si el margen ahora es negativo, la fila lo dice "
                        "explícitamente: estás vendiendo bajo costo.",
                    ),
                ],
                "gotchas": [
                    "Ambas vistas piden al menos dos recepciones registradas de ese "
                    "producto o proveedor dentro de la ventana. Con una sola no hay "
                    "con qué comparar, y el producto queda fuera en vez de mostrarse "
                    "con un cambio inventado.",
                    "«Margen que se achica» nunca dice que bajaste el precio, aunque "
                    "eso también explicaría un margen menor. StockAI no guarda tus "
                    "precios pasados, solo tus costos pasados, así que solo puede "
                    "acusar al costo — nunca al precio, porque no tiene cómo "
                    "probarlo.",
                    "Un producto sin precio de venta registrado, o sin suficiente "
                    "historial de costo, queda fuera de «Margen que se achica» y se "
                    "cuenta aparte, no se descarta en silencio.",
                    "El margen que ves aquí puede salir negativo, y se muestra tal "
                    "cual: la pantalla no lo recorta a cero para que se vea mejor.",
                ],
            },
            {
                "name": "Costo de ignorar",
                "route": "/inventario",
                "image": "ignoring",
                "purpose": (
                    "Es el registro de lo que StockAI te recomendó cada día y qué pasó "
                    "después. Por cada producto que estuvo en PEDIR_YA o PEDIR_PRONTO, "
                    "dice si terminaste pidiéndolo, si probablemente te quedaste sin "
                    "stock por no pedirlo, o si no hay forma de saberlo con lo que "
                    "tienes registrado — y esa tercera opción no es un cero: es que "
                    "falta el dato para afirmar cualquier cosa."
                ),
                "walkthrough": [
                    "Se abre desde la vista «Costo de ignorar» del selector de "
                    "Inventario.",
                    "Lista un producto por fila con el resultado de cada alerta que le "
                    "saliste: pediste a tiempo, probable quiebre de stock, o no se "
                    "puede saber.",
                    "«Probable quiebre de stock» solo aparece cuando StockAI vio de "
                    "verdad tu stock llegar a cero después de la alerta, nunca porque "
                    "lo suponga.",
                    "Cuando el quiebre todavía seguía abierto al final de la ventana "
                    "que estás mirando, la pantalla lo marca como un mínimo: lo que "
                    "perdiste hasta ahí, no lo que vas a perder en total.",
                    "«No se puede saber» aparece cuando ni generaste la orden a tiempo "
                    "ni viste el stock llegar a cero: no hay evidencia para decir que "
                    "ignorar la alerta te costó algo, y tampoco la hay para lo "
                    "contrario.",
                    "Las unidades y el valor perdidos usan tu venta diaria promedio y "
                    "tu precio de venta de HOY; si falta cualquiera de los dos, la "
                    "fila lo dice en vez de adivinar un número.",
                ],
                "fields": [
                    ("Pediste a tiempo", "Generaste una orden de compra para ese "
                     "producto dentro de los 14 días siguientes a la alerta."),
                    ("Probable quiebre de stock", "No generaste la orden a tiempo, y "
                     "StockAI vio tu stock llegar a cero después. Las unidades y el valor "
                     "perdidos vienen calculados, nunca en cero."),
                    ("No se puede saber", "No generaste la orden a tiempo, pero "
                     "tampoco viste el stock llegar a cero. No es que no haya pasado "
                     "nada: es que no hay evidencia para afirmarlo ni para "
                     "descartarlo."),
                    ("Precio desconocido", "Aparece cuando sí se sabe cuántas "
                     "unidades perdiste pero no su valor, porque el producto no tiene "
                     "precio de venta registrado."),
                ],
                "tasks": [
                    (
                        "Ver qué te costó no pedir a tiempo",
                        " 1. Abre la vista «Costo de ignorar». 2. Busca las filas "
                        "marcadas «Probable quiebre de stock». 3. Revisa las unidades "
                        "y el valor perdido de cada una. 4. Si alguna dice que es un "
                        "mínimo, el quiebre seguía abierto al final de la ventana: lo "
                        "real es igual o mayor.",
                    ),
                ],
                "gotchas": [
                    "«No se puede saber» no es una forma disimulada de decir cero. "
                    "Es la respuesta honesta cuando no hay ni una orden ni un quiebre "
                    "de stock observado: falta evidencia, en cualquiera de los dos "
                    "sentidos.",
                    "El valor perdido es venta, no margen: unidades por precio de "
                    "venta de hoy, no lo que realmente habrías ganado.",
                    "Esta pantalla usa el precio de venta de HOY para valorar un "
                    "quiebre pasado, porque StockAI no guarda un historial de precios. "
                    "Inventarse el precio de entonces sería justo el tipo de número "
                    "que esta pantalla existe para evitar.",
                    "Un producto sin venta diaria promedio registrada no puede tener "
                    "unidades perdidas calculadas, y la fila lo dice en vez de "
                    "mostrar un cero.",
                ],
            },
            {
                "name": "Eventos que mueven la recomendación",
                "route": "/inventario",
                "image": "events",
                "purpose": (
                    "Declarar una temporada alta — Semana Santa, Black Friday, tu "
                    "propia fecha de campaña — no es un ejercicio: en cuanto la "
                    "guardas, cambia lo que StockAI te dice que pidas, todos los días, "
                    "hasta que la fecha pase. El multiplicador que escribes no se "
                    "aplica entero de un día para otro: se reparte según cuánto de tu "
                    "tiempo de entrega cae dentro de las fechas del evento."
                ),
                "walkthrough": [
                    "Se declara al pie de Inventario, en «Eventos y temporadas»: "
                    "nombre, fecha de inicio, fecha de fin y un multiplicador de "
                    "demanda, con un SKU o una categoría opcional.",
                    "En cuanto guardas el evento, empieza a mover la cantidad a pedir "
                    "de los productos que le tocan — no solo el resultado del "
                    "Simulador de escenarios.",
                    "El efecto depende de cuánto del tiempo de entrega de cada "
                    "producto cae dentro de las fechas del evento: si tu proveedor "
                    "tarda 15 días y el evento cubre 4 de esos días, no recibes el "
                    "multiplicador completo, sino una fracción de él.",
                    "Por ejemplo: un evento de x1.8 que cubre 4 de los 15 días de "
                    "tiempo de entrega de un producto no multiplica su demanda por "
                    "1.8, sino por cerca de 1.21 — la parte del camino que el evento "
                    "realmente alcanza a cubrir.",
                    "Cuando la fila de un producto cambió por un evento, «Por qué "
                    "cambió» te lo dice con el cálculo exacto: el nombre del evento, "
                    "el multiplicador y sobre cuántos de los días del tiempo de "
                    "entrega se aplicó.",
                    "Varios eventos que se solapan en el mismo producto se combinan "
                    "entre sí, no se reemplazan uno al otro.",
                ],
                "fields": [
                    ("Multiplicador de demanda", "Cuánto más vas a vender durante el "
                     "evento. ×1.8 es 80% más. La pantalla lo clasifica de leve a "
                     "pico según qué tan alto sea."),
                    ("Por qué cambió (evento)", "Cuando el cambio de una fila viene "
                     "de un evento, la frase trae su nombre, el multiplicador y sobre "
                     "cuántos de los días de tiempo de entrega se aplicó — por "
                     "ejemplo, «Semana Santa: x1.8 sobre 4 de los 15 días»."),
                ],
                "tasks": [
                    (
                        "Preparar el inventario para una temporada alta",
                        " 1. Ve al pie de Inventario y abre «Eventos y temporadas». "
                        "2. Crea el evento con su nombre, sus fechas y el "
                        "multiplicador que esperas. 3. Vuelve al semáforo: los "
                        "productos que le tocan ya piden más, en proporción a cuánto "
                        "del evento cae dentro de su tiempo de entrega. 4. Si quieres "
                        "ver el efecto antes de guardarlo, usa el Simulador de "
                        "escenarios con una regla de «Promoción» en las mismas "
                        "fechas.",
                    ),
                ],
                "gotchas": [
                    "El multiplicador que escribes no se aplica completo salvo que el "
                    "evento cubra todo el tiempo de entrega del producto. El mismo "
                    "evento de una semana mueve mucho menos la recomendación de un "
                    "proveedor de 30 días que la de uno de 5.",
                    "Esto es distinto del Simulador de escenarios: una regla de "
                    "simulación no toca nada hasta que la borras; un evento guardado "
                    "en «Eventos y temporadas» sí cambia lo que ves en el semáforo "
                    "todos los días, mientras dure.",
                    "Si dos eventos se solapan sobre el mismo producto, sus "
                    "multiplicadores se combinan; no gana el más alto.",
                ],
            },
            {
                "name": "El pronóstico en plata",
                "route": "/inventario",
                "image": "forecastmoney",
                "purpose": (
                    "Multiplica lo que el pronóstico dice que vas a vender por tu "
                    "precio y tu costo de hoy, para dar una cifra en dinero en vez de "
                    "solo en unidades: cuánto vas a vender, cuánto margen te va a "
                    "dejar, y qué productos concentran esa plata."
                ),
                "walkthrough": [
                    "Se abre desde la vista «Pronóstico en plata» del selector de "
                    "Inventario, y necesita una actualización activa.",
                    "Arriba hay tres cifras: ventas proyectadas, margen proyectado y "
                    "cuántos productos entraron en la cuenta.",
                    "Debajo, un aviso fijo recuerda que la proyección usa tu precio de "
                    "venta de HOY, porque StockAI no guarda un historial de precios y no "
                    "sabe cuál será el precio en el futuro.",
                    "La tabla lista un producto por fila —unidades pronosticadas, "
                    "ventas, costo, margen y margen porcentual— ordenada del que más "
                    "margen aporta al que menos.",
                    "Un aviso arriba de la tabla dice qué porcentaje del margen "
                    "proyectado explican los diez productos que más aportan.",
                    "Si un producto no tiene precio o costo registrado, no "
                    "desaparece de la tabla: se queda con esa celda marcada como "
                    "desconocida, y se cuenta aparte en el pie de la pantalla.",
                ],
                "fields": [
                    ("Ventas proyectadas", "Unidades que el pronóstico espera que "
                     "vendas en lo que queda del horizonte, multiplicadas por tu "
                     "precio de venta de hoy."),
                    ("Margen proyectado", "Lo mismo, pero con el margen unitario de "
                     "cada producto en vez del precio completo."),
                    ("Productos pronosticados", "Cuántos productos entraron en la "
                     "cuenta. Los que no tienen pronóstico, precio o costo no "
                     "entran, y se cuentan aparte."),
                    ("Unidades pronosticadas", "La suma del pronóstico de ese "
                     "producto para lo que queda del horizonte de esta "
                     "actualización, nunca negativa."),
                    ("Margen / Margen %", "Igual que en el resto de la app: si falta "
                     "el precio o el costo, la celda dice que no se sabe, nunca un "
                     "cero."),
                ],
                "tasks": [
                    (
                        "Ver qué productos concentran tu plata futura",
                        " 1. Abre la vista «Pronóstico en plata». 2. Lee la frase de "
                        "arriba de la tabla: te dice qué parte del margen explican "
                        "los diez primeros. 3. Revisa esos diez productos primero: "
                        "son los que más te conviene no dejar sin stock.",
                    ),
                ],
                "gotchas": [
                    "La cifra usa el precio y el costo de HOY, no uno futuro. Si vas "
                    "a subir un precio, o tu proveedor te va a subir el costo, esta "
                    "pantalla todavía no lo sabe.",
                    "Un producto sin precio o sin costo no se descarta: se cuenta "
                    "aparte para que el total no parezca completo cuando no lo es.",
                    "El margen puede salir negativo, y se muestra así, sin "
                    "recortarlo a cero.",
                ],
            },
            {
                "name": "Pedidos",
                "route": "/pedidos",
                "image": "pedidos",
                "purpose": (
                    "Aquí vive todo lo que ya pediste. Cada orden que generaste queda "
                    "registrada con lo que pediste, a quién y cuándo; desde esta pantalla la "
                    "envías al proveedor y registras la llegada de la mercadería. Registrar "
                    "la llegada es lo que actualiza tu stock y lo que le enseña a StockAI cuánto "
                    "tarda de verdad cada proveedor."
                ),
                "walkthrough": [
                    "El encabezado dice «Pedidos» con el subtítulo «Órdenes generadas y "
                    "registro de llegadas».",
                    "A la derecha aparece el contador «N por recibir» y el botón «Nueva "
                    "orden», que abre el formulario de orden manual.",
                    "Si tienes dos o más bodegas, debajo hay dos pestañas: «Órdenes de "
                    "compra» y «Transferencias».",
                    "Antes de la tabla se muestran los avisos de proveedores: los que no "
                    "tienen email ni WhatsApp registrados y tienen órdenes abiertas, y los "
                    "que se están tardando más que su propio historial.",
                    "Si nunca has generado una orden, en lugar de la tabla verás una pantalla "
                    "de bienvenida con el botón «Ir al Panel de Compras».",
                    "La tabla lista una orden por fila, de la más reciente a la más antigua, "
                    "con su número, fecha, cantidad de SKUs, urgentes, próximos, unidades y "
                    "valor total.",
                    "La última columna es la de recepción: una etiqueta de estado y, a su "
                    "lado, los botones «Registrar llegada», «Enviar pedido», «Enviarme por "
                    "WhatsApp», «Abrir en WhatsApp» y «Copiar mensaje».",
                    "«Registrar llegada» abre una ventana con las líneas de la orden: para "
                    "cada producto ves lo pedido, lo recibido antes, y escribes lo que llega "
                    "ahora.",
                    "Esa ventana se cierra con «Guardar cantidades», si llegó parte, o con "
                    "«Llegó todo completo», si llegó la orden entera.",
                    "«Enviar pedido» primero te muestra a qué proveedores se va a enviar y a "
                    "cuáles se va a omitir por falta de contacto, y recién entonces pide "
                    "confirmación.",
                    "«Enviarme por WhatsApp», «Abrir en WhatsApp» y «Copiar mensaje» te "
                    "entregan a ti el texto de la orden para que lo reenvíes tú; no dependen "
                    "de que el proveedor esté configurado.",
                    "«Nueva orden» abre un formulario donde eliges proveedor, bodega de "
                    "destino y escribes las líneas a mano: SKU, cantidad y costo unitario. No "
                    "necesita ningún pronóstico.",
                ],
                "fields": [
                    ("N por recibir",
                     "Cuántas órdenes siguen esperando mercadería. Cuenta las que están en "
                     "camino, las parciales y las marcadas como «No llegó»."),
                    ("Orden",
                     "El número de la orden de compra, tal como quedó al generarse."),
                    ("Fecha y hora",
                     "Cuándo se generó la orden."),
                    ("SKUs en la orden",
                     "Cuántos productos distintos lleva la orden."),
                    ("Urgentes",
                     "Cuántas de sus líneas venían con señal PEDIR_YA cuando se generó."),
                    ("Próximos",
                     "Cuántas de sus líneas venían con señal PEDIR_PRONTO."),
                    ("Unidades totales",
                     "La suma de unidades pedidas en toda la orden."),
                    ("Valor total",
                     "La suma de cantidad por costo unitario. Muestra «—» si las líneas no "
                     "traían costo."),
                    ("Recepción: En camino / Parcial / Recibida / No llegó",
                     "El estado de la mercadería. «En camino» es también el estado de una "
                     "orden a la que nunca se le registró nada: la ausencia de registro no "
                     "prueba que haya llegado."),
                    ("Registrar llegada",
                     "Abre la ventana de recepción. Aparece mientras la orden esté en camino "
                     "o parcial."),
                    ("Pedido / Recibido antes / Llega ahora",
                     "Las tres columnas de la ventana de recepción: lo que pediste, lo que ya "
                     "habías registrado y lo que estás recibiendo en este momento. El campo "
                     "viene precargado con lo que falta."),
                    ("Guardar cantidades / Llegó todo completo",
                     "El primero registra exactamente lo que escribiste línea por línea; el "
                     "segundo da por recibida la orden entera sin escribir nada."),
                    ("Enviar pedido",
                     "Manda la orden por email o WhatsApp a los proveedores de sus líneas. "
                     "Antes de enviar te muestra quién la recibirá y quién quedará omitido."),
                    ("Enviarme por WhatsApp / Abrir en WhatsApp / Copiar mensaje",
                     "Te entregan a ti el texto de la orden: a tu WhatsApp, abriendo WhatsApp "
                     "con el mensaje listo, o al portapapeles."),
                    ("Nueva orden",
                     "Formulario de orden manual: proveedor, bodega de destino y una línea "
                     "por producto con SKU, cantidad y costo unitario opcional."),
                    ("Pestaña Transferencias",
                     "Los traslados de stock entre tus bodegas, con su propio ciclo de envío "
                     "y recepción. Solo aparece con dos o más bodegas."),
                ],
                "tasks": [
                    ("Registrar que llegó todo el pedido",
                     " 1. Busca la orden en la tabla. 2. Pulsa «Registrar llegada» en la "
                     "columna de recepción. 3. Revisa que las líneas sean las correctas. "
                     "4. Pulsa «Llegó todo completo». 5. El stock de cada producto sube y la "
                     "orden pasa a «Recibida»."),
                    ("Registrar una llegada parcial",
                     " 1. Pulsa «Registrar llegada» en la orden. 2. En «Llega ahora», escribe "
                     "las unidades que realmente llegaron de cada producto. 3. Deja en cero "
                     "las que no llegaron. 4. Pulsa «Guardar cantidades». 5. La orden queda "
                     "como «Parcial» y conserva el botón para registrar el resto cuando "
                     "llegue."),
                    ("Enviar una orden a sus proveedores",
                     " 1. Pulsa «Enviar pedido» en la fila de la orden. 2. Lee el resumen: a "
                     "quién se enviará y a quién se omitirá. 3. Pulsa «Enviar» para "
                     "confirmar. 4. Para los proveedores omitidos, usa «Copiar mensaje» y "
                     "mándaselo por tu cuenta."),
                    ("Crear una orden manual",
                     " 1. Pulsa «Nueva orden». 2. Elige el proveedor y, si tienes varias "
                     "bodegas, la de destino. 3. Escribe el SKU, la cantidad y —si la "
                     "tienes— el costo unitario de cada línea. 4. Pulsa «Agregar producto» "
                     "para las líneas siguientes. 5. Pulsa «Crear orden»."),
                ],
                "gotchas": [
                    "Marcar «No llegó» no cierra la orden. Es un dato sobre una entrega que "
                    "no ocurrió, no sobre una orden cancelada, así que la orden sigue "
                    "contando en «por recibir».",
                    "Registrar la llegada no es papeleo: es lo que sube tu stock y lo que le "
                    "enseña a StockAI el tiempo de entrega real de ese proveedor. Con 3 recepciones "
                    "registradas StockAI reemplaza el tiempo de entrega configurado por el promedio "
                    "real.",
                    "«Enviar pedido» omite en silencio a los proveedores sin email ni "
                    "WhatsApp en su ficha; por eso la confirmación te los nombra antes. Para "
                    "esos, la salida es reenviar tú el mensaje.",
                    "Una orden sin proveedor asignado en ninguna de sus líneas no se puede "
                    "enviar a nadie, y la confirmación te lo dice antes de intentarlo.",
                    "Con rol de viewer no verás «Nueva orden» ni podrás registrar llegadas: "
                    "ambas escriben datos del negocio.",
                ],
            },
            {
                "name": "Mensajes",
                "route": "/mensajes",
                "image": "mensajes",
                "purpose": (
                    "Mensajería uno a uno entre las personas de tu empresa, dentro de StockAI. "
                    "Sirve para lo que ocurre alrededor de una decisión de compra —«ya "
                    "confirmé con el proveedor», «esa cantidad la bajé a la mitad»— sin "
                    "salir a otra aplicación ni perder el contexto."
                ),
                "walkthrough": [
                    "La columna izquierda empieza con el título «Mensajes» y, a su derecha, "
                    "cuántas personas hay en tu equipo.",
                    "Debajo está el buscador de personas, que siempre está a la vista y no "
                    "mueve la lista al usarlo.",
                    "Luego viene la lista de conversaciones, ordenada por la más reciente: "
                    "inicial de la persona, su nombre, la hora del último mensaje y una "
                    "vista previa de ese mensaje.",
                    "Cuando tienes mensajes sin leer de alguien, su fila se muestra en "
                    "negrita y con un contador redondo a la derecha.",
                    "Al escribir en el buscador aparece además el bloque «Escribirle por "
                    "primera vez» con los colegas con los que todavía no has hablado.",
                    "La zona derecha, mientras no elijas a nadie, dice «Elige una "
                    "conversación».",
                    "Al abrir una conversación, arriba queda el nombre de la persona; en "
                    "pantallas angostas aparece además una flecha para volver a la lista.",
                    "Los mensajes se leen de arriba abajo: los tuyos alineados a la derecha "
                    "y en color de acento, los de la otra persona a la izquierda, cada uno "
                    "con su hora.",
                    "Una conversación nueva se abre con la frase «Este es el inicio de la "
                    "conversación».",
                    "Abajo está el campo «Escribe un mensaje…»: Enter envía, y el mensaje "
                    "admite hasta 4.000 caracteres.",
                    "Al abrir la conversación, los mensajes que te habían enviado quedan "
                    "marcados como leídos y el contador desaparece.",
                    "En la barra superior de la aplicación hay un sobre con el total de "
                    "mensajes sin leer, que te trae de vuelta a esta pantalla desde "
                    "cualquier parte.",
                ],
                "fields": [
                    ("N en tu equipo",
                     "Cuántas personas activas hay en tu empresa a las que puedes "
                     "escribirles."),
                    ("Buscar persona…",
                     "Filtra a la vez tus conversaciones y el resto de tus colegas, por "
                     "nombre o por correo."),
                    ("Fila de conversación",
                     "Inicial, nombre, hora del último mensaje y su vista previa. Si el "
                     "último mensaje es tuyo, la vista previa empieza con «Tú:»."),
                    ("Contador de no leídos",
                     "El círculo con un número a la derecha de la fila: cuántos mensajes de "
                     "esa persona no has abierto."),
                    ("Escribirle por primera vez",
                     "Bloque que solo aparece mientras buscas, con los colegas con los que "
                     "todavía no tienes conversación."),
                    ("Elige una conversación",
                     "El estado inicial del panel derecho, mientras no has abierto ningún "
                     "hilo."),
                    ("Este es el inicio de la conversación",
                     "Lo que dice un hilo recién abierto, sin mensajes todavía."),
                    ("Escribe un mensaje…",
                     "El campo de escritura. Enter envía; el límite es de 4.000 caracteres "
                     "por mensaje."),
                    ("Botón de enviar",
                     "El botón redondo junto al campo. Queda deshabilitado mientras el "
                     "mensaje esté vacío o se esté enviando."),
                    ("Sobre de la barra superior",
                     "El indicador global de mensajes sin leer; muestra «99+» cuando pasan "
                     "de noventa y nueve."),
                    ("Recibir aviso cuando te escriban",
                     "Interruptor que vive en Mi cuenta, no aquí. Cuando está encendido y "
                     "tienes tu número vinculado, recibes un aviso por WhatsApp (o SMS si "
                     "WhatsApp no está disponible) si te escriben mientras no estás en "
                     "StockAI."),
                ],
                "tasks": [
                    ("Escribirle a alguien por primera vez",
                     " 1. Escribe su nombre o su correo en el buscador. 2. Búscalo bajo "
                     "«Escribirle por primera vez». 3. Haz clic en su nombre. 4. Escribe el "
                     "mensaje y pulsa Enter."),
                    ("Retomar una conversación y marcarla como leída",
                     " 1. Busca la fila en negrita con el contador de no leídos. 2. Haz clic "
                     "en ella. 3. Al abrirse, los mensajes quedan marcados como leídos y el "
                     "contador desaparece, también el del sobre de arriba."),
                    ("Activar el aviso por WhatsApp",
                     " 1. Ve a Mi cuenta. 2. Vincula tu número de WhatsApp si todavía no lo "
                     "hiciste. 3. Busca «Mensajes del equipo» y enciende «Recibir aviso "
                     "cuando te escriban»."),
                ],
                "gotchas": [
                    "No hay grupos ni canales: solo conversaciones de una persona con otra, "
                    "y siempre dentro de tu misma empresa.",
                    "La lista de colegas con los que no has hablado solo aparece mientras "
                    "escribes en el buscador. Sin buscar, la columna muestra únicamente tus "
                    "conversaciones.",
                    "La pantalla se actualiza sola —la lista cada 15 segundos y el hilo "
                    "abierto cada 5— así que un mensaje nuevo puede tardar unos segundos en "
                    "aparecer. No hay «escribiendo…» ni confirmación de lectura para quien "
                    "envía.",
                    "El aviso por WhatsApp viene apagado y necesita tu número vinculado en "
                    "Mi cuenta. Además solo se dispara si no estás en la conversación: si "
                    "acabas de leer a esa persona, no te vuelve a avisar.",
                    "Los usuarios con rol de viewer también pueden escribir y recibir "
                    "mensajes. El límite aquí es la empresa, no el rol.",
                ],
            },
        ],
    },
    "en": {
        "title": "Daily operation",
        "intro": (
            "These four screens are your everyday routine: you open the Purchasing Panel "
            "in the morning to decide what to order, go into Inventory when you need to "
            "check or fix a particular product, pass through Orders when goods arrive, and "
            "use Messages to coordinate with your team without leaving StockAI. The first "
            "three work on the same data — the demand forecast and the stock you have on "
            "record — so what you fix in one shows up in the others."
        ),
        "screens": [
            {
                "name": "Purchasing Panel",
                "route": "/compras",
                "image": "panel",
                "purpose": (
                    "This is the screen you start the day with. It takes the demand forecast "
                    "and the stock you have on record and hands you a short list of "
                    "decisions: which products to order today, which ones can wait until "
                    "this week, how much of each, and from which supplier. You approve or "
                    "reject line by line and generate the purchase order at the end."
                ),
                "walkthrough": [
                    "Top left is the “Purchasing Dashboard” badge with the subtitle "
                    "“Your upcoming purchases, calculated.”, and below it the "
                    "greeting with your name and how many actions are pending today.",
                    "Under the greeting sit the date and “Data in use”, the name of "
                    "the sales upload everything you see was computed from; on the right, the "
                    "indicator of how fresh that data is.",
                    "If your stock or your sales have gone a long time without an update, a "
                    "notice appears before anything else: the traffic light below was "
                    "computed on stale data and cannot be presented as trustworthy.",
                    "The assumptions notice (“These recommendations use N assumptions of "
                    "ours”) shows up when part of the calculation used values you never "
                    "gave us, and links to what to configure first.",
                    "Then come the supplier warnings: suppliers running later than usual, and "
                    "orders whose arrival date has already passed (“Did these orders "
                    "arrive?”) with a “Record arrival” button.",
                    "The indicator row shows Total SKUs monitored, Risk today, This week, "
                    "Average accuracy and Warehouse value, with a note underneath when some "
                    "products have no stock count.",
                    "“Executive summary of the day” is a paragraph explaining the "
                    "situation in words; it has a button to generate it again.",
                    "If you have more than one warehouse, transfer suggestions appear before "
                    "the purchases: moving stock between your own warehouses is free, so it "
                    "is proposed before buying.",
                    "“Urgent — act today” lists the products with the PEDIR_YA "
                    "signal and “This week” the PEDIR_PRONTO ones. Each card "
                    "carries the name, the signal, the SKU, the reason, the supplier, the "
                    "quantity and the “Approve” and “Reject” buttons.",
                    "“Why?” opens the card's breakdown: current coverage, "
                    "forecasted daily demand, supplier lead time, service level, unit cost, "
                    "MOQ, current stock and reorder point.",
                    "As soon as you approve the first line, the cart bar appears at the "
                    "bottom with the number of approved products, the total, the margin the "
                    "order protects and the “Download purchase order” button.",
                    "Further down are “Suggested purchases and transfers” (the "
                    "optimiser's plan for the whole horizon), “Get ahead — upcoming "
                    "demand peaks”, “Demand changes”, “System "
                    "recommendations” and “Capital opportunities”."
                ],
                "fields": [
                    ("Total SKUs monitored",
                     "How many products the sales upload in use covers."),
                    ("Risk today",
                     "How many products carry the PEDIR_YA signal. It shows “—” "
                     "when no product has stock on file, because a zero then does not mean "
                     "“no risk” but “we do not know”."),
                    ("This week",
                     "How many products carry the PEDIR_PRONTO signal."),
                    ("Average accuracy",
                     "How close the forecast was to actual sales in testing, measured on data "
                     "the model did not see during training. 85% or more is good; below 70% "
                     "it is worth reviewing that product's history."),
                    ("Warehouse value",
                     "Current stock times unit cost, summed. It shows “—” if "
                     "you have not recorded a cost for any product: the value is unknown, not "
                     "zero."),
                    ("Why?",
                     "Opens the card's breakdown with the six to eight values the "
                     "recommendation was built on. All of them come computed from the server; "
                     "the screen invents none of them."),
                    ("Current coverage",
                     "How long the stock you have lasts at the forecasted sales rate. The "
                     "unit follows your update's period (days in a daily update)."),
                    ("Supplier lead time",
                     "Days between placing the order and it reaching your warehouse. If it "
                     "carries the “estimated” tag, it is StockAI's assumption (15 "
                     "days) because nobody has configured it yet."),
                    ("Service level",
                     "The probability you want to cover demand with during the lead time. If "
                     "you have not configured it, StockAI uses 95% and marks it as estimated. "
                     "Measured limit: on intermittent products (many days with no sales) the "
                     "cushion holds about half the time, not 95%; on those products the panel "
                     "says so next to the percentage."),
                    ("Unit cost / MOQ",
                     "The cost you recorded for that product and the minimum order the "
                     "supplier requires. If you did not provide them, they are marked as "
                     "estimated (the default minimum order is 1)."),
                    ("Reorder point",
                     "The stock level at which you must order so the goods arrive before you "
                     "hit zero: demand during the lead time plus the safety buffer."),
                    ("Order:",
                     "The suggested quantity. You can click the number and type another one; "
                     "changing it marks the line as modified and puts it in the cart."),
                    ("Supplier:",
                     "Dropdown to point that line at a different supplier before the order is "
                     "generated. Changing it also marks the line as modified."),
                    ("Approve / Reject / Undo / Restore",
                     "Approve puts the line in the cart; Reject takes it out and records that "
                     "you did not follow the recommendation; Undo and Restore reverse each of "
                     "the two."),
                    ("This order protects … in sales with … in margin",
                     "Sales and margin of the approved lines that have both a sale price and "
                     "a cost on file. Lines missing either are left out and reported "
                     "separately, so the figure is neither inflated nor deflated."),
                    ("Deliver to",
                     "The order's destination warehouse. Only appears if you have two or more "
                     "warehouses."),
                    ("Download purchase order",
                     "Downloads the order's CSV and, at the same time, records the order in "
                     "StockAI so it shows up in Orders and its arrival can be logged."),
                    ("Send to suppliers now",
                     "Appears right after the order is generated. Sends the order by email or "
                     "WhatsApp to every supplier that has contact details on their record."),
                    ("Suggested purchases and transfers",
                     "The optimiser's plan: what to buy and what to move to cover your whole "
                     "planning horizon at the lowest total cost. “Convert to PO” "
                     "turns one line into a purchase order without going through the cart."),
                    ("Get ahead — upcoming demand peaks",
                     "Peaks the forecast sees before the traffic light turns red, with the "
                     "deadline to order based on the supplier's lead time."),
                    ("Capital opportunities",
                     "How much money you have tied up in products with excessive coverage, "
                     "and which ones they are."),
                    ("Last updated / Refresh data",
                     "How long ago what you see was loaded, and the button to request it "
                     "again."),
                ],
                "tasks": [
                    ("Generate today's purchase order",
                     " 1. Go through the cards under “Urgent — act today” and "
                     "“This week”. 2. On each one, adjust the quantity or the "
                     "supplier if needed. 3. Press “Approve” on the ones you will "
                     "order and “Reject” on the ones you will not. 4. Check the "
                     "cart bar at the bottom: approved products, total and protected margin. "
                     "5. If you have several warehouses, pick one under “Deliver "
                     "to”. 6. Press “Download purchase order”: the CSV "
                     "downloads and the order is recorded in Orders."),
                    ("Change the quantity StockAI suggests",
                     " 1. Click the number next to “Order:”. 2. Type the quantity "
                     "you are going to order. 3. Press Enter or click outside the field. "
                     "4. The line is marked as modified and goes into the cart with your "
                     "quantity."),
                    ("Send the order to the supplier",
                     " 1. Generate the order with “Download purchase order”. 2. In "
                     "the “Purchase order generated” panel, check which lines go to "
                     "which supplier. 3. Press “Send to suppliers now”. 4. If a "
                     "supplier is listed as skipped for missing contact details, use "
                     "“Send to my WhatsApp” or “Copy message” and forward "
                     "it yourself."),
                    ("Record the arrival of a late order",
                     " 1. Find the “Did these orders arrive?” block near the top of "
                     "the screen. 2. Press “Record arrival” on the supplier's line. "
                     "3. In the window that opens, type how much of each product arrived. "
                     "4. Press “Everything arrived” if the whole order came in, or "
                     "“Save quantities” if it arrived partially."),
                    ("Understand why a product is recommended",
                     " 1. On the product's card, press “Why?”. 2. Read the "
                     "explanation sentence and go through the breakdown values. 3. Notice "
                     "which ones carry the “estimated” tag: those are our "
                     "assumptions, not your data. 4. If any of them is wrong, fix it in "
                     "Inventory and reload this screen."),
                ],
                "gotchas": [
                    "“Download purchase order” does two things at once: it "
                    "downloads the file and records the order. If the recording fails you "
                    "will see an error notice: in that case you have the CSV but the order "
                    "does NOT exist in StockAI, and you have to generate it again.",
                    "“Suggested purchases and transfers” usually asks for more "
                    "units than the cards above. That is not a contradiction: the cards "
                    "answer “what do I order today” and the optimiser answers "
                    "“how do I cover the whole planning horizon”.",
                    "A “Risk today” of zero does not always mean you are safe. If "
                    "your products have no stock on record, the indicator shows "
                    "“—” and a note: no risk was detected because nothing was "
                    "measured.",
                    "With the viewer role you see every recommendation but cannot approve "
                    "them: instead of the buttons you get “Your role cannot generate "
                    "orders”.",
                    "The “estimated” tag next to a number is not an error. It means "
                    "StockAI chose that value because nobody configured it; as soon as you "
                    "record yours, it stops appearing.",
                ],
            },
            {
                "name": "Inventory",
                "route": "/inventario",
                "image": "inventory",
                "purpose": (
                    "This is the full list of your products with the stock traffic light. "
                    "Here you check product by product how much coverage is left, how much "
                    "should be ordered and where that number comes from; and here you fix "
                    "the data everything else rests on: current stock, supplier, lead time, "
                    "cost, sale price and minimum order."
                ),
                "walkthrough": [
                    "The header reads “Inventory” with the subtitle “Stock "
                    "signal · Purchase recommendations”, and the data-freshness "
                    "indicator on the right.",
                    "Next to it is the view switcher: Table, Simple, Provider, Update stock "
                    "(only if your role can edit), Money not moving, Rising "
                    "costs, Shrinking margin, Forecast in money and Cost of ignoring.",
                    "The toolbar holds the refresh button, the CSV import, "
                    "“Template”, “Export PO”, “Export PO "
                    "(edited)”, PDF, and the links to Impact, Suppliers and “Log "
                    "stock-out”.",
                    "If some products were left out of the forecast, a notice lists them with "
                    "the reason for each one.",
                    "Below that comes the plain-language situation line (“N product(s) "
                    "will run out before your next order arrives…”) and, when "
                    "relevant, the notice of how many SKUs have no stock on record.",
                    "The row of six cards — Total SKUs, Order NOW, Order soon, OK, "
                    "Overstock and Inventory value — also works as a filter: click one "
                    "and the table shows only that signal.",
                    "If you have two or more warehouses, the warehouse selector sits right "
                    "below; picking one replaces the main table with that warehouse's traffic "
                    "light.",
                    "The table's own bar has a search box for SKU, name or supplier, and on "
                    "the right how many SKUs are left after the filter.",
                    "The table lists one product per row: Signal, SKU / Name, Stock, Trend, "
                    "Coverage, Demand (LT), Qty to order, Lead time, MOQ, ABC-XYZ and "
                    "Warehouse value.",
                    "The ▶ arrow at the start of the row opens “How this "
                    "recommendation was calculated”: the subtraction step by step, from "
                    "average daily sales down to the final quantity.",
                    "The icons at the end of the row open the scenario simulator (lead time, "
                    "demand variation and extra stock) and the product editor, where you "
                    "change name, category, stock, supplier, lead time, MOQ, cost, sale price "
                    "and service level.",
                    "At the foot of the screen are “Events and seasons”, to record "
                    "Black Friday or year-end with their multiplier, and the legend explaining "
                    "the five signals.",
                ],
                "fields": [
                    ("Signal",
                     "The product's traffic light, computed by comparing its coverage against "
                     "its lead time: PEDIR_YA below half a lead time, PEDIR_PRONTO while it "
                     "has not reached its reorder point (what it sells while the order travels "
                     "plus the buffer), OK above that, and SOBRESTOCK from 3 lead times up (or "
                     "twice the reorder point, if larger). Half a lead time and 3 lead times "
                     "are the factory values: change them under “Set up inventory”, “Stock "
                     "signal rules”, for the whole company or per supplier. No data "
                     "appears when the stock or the forecast is missing."),
                    ("SKU / Name",
                     "The product code, the name you gave it and its supplier."),
                    ("Stock",
                     "Units on hand today. It says “No record” when that figure was "
                     "never loaded."),
                    ("Trend",
                     "A thumbnail of how your stock moved over the last 14 days."),
                    ("Coverage",
                     "How long your current stock lasts at the forecasted rate. The header "
                     "states the unit, which follows your update's period."),
                    ("Demand (LT)",
                     "How much you expect to sell while waiting for the order to arrive."),
                    ("Qty to order",
                     "What you should order today: demand over the lead time plus the safety "
                     "buffer, minus current stock and minus what is already on the way; never "
                     "below the MOQ. You can edit it by clicking the number."),
                    ("Don't order / Not yet · once it drops to N",
                     "What appears instead of a quantity when it is not time to order yet. "
                     "“Not yet” states the stock level at which it will be."),
                    ("Lead time",
                     "Days your supplier takes. It carries the “estimated” tag "
                     "while it is StockAI's assumption (15 days); it becomes learned once you "
                     "record 3 receptions from that supplier."),
                    ("MOQ",
                     "Minimum units per order. We never recommend less than that number; "
                     "above it we ask for what you actually need (it is not a pack "
                     "multiple)."),
                    ("ABC-XYZ",
                     "A/B/C is revenue importance and X/Y/Z is how predictable demand is. AZ "
                     "— high value and erratic demand — is the trickiest."),
                    ("Warehouse value",
                     "Current stock times unit cost. It only appears if you recorded the "
                     "cost."),
                    ("How this recommendation was calculated",
                     "The breakdown the ▶ arrow opens: average daily sales × lead "
                     "time days, + safety stock, − current stock, = before rounding, and "
                     "the rounding up to the MOQ when it applies. Below it, when there is a "
                     "prior value to compare against, “Why it changed” says whether the "
                     "change since last time came from a new forecast update or from "
                     "something that moved in your own business — your stock or your lead "
                     "time."),
                    ("Scenario simulator",
                     "Sliders for lead time, demand variation and extra stock that show how "
                     "the recommended quantity would change. It saves nothing: it is for "
                     "looking."),
                    ("Simple view",
                     "The same information cut down to four columns — product, signal, "
                     "qty to order and supplier — for a quick read."),
                    ("Provider view",
                     "Groups products by supplier, showing how many urgent and how many "
                     "upcoming each one has."),
                    ("Update stock view",
                     "An editable table of stock, lead time and supplier so you can fix many "
                     "products in a row and save them at once. Only for roles that can "
                     "edit."),
                    ("Export PO / Export PO (edited)",
                     "The first downloads the order the server computes; the second "
                     "downloads the order with the quantities you edited in the table. Both "
                     "record the order in Orders."),
                    ("Template / CSV / PDF",
                     "“Template” downloads an empty CSV with the right columns; the "
                     "CSV button imports your inventory (sku, current_stock, "
                     "lead_time_days); PDF downloads the whole traffic light as a report."),
                    ("Log stock-out",
                     "Deducts units that left the warehouse for a reason other than a sale "
                     "— breakage, expiry, self-consumption, gift or sample — and "
                     "accumulates their cost in the shrinkage summary."),
                    ("Events and seasons",
                     "High-demand seasons with a demand multiplier (×1.2 to ×3.0). As "
                     "soon as you save the event it changes the quantity to order for the "
                     "products it applies to — not just a simulator exercise. See “Events "
                     "that move the recommendation” later in this chapter."),
                ],
                "tasks": [
                    ("Fix a product's stock",
                     " 1. Find it by SKU or name in the search box. 2. Press the pencil icon "
                     "at the end of the row. 3. Change “Current stock” and anything "
                     "else that is wrong. 4. Press “Save”. 5. The signal and the "
                     "quantity to order are recomputed with the new figure."),
                    ("Load your whole inventory at once",
                     " 1. Press “Template” and open the file that downloads. "
                     "2. Fill in the sku, current_stock and lead_time_days columns. 3. Press "
                     "the CSV button and pick your file. 4. Check the notice telling you how "
                     "many rows were imported out of how many."),
                    ("See where a suggested quantity comes from",
                     " 1. Press the ▶ arrow at the start of the product's row. 2. Read "
                     "the subtraction step by step. 3. Look at the origin of the lead time "
                     "shown next to the lead-time days. 4. To try another scenario, open the "
                     "simulator with the sliders icon."),
                    ("Log shrinkage",
                     " 1. Press “Log stock-out” in the top bar. 2. Type the SKU and "
                     "pick the product. 3. Enter how many units left and choose the reason. "
                     "4. Confirm: stock drops immediately and the cost is recorded."),
                ],
                "gotchas": [
                    "“No data” is not a StockAI error: it is a product whose stock you "
                    "never recorded. While it stays that way it is left out of the purchase "
                    "recommendations, because how much to order depends on exactly how much "
                    "you have left.",
                    "A row can say “Order soon” and “Not yet” in the "
                    "quantity at the same time. They do not contradict each other: the signal "
                    "warns the buffer is thin, and the quantity says you are still above the "
                    "reorder point.",
                    "Two products with the same coverage can carry different signals. The "
                    "traffic light compares coverage against each product's own lead time, so "
                    "10 days of stock are comfortable with a 5-day supplier and critical with "
                    "a 30-day one.",
                    "Editing the quantity directly in the table saves nothing on the product: "
                    "that number is only used if you download “Export PO (edited)”.",
                    "The “Update stock” view does not appear for the viewer role. It "
                    "is an editor, not a view, and saving would be refused anyway.",
                ],
            },
            {
                "name": "Money not moving",
                "route": "/inventario",
                "image": "deadcapital",
                "purpose": (
                    "The list of products whose stock has gone a long time without "
                    "falling, ordered by how much money they represent, the most "
                    "expensive first. It is not the same as the SOBRESTOCK signal: that "
                    "signal compares your coverage against the product's lead time, so a "
                    "product can read “OK” or even “Order soon” on the traffic light and "
                    "still be money that has not moved in months, if demand simply "
                    "stalled without stock ever crossing the overstock threshold. This "
                    "view needs no trained update and no forecast: it reads your stock's "
                    "real history directly."
                ),
                "walkthrough": [
                    "Opens from the “Money not moving” view in the Inventory switcher.",
                    "At the top is a total in money — it only counts products with a "
                    "cost on file — and a note of how many idle products have no cost "
                    "and are left out of that total.",
                    "The table lists one product per row: name and SKU, supplier, "
                    "current stock, what it is worth (or “No cost on file”), and how "
                    "long it has gone without falling.",
                    "Priced products come first, most expensive to least; unpriced ones "
                    "come last, ordered by how many days they have been still.",
                    "When the recorded history is not long enough to have ever seen the "
                    "stock fall, the day count is not an exact date: it reads “at least "
                    "N days” — a floor, not a closed measurement.",
                    "With an active update, every row also carries its current signal, "
                    "so you can compare the two readings without leaving the screen; "
                    "with none active, the signal reads “No active session”.",
                ],
                "fields": [
                    ("SKU / Name", "The product, its supplier and its category."),
                    ("Current stock", "Units you hold today."),
                    ("Worth", "Current stock times unit cost. If the product has no "
                     "cost on file, it does not show a zero: it reads “No cost on "
                     "file — we don't know what it's worth”."),
                    ("Still since", "The days since that product's stock last fell. "
                     "When the recorded history is short, the figure carries “at "
                     "least”, because it is a floor, not an exact date."),
                    ("Signal", "The product's current traffic-light signal, if you "
                     "have an active update. It gates nothing on this list: a product "
                     "can appear here under any signal, or none."),
                ],
                "tasks": [
                    (
                        "Find where most of your money is sitting still",
                        " 1. Open the “Money not moving” view in Inventory. 2. Check "
                        "the total at the top: it only adds up products with a cost on "
                        "file. 3. Scroll down the table: it is already ordered from "
                        "most to least money. 4. If the product you care about says "
                        "“No cost on file”, add its cost on the product card so it "
                        "enters the total.",
                    ),
                ],
                "gotchas": [
                    "This view replaces the old “Dead stock” one. That view priced a "
                    "product with no cost on file at zero and sent it to the bottom "
                    "of the list, as if no money were at risk; it was retired so this "
                    "question has one list.",
                    "A product with no cost on file never counts as zero. If it did, "
                    "it would read as “no money at risk here”, which is the "
                    "opposite of what missing the data means: it is shown apart and "
                    "counted apart instead.",
                    "“At least N days” is not the same as “N days”. It appears when "
                    "the recorded history does not reach far enough back to have "
                    "ever seen the stock fall, and it is a floor on what is known, "
                    "not an exact measurement.",
                    "You do not need a trained update to see this list: it builds "
                    "itself from your stock's real history, so a product you never "
                    "put through a forecast can still show up here.",
                ],
            },
            {
                "name": "Rising costs and shrinking margin",
                "route": "/inventario",
                "image": "costalerts",
                "purpose": (
                    "Two views that read the same source: what you actually paid on "
                    "every reception of goods, not what the supplier's card claims. "
                    "“Rising costs” ranks your suppliers by how much they have raised "
                    "their prices; “Shrinking margin” ranks your products by how many "
                    "points their margin lost. Both exist so a cost increase does not "
                    "slip past you for lack of checking every product one by one."
                ),
                "walkthrough": [
                    "Open from the “Rising costs” and “Shrinking margin” views in the "
                    "Inventory switcher.",
                    "Both read only receptions that actually arrived: a quoted or "
                    "rejected order is not evidence of what you paid.",
                    "“Rising costs” groups by supplier: each row carries how much they "
                    "raised overall and, inside it, the products that pushed that "
                    "increase the most.",
                    "“Shrinking margin” ranks your products from the one that lost "
                    "the most margin to the one that lost the least, with the before "
                    "and after margin side by side.",
                    "Above “Shrinking margin” there is a control for the minimum "
                    "margin points lost you want to see; half a point by default, so "
                    "the list is not full of noise.",
                    "A fixed notice explains the limit both views share: StockAI stores "
                    "only your CURRENT sale price, never a price history, so the "
                    "“before” margin is built from today's price and the cost from "
                    "back then — it can never blame a price cut, only a cost "
                    "increase.",
                ],
                "fields": [
                    ("Increase", "The percentage that supplier, or that product, rose "
                     "in price over the window you are looking at, comparing the "
                     "first recorded reception with the most recent one."),
                    ("Products that rose most", "Within a supplier, the ones that "
                     "pushed that increase the hardest."),
                    ("Margin before / Margin now", "The margin with the cost from "
                     "the first recorded reception and with the most recent one, "
                     "both using your CURRENT sale price. It is not a real "
                     "historical margin: it is what would have happened had the "
                     "price never changed."),
                    ("Points lost", "The gap between the before and the now margin, "
                     "in percentage points. Only products that lost at least the "
                     "minimum you set above make the list."),
                    ("You are selling below cost", "A notice that appears when the "
                     "current margin is negative: cost has already overtaken the "
                     "sale price."),
                ],
                "tasks": [
                    (
                        "See which supplier has raised your prices the most",
                        " 1. Open the “Rising costs” view. 2. The table is already "
                        "ordered by the supplier that rose the most. 3. Open their "
                        "row to see which products drove that increase. 4. If you "
                        "are going to renegotiate, those are the products to start "
                        "with.",
                    ),
                    (
                        "Find the product whose margin is slipping away",
                        " 1. Open the “Shrinking margin” view. 2. Adjust the minimum "
                        "points if the list is too long or too short. 3. Compare "
                        "“Margin before” with “Margin now” on the row you care "
                        "about. 4. If the current margin is negative, the row says "
                        "so explicitly: you are selling below cost.",
                    ),
                ],
                "gotchas": [
                    "Both views need at least two recorded receptions of that "
                    "product or supplier within the window. With only one there is "
                    "nothing to compare against, and the product is left out "
                    "instead of shown with an invented change.",
                    "“Shrinking margin” never says you cut the price, even though "
                    "that would also explain a lower margin. StockAI does not store "
                    "your past prices, only your past costs, so it can only blame "
                    "cost — never price, because it has no way to prove it.",
                    "A product with no sale price on file, or without enough cost "
                    "history, is left out of “Shrinking margin” and counted "
                    "separately, not silently dropped.",
                    "The margin you see here can come out negative, and is shown "
                    "as such: the screen does not clip it to zero to look better.",
                ],
            },
            {
                "name": "Cost of ignoring",
                "route": "/inventario",
                "image": "ignoring",
                "purpose": (
                    "The record of what StockAI recommended each day and what happened "
                    "afterwards. For every product that carried a PEDIR_YA or "
                    "PEDIR_PRONTO signal, it says whether you ended up ordering it, "
                    "whether you likely ran out of stock for not ordering it, or "
                    "whether there is no way to tell from what is on record — and "
                    "that third answer is not a zero: it is that the evidence to "
                    "claim anything is missing."
                ),
                "walkthrough": [
                    "Opens from the “Cost of ignoring” view in the Inventory "
                    "switcher.",
                    "Lists one product per row with the outcome of every alert it "
                    "carried: ordered in time, likely stockout, or cannot tell.",
                    "“Likely stockout” only appears when StockAI actually saw your "
                    "stock hit zero after the alert, never because it assumed it "
                    "would.",
                    "When the stockout was still open at the end of the window you "
                    "are looking at, the screen flags it as a floor: what you lost "
                    "so far, not what you will lose in total.",
                    "“Cannot tell” appears when you neither generated the order in "
                    "time nor saw stock hit zero: there is no evidence that "
                    "ignoring the alert cost you anything, and none for the "
                    "opposite either.",
                    "Lost units and lost value use your average daily demand and "
                    "your CURRENT sale price; if either is missing, the row says "
                    "so instead of guessing a number.",
                ],
                "fields": [
                    ("Ordered in time", "You generated a purchase order for that "
                     "product within the 14 days after the alert."),
                    ("Likely stockout", "You did not order in time, and StockAI saw "
                     "your stock hit zero afterwards. Lost units and lost value "
                     "come computed, never as zero."),
                    ("Cannot tell", "You did not order in time, but you also never "
                     "saw stock hit zero. It is not that nothing happened: it is "
                     "that there is no evidence to say so, nor to rule it out."),
                    ("Price unknown", "Appears when the lost units are known but "
                     "their value is not, because the product has no sale price on "
                     "file."),
                ],
                "tasks": [
                    (
                        "See what not ordering in time cost you",
                        " 1. Open the “Cost of ignoring” view. 2. Look for the rows "
                        "marked “Likely stockout”. 3. Check the lost units and "
                        "lost value on each. 4. If one says it is a floor, the "
                        "stockout was still open at the end of the window: the "
                        "real figure is at least that much.",
                    ),
                ],
                "gotchas": [
                    "“Cannot tell” is not a disguised way of saying zero. It is "
                    "the honest answer when there is neither an order nor an "
                    "observed stockout: the evidence is missing, in either "
                    "direction.",
                    "Lost value is revenue, not margin: units times today's sale "
                    "price, not what you would actually have earned.",
                    "This screen prices a past stockout with today's sale price, "
                    "because StockAI stores no price history. Inventing what the "
                    "price was back then would be exactly the kind of number "
                    "this screen exists to avoid.",
                    "A product with no average daily demand on record cannot "
                    "have lost units computed, and the row says so instead of "
                    "showing a zero.",
                ],
            },
            {
                "name": "Events that move the recommendation",
                "route": "/inventario",
                "image": "events",
                "purpose": (
                    "Declaring a high season — Easter, Black Friday, your own "
                    "campaign date — is not an exercise: as soon as you save it, it "
                    "changes what StockAI tells you to order, every day, until the date "
                    "passes. The multiplier you type does not apply in full "
                    "overnight: it is scaled by how much of your lead time falls "
                    "inside the event's dates."
                ),
                "walkthrough": [
                    "Declared at the foot of Inventory, under “Events and "
                    "seasons”: name, start date, end date and a demand "
                    "multiplier, with an optional SKU or category.",
                    "As soon as you save the event, it starts moving the quantity "
                    "to order for the products it applies to — not just the "
                    "Scenario simulator's result.",
                    "The effect depends on how much of each product's lead time "
                    "falls inside the event's dates: if your supplier takes 15 "
                    "days and the event covers 4 of them, you do not get the full "
                    "multiplier, only a fraction of it.",
                    "For example: a ×1.8 event covering 4 of a product's 15 "
                    "lead-time days does not multiply its demand by 1.8, but by "
                    "roughly 1.21 — the share of the delivery window the event "
                    "actually reaches.",
                    "When a product's row changed because of an event, “Why it "
                    "changed” tells you with the exact math: the event's name, "
                    "its multiplier and how many of the lead-time days it applied "
                    "over.",
                    "Several events that overlap on the same product combine "
                    "with each other; they do not replace one another.",
                ],
                "fields": [
                    ("Demand multiplier", "How much more you will sell during "
                     "the event. ×1.8 is 80% more. The screen classifies it from "
                     "mild to peak depending on how high it is."),
                    ("Why it changed (event)", "When a row's change came from an "
                     "event, the sentence carries its name, the multiplier and "
                     "how many of the lead-time days it applied over — for "
                     "example, “Easter: x1.8 over 4 of 15 days”."),
                ],
                "tasks": [
                    (
                        "Get your inventory ready for a high season",
                        " 1. Go to the foot of Inventory and open “Events and "
                        "seasons”. 2. Create the event with its name, dates and "
                        "the multiplier you expect. 3. Go back to the traffic "
                        "light: the products it applies to already ask for more, "
                        "in proportion to how much of the event falls inside "
                        "their lead time. 4. To see the effect before saving it, "
                        "use the Scenario simulator with a “Promotion” rule on "
                        "the same dates.",
                    ),
                ],
                "gotchas": [
                    "The multiplier you type does not apply in full unless the "
                    "event covers the product's whole lead time. The same "
                    "one-week event moves a 30-day supplier's recommendation far "
                    "less than a 5-day one's.",
                    "This is different from the Scenario simulator: a simulated "
                    "rule touches nothing until you delete it; an event saved "
                    "under “Events and seasons” does change what you see on the "
                    "traffic light every day, for as long as it runs.",
                    "If two events overlap on the same product, their "
                    "multipliers combine; the higher one does not simply win.",
                ],
            },
            {
                "name": "The forecast in money",
                "route": "/inventario",
                "image": "forecastmoney",
                "purpose": (
                    "Multiplies what the forecast says you will sell by today's "
                    "price and cost, to give a figure in money instead of just in "
                    "units: how much you will sell, how much margin it will leave "
                    "you, and which products carry that money."
                ),
                "walkthrough": [
                    "Opens from the “Forecast in money” view in the Inventory "
                    "switcher, and needs an active update.",
                    "At the top are three figures: projected sales, projected "
                    "margin and how many products made it into the count.",
                    "Below, a fixed notice reminds you the projection uses your "
                    "CURRENT sale price, because StockAI stores no price history "
                    "and does not know what the price will be in the future.",
                    "The table lists one product per row — units forecast, "
                    "revenue, cost, margin and margin percentage — ordered from "
                    "the one that contributes the most margin to the one that "
                    "contributes the least.",
                    "A notice above the table says what percentage of the "
                    "projected margin the top ten contributing products "
                    "explain.",
                    "A product with no price or cost on file does not disappear "
                    "from the table: it stays with that cell marked unknown, and "
                    "is counted separately at the foot of the screen.",
                ],
                "fields": [
                    ("Projected sales", "Units the forecast expects you to sell "
                     "over the rest of the horizon, multiplied by today's sale "
                     "price."),
                    ("Projected margin", "The same, but with each product's "
                     "unit margin instead of the full price."),
                    ("Products forecast", "How many products made it into the "
                     "count. Ones with no forecast, price or cost do not, and "
                     "are counted separately."),
                    ("Units forecast", "The sum of that product's forecast over "
                     "the rest of this update's horizon, never negative."),
                    ("Margin / Margin %", "Same rule as the rest of the app: if "
                     "the price or the cost is missing, the cell says it is "
                     "unknown, never a zero."),
                ],
                "tasks": [
                    (
                        "See which products carry your future money",
                        " 1. Open the “Forecast in money” view. 2. Read the "
                        "line above the table: it tells you what share of the "
                        "margin the top ten explain. 3. Check those ten "
                        "products first: they are the ones you can least "
                        "afford to run out of.",
                    ),
                ],
                "gotchas": [
                    "The figure uses TODAY's price and cost, not a future one. "
                    "If you are about to raise a price, or your supplier is "
                    "about to raise a cost, this screen does not know it yet.",
                    "A product with no price or no cost is not dropped: it is "
                    "counted separately so the total does not look complete "
                    "when it is not.",
                    "The margin can come out negative, and is shown as such, "
                    "with no clipping to zero.",
                ],
            },
            {
                "name": "Orders",
                "route": "/pedidos",
                "image": "pedidos",
                "purpose": (
                    "This is where everything you have already ordered lives. Every order "
                    "you generated is recorded with what you bought, from whom and when; "
                    "from this screen you send it to the supplier and log the arrival of the "
                    "goods. Logging the arrival is what updates your stock and what teaches "
                    "StockAI how long each supplier really takes."
                ),
                "walkthrough": [
                    "The header reads “Orders” with the subtitle “Generated "
                    "orders and reception tracking”.",
                    "On the right sit the “N awaiting reception” counter and the "
                    "“New order” button, which opens the manual order form.",
                    "If you have two or more warehouses, two tabs appear below: “Purchase "
                    "orders” and “Transfers”.",
                    "Before the table come the supplier warnings: those with no email or "
                    "WhatsApp on file that have open orders, and those running later than "
                    "their own history.",
                    "If you have never generated an order, instead of the table you get a "
                    "welcome screen with the “Go to the Purchasing Panel” button.",
                    "The table lists one order per row, newest first, with its number, date, "
                    "number of SKUs, urgent, upcoming, units and total value.",
                    "The last column is the reception one: a status badge and, beside it, the "
                    "“Log arrival”, “Send order”, “Send to my "
                    "WhatsApp”, “Open in WhatsApp” and “Copy message” "
                    "buttons.",
                    "“Log arrival” opens a window with the order's lines: for each "
                    "product you see what was ordered, what was received before, and you type "
                    "what is arriving now.",
                    "That window closes with “Save quantities”, if part of it "
                    "arrived, or with “Everything arrived”, if the whole order came "
                    "in.",
                    "“Send order” first shows you which suppliers it will be sent to "
                    "and which will be skipped for missing contact details, and only then "
                    "asks for confirmation.",
                    "“Send to my WhatsApp”, “Open in WhatsApp” and "
                    "“Copy message” hand the order's text to you so you can forward "
                    "it yourself; they do not depend on the supplier being configured.",
                    "“New order” opens a form where you pick the supplier and the "
                    "destination warehouse and type the lines by hand: SKU, quantity and unit "
                    "cost. No forecast required.",
                ],
                "fields": [
                    ("N awaiting reception",
                     "How many orders are still waiting for goods. It counts the ones on the "
                     "way, the partial ones and the ones marked “Did not arrive”."),
                    ("Order",
                     "The purchase order's number, exactly as assigned when it was "
                     "generated."),
                    ("Date & time",
                     "When the order was generated."),
                    ("SKUs in order",
                     "How many distinct products the order carries."),
                    ("Urgent",
                     "How many of its lines carried the PEDIR_YA signal when it was "
                     "generated."),
                    ("Upcoming",
                     "How many of its lines carried the PEDIR_PRONTO signal."),
                    ("Total units",
                     "The sum of units ordered across the whole order."),
                    ("Total value",
                     "The sum of quantity times unit cost. It shows “—” if the "
                     "lines carried no cost."),
                    ("Reception: On the way / Partial / Received / Did not arrive",
                     "The state of the goods. “On the way” is also the state of an "
                     "order nothing was ever logged against: the absence of a record is not "
                     "proof the shipment arrived."),
                    ("Log arrival",
                     "Opens the reception window. It appears while the order is on the way or "
                     "partial."),
                    ("Ordered / Received before / Arriving now",
                     "The three columns of the reception window: what you ordered, what you "
                     "had already logged and what you are receiving right now. The field "
                     "comes pre-filled with what is still outstanding."),
                    ("Save quantities / Everything arrived",
                     "The first records exactly what you typed line by line; the second marks "
                     "the whole order as received without typing anything."),
                    ("Send order",
                     "Sends the order by email or WhatsApp to the suppliers on its lines. "
                     "Before sending it shows you who will receive it and who will be "
                     "skipped."),
                    ("Send to my WhatsApp / Open in WhatsApp / Copy message",
                     "These hand the order's text to you: to your WhatsApp, by opening "
                     "WhatsApp with the message ready, or to the clipboard."),
                    ("New order",
                     "The manual order form: supplier, destination warehouse and one line per "
                     "product with SKU, quantity and an optional unit cost."),
                    ("Transfers tab",
                     "Stock moves between your own warehouses, with their own send and "
                     "reception cycle. Only appears with two or more warehouses."),
                ],
                "tasks": [
                    ("Record that the whole order arrived",
                     " 1. Find the order in the table. 2. Press “Log arrival” in the "
                     "reception column. 3. Check the lines are the right ones. 4. Press "
                     "“Everything arrived”. 5. Each product's stock goes up and the "
                     "order becomes “Received”."),
                    ("Record a partial arrival",
                     " 1. Press “Log arrival” on the order. 2. Under “Arriving "
                     "now”, type the units that actually arrived for each product. "
                     "3. Leave the ones that did not arrive at zero. 4. Press “Save "
                     "quantities”. 5. The order stays “Partial” and keeps the "
                     "button to log the rest when it comes in."),
                    ("Send an order to its suppliers",
                     " 1. Press “Send order” on the order's row. 2. Read the "
                     "summary: who it will be sent to and who will be skipped. 3. Press "
                     "“Send” to confirm. 4. For the skipped suppliers, use "
                     "“Copy message” and send it yourself."),
                    ("Create a manual order",
                     " 1. Press “New order”. 2. Pick the supplier and, if you have "
                     "several warehouses, the destination one. 3. Type the SKU, the quantity "
                     "and — if you have it — the unit cost of each line. 4. Press "
                     "“Add product” for the next lines. 5. Press “Create "
                     "order”."),
                ],
                "gotchas": [
                    "Marking “Did not arrive” does not close the order. It is a "
                    "statement about a delivery that did not happen, not about a cancelled "
                    "order, so the order keeps counting under “awaiting reception”.",
                    "Logging the arrival is not paperwork: it is what raises your stock and "
                    "what teaches StockAI that supplier's real lead time. After 3 recorded "
                    "receptions StockAI replaces the configured lead time with the real "
                    "average.",
                    "“Send order” silently skips suppliers with no email or "
                    "WhatsApp on their record; that is why the confirmation names them "
                    "beforehand. For those, the way out is forwarding the message yourself.",
                    "An order with no supplier assigned on any of its lines cannot be sent to "
                    "anyone, and the confirmation tells you so before you try.",
                    "With the viewer role you will not see “New order” and cannot "
                    "log arrivals: both write business data.",
                ],
            },
            {
                "name": "Messages",
                "route": "/mensajes",
                "image": "mensajes",
                "purpose": (
                    "One-to-one messaging between the people in your company, inside StockAI. "
                    "It is for what happens around a purchasing decision — “already "
                    "confirmed with the supplier”, “I halved that quantity” "
                    "— without leaving for another app or losing the context."
                ),
                "walkthrough": [
                    "The left column starts with the “Messages” title and, on its "
                    "right, how many people are on your team.",
                    "Below it is the people search box, always on screen and never shifting "
                    "the list under your cursor as you use it.",
                    "Then comes the conversation list, most recent first: the person's "
                    "initial, their name, the time of the last message and a preview of it.",
                    "When you have unread messages from someone, their row is shown in bold "
                    "with a round counter on the right.",
                    "As soon as you type in the search box, a “Write to them for the "
                    "first time” block also appears with the colleagues you have not "
                    "talked to yet.",
                    "The right-hand area, until you pick someone, says “Pick a "
                    "conversation”.",
                    "When you open a conversation, the person's name stays at the top; on "
                    "narrow screens a back arrow to the list appears as well.",
                    "Messages read top to bottom: yours aligned right in the accent colour, "
                    "theirs on the left, each one with its time.",
                    "A brand-new conversation opens with the line “This is the start of "
                    "the conversation”.",
                    "At the bottom is the “Write a message…” field: Enter "
                    "sends, and a message can be up to 4,000 characters.",
                    "Opening the conversation marks the messages sent to you as read and the "
                    "counter disappears.",
                    "The application's top bar carries an envelope with your total unread "
                    "count, which brings you back to this screen from anywhere.",
                ],
                "fields": [
                    ("N on your team",
                     "How many active people there are in your company that you can write "
                     "to."),
                    ("Search for a person…",
                     "Filters your conversations and the rest of your colleagues at the same "
                     "time, by name or by email."),
                    ("Conversation row",
                     "Initial, name, time of the last message and its preview. If the last "
                     "message is yours, the preview starts with “You:”."),
                    ("Unread counter",
                     "The circle with a number on the right of the row: how many messages "
                     "from that person you have not opened."),
                    ("Write to them for the first time",
                     "A block that only appears while you are searching, listing the "
                     "colleagues you have no conversation with yet."),
                    ("Pick a conversation",
                     "The right panel's initial state, before you have opened any thread."),
                    ("This is the start of the conversation",
                     "What a freshly opened thread says when it has no messages yet."),
                    ("Write a message…",
                     "The composer. Enter sends; the limit is 4,000 characters per message."),
                    ("Send button",
                     "The round button next to the field. It stays disabled while the message "
                     "is empty or while it is being sent."),
                    ("Top-bar envelope",
                     "The global unread indicator; it shows “99+” past ninety-"
                     "nine."),
                    ("Get a heads-up when someone writes to you",
                     "A switch that lives in My account, not here. When it is on and your "
                     "number is linked, you get a WhatsApp heads-up (or an SMS if WhatsApp is "
                     "unavailable) if someone writes to you while you are away from StockAI."),
                ],
                "tasks": [
                    ("Write to someone for the first time",
                     " 1. Type their name or email in the search box. 2. Find them under "
                     "“Write to them for the first time”. 3. Click their name. "
                     "4. Type the message and press Enter."),
                    ("Pick a conversation back up and mark it read",
                     " 1. Look for the bold row with the unread counter. 2. Click it. 3. As "
                     "it opens, the messages are marked as read and the counter disappears, "
                     "along with the one on the envelope above."),
                    ("Turn on the WhatsApp heads-up",
                     " 1. Go to My account. 2. Link your WhatsApp number if you have not "
                     "already. 3. Find “Team messages” and switch on “Get a "
                     "heads-up when someone writes to you”."),
                ],
                "gotchas": [
                    "There are no groups and no channels: only one-person-to-one-person "
                    "conversations, always within your own company.",
                    "The list of colleagues you have not talked to only appears while you are "
                    "typing in the search box. Without searching, the column shows just your "
                    "conversations.",
                    "The screen refreshes itself — the list every 15 seconds and the "
                    "open thread every 5 — so a new message can take a few seconds to "
                    "show up. There is no “typing…” and no read receipt for "
                    "the sender.",
                    "The WhatsApp heads-up is off by default and needs your number linked in "
                    "My account. It also only fires when you are away from the conversation: "
                    "if you have just read that person, you are not notified again.",
                    "Users with the viewer role can send and receive messages too. The "
                    "boundary here is the company, not the role.",
                ],
            },
        ],
    },
}

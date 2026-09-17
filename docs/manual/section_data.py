# User-manual content for the four "your data" screens.
#
# Pure data: no imports, no logic. The PDF builder owns rendering; this module
# owns wording. Every number here was read out of the code, not remembered:
#
#   ForecastingCore/forecasting_core/data/gate.py   MIN_TRAINABLE_PERIODS = 20
#   ForecastingCore/forecasting_core/data/quality.py filter_valid_skus (drops)
#   backend/sessions/defaults.py                    min_history = 20
#   backend/inventory/service.py                    MIN_LEAD_TIME_OBSERVATIONS = 3
#   backend/inventory/cash_service.py               parse_payment_terms_days
#   backend/inventory/price_break_service.py        MAX_COVERAGE_DAYS / MIN_NET_SAVING_PCT
#   backend/inventory/reception_service.py          _fill_rate cap, on_time_rate
#   backend/inventory/supplier_health_service.py    MIN_RECENT / MIN_BASELINE / Z_THRESHOLD
#   backend/datasets/service.py                     ALLOWED_EXTENSIONS
#   backend/entitlements/plans.py                   free: 25 MB, 100 SKUs
#   Frontend/src/lib/inventoryDefaults.ts           DEFAULT_LEAD_TIME_DAYS = 15
#
# On-screen labels are quoted verbatim from Frontend/src/i18n/translations.ts
# and Frontend/src/i18n/stockSetup.ts, in each language's own catalogue.

SECTION = {
    "id": "data",
    "es": {
        "title": "Tus datos",
        "intro": (
            "Faro no adivina nada: todo lo que ves en el semáforo sale de cuatro pantallas "
            "que llenas tú. En «Mis ventas» subes el historial con el que se calcula la demanda; "
            "en «Configurar inventario» dices cuánto tienes, cuánto te cuesta y cuánto tarda en "
            "llegar; en «Proveedores» registras a quién le compras; y en el «Scorecard» comparas "
            "lo que cada proveedor promete con lo que realmente cumple. Mientras falte alguno de "
            "esos datos, Faro te lo dice en la pantalla en vez de suponerlo en silencio."
        ),
        "screens": [
            {
                "name": "Mis ventas",
                "route": "/ventas",
                "image": "ventas",
                "purpose": (
                    "Es la puerta de entrada del producto. Aquí subes tu historial de ventas, le "
                    "dices a Faro qué columna es cuál, y arrancas el cálculo que produce el "
                    "pronóstico por producto. Todo lo demás — el semáforo, las órdenes de compra, "
                    "el scorecard — se apoya en lo que entra por esta pantalla."
                ),
                "walkthrough": [
                    "La pantalla se abre en el primero de tres pasos, marcados arriba: «Sube tus ventas», «Confirma columnas» y «El sistema aprende».",
                    "Antes de tocar el archivo llenas el plan: «Ponle un nombre (opcional)», «¿Hasta cuándo quieres planificar?» con las opciones 4 semanas, 8 semanas y 6 meses, y «Nivel de detalle del plan» con Auto (recomendado), Diario, Semanal y Mensual.",
                    "El último control del plan es «¿En qué país vendes?»: decide de qué calendario de feriados aprende el modelo, ofrece doce países y viene preseleccionado en Colombia.",
                    "Arrastras tu archivo a la caja punteada o haces clic para buscarlo; la propia caja indica «Formatos aceptados: CSV, Excel (.xlsx, .xls)».",
                    "Si ya subiste archivos antes aparecen dos pestañas más junto a «Subir archivo nuevo»: «Usar un archivo ya subido» y «Repetir una carga anterior», que reutiliza también las columnas de esa carga.",
                    "Cuando el archivo es CSV, Faro lo revisa en tu propia máquina antes de subirlo y te lista los problemas con el número de fila, además de ofrecerte una plantilla descargable.",
                    "Con el archivo cargado pasas a «Confirma tus columnas»: quince campos, tres de ellos obligatorios y marcados con una estrella roja, donde eliges qué columna de tu archivo corresponde a cada uno.",
                    "Debajo de los selectores hay una «Vista previa de tus datos» con las primeras cinco columnas y tres filas, para que compruebes que leímos lo que creías.",
                    "Si el archivo tiene algo que cambiaría el pronóstico sin avisar, aparece el bloque «Tienes que decidir algo antes de seguir» con una pregunta por hallazgo, y el botón de continuar queda apagado hasta que respondas todas.",
                    "Al pulsar «Esto se ve bien, continuar →» empieza el tercer paso, con una barra que avanza por etapas — «Aprendiendo de tu historial…», «Midiendo qué tan preciso quedó…» — y al terminar la vista diaria te deja directamente en la pantalla de compras.",
                ],
                "fields": [
                    ("Ponle un nombre (opcional)", "Cómo se llamará esta carga en tu historial. Solo sirve para reconocerla después; puedes dejarlo vacío."),
                    ("¿Hasta cuándo quieres planificar?", "El horizonte del pronóstico en días de calendario: 4 semanas son 28 días, 8 semanas 56 y 6 meses 180."),
                    ("Nivel de detalle del plan", "Si el plan se calcula por día, por semana o por mes. «Auto (recomendado)» deja que Faro lo decida según la forma de tu archivo."),
                    ("¿En qué país vendes?", "El calendario de feriados que aprende el modelo. Doce países disponibles; si no lo cambias, se usa Colombia."),
                    ("SKU / Producto ★", "Obligatorio. La columna que identifica cada producto."),
                    ("Fecha ★", "Obligatorio. La columna con la fecha de cada venta."),
                    ("Demanda ★", "Obligatorio. La columna con las unidades vendidas — unidades, no dinero."),
                    ("Inventario", "Opcional. Si no la das, un día sin ventas no se distingue de un día sin producto en bodega, y el pronóstico sale conservador."),
                    ("Lead Time (días)", "Opcional. Si tu archivo no la trae, la pantalla muestra «Default: 15» — los quince días que Faro supone."),
                    ("Vista previa de tus datos:", "Las tres primeras filas de las cinco primeras columnas, tal como Faro las leyó."),
                ],
                "tasks": [
                    (
                        "Subir tu primer archivo de ventas",
                        " 1. Elige el horizonte y el nivel de detalle, y cambia el país si no vendes en Colombia."
                        " 2. Arrastra el CSV o el Excel a la caja punteada."
                        " 3. Revisa los problemas por fila que aparezcan y corrige el archivo si hace falta."
                        " 4. En el paso 2, confirma las tres columnas obligatorias y las opcionales que tengas."
                        " 5. Mira la vista previa para verificar que las columnas quedaron donde corresponde."
                        " 6. Pulsa «Esto se ve bien, continuar →» y espera sin cerrar la pestaña."
                    ),
                    (
                        "Volver a calcular con el archivo del mes pasado",
                        " 1. En el paso 1, abre la pestaña «Repetir una carga anterior»."
                        " 2. Cambia arriba el horizonte o el nivel de detalle que quieras probar."
                        " 3. Pulsa «Repetir» en la carga que quieras reusar: se reutiliza el mismo archivo y las mismas columnas."
                        " 4. Confirma en el paso 2 y continúa."
                    ),
                    (
                        "Responder una pregunta del control de datos",
                        " 1. Lee el título del hallazgo, por ejemplo «No sabemos si tus fechas están en día/mes o mes/día»."
                        " 2. Compara las dos opciones: cada una dice qué hace y qué te cuesta si te equivocas."
                        " 3. Marca la que corresponda a tu archivo; la opción marcada como «recomendado» es solo una sugerencia."
                        " 4. Cuando no quede ninguna pregunta sin responder, el botón de continuar se enciende solo."
                    ),
                ],
                "gotchas": [
                    "Un producto con menos de 20 períodos de historia no se marca con un aviso: se elimina antes de entrenar. No tendrá pronóstico ni aparecerá en el semáforo, y por eso las opciones que borran filas te advierten cuántos productos pueden caer por debajo de ese mínimo.",
                    "En el plan gratuito el archivo no puede pasar de 25 MB y el catálogo se topa en 100 productos; el plan pagado sube el archivo hasta 2000 MB y quita el tope de productos.",
                    "No existe un «continuar de todos modos». Si el control de datos encuentra algo sin arreglo posible, la única salida es corregir el mapeo de columnas arriba o subir otro archivo.",
                    "El país de feriados viene en Colombia porque es lo que usaban todas las cargas antes de que existiera el control. Si vendes en otro país, cámbialo antes de subir: se aplica a esa carga, no hacia atrás.",
                    "Si tu última carga terminada usaba columnas que siguen existiendo en el archivo nuevo, Faro las reutiliza y te lo dice arriba. Si alguna desapareció, te nombra cuáles y vuelve a proponer el mapeo desde cero.",
                ],
            },
            {
                "name": "Configurar inventario",
                "route": "/configurar-inventario",
                "image": "configurar",
                "purpose": (
                    "Faro ya sabe cuánto vas a vender; esta pantalla es donde le dices contra qué "
                    "comparar esa venta. Ofrece dos caminos hacia lo mismo: subir el archivo que "
                    "tu sistema ya exporta, o llenar a mano solo los productos que se llevan tu "
                    "plata. Los dos escriben en el mismo lugar y mueven la misma barra."
                ),
                "walkthrough": [
                    "Bajo el título «Configurar mi inventario» hay un recuadro que explica la regla: sin stock o sin costo el producto no aparece en el semáforo, y sin días de entrega sí aparece, pero calculado sobre los 15 días que Faro supone.",
                    "El primer panel es «O sube el archivo de tu sistema»: CSV o Excel tal como te lo exporta tu sistema, sin editar encabezados.",
                    "Pulsas «Elegir archivo» y Faro lo lee al momento; si es CSV, primero lo revisa en tu máquina y te nombra la fila y la columna de cada problema.",
                    "Debajo aparece «Así entendimos tus columnas» con un desplegable por campo; cambias el que esté mal y lo que dejes en «No importar» se queda fuera.",
                    "El resumen te dice «Filas listas para importar», «Filas con problemas que quedan fuera» y «Filas sin código de producto que se saltan», con ejemplos concretos del tipo «Fila 214 (SKU-001): \"N/D\"».",
                    "Confirmas con «Importar» — el botón lleva la cuenta entre paréntesis — y al terminar la pantalla dice cuántos productos entraron y cuántas filas quedaron fuera.",
                    "El segundo panel, «Empieza por estos», ordena tus productos por la plata que mueven, no por orden alfabético ni por filas.",
                    "La barra de arriba dice «X% de tu compra del mes ya configurado» y debajo aclara «La barra mide plata, no filas», con el conteo de productos listos sobre el total.",
                    "Cada fila muestra la venta proyectada, cuánto vale reponerla, cuánto pesa dentro del mes, el acumulado y una columna «Le falta» que nombra exactamente qué dato no tienes.",
                    "Al final de cada fila hay tres casillas — Stock, Costo y Días de entrega — y un botón «Guardar» que confirma con la palabra «Guardado».",
                ],
                "fields": [
                    ("Stock actual", "Cuántas unidades tienes hoy físicamente en bodega, sin contar lo que está por llegar."),
                    ("Costo", "Lo que TÚ le pagas a tu proveedor por una unidad, no el precio al que la vendes."),
                    ("Días de entrega", "Cuántos días pasan desde que le haces el pedido a tu proveedor hasta que la mercadería está en tu bodega."),
                    ("Compra mínima", "La cantidad mínima que ese proveedor te acepta por pedido, si la tienes en el archivo."),
                    ("Proveedor", "A quién le compras ese producto. Es lo que permite agrupar la orden y, más adelante, medir al proveedor."),
                    ("Venta proyectada", "Cuántas unidades esperamos que vendas en el período, según tu propio historial. Es un pronóstico, no una meta."),
                    ("Vale", "Lo que te va a costar reponer esa venta: las unidades proyectadas por lo que pagas por unidad. Es la cifra por la que se ordena la lista."),
                    ("Acumulado", "Sumando este producto y todos los de arriba, qué porcentaje de tu compra del período ya llevas cubierto."),
                    ("Le falta", "Los datos que todavía no diste de ese producto: stock, costo, tiempo de entrega, precio o proveedor."),
                ],
                "tasks": [
                    (
                        "Importar el stock desde el archivo de tu sistema",
                        " 1. En «O sube el archivo de tu sistema», pulsa «Elegir archivo» y escoge el export de tu ERP."
                        " 2. Revisa «Así entendimos tus columnas» y corrige las asignaciones que estén mal."
                        " 3. Marca «No importar» en las columnas que no quieras traer."
                        " 4. Mira el conteo de filas listas y de filas que quedan fuera."
                        " 5. Pulsa «Importar» y espera el mensaje con el total de productos importados."
                    ),
                    (
                        "Llegar al 80% de tu compra sin configurar todo el catálogo",
                        " 1. Baja al panel «Empieza por estos»."
                        " 2. Sigue la columna «Acumulado» de arriba hacia abajo."
                        " 3. Llena Stock, Costo y Días de entrega de cada fila y pulsa «Guardar»."
                        " 4. Detente cuando el acumulado llegue al 80%: el resto casi no mueve plata."
                    ),
                    (
                        "Corregir un producto que dice «Le falta costo»",
                        " 1. Ubica la fila en «Empieza por estos» y lee qué dato falta."
                        " 2. Escribe en la casilla «Costo» lo que le pagas al proveedor por unidad."
                        " 3. Pulsa «Guardar» y espera a que diga «Guardado»."
                        " 4. Usa «Actualizar» para ver la barra recalculada con esa fila ya completa."
                    ),
                ],
                "gotchas": [
                    "Un producto sin stock o sin costo no entra al semáforo, ni siquiera en verde: sencillamente no aparece. Sin días de entrega sí aparece, pero planificado sobre 15 días supuestos, así que un proveedor que tarda 45 te va a avisar tarde.",
                    "Si un producto aparece varias veces en el archivo, se queda con el valor de la última fila. Las filas con stock o costo negativos quedan fuera.",
                    "La pantalla no inventa ceros. Si guardas una fila sin escribir el stock de un producto que aún no tiene inventario registrado, te lo pide: un cero inventado es indistinguible de uno contado y pondría en rojo una bodega llena.",
                    "Mientras no hayas dado ningún costo ni precio, la lista se ordena por volumen y lo dice: «Todavía no nos has dado costos ni precios, así que ordenamos por volumen». En cuanto subes costos, vuelve a ordenar por plata.",
                    "El panel «Empieza por estos» necesita un pronóstico para priorizar. Si todavía no has subido ventas, muestra un aviso y te manda a hacerlo primero.",
                ],
            },
            {
                "name": "Proveedores",
                "route": "/proveedores",
                "image": "proveedores",
                "purpose": (
                    "Un proveedor es quien te vende cada producto, y registrarlo es lo que "
                    "convierte una alerta de stock en una orden que se puede enviar. Aquí guardas "
                    "sus datos de contacto, sus plazos, sus términos de pago y sus escalas de "
                    "precio por volumen — y ves cuánto ha aprendido Faro de sus entregas reales."
                ),
                "walkthrough": [
                    "El encabezado «Proveedores» trae dos accesos: «Scorecard», que lleva a la pantalla de desempeño, y «Agregar proveedor».",
                    "Si aún no tienes ninguno, la pantalla muestra un estado vacío que explica para qué sirven y ofrece «Agregar primer proveedor».",
                    "El formulario pide Nombre — el único obligatorio —, Email (para enviar OC), Teléfono, WhatsApp, Términos de pago, Lead time (días), Variabilidad (días) y Notas.",
                    "«Términos de pago» es un desplegable con Contado, 15 días, 30 días, 60 días, 90 días y Otro.",
                    "El lead time llega con 15 días y la variabilidad con 3; puedes cambiar ambos antes de crear la ficha.",
                    "Bajo el campo de lead time, una nota fija la precedencia: ese plazo se aplica a todos los productos de ese proveedor que no tengan uno propio, y el que tú configuraste en la ficha de un producto gana.",
                    "La tabla lista Nombre, Lead time, Variabilidad, Aprendizaje, Términos de pago, Email, Teléfono / WhatsApp y Acciones.",
                    "La columna «Aprendizaje» dice en qué punto va cada proveedor: «Todavía no tengo entregas tuyas…», «Llevo n de 3 entregas registradas…», «Aprendí de n entregas: tardan X días en promedio…» o el caso en que las entregas registradas no dicen nada.",
                    "El icono de etiqueta al final de la fila despliega las «Escalas de precio» de ese proveedor, con SKU, Cantidad mínima, Precio unitario y Notas.",
                    "Los otros dos iconos editan y eliminan la ficha; eliminar pide confirmación y avisa que la acción es irreversible.",
                ],
                "fields": [
                    ("Nombre", "El único campo obligatorio. Es la llave con la que se cruzan los productos, las recepciones y el scorecard, y se compara ignorando mayúsculas."),
                    ("Email (para enviar OC)", "La dirección a la que salen las órdenes de compra desde Faro."),
                    ("Teléfono", "Contacto telefónico del proveedor. Se muestra en la tabla junto al WhatsApp."),
                    ("WhatsApp", "Número al que se pueden mandar avisos y órdenes por WhatsApp."),
                    ("Términos de pago", "A cuántos días te cobra. Alimenta el calendario de caja, que estima cuánto vence cada semana."),
                    ("Lead time (días)", "Días que tarda en entregarte desde que haces el pedido. Se aplica a todos sus productos que no tengan uno propio."),
                    ("Variabilidad (días)", "Qué tanto puede variar ese plazo. Si dice 15 días pero a veces llega en 18, pon 3. A más variabilidad, más stock de seguridad."),
                    ("Notas", "Condiciones especiales, contacto, observaciones. Texto libre para ti."),
                    ("Aprendizaje", "Columna de solo lectura: cuántas entregas suyas llevas registradas y si ya son suficientes para que Faro reemplace el plazo configurado."),
                    ("Escalas de precio: Cantidad mínima / Precio unitario", "A partir de esa cantidad, cada unidad cuesta ese precio. Se usan para sugerir cuándo conviene pedir más."),
                ],
                "tasks": [
                    (
                        "Registrar un proveedor para poder enviarle órdenes",
                        " 1. Pulsa «Agregar proveedor»."
                        " 2. Escribe el nombre exactamente como lo usas en tus productos."
                        " 3. Llena el email: sin email ni WhatsApp, el envío de órdenes lo omite."
                        " 4. Ajusta el lead time y la variabilidad a lo que ese proveedor te promete."
                        " 5. Elige los términos de pago y pulsa «Crear proveedor»."
                    ),
                    (
                        "Cargar una escala de precio por volumen",
                        " 1. En la fila del proveedor, pulsa el icono de etiqueta para desplegar «Escalas de precio»."
                        " 2. Escribe el SKU al que aplica la escala."
                        " 3. Pon la cantidad mínima desde la cual baja el precio y el precio unitario a esa cantidad."
                        " 4. Pulsa «Agregar escala» y repite para cada peldaño de la escala."
                    ),
                    (
                        "Hacer que Faro aprenda el plazo real de un proveedor",
                        " 1. Registra la llegada de cada orden de compra cuando la recibas."
                        " 2. Vuelve a esta pantalla y mira la columna «Aprendizaje»."
                        " 3. Cuando lleve 3 entregas registradas, el texto cambia a «Aprendí de n entregas» y ese pasa a ser el número con el que se planifica."
                        " 4. Si el aprendido difiere mucho de lo que el proveedor promete, corrígelo también en su ficha para que el scorecard lo mida contra una promesa real."
                    ),
                ],
                "gotchas": [
                    "Faro no reemplaza el plazo que configuraste por el aprendido hasta tener 3 recepciones registradas de ese proveedor. Con una sola, una entrega rara — un feriado, una huelga, un camión varado — reescribiría el plazo de todos sus productos.",
                    "Si todas las entregas registradas llegaron el mismo día que las pediste, el promedio observado es 0 y no dice nada del proveedor. Faro lo declara inutilizable, lo dice en la columna «Aprendizaje» y sigue usando el plazo que configuraste.",
                    "Los términos de pago son texto que se interpreta para el calendario de caja. Se entienden «contado», «contra entrega», «anticipo», «prepago» y «COD» como 0 días; «N meses» como N por 30; «quincenal» como 15; y el primer número de cosas como «30 días» o «net 30».",
                    "Lo que NO se entiende queda como plazo desconocido, y se reporta como tal en vez de inventarse un número: las cuotas tipo «2x30» o «30/60/90», los rangos como «30-45 días» y los textos como «a convenir».",
                    "El plazo del proveedor manda sobre todos sus productos que no tengan uno propio; el que escribas en la ficha de un producto siempre gana sobre el del proveedor.",
                ],
            },
            {
                "name": "Scorecard de proveedores",
                "route": "/proveedores/scorecard",
                "image": "scorecard",
                "purpose": (
                    "La pantalla que compara lo que cada proveedor promete con lo que realmente "
                    "cumple. Se arma sola con las recepciones que registraste: plazo real contra "
                    "plazo declarado, porcentaje de entregas a tiempo, cuánto de lo pedido llegó y "
                    "cuánto le has comprado."
                ),
                "walkthrough": [
                    "Se llega desde el botón «Scorecard» en Proveedores, y se vuelve con «Volver a Proveedores» arriba a la derecha.",
                    "Todo lo que muestra sale de las recepciones registradas: un proveedor sin ninguna llegada anotada no tiene fila.",
                    "Si algún proveedor viene tardando más que de costumbre, arriba aparece un aviso ámbar con cuántos son, cuántos días se desviaron y sobre cuántas recepciones se comparó.",
                    "Ese mismo aviso explica el método al pie: una regla de control estadístico de 3 sigma sobre la mediana y la desviación absoluta mediana del historial de cada proveedor.",
                    "La tabla tiene nueve columnas: Proveedor, Recepciones, Lead time real, Declarado, Tendencia, % A tiempo, % Fill rate, Valor comprado y Última recepción.",
                    "«Lead time real» es un rango mínimo–máximo de lo observado, no un promedio: dos entregas de 5 y 25 días se muestran como 5–25d, no como 15d.",
                    "«% A tiempo» se pinta verde desde 70%, ámbar desde 40% y rojo por debajo, para que la fila se lea de un vistazo.",
                    "«Valor comprado» muestra un guion si ninguna línea de esas órdenes traía costo unitario, y un «≥» delante del monto si solo algunas lo traían.",
                    "Cuando una celda no se puede medir, la pantalla lo dice con palabras — «No concluyente», «Aún no» — en lugar de imprimir un cero que invitaría a la decisión equivocada.",
                    "Si no hay ninguna fila, hay dos estados vacíos distintos: uno para cuando aún no registraste recepciones, y otro para cuando sí las registraste pero sin proveedor asignado a los productos.",
                ],
                "fields": [
                    ("Proveedor", "El nombre tal como venía en las órdenes. Las mayúsculas se ignoran al agrupar, así que «Acme» y «ACME» son una sola fila."),
                    ("Recepciones", "Cuántas llegadas suyas llevas registradas. Es el tamaño de muestra detrás de todo lo demás en la fila."),
                    ("Lead time real", "El rango de días observados entre el pedido y la llegada, del mínimo al máximo."),
                    ("Declarado", "El plazo que hay en la ficha del proveedor. Aparece un guion si nadie llegó a llenarlo, para no medirlo contra una promesa que nunca hizo."),
                    ("Tendencia", "Si su plazo reciente se salió de su propio rango normal. Muestra los días de desviación, «Estable», o «Aún no» cuando todavía no hay con qué comparar."),
                    ("% A tiempo", "Proporción de entregas cuyo plazo real fue menor o igual al declarado. Queda vacío si no hay plazo declarado."),
                    ("% Fill rate", "Cuánto de lo que pediste llegó realmente, tope 100%."),
                    ("Valor comprado", "Lo que le has comprado, sumando las líneas que sí traen costo unitario."),
                    ("Última recepción", "La fecha de la llegada más reciente que registraste de ese proveedor."),
                ],
                "tasks": [
                    (
                        "Comparar lo que un proveedor promete con lo que cumple",
                        " 1. Ubica su fila y lee «Declarado» — lo que dice su ficha."
                        " 2. Compáralo con «Lead time real», que es el rango que has visto de verdad."
                        " 3. Mira «% A tiempo» para saber con qué frecuencia cumple ese plazo."
                        " 4. Si el real está sistemáticamente por encima del declarado, corrige el plazo en su ficha en Proveedores."
                    ),
                    (
                        "Entender por qué una fila dice «No concluyente»",
                        " 1. Pasa el cursor sobre el texto para leer la explicación completa."
                        " 2. Significa que todas las entregas registradas llegaron el mismo día que las pediste."
                        " 3. Revisa si registraste la fecha de llegada correcta al recibir esas órdenes."
                        " 4. Mientras siga así, Faro planifica con el plazo que configuraste, no con un cero."
                    ),
                    (
                        "Llenar el scorecard cuando está vacío",
                        " 1. Lee cuál de los dos estados vacíos te salió: sin recepciones, o con recepciones sin proveedor."
                        " 2. Si es el primero, registra la llegada de una orden de compra desde el historial."
                        " 3. Si es el segundo, asigna el proveedor en la ficha de cada producto."
                        " 4. Vuelve al scorecard: se llena con las próximas llegadas que registres."
                    ),
                ],
                "gotchas": [
                    "La columna «Tendencia» necesita al menos 6 recepciones para decir algo: 2 en la ventana reciente y 4 en la base histórica. Con menos, muestra «Aún no» en lugar de llamar «Estable» a un plazo que nadie ha visto variar.",
                    "El fill rate se topa en 100%. Un proveedor que manda 120 contra un pedido de 100 cumplió la orden y además te dejó 20 unidades que nadie pidió: eso es un problema de stock, no un mérito.",
                    "«Valor comprado» nunca es cero por falta de costos. Si ninguna línea traía costo unitario muestra un guion, y si solo algunas lo traían muestra «≥» delante del monto para avisarte que es un piso, no el total.",
                    "«Declarado» queda vacío cuando nadie llenó el plazo en la ficha del proveedor, y el «% A tiempo» se va con él. Es a propósito: los 15 días que Faro supone no son una promesa del proveedor y no se le pueden cobrar.",
                    "Faro es más estricto para acusar que para ajustar: le bastan 3 recepciones para aprender un plazo, pero exige 6 antes de señalar a un proveedor por venir tarde. Acusar cuesta más que ajustar.",
                ],
            },
        ],
    },
    "en": {
        "title": "Your data",
        "intro": (
            "Faro guesses nothing: everything you see in the traffic light comes from four screens "
            "you fill in yourself. Under \"My sales\" you upload the history the demand is computed "
            "from; under \"Set up my inventory\" you say how much you hold, what it costs you and how "
            "long it takes to arrive; under \"Suppliers\" you record who you buy from; and the "
            "\"Supplier scorecard\" compares what each supplier promises with what they actually "
            "deliver. While any of those is missing, Faro says so on screen instead of quietly "
            "assuming it."
        ),
        "screens": [
            {
                "name": "My sales",
                "route": "/ventas",
                "image": "ventas",
                "purpose": (
                    "This is the product's front door. Here you upload your sales history, tell "
                    "Faro which column is which, and start the run that produces the per-product "
                    "forecast. Everything else — the traffic light, the purchase orders, the "
                    "scorecard — rests on what comes in through this screen."
                ),
                "walkthrough": [
                    "The screen opens on the first of three steps, shown along the top: \"Upload your sales\", \"Confirm columns\" and \"The system learns\".",
                    "Before you touch the file you fill in the plan: \"Give it a name (optional)\", \"How far ahead do you want to plan?\" with the options 4 weeks, 8 weeks and 6 months, and \"Plan detail level\" with Auto (recommended), Daily, Weekly and Monthly.",
                    "The last plan control is \"Which country do you sell in?\": it decides whose public holidays the model learns from, offers twelve countries, and comes preset to Colombia.",
                    "You drag your file onto the dashed box or click to browse; the box itself states \"Accepted formats: CSV, Excel (.xlsx, .xls)\".",
                    "If you have uploaded before, two more tabs appear next to \"Upload a new file\": \"Use a file you already uploaded\" and \"Repeat a previous upload\", which reuses that run's columns as well.",
                    "When the file is a CSV, Faro checks it on your own machine before uploading and lists the problems with their row number, alongside a downloadable template.",
                    "With the file loaded you move on to \"Confirm your columns\": fifteen fields, three of them required and marked with a red star, where you pick which column of your file each one is.",
                    "Below the selectors there is a \"Preview of your data:\" with the first five columns and three rows, so you can check we read what you thought.",
                    "If the file holds something that would change the forecast without saying so, the block \"There is something you have to decide first\" appears with one question per finding, and the continue button stays dead until you answer them all.",
                    "Pressing \"This looks good, continue →\" starts the third step, with a bar moving through stages — \"Learning from your history…\", \"Measuring how accurate it turned out…\" — and when the daily view finishes it drops you straight on the purchasing screen.",
                ],
                "fields": [
                    ("Give it a name (optional)", "What this upload will be called in your history. It only helps you recognise it later; you can leave it empty."),
                    ("How far ahead do you want to plan?", "The forecast horizon in calendar days: 4 weeks is 28 days, 8 weeks is 56 and 6 months is 180."),
                    ("Plan detail level", "Whether the plan is computed by day, week or month. \"Auto (recommended)\" lets Faro decide from the shape of your file."),
                    ("Which country do you sell in?", "The holiday calendar the model learns from. Twelve countries are offered; leave it alone and Colombia is used."),
                    ("SKU / Product ★", "Required. The column identifying each product."),
                    ("Date ★", "Required. The column holding the date of each sale."),
                    ("Demand ★", "Required. The column with units sold — units, not money."),
                    ("Inventory", "Optional. Without it, a day with no sales cannot be told apart from a day with nothing on the shelf, and the forecast runs conservative."),
                    ("Lead time (days)", "Optional. If your file has no such column, the screen shows \"Default: 15\" — the fifteen days Faro assumes."),
                    ("Preview of your data:", "The first three rows of the first five columns, exactly as Faro read them."),
                ],
                "tasks": [
                    (
                        "Upload your first sales file",
                        " 1. Pick the horizon and the detail level, and change the country if you do not sell in Colombia."
                        " 2. Drag the CSV or Excel file onto the dashed box."
                        " 3. Read any row-level problems reported and fix the file if needed."
                        " 4. On step 2, confirm the three required columns and whichever optional ones you have."
                        " 5. Check the preview to make sure the columns landed where they belong."
                        " 6. Press \"This looks good, continue →\" and wait without closing the tab."
                    ),
                    (
                        "Re-run the numbers on last month's file",
                        " 1. On step 1, open the \"Repeat a previous upload\" tab."
                        " 2. Change the horizon or the detail level above to whatever you want to try."
                        " 3. Press \"Repeat\" on the upload you want to reuse: the same file and the same columns are carried over."
                        " 4. Confirm on step 2 and continue."
                    ),
                    (
                        "Answer a data-check question",
                        " 1. Read the finding's title, for example \"We cannot tell whether your dates are day/month or month/day\"."
                        " 2. Compare the two options: each states what it does and what it costs you if you get it wrong."
                        " 3. Pick the one that matches your file; the one marked \"recommended\" is only a suggestion."
                        " 4. Once no question is left unanswered, the continue button switches on by itself."
                    ),
                ],
                "gotchas": [
                    "A product with fewer than 20 periods of history is not flagged with a warning: it is dropped before training. It gets no forecast and never appears in the traffic light, which is why the options that delete rows warn you how many products could fall below that minimum.",
                    "On the free plan the file cannot exceed 25 MB and the catalogue is capped at 100 products; the paid plan raises the file to 2000 MB and removes the product cap.",
                    "There is no \"continue anyway\". If the data check finds something with no possible fix, the only way out is correcting the column mapping above or uploading a different file.",
                    "The holiday country starts on Colombia because that is what every upload silently used before this control existed. If you sell elsewhere, change it before uploading: it applies to that run, not retroactively.",
                    "If your last finished upload used columns that still exist in the new file, Faro reuses them and says so at the top. If any went missing, it names which ones and proposes the mapping from scratch again.",
                ],
            },
            {
                "name": "Set up my inventory",
                "route": "/configurar-inventario",
                "image": "configurar",
                "purpose": (
                    "Faro already knows how much you will sell; this screen is where you tell it "
                    "what to compare those sales against. It offers two routes to the same place: "
                    "upload the file your system already exports, or fill in by hand only the "
                    "products that carry your money. Both write to the same place and move the "
                    "same bar."
                ),
                "walkthrough": [
                    "Under the title \"Set up my inventory\" a box states the rule: without stock or cost the product does not appear in the traffic light, and without a lead time it does appear — but planned on the 15 days Faro assumes.",
                    "The first panel is \"Or upload your system's file\": CSV or Excel exactly as your system exports it, with no header editing.",
                    "You press \"Choose file\" and Faro reads it immediately; for a CSV it checks the file on your machine first and names the row and the column of every problem.",
                    "Below, \"This is how we read your columns\" appears with one dropdown per field; change whichever is wrong, and anything left on \"Do not import\" stays out.",
                    "The summary tells you \"Rows ready to import\", \"Rows with problems left out\" and \"Rows with no product code, skipped\", with concrete examples such as \"Row 214 (SKU-001): \"N/A\"\".",
                    "You confirm with \"Import\" — the button carries the count in brackets — and when it finishes the screen says how many products came in and how many rows were left out.",
                    "The second panel, \"Start with these\", ranks your products by the money they move, not alphabetically and not by row order.",
                    "The bar at the top reads \"X% of this month's purchase already configured\" and underneath clarifies \"The bar measures money, not rows\", with the count of finished products over the total.",
                    "Each row shows the projected sales, what replacing them is worth, how much it weighs inside the month, the cumulative share, and a \"Missing\" column naming exactly which datum you do not have.",
                    "At the end of each row there are three boxes — Stock, Cost and Lead time (days) — and a \"Save\" button that confirms with the word \"Saved\".",
                ],
                "fields": [
                    ("Current stock", "How many units you physically hold today, not counting anything in transit."),
                    ("Cost", "What YOU pay your supplier per unit — not the price you sell it at."),
                    ("Lead time (days)", "How many days pass between placing the order with your supplier and the goods being in your warehouse."),
                    ("Minimum order", "The smallest quantity that supplier accepts per order, if your file carries it."),
                    ("Supplier", "Who you buy that product from. It is what lets the order be grouped and, later, the supplier be measured."),
                    ("Projected sales", "How many units we expect you to sell in the period, from your own history. It is a forecast, not a target."),
                    ("Worth", "What replacing those sales will cost you: projected units times what you pay per unit. This is the figure the list is ranked by."),
                    ("Cumulative", "Counting this product and everything above it, how much of your purchase for the period you have covered."),
                    ("Missing", "What we still do not know about that product: stock, cost, lead time, price or supplier."),
                ],
                "tasks": [
                    (
                        "Import stock from your system's file",
                        " 1. Under \"Or upload your system's file\", press \"Choose file\" and pick your ERP export."
                        " 2. Check \"This is how we read your columns\" and fix any wrong assignment."
                        " 3. Set \"Do not import\" on the columns you do not want to bring in."
                        " 4. Look at the count of ready rows and of rows being left out."
                        " 5. Press \"Import\" and wait for the message with the total of products imported."
                    ),
                    (
                        "Reach 80% of your purchase without configuring the whole catalogue",
                        " 1. Scroll down to the \"Start with these\" panel."
                        " 2. Follow the \"Cumulative\" column from the top down."
                        " 3. Fill in Stock, Cost and Lead time (days) on each row and press \"Save\"."
                        " 4. Stop when the cumulative reaches 80%: the rest barely move money."
                    ),
                    (
                        "Fix a product whose \"Missing\" says cost",
                        " 1. Find the row under \"Start with these\" and read what is missing."
                        " 2. Type into the \"Cost\" box what you pay your supplier per unit."
                        " 3. Press \"Save\" and wait for it to read \"Saved\"."
                        " 4. Use \"Refresh\" to see the bar recomputed with that row now complete."
                    ),
                ],
                "gotchas": [
                    "A product with no stock or no cost does not enter the traffic light, not even in green: it simply does not appear. Without a lead time it does appear, but planned on an assumed 15 days, so a supplier who takes 45 will be flagged to you too late.",
                    "If a product appears more than once in the file, the last row wins. Rows with negative stock or cost are left out.",
                    "The screen invents no zeros. If you save a row without entering the stock of a product that has no inventory recorded yet, it asks you for it: an invented zero is indistinguishable from a counted one and would turn a full warehouse red.",
                    "Until you have given any cost or price, the list is ranked by volume and says so: \"You have not given us costs or prices yet, so we rank by volume\". Upload costs and it ranks by money again.",
                    "The \"Start with these\" panel needs a forecast to prioritise with. If you have not uploaded sales yet, it shows a notice and sends you to do that first.",
                ],
            },
            {
                "name": "Suppliers",
                "route": "/proveedores",
                "image": "proveedores",
                "purpose": (
                    "A supplier is whoever sells you each product, and registering them is what "
                    "turns a stock alert into an order you can actually send. Here you keep their "
                    "contact details, their lead times, their payment terms and their volume price "
                    "breaks — and see how much Faro has learned from their real deliveries."
                ),
                "walkthrough": [
                    "The \"Suppliers\" header carries two entries: \"Scorecard\", which opens the performance screen, and \"Add supplier\".",
                    "If you have none yet, the screen shows an empty state explaining what they are for and offering \"Add first supplier\".",
                    "The form asks for Name — the only required field —, Email (to send PO), Phone, WhatsApp, Payment terms, Lead time (days), Variability (days) and Notes.",
                    "\"Payment terms\" is a dropdown whose options are written in Spanish in both languages: Contado, 15 días, 30 días, 60 días, 90 días and Otro.",
                    "The lead time arrives at 15 days and the variability at 3; you can change both before creating the card.",
                    "Under the lead-time field a note fixes the precedence: that time applies to every product from this supplier that does not have its own, and the one you set on a product card wins.",
                    "The table lists Name, Lead time, Variability, Learning, Payment terms, Email, Phone / WhatsApp and Actions.",
                    "The \"Learning\" column states where each supplier stands: \"No deliveries from this supplier recorded yet…\", \"n of 3 deliveries recorded…\", \"Learned from n deliveries: X days on average…\", or the case where the recorded deliveries say nothing.",
                    "The tag icon at the end of the row expands that supplier's \"Price breaks\", with SKU, Min. quantity, Unit price and Notes.",
                    "The other two icons edit and delete the card; deleting asks for confirmation and warns that the action is irreversible.",
                ],
                "fields": [
                    ("Name", "The only required field. It is the key that ties products, receptions and the scorecard together, and it is matched ignoring capitalisation."),
                    ("Email (to send PO)", "The address purchase orders go out to from Faro."),
                    ("Phone", "The supplier's phone contact. Shown in the table next to the WhatsApp number."),
                    ("WhatsApp", "The number notices and orders can be sent to over WhatsApp."),
                    ("Payment terms", "How many days they give you to pay. It feeds the cash calendar, which estimates how much falls due each week."),
                    ("Lead time (days)", "Days it takes to deliver to you from when you place the order. Applies to every one of their products without its own."),
                    ("Variability (days)", "How much that time can vary. If it says 15 days but sometimes arrives in 18, put 3. More variability means more safety stock."),
                    ("Notes", "Special conditions, contact, remarks. Free text for you."),
                    ("Learning", "A read-only column: how many of their deliveries you have recorded, and whether that is enough for Faro to replace the configured lead time."),
                    ("Price breaks: Min. quantity / Unit price", "From that quantity on, each unit costs that price. Used to suggest when it is worth ordering more."),
                ],
                "tasks": [
                    (
                        "Register a supplier so you can send them orders",
                        " 1. Press \"Add supplier\"."
                        " 2. Type the name exactly as you use it on your products."
                        " 3. Fill in the email: with no email and no WhatsApp, sending orders skips them."
                        " 4. Set the lead time and the variability to what that supplier promises you."
                        " 5. Pick the payment terms and press \"Create supplier\"."
                    ),
                    (
                        "Load a volume price break",
                        " 1. On the supplier's row, press the tag icon to expand \"Price breaks\"."
                        " 2. Type the SKU the scale applies to."
                        " 3. Enter the minimum quantity from which the price drops and the unit price at that quantity."
                        " 4. Press \"Add tier\" and repeat for each rung of the scale."
                    ),
                    (
                        "Get Faro to learn a supplier's real lead time",
                        " 1. Record the arrival of every purchase order as you receive it."
                        " 2. Come back to this screen and read the \"Learning\" column."
                        " 3. Once it holds 3 recorded deliveries the text changes to \"Learned from n deliveries\", and that becomes the number we plan with."
                        " 4. If the learned figure differs a lot from what the supplier promises, correct their card too, so the scorecard measures them against a real promise."
                    ),
                ],
                "gotchas": [
                    "Faro does not replace the lead time you configured with the learned one until it has 3 recorded receptions from that supplier. With a single one, a freak delivery — a public holiday, a strike, a stranded truck — would rewrite the lead time for all their products.",
                    "If every recorded delivery arrived the same day it was ordered, the observed average is 0 and says nothing about the supplier. Faro declares it unusable, says so in the \"Learning\" column, and keeps using the lead time you configured.",
                    "Payment terms are text that gets interpreted for the cash calendar. It understands \"contado\", \"contra entrega\", \"anticipo\", \"prepago\" and \"COD\" as 0 days; \"N meses\" as N times 30; \"quincenal\" as 15; and the first number in things like \"30 días\" or \"net 30\".",
                    "What is NOT understood stays as an unknown term, reported as such rather than filled with an invented number: instalment schedules like \"2x30\" or \"30/60/90\", ranges like \"30-45 días\", and free text like \"a convenir\".",
                    "The supplier's lead time governs all of their products that have none of their own; whatever you type on a product card always beats the supplier's value.",
                ],
            },
            {
                "name": "Supplier scorecard",
                "route": "/proveedores/scorecard",
                "image": "scorecard",
                "purpose": (
                    "The screen that compares what each supplier promises with what they actually "
                    "deliver. It builds itself from the receptions you recorded: real lead time "
                    "against declared lead time, share of deliveries on time, how much of what you "
                    "ordered arrived, and how much you have bought from them."
                ),
                "walkthrough": [
                    "You get here from the \"Scorecard\" button on Suppliers, and go back with \"Back to Suppliers\" at the top right.",
                    "Everything shown comes from recorded receptions: a supplier with no arrival on file has no row.",
                    "If a supplier has been taking longer than usual, an amber notice at the top says how many they are, by how many days they drifted, and over how many receptions the comparison was made.",
                    "That same notice explains its method at the foot: a 3-sigma statistical control rule over the median and median absolute deviation of each supplier's own history.",
                    "The table has nine columns: Supplier, Receptions, Real lead time, Declared, Trend, % On time, % Fill rate, Purchased value and Last reception.",
                    "\"Real lead time\" is a minimum–maximum range of what was observed, not an average: two deliveries of 5 and 25 days show as 5–25d, never as 15d.",
                    "\"% On time\" is painted green from 70%, amber from 40% and red below that, so the row reads at a glance.",
                    "\"Purchased value\" shows a dash if no line of those orders carried a unit cost, and a \"≥\" in front of the amount when only some of them did.",
                    "When a cell cannot be measured, the screen says so in words — \"Inconclusive\", \"Not yet\" — instead of printing a zero that would invite the wrong decision.",
                    "If there is no row at all, there are two distinct empty states: one for when you have recorded no receptions yet, and one for when you did record them but with no supplier assigned to the products.",
                ],
                "fields": [
                    ("Supplier", "The name as it came in on the orders. Capitalisation is ignored when grouping, so \"Acme\" and \"ACME\" are a single row."),
                    ("Receptions", "How many of their arrivals you have recorded. It is the sample size behind everything else on the row."),
                    ("Real lead time", "The range of days observed between order and arrival, from the minimum to the maximum."),
                    ("Declared", "The lead time on the supplier's card. It shows a dash if nobody ever filled it in, so they are not scored against a promise they never made."),
                    ("Trend", "Whether their recent lead time stepped outside their own normal range. Shows the days of drift, \"Stable\", or \"Not yet\" when there is nothing to compare with."),
                    ("% On time", "Share of deliveries whose real lead time was at or below the declared one. Blank when no lead time is declared."),
                    ("% Fill rate", "How much of what you ordered actually arrived, capped at 100%."),
                    ("Purchased value", "What you have bought from them, summing the lines that do carry a unit cost."),
                    ("Last reception", "The date of the most recent arrival you recorded from that supplier."),
                ],
                "tasks": [
                    (
                        "Compare what a supplier promises with what they deliver",
                        " 1. Find their row and read \"Declared\" — what their card claims."
                        " 2. Compare it with \"Real lead time\", the range you have actually seen."
                        " 3. Read \"% On time\" to see how often they meet that promise."
                        " 4. If the real figure sits consistently above the declared one, correct the lead time on their card under Suppliers."
                    ),
                    (
                        "Understand why a row says \"Inconclusive\"",
                        " 1. Hover the text to read the full explanation."
                        " 2. It means every recorded delivery arrived the same day it was ordered."
                        " 3. Check whether you recorded the right arrival date when receiving those orders."
                        " 4. While it stays that way, Faro plans with the lead time you configured, not with a zero."
                    ),
                    (
                        "Fill the scorecard when it is empty",
                        " 1. Read which of the two empty states you got: no receptions, or receptions with no supplier."
                        " 2. If it is the first, record the arrival of a purchase order from the history."
                        " 3. If it is the second, set the supplier on each product."
                        " 4. Come back to the scorecard: it fills up with the next arrivals you record."
                    ),
                ],
                "gotchas": [
                    "The \"Trend\" column needs at least 6 receptions before it says anything: 2 in the recent window and 4 in the historical baseline. With fewer it shows \"Not yet\" rather than calling \"Stable\" a lead time nobody has seen vary.",
                    "The fill rate is capped at 100%. A supplier who ships 120 against an order of 100 filled the order and also left you 20 units nobody asked for: that is a stock problem, not a merit.",
                    "\"Purchased value\" is never a zero for lack of costs. If no line carried a unit cost it shows a dash, and if only some did it shows \"≥\" in front of the amount to warn you it is a floor, not the total.",
                    "\"Declared\" is blank when nobody filled the lead time in on the supplier's card, and \"% On time\" goes with it. That is deliberate: the 15 days Faro assumes are not a promise from the supplier and cannot be held against them.",
                    "Faro is stricter about accusing than about adjusting: 3 receptions are enough to learn a lead time, but 6 are required before a supplier is flagged for running late. Accusing costs more than adjusting.",
                ],
            },
        ],
    },
}

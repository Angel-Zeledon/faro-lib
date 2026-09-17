"""Manual section: the six analysis screens.

Content only — no imports, no logic. The PDF builder reads SECTION.
Every figure and label here was taken from the code that renders it.
"""

SECTION = {
    "id": "analysis",
    "es": {
        "title": "Análisis",
        "intro": (
            "Estas seis pantallas son las que explican el porqué de cada decisión de compra. "
            "Aquí ves lo que Faro proyecta para cada producto, cómo se comporta ese producto en tu "
            "propio historial, qué pasaría si cambiaran los supuestos, qué has hecho con las "
            "recomendaciones y qué te puede contar la IA sobre todo eso. Ninguna de ellas genera "
            "órdenes de compra: son para entender antes de comprar, y el resto de la app se encarga "
            "de lo demás."
        ),
        "screens": [
            {
                "name": "Pronóstico por producto",
                "route": "/pronosticos",
                "image": "forecast",
                "purpose": (
                    "Es la pantalla donde ves, producto por producto, qué vendiste y qué espera Faro "
                    "que vendas. Reúne la curva histórica, el pronóstico con su rango de "
                    "incertidumbre, la tabla de los modelos que compitieron por ese SKU y la calidad "
                    "de sus datos. Todo lo que la app decide después — el semáforo, el punto de "
                    "reorden, la cantidad a pedir — sale del modelo que gana aquí."
                ),
                "walkthrough": [
                    "Arriba a la izquierda está el título «Predicciones» con la cantidad de SKUs de la sesión seleccionada; a la derecha están «Exportar Todos los SKUs», «Comparar», el selector de sesión y «Actualizar».",
                    "Faro selecciona sola la sesión activa de planificación al abrir la pantalla, y si no hay ninguna resuelta usa la última sesión completada.",
                    "La columna izquierda lista tus productos, con el buscador «Buscar SKUs…» arriba y la paginación abajo; cada tarjeta muestra el SKU, una miniatura de su serie y su semáforo.",
                    "Al hacer clic en un producto, la cabecera del panel derecho muestra el SKU, su tipo de serie, cuántas filas de historial tiene, su porcentaje de calidad, su «Precisión» y su semáforo.",
                    "Debajo hay cinco pestañas: «Forecast», «Cómo se vende», «Métricas», «Calidad» e «Inventario».",
                    "En «Forecast», la barra de herramientas te deja cambiar la «Granularidad» (D, W, M, Q, Y), el «Gráfico» (Línea o Barra) y el «Modelo» que se dibuja.",
                    "Los chips de «Modelo» son de selección múltiple: el primero que elijas manda sobre los ejes y las estadísticas, y los demás se superponen como líneas de otro color para comparar.",
                    "El botón «Rango probable» enciende y apaga la nube de incertidumbre, que se dibuja como tres anillos anidados: «Rango amplio», «Rango probable» y «Zona central».",
                    "La franja de estadísticas bajo la barra resume promedio de ventas, variabilidad, mínimo, máximo, puntos históricos, pasos de forecast, «WAPE del elegido» y «Mejor modelo».",
                    "La pestaña «Métricas» abre la tabla de todos los modelos que corrieron sobre ese SKU, ordenada por «Costo» del mejor al peor, con la etiqueta «MEJOR» sobre el ganador.",
                    "Al pie de la página, el panel «Qué habría pasado comprando así» repite tu política de compra sobre las ventas que ya ocurrieron y la compara contra pedir lo mismo que la vez anterior.",
                ],
                "fields": [
                    ("Precisión", "Uno menos el WAPE del modelo elegido para ese producto, en porcentaje. No aparece cuando el error del mejor modelo es exactamente cero, porque eso significa que no hubo venta que acertar."),
                    ("WAPE del elegido", "El WAPE del modelo que Faro terminó usando para ese producto — no el WAPE más bajo de la tabla. El WAPE es la suma de los errores absolutos dividida entre la venta real total."),
                    ("Mejor modelo", "El modelo ganador del SKU, excluidas las referencias. Se muestra como «Modelo 1» … «Modelo 9»: el nombre del algoritmo no cambia ninguna decisión de compra, y la numeración es la misma en toda la app."),
                    ("Costo", "Lo que costaría equivocarse con ese modelo, contando un faltante tres veces más caro que un sobrante. Es la columna con la que se elige el modelo ganador."),
                    ("MAE", "Error absoluto promedio, en unidades del producto."),
                    ("RMSE", "Como el MAE, pero elevando los errores al cuadrado: castiga más los fallos grandes aislados."),
                    ("Sesgo", "Error promedio con signo. Positivo significa que el modelo pronostica de más; negativo, de menos."),
                    ("Folds", "Cuántos cortes de validación hacia adelante se usaron para medir ese modelo."),
                    ("Tipo", "De dónde viene la fila: un modelo entrenado o una referencia. Las referencias — «Referencia (último valor)», «Referencia (temporada)» y «Referencia (promedio)» — son la vara a superar, no candidatos."),
                    ("Frecuencia / Vista", "Al pie del gráfico: la frecuencia original de tus datos y el nivel al que estás mirando la serie en este momento."),
                ],
                "tasks": [
                    (
                        "Comparar dos modelos sobre el mismo producto",
                        " 1. Elige el producto en la lista de la izquierda. 2. Quédate en la pestaña «Forecast». 3. En «Modelo», haz clic sobre un segundo chip: su pronóstico se dibuja encima en otro color. 4. Apaga «Rango probable» si la nube estorba; solo se dibuja con un modelo seleccionado. 5. Abre «Métricas» para ver cuál de los dos tiene menor «Costo».",
                    ),
                    (
                        "Llevarte el pronóstico a Excel",
                        " 1. Selecciona el producto y la granularidad que quieres. 2. Abre el menú «Exportar» de la barra del gráfico. 3. Elige «Excel (.xlsx)» para ese SKU, o «CSV — datos del gráfico» si solo quieres las series. 4. Para todo el catálogo de una vez, usa «Exportar Todos los SKUs» arriba: genera un archivo con una hoja por producto.",
                    ),
                    (
                        "Ver si un reentrenamiento mejoró las cosas",
                        " 1. Pulsa «Comparar» en la barra superior. 2. Elige la otra sesión en «Comparando con:». 3. Elige el SKU en el selector que aparece al lado. 4. La pantalla se parte en dos: arriba la sesión A, abajo la B, con el mismo producto en ambas.",
                    ),
                ],
                "gotchas": [
                    "La tabla de métricas se ordena por «Costo», no por WAPE, así que el modelo con la etiqueta «MEJOR» puede no ser el de menor WAPE. Es a propósito: quedarse sin producto cuesta más que sobrar, y esa es la comparación con la que se compra.",
                    "Las referencias nunca ganan. Aparecen en la tabla para que veas qué tan lejos quedó pronosticar «lo mismo que la vez pasada», pero Faro no compra con ellas y no reciben la etiqueta «MEJOR».",
                    "Los modelos se llaman «Modelo 1» a «Modelo 9». Compiten nueve modelos entrenados, más tres referencias y el «Modelo combinado», que es la mezcla de los numerados.",
                    "«Rango probable» solo se puede encender con un único modelo seleccionado, y algunos modelos no guardaron cuantiles: en ese caso el botón queda apagado y al lado dice «(sin rango)».",
                    "No todos los modelos corren sobre todos los productos. Faro asigna a cada SKU los modelos que le convienen según su tipo de serie, siempre dentro de los que elegiste al entrenar, así que dos productos de la misma sesión pueden mostrar listas de modelos distintas.",
                ],
            },
            {
                "name": "Cómo se vende",
                "route": "/pronosticos",
                "image": "pattern",
                "purpose": (
                    "La pestaña «Cómo se vende» mira solo tu historial: ningún modelo interviene y "
                    "nada de lo que ves aquí es una predicción. Sirve para responder tres preguntas "
                    "de negocio: si el producto está creciendo de verdad o solo repite su patrón, qué "
                    "días o qué meses vende más, y qué tan disparejo es de un período a otro."
                ),
                "walkthrough": [
                    "Se entra desde la pestaña «Cómo se vende» de la pantalla de pronósticos, con un producto ya seleccionado.",
                    "El primer bloque, «¿Está creciendo de verdad?», dibuja dos líneas: la tenue es tu venta tal cual y la gruesa es la misma venta con el sube y baja de siempre ya descontado.",
                    "Si la línea gruesa sube, el crecimiento es real; si va plana, lo que veías era el patrón repitiéndose.",
                    "Bajo el gráfico, una frase dice qué porcentaje del movimiento del producto explica el patrón (semanal o anual) y qué porcentaje explica la tendencia; lo que falta para el cien es variación que no se repite.",
                    "Cuando el producto no tiene historial suficiente para separar las dos cosas, ese bloque no dibuja nada y en su lugar dice cuántos períodos hacen falta y cuántos hay.",
                    "El segundo bloque promedia tus ventas «Por día» de la semana o «Por mes», según el chip que elijas, y bajo cada barra indica sobre cuántos períodos está calculada.",
                    "El tercer bloque, «Cuánto vendes por día / semana / mes», reparte tus períodos en rangos y cuenta cuántas veces caíste en cada uno, con una marca en el «Promedio».",
                    "La cola derecha de ese reparto son los picos que tu stock tiene que aguantar; es la lectura que justifica el inventario de seguridad.",
                ],
                "fields": [
                    ("Venta real", "La serie tal como está en tu historial, sin tocar."),
                    ("Tendencia", "La misma serie con el componente que se repite ya descontado."),
                    ("Patrón de la semana / del año", "Qué porcentaje del movimiento del producto explica el ciclo que se repite."),
                    ("Tendencia (porcentaje)", "Qué porcentaje del movimiento explica la subida o bajada de fondo. Lo que sobra hasta cien es variación que no se repite."),
                    ("Promedio por día de la semana", "El promedio de lo vendido cada lunes, cada martes, y así. Es promedio, nunca suma: un día con más registros se vería más alto por una razón que no tiene que ver con cuánto vende."),
                    ("Promedio por mes", "Lo mismo, por mes calendario. Sirve para anticipar los meses fuertes."),
                    ("Promedio", "La marca vertical del histograma del último bloque, puesta en el valor promedio de un período."),
                ],
                "tasks": [
                    (
                        "Decidir qué día conviene recibir mercadería",
                        " 1. Abre «Cómo se vende» con el producto seleccionado. 2. En el segundo bloque elige el chip «Por día». 3. Lee qué días quedan más altos: son los días en que el producto tiene que estar en góndola. 4. Programa la recepción uno o dos días antes de ese pico.",
                    ),
                    (
                        "Distinguir crecimiento real de temporada",
                        " 1. Abre «Cómo se vende». 2. Mira si la línea gruesa de «¿Está creciendo de verdad?» sube o va plana. 3. Lee la frase de abajo: si el patrón explica mucho más que la tendencia, lo que subió fue la temporada. 4. Si el bloque dice que falta historial, no hay respuesta todavía; no la deduzcas del gráfico de arriba.",
                    ),
                ],
                "gotchas": [
                    "Nada de esta pestaña es un pronóstico. Son promedios de lo que ya vendiste, y la propia pantalla lo dice bajo cada gráfico.",
                    "El promedio por día de la semana necesita al menos 8 semanas de historial y ventas en al menos 5 días distintos. Si cargaste tus ventas por semana o por mes, no se pueden separar por día y la pantalla lo avisa.",
                    "El promedio por mes necesita al menos 12 meses de historial; con menos, comparar meses compara temporadas incompletas.",
                    "Las columnas que se apoyan en menos de 3 registros se ocultan, y la pantalla dice cuántas ocultó. Una barra sostenida por dos datos es ruido dibujado como patrón.",
                ],
            },
            {
                "name": "Simulador de escenarios",
                "route": "/escenarios",
                "image": "escenarios",
                "purpose": (
                    "El simulador responde «¿qué pasa si…?» sin tocar nada. Armas una lista de reglas "
                    "— más demanda, una promoción, un proveedor atrasado, otro nivel de servicio — y "
                    "Faro vuelve a correr exactamente el mismo cálculo del semáforo con esos "
                    "supuestos, y te muestra el plan actual y el escenario lado a lado."
                ),
                "walkthrough": [
                    "Arriba a la derecha eliges la «Sesión de pronóstico» sobre la que quieres simular; solo aparecen las sesiones terminadas.",
                    "La columna izquierda es el constructor: «Reglas del escenario», que abre con una regla de «Cambio de demanda» ya puesta.",
                    "Cada regla empieza con un selector de tipo, y al lado se lee en una línea qué hace ese tipo.",
                    "Hay cuatro tipos: «Cambio de demanda», «Promoción», «Atraso de proveedor» e «Inventario de seguridad».",
                    "Los campos cambian según el tipo: los dos primeros piden multiplicador y, opcionalmente, SKU o categoría y un rango de fechas; el atraso pide días y un proveedor opcional; el inventario de seguridad pide un nivel de servicio.",
                    "«Agregar regla» añade otra línea; la equis de la derecha la quita.",
                    "«Simular» corre el escenario y llena la columna derecha; a partir de ahí el botón dice «Volver a simular».",
                    "Arriba a la derecha, «Plan actual vs escenario» compara seis cifras — unidades a pedir, valor de la compra, SKUs a pedir, SKUs PEDIR_YA, SKUs PEDIR_PRONTO y SKUs con sobrestock — cada una con su diferencia.",
                    "Debajo, «Productos más afectados» lista los SKUs cuyo semáforo o cantidad cambió, ordenados por el tamaño del cambio, con el antes y el después de cada uno.",
                    "Si el escenario te sirve, escríbele un nombre en «Guardar escenario»: queda en «Escenarios guardados» y se vuelve a correr con «Cargar».",
                ],
                "fields": [
                    ("Multiplicador", "Cuánto se multiplica la demanda pronosticada. 1.4 es vender un 40 % más. Se acepta entre 0.01 y 10."),
                    ("SKU (opcional) / Categoría (opcional)", "A qué productos aplica la regla. Se llena uno o el otro, nunca los dos. Vacíos, la regla aplica a todo el portafolio."),
                    ("Desde / Hasta", "El rango de fechas en el que la regla actúa. En una «Promoción» son obligatorias; en un «Cambio de demanda» son opcionales."),
                    ("Días de atraso", "Días que se le suman al tiempo de entrega, entre 0 y 365. Se suman sobre el tiempo de entrega que el semáforo ya usa para ese producto, no sobre un valor cualquiera."),
                    ("Proveedor (opcional)", "Limita el atraso a un proveedor. Vacío, atrasa a todos."),
                    ("Nivel de servicio", "Con qué probabilidad quieres cubrir la demanda durante el tiempo de entrega. El selector ofrece 90 %, 95 %, 97 % y 99 %."),
                    ("Plan actual / Escenario / Diferencia", "Las tres columnas de la comparación: tu plan tal como está hoy, el mismo cálculo con las reglas aplicadas, y la resta entre ambos."),
                    ("{n} series ajustadas", "Cuántas series de pronóstico tocaron las reglas de demanda. Una regla sin SKU ni categoría las toca todas."),
                    ("Semáforo", "En «Productos más afectados», el semáforo antes y después de las reglas, con una flecha entre los dos."),
                ],
                "tasks": [
                    (
                        "Prepararte para una temporada alta",
                        " 1. Elige la sesión de pronóstico. 2. Deja la regla «Cambio de demanda» y sube el multiplicador a lo que esperas vender de más. 3. Deja SKU y categoría vacíos para aplicarlo a todo, o escribe la categoría de temporada. 4. Pulsa «Simular». 5. Lee la fila «SKUs PEDIR_YA» de la comparación: es cuántos productos se te vuelven urgentes con esa demanda.",
                    ),
                    (
                        "Medir el daño de un proveedor atrasado",
                        " 1. Cambia el tipo de la regla a «Atraso de proveedor». 2. Escribe los días de atraso. 3. Escribe el nombre del proveedor tal como aparece en tus datos, o déjalo vacío para todos. 4. Pulsa «Simular» y revisa «Productos más afectados»: la última columna muestra el tiempo de entrega antes y después.",
                    ),
                    (
                        "Guardar y reutilizar un escenario",
                        " 1. Arma las reglas y simula hasta que el resultado te convenza. 2. Escribe un nombre en «Guardar escenario» y pulsa «Guardar». 3. Aparece en «Escenarios guardados». 4. Para volver a correrlo, pulsa «Cargar»: las reglas vuelven al constructor y el resultado se recalcula con los datos de hoy.",
                    ),
                ],
                "gotchas": [
                    "Simular no cambia nada: no mueve tu stock, no genera órdenes de compra, no reentrena y no modifica el pronóstico guardado. El resultado se calcula y se muestra, no se guarda.",
                    "El escenario no inventa una matemática nueva. Usa el mismo cálculo del semáforo que ves en Inventario, alimentado con los supuestos que escribiste; por eso las cifras de «Plan actual» coinciden con las de esa pantalla.",
                    "Un escenario admite hasta 50 reglas y queda pegado a una sesión. Si cambias de sesión en el selector, la comparación en pantalla se limpia: sería un resultado viejo bajo un título nuevo.",
                    "Una regla apunta a un SKU o a una categoría, nunca a los dos. Al escribir en uno de los campos, el otro se vacía solo.",
                    "Guardar y eliminar escenarios requiere rol de analista o administrador. Un usuario de solo lectura puede simular y cargar escenarios, pero no ve el bloque de guardar.",
                ],
            },
            {
                "name": "Impacto y ROI",
                "route": "/impacto",
                "image": "impacto",
                "purpose": (
                    "«Impacto & ROI» es el registro de lo que hiciste con Faro: cuántas órdenes "
                    "generaste, cuántas líneas urgentes pediste, cuánto valor de compra manejaste y "
                    "qué tanto seguiste las recomendaciones. La pantalla es deliberadamente "
                    "conservadora: no estima ahorros y no cuenta quiebres evitados, porque ninguna de "
                    "esas dos cosas se puede medir con tus datos. Todo lo que ves es una suma de "
                    "registros que la app ya escribió."
                ),
                "walkthrough": [
                    "Lo primero es «Resumen de <mes>», el recuento del mes que acaba de cerrar: el mismo que te llega por correo el primer día de cada mes.",
                    "Si no generaste ninguna orden en ese mes, el bloque no muestra ceros: dice que todavía no hay suficiente historial y nombra el mes del que habla.",
                    "Sus mosaicos son órdenes de compra generadas, porcentaje de adopción, líneas urgentes que pediste, baja del sobrestock y compras gestionadas.",
                    "Al pie del recuento, «De dónde sale cada número» explica en una frase el origen de cada cifra y qué es lo que no afirma.",
                    "Debajo, «Tu historial con Faro» acumula todo el tiempo: órdenes generadas, desde qué fecha, cuántos días activo, líneas urgentes pedidas y el valor de las compras gestionadas.",
                    "Cuando ningún producto tiene costo unitario, esa última cifra se reemplaza por las unidades totales ordenadas y te sugiere cargar los costos.",
                    "«Confianza en las recomendaciones» muestra tu tasa de adopción con una barra: cuántas de las líneas sobre las que decidiste terminaste pidiendo.",
                    "«Evolución mensual» es la tabla de los últimos seis meses con pedidos, líneas urgentes pedidas, valor gestionado, porcentaje de adopción y baja del sobrestock.",
                    "Al final, un enlace lleva a la pantalla de pedidos para registrar llegadas, y «Por qué esto importa» cierra explicando que son registros de lo que hiciste, no una medición de lo que evitaste.",
                ],
                "fields": [
                    ("órdenes de compra generadas", "Cuántas órdenes generaste desde Faro. Es un conteo de registros, no una estimación."),
                    ("días activo", "Días de calendario distintos en los que generaste al menos una orden. No es el tiempo transcurrido desde la primera: quien pidió dos veces con un año de diferencia lleva 2 días activo, no 365."),
                    ("líneas urgentes que pediste", "Líneas marcadas PEDIR_YA que realmente ordenaste. Cuenta líneas, no productos distintos, e incluye órdenes que todavía no llegaron."),
                    ("compras gestionadas con Faro", "Unidades ordenadas por su costo unitario, tomado de tus propios datos. Solo suma las líneas que tienen costo registrado."),
                    ("tasa de adopción", "Líneas que aprobaste o ajustaste, dividido entre las líneas sobre las que llegaste a decidir. Las recomendaciones que dejaste pasar sin tocar no entran por ningún lado."),
                    ("Baja del sobrestock", "La diferencia entre el valor de tu inventario en sobrestock al inicio del mes y al inicio del siguiente. Son dos mediciones restadas, no un modelo."),
                    ("Aún no hay suficiente historial", "Lo que dice la columna de sobrestock cuando falta una de las dos mediciones mensuales."),
                    ("Tu sobrestock subió este mes", "Lo que dice esa misma columna cuando las dos mediciones existen y el sobrestock creció. Es un resultado, no falta de datos."),
                    ("≥ delante de un monto", "Solo algunas de las líneas ordenadas tenían costo unitario, así que la cifra es un piso: el valor real es mayor."),
                ],
                "tasks": [
                    (
                        "Saber si estás siguiendo las recomendaciones",
                        " 1. Abre Impacto. 2. Busca «Confianza en las recomendaciones». 3. Lee el porcentaje y la frase de al lado: dice cuántas líneas seguiste de cuántas decididas. 4. Si el número de descartadas es alto, revisa los tiempos de entrega y los niveles de servicio de esos productos antes de culpar al pronóstico.",
                    ),
                    (
                        "Cerrar el mes con cifras defendibles",
                        " 1. Abre Impacto el primer día del mes. 2. Lee «Resumen de <mes anterior>». 3. Copia solo lo que el mosaico afirma; si un número dice «No disponible», dilo así en vez de poner un cero. 4. Si necesitas el valor completo en dinero, carga el costo unitario de los productos que no lo tienen y vuelve a mirar.",
                    ),
                ],
                "gotchas": [
                    "Esta pantalla nunca dice «Faro te ahorró tanto». No estima ahorros ni cuenta quiebres evitados, porque para eso habría que saber qué habría pasado si no hubieras comprado, y eso no está en tus datos.",
                    "La baja del sobrestock no se le atribuye a Faro. El sobrestock también baja al vender, al registrar merma, al borrar productos y al reentrenar; la pantalla dice qué se movió, no quién lo movió.",
                    "Un número que no se puede calcular aparece como «No disponible» con la razón, nunca como cero. Un cero se leería como un resultado.",
                    "«Líneas urgentes que pediste» suma líneas: los mismos 30 productos urgentes pedidos cada mes durante un año suman 360. Tampoco comprueba que la mercadería haya llegado.",
                    "La tasa de adopción no dice qué parte de todo lo que Faro te sugirió llegaste a seguir. Las recomendaciones que nunca tocaste no quedan registradas, así que no están en ninguno de los dos lados de la división.",
                ],
            },
            {
                "name": "Analista IA",
                "route": "/asistente",
                "image": "asistente",
                "purpose": (
                    "El Analista IA es un chat que responde sobre tus propios datos: la precisión de "
                    "los modelos, la calidad de tus series, las tendencias del pronóstico y el estado "
                    "de tu inventario. Guarda las conversaciones, las puedes marcar como favoritas y "
                    "buscarlas después, y cada usuario ve solo las suyas. Una conversación puede "
                    "colgar de una sesión de pronóstico o quedarse en «— General —» para preguntas de "
                    "concepto."
                ),
                "walkthrough": [
                    "La columna izquierda lista tus conversaciones, con «Nueva Conversación» arriba y un buscador debajo.",
                    "Las conversaciones se agrupan en «Favoritos» y «Recientes»; la estrella de cada fila las mueve de un grupo al otro y el basurero las borra, pidiendo confirmación antes.",
                    "El título de una conversación nueva lo escribe la propia IA a partir de tu primera pregunta.",
                    "Arriba del hilo, el selector «Sesión:» decide de qué sesión de pronóstico sale el contexto; «— General —» desactiva ese contexto y deja solo preguntas de concepto y de uso de la plataforma.",
                    "En un hilo vacío, Faro propone «Preguntas frecuentes» para arrancar con un clic.",
                    "Escribes abajo y envías con Enter; Shift+Enter hace un salto de línea.",
                    "Cada respuesta lleva una etiqueta que dice de dónde salió: «Tus datos», «Respuesta general», «General», «Fuera de tema», «Sin acceso» o «Error».",
                    "El botón de filtro junto al campo de texto abre «Fuentes de Datos» y te deja limitar el contexto a resumen de datos, desempeño de modelos, precisión por producto, inventario, calidad de datos, elección de modelo, configuración o tendencias del pronóstico.",
                    "Si la respuesta no llega a tiempo o el servicio falla, tu pregunta se queda en el hilo con un mensaje que explica qué pasó, para que la reintentes sin volver a escribirla.",
                ],
                "fields": [
                    ("Sesión:", "La sesión de pronóstico de la que sale el contexto de la conversación. Cambia de qué entrenamiento hablan la precisión y las tendencias."),
                    ("— General —", "Conversación sin sesión: la IA responde sobre conceptos de pronóstico y sobre cómo funciona la plataforma, sin mirar tus datos."),
                    ("Fuentes de Datos", "El filtro que limita a qué partes del contexto puede mirar la respuesta. Sin filtro, el botón dice «Todas las fuentes»."),
                    ("Tus datos", "La etiqueta de una respuesta construida sobre el contexto de tu sesión y de tu inventario."),
                    ("Respuesta general", "La etiqueta de una respuesta armada con el contexto de la sesión, pero sin búsqueda semántica previa."),
                    ("Fuera de tema", "La pregunta no se parece a nada de lo que hay en tus datos, y la IA lo dice en vez de inventar una respuesta."),
                    ("Preguntas frecuentes", "Los atajos que aparecen en una conversación vacía; al hacer clic se envían como pregunta."),
                ],
                "tasks": [
                    (
                        "Preguntar por el inventario de hoy",
                        " 1. Abre una conversación nueva. 2. Escribe la pregunta directamente, por ejemplo cuáles productos están en riesgo. 3. Lee la respuesta y contrástala con la pantalla de Inventario: el asistente lee ese mismo semáforo.",
                    ),
                    (
                        "Entender por qué un producto tiene poca precisión",
                        " 1. Elige en «Sesión:» la sesión donde entrenaste ese producto. 2. Pregunta por el SKU tal como aparece en tus datos. 3. Si quieres acotar la respuesta, abre el filtro de «Fuentes de Datos» y deja marcadas «Precisión por producto» y «Calidad de datos».",
                    ),
                    (
                        "Encontrar una conversación vieja",
                        " 1. Escribe en el buscador de la columna izquierda. 2. O marca con la estrella las conversaciones que vas a volver a usar: quedan arriba, en «Favoritos».",
                    ),
                ],
                "gotchas": [
                    "Las respuestas sobre inventario salen siempre del semáforo de la sesión activa de planificación, no de la sesión que elegiste en el chat. Es a propósito: el inventario tiene una sola respuesta en este producto, y es la que está en la pantalla de Inventario.",
                    "El contexto de inventario que recibe la IA está limitado a los primeros 40 productos. En un catálogo grande, la respuesta describe esa parte y no todo el catálogo.",
                    "La IA solo lee lo que la sesión y el inventario ya calcularon. No corre modelos, no consulta internet y no ve tus archivos originales.",
                    "Cada respuesta se construye con los últimos 6 mensajes del hilo. Lo que dijiste mucho más arriba en una conversación larga ya no está en el contexto.",
                    "Hay un límite de 20 mensajes por minuto para toda la organización, y una pregunta no puede pasar de 4000 caracteres. La respuesta también tiene un presupuesto de tiempo: si se agota, la pantalla lo dice y conserva tu pregunta.",
                ],
            },
            {
                "name": "Historial de sesiones",
                "route": "/historial",
                "image": "historial",
                "purpose": (
                    "«Historial de actualizaciones» es la lista de todas las veces que subiste tus "
                    "ventas y Faro las procesó. Desde aquí abres los resultados de cualquier "
                    "actualización terminada, la renombras para reconocerla después o la eliminas. "
                    "También es donde ves por qué falló una que no llegó a terminar."
                ),
                "walkthrough": [
                    "La pantalla abre con la tabla completa, de la actualización más reciente a la más vieja, hasta 200 filas.",
                    "Cada fila trae nombre, estado, archivo de origen, fecha de creación, horizonte, granularidad y cantidad de SKUs.",
                    "El estado va desde «Borrador» hasta «Completada», pasando por «En cola», «Calculando», «Fallida» y «Cancelada».",
                    "Hacer clic en una fila «Completada» abre sus resultados en la pantalla de pronósticos, con esa sesión ya seleccionada.",
                    "Las filas que no están completadas no son clicables: todavía no hay resultados que abrir.",
                    "Cuando una actualización aparece como «Fallida», debajo del estado se muestra el «Detalle técnico:» que reportó el motor de pronóstico.",
                    "El lápiz de la derecha renombra la fila sin salir de la tabla: Enter guarda y Escape cancela.",
                    "El basurero elimina la actualización y las proyecciones que salieron de ella, con una confirmación antes; queda deshabilitado mientras la actualización está «Calculando».",
                ],
                "fields": [
                    ("Nombre", "El nombre de la actualización. Lo puedes cambiar cuando quieras; existe solo para que la reconozcas."),
                    ("Estado", "En qué punto quedó: «Completada» tiene resultados, «Calculando» está en curso, «Fallida» se detuvo."),
                    ("Archivo", "El archivo de ventas del que salió esa actualización."),
                    ("Creada", "La fecha en que se creó."),
                    ("Horizonte", "Cuántos períodos hacia adelante pronosticó esa corrida."),
                    ("Granularidad", "Si la serie se trabajó «Diaria», «Semanal» o «Mensual»."),
                    ("SKUs", "Cuántos productos entraron en esa actualización."),
                    ("Detalle técnico:", "El motivo exacto del fallo, tal como lo reportó el motor de pronóstico."),
                ],
                "tasks": [
                    (
                        "Volver a un resultado anterior",
                        " 1. Abre Historial. 2. Ubica la fila «Completada» que buscas, por su nombre o por su fecha. 3. Haz clic en cualquier parte de la fila: se abre la pantalla de pronósticos con esa actualización seleccionada.",
                    ),
                    (
                        "Ponerle nombres que después reconozcas",
                        " 1. Pulsa el lápiz de la fila. 2. Escribe un nombre que diga qué tiene dentro, por ejemplo el mes o la bodega. 3. Pulsa Enter, o el check verde. 4. Escape cancela sin guardar.",
                    ),
                    (
                        "Averiguar por qué falló una actualización",
                        " 1. Busca la fila con estado «Fallida». 2. Lee el texto que sigue a «Detalle técnico:». 3. Si el mensaje habla de columnas o de fechas, corrige el archivo y vuelve a subirlo. 4. Si no lo entiendes, pásalo tal cual a quien te dé soporte: está escrito para eso.",
                    ),
                ],
                "gotchas": [
                    "Eliminar una actualización elimina también las proyecciones de demanda que salieron de ella, y no se puede deshacer.",
                    "No se puede eliminar una actualización mientras está «Calculando»; el botón queda apagado hasta que termine.",
                    "Renombrar y eliminar requieren rol de analista o administrador. Un usuario de solo lectura ve la tabla y abre resultados, pero no ve esos botones.",
                    "El detalle de un fallo viene en inglés desde el motor de pronóstico. No es un descuido de traducción: es el mensaje técnico exacto, y reescribirlo dificultaría el diagnóstico.",
                ],
            },
        ],
    },
    "en": {
        "title": "Analysis",
        "intro": (
            "These six screens are the ones that explain the reasoning behind every purchase "
            "decision. Here you see what Faro projects for each product, how that product behaves in "
            "your own history, what would happen if the assumptions changed, what you have done with "
            "the recommendations, and what the AI can tell you about all of it. None of them "
            "generates a purchase order: they are for understanding before you buy, and the rest of "
            "the app takes care of the buying."
        ),
        "screens": [
            {
                "name": "Forecast by product",
                "route": "/pronosticos",
                "image": "forecast",
                "purpose": (
                    "This is the screen where you see, product by product, what you sold and what "
                    "Faro expects you to sell. It brings together the historical curve, the forecast "
                    "with its uncertainty range, the table of models that competed for that SKU, and "
                    "the quality of its data. Everything the app decides afterwards — the signal, the "
                    "reorder point, the quantity to order — comes from the model that wins here."
                ),
                "walkthrough": [
                    "Top left is the title “Predictions” with the number of SKUs in the selected session; on the right are “Export All SKUs”, “Compare”, the session selector and “Refresh”.",
                    "Faro selects the active planning session on its own when the screen opens, and falls back to the most recent completed session when none is resolved.",
                    "The left column lists your products, with the “Search SKUs…” box above and pagination below; each card shows the SKU, a thumbnail of its series and its signal.",
                    "When you click a product, the header of the right-hand panel shows the SKU, its series type, how many rows of history it has, its quality percentage, its “Accuracy” and its signal.",
                    "Below that are five tabs: “Forecast”, “How it sells”, “Metrics”, “Quality” and “Inventory”.",
                    "In “Forecast”, the toolbar lets you change the “Granularity” (D, W, M, Q, Y), the “Chart” (Line or Bar) and the “Model” that is drawn.",
                    "The “Model” chips are multi-select: the first one you pick drives the axes and the statistics, and the rest are overlaid as lines in another colour so you can compare.",
                    "The “Likely range” button turns the uncertainty cloud on and off; it is drawn as three nested rings: “Wide range”, “Likely range” and “Middle zone”.",
                    "The stats strip under the toolbar sums up average sales, variability, min, max, historical points, forecast steps, “Chosen model's WAPE” and “Best model”.",
                    "The “Metrics” tab opens the table of every model that ran on that SKU, ordered by “Cost”, best first, with the “BEST” badge on the winner.",
                    "At the foot of the page, the panel “What buying this way would have done” replays your purchasing policy over sales that already happened and compares it against ordering the same as last time.",
                ],
                "fields": [
                    ("Accuracy", "One minus the WAPE of the model chosen for that product, as a percentage. It is hidden when the best model's error is exactly zero, because that means there was no sale to get right."),
                    ("Chosen model's WAPE", "The WAPE of the model Faro actually used for that product — not the lowest WAPE in the table. WAPE is the sum of absolute errors divided by total real demand."),
                    ("Best model", "The SKU's winning model, baselines excluded. It is shown as “Model 1” … “Model 9”: the algorithm's name changes no purchasing decision, and the numbering is the same everywhere in the app."),
                    ("Cost", "What getting it wrong would cost with that model, counting a shortfall three times as expensive as a surplus. This is the column the winning model is picked by."),
                    ("MAE", "Mean absolute error, in units of the product."),
                    ("RMSE", "Like MAE, but squaring the errors: it punishes isolated large misses more."),
                    ("Bias", "Mean signed error. Positive means the model forecasts too high; negative, too low."),
                    ("Folds", "How many walk-forward validation splits were used to measure that model."),
                    ("Type", "Where the row comes from: a trained model or a reference. The references — “Reference (last value)”, “Reference (season)” and “Reference (average)” — are the bar to clear, not candidates."),
                    ("Freq / View", "At the foot of the chart: the original frequency of your data and the level you are currently viewing the series at."),
                ],
                "tasks": [
                    (
                        "Compare two models on the same product",
                        " 1. Pick the product in the left-hand list. 2. Stay on the “Forecast” tab. 3. Under “Model”, click a second chip: its forecast is drawn on top in another colour. 4. Turn off “Likely range” if the cloud gets in the way; it is only drawn with a single model selected. 5. Open “Metrics” to see which of the two has the lower “Cost”.",
                    ),
                    (
                        "Take the forecast into Excel",
                        " 1. Select the product and the granularity you want. 2. Open the “Export” menu in the chart toolbar. 3. Choose “Excel (.xlsx)” for that SKU, or “CSV — chart data” if you only want the series. 4. For the whole catalog at once, use “Export All SKUs” at the top: it produces one file with a sheet per product.",
                    ),
                    (
                        "Check whether a retrain improved things",
                        " 1. Press “Compare” in the top bar. 2. Pick the other session in “Comparing with:”. 3. Pick the SKU in the selector that appears beside it. 4. The screen splits in two: session A on top, B below, with the same product in both.",
                    ),
                ],
                "gotchas": [
                    "The metrics table is ordered by “Cost”, not by WAPE, so the model carrying the “BEST” badge may not be the one with the lowest WAPE. That is deliberate: running out costs more than having spare, and that is the comparison you buy on.",
                    "References never win. They are in the table so you can see how far off forecasting “the same as last time” would be, but Faro does not buy from them and they never get the “BEST” badge.",
                    "Models are named “Model 1” through “Model 9”. Nine trained models compete, plus three references and the “Combined model”, which is the blend of the numbered ones.",
                    "“Likely range” can only be turned on with a single model selected, and some models stored no quantiles: in that case the button stays off and “(no range)” appears beside it.",
                    "Not every model runs on every product. Faro assigns each SKU the models that suit its series type, always within the ones you chose when training, so two products from the same session can show different model lists.",
                ],
            },
            {
                "name": "How it sells",
                "route": "/pronosticos",
                "image": "pattern",
                "purpose": (
                    "The “How it sells” tab looks only at your history: no model is involved and "
                    "nothing here is a prediction. It answers three business questions: whether the "
                    "product is really growing or just repeating its pattern, which days or months it "
                    "sells most on, and how uneven it is from one period to the next."
                ),
                "walkthrough": [
                    "You get here from the “How it sells” tab on the forecasts screen, with a product already selected.",
                    "The first block, “Is it really growing?”, draws two lines: the faint one is your sales as they happened and the thick one is the same sales with the usual ups and downs taken out.",
                    "If the thick line climbs, the growth is real; if it stays flat, what you were seeing was the pattern repeating.",
                    "Under the chart, one sentence says what percentage of the product's movement the pattern (weekly or yearly) explains and what percentage the trend explains; what is missing from a hundred is variation that does not repeat.",
                    "When the product has too little history to separate the two, that block draws nothing and instead says how many periods are needed and how many there are.",
                    "The second block averages your sales “By day” of the week or “By month”, depending on the chip you pick, and under each bar says how many periods it rests on.",
                    "The third block, “How much you sell per day / week / month”, splits your periods into ranges and counts how many times you landed in each, with a marker on the “Average”.",
                    "The tail on the right of that spread is the peak your stock has to absorb; that is the reading that justifies safety stock.",
                ],
                "fields": [
                    ("Actual sales", "The series exactly as it is in your history, untouched."),
                    ("Trend", "The same series with the repeating component taken out."),
                    ("Weekly / yearly pattern", "What percentage of the product's movement the repeating cycle explains."),
                    ("Trend (percentage)", "What percentage of the movement the underlying rise or fall explains. Whatever is left over up to a hundred is variation that does not repeat."),
                    ("Average by day of the week", "The average of what you sold each Monday, each Tuesday, and so on. It is an average, never a sum: a day with more records would look taller for a reason that has nothing to do with how much it sells."),
                    ("Average by month", "The same, by calendar month. Useful for anticipating your strong months."),
                    ("Average", "The vertical marker on the histogram in the last block, placed at the average value of a period."),
                ],
                "tasks": [
                    (
                        "Decide which day to take delivery on",
                        " 1. Open “How it sells” with the product selected. 2. In the second block pick the “By day” chip. 3. Read which days come out highest: those are the days the product has to be on the shelf. 4. Schedule the reception one or two days before that peak.",
                    ),
                    (
                        "Tell real growth from seasonality",
                        " 1. Open “How it sells”. 2. Look at whether the thick line in “Is it really growing?” climbs or stays flat. 3. Read the sentence below it: if the pattern explains much more than the trend, what went up was the season. 4. If the block says history is too short, there is no answer yet; do not infer one from the chart above.",
                    ),
                ],
                "gotchas": [
                    "Nothing on this tab is a forecast. These are averages of what you already sold, and the screen itself says so under each chart.",
                    "The average by day of the week needs at least 8 weeks of history and sales on at least 5 distinct days. If you loaded your sales by week or by month, they cannot be split by day and the screen says so.",
                    "The average by month needs at least 12 months of history; with less, comparing months compares incomplete seasons.",
                    "Columns resting on fewer than 3 records are hidden, and the screen says how many it hid. A bar held up by two data points is noise drawn as a pattern.",
                ],
            },
            {
                "name": "Scenario simulator",
                "route": "/escenarios",
                "image": "escenarios",
                "purpose": (
                    "The simulator answers “what if…?” without touching anything. You assemble a "
                    "list of rules — more demand, a promotion, a late supplier, a different service "
                    "level — and Faro re-runs exactly the same signal calculation with those "
                    "assumptions, then shows you the current plan and the scenario side by side."
                ),
                "walkthrough": [
                    "Top right you pick the “Forecast session” you want to simulate on; only finished sessions appear.",
                    "The left column is the builder: “Scenario rules”, which opens with one “Demand change” rule already in place.",
                    "Every rule starts with a type selector, and beside it one line explains what that type does.",
                    "There are four types: “Demand change”, “Promotion”, “Supplier delay” and “Safety stock”.",
                    "The fields change with the type: the first two ask for a multiplier and, optionally, a SKU or a category and a date range; the delay asks for days and an optional supplier; safety stock asks for a service level.",
                    "“Add rule” appends another line; the x on the right removes it.",
                    "“Simulate” runs the scenario and fills the right-hand column; from then on the button reads “Simulate again”.",
                    "Top right, “Current plan vs scenario” compares six figures — units to order, purchase value, SKUs to order, PEDIR_YA SKUs, PEDIR_PRONTO SKUs and overstocked SKUs — each with its difference.",
                    "Below it, “Most affected products” lists the SKUs whose signal or quantity changed, sorted by the size of the change, with the before and after of each.",
                    "If the scenario is worth keeping, give it a name under “Save scenario”: it lands in “Saved scenarios” and runs again from “Load”.",
                ],
                "fields": [
                    ("Multiplier", "How much forecast demand is multiplied by. 1.4 is selling 40 % more. Accepted between 0.01 and 10."),
                    ("SKU (optional) / Category (optional)", "Which products the rule applies to. You fill in one or the other, never both. Left empty, the rule applies to the whole portfolio."),
                    ("From / To", "The date range the rule acts in. On a “Promotion” they are required; on a “Demand change” they are optional."),
                    ("Extra days", "Days added to the lead time, between 0 and 365. They are added on top of the lead time the signal already plans that product on, not on top of some other value."),
                    ("Supplier (optional)", "Limits the delay to one supplier. Left empty, it delays them all."),
                    ("Service level", "How often you want demand covered during the lead time. The selector offers 90 %, 95 %, 97 % and 99 %."),
                    ("Current plan / Scenario / Difference", "The three columns of the comparison: your plan as it stands today, the same calculation with the rules applied, and the subtraction between them."),
                    ("{n} series adjusted", "How many forecast series the demand rules reached. A rule with no SKU and no category reaches all of them."),
                    ("Signal", "In “Most affected products”, the signal before and after the rules, with an arrow between the two."),
                ],
                "tasks": [
                    (
                        "Get ready for a high season",
                        " 1. Pick the forecast session. 2. Keep the “Demand change” rule and raise the multiplier to what you expect to sell on top. 3. Leave SKU and category empty to apply it to everything, or type the seasonal category. 4. Press “Simulate”. 5. Read the “PEDIR_YA SKUs” row of the comparison: that is how many products turn urgent at that demand.",
                    ),
                    (
                        "Measure the damage of a late supplier",
                        " 1. Change the rule type to “Supplier delay”. 2. Type the number of days late. 3. Type the supplier's name exactly as it appears in your data, or leave it empty for all of them. 4. Press “Simulate” and check “Most affected products”: the last column shows the lead time before and after.",
                    ),
                    (
                        "Save and reuse a scenario",
                        " 1. Build the rules and simulate until the result convinces you. 2. Type a name under “Save scenario” and press “Save”. 3. It appears under “Saved scenarios”. 4. To run it again, press “Load”: the rules return to the builder and the result is recomputed against today's data.",
                    ),
                ],
                "gotchas": [
                    "Simulating changes nothing: it does not move your stock, does not generate purchase orders, does not retrain and does not modify the stored forecast. The result is computed and displayed, not saved.",
                    "The scenario does not invent new maths. It uses the same signal calculation you see in Inventory, fed with the assumptions you typed; that is why the “Current plan” figures match that screen.",
                    "A scenario takes up to 50 rules and is tied to one session. If you change session in the selector, the comparison on screen is cleared: it would be an old result under a new title.",
                    "A rule targets a SKU or a category, never both. Typing into one of the fields clears the other on its own.",
                    "Saving and deleting scenarios requires the analyst or admin role. A read-only user can simulate and load scenarios, but does not see the save block.",
                ],
            },
            {
                "name": "Impact and ROI",
                "route": "/impacto",
                "image": "impacto",
                "purpose": (
                    "“Impact & ROI” is the record of what you did with Faro: how many orders you "
                    "generated, how many urgent lines you ordered, how much purchasing value you "
                    "handled and how closely you followed the recommendations. The screen is "
                    "deliberately conservative: it does not estimate savings and does not count "
                    "stockouts avoided, because neither can be measured from your data. Everything "
                    "you see is a sum of records the app already wrote."
                ),
                "walkthrough": [
                    "First comes “Recap for <month>”, the tally of the month that just closed: the same one that reaches you by email on the first day of each month.",
                    "If you generated no order that month, the block does not show zeros: it says there is not enough history yet and names the month it is talking about.",
                    "Its tiles are purchase orders generated, adoption percentage, urgent lines you ordered, overstock reduction and managed purchases.",
                    "At the foot of the recap, “Where each number comes from” explains in one paragraph the origin of each figure and what it does not claim.",
                    "Below it, “Your history with Faro” accumulates across all time: orders generated, since which date, how many days active, urgent lines ordered and the value of the purchases managed.",
                    "When no product carries a unit cost, that last figure is replaced by the total units ordered and it suggests loading costs.",
                    "“Confidence in recommendations” shows your adoption rate with a bar: how many of the lines you decided on you ended up ordering.",
                    "“Monthly evolution” is the table of the last six months with orders, urgent lines ordered, value managed, adoption percentage and overstock reduction.",
                    "At the end, a link takes you to the orders screen to record receptions, and “Why this matters” closes by explaining that these are records of what you did, not a measurement of what you avoided.",
                ],
                "fields": [
                    ("purchase orders generated", "How many orders you generated from Faro. It is a count of records, not an estimate."),
                    ("days active", "Distinct calendar days on which you generated at least one order. It is not the time elapsed since the first one: someone who ordered twice a year apart has 2 days active, not 365."),
                    ("urgent lines you ordered", "Lines flagged PEDIR_YA that you actually ordered. It counts lines, not distinct products, and it includes orders that have not arrived yet."),
                    ("purchases managed with Faro", "Units ordered times their unit cost, taken from your own data. It only sums the lines that carry a recorded cost."),
                    ("adoption rate", "Lines you approved or adjusted, divided by the lines you actually decided on. Recommendations you let pass untouched are on neither side."),
                    ("Overstock reduction", "The difference between the value of your overstocked inventory at the start of the month and at the start of the next. It is two measurements subtracted, not a model."),
                    ("Not enough history yet", "What the overstock column says when one of the two monthly measurements is missing."),
                    ("Your overstock went up this month", "What that same column says when both measurements exist and overstock grew. That is a result, not missing data."),
                    ("≥ in front of an amount", "Only some of the ordered lines carried a unit cost, so the figure is a floor: the real value is higher."),
                ],
                "tasks": [
                    (
                        "See whether you are following the recommendations",
                        " 1. Open Impact. 2. Find “Confidence in recommendations”. 3. Read the percentage and the sentence beside it: it says how many lines you followed out of how many you decided on. 4. If the dismissed count is high, review the lead times and service levels of those products before blaming the forecast.",
                    ),
                    (
                        "Close the month with figures you can defend",
                        " 1. Open Impact on the first day of the month. 2. Read “Recap for <previous month>”. 3. Copy only what the tile claims; if a number says “Not available”, say exactly that instead of writing a zero. 4. If you need the full money figure, load the unit cost of the products that lack one and look again.",
                    ),
                ],
                "gotchas": [
                    "This screen never says “Faro saved you this much”. It does not estimate savings or count stockouts avoided, because that would require knowing what would have happened had you not bought, and that is not in your data.",
                    "The overstock reduction is not attributed to Faro. Overstock also falls when you sell, record shrinkage, delete products or retrain; the screen says what moved, not who moved it.",
                    "A number that cannot be computed appears as “Not available” with the reason, never as a zero. A zero would read as a result.",
                    "“Urgent lines you ordered” sums lines: the same 30 urgent products ordered every month for a year add up to 360. It also does not check that the goods arrived.",
                    "The adoption rate does not say what share of everything Faro suggested you followed. Recommendations you never touched are not recorded anywhere, so they are on neither side of the division.",
                ],
            },
            {
                "name": "AI Analyst",
                "route": "/asistente",
                "image": "asistente",
                "purpose": (
                    "The AI Analyst is a chat that answers about your own data: model accuracy, the "
                    "quality of your series, forecast trends and the state of your inventory. It "
                    "keeps conversations, you can star them and search them later, and each user sees "
                    "only their own. A conversation can hang off a forecast session, or stay on "
                    "“— General —” for conceptual questions."
                ),
                "walkthrough": [
                    "The left column lists your chats, with “New Chat” at the top and a search box below.",
                    "Chats are grouped into “Favorites” and “Recent”; the star on each row moves it between groups and the bin deletes it, asking for confirmation first.",
                    "The title of a new chat is written by the AI itself from your first question.",
                    "Above the thread, the “Session:” selector decides which forecast session the context comes from; “— General —” switches that context off and leaves only conceptual and platform questions.",
                    "In an empty thread, Faro offers “Suggested questions” so you can start with one click.",
                    "You type at the bottom and send with Enter; Shift+Enter inserts a line break.",
                    "Every answer carries a badge saying where it came from: “Your data”, “General answer”, “General”, “Off topic”, “No access” or “Error”.",
                    "The filter button beside the input opens “Data Sources” and lets you limit the context to data overview, model performance, per-product accuracy, inventory, data quality, model routing, configuration or forecast trends.",
                    "If the answer does not arrive in time or the service fails, your question stays in the thread with a message explaining what happened, so you can retry it without retyping.",
                ],
                "fields": [
                    ("Session:", "The forecast session the chat's context comes from. It changes which training run the accuracy and the trends are about."),
                    ("— General —", "A chat with no session: the AI answers about forecasting concepts and about how the platform works, without looking at your data."),
                    ("Data Sources", "The filter that limits which parts of the context the answer may look at. With no filter, the button reads “All sources”."),
                    ("Your data", "The badge on an answer built from your session and inventory context."),
                    ("General answer", "The badge on an answer assembled from the session context, but without a prior semantic search."),
                    ("Off topic", "The question does not resemble anything in your data, and the AI says so instead of inventing an answer."),
                    ("Suggested questions", "The shortcuts that appear in an empty chat; clicking one sends it as a question."),
                ],
                "tasks": [
                    (
                        "Ask about today's inventory",
                        " 1. Open a new chat. 2. Type the question directly, for example which products are at risk. 3. Read the answer and check it against the Inventory screen: the assistant reads that same signal.",
                    ),
                    (
                        "Understand why a product has low accuracy",
                        " 1. Under “Session:” pick the session where that product was trained. 2. Ask about the SKU exactly as it appears in your data. 3. To narrow the answer, open the “Data Sources” filter and leave “Per-product accuracy” and “Data quality” ticked.",
                    ),
                    (
                        "Find an old conversation",
                        " 1. Type in the search box of the left column. 2. Or star the chats you will come back to: they stay at the top, under “Favorites”.",
                    ),
                ],
                "gotchas": [
                    "Inventory answers always come from the signal of the active planning session, not from the session you picked in the chat. That is deliberate: inventory has exactly one answer in this product, and it is the one on the Inventory screen.",
                    "The inventory context the AI receives is limited to the first 40 products. On a large catalog, the answer describes that slice and not the whole catalog.",
                    "The AI only reads what the session and the inventory already computed. It does not run models, does not browse the internet and does not see your original files.",
                    "Every answer is built from the last 6 messages of the thread. What you said much further up in a long conversation is no longer in the context.",
                    "There is a limit of 20 messages per minute for the whole organization, and a question cannot exceed 4000 characters. The answer also has a time budget: if it runs out, the screen says so and keeps your question.",
                ],
            },
            {
                "name": "Session history",
                "route": "/historial",
                "image": "historial",
                "purpose": (
                    "“Update history” is the list of every time you uploaded your sales and Faro "
                    "processed them. From here you open the results of any finished update, rename it "
                    "so you recognise it later, or delete it. It is also where you see why one that "
                    "never finished failed."
                ),
                "walkthrough": [
                    "The screen opens with the full table, newest update first, up to 200 rows.",
                    "Each row carries name, status, source file, creation date, horizon, granularity and SKU count.",
                    "Status runs from “Draft” to “Completed”, through “Queued”, “Calculating”, “Failed” and “Cancelled”.",
                    "Clicking a “Completed” row opens its results on the forecasts screen, with that session already selected.",
                    "Rows that are not completed are not clickable: there are no results to open yet.",
                    "When an update shows as “Failed”, the “Technical detail:” reported by the forecasting engine is shown under the status.",
                    "The pencil on the right renames the row without leaving the table: Enter saves and Escape cancels.",
                    "The bin deletes the update and the projections that came out of it, with a confirmation first; it stays disabled while the update is “Calculating”.",
                ],
                "fields": [
                    ("Name", "The name of the update. You can change it whenever you like; it exists only so you recognise it."),
                    ("Status", "Where it ended up: “Completed” has results, “Calculating” is in progress, “Failed” stopped."),
                    ("File", "The sales file that update came from."),
                    ("Created", "The date it was created."),
                    ("Horizon", "How many periods ahead that run forecast."),
                    ("Granularity", "Whether the series was worked “Daily”, “Weekly” or “Monthly”."),
                    ("SKUs", "How many products went into that update."),
                    ("Technical detail:", "The exact reason for the failure, as the forecasting engine reported it."),
                ],
                "tasks": [
                    (
                        "Go back to an earlier result",
                        " 1. Open History. 2. Find the “Completed” row you want, by its name or its date. 3. Click anywhere on the row: the forecasts screen opens with that update selected.",
                    ),
                    (
                        "Give them names you will recognise later",
                        " 1. Press the pencil on the row. 2. Type a name that says what is inside, for example the month or the warehouse. 3. Press Enter, or the green check. 4. Escape cancels without saving.",
                    ),
                    (
                        "Find out why an update failed",
                        " 1. Look for the row with status “Failed”. 2. Read the text following “Technical detail:”. 3. If the message is about columns or dates, fix the file and upload it again. 4. If you do not understand it, pass it on verbatim to whoever supports you: that is what it is written for.",
                    ),
                ],
                "gotchas": [
                    "Deleting an update also deletes the demand projections that came out of it, and it cannot be undone.",
                    "An update cannot be deleted while it is “Calculating”; the button stays off until it finishes.",
                    "Renaming and deleting require the analyst or admin role. A read-only user sees the table and opens results, but does not see those buttons.",
                    "The detail of a failure comes in English from the forecasting engine. That is not a missing translation: it is the exact technical message, and rewriting it would make diagnosis harder.",
                ],
            },
        ],
    },
}

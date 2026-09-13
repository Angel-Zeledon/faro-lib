SECTION = {
    "id": "sistema",
    "es": {
        "title": "Primeros pasos y tu cuenta",
        "intro": (
            "Este capítulo cubre dos cosas. La primera es el camino completo de "
            "un usuario nuevo: crear la cuenta, subir el primer archivo de "
            "ventas, completar el inventario y leer el primer semáforo. La "
            "segunda son las cuatro pantallas donde administras la cuenta "
            "misma: Usuarios, Mi cuenta, Automatización y API. Ninguna de esas "
            "cuatro hace falta el primer día, pero son las que decides una vez "
            "y casi no vuelves a tocar."
        ),
        "screens": [
            {
                "name": "Primeros pasos",
                "route": "",
                "image": "",
                "purpose": (
                    "Faro no adivina nada de tu negocio: aprende de tu historial de ventas "
                    "y compara ese pronóstico contra lo que tienes en bodega. Por eso el "
                    "arranque tiene un orden fijo — primero tus ventas, después tu "
                    "inventario, y solo entonces el semáforo. Este recorrido es el que "
                    "decide si el producto te sirve, y se hace una sola vez."
                ),
                "walkthrough": [
                    "Entra a la página de inicio de Faro y crea tu cuenta: el formulario «Crea tu espacio de trabajo» te pide nombre completo, nombre de la empresa, correo electrónico, WhatsApp y contraseña, y la contraseña tiene que cumplir las cuatro condiciones que ves marcarse en vivo — 8 caracteres, una mayúscula, un número y un símbolo.",
                    "El WhatsApp no es un dato de relleno: es a donde te llegan tus órdenes de compra para reenviarlas a tus proveedores, así que escríbelo con el código de país, por ejemplo +50688887777.",
                    "Al enviar el formulario verás «Revisa tu correo»: te mandamos un enlace de verificación, y con abrirlo tu cuenta queda activa para iniciar sesión.",
                    "Puedes entrar y trabajar antes de verificar — subir el archivo, entrenar y ver el semáforo funcionan igual; lo que queda bloqueado hasta verificar son las acciones que salen de tu empresa: invitar personas, conectar integraciones y enviar notificaciones.",
                    "Al iniciar sesión caes en el Panel de compras, que con la cuenta vacía dice «Aún no tienes datos» y te ofrece dos caminos: «Subir mi historial de ventas» y «Probar con datos demo», que carga ventas de ejemplo de 5 productos y no toca tus datos.",
                    "El primer botón te lleva a Mis ventas, que trabaja en tres pasos numerados: «Sube tus ventas», «Confirma columnas» y «El sistema aprende».",
                    "Antes de soltar el archivo llena los cuatro campos de arriba: un nombre para la carga (opcional), hasta cuándo quieres planificar, el nivel de detalle del plan y en qué país vendes — este último lo usamos para los días feriados, que son de los días que más mueven las ventas y son distintos en cada país.",
                    "Arrastra tu archivo o haz clic para elegirlo: aceptamos CSV y Excel (.xlsx, .xls), y como mínimo tiene que traer tres columnas — fecha, producto y cantidad vendida.",
                    "En «Confirma tus columnas» nos dices cuál columna es la fecha, cuál la cantidad vendida y cuál el producto; elige la columna de producto aunque manejes uno solo, porque si la dejas en «un solo producto» todo se mezcla en una sola serie y pierdes el detalle por producto.",
                    "Si el archivo trae problemas que producirían un pronóstico equivocado, la pantalla te obliga a resolverlos antes de continuar y cada opción te dice qué hace y qué cuesta; los avisos que no bloquean solo se reportan.",
                    "El entrenamiento corre solo y puede tardar varios minutos según el tamaño del archivo: no cierres la pestaña, y al terminar te llevamos al semáforo con la vista diaria lista mientras las demás se siguen calculando en segundo plano.",
                    "Ahora abre «Configurar inventario» y completa los tres datos que Faro todavía no tiene de cada producto: cuántas unidades tienes hoy en bodega, cuánto te cuesta a ti la unidad y cuántos días tarda tu proveedor en entregarte.",
                    "Esa lista no viene alfabética sino ordenada por plata, y te dice arriba cuántos productos necesitas completar para cubrir el grueso de tu compra del mes — no hace falta configurarlos todos; si prefieres, sube el archivo de stock tal como te lo exporta tu sistema.",
                    "Vuelve al Panel de compras y lee el semáforo — Pedir YA, Pedir pronto, OK, Sobrestock y Sin datos — con la cantidad sugerida por producto, y cuando confirmes una compra regístrala en Pedidos: sin ese registro la orden no existe para Faro y nunca se aprende el plazo real de tus proveedores.",
                ],
                "fields": [
                    ("Fecha", "La columna de tu archivo de ventas con el día de la venta. Es obligatoria."),
                    ("Producto / SKU", "La columna que identifica cada producto: un código, una referencia o un nombre. Elígela aunque tengas un solo producto."),
                    ("Cantidad vendida", "Las unidades vendidas en esa fecha. Tiene que ser una columna numérica."),
                    ("Stock", "Cuántas unidades tienes hoy físicamente en bodega, sin contar lo que está por llegar. Se llena en Configurar inventario."),
                    ("Costo", "Lo que TÚ pagas por una unidad a tu proveedor, no el precio al que la vendes."),
                    ("Días de entrega", "Cuántos días pasan desde que le haces el pedido a tu proveedor hasta que la mercadería está en tu bodega."),
                ],
                "tasks": [
                    (
                        "Llegar de cero a tu primer semáforo con tu propio archivo",
                        "1. Crea la cuenta y abre el enlace de verificación del correo. "
                        "2. Inicia sesión y en el Panel de compras haz clic en «Subir mi historial de ventas». "
                        "3. Elige horizonte, nivel de detalle y país, y sube tu CSV o Excel de ventas. "
                        "4. Confirma las columnas de fecha, producto y cantidad vendida. "
                        "5. Espera a que termine el entrenamiento sin cerrar la pestaña. "
                        "6. Abre «Configurar inventario» y completa stock, costo y días de entrega de los productos que la lista pone arriba. "
                        "7. Vuelve al Panel de compras: ahí está tu semáforo con la cantidad sugerida por producto."
                    ),
                    (
                        "Ver cómo funciona antes de tener tu archivo listo",
                        "1. Inicia sesión. "
                        "2. En el Panel de compras haz clic en «Probar con datos demo». "
                        "3. Espera a que termine la carga: te deja directo en el semáforo con 5 productos de ejemplo. "
                        "4. Recorre el Panel de compras, Inventario y Pronósticos con esos datos. "
                        "5. Cuando tengas tu archivo real, vuelve a Mis ventas y súbelo: la demo no toca tus datos."
                    ),
                ],
                "gotchas": [
                    "Un producto necesita al menos 20 periodos de historia para entrar al pronóstico. Los que no llegan quedan fuera del cálculo: no aparecen con un pronóstico malo, simplemente no aparecen.",
                    "Sin stock o sin costo, un producto no recibe señal y sale como «Sin datos». No está roto: está sin configurar.",
                    "Sin días de entrega el producto sí aparece, pero calculado sobre 15 días que estamos suponiendo nosotros. Si tu proveedor tarda 45, el aviso te va a llegar tarde.",
                    "En el plan gratis cada archivo puede pesar hasta 25 MB y la cuenta admite hasta 100 productos. Cuando te quedes corto, la pantalla te lo dice y te abre el cuadro para escribirnos.",
                    "No cierres la pestaña mientras el sistema está aprendiendo. El proceso puede tomar varios minutos según el tamaño de tu archivo.",
                ],
            },
            {
                "name": "Equipo y permisos",
                "route": "/usuarios",
                "image": "usuarios",
                "purpose": (
                    "Aquí un administrador da de alta a su equipo y decide qué puede hacer "
                    "cada quien. Es la única pantalla del producto que un no administrador "
                    "no puede abrir siquiera: quien entra sin el rol ve «No tienes permiso "
                    "para ver esta página»."
                ),
                "walkthrough": [
                    "El encabezado dice cuántos usuarios hay en este espacio de trabajo y trae dos botones: «Actualizar», que recarga la lista, y «Crear usuario».",
                    "Al crear un usuario llenas nombre completo (opcional), correo electrónico (obligatorio) y rol; la pantalla te avisa que se le enviará un correo de configuración de cuenta con un enlace de verificación.",
                    "La tabla tiene seis columnas: Nombre / Correo, Estado, Rol, Creado, Último acceso y Acciones, y tu propia fila viene marcada con «(tú)».",
                    "El Estado se muestra como insignia de color — Activo, Pendiente, Inactivo o Suspendido — y el Rol como Administrador, Analista o Solo lectura.",
                    "El botón de flecha al final de cada fila cambia el estado entre Activo, Inactivo y Suspendido; no aparece en tu propia fila, porque nadie puede suspenderse a sí mismo.",
                    "Cuando alguien está en «Pendiente» aparece además un icono de sobre: es reenviar el correo de verificación, y te confirma en el mismo botón si se envió o si falló.",
                    "El icono de lápiz abre «Editar usuario» para corregir el nombre, el correo o el rol; cambiar el correo obliga a volver a verificarlo.",
                    "El icono de papelera abre «¿Eliminar usuario?», que te recuerda que se borran todas sus sesiones, tokens y permisos y que la acción no se puede deshacer; ese icono tampoco sale en tu propia fila.",
                    "Arriba de la tabla hay un buscador por nombre o correo y dos filtros, «Todos los estados» y «Todos los roles», y la lista se pagina de 20 en 20 con el conteo «Mostrando X–Y de Z».",
                ],
                "fields": [
                    ("Administrador", "Puede todo lo del analista y además es el único que administra el equipo desde esta pantalla, cambia la moneda, la zona horaria y cada cuánto se calculan las compras."),
                    ("Analista", "Trabaja con normalidad: sube archivos, entrena, edita inventario, registra órdenes de compra, crea llaves de API y programa recálculos. Lo único que no puede es administrar usuarios."),
                    ("Solo lectura", "Ve todo el producto y no cambia nada. Cualquier acción que modifique datos le responde con un error de permisos y el estado queda igual: no puede subir archivos, ni entrenar, ni editar stock, ni registrar pedidos, ni crear llaves, ni programar recálculos."),
                    ("Estado de la cuenta", "Activo trabaja normal; Pendiente todavía no abrió el enlace de verificación; Inactivo y Suspendido no pueden entrar."),
                ],
                "tasks": [
                    (
                        "Sumar a alguien de tu equipo",
                        "1. Abre Usuarios y haz clic en «Crear usuario». "
                        "2. Escribe su correo y, si quieres, su nombre completo. "
                        "3. Elige el rol: Analista si va a comprar o cargar datos, Solo lectura si solo va a mirar. "
                        "4. Guarda: le llega un correo de configuración con el enlace de verificación. "
                        "5. Mientras no lo abra aparecerá como «Pendiente»; si no le llegó, usa el icono de sobre para reenviarlo."
                    ),
                    (
                        "Quitarle el acceso a alguien que se fue",
                        "1. Busca a la persona por nombre o correo. "
                        "2. Si es temporal, abre la flecha de estado y ponla en «Inactivo» o «Suspendido». "
                        "3. Si es definitivo, usa el icono de papelera y confirma en «¿Eliminar usuario?». "
                        "4. Ten claro que eliminar borra sus sesiones, tokens y permisos y no se puede deshacer. "
                        "5. Si esa persona había creado llaves de API, revócalas aparte en Automatización: la llave sigue viva aunque el usuario ya no esté."
                    ),
                ],
                "gotchas": [
                    "En el plan gratis caben dos usuarios: tú y una persona más. Al intentar crear el tercero se abre «Llegaste al límite de tu plan gratis», que te explica que no hay nada bloqueado por función y te da tres formas de escribirnos.",
                    "No puedes cambiarte el estado ni eliminarte a ti mismo: esos dos controles no se dibujan en tu propia fila.",
                    "Lo que decide los permisos es el rol, no una lista de casillas por persona. No hay permisos individuales que configurar.",
                    "«Pendiente» no significa que algo falló: significa que esa persona todavía no abrió el correo de verificación. Reenvíalo con el icono de sobre antes de crearle una cuenta nueva.",
                    "Cambiarle el correo a alguien lo devuelve a verificar. Hasta que abra el nuevo enlace no podrá invitar gente ni conectar integraciones.",
                ],
            },
            {
                "name": "Mi cuenta",
                "route": "/mi-cuenta",
                "image": "cuenta",
                "purpose": (
                    "Es la pantalla de configuración, y mezcla dos cosas que conviene no "
                    "confundir: lo tuyo — tu nombre, tu idioma, tu tema, tu contraseña, tu "
                    "WhatsApp — y lo de toda la empresa — moneda, zona horaria y cada "
                    "cuánto se calculan las compras, que solo un administrador cambia y le "
                    "aplican a todo el mundo. Aquí también está el panel de uso y límites."
                ),
                "walkthrough": [
                    "La columna izquierda es «Perfil de Usuario»: el nombre completo se edita con el icono de lápiz y se guarda ahí mismo; correo, rol y estado de cuenta se muestran pero no se editan.",
                    "La primera tarjeta de la columna derecha es «Uso y límites», con la insignia del plan y una barra por cada cosa que se puede llenar: Productos (SKUs), Usuarios, Bodegas, Pronósticos guardados y Llaves de API.",
                    "La barra cambia de color cuando te acercas — ámbar pasado el 80 %, rojo al llegar al tope — y el botón «Necesito más espacio» abre el cuadro para escribirnos: no hay nada que comprar en la pantalla.",
                    "«Moneda» decide en qué símbolo se muestran tus cifras — valor del inventario, costos por unidad y totales de las órdenes de compra — y te muestra un ejemplo con el formato aplicado.",
                    "«Zona horaria» es la que se usa para leer las frecuencias de Automatización: si programas «cada lunes a las 6am», corre a las 6am de esa zona, y la tarjeta te dice qué hora es allí en este momento.",
                    "«Configuración de App» tiene el idioma (Español / English) y el tema (Claro / Oscuro); son preferencias tuyas, se guardan en tu cuenta y no afectan a nadie más.",
                    "«Cada cuánto se calculan tus compras» solo aparece cuando tu historial permite más de un período, te explica por qué el sistema eligió el que está usando y te deja cambiarlo entre Día, Semana y Mes.",
                    "«Seguridad» cambia la contraseña en dos pasos: escribes la nueva (mínimo 8 caracteres), te enviamos un código de 6 dígitos a tu correo que vence en 10 minutos, y lo confirmas.",
                    "«WhatsApp» enlaza y verifica tu número con un código, y debajo el interruptor de mensajes directos por SMS queda bloqueado mientras no tengas un número verificado.",
                    "Más abajo, «Modelos Disponibles» lista los modelos de pronóstico de la plataforma con su categoría y si están disponibles o en beta.",
                    "Al final, «Registros de Actividad» muestra qué se hizo en la cuenta — acción, recurso, estado y fecha — con un filtro por tipo de acción y un botón «Cargar más» que dice cuántos registros quedan.",
                ],
                "fields": [
                    ("Nombre completo", "Lo único editable de tu perfil. Se guarda con el botón «Guardar Cambios» de la propia tarjeta."),
                    ("Correo electrónico", "Se muestra, no se edita aquí. Lo cambia un administrador desde Usuarios, y el cambio obliga a volver a verificarlo."),
                    ("Rol y Estado de cuenta", "Informativos. El rol lo asigna un administrador desde Usuarios."),
                    ("Idioma y Tema", "Tuyos. Se guardan en tu cuenta, así que te siguen a cualquier navegador donde inicies sesión."),
                    ("Moneda", "De toda la empresa, y solo un administrador la cambia. Cambia el símbolo, no convierte los montos que ya cargaste."),
                    ("Zona horaria", "De toda la empresa, y solo un administrador la cambia. Manda sobre la hora a la que corren los recálculos programados."),
                    ("Cada cuánto se calculan tus compras", "Día, Semana o Mes, para toda la cuenta y solo por un administrador. Solo se ofrecen los períodos que tu historial permite."),
                    ("Uso y límites", "Cinco barras: Productos (SKUs), Usuarios, Bodegas, Pronósticos guardados y Llaves de API. En el plan gratis son 100, 2, 1, 3 y 1; en el plan completo dicen «Sin límite»."),
                    ("Seguridad", "Cambio de contraseña con un código de 6 dígitos al correo, válido por 10 minutos."),
                    ("WhatsApp", "Tu número verificado para recibir mensajes de Faro. Sin él, el interruptor de SMS no se puede activar."),
                ],
                "tasks": [
                    (
                        "Cambiar tu contraseña",
                        "1. Abre Mi cuenta y baja hasta «Seguridad». "
                        "2. Haz clic en «Cambiar contraseña». "
                        "3. Escribe la nueva contraseña, de al menos 8 caracteres. "
                        "4. Pulsa «Enviar código al correo» y revisa tu bandeja. "
                        "5. Escribe el código de 6 dígitos antes de que venzan los 10 minutos y confirma el cambio."
                    ),
                    (
                        "Saber cuánto espacio te queda y pedir más",
                        "1. Abre Mi cuenta y mira la tarjeta «Uso y límites», arriba a la derecha. "
                        "2. Lee cada barra: te dice cuánto llevas usado de cuánto. "
                        "3. Si alguna está en ámbar o en rojo, haz clic en «Necesito más espacio». "
                        "4. Elige cómo prefieres hablarnos: WhatsApp, correo, o el formulario de la misma ventana. "
                        "5. Cuéntanos cuántos productos, bodegas y personas manejas: eso es lo que necesitamos para ampliarte los límites."
                    ),
                ],
                "gotchas": [
                    "Cambiar la moneda solo cambia el símbolo: no convierte nada. Si tus costos están en colones y eliges dólares, verás colones con signo de dólar.",
                    "La zona horaria no cambia cómo se muestran tus ventas ni tus pronósticos. Lo único que decide es a qué hora corren los recálculos programados.",
                    "Cambiar cada cuánto se calculan tus compras no es un cambio de vista: cambia las cantidades que te sugerimos comprar y lo que te llega por correo y WhatsApp cada mañana, y un producto con poca historia puede desaparecer del cálculo diario y sí aparecer en el semanal.",
                    "El panel de uso muestra cinco límites, pero no todos los que existen. El tamaño máximo de archivo (25 MB en el plan gratis) y el tope de llamadas de API por día se cuentan aparte.",
                    "No hay pantalla de compra ni checkout. Pasar al plan completo es una conversación con nosotros, y el botón «Necesito más espacio» es toda la superficie comercial que tiene el producto.",
                ],
            },
            {
                "name": "Automatización",
                "route": "/automatizacion",
                "image": "automatizacion",
                "purpose": (
                    "Dos cosas que hacen que Faro trabaje sin que nadie lo abra: las llaves "
                    "con las que otro sistema entra a tus datos, y el horario en el que tus "
                    "pronósticos se vuelven a calcular solos. Solo un administrador ve esta "
                    "entrada en el menú."
                ),
                "walkthrough": [
                    "La pantalla tiene dos pestañas: «API Keys» y «Tareas programadas».",
                    "En API Keys, lo primero que lees es el aviso que importa: la clave se muestra una sola vez, al crearla, no la guardamos en ningún lado, y si se pierde se crea otra y se revoca la anterior.",
                    "«Generar key» abre dos campos: un nombre para reconocerla después — por ejemplo «Integración ERP» — y «Qué puede hacer la clave», con dos opciones, «Solo leer» y «Leer y escribir».",
                    "Al crearla aparece un recuadro con la clave completa, que empieza por sk_live_, y un botón «Copiar»; esa es tu única oportunidad de guardarla.",
                    "La tabla de abajo lista tus llaves con Nombre, Creada y Último uso — que dice «Nunca» si nadie la ha usado — y un botón «Revocar» por fila que te pide confirmación, porque las apps que la usen dejarán de funcionar.",
                    "En «Tareas programadas» eliges primero una sesión en el desplegable «Sesión:»; solo aparecen las sesiones que terminaron de entrenar.",
                    "«Frecuencia» ofrece seis horarios listos y debajo te muestra en letra monoespaciada la expresión cron que corresponde, para que no haya duda de qué se guardó.",
                    "La casilla «Programación activada» deja la programación guardada pero apagada cuando la desmarcas, y el botón guarda o actualiza según si esa sesión ya tenía una.",
                    "Una vez guardada, la pantalla te dice «Próxima ejecución» con fecha y hora y aclara de qué zona horaria son esas horas: la que tenga configurada tu empresa en Mi cuenta.",
                    "Arriba aparece «Ya programado», la lista de todo lo que está armado en cualquier sesión — con su frecuencia, si está en pausa, su próxima corrida y, si la última falló, el error — y cada línea es un atajo para editar esa programación.",
                    "Debajo, «Últimas corridas programadas» muestra el histórico real: fecha, sesión y si terminó bien, falló, está corriendo, está en cola o se canceló.",
                ],
                "fields": [
                    ("Nombre de la key", "Solo para que la reconozcas en la lista. Ponle el nombre del sistema que la va a usar."),
                    ("Qué puede hacer la clave", "«Solo leer» consulta el semáforo y los pronósticos. «Leer y escribir» además sube archivos, encola entrenamientos y registra órdenes de compra."),
                    ("Cada lunes a las 6am", "Cron 0 6 * * 1."),
                    ("Todos los días a medianoche", "Cron 0 0 * * *."),
                    ("Días laborables a las 6am", "Cron 0 6 * * 1-5."),
                    ("Cada domingo a las 8am", "Cron 0 8 * * 0."),
                    ("Cada hora", "Cron 0 * * * *."),
                    ("Primer día del mes", "Cron 0 0 1 * *."),
                    ("Programación activada", "Desmarcarla conserva el horario guardado pero no dispara nada hasta que la vuelvas a marcar."),
                ],
                "tasks": [
                    (
                        "Crear una llave para que tu ERP lea el semáforo",
                        "1. Abre Automatización y quédate en la pestaña «API Keys». "
                        "2. Haz clic en «Generar key». "
                        "3. Ponle un nombre que diga qué sistema la usa, por ejemplo «Integración ERP». "
                        "4. En «Qué puede hacer la clave» deja «Solo leer» si el ERP únicamente va a consultar. "
                        "5. Pulsa «Crear» y copia la clave en ese momento con el botón «Copiar»: no se vuelve a mostrar. "
                        "6. Pégala en la configuración de tu sistema y comprueba en la tabla que la columna «Último uso» deja de decir «Nunca»."
                    ),
                    (
                        "Dejar que tus pronósticos se recalculen solos cada semana",
                        "1. Confirma primero en Mi cuenta que la zona horaria de la empresa es la tuya. "
                        "2. Abre Automatización y ve a «Tareas programadas». "
                        "3. Elige en «Sesión:» la sesión que quieres reentrenar. "
                        "4. En «Frecuencia» elige «Cada lunes a las 6am». "
                        "5. Deja marcada «Programación activada» y pulsa «Guardar programación». "
                        "6. Verifica que aparezca en «Ya programado» con la próxima ejecución a la hora que esperabas."
                    ),
                ],
                "gotchas": [
                    "La clave se ve una sola vez. Si cierras el recuadro sin copiarla no hay forma de recuperarla: hay que crear otra y revocar la vieja.",
                    "Revocar es inmediato y no avisa a nadie. Todo lo que estuviera usando esa clave deja de funcionar en la siguiente llamada.",
                    "En el plan gratis cabe una sola llave de API, y cada llave admite 120 llamadas por minuto y 500 por día; en el plan completo el tope diario desaparece.",
                    "Las horas de la programación se leen en la zona horaria de tu empresa, no en la del servidor ni en la de tu navegador. Si esa zona está mal en Mi cuenta, todo lo programado corre a la hora equivocada.",
                    "Solo se pueden programar sesiones que ya terminaron de entrenar. Si el desplegable dice «Sin sesiones completadas», primero entrena una en Mis ventas.",
                    "Revisa de vez en cuando «Ya programado» y «Últimas corridas programadas»: una programación que lleva semanas fallando se ve igual de tranquila en el menú, y ahí es donde aparece el error.",
                ],
            },
            {
                "name": "API pública",
                "route": "/api",
                "image": "api",
                "purpose": (
                    "La referencia de la API, y además una consola: cada endpoint se puede "
                    "ejecutar desde la misma página con tu propia clave. Sirve para que tu "
                    "sistema empuje los datos y se lleve la decisión sin que nadie abra "
                    "Faro. No es solo para administradores: un analista es quien "
                    "normalmente conecta una integración."
                ),
                "walkthrough": [
                    "El encabezado muestra «La URL base» con la dirección exacta a la que van tus llamadas, incluido el /v1; las rutas de la página se escriben sin ese prefijo, y esa base es el valor de $FARO en los ejemplos.",
                    "Debajo hay un campo «Tu API key» donde pegas una clave sk_live_: vive solo en esa pestaña mientras la usas y no se guarda ni se manda a ningún otro lado.",
                    "Si todavía no tienes una, el botón «Generar una clave» te lleva directo a Automatización.",
                    "A la izquierda queda fija la lista de endpoints; a la derecha, cada uno con su método, su ruta y una explicación de para qué sirve en el trabajo real.",
                    "Los que modifican datos llevan la insignia «Escribe», y sus campos de prueba vienen con un aviso ámbar del efecto que van a tener.",
                    "En cada endpoint, «Probar» ejecuta la llamada de verdad contra tu cuenta y te devuelve el estado HTTP, la duración en milisegundos y la respuesta; «Ejemplo» te da el mismo llamado escrito como comando curl para copiarlo.",
                    "Antes de ejecutar una escritura sale una confirmación que nombra la consecuencia concreta — reemplazar tu archivo, encolar un entrenamiento real o registrar una orden de compra que después se controla en recepción.",
                    "Las secciones de referencia al final explican tres cosas: la autenticación, que va en la cabecera Authorization; los límites, 120 llamadas por minuto y 500 por día en el plan gratis, con 429 y Retry-After al pasarte; y el envoltorio, donde tu contenido siempre viene en data y los errores traen error_code.",
                    "El cierre de la página es una promesa explícita: estos endpoints no cambian de ruta ni de forma sin aviso, y cualquier otro endpoint del servicio es interno — alcanzable con tu clave, pero sin compromiso de mantenerlo.",
                ],
                "fields": [
                    ("GET /planning", "Devuelve el active_session_id que usan las demás llamadas. Pídelo cada vez, porque cambia cuando se entrena una corrida nueva."),
                    ("GET /data-sources", "Lista tus fuentes para saber qué source_id reemplazar."),
                    ("POST /data-sources/{source_id}/file", "Escribe. Sube el export encima de la fuente: conserva su id y su mapeo de columnas. Acepta .csv, .xlsx, .xls, .parquet y .json."),
                    ("POST /sessions/{session_id}/train", "Escribe. Encola un reentrenamiento. Si la sesión ya tiene programación, no necesitas esta llamada."),
                    ("GET /sessions/{session_id}/train/status", "El estado del último entrenamiento: QUEUED, RUNNING, COMPLETED o FAILED."),
                    ("GET /inventory/status", "Todos los productos con su señal y su recommended_qty. No hay paginación: filtra con signal o supplier."),
                    ("GET /inventory/morning-briefing", "Lo mismo ordenado como el trabajo del día, con el motivo, el proveedor sugerido y el valor estimado."),
                    ("POST /inventory/log-po", "Escribe. Registra la orden de compra. Sin esta llamada la orden no existe para Faro y el plazo real de tus proveedores nunca se aprende."),
                ],
                "tasks": [
                    (
                        "Probar la API sin escribir una línea de código",
                        "1. Crea una clave «Solo leer» en Automatización y cópiala. "
                        "2. Abre la pantalla API y pégala en «Tu API key». "
                        "3. Elige GET /planning en la lista de la izquierda y pulsa «Ejecutar». "
                        "4. Copia el active_session_id de la respuesta. "
                        "5. Abre GET /inventory/morning-briefing y ejecútalo: es el mismo trabajo del día que ves en el Panel de compras, en JSON."
                    ),
                    (
                        "Automatizar el ciclo completo desde tu sistema",
                        "1. Crea una clave «Leer y escribir». "
                        "2. Cada corrida, llama primero a GET /planning para obtener el active_session_id. "
                        "3. Sube el export de ventas más reciente con POST /data-sources/{source_id}/file sobre la fuente que ya usas. "
                        "4. Encola el recálculo con POST /sessions/{session_id}/train, o salta este paso si esa sesión ya tiene una programación armada. "
                        "5. Consulta GET /sessions/{session_id}/train/status hasta que diga COMPLETED. "
                        "6. Lee GET /inventory/status y arma tu orden con la señal y la recommended_qty. "
                        "7. Cuando la orden salga de verdad, regístrala con POST /inventory/log-po, incluyendo también las líneas que rechazaste."
                    ),
                ],
                "gotchas": [
                    "No hay ambiente de pruebas. El botón «Ejecutar» de esta página actúa sobre tu cuenta real: subir un archivo reemplaza el de esa fuente y registrar una orden la hace aparecer en Pedidos.",
                    "Ojo con log-po: si mandas el cuerpo vacío o sin la lista items, no se registra nada vacío — se registra una orden con TODOS los productos en PEDIR_YA y PEDIR_PRONTO de esa sesión.",
                    "Una clave «Solo leer» no puede escribir. Si tu integración sube archivos, entrena o registra órdenes, necesita una clave «Leer y escribir».",
                    "Ramifica por error_code, nunca por el texto del error: el texto puede cambiar de idioma o de redacción, el código no.",
                    "El active_session_id cambia cada vez que se entrena una corrida nueva. Guardarlo fijo en tu integración es la forma más fácil de terminar leyendo un pronóstico viejo.",
                    "Solo estos endpoints tienen compromiso de estabilidad. Tu clave alcanza otros, pero nadie prometió mantenerlos como están.",
                ],
            },
        ],
    },
    "en": {
        "title": "Getting started and your account",
        "intro": (
            "This chapter covers two things. The first is the whole path a new "
            "user walks: creating the account, uploading the first sales file, "
            "filling in the inventory and reading the first traffic light. The "
            "second is the four screens where you administer the account "
            "itself: Users, My account, Automation and API. None of those four "
            "is needed on day one, but they are the ones you decide once and "
            "then barely touch again."
        ),
        "screens": [
            {
                "name": "Getting started",
                "route": "",
                "image": "",
                "purpose": (
                    "Faro guesses nothing about your business: it learns from your sales "
                    "history and compares that forecast against what you hold in the "
                    "warehouse. That is why the start has a fixed order — first your sales, "
                    "then your inventory, and only then the traffic light. This walk is what "
                    "decides whether the product works for you, and you do it once."
                ),
                "walkthrough": [
                    "Go to the Faro landing page and create your account: the “Create your workspace” form asks for your full name, company name, email address, WhatsApp and a password, and the password has to meet the four conditions you see tick off live — 8 characters, one uppercase letter, one number and one symbol.",
                    "The WhatsApp number is not filler: it is where your purchase orders arrive so you can forward them to your suppliers, so write it with the country code, for example +50688887777.",
                    "When you submit the form you will see “Check your email”: we send a verification link, and opening it activates your account so you can sign in.",
                    "You can sign in and work before verifying — uploading the file, training and seeing the traffic light all work; what stays blocked until you verify are the actions that leave your company: inviting people, wiring integrations and sending notifications.",
                    "Signing in lands you on the Purchasing Panel, which on an empty account says “You don’t have data yet” and offers two ways forward: “Upload my sales history” and “Try it with demo data”, which loads sample sales for 5 products and never touches your data.",
                    "The first button takes you to My sales, which works in three numbered steps: “Upload your sales”, “Confirm columns” and “The system learns”.",
                    "Before dropping the file, fill in the four fields at the top: a name for the run (optional), how far ahead you want to plan, the plan detail level, and which country you sell in — we use that last one for public holidays, which are among the days that move sales the most and differ in every country.",
                    "Drag your file in or click to pick it: we accept CSV and Excel (.xlsx, .xls), and it must carry at least three columns — date, product and quantity sold.",
                    "In “Confirm your columns” you tell us which column is the date, which is the quantity sold and which is the product; pick the product column even if you carry a single product, because leaving it as “a single product” merges everything into one series and you lose the per-product detail.",
                    "If the file carries problems that would produce a wrong forecast, the screen makes you resolve them before continuing and each option states what it does and what it costs; the warnings that do not block are simply reported.",
                    "Training runs on its own and can take several minutes depending on the size of your file: do not close the tab, and when it finishes we take you to the traffic light with the daily view ready while the rest keeps computing in the background.",
                    "Now open “Set up inventory” and fill in the three things Faro still does not know about each product: how many units you hold today, what one unit costs you, and how many days your supplier takes to deliver.",
                    "That list is not alphabetical but ordered by money, and it tells you at the top how many products you need to complete to cover the bulk of this month’s purchase — you do not have to configure them all; if you prefer, upload the stock file exactly as your system exports it.",
                    "Go back to the Purchasing Panel and read the traffic light — Order NOW, Order soon, OK, Overstock and No data — with the suggested quantity per product, and once you confirm a purchase, record it in Orders: without that record the order does not exist for Faro and your suppliers’ real lead times are never learned.",
                ],
                "fields": [
                    ("Date", "The column in your sales file with the date of the sale. It is required."),
                    ("Product / SKU", "The column that identifies each product: a code, a reference or a name. Pick it even if you have a single product."),
                    ("Quantity sold", "The units sold on that date. It has to be a numeric column."),
                    ("Stock", "How many units you physically hold today, not counting anything in transit. Filled in on Set up inventory."),
                    ("Cost", "What YOU pay your supplier per unit, not the price you sell it at."),
                    ("Lead time in days", "How many days pass between placing the order with your supplier and the goods being in your warehouse."),
                ],
                "tasks": [
                    (
                        "Go from zero to your first traffic light with your own file",
                        "1. Create the account and open the verification link in your email. "
                        "2. Sign in and, on the Purchasing Panel, click “Upload my sales history”. "
                        "3. Choose the horizon, the detail level and your country, then upload your sales CSV or Excel. "
                        "4. Confirm the date, product and quantity-sold columns. "
                        "5. Wait for training to finish without closing the tab. "
                        "6. Open “Set up inventory” and fill in stock, cost and lead time for the products the list puts at the top. "
                        "7. Go back to the Purchasing Panel: there is your traffic light with the suggested quantity per product."
                    ),
                    (
                        "See how it works before your file is ready",
                        "1. Sign in. "
                        "2. On the Purchasing Panel click “Try it with demo data”. "
                        "3. Wait for the load to finish: it drops you straight into the traffic light with 5 sample products. "
                        "4. Walk the Purchasing Panel, Inventory and Forecasts with that data. "
                        "5. When you have your real file, go back to My sales and upload it: the demo never touches your data."
                    ),
                ],
                "gotchas": [
                    "A product needs at least 20 periods of history to enter the forecast. The ones that fall short are left out of the calculation: they do not show up with a bad forecast, they simply do not show up.",
                    "Without stock or without cost, a product gets no signal and comes out as “No data”. It is not broken: it is unconfigured.",
                    "Without a lead time the product does appear, but planned on an assumed 15 days. If your supplier takes 45, the warning will reach you late.",
                    "On the free plan each file can be up to 25 MB and the account holds up to 100 products. When you outgrow that, the screen says so and opens the dialog to write to us.",
                    "Do not close the tab while the system is learning. The process can take several minutes depending on the size of your file.",
                ],
            },
            {
                "name": "Users",
                "route": "/usuarios",
                "image": "usuarios",
                "purpose": (
                    "This is where an administrator adds their team and decides what each "
                    "person may do. It is the one screen in the product a non-administrator "
                    "cannot even open: anyone without the role sees “You don’t have "
                    "permission to view this page”."
                ),
                "walkthrough": [
                    "The header says how many users are in this workspace and carries two buttons: “Refresh”, which reloads the list, and “Create user”.",
                    "Creating a user asks for a full name (optional), an email address (required) and a role; the screen tells you that an account setup email with a verification link will be sent to them.",
                    "The table has six columns: Name / Email, Status, Role, Created, Last login and Actions, and your own row is marked “(you)”.",
                    "Status shows as a coloured badge — Active, Pending, Inactive or Suspended — and Role as Administrator, Analyst or Read only.",
                    "The chevron button at the end of each row switches the status between Active, Inactive and Suspended; it does not appear on your own row, because nobody can suspend themselves.",
                    "When somebody is “Pending” an envelope icon appears as well: it resends the verification email, and the button itself confirms whether it went out or failed.",
                    "The pencil icon opens “Edit user” to fix the name, the email or the role; changing the email forces re-verification.",
                    "The bin icon opens “Delete user?”, which reminds you that all their sessions, tokens and permissions will be removed and that this cannot be undone; that icon is also absent from your own row.",
                    "Above the table there is a search by name or email and two filters, “All statuses” and “All roles”, and the list pages 20 at a time with the “Showing X–Y of Z” count.",
                ],
                "fields": [
                    ("Administrator", "Can do everything an analyst can and is additionally the only role that manages the team from this screen, changes the currency, the time zone and how often purchases are computed."),
                    ("Analyst", "Works normally: uploads files, trains, edits inventory, records purchase orders, creates API keys and schedules recalculations. The one thing they cannot do is manage users."),
                    ("Read only", "Sees the whole product and changes nothing. Any action that modifies data answers with a permission error and the state stays as it was: they cannot upload files, train, edit stock, record orders, create keys or schedule recalculations."),
                    ("Account status", "Active works normally; Pending has not opened the verification link yet; Inactive and Suspended cannot sign in."),
                ],
                "tasks": [
                    (
                        "Add somebody from your team",
                        "1. Open Users and click “Create user”. "
                        "2. Type their email and, if you want, their full name. "
                        "3. Choose the role: Analyst if they will buy or load data, Read only if they will only look. "
                        "4. Save: they receive a setup email with the verification link. "
                        "5. Until they open it they will show as “Pending”; if it never arrived, use the envelope icon to resend it."
                    ),
                    (
                        "Cut off access for somebody who left",
                        "1. Find the person by name or email. "
                        "2. If it is temporary, open the status chevron and set them to “Inactive” or “Suspended”. "
                        "3. If it is final, use the bin icon and confirm in “Delete user?”. "
                        "4. Be clear that deleting removes their sessions, tokens and permissions and cannot be undone. "
                        "5. If that person had created API keys, revoke them separately in Automation: a key stays alive even after its creator is gone."
                    ),
                ],
                "gotchas": [
                    "The free plan holds two users: you and one more. Trying to create a third opens “You reached your free plan limit”, which explains that no feature is locked and gives you three ways to write to us.",
                    "You cannot change your own status or delete yourself: neither control is drawn on your own row.",
                    "What decides permissions is the role, not a list of per-person checkboxes. There are no individual permissions to configure.",
                    "“Pending” does not mean something failed: it means that person has not opened the verification email yet. Resend it with the envelope icon before creating them a second account.",
                    "Changing somebody’s email sends them back to verification. Until they open the new link they cannot invite people or wire integrations.",
                ],
            },
            {
                "name": "My account",
                "route": "/mi-cuenta",
                "image": "cuenta",
                "purpose": (
                    "This is the settings screen, and it mixes two things worth keeping "
                    "apart: what is yours — your name, your language, your theme, your "
                    "password, your WhatsApp — and what belongs to the whole company — "
                    "currency, time zone and how often purchases are computed, which only "
                    "an administrator changes and which apply to everybody. The usage and "
                    "limits panel lives here too."
                ),
                "walkthrough": [
                    "The left column is “User Profile”: the full name is edited with the pencil icon and saved right there; email, role and account status are shown but not editable.",
                    "The first card in the right column is “Usage and limits”, with the plan badge and one bar for every thing that can fill up: Products (SKUs), Users, Warehouses, Saved forecasts and API keys.",
                    "The bar changes colour as you approach — amber past 80 %, red at the ceiling — and the “I need more room” button opens the dialog to write to us: there is nothing to buy on the screen.",
                    "“Currency” decides which symbol your figures are shown in — inventory value, unit costs and purchase-order totals — and shows an example with the formatting applied.",
                    "“Time zone” is the one used to read the frequencies in Automation: schedule “every Monday at 6am” and it runs at 6am in that zone, and the card tells you what time it is there right now.",
                    "“App Settings” holds the language (Español / English) and the theme (Light / Dark); these are your own preferences, stored on your account, and they affect nobody else.",
                    "“How often your purchases are computed” only appears when your history affords more than one period, explains why the system chose the one in use, and lets you switch between Day, Week and Month.",
                    "“Security” changes the password in two steps: you type the new one (at least 8 characters), we email you a 6-digit code that expires in 10 minutes, and you confirm with it.",
                    "“WhatsApp” links and verifies your number with a code, and below it the direct-SMS toggle stays blocked until you have a verified number.",
                    "Further down, “Available Models” lists the platform’s forecasting models with their category and whether they are available or in beta.",
                    "At the bottom, “Activity Logs” shows what has been done in the account — action, resource, status and date — with a filter by action type and a “Load more” button that says how many records are left.",
                ],
                "fields": [
                    ("Full name", "The only editable part of your profile. Saved with the “Save Changes” button on that card."),
                    ("Email address", "Shown, not edited here. An administrator changes it from Users, and the change forces re-verification."),
                    ("Role and Account status", "Informational. The role is assigned by an administrator from Users."),
                    ("Language and Theme", "Yours. Stored on your account, so they follow you to any browser you sign in from."),
                    ("Currency", "Company-wide, and only an administrator changes it. It changes the symbol; it does not convert amounts you already loaded."),
                    ("Time zone", "Company-wide, and only an administrator changes it. It rules the hour at which scheduled recalculations run."),
                    ("How often your purchases are computed", "Day, Week or Month, for the whole account and only by an administrator. Only the periods your history affords are offered."),
                    ("Usage and limits", "Five bars: Products (SKUs), Users, Warehouses, Saved forecasts and API keys. On the free plan those are 100, 2, 1, 3 and 1; on the full plan they read “Unlimited”."),
                    ("Security", "Password change with a 6-digit code emailed to you, valid for 10 minutes."),
                    ("WhatsApp", "Your verified number for receiving messages from Faro. Without it the SMS toggle cannot be turned on."),
                ],
                "tasks": [
                    (
                        "Change your password",
                        "1. Open My account and scroll to “Security”. "
                        "2. Click “Change password”. "
                        "3. Type the new password, at least 8 characters. "
                        "4. Press “Send code to email” and check your inbox. "
                        "5. Enter the 6-digit code before the 10 minutes run out and confirm the change."
                    ),
                    (
                        "See how much room you have left and ask for more",
                        "1. Open My account and look at the “Usage and limits” card, top right. "
                        "2. Read each bar: it tells you how much of how much you have used. "
                        "3. If any is amber or red, click “I need more room”. "
                        "4. Choose how you would rather talk to us: WhatsApp, email, or the form in the same dialog. "
                        "5. Tell us how many products, warehouses and people you handle: that is what we need in order to raise your limits."
                    ),
                ],
                "gotchas": [
                    "Changing the currency only changes the symbol: it converts nothing. If your costs are in colones and you pick dollars, you will see colones with a dollar sign.",
                    "The time zone does not change how your sales or forecasts are displayed. The only thing it decides is what hour the scheduled recalculations run at.",
                    "Changing how often your purchases are computed is not a change of view: it changes the quantities we suggest you buy and what reaches you by email and WhatsApp every morning, and a product with little history can disappear from the daily calculation and still appear in the weekly one.",
                    "The usage panel shows five limits, not every limit there is. The maximum file size (25 MB on the free plan) and the daily API call ceiling are counted separately.",
                    "There is no purchase screen and no checkout. Moving to the full plan is a conversation with us, and the “I need more room” button is the entire commercial surface of the product.",
                ],
            },
            {
                "name": "Automation",
                "route": "/automatizacion",
                "image": "automatizacion",
                "purpose": (
                    "Two things that make Faro work without anybody opening it: the keys "
                    "another system uses to reach your data, and the schedule on which your "
                    "forecasts recompute themselves. Only an administrator sees this entry "
                    "in the menu."
                ),
                "walkthrough": [
                    "The screen has two tabs: “API Keys” and “Schedules”.",
                    "In API Keys, the first thing you read is the fact that matters: the key is shown once, when you create it, we do not store it anywhere, and if it is lost you create another and revoke the old one.",
                    "“Generate key” opens two fields: a name so you can recognise it later — for instance “ERP integration” — and “What the key can do”, with two options, “Read only” and “Read and write”.",
                    "Once created, a box appears with the full key, which starts with sk_live_, and a “Copy” button; that is your only chance to save it.",
                    "The table below lists your keys with Name, Created and Last used — which says “Never” if nobody has used it — and a “Revoke” button per row that asks for confirmation, because any apps using it will stop working.",
                    "In “Schedules” you first pick a session in the “Session:” dropdown; only sessions that finished training appear there.",
                    "“Frequency” offers six ready-made schedules and shows the matching cron expression in monospace underneath, so there is no doubt about what was saved.",
                    "The “Schedule enabled” checkbox keeps the schedule saved but switched off when you clear it, and the button saves or updates depending on whether that session already had one.",
                    "Once saved, the screen tells you “Next run:” with a date and time and states which time zone those hours are in: the one your company has configured in My account.",
                    "Above it sits “Already scheduled”, the list of everything armed across every session — with its frequency, whether it is paused, its next run and, if the last one failed, the error — and each line is a shortcut to edit that schedule.",
                    "Below that, “Recent scheduled runs” shows the real history: date, session and whether it finished, failed, is running, is queued or was cancelled.",
                ],
                "fields": [
                    ("Key name", "Only so you recognise it in the list. Name it after the system that will use it."),
                    ("What the key can do", "“Read only” queries the traffic light and the forecasts. “Read and write” additionally uploads files, queues trainings and records purchase orders."),
                    ("Every Monday at 6am", "Cron 0 6 * * 1."),
                    ("Every day at midnight", "Cron 0 0 * * *."),
                    ("Weekdays at 6am", "Cron 0 6 * * 1-5."),
                    ("Every Sunday at 8am", "Cron 0 8 * * 0."),
                    ("Every hour", "Cron 0 * * * *."),
                    ("First day of month", "Cron 0 0 1 * *."),
                    ("Schedule enabled", "Clearing it keeps the saved schedule but fires nothing until you tick it again."),
                ],
                "tasks": [
                    (
                        "Create a key so your ERP can read the traffic light",
                        "1. Open Automation and stay on the “API Keys” tab. "
                        "2. Click “Generate key”. "
                        "3. Give it a name that says which system uses it, for example “ERP integration”. "
                        "4. Under “What the key can do” leave “Read only” if the ERP will only query. "
                        "5. Press “Create” and copy the key right then with the “Copy” button: it is never shown again. "
                        "6. Paste it into your system’s configuration and check in the table that the “Last used” column stops saying “Never”."
                    ),
                    (
                        "Let your forecasts recompute themselves every week",
                        "1. First confirm in My account that the company time zone is yours. "
                        "2. Open Automation and go to “Schedules”. "
                        "3. Pick the session you want retrained in “Session:”. "
                        "4. Under “Frequency” choose “Every Monday at 6am”. "
                        "5. Leave “Schedule enabled” ticked and press “Save schedule”. "
                        "6. Check that it appears under “Already scheduled” with the next run at the hour you expected."
                    ),
                ],
                "gotchas": [
                    "The key is shown once. If you close the box without copying it there is no way to recover it: you have to create another and revoke the old one.",
                    "Revoking is immediate and warns nobody. Anything that was using that key stops working on its next call.",
                    "The free plan holds a single API key, and every key allows 120 calls per minute and 500 per day; on the full plan the daily ceiling disappears.",
                    "Schedule hours are read in your company’s time zone, not the server’s and not your browser’s. If that zone is wrong in My account, everything scheduled runs at the wrong hour.",
                    "Only sessions that already finished training can be scheduled. If the dropdown says “No completed sessions”, train one first in My sales.",
                    "Check “Already scheduled” and “Recent scheduled runs” from time to time: a schedule that has been failing for weeks looks just as calm in the menu, and that is where the error shows up.",
                ],
            },
            {
                "name": "Public API",
                "route": "/api",
                "image": "api",
                "purpose": (
                    "The API reference, and also a console: every endpoint can be run from "
                    "the page itself with your own key. It exists so your system pushes the "
                    "data and takes the decision without anyone opening Faro. It is not "
                    "administrators only: an analyst is usually the person who wires an "
                    "integration."
                ),
                "walkthrough": [
                    "The header shows “The base URL” with the exact address your calls go to, /v1 included; the paths on the page are written without that prefix, and the base is the $FARO value in the examples.",
                    "Below it there is a “Your API key” field where you paste an sk_live_ key: it lives only in that tab while you use it and is neither stored nor sent anywhere else.",
                    "If you do not have one yet, the “Generate a key” button takes you straight to Automation.",
                    "The endpoint list stays fixed on the left; on the right, each one with its method, its path and an explanation of what it is for in real work.",
                    "The ones that modify data carry the “Writes” badge, and their test fields come with an amber notice of the effect they will have.",
                    "On each endpoint, “Try it” runs the call for real against your account and returns the HTTP status, the duration in milliseconds and the response; “Example” gives you the same call written as a curl command to copy.",
                    "Before running a write, a confirmation names the concrete consequence — replacing your file, queueing a real training run, or recording a purchase order whose reception is then tracked.",
                    "The reference sections at the end explain three things: authentication, which goes in the Authorization header; the rate limits, 120 calls per minute and 500 per day on the free plan, with 429 and Retry-After once you go over; and the envelope, where your payload always comes in data and errors carry error_code.",
                    "The page closes with an explicit promise: these endpoints do not change path or shape without notice, and any other endpoint of the service is internal — reachable with your key, but with no commitment to keep it.",
                ],
                "fields": [
                    ("GET /planning", "Returns the active_session_id the other calls need. Ask for it every run, because it changes when a new session trains."),
                    ("GET /data-sources", "Lists your sources so you know which source_id to replace."),
                    ("POST /data-sources/{source_id}/file", "Writes. Uploads the export in place: the source keeps its id and column mapping. Accepts .csv, .xlsx, .xls, .parquet and .json."),
                    ("POST /sessions/{session_id}/train", "Writes. Queues a retrain. If the session already has a schedule, you do not need this call."),
                    ("GET /sessions/{session_id}/train/status", "The state of the last training run: QUEUED, RUNNING, COMPLETED or FAILED."),
                    ("GET /inventory/status", "Every product with its signal and recommended_qty. There is no pagination: filter with signal or supplier."),
                    ("GET /inventory/morning-briefing", "The same data ordered as a day of work, with the reason, the suggested supplier and the estimated value."),
                    ("POST /inventory/log-po", "Writes. Records the purchase order. Without this call the order does not exist for Faro and your suppliers’ real lead times are never learned."),
                ],
                "tasks": [
                    (
                        "Try the API without writing a line of code",
                        "1. Create a “Read only” key in Automation and copy it. "
                        "2. Open the API screen and paste it into “Your API key”. "
                        "3. Pick GET /planning in the list on the left and press “Run”. "
                        "4. Copy the active_session_id from the response. "
                        "5. Open GET /inventory/morning-briefing and run it: it is the same day of work you see on the Purchasing Panel, in JSON."
                    ),
                    (
                        "Automate the whole cycle from your own system",
                        "1. Create a “Read and write” key. "
                        "2. On every run, call GET /planning first to get the active_session_id. "
                        "3. Upload the latest sales export with POST /data-sources/{source_id}/file over the source you already use. "
                        "4. Queue the recalculation with POST /sessions/{session_id}/train, or skip this step if that session already has a schedule armed. "
                        "5. Poll GET /sessions/{session_id}/train/status until it says COMPLETED. "
                        "6. Read GET /inventory/status and build your order from the signal and the recommended_qty. "
                        "7. When the order actually goes out, record it with POST /inventory/log-po, including the lines you rejected."
                    ),
                ],
                "gotchas": [
                    "There is no test environment. The “Run” button on this page acts on your real account: uploading a file replaces that source’s file, and recording an order makes it appear in Orders.",
                    "Careful with log-po: if you send an empty body, or one without an items list, nothing empty gets recorded — an order is recorded containing EVERY PEDIR_YA and PEDIR_PRONTO product in that session.",
                    "A “Read only” key cannot write. If your integration uploads files, trains or records orders, it needs a “Read and write” key.",
                    "Branch on error_code, never on the error text: the text can change language or wording, the code does not.",
                    "The active_session_id changes every time a new session trains. Hard-coding it in your integration is the easiest way to end up reading a stale forecast.",
                    "Only these endpoints carry a stability commitment. Your key reaches others, but nobody promised to keep them as they are.",
                ],
            },
        ],
    },
}

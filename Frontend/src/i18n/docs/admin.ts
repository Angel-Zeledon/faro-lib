// Help center — Administration section. Verified against Frontend/src/app/
// usuarios, mi-cuenta, automatizacion, instalacion; components/limits/;
// i18n/serviceConfig.ts; backend/entitlements/plans.py; backend/service_config/
// access.py; backend/sessions/retrain_service.py; backend/auth/api_key_auth.py
// and the admin guards in backend/api/v1 (currency, timezone, planning, users).
import type { DocSectionContent } from '@/i18n/docs/types'
import type { DocPageIdOf } from '@/i18n/docs/tree'

export const ADMIN: DocSectionContent<DocPageIdOf<'administracion'>> = {
  es: {
    'administracion/usuarios-y-roles': {
      title: 'Usuarios y roles',
      description:
        'Cómo sumar a tu equipo, qué puede hacer cada rol y cómo quitarle el acceso a alguien. Los permisos los decide el rol, no una lista por persona.',
      blocks: [
        { t: 'p', text: 'El equipo se administra en [Usuarios](app:/usuarios), en el grupo Sistema del menú. Solo un administrador ve esa entrada; si alguien sin el rol abre la dirección, la pantalla dice «No tienes permiso para ver esta página.»' },
        { t: 'shot', key: 'usuarios', alt: 'La tabla de usuarios con nombre, estado, rol, fecha de creación, último acceso y acciones' },

        { t: 'h2', id: 'roles', text: 'Los tres roles' },
        { t: 'table', head: ['Puede…', 'Administrador', 'Analista', 'Solo lectura'], rows: [
          ['Ver todas las pantallas, pronósticos, el semáforo y las órdenes', 'Sí', 'Sí', 'Sí'],
          ['Usar el asistente y escribir mensajes al equipo', 'Sí', 'Sí', 'Sí'],
          ['Subir ventas, entrenar y editar inventario, proveedores y reglas del semáforo', 'Sí', 'Sí', 'No'],
          ['Generar, enviar, recibir, marcar pagada y cancelar órdenes de compra', 'Sí', 'Sí', 'No'],
          ['Guardar escenarios, programar recálculos y crear llaves de API', 'Sí', 'Sí', 'No'],
          ['Crear, editar, suspender y eliminar usuarios', 'Sí', 'No', 'No'],
          ['Cambiar la moneda, la zona horaria y cada cuánto se calculan las compras', 'Sí', 'No', 'No'],
          ['Configurar los canales de la empresa en Instalación', 'Sí', 'No', 'No'],
        ] },
        { t: 'p', text: 'Un usuario de **Solo lectura** que intenta algo que cambia datos recibe «Tu rol no puede hacer esto» y nada cambia. En las pantallas, los botones que no le corresponden no aparecen.' },
        { t: 'note', tone: 'info', text: 'No hay permisos individuales que configurar: lo que puede hacer cada persona lo decide solo su rol. Para que alguien pueda menos, cámbiale el rol.' },

        { t: 'h2', id: 'add-someone', text: 'Sumar a alguien' },
        { t: 'steps', items: [
          'En [Usuarios](app:/usuarios), pulsa «Crear usuario».',
          'Escribe su «Correo electrónico» y, si quieres, su «Nombre completo (opcional)».',
          'Elige el rol: Analista si va a comprar o cargar datos, Solo lectura si solo va a mirar.',
          'Guarda. Le llega un correo de configuración de cuenta con un enlace de verificación.',
          'Mientras no lo abra aparece como «Pendiente». Si no le llegó, usa el icono de sobre de su fila: «Reenviar correo de verificación».',
        ] },
        { t: 'p', text: 'Para invitar a alguien tu propio correo tiene que estar verificado: invitar es algo que sale de tu empresa.' },

        { t: 'h2', id: 'the-table', text: 'La tabla' },
        { t: 'dl', items: [
          ['Nombre / Correo', 'Tu propia fila lleva «(tú)».'],
          ['Estado', 'Activo trabaja normal; Pendiente todavía no abrió el enlace de verificación; Inactivo y Suspendido no pueden entrar.'],
          ['Rol', 'Administrador, Analista o Solo lectura.'],
          ['Creado / Último acceso', 'Cuándo se creó la cuenta y cuándo entró por última vez.'],
          ['Acciones', '«Cambiar estado», editar (nombre, correo y rol) y eliminar. En tu propia fila no aparecen ni el cambio de estado ni eliminar: nadie puede suspenderse ni borrarse a sí mismo.'],
        ] },
        { t: 'p', text: 'Arriba hay un buscador por nombre o correo y dos filtros, «Todos los estados» y «Todos los roles».' },

        { t: 'h2', id: 'remove-access', text: 'Quitarle el acceso a alguien' },
        { t: 'ul', items: [
          '**Temporal:** cambia su estado a Inactivo o Suspendido. Conserva su cuenta y no puede entrar.',
          '**Definitivo:** el icono de papelera abre «¿Eliminar usuario?». Se eliminan todas sus sesiones, tokens y permisos, y no se puede deshacer.',
          'Las llaves de API que haya creado siguen funcionando aunque la persona ya no esté. Revócalas en [Automatización](/docs/administracion/automatizacion).',
        ] },
        { t: 'p', text: 'Cambiarle el correo a alguien lo obliga a verificar el nuevo.' },

        { t: 'h2', id: 'free-plan', text: 'Límite de usuarios' },
        { t: 'p', text: 'Una cuenta puede tener un tope de usuarios. Al intentar crear uno más del tope se abre el cuadro «Llegaste al límite de tu plan», que te da las formas de escribirnos. Ver [Límites de uso](/docs/administracion/limites-del-plan).' },
      ],
    },

    'administracion/mi-cuenta': {
      title: 'Mi cuenta',
      description:
        'Tu perfil, tu idioma y tu contraseña, y también lo que vale para toda la empresa: moneda, zona horaria, cada cuánto se calculan tus compras y cuánto espacio te queda.',
      blocks: [
        { t: 'p', text: '[Mi cuenta](app:/mi-cuenta) mezcla dos cosas que conviene no confundir. A la izquierda está lo tuyo, que no afecta a nadie más; a la derecha, lo de la empresa, que solo un administrador cambia y que aplica a todo el equipo. En el celular la misma pantalla es una lista agrupada: cada fila abre su sección.' },
        { t: 'shot', key: 'cuenta', alt: 'La pantalla Mi cuenta con el perfil a la izquierda y uso y límites, moneda y zona horaria a la derecha' },

        { t: 'h2', id: 'yours', text: 'Lo tuyo' },
        { t: 'dl', items: [
          ['Perfil de Usuario', 'Tu «Nombre completo» se edita con el lápiz y se guarda ahí mismo. El correo, el rol y el estado de la cuenta se muestran pero no se editan aquí: el correo y el rol los cambia un administrador desde [Usuarios](/docs/administracion/usuarios-y-roles).'],
          ['Configuración de App', 'Idioma (Español / English) y tema (claro / oscuro). Se guardan en tu cuenta, así que te siguen a cualquier navegador donde inicies sesión.'],
          ['Seguridad', 'El cambio de contraseña, con un código de 6 dígitos que te enviamos al correo. Si tu instalación tiene activado el inicio con Google, Apple o Facebook, aquí también ves tus «Cuentas vinculadas» y puedes desvincularlas.'],
          ['Vincular WhatsApp', 'Tu número verificado. Ahí te llegan las alertas de inventario por WhatsApp y desde ahí puedes [hablar con el asistente](/docs/asistente/asistente-por-whatsapp); si tu cuenta no tiene el bot de WhatsApp habilitado, la tarjeta lo indica y no se vincula número.'],
          ['Mensajes del equipo', 'El interruptor «Recibir aviso cuando te escriban»: un aviso por WhatsApp (o SMS) cuando un compañero te escribe en Mensajes. Necesita tu número vinculado, que se vincula con el bot de WhatsApp habilitado.'],
          ['Legal', 'Enlaces a los términos, la privacidad y los demás documentos legales.'],
        ] },

        { t: 'h3', id: 'change-password', text: 'Cambiar tu contraseña' },
        { t: 'steps', items: [
          'En «Seguridad», pulsa «Cambiar contraseña».',
          'Escribe la nueva contraseña, de al menos 8 caracteres.',
          'Pulsa «Enviar código al correo»: te llega un código de 6 dígitos. Expira en 10 minutos.',
          'Escríbelo y confirma el cambio.',
        ] },
        { t: 'p', text: 'Si entras solo con Google, Apple o Facebook, tu cuenta todavía no tiene contraseña; este mismo paso te crea una para tener otra forma de entrar.' },

        { t: 'h2', id: 'company', text: 'Lo de la empresa' },
        { t: 'dl', items: [
          ['Uso y límites', 'Una barra por cada techo: Productos (SKUs), Usuarios, Bodegas, Pronósticos guardados y Llaves de API. La barra se pone ámbar desde el 80 % y roja al llegar al tope. «Necesito más espacio» abre el cuadro para escribirnos. Ver [Límites de uso](/docs/administracion/limites-del-plan).'],
          ['Cada cuánto se calculan tus compras', 'Día, semana o mes. Solo se ofrecen los que tu historial permite, y la tarjeta explica por qué se usa el actual. Ver [Granularidad](/docs/conceptos/granularidad).'],
          ['Moneda', 'En qué moneda se muestran tus cifras: valor del inventario, costos y totales de las órdenes.'],
          ['Zona horaria', 'A qué hora corren tus recálculos programados.'],
        ] },
        { t: 'note', tone: 'warn', title: 'Antes de cambiar algo de la empresa', text: 'Cambiar la **moneda** solo cambia el símbolo: no convierte ningún monto. Cambiar **cada cuánto se calculan tus compras** no es un cambio de vista: cambia las cantidades sugeridas y lo que llega cada mañana por correo y WhatsApp, y un producto con poca historia puede desaparecer del cálculo diario y aparecer en el semanal. Las tres cosas las cambia solo un administrador.' },

        { t: 'h2', id: 'how-it-calculates', text: 'Cómo calcula StockAI' },
        { t: 'p', text: 'Debajo de las dos columnas, una línea lo resume: StockAI prueba varios métodos con cada producto y se queda con el que menos se equivoca sobre tu propio historial. No hay nada que configurar. El detalle está en [Cómo compiten los modelos](/docs/conceptos/como-compiten-los-modelos).' },

        { t: 'h2', id: 'activity-log', text: 'Registros de Actividad' },
        { t: 'p', text: 'Al final está el registro de lo que se hizo en la cuenta — acción, recurso, estado y fecha — con un filtro por tipo de acción y «Cargar más» para ver registros anteriores. Para ver lo que StockAI hizo por su cuenta (entrenamientos, envíos, alertas) usa [Qué ha pasado](/docs/analisis/actividad).' },
      ],
    },

    'administracion/automatizacion': {
      title: 'Automatización',
      description:
        'Que tus pronósticos se recalculen solos en un horario fijo, y las llaves de API con las que otro sistema lee o escribe en tu cuenta.',
      blocks: [
        { t: 'p', text: '[Automatización](app:/automatizacion) tiene dos pestañas: «Tareas programadas», que es la que abre primero, y «API Keys». En el menú, la entrada la ven los administradores.' },
        { t: 'shot', key: 'automatizacion', alt: 'La pestaña de tareas programadas con el selector de sesión, la frecuencia y la próxima ejecución' },

        { t: 'h2', id: 'schedules', text: 'Recálculos programados' },
        { t: 'p', text: 'Una programación vuelve a entrenar una actualización en un horario recurrente, sin que nadie suba nada ni pulse un botón.' },
        { t: 'steps', items: [
          'Confirma primero en [Mi cuenta](/docs/administracion/mi-cuenta) que la zona horaria de la empresa es la tuya.',
          'En «Tareas programadas», elige en «Sesión:» la actualización que quieres recalcular. Solo aparecen las que terminaron de entrenar; si dice «Sin sesiones completadas», entrena una primero en [Mis ventas](/docs/primeros-pasos/subir-tus-ventas).',
          'Elige la frecuencia. Debajo se ve la expresión cron que corresponde, para que no haya duda de qué se guarda.',
          'Deja marcada «Programación activada» y pulsa «Guardar programación».',
          'Comprueba en «Ya programado» que aparece con la «Próxima ejecución:» a la hora que esperabas.',
        ] },
        { t: 'table', head: ['Frecuencia', 'Cron'], rows: [
          ['Cada lunes a las 6am', '`0 6 * * 1`'],
          ['Todos los días a medianoche', '`0 0 * * *`'],
          ['Días laborables a las 6am', '`0 6 * * 1-5`'],
          ['Cada domingo a las 8am', '`0 8 * * 0`'],
          ['Cada hora', '`0 * * * *`'],
          ['Primer día del mes', '`0 0 1 * *`'],
        ] },
        { t: 'p', text: 'Las horas se leen en la zona horaria de tu empresa, no en la de tu navegador ni la del servidor; la pantalla lo recuerda debajo de la lista. Desmarcar «Programación activada» deja el horario guardado pero en pausa.' },

        { t: 'h3', id: 'safe-retrain', text: 'Un recálculo que falla no te deja sin números' },
        { t: 'p', text: 'Cada corrida programada entrena una **actualización nueva** a partir de la que elegiste, que nunca se toca. Solo cuando la nueva termina bien pasa a ser la que leen el Panel de compras, el semáforo y las alertas. Si falla, sigues viendo los números de la vez anterior, y el error aparece en «Ya programado» y en «Últimas corridas programadas».' },
        { t: 'p', text: 'Para no llenarte el espacio, cada programación ocupa como mucho dos pronósticos guardados: el que se está leyendo y el que se está entrenando para reemplazarlo. Las actualizaciones que creaste tú no se borran nunca.' },
        { t: 'note', tone: 'info', text: 'Revisa de vez en cuando «Últimas corridas programadas»: muestra cada corrida con su fecha, su sesión y si terminó bien, falló, está corriendo, está en cola o se canceló. Las fallas también quedan en [Qué ha pasado](/docs/analisis/actividad).' },

        { t: 'h2', id: 'api-keys', text: 'Llaves de API' },
        { t: 'p', text: 'Una llave le permite a otro sistema — tu ERP, tu POS, un script — usar StockAI sin que nadie inicie sesión. La referencia de qué se puede llamar está en [API](/docs/integraciones/api).' },
        { t: 'steps', items: [
          'En la pestaña «API Keys», pulsa «Generar key».',
          'Ponle un nombre que diga qué sistema la usa, por ejemplo «Integración ERP».',
          'En «Qué puede hacer la clave» elige «Solo leer» o «Leer y escribir».',
          'Pulsa «Crear» y copia la clave en ese momento con «Copiar». Empieza por `sk_live_` y **no se vuelve a mostrar**.',
          'Pégala en tu sistema y comprueba en la tabla que «Último uso» deja de decir «Nunca».',
        ] },
        { t: 'dl', items: [
          ['Solo leer', 'Consulta el semáforo, los pronósticos y el resto de los datos, como un usuario de Solo lectura.'],
          ['Leer y escribir', 'Además sube archivos, encola entrenamientos y registra órdenes de compra, como un Analista.'],
          ['Revocar', 'Es inmediato: todo lo que usaba esa llave deja de funcionar en la siguiente llamada. Si perdiste una clave, crea otra y revoca la vieja.'],
        ] },
        { t: 'p', text: 'Cada llave admite 120 llamadas por minuto. Las llaves de API vienen con el código fuente. Si tu cuenta no las tiene habilitadas, esta pestaña muestra una tarjeta de función no disponible en lugar de las llaves.' },
      ],
    },

    'administracion/instalacion': {
      title: 'Instalación',
      description:
        'Qué servicios tiene conectados este StockAI, cuáles están encendidos y qué se pierde con los que no. Una pestaña es de quien opera el servidor; la otra, de tu empresa.',
      blocks: [
        { t: 'p', text: 'StockAI funciona sin ningún servicio externo: el pronóstico, el semáforo y las órdenes de compra no dependen de nada de afuera. El asistente, los correos, el WhatsApp y la búsqueda en documentos sí. Cuando algo de eso «no responde», [Instalación](app:/instalacion) dice por qué.' },
        { t: 'shot', key: 'instalacion', alt: 'La pantalla de instalación con una tarjeta por servicio y su estado' },

        { t: 'h2', id: 'two-tabs', text: 'Dos pestañas, dos dueños' },
        { t: 'dl', items: [
          ['Esta instalación', 'Todos los servicios del despliegue: el asistente (DeepSeek), el correo, WhatsApp, SMS, el inicio de sesión con Google, Apple y Facebook, el contacto comercial, las tareas programadas y más. Es de quien opera el servidor, no de cualquier administrador de empresa.'],
          ['Mis canales', 'La identidad con la que tu empresa le escribe a tu gente y a tus proveedores: tu cuenta de correo y tu número de WhatsApp. La edita un administrador de tu empresa. Lo que dejes vacío usa lo de la instalación.'],
        ] },
        { t: 'p', text: 'La pantalla abre en la pestaña que te corresponde. En el menú, la entrada solo la ven los administradores.' },

        { t: 'h2', id: 'who-operates', text: 'Quién opera la instalación' },
        { t: 'p', text: 'No lo decide el rol de administrador, porque cada empresa que se registra tiene uno. Lo decide la variable `INSTANCE_ADMIN_EMAILS` del servidor: la lista de correos de los administradores que pueden abrir «Esta instalación». Si tu cuenta no está en ella, verás «Esta cuenta no opera la instalación»; tus propios canales sí los puedes configurar en la otra pestaña.' },
        { t: 'note', tone: 'warn', title: 'Instalación recién montada', text: 'Si tu empresa es la única en el servidor y nadie está en `INSTANCE_ADMIN_EMAILS`, su administrador opera la instalación sin que nadie lo nombre. Ese acceso se termina en cuanto entra una segunda empresa. Antes de invitar a alguien, pon tu correo en `INSTANCE_ADMIN_EMAILS` (en el archivo .env del servidor) y reinicia, o nadie podrá volver a abrir esa pestaña.' },

        { t: 'h2', id: 'card-states', text: 'Qué dice cada tarjeta' },
        { t: 'dl', items: [
          ['Listo', 'Tiene lo que necesita para funcionar.'],
          ['Sin configurar', 'Le falta una credencial. No es un error: la tarjeta dice qué se pierde mientras esté así y nombra la variable que falta.'],
          ['Con problemas', 'La credencial está puesta, pero el proveedor la rechazó, no respondió o no se le pudo alcanzar. El arreglo puede ser una llave nueva o revisar la red.'],
          ['Apagado', 'Un interruptor del despliegue que está deliberadamente en falso.'],
        ] },
        { t: 'p', text: '«Probar conexión» le pregunta al proveedor si la credencial sirve; nunca manda un correo ni un WhatsApp a nadie para averiguarlo. Que una tarjeta diga «Listo» significa que tiene sus credenciales, no que el proveedor esté sano: para eso está la prueba.' },

        { t: 'h2', id: 'value-source', text: 'De dónde viene cada valor' },
        { t: 'p', text: 'Cada campo dice qué valor está mandando («Manda»): de tu empresa, de este panel, del entorno (el archivo .env del servidor) o el valor por defecto. Lo que se guarda en el panel gana sobre el archivo y toma efecto sin reiniciar.' },
        { t: 'ul', items: [
          'Los campos «Solo entorno» se muestran pero no se editan: el servidor los lee al arrancar.',
          'Una llave guardada no se vuelve a mostrar; como mucho ves sus últimos cuatro caracteres, para reconocer cuál pegaste.',
          '«Volver al entorno» borra lo que el panel guardó para ese servicio y deja mandando otra vez el archivo del servidor.',
        ] },

        { t: 'h2', id: 'my-channels', text: 'Mis canales' },
        { t: 'p', text: 'Aquí configuras el remitente que ven tus proveedores y tu equipo: tu llave de Resend o los datos de tu servidor SMTP, la dirección remitente y tu número de WhatsApp. Desde ese momento tus alertas y tus órdenes de compra salen con tu identidad.' },
        { t: 'note', tone: 'info', text: 'Los correos de la cuenta — verificar el correo, recuperar la contraseña, invitar a alguien — siempre salen por el transporte de la instalación, aunque tu empresa tenga el suyo. Una empresa no puede mandar el mensaje que da acceso a una cuenta.' },

        { t: 'h2', id: 'turn-on-assistant', text: 'Ejemplo: encender el asistente' },
        { t: 'steps', items: [
          'En «Esta instalación», busca la tarjeta «Asistente (DeepSeek)».',
          'Si dice «Sin configurar», la tarjeta nombra la variable que falta: `DEEPSEEK_API_KEY`.',
          'Pega la llave y pulsa «Guardar».',
          'Pulsa «Probar conexión»: tiene que decir «Responde correctamente.»',
          'Abre el [asistente](/docs/asistente/asistente-ia): el aviso de «no disponible» ya no está.',
        ] },
      ],
    },

    'administracion/limites-del-plan': {
      title: 'Límites de uso',
      nav: 'Límites de uso',
      description:
        'Qué pasa cuando una cuenta llega a un tope de uso, cómo ver cuánto te queda y cómo pedir más espacio.',
      blocks: [
        { t: 'p', text: 'Una cuenta puede tener topes de uso: productos (SKUs), usuarios, bodegas, pronósticos guardados, llaves de API y tamaño de archivo. Las pantallas, el pronóstico y el asistente son los mismos; los topes solo cambian cuánto cabe. La API (llaves `sk_live_`), el acceso por MCP y el bot de WhatsApp vienen con el código fuente.' },

        { t: 'h2', id: 'see-usage', text: 'Ver cuánto te queda' },
        { t: 'p', text: 'En [Mi cuenta](app:/mi-cuenta), la tarjeta «Uso y límites» muestra una barra por Productos (SKUs), Usuarios, Bodegas, Pronósticos guardados y Llaves de API. Se pone ámbar desde el 80 % y roja al llegar al tope. El tamaño de archivo y las llamadas de API por día no tienen barra: se comprueban al subir el archivo y en cada llamada.' },

        { t: 'h2', id: 'at-the-limit', text: 'Qué pasa al llegar a un techo' },
        { t: 'p', text: 'La acción que lo pasaría no se hace — por ejemplo, crear un usuario o una bodega de más — y se abre el cuadro «Llegaste al límite de tu plan», que dice cuánto llevas de cuánto. Lo que ya tienes no se toca: nada se borra ni se apaga por estar en el techo.' },
        { t: 'p', text: 'Si el aviso llega como mensaje, dice cuánto llevas de cuánto y te pide escribirnos para ampliarlo.' },
        { t: 'p', text: 'Usar la API, el MCP o el bot de WhatsApp cuando tu cuenta no los tiene habilitados tampoco se hace: la API responde con un error, y en la pantalla aparece una tarjeta de función no disponible con el botón «Escríbenos para activarlo».' },

        { t: 'h2', id: 'more-room', text: 'Pedir más espacio' },
        { t: 'p', text: 'Para ampliar los límites, hablas con nosotros:' },
        { t: 'steps', items: [
          'Pulsa «Necesito más espacio» en Mi cuenta, o espera a que el cuadro se abra solo al llegar a un techo.',
          'Escríbenos por WhatsApp o por correo, o deja tu solicitud en el formulario del mismo cuadro («Enviar solicitud»).',
          'Cuéntanos cuántos productos, bodegas y personas manejas: con eso te ampliamos los límites.',
        ] },
        { t: 'p', text: 'Los botones de WhatsApp y correo solo aparecen si la instalación tiene configurado su contacto comercial; el formulario está siempre.' },

        { t: 'h2', id: 'trial', text: 'Cuentas de prueba' },
        { t: 'note', tone: 'info', title: 'Una cuenta de prueba tiene sus propios techos', text: 'La cuenta de prueba de 24 horas que se crea desde la página principal trae el producto con muy poco espacio: 30 productos, 1 usuario, 2 bodegas (para que puedas probar traslados), 2 pronósticos guardados, archivos de hasta 5 MB y un entrenamiento a la vez. No incluye la API, el MCP ni el bot de WhatsApp. Ver [Crear tu cuenta](/docs/primeros-pasos/crear-tu-cuenta).' },
      ],
    },
  },

  en: {
    'administracion/usuarios-y-roles': {
      title: 'Users and roles',
      description:
        'How to add your team, what each role can do, and how to take someone\'s access away. Permissions come from the role, not from a per-person list.',
      blocks: [
        { t: 'p', text: 'The team is managed in [Users](app:/usuarios), in the System group of the menu. Only an administrator sees that entry; if someone without the role opens the address, the screen says "You don\'t have permission to view this page."' },
        { t: 'shot', key: 'usuarios', alt: 'The users table with name, status, role, creation date, last login and actions' },

        { t: 'h2', id: 'roles', text: 'The three roles' },
        { t: 'table', head: ['Can…', 'Administrator', 'Analyst', 'Read only'], rows: [
          ['See every screen, forecast, stock signal and order', 'Yes', 'Yes', 'Yes'],
          ['Use the assistant and message the team', 'Yes', 'Yes', 'Yes'],
          ['Upload sales, train, and edit inventory, suppliers and signal rules', 'Yes', 'Yes', 'No'],
          ['Create, send, receive, mark paid and cancel purchase orders', 'Yes', 'Yes', 'No'],
          ['Save scenarios, schedule recalculations and create API keys', 'Yes', 'Yes', 'No'],
          ['Create, edit, suspend and delete users', 'Yes', 'No', 'No'],
          ['Change the currency, the time zone and how often purchases are calculated', 'Yes', 'No', 'No'],
          ["Configure the company's channels under Installation", 'Yes', 'No', 'No'],
        ] },
        { t: 'p', text: 'A **Read only** user who tries something that changes data is told their role cannot do it, and nothing changes. On screen, the buttons they cannot use are not shown.' },
        { t: 'note', tone: 'info', text: 'There are no per-person permissions to configure: what someone can do is decided by their role alone. To let someone do less, change their role.' },

        { t: 'h2', id: 'add-someone', text: 'Add someone' },
        { t: 'steps', items: [
          'In [Users](app:/usuarios), press "Create user".',
          'Type their "Email address" and, if you like, their "Full name (optional)".',
          'Pick the role: Analyst if they will buy or load data, Read only if they will only look.',
          'Save. They receive an account setup email with a verification link.',
          'Until they open it they show as "Pending". If it did not arrive, use the envelope icon on their row: "Resend verification email".',
        ] },
        { t: 'p', text: 'Your own email has to be verified before you can invite anyone: an invitation is something that leaves your company.' },

        { t: 'h2', id: 'the-table', text: 'The table' },
        { t: 'dl', items: [
          ['Name / Email', 'Your own row is marked "(you)".'],
          ['Status', 'Active works normally; Pending has not opened the verification link yet; Inactive and Suspended cannot sign in.'],
          ['Role', 'Administrator, Analyst or Read only.'],
          ['Created / Last login', 'When the account was created and when it last signed in.'],
          ['Actions', '"Change status", edit (name, email and role) and delete. Your own row has neither the status change nor delete: nobody can suspend or delete themselves.'],
        ] },
        { t: 'p', text: 'Above the table there is a search by name or email and two filters, "All statuses" and "All roles".' },

        { t: 'h2', id: 'remove-access', text: "Take someone's access away" },
        { t: 'ul', items: [
          '**Temporarily:** change their status to Inactive or Suspended. Their account stays and they cannot sign in.',
          '**For good:** the bin icon opens "Delete user?". All their sessions, tokens and permissions are removed, and it cannot be undone.',
          'API keys they created keep working after they are gone. Revoke them under [Automation](/docs/administracion/automatizacion).',
        ] },
        { t: 'p', text: "Changing someone's email makes them verify the new one." },

        { t: 'h2', id: 'free-plan', text: 'User limit' },
        { t: 'p', text: 'An account can have a ceiling on users. Trying to create one more than the ceiling opens the "You reached your plan limit" dialog with the ways to reach us. See [Usage limits](/docs/administracion/limites-del-plan).' },
      ],
    },

    'administracion/mi-cuenta': {
      title: 'My account',
      description:
        'Your profile, your language and your password — and also what applies to the whole company: currency, time zone, how often your purchases are calculated and how much room you have left.',
      blocks: [
        { t: 'p', text: "[My account](app:/mi-cuenta) mixes two things worth keeping apart. On the left is what is yours, which affects nobody else; on the right, the company's settings, which only an administrator changes and which apply to the whole team. On a phone the same screen is a grouped list: each row opens its section." },
        { t: 'shot', key: 'cuenta', alt: 'The My account screen with the profile on the left and usage and limits, currency and time zone on the right' },

        { t: 'h2', id: 'yours', text: 'What is yours' },
        { t: 'dl', items: [
          ['User Profile', 'Your "Full name" is edited with the pencil and saved in place. Email, role and account status are shown but not edited here: an administrator changes email and role under [Users](/docs/administracion/usuarios-y-roles).'],
          ['App Settings', 'Language (Español / English) and theme (light / dark). They are saved on your account, so they follow you to any browser you sign in on.'],
          ['Security', 'Changing your password, with a 6-digit code we email you. If your installation has sign-in with Google, Apple or Facebook turned on, this is also where you see your "Linked accounts" and can unlink them.'],
          ['Link WhatsApp', 'Your verified number. Your WhatsApp inventory alerts arrive there and from it you can [talk to the assistant](/docs/asistente/asistente-por-whatsapp); if your account does not have the WhatsApp bot enabled, the card says so and no number is linked.'],
          ['Team messages', 'The "Get a heads-up when someone writes to you" switch: a WhatsApp (or SMS) notice when a colleague writes to you in Messages. It needs your number linked, which is done with the WhatsApp bot enabled.'],
          ['Legal', 'Links to the terms, the privacy policy and the other legal documents.'],
        ] },

        { t: 'h3', id: 'change-password', text: 'Change your password' },
        { t: 'steps', items: [
          'Under "Security", press "Change password".',
          'Type the new password, at least 8 characters long.',
          'Press "Send code to email": a 6-digit code arrives. It expires in 10 minutes.',
          'Type it and confirm the change.',
        ] },
        { t: 'p', text: 'If you only sign in with Google, Apple or Facebook, your account has no password yet; this same step creates one so you have another way in.' },

        { t: 'h2', id: 'company', text: "The company's settings" },
        { t: 'dl', items: [
          ['Usage and limits', 'One bar per ceiling: Products (SKUs), Users, Warehouses, Saved forecasts and API keys. A bar turns amber from 80% and red at the ceiling. "I need more room" opens the dialog to reach us. See [Usage limits](/docs/administracion/limites-del-plan).'],
          ['How often your purchases are calculated', 'Day, week or month. Only the ones your history supports are offered, and the card explains why the current one is used. See [Granularity](/docs/conceptos/granularidad).'],
          ['Currency', 'Which currency your figures are shown in: inventory value, costs and purchase-order totals.'],
          ['Time zone', 'What time your scheduled recalculations run.'],
        ] },
        { t: 'note', tone: 'warn', title: 'Before you change a company setting', text: 'Changing the **currency** only changes the symbol: no amount is converted. Changing **how often your purchases are calculated** is not a change of view: it changes the suggested quantities and what arrives each morning by email and WhatsApp, and a product with little history can drop out of the daily calculation and appear in the weekly one. Only an administrator can change any of the three.' },

        { t: 'h2', id: 'how-it-calculates', text: 'How StockAI calculates' },
        { t: 'p', text: 'Below the two columns, one line sums it up: StockAI tries several methods on every product and keeps whichever is least wrong about your own history. There is nothing to configure. The detail is in [How the models compete](/docs/conceptos/como-compiten-los-modelos).' },

        { t: 'h2', id: 'activity-log', text: 'Activity Logs' },
        { t: 'p', text: 'At the bottom is the log of what was done on the account — action, resource, status and date — with a filter by action type and "Load more" for older entries. To see what StockAI did on its own (trainings, sends, alerts), use [What happened](/docs/analisis/actividad).' },
      ],
    },

    'administracion/automatizacion': {
      title: 'Automation',
      description:
        'Have your forecasts recalculate themselves on a fixed schedule, and manage the API keys another system uses to read or write in your account.',
      blocks: [
        { t: 'p', text: '[Automation](app:/automatizacion) has two tabs: "Schedules", which opens first, and "API Keys". In the menu, administrators see the entry.' },
        { t: 'shot', key: 'automatizacion', alt: 'The schedules tab with the session picker, the frequency and the next run' },

        { t: 'h2', id: 'schedules', text: 'Scheduled recalculations' },
        { t: 'p', text: 'A schedule retrains an update on a recurring timetable, with nobody uploading anything or pressing a button.' },
        { t: 'steps', items: [
          "First confirm in [My account](/docs/administracion/mi-cuenta) that the company's time zone is yours.",
          'Under "Schedules", pick the update to recalculate in "Session:". Only finished ones are listed; if it says "No completed sessions", train one first in [My sales](/docs/primeros-pasos/subir-tus-ventas).',
          'Pick the frequency. The matching cron expression is shown below it, so there is no doubt about what gets saved.',
          'Leave "Schedule enabled" ticked and press "Save schedule".',
          'Check under "Already scheduled" that it appears with the "Next run:" at the time you expected.',
        ] },
        { t: 'table', head: ['Frequency', 'Cron'], rows: [
          ['Every Monday at 6am', '`0 6 * * 1`'],
          ['Every day at midnight', '`0 0 * * *`'],
          ['Weekdays at 6am', '`0 6 * * 1-5`'],
          ['Every Sunday at 8am', '`0 8 * * 0`'],
          ['Every hour', '`0 * * * *`'],
          ['First day of month', '`0 0 1 * *`'],
        ] },
        { t: 'p', text: "Hours are read in your company's time zone, not your browser's or the server's; the screen reminds you below the list. Unticking \"Schedule enabled\" keeps the timetable saved but paused." },

        { t: 'h3', id: 'safe-retrain', text: 'A failed recalculation does not leave you without numbers' },
        { t: 'p', text: 'Each scheduled run trains a **new update** from the one you picked, which is never touched. Only once the new one finishes successfully does it become what the Purchasing panel, the stock signal and the alerts read. If it fails, you keep seeing the previous numbers, and the error shows under "Already scheduled" and "Recent scheduled runs".' },
        { t: 'p', text: 'So it does not fill up your room, a schedule takes at most two saved forecasts: the one being read and the one training to replace it. Updates you created yourself are never deleted.' },
        { t: 'note', tone: 'info', text: 'Look at "Recent scheduled runs" now and then: it lists each run with its date, its session, and whether it succeeded, failed, is running, is queued or was cancelled. Failures also show up in [What happened](/docs/analisis/actividad).' },

        { t: 'h2', id: 'api-keys', text: 'API keys' },
        { t: 'p', text: 'A key lets another system — your ERP, your POS, a script — use StockAI without anyone signing in. What it can call is described under [API](/docs/integraciones/api).' },
        { t: 'steps', items: [
          'On the "API Keys" tab, press "Generate key".',
          'Give it a name saying which system uses it, for example "ERP integration".',
          'Under "What the key can do", pick "Read only" or "Read and write".',
          'Press "Create" and copy the key right then with "Copy". It starts with `sk_live_` and **is never shown again**.',
          'Paste it into your system and check in the table that "Last used" no longer says "Never".',
        ] },
        { t: 'dl', items: [
          ['Read only', 'Reads the stock signal, the forecasts and the rest of the data, like a Read only user.'],
          ['Read and write', 'Also uploads files, queues trainings and logs purchase orders, like an Analyst.'],
          ['Revoke', 'Immediate: anything using that key stops working on its next call. If you lost a key, create another and revoke the old one.'],
        ] },
        { t: 'p', text: 'Each key allows 120 calls per minute. API keys ship with the source code. If your account does not have them enabled, this tab shows a feature-unavailable card instead of the keys.' },
      ],
    },

    'administracion/instalacion': {
      title: 'Installation',
      description:
        'Which services this StockAI has connected, which are on, and what is lost with the ones that are not. One tab belongs to whoever operates the server; the other to your company.',
      blocks: [
        { t: 'p', text: 'StockAI works with no outside service at all: forecasting, the stock signal and purchase orders depend on nothing external. The assistant, email, WhatsApp and document search do. When one of those "does not answer", [Installation](app:/instalacion) tells you why.' },
        { t: 'shot', key: 'instalacion', alt: 'The installation screen with one card per service and its status' },

        { t: 'h2', id: 'two-tabs', text: 'Two tabs, two owners' },
        { t: 'dl', items: [
          ['This installation', "Every service of the deployment: the assistant (DeepSeek), email, WhatsApp, SMS, sign-in with Google, Apple and Facebook, the commercial contact, scheduled jobs and more. It belongs to whoever operates the server, not to any company's administrator."],
          ['My channels', "The identity your company uses to write to your people and your suppliers: your own email account and your WhatsApp number. A company administrator edits it. Anything left empty uses the installation's."],
        ] },
        { t: 'p', text: 'The screen opens on the tab that is yours. In the menu, only administrators see the entry.' },

        { t: 'h2', id: 'who-operates', text: 'Who operates the installation' },
        { t: 'p', text: 'Not the administrator role, because every company that signs up has one. It is the server variable `INSTANCE_ADMIN_EMAILS`: the list of administrator emails allowed to open "This installation". If your account is not on it you see "This account does not operate the installation"; you can still configure your own channels on the other tab.' },
        { t: 'note', tone: 'warn', title: 'A freshly set-up installation', text: "If yours is the only company on the server and nobody is listed in `INSTANCE_ADMIN_EMAILS`, its administrator operates the installation without being named. That access ends the moment a second company signs up. Before inviting anybody, put your address in `INSTANCE_ADMIN_EMAILS` (in the server's .env file) and restart, or nobody will be able to open that tab again." },

        { t: 'h2', id: 'card-states', text: 'What each card says' },
        { t: 'dl', items: [
          ['Ready', 'It has what it needs to work.'],
          ['Not configured', 'A credential is missing. That is not an error: the card says what is lost while it stays that way and names the missing variable.'],
          ['Failing', 'The credential is set, but the provider rejected it, did not answer or could not be reached. The fix may be a new key or checking the network.'],
          ['Off', 'A deployment switch that is deliberately set to false.'],
        ] },
        { t: 'p', text: '"Test connection" asks the provider whether the credential works; it never sends an email or a WhatsApp to anyone to find out. A card saying "Ready" means it has its credentials, not that the provider is healthy — that is what the test is for.' },

        { t: 'h2', id: 'value-source', text: 'Where each value comes from' },
        { t: 'p', text: 'Every field says which value is "In effect": from your company, from this panel, from the environment (the server\'s .env file) or the built-in default. What the panel saves beats the file and takes effect without a restart.' },
        { t: 'ul', items: [
          '"Environment only" fields are shown but not editable: the server reads them at startup.',
          'A saved key is never shown again; at most you see its last four characters, so you can tell which one you pasted.',
          '"Back to the environment" removes what the panel saved for that service and hands control back to the server file.',
        ] },

        { t: 'h2', id: 'my-channels', text: 'My channels' },
        { t: 'p', text: 'This is where you set the sender your suppliers and your team see: your Resend key or your SMTP server details, the sender address and your WhatsApp number. From then on your alerts and purchase orders go out under your identity.' },
        { t: 'note', tone: 'info', text: "Account emails — verifying an address, resetting a password, inviting someone — always go out through the installation's transport, even if your company has its own. A company cannot send the message that grants access to an account." },

        { t: 'h2', id: 'turn-on-assistant', text: 'Example: turn the assistant on' },
        { t: 'steps', items: [
          'Under "This installation", find the "Assistant (DeepSeek)" card.',
          'If it says "Not configured", the card names the missing variable: `DEEPSEEK_API_KEY`.',
          'Paste the key and press "Save".',
          'Press "Test connection": it should say "Answers correctly."',
          'Open the [assistant](/docs/asistente/asistente-ia): the "unavailable" notice is gone.',
        ] },
      ],
    },

    'administracion/limites-del-plan': {
      title: 'Usage limits',
      nav: 'Usage limits',
      description:
        'What happens when an account reaches a usage ceiling, how to see how much room is left and how to ask for more.',
      blocks: [
        { t: 'p', text: 'An account can have usage ceilings: products (SKUs), users, warehouses, saved forecasts, API keys and file size. The screens, the forecasting and the assistant are the same; the ceilings only change how much fits. The API (`sk_live_` keys), MCP access and the WhatsApp bot ship with the source code.' },

        { t: 'h2', id: 'see-usage', text: 'See how much room is left' },
        { t: 'p', text: 'In [My account](app:/mi-cuenta), the "Usage and limits" card shows one bar each for Products (SKUs), Users, Warehouses, Saved forecasts and API keys. A bar turns amber from 80% and red at the ceiling. File size and daily API calls have no bar: they are checked when you upload a file and on every call.' },

        { t: 'h2', id: 'at-the-limit', text: 'What happens at a ceiling' },
        { t: 'p', text: 'The action that would cross it does not happen — creating one user or warehouse too many, for example — and the "You reached your plan limit" dialog opens, saying how much you have used of how much. What you already have is untouched: nothing is deleted or switched off for being at the ceiling.' },
        { t: 'p', text: 'When the notice arrives as a message, it says how much you have used of how much and asks you to write to us to raise it.' },
        { t: 'p', text: 'Using the API, MCP or the WhatsApp bot when your account does not have them enabled does not work either: the API answers with an error, and on screen you see a feature-unavailable card with the "Write to us to turn it on" button.' },

        { t: 'h2', id: 'more-room', text: 'Ask for more room' },
        { t: 'p', text: 'To raise the limits, you talk to us:' },
        { t: 'steps', items: [
          'Press "I need more room" in My account, or wait for the dialog to open by itself at a ceiling.',
          'Message us on WhatsApp or by email, or leave your request in the form in the same dialog ("Send request").',
          'Tell us how many products, warehouses and people you handle: that is what we need to raise your limits.',
        ] },
        { t: 'p', text: 'The WhatsApp and email buttons only appear if the installation has its commercial contact configured; the form is always there.' },

        { t: 'h2', id: 'trial', text: 'Trial accounts' },
        { t: 'note', tone: 'info', title: 'A trial account has its own ceilings', text: 'The 24-hour trial account created from the home page has the product with very little room: 30 products, 1 user, 2 warehouses (so you can try transfers), 2 saved forecasts, files up to 5 MB and one training at a time. It does not include the API, MCP or the WhatsApp bot. See [Create your account](/docs/primeros-pasos/crear-tu-cuenta).' },
      ],
    },
  },
}

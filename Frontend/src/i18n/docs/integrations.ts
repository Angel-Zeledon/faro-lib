// Help center — Integraciones: API, MCP, SQL sources, WhatsApp and email.
// Every figure was read from the code on 2026-10-02:
//   backend/auth/api_key_auth.py        120 calls/min per key, daily window per key
//   backend/entitlements/plans.py       500 calls/day free, unlimited paid, 1 key on free
//   backend/api/public_surface.py       which routes a key can and cannot reach
//   backend/api/openapi_public.py       envelope (`data`) and error shape (`error_code`)
//   backend/mcp/catalog.py              the five read-only MCP tools
//   backend/api/v1/mcp.py               POST /mcp, GET /mcp answers 405
//   backend/datasources/service.py      engines, 30 s statement timeout, snapshot
//   backend/config.py                   sql_materialize_max_rows = 500_000
//   backend/notifications/email.py      Resend first, SMTP fallback; platform mail
//   backend/api/v1/messages.py          DM heads-up: WhatsApp, then SMS
import type { DocSectionContent } from '@/i18n/docs/types'
import type { DocPageIdOf } from '@/i18n/docs/tree'

const PLANNING_CURL = `curl https://<tu-dominio>/api/v1/planning \\
  -H "Authorization: Bearer sk_live_..."`

const PLANNING_CURL_EN = `curl https://<your-domain>/api/v1/planning \\
  -H "Authorization: Bearer sk_live_..."`

export const INTEGRATIONS: DocSectionContent<DocPageIdOf<'integraciones'>> = {
  es: {
    'integraciones/api': {
      title: 'La API de StockAI',
      nav: 'API',
      description:
        'Conecta tu ERP, tu punto de venta o tu propio sistema a StockAI con una API key: el mismo trabajo que haces en la app, ' +
        'sin que nadie la abra.',
      blocks: [
        { t: 'p', text: 'La API sirve para que otro sistema haga lo que tú harías a mano: subir el export de ventas, pedir un recálculo, leer el semáforo y registrar la orden de compra. Esta página explica cómo funciona sin entrar en código; la referencia completa, endpoint por endpoint y con ejemplos, está en [la documentación para desarrolladores](/desarrolladores).' },
        { t: 'h2', id: 'keys', text: 'Las llaves de API' },
        { t: 'p', text: 'Una llave se crea en [Automatización](app:/automatizacion), pestaña «API Keys», con «Generar key». Le pones un nombre para reconocerla (por ejemplo «Integración ERP») y eliges qué puede hacer:' },
        { t: 'dl', items: [
          ['Solo leer', 'Actúa como un usuario de **solo lectura**: consulta el semáforo, los pronósticos, tus fuentes de datos y el estado de un entrenamiento, pero no cambia nada.'],
          ['Leer y escribir', 'Actúa como un **analista**: además sube archivos, encola entrenamientos y registra órdenes de compra.'],
        ] },
        { t: 'p', text: 'La llave completa empieza por `sk_live_` y **se muestra una sola vez**, al crearla. StockAI no la guarda en ningún lado donde se pueda volver a leer: si la pierdes, crea otra y revoca la anterior. Revocar es inmediato; lo que estuviera usando esa llave deja de funcionar en la siguiente llamada.' },
        { t: 'note', tone: 'info', text: 'En el plan gratis cabe **una** llave; en el plan completo no hay tope. Ver [Límites del plan gratis](/docs/administracion/limites-del-plan).' },
        { t: 'h2', id: 'calling', text: 'Cómo se hace una llamada' },
        { t: 'p', text: 'Todas las rutas cuelgan de `/api/v1` en el dominio de tu instalación, y la llave va en la cabecera `Authorization`:' },
        { t: 'code', text: PLANNING_CURL },
        { t: 'p', text: 'Esa llamada devuelve `active_session_id`, la actualización con la que se calcula hoy el semáforo. Pídela cada vez en lugar de guardarla: cambia cada vez que se entrena una actualización nueva.' },
        { t: 'p', text: 'Las respuestas correctas traen tu contenido dentro de `data`. Los errores traen un `error_code` estable y un texto en inglés en `detail`. Si tu sistema necesita decidir qué hacer ante un error, que lo decida por `error_code`: el texto puede cambiar de redacción, el código no.' },
        { t: 'h2', id: 'limits', text: 'Límites y consumo' },
        { t: 'ul', items: [
          '**120 llamadas por minuto por llave**, en cualquier plan.',
          '**500 llamadas por día por llave** en el plan gratis; sin tope diario en el plan completo.',
          'Al pasarte recibes un `429` con la cabecera `Retry-After`, que dice cuántos segundos esperar antes de reintentar.',
          'Cada llamada que pasa todos los controles suma uno al consumo del día de esa llave. Las llamadas rechazadas no cuentan. Un administrador ve el consumo por clave y por día en la pantalla [API](app:/api).',
        ] },
        { t: 'h2', id: 'reach', text: 'Qué se puede hacer con una llave, y qué no' },
        { t: 'p', text: 'Con una llave puedes hacer casi todo lo que haces en la app: inventario, proveedores, pronósticos, fuentes de datos, entrenamientos, escenarios, órdenes de compra y recepciones. Hay cosas que **nunca** se alcanzan con una llave, a propósito, porque son de una persona y no de un sistema:' },
        { t: 'ul', items: [
          'Iniciar sesión, usuarios y roles, y las propias llaves de API.',
          'Preferencias personales, mensajes del equipo y conversaciones con el asistente.',
          'Exportar o borrar la cuenta, y la configuración de la instalación.',
          'Las reglas del semáforo, marcar una orden como pagada y cancelar o reabrir una orden.',
        ] },
        { t: 'p', text: 'Si lo intentas, la respuesta es `api_key_route_not_exposed`: «Esto no se puede hacer con una API key (usuarios, claves, configuración o borrado de la cuenta). Hazlo desde la app.» Si una llave de solo lectura intenta escribir, recibes `api_key_scope_insufficient`.' },
        { t: 'note', tone: 'warn', title: 'Antes de que una llave pueda mandar algo fuera', text: 'Mientras ningún administrador de la cuenta tenga el correo verificado, una llave no puede enviar nada fuera de StockAI (por ejemplo, una orden a un proveedor). Verifica el correo de un administrador primero.' },
        { t: 'h2', id: 'console', text: 'Probarla sin escribir código' },
        { t: 'p', text: 'La pantalla [API](app:/api) de la app es a la vez una guía y una consola: muestra «La URL base» de tu instalación, tiene un campo «Tu API key» donde pegas la llave (vive solo en esa pestaña) y, en cada llamada, un botón «Ejecutar» que la corre de verdad contra tu cuenta y te muestra la respuesta, y un «Ejemplo» con el mismo llamado escrito como `curl`.' },
        { t: 'shot', key: 'api', alt: 'La pantalla API de StockAI con la URL base, el campo para la llave y la lista de llamadas' },
        { t: 'note', tone: 'warn', text: 'No hay ambiente de pruebas. «Ejecutar» actúa sobre tu cuenta real: subir un archivo reemplaza el de esa fuente y registrar una orden la hace aparecer en Pedidos. Antes de cada escritura la consola te pide confirmación y nombra la consecuencia.' },
        { t: 'h2', id: 'typical-loop', text: 'El ciclo típico de una integración' },
        { t: 'steps', items: [
          'Crea una llave «Leer y escribir».',
          'Pide `GET /planning` para saber qué actualización está activa.',
          'Sube el export de ventas más reciente sobre la fuente que ya usas.',
          'Encola el recálculo (o deja que lo haga una [programación](/docs/administracion/automatizacion)) y consulta su estado hasta que diga `COMPLETED`.',
          'Lee el semáforo y arma la orden con la señal y la cantidad recomendada.',
          'Cuando la orden salga de verdad, regístrala en StockAI. Sin ese registro la orden no existe para StockAI y nunca se aprende el [tiempo de entrega real](/docs/conceptos/tiempo-de-entrega-aprendido) de tus proveedores.',
        ] },
        { t: 'p', text: 'Si lo que quieres no es que un sistema corra solo sino preguntarle a un asistente de IA qué comprar, mira [MCP](/docs/integraciones/mcp).' },
      ],
    },

    'integraciones/mcp': {
      title: 'Conecta tu asistente de IA (MCP)',
      nav: 'MCP',
      description:
        'StockAI habla el Model Context Protocol: un asistente de IA como Claude puede leer tu semáforo y decirte qué comprar hoy. ' +
        'Solo lee; nunca cambia nada.',
      blocks: [
        { t: 'p', text: 'MCP es el protocolo con el que los asistentes de IA se conectan a herramientas externas. StockAI ofrece un servidor MCP en tu propia instalación, para que le preguntes a tu asistente «¿qué compro hoy?» y la respuesta salga de tus datos, no de suposiciones.' },
        { t: 'h2', id: 'endpoint', text: 'Dónde está y cómo se entra' },
        { t: 'ul', items: [
          'La dirección es `/api/v1/mcp` en el dominio de tu instalación; la pantalla [API](app:/api) la muestra completa en «Conecta tu asistente (MCP)».',
          'Se entra con una llave de API normal, en la cabecera `Authorization: Bearer sk_live_...`. Una llave «Solo leer» alcanza para todo.',
          'Comparte el mismo límite que la API: 120 llamadas por minuto por llave, y el tope diario de tu plan.',
          'Las preguntas se mandan con `POST`. Un `GET` a la misma dirección responde `405`: el servidor no abre un canal de eventos, porque todo cabe en la respuesta de cada pregunta.',
        ] },
        { t: 'h2', id: 'tools', text: 'Las cinco herramientas' },
        { t: 'dl', items: [
          ['`get_planning_context`', 'Qué actualización está activa, con qué detalle (día, semana o mes) y sobre cuántos períodos se calculó todo lo demás. Es el punto de partida.'],
          ['`get_morning_briefing`', 'El trabajo del día: los riesgos que vale la pena atender, el motivo de cada recomendación, el proveedor sugerido y el valor estimado. Es la herramienta para «¿qué compro hoy?».'],
          ['`get_inventory_status`', 'El semáforo por producto (`PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK`, `SIN_DATOS`) con la cantidad a pedir y de dónde salió cada supuesto. Se puede filtrar por señal o por proveedor.'],
          ['`list_data_sources`', 'Tus fuentes de ventas y cuándo se actualizó cada una, para saber si los números se basan en datos frescos.'],
          ['`get_training_status`', 'Si una actualización terminó de entrenar: en cola, calculando, completada o fallida.'],
        ] },
        { t: 'p', text: 'Las herramientas le advierten al asistente que `SIN_DATOS` significa «no sabemos», no «está bien», y que un valor marcado como supuesto de StockAI no es un dato tuyo. Así el asistente no te presenta como tranquilo algo que nadie midió.' },
        { t: 'h2', id: 'connect', text: 'Conectar tu cliente' },
        { t: 'steps', items: [
          'Crea una llave «Solo leer» en [Automatización](app:/automatizacion).',
          'Si tu cliente de IA acepta una URL, dale la dirección MCP de tu instalación con la cabecera `Authorization: Bearer` y tu llave.',
          'Si es un cliente de escritorio que habla por la consola (stdin/stdout), como Claude Desktop, usa el archivo `mcp_server/stockai_mcp.py` del repositorio: es un solo archivo de Python sin dependencias. Define `STOCKAI_URL` (la dirección de tu instalación, sin barra al final) y `STOCKAI_API_KEY` (tu llave), y apunta tu cliente a ese archivo.',
          'Pregúntale «¿qué compro hoy?». La respuesta sale del mismo trabajo del día que ves en el [Panel de compras](/docs/uso-diario/panel-de-compras).',
        ] },
        { t: 'h2', id: 'read-only', text: 'Por qué no escribe nada' },
        { t: 'p', text: 'Por MCP no se sube ningún archivo, no se entrena, no se registra ninguna orden y no se edita stock. No es un límite del rol: aunque uses una llave «Leer y escribir», el asistente solo lee. Un asistente de IA no puede mostrarte la tarjeta de confirmación ni ofrecerte deshacer, que es lo que StockAI exige antes de un cambio en tus datos. Esas acciones se quedan en la app y en la [API](/docs/integraciones/api), donde las dispara una persona que ve lo que pasó.' },
        { t: 'note', tone: 'warn', text: 'Una llave conectada a un asistente lee tu stock, tus costos y los nombres de tus proveedores, y el proveedor de ese asistente los recibe. Trátala como cualquier otra credencial y revócala en Automatización cuando deje de usarse.' },
      ],
    },

    'integraciones/fuentes-sql': {
      title: 'Fuentes de datos SQL',
      nav: 'Fuentes SQL',
      description:
        'Si tus ventas viven en una base de datos, StockAI puede consultarla directamente desde Fuentes de datos y guardar el resultado como un archivo de ventas.',
      blocks: [
        { t: 'p', text: 'Además de subir un CSV o un Excel, puedes conectar StockAI a la base de datos de tu sistema, escribir la consulta que trae tus ventas y usar el resultado para entrenar. Se hace en **Fuentes de datos** ([/archivos](app:/archivos)), la segunda pestaña de **Mis ventas**.' },
        { t: 'h2', id: 'engines', text: 'Bases de datos compatibles' },
        { t: 'table', head: ['Motor', 'Puerto por defecto'], rows: [
          ['PostgreSQL', '5432'],
          ['MySQL', '3306'],
          ['SQL Server', '1433'],
          ['Oracle', '1521'],
        ] },
        { t: 'h2', id: 'connect', text: 'Crear la conexión' },
        { t: 'steps', items: [
          'En Fuentes de datos, pulsa «Nueva fuente de datos» y elige «Base de datos SQL».',
          'Escribe un nombre, elige el «Motor» y llena servidor, puerto, base de datos, usuario y contraseña.',
          'Pulsa «Crear conexión» y luego «Probar conexión»: debe decir «Conexión exitosa». Mientras no la pruebes, la fuente no se puede consultar.',
        ] },
        { t: 'note', tone: 'info', text: 'Usa un usuario de base de datos que solo pueda leer. StockAI ejecuta únicamente las consultas que tú escribes y no crea, cambia ni borra nada por su cuenta, pero un usuario de solo lectura garantiza que una consulta mal escrita no pueda tocar tu base.' },
        { t: 'h2', id: 'query', text: 'Escribir y probar la consulta' },
        { t: 'p', text: 'En la pestaña «Editor de consultas» escribes el `SELECT` que trae tus ventas. Las columnas que traiga son las que tendrá la fuente: como mínimo una fecha, un producto y la cantidad vendida, igual que un archivo. «Ejecutar» muestra una vista previa de hasta 500 filas; «Guardar consulta» la deja guardada en la fuente. Cada consulta tiene 30 segundos para responder.' },
        { t: 'p', text: '«Descargar Excel» baja el resultado completo como `.xlsx`, por si quieres revisarlo antes de usarlo.' },
        { t: 'h2', id: 'materialize', text: 'Usar el resultado para entrenar' },
        { t: 'p', text: '«Usar como dataset» ejecuta la consulta completa y guarda el resultado como un **archivo nuevo**, que aparece en Mis ventas listo para entrenar, igual que uno que hubieras subido. Desde ahí sigues el mismo camino de [subir tus ventas](/docs/primeros-pasos/subir-tus-ventas): confirmar columnas y dejar que el sistema aprenda.' },
        { t: 'ul', items: [
          'El resultado puede tener hasta **500.000 filas**. Si la consulta devuelve más, StockAI no la corta: la rechaza con «La consulta devolvió más de 500000 filas, el límite para guardarla. Acótala con WHERE o LIMIT.»',
          'Si la consulta no devuelve nada: «La consulta no devolvió filas — no hay nada que guardar.»',
          'Si la base rechaza la consulta, el mensaje empieza con «La consulta falló:» seguido del error tal como lo dio tu base de datos.',
        ] },
        { t: 'h2', id: 'no-sync', text: 'Lo que no hace: sincronizar sola' },
        { t: 'p', text: 'Guardar el resultado es una **foto**, no una conexión viva. Es a propósito: un pronóstico tiene que poder repetirse con los mismos datos con los que se entrenó.' },
        { t: 'note', tone: 'warn', title: 'Los recálculos programados no vuelven a correr tu consulta', text: 'Una [programación](/docs/administracion/automatizacion) reentrena sobre los datos que ya tiene la actualización; no vuelve a consultar tu base. Para usar ventas nuevas, vuelve a pulsar «Usar como dataset» y entrena con ese archivo, o empuja el export por la [API](/docs/integraciones/api).' },
      ],
    },

    'integraciones/whatsapp': {
      title: 'WhatsApp',
      description:
        'Qué te manda StockAI por WhatsApp, qué les manda a tus proveedores y qué hace falta configurar para que funcione.',
      blocks: [
        { t: 'p', text: 'StockAI usa WhatsApp para cinco cosas. Todas salen por una cuenta de Twilio: la de tu instalación o, si la configuraste, la de tu propia empresa.' },
        { t: 'h2', id: 'what-is-sent', text: 'Qué se manda por WhatsApp' },
        { t: 'dl', items: [
          ['Órdenes a proveedores', '«Enviar pedido» en [Pedidos](/docs/uso-diario/pedidos) manda la orden a cada proveedor que tenga WhatsApp (o email) en su ficha. Los que no tienen ninguno de los dos se omiten, y la confirmación te los nombra antes de enviar.'],
          ['La orden a tu propio WhatsApp', '«Enviarme por WhatsApp» te manda el texto de la orden a ti, para que se lo reenvíes al proveedor. «Abrir en WhatsApp» y «Copiar mensaje» hacen lo mismo sin pasar por StockAI.'],
          ['La alerta diaria', 'Cada mañana, si hay productos en Pedir YA o Pedir pronto, un resumen llega a quien tenga su número vinculado. Ver [Alertas diarias](/docs/uso-diario/alertas-diarias).'],
          ['Avisos de mensajes del equipo', 'Si alguien te escribe en [Mensajes](/docs/uso-diario/mensajes) mientras no estás en StockAI, te llega un aviso por WhatsApp, o por SMS si WhatsApp no está disponible. Viene apagado.'],
          ['El asistente', 'Puedes preguntarle por tus compras por WhatsApp. Ver [El asistente por WhatsApp](/docs/asistente/asistente-por-whatsapp).'],
        ] },
        { t: 'h2', id: 'link-number', text: 'Vincular tu número' },
        { t: 'steps', items: [
          'Abre [Mi cuenta](app:/mi-cuenta) y busca «Vincular WhatsApp».',
          'Escribe tu número con el código de país, por ejemplo `+506…`.',
          'Escribe el código de 6 dígitos que te llega por WhatsApp. Vence en 15 minutos.',
          'Si quieres el aviso de mensajes del equipo, enciende «Recibir aviso cuando te escriban» en «Mensajes del equipo».',
        ] },
        { t: 'p', text: 'Sin número vinculado no recibes alertas por WhatsApp ni puedes usar el asistente por WhatsApp. Dejar el campo vacío es la forma de no recibir alertas por ese canal.' },
        { t: 'h2', id: 'setup', text: 'Qué tiene que estar configurado' },
        { t: 'p', text: 'WhatsApp funciona solo si la instalación tiene una cuenta de Twilio configurada. Quien administra el servidor lo ve y lo configura en [Instalación](/docs/administracion/instalacion), pestaña «Esta instalación». Sin Twilio, StockAI no manda nada por WhatsApp y te lo dice en lugar de fingir que lo envió.' },
        { t: 'p', text: 'Si tu empresa quiere que sus proveedores y su equipo reciban los mensajes desde un número propio, un administrador lo configura en Instalación → «Mis canales». Lo que dejes vacío ahí usa el de la instalación.' },
        { t: 'note', tone: 'info', text: 'Para mandar órdenes a tus proveedores tu correo tiene que estar verificado. Ver [Cuenta y acceso](/docs/solucion-de-problemas/cuenta-y-acceso).' },
      ],
    },

    'integraciones/correo': {
      title: 'Correo electrónico',
      nav: 'Correo',
      description:
        'Qué correos manda StockAI, desde qué remitente salen y cómo hacer que tus órdenes y alertas lleguen con el nombre de tu empresa.',
      blocks: [
        { t: 'h2', id: 'emails', text: 'Los correos que manda StockAI' },
        { t: 'table', head: ['Correo', 'A quién', 'Remitente'], rows: [
          ['Verificar tu correo, activar una cuenta invitada', 'La persona que se registra o a quien invitan', 'Instalación'],
          ['Código para recuperar o cambiar la contraseña', 'La persona que lo pide', 'Instalación'],
          ['Orden de compra', 'Cada proveedor con email en su ficha', 'Tu empresa, si lo configuraste'],
          ['Alerta diaria de productos en riesgo', 'Administradores y analistas', 'Tu empresa, si lo configuraste'],
          ['Proveedores tardando más de lo habitual', 'Administradores y analistas', 'Tu empresa, si lo configuraste'],
          ['Recordatorio de datos desactualizados', 'Administradores y analistas', 'Tu empresa, si lo configuraste'],
          ['Resumen del mes', 'Administradores y analistas, el día 1', 'Tu empresa, si lo configuraste'],
        ] },
        { t: 'p', text: 'Los correos de la cuenta — verificación, contraseña e invitaciones — **siempre** salen con el remitente de la instalación, aunque tu empresa tenga el suyo. Es a propósito: el mensaje que da acceso a una cuenta habla en nombre de la instalación, y una empresa no debe poder enviar el que restablece una contraseña.' },
        { t: 'h2', id: 'transport', text: 'Cómo sale el correo' },
        { t: 'p', text: 'StockAI manda el correo con **Resend** cuando hay una llave de Resend configurada, y si no con **SMTP** (servidor, puerto, usuario y contraseña). Basta con uno de los dos. Quien administra el servidor configura el de la instalación en [Instalación](/docs/administracion/instalacion).' },
        { t: 'p', text: 'Si no hay ningún transporte configurado, el correo no sale y StockAI lo registra como no enviado: nunca dice que mandó algo que nadie recibió.' },
        { t: 'h2', id: 'own-sender', text: 'Mandar desde el dominio de tu empresa' },
        { t: 'steps', items: [
          'Como administrador de tu empresa, abre [Instalación](app:/instalacion) y entra a la pestaña «Mis canales».',
          'En la tarjeta de correo, pega tu llave de Resend, o llena los datos de tu servidor SMTP.',
          'Escribe el remitente que quieres que vean tus proveedores.',
          'Guarda y pulsa «Probar conexión». La prueba pregunta al proveedor si la credencial sirve; no manda ningún correo a nadie.',
        ] },
        { t: 'p', text: 'Desde ese momento tus órdenes de compra y tus alertas salen con tu identidad. Una credencial guardada no se vuelve a mostrar: el panel enseña como mucho sus últimos cuatro caracteres.' },
        { t: 'shot', key: 'instalacion', alt: 'La pantalla Instalación con las tarjetas de cada servicio y su estado' },
        { t: 'h2', id: 'delivery-log', text: 'Saber si un correo salió' },
        { t: 'p', text: 'Cada alerta enviada, y cada una que falló, queda registrada. Las ves en [Qué ha pasado](/docs/analisis/actividad), junto con las órdenes enviadas y las que **no** se pudieron enviar. Al enviar una orden, la pantalla también te dice a qué proveedores llegó y a cuáles se omitió.' },
        { t: 'note', tone: 'info', text: 'Las cuentas de prueba usan una dirección inventada y nunca reciben correo.' },
      ],
    },
  },

  en: {
    'integraciones/api': {
      title: 'The StockAI API',
      nav: 'API',
      description:
        'Connect your ERP, your point of sale or your own system to StockAI with an API key: the same work you do in the app, ' +
        'without anyone opening it.',
      blocks: [
        { t: 'p', text: 'The API lets another system do what you would do by hand: upload the sales export, ask for a recalculation, read the stock signal and log the purchase order. This page explains how it works without code; the full reference, endpoint by endpoint with examples, is in [the developer documentation](/desarrolladores).' },
        { t: 'h2', id: 'keys', text: 'API keys' },
        { t: 'p', text: 'You create a key under [Automation](app:/automatizacion), on the "API Keys" tab, with "Generate key". Give it a name you will recognise (for example "ERP integration") and choose what it can do:' },
        { t: 'dl', items: [
          ['Read only', 'Acts as a **read-only** user: it reads the stock signal, the forecasts, your data sources and a training run\'s status, and changes nothing.'],
          ['Read and write', 'Acts as an **analyst**: it can also upload files, queue trainings and log purchase orders.'],
        ] },
        { t: 'p', text: 'The full key starts with `sk_live_` and **is shown only once**, when you create it. StockAI keeps it nowhere it can be read back: if you lose it, create another and revoke the old one. Revoking is immediate; whatever was using that key stops working on its next call.' },
        { t: 'note', tone: 'info', text: 'The free plan holds **one** key; the paid plan has no cap. See [Free plan limits](/docs/administracion/limites-del-plan).' },
        { t: 'h2', id: 'calling', text: 'Making a call' },
        { t: 'p', text: 'Every route hangs off `/api/v1` on your installation\'s domain, and the key goes in the `Authorization` header:' },
        { t: 'code', text: PLANNING_CURL_EN },
        { t: 'p', text: 'That call returns `active_session_id`, the run the stock signal is computed from today. Ask for it every time rather than storing it: it changes whenever a new run trains.' },
        { t: 'p', text: 'Successful responses carry your content inside `data`. Errors carry a stable `error_code` and an English sentence in `detail`. If your system has to decide what to do about an error, let it decide on `error_code`: the wording can change, the code does not.' },
        { t: 'h2', id: 'limits', text: 'Limits and usage' },
        { t: 'ul', items: [
          '**120 calls per minute per key**, on every plan.',
          '**500 calls per day per key** on the free plan; no daily cap on the paid plan.',
          'Past a limit you get a `429` with a `Retry-After` header saying how many seconds to wait before retrying.',
          'Every call that passes all checks adds one to that key\'s daily usage. Refused calls do not count. An administrator sees usage by key and by day on the [API](app:/api) screen.',
        ] },
        { t: 'h2', id: 'reach', text: 'What a key can and cannot do' },
        { t: 'p', text: 'A key can do almost everything you do in the app: inventory, suppliers, forecasts, data sources, trainings, scenarios, purchase orders and receptions. Some things are **never** reachable with a key, on purpose, because they belong to a person and not to a system:' },
        { t: 'ul', items: [
          'Signing in, users and roles, and the API keys themselves.',
          'Personal preferences, team messages and conversations with the assistant.',
          'Exporting or deleting the account, and the installation\'s configuration.',
          'The stock signal rules, marking an order as paid, and cancelling or reopening an order.',
        ] },
        { t: 'p', text: 'Try one and the answer is `api_key_route_not_exposed`: "This cannot be done with an API key (users, keys, configuration or account deletion). Do it in the app." A read-only key that tries to write gets `api_key_scope_insufficient`.' },
        { t: 'note', tone: 'warn', title: 'Before a key can send anything out', text: 'Until at least one administrator on the account has a verified email, a key cannot send anything outside StockAI (an order to a supplier, for example). Verify an administrator\'s email first.' },
        { t: 'h2', id: 'console', text: 'Trying it without writing code' },
        { t: 'p', text: 'The app\'s [API](app:/api) screen is both a guide and a console: it shows your installation\'s "The base URL", has a "Your API key" field where you paste the key (it lives only in that tab) and, on each call, a "Run" button that really runs it against your account and shows the response, plus an "Example" with the same call written as `curl`.' },
        { t: 'shot', key: 'api', alt: 'The StockAI API screen with the base URL, the key field and the list of calls' },
        { t: 'note', tone: 'warn', text: 'There is no sandbox. "Run" acts on your real account: uploading a file replaces that source\'s file, and logging an order makes it appear under Orders. Before every write the console asks you to confirm and names the consequence.' },
        { t: 'h2', id: 'typical-loop', text: 'An integration\'s typical loop' },
        { t: 'steps', items: [
          'Create a "Read and write" key.',
          'Call `GET /planning` to learn which run is active.',
          'Upload the latest sales export onto the source you already use.',
          'Queue the recalculation (or let a [schedule](/docs/administracion/automatizacion) do it) and poll its status until it reads `COMPLETED`.',
          'Read the stock signal and build the order from the signal and the recommended quantity.',
          'When the order really goes out, log it in StockAI. Without that record the order does not exist for StockAI, and your suppliers\' [real lead time](/docs/conceptos/tiempo-de-entrega-aprendido) is never learned.',
        ] },
        { t: 'p', text: 'If what you want is not a system running on its own but asking an AI assistant what to buy, see [MCP](/docs/integraciones/mcp).' },
      ],
    },

    'integraciones/mcp': {
      title: 'Connect your AI assistant (MCP)',
      nav: 'MCP',
      description:
        'StockAI speaks the Model Context Protocol: an AI assistant such as Claude can read your stock signal and tell you what to buy today. ' +
        'It only reads; it never changes anything.',
      blocks: [
        { t: 'p', text: 'MCP is the protocol AI assistants use to connect to outside tools. StockAI runs an MCP server on your own installation, so you can ask your assistant "what should I buy today?" and get an answer from your data rather than from guesses.' },
        { t: 'h2', id: 'endpoint', text: 'Where it is and how to get in' },
        { t: 'ul', items: [
          'The address is `/api/v1/mcp` on your installation\'s domain; the [API](app:/api) screen shows it in full under "Connect your assistant (MCP)".',
          'You get in with an ordinary API key in the `Authorization: Bearer sk_live_...` header. A "Read only" key is enough for everything.',
          'It shares the API\'s limits: 120 calls per minute per key, and your plan\'s daily ceiling.',
          'Questions are sent with `POST`. A `GET` to the same address answers `405`: the server opens no event stream, because every answer fits in the response to its question.',
        ] },
        { t: 'h2', id: 'tools', text: 'The five tools' },
        { t: 'dl', items: [
          ['`get_planning_context`', 'Which run is active, at what detail (day, week or month) and over how many periods everything else was computed. The starting point.'],
          ['`get_morning_briefing`', 'The day\'s work: the risks worth acting on, the reason behind each recommendation, the suggested supplier and the estimated value. The tool for "what should I buy today?".'],
          ['`get_inventory_status`', 'The stock signal per product (`PEDIR_YA`, `PEDIR_PRONTO`, `OK`, `SOBRESTOCK`, `SIN_DATOS`) with the quantity to order and where each assumption came from. Filterable by signal or supplier.'],
          ['`list_data_sources`', 'Your sales sources and when each was last updated, to tell whether the numbers rest on fresh data.'],
          ['`get_training_status`', 'Whether a run has finished training: queued, running, completed or failed.'],
        ] },
        { t: 'p', text: 'The tools warn the assistant that `SIN_DATOS` means "unknown", not "fine", and that a value marked as a StockAI assumption is not your own figure. That way the assistant never reports as safe something nobody measured.' },
        { t: 'h2', id: 'connect', text: 'Connecting your client' },
        { t: 'steps', items: [
          'Create a "Read only" key under [Automation](app:/automatizacion).',
          'If your AI client accepts a URL, give it your installation\'s MCP address with the `Authorization: Bearer` header and your key.',
          'If it is a desktop client that talks over the console (stdin/stdout), such as Claude Desktop, use the repository\'s `mcp_server/stockai_mcp.py`: a single Python file with no dependencies. Set `STOCKAI_URL` (your installation\'s address, no trailing slash) and `STOCKAI_API_KEY` (your key), and point your client at that file.',
          'Ask it "what should I buy today?". The answer comes from the same day\'s work you see on the [Purchasing panel](/docs/uso-diario/panel-de-compras).',
        ] },
        { t: 'h2', id: 'read-only', text: 'Why nothing writes' },
        { t: 'p', text: 'Over MCP no file is uploaded, nothing is trained, no order is logged and no stock is edited. It is not a role limit: even with a "Read and write" key, the assistant only reads. An AI assistant cannot show you the confirmation card or offer an undo, which StockAI requires before changing your data. Those actions stay in the app and the [API](/docs/integraciones/api), where a person who sees the result triggers them.' },
        { t: 'note', tone: 'warn', text: 'A key connected to an assistant reads your stock, your costs and your suppliers\' names, and that assistant\'s provider receives them. Treat it like any other credential and revoke it under Automation once it is no longer used.' },
      ],
    },

    'integraciones/fuentes-sql': {
      title: 'SQL data sources',
      nav: 'SQL sources',
      description:
        'If your sales live in a database, StockAI can query it directly from Data sources and save the result as a sales file.',
      blocks: [
        { t: 'p', text: 'Besides uploading a CSV or an Excel file, you can connect StockAI to your system\'s database, write the query that brings your sales, and train on the result. You do it under **Data sources** ([/archivos](app:/archivos)), the second tab of **My sales**.' },
        { t: 'h2', id: 'engines', text: 'Supported databases' },
        { t: 'table', head: ['Engine', 'Default port'], rows: [
          ['PostgreSQL', '5432'],
          ['MySQL', '3306'],
          ['SQL Server', '1433'],
          ['Oracle', '1521'],
        ] },
        { t: 'h2', id: 'connect', text: 'Creating the connection' },
        { t: 'steps', items: [
          'In Data sources, press "New Data Source" and choose "SQL Database".',
          'Type a name, pick the "Engine" and fill in host, port, database, username and password.',
          'Press "Create Connection" and then "Test Connection": it should say "Connection successful". Until you test it, the source cannot be queried.',
        ] },
        { t: 'note', tone: 'info', text: 'Use a database user that can only read. StockAI runs only the queries you write and creates, changes or deletes nothing on its own, but a read-only user guarantees that a badly written query cannot touch your database.' },
        { t: 'h2', id: 'query', text: 'Writing and testing the query' },
        { t: 'p', text: 'On the "Query Editor" tab you write the `SELECT` that brings your sales. The columns it returns are the columns the source will have: at least a date, a product and the quantity sold, just like a file. "Run" shows a preview of up to 500 rows; "Save Query" keeps it on the source. Each query has 30 seconds to answer.' },
        { t: 'p', text: '"Download Excel" downloads the full result as `.xlsx`, in case you want to check it before using it.' },
        { t: 'h2', id: 'materialize', text: 'Training on the result' },
        { t: 'p', text: '"Use as dataset" runs the full query and saves the result as a **new file**, which appears under My sales ready to train, just like one you uploaded. From there you follow the same path as [uploading your sales](/docs/primeros-pasos/subir-tus-ventas): confirm the columns and let the system learn.' },
        { t: 'ul', items: [
          'The result can have up to **500,000 rows**. If the query returns more, StockAI does not cut it: it refuses with "The query returned more than 500000 rows, the save limit. Narrow it with WHERE or LIMIT."',
          'If the query returns nothing: "The query returned no rows — there is nothing to save."',
          'If the database rejects the query, the message starts with "The query failed:" followed by the error exactly as your database gave it.',
        ] },
        { t: 'h2', id: 'no-sync', text: 'What it does not do: sync on its own' },
        { t: 'p', text: 'Saving the result is a **snapshot**, not a live connection. That is deliberate: a forecast has to be reproducible against the data it trained on.' },
        { t: 'note', tone: 'warn', title: 'Scheduled recalculations do not re-run your query', text: 'A [schedule](/docs/administracion/automatizacion) retrains on the data the run already has; it does not query your database again. To use new sales, press "Use as dataset" again and train on that file, or push the export through the [API](/docs/integraciones/api).' },
      ],
    },

    'integraciones/whatsapp': {
      title: 'WhatsApp',
      description:
        'What StockAI sends you over WhatsApp, what it sends your suppliers, and what has to be set up for it to work.',
      blocks: [
        { t: 'p', text: 'StockAI uses WhatsApp for five things. All of them go out through a Twilio account: your installation\'s or, if you set one up, your own company\'s.' },
        { t: 'h2', id: 'what-is-sent', text: 'What goes out over WhatsApp' },
        { t: 'dl', items: [
          ['Orders to suppliers', '"Send order" under [Orders](/docs/uso-diario/pedidos) sends the order to every supplier with WhatsApp (or email) on their card. Those with neither are skipped, and the confirmation names them before sending.'],
          ['The order to your own WhatsApp', '"Send to my WhatsApp" sends you the order text so you can forward it to the supplier. "Open in WhatsApp" and "Copy message" do the same without going through StockAI.'],
          ['The daily alert', 'Every morning, if any products are in Order NOW or Order soon, a summary reaches whoever has a linked number. See [Daily alerts](/docs/uso-diario/alertas-diarias).'],
          ['Team message heads-ups', 'If someone writes to you under [Messages](/docs/uso-diario/mensajes) while you are not in StockAI, you get a WhatsApp heads-up, or an SMS if WhatsApp is not available. Off by default.'],
          ['The assistant', 'You can ask about your purchasing over WhatsApp. See [The assistant on WhatsApp](/docs/asistente/asistente-por-whatsapp).'],
        ] },
        { t: 'h2', id: 'link-number', text: 'Linking your number' },
        { t: 'steps', items: [
          'Open [My account](app:/mi-cuenta) and find "Link WhatsApp".',
          'Type your number with the country code, for example `+506…`.',
          'Enter the 6-digit code that arrives on WhatsApp. It expires in 15 minutes.',
          'If you want team-message heads-ups, switch on "Get a heads-up when someone writes to you" under "Team messages".',
        ] },
        { t: 'p', text: 'Without a linked number you get no WhatsApp alerts and cannot use the assistant over WhatsApp. Leaving the field empty is how you opt out of alerts on that channel.' },
        { t: 'h2', id: 'setup', text: 'What has to be configured' },
        { t: 'p', text: 'WhatsApp only works if the installation has a Twilio account configured. Whoever administers the server sees and sets it under [Installation](/docs/administracion/instalacion), on the "This installation" tab. Without Twilio, StockAI sends nothing over WhatsApp and says so instead of pretending it sent.' },
        { t: 'p', text: 'If your company wants its suppliers and team to receive messages from its own number, an administrator sets it under Installation → "My channels". Anything left empty there uses the installation\'s.' },
        { t: 'note', tone: 'info', text: 'Sending orders to your suppliers requires a verified email. See [Account and access](/docs/solucion-de-problemas/cuenta-y-acceso).' },
      ],
    },

    'integraciones/correo': {
      title: 'Email',
      nav: 'Email',
      description:
        'Which emails StockAI sends, which sender they come from, and how to make your orders and alerts arrive under your company\'s name.',
      blocks: [
        { t: 'h2', id: 'emails', text: 'The emails StockAI sends' },
        { t: 'table', head: ['Email', 'To whom', 'Sender'], rows: [
          ['Verify your email, activate an invited account', 'The person signing up or being invited', 'Installation'],
          ['Code to recover or change the password', 'The person asking for it', 'Installation'],
          ['Purchase order', 'Every supplier with an email on their card', 'Your company, if configured'],
          ['Daily alert of products at risk', 'Administrators and analysts', 'Your company, if configured'],
          ['Suppliers taking longer than usual', 'Administrators and analysts', 'Your company, if configured'],
          ['Stale data reminder', 'Administrators and analysts', 'Your company, if configured'],
          ['Monthly recap', 'Administrators and analysts, on the 1st', 'Your company, if configured'],
        ] },
        { t: 'p', text: 'Account emails — verification, password and invitations — **always** go out with the installation\'s sender, even when your company has its own. That is deliberate: the message that grants access to an account speaks for the installation, and a company must not be able to send the one that resets a password.' },
        { t: 'h2', id: 'transport', text: 'How email goes out' },
        { t: 'p', text: 'StockAI sends through **Resend** when a Resend key is configured, and otherwise through **SMTP** (host, port, username and password). Either one is enough. Whoever administers the server sets the installation\'s under [Installation](/docs/administracion/instalacion).' },
        { t: 'p', text: 'With no transport configured, the email does not go out and StockAI records it as not sent: it never claims to have sent something nobody received.' },
        { t: 'h2', id: 'own-sender', text: 'Sending from your company\'s domain' },
        { t: 'steps', items: [
          'As your company\'s administrator, open [Installation](app:/instalacion) and go to the "My channels" tab.',
          'On the email card, paste your Resend key, or fill in your SMTP server\'s details.',
          'Type the sender you want your suppliers to see.',
          'Save and press "Test connection". The test asks the provider whether the credential works; it emails nobody.',
        ] },
        { t: 'p', text: 'From then on your purchase orders and alerts go out under your identity. A saved credential is never shown again: the panel shows at most its last four characters.' },
        { t: 'shot', key: 'instalacion', alt: 'The Installation screen with a card per service and its state' },
        { t: 'h2', id: 'delivery-log', text: 'Knowing whether an email went out' },
        { t: 'p', text: 'Every alert sent, and every one that failed, is recorded. You see them under [What happened](/docs/analisis/actividad), alongside orders sent and orders that could **not** be sent. When you send an order, the screen also tells you which suppliers it reached and which were skipped.' },
        { t: 'note', tone: 'info', text: 'Trial accounts use a made-up address and never receive email.' },
      ],
    },
  },
}

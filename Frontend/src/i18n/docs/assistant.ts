// Help center — AI assistant section. Verified against backend/assistant/
// (core.py, context.py, channels.py, grounding), backend/api/v1/chats.py,
// backend/api/v1/whatsapp.py, backend/whatsapp/identity.py and agent.py,
// docs/data-that-leaves.md and Frontend/src/app/asistente/.
import type { DocSectionContent } from '@/i18n/docs/types'
import type { DocPageIdOf } from '@/i18n/docs/tree'

export const ASSISTANT: DocSectionContent<DocPageIdOf<'asistente'>> = {
  es: {
    'asistente/asistente-ia': {
      title: 'El asistente IA',
      nav: 'Asistente IA',
      description:
        'Un chat que responde con los datos de tu propia cuenta: qué está en riesgo hoy, qué pedidos vienen atrasados, cómo cumple un proveedor. Lee, nunca cambia nada.',
      blocks: [
        { t: 'p', text: 'El asistente vive en [Asistente IA](app:/asistente), en el grupo de Análisis del menú (en el celular, en la pestaña «Asistente»). La pantalla se titula «Analista IA». No es un buscador de respuestas genéricas: antes de cada pregunta lee tu cuenta igual que lo hacen tus pantallas y responde sobre tus productos, tus pedidos y tus proveedores.' },
        { t: 'shot', key: 'asistente', alt: 'La pantalla del asistente IA con la lista de conversaciones a la izquierda y una respuesta sobre los productos en riesgo' },

        { t: 'h2', id: 'what-it-knows', text: 'Qué sabe de tu cuenta' },
        { t: 'p', text: 'Con cada mensaje el asistente arma un resumen de tu cuenta en vivo, con las partes que más tienen que ver con tu pregunta primero:' },
        { t: 'ul', items: [
          'Quién eres (tu nombre, tu rol y tu empresa) y qué tan reciente es tu pronóstico.',
          'Los productos en rojo y en ámbar de hoy, con la cantidad sugerida, la cobertura, el proveedor y su tiempo de entrega.',
          'El sobrestock y el dinero parado en él.',
          'Tus órdenes de compra abiertas y las atrasadas.',
          'El tiempo de entrega real y la confiabilidad de tus proveedores.',
          'La tendencia de la demanda, los picos que vienen y lo que hiciste últimamente en StockAI.',
        ] },
        { t: 'p', text: 'Si necesita algo más específico — un producto, su pronóstico, un proveedor, una orden de compra, la lista de tus productos o tu actividad — lo consulta con herramientas de **solo lectura**, igual que las pantallas.' },
        { t: 'p', text: 'Los productos en rojo y ámbar del resumen salen del mismo semáforo que ves en [Panel de compras](app:/compras) y en [Inventario](app:/inventario): la actualización activa, al ritmo de cálculo de tu cuenta. Si quieres entender ese semáforo, lee [El semáforo](/docs/conceptos/semaforo).' },

        { t: 'h2', id: 'checked-numbers', text: 'Cifras comprobadas' },
        { t: 'p', text: 'Cada número de una respuesta tiene que aparecer en tus datos, en lo que devolvió una herramienta o en tu propio mensaje. StockAI lo comprueba después de que el modelo responde. Si alguna cifra no cuadra, le pide una corrección; si aun así queda alguna sin respaldo, no la esconde: la respuesta termina con un aviso del tipo «No pude verificar estas cifras contra tus datos» y la lista de esos números.' },
        { t: 'p', text: 'Cada respuesta lleva una etiqueta que dice de dónde salió:' },
        { t: 'dl', items: [
          ['Tus datos', 'La respuesta la escribió el modelo y todas sus cifras se encontraron en tu cuenta.'],
          ['Revisa las cifras', 'La respuesta trae al menos una cifra que no se pudo verificar. Confírmala en la pantalla antes de decidir.'],
          ['Resumen de tus datos', 'No respondió el modelo — no está configurado, falló o se quedó sin tiempo — y StockAI escribió el resumen por reglas, con los mismos datos. La respuesta empieza diciendo por qué.'],
        ] },

        { t: 'h2', id: 'read-only', text: 'Solo lee, nunca cambia nada' },
        { t: 'p', text: 'El asistente no puede crear, enviar, aprobar ni recibir órdenes de compra, ni editar stock, costos o proveedores. Es una decisión de diseño: un chat no puede mostrarte una confirmación ni darte un botón de deshacer. Cuando pides una acción, te dice qué haría y te manda a la pantalla donde se hace:' },
        { t: 'table', head: ['Para…', 'Pantalla'], rows: [
          ['Decidir qué pedir hoy y armar la orden de compra', '[Panel de compras](app:/compras)'],
          ['Enviar una orden, registrar la llegada, seguir lo que viene', '[Pedidos](app:/pedidos)'],
          ['Stock, costos, tiempos de entrega y compras mínimas', '[Inventario](app:/inventario)'],
          ['Proveedores y su scorecard', '[Proveedores](app:/proveedores)'],
          ['El gráfico del pronóstico de un producto', '[Pronósticos](app:/pronosticos)'],
          ['Subir ventas y volver a calcular', '[Mis ventas](app:/ventas)'],
          ['El impacto de seguir las recomendaciones', '[Impacto](app:/impacto)'],
        ] },

        { t: 'h2', id: 'conversations', text: 'Conversaciones' },
        { t: 'ul', items: [
          'Una conversación vacía te saluda por tu nombre, resume lo que ve hoy en tu cuenta y te propone preguntas armadas con tus propios productos en riesgo, bajo «Pregúntame por tu cuenta». Un clic las envía.',
          'Escribe abajo y envía con Enter; Shift+Enter hace un salto de línea.',
          'La columna izquierda guarda tus conversaciones, con «Nueva Conversación» arriba y «Buscar conversaciones…» debajo. Se agrupan en «Favoritos» y «Recientes»: la estrella mueve una conversación a favoritos.',
          'El título de una conversación nueva se escribe solo a partir de tu primera pregunta.',
          'El basurero pide confirmación y, al borrar, te deja «Deshacer» durante unos segundos desde el aviso.',
          'Cada persona ve solo sus propias conversaciones.',
        ] },
        { t: 'note', tone: 'info', text: 'El asistente responde siempre sobre la cuenta completa y la actualización activa. Ya no hay que elegir una sesión ni filtrar «fuentes de datos» para preguntar.' },

        { t: 'h2', id: 'limits', text: 'Límites' },
        { t: 'dl', items: [
          ['Largo de una pregunta', 'Hasta 4.000 caracteres.'],
          ['Mensajes por minuto', 'Hasta 20 mensajes por minuto para toda la empresa. Al pasarte verás «Demasiadas solicitudes — espera un momento antes de enviar otro mensaje.»'],
          ['Memoria de la conversación', 'Cada respuesta tiene en cuenta los últimos 8 mensajes del hilo. Lo que dijiste mucho más arriba ya no entra.'],
          ['Tiempo por respuesta', 'Cada respuesta tiene un presupuesto de tiempo de algo más de 20 segundos. Si se agota, la pantalla lo dice y tu pregunta se queda en el campo para que la reintentes, o la hagas más corta.'],
        ] },

        { t: 'h2', id: 'unavailable', text: 'Cuando el asistente no está disponible' },
        { t: 'p', text: 'El asistente necesita un modelo de lenguaje configurado en la instalación. Si no lo hay, la pantalla lo dice antes de que escribas: «Esta instalación no tiene configurado un modelo de lenguaje, así que el asistente no puede responder. El resto de StockAI funciona igual: los pronósticos, el semáforo y las órdenes de compra no dependen de él.» Quien administra el servidor lo activa en [Instalación](/docs/administracion/instalacion).' },
        { t: 'p', text: 'Los pronósticos, el semáforo, las recomendaciones y las órdenes de compra se calculan sin el asistente. Qué datos salen hacia el proveedor de IA está en [Privacidad del asistente](/docs/asistente/privacidad-del-asistente).' },
      ],
    },

    'asistente/asistente-por-whatsapp': {
      title: 'El asistente por WhatsApp',
      nav: 'Por WhatsApp',
      description:
        'El mismo asistente, desde tu WhatsApp: le escribes desde tu número verificado y te contesta con los datos de tu cuenta, en mensajes cortos.',
      blocks: [
        { t: 'p', text: 'El bot de WhatsApp usa el mismo núcleo que el chat web: el mismo resumen de tu cuenta, las mismas herramientas de solo lectura y la misma comprobación de cifras. Una misma pregunta tiene la misma respuesta en los dos lados; solo cambia el formato.' },

        { t: 'h2', id: 'requirements', text: 'Qué necesitas' },
        { t: 'ul', items: [
          'Que la instalación tenga WhatsApp conectado (Twilio). Si no, no se manda ni se recibe ningún WhatsApp. Lo configura quien administra el servidor en [Instalación](/docs/administracion/instalacion).',
          'Que la instalación tenga el modelo de lenguaje configurado. Sin él, el bot contesta con el resumen por reglas, igual que el chat web.',
          'Tu número vinculado y verificado en [Mi cuenta](app:/mi-cuenta). El número verificado es tu credencial: es lo único que le dice al bot quién eres y de qué empresa.',
        ] },

        { t: 'h2', id: 'link-number', text: 'Vincular tu número' },
        { t: 'steps', items: [
          'Abre [Mi cuenta](app:/mi-cuenta) y busca la tarjeta «Vincular WhatsApp».',
          'Escribe tu número con el código de país, por ejemplo `+50688887777`.',
          'Pulsa «Enviar código». Te llega un código de 6 dígitos por WhatsApp.',
          'Escríbelo en la misma tarjeta y confírmalo. El código vence a los 15 minutos y admite 5 intentos; si se te pasa, pide otro.',
          'Cuando la tarjeta muestra tu número como verificado, ya puedes escribirle al bot.',
        ] },
        { t: 'p', text: 'Ese mismo número es al que te llegan las alertas de inventario por WhatsApp. Si escribes al bot desde un número que no está vinculado, te contesta «Hola 👋 No reconozco este número. Vincula tu WhatsApp desde tu perfil en StockAI para poder ayudarte por aquí.» y no lee nada de ninguna cuenta.' },

        { t: 'h2', id: 'what-to-ask', text: 'Qué preguntarle' },
        { t: 'p', text: 'Lo mismo que en el chat web: qué tienes que pedir hoy, qué pedido viene atrasado, cuánto tarda de verdad un proveedor, cómo va un producto. Las respuestas están pensadas para leerse en el celular:' },
        { t: 'ul', items: [
          'Texto plano, en pocas líneas cortas, sin tablas.',
          'Hasta unos 1.200 caracteres por respuesta.',
          'Cuando te manda a una pantalla, la nombra con su ruta (por ejemplo `/compras`) para que la abras en StockAI.',
          'Solo en español, aunque tu cuenta esté en inglés.',
        ] },

        { t: 'h2', id: 'read-only', text: 'Solo lee' },
        { t: 'p', text: 'Por WhatsApp tampoco se aprueba, envía ni recibe nada. Si le pides registrar una llegada o aprobar una orden, te dice dónde hacerlo en la app. Así una respuesta mal entendida nunca mueve tu stock ni tus órdenes de compra.' },

        { t: 'h2', id: 'limits', text: 'Límites' },
        { t: 'dl', items: [
          ['Mensajes por minuto', 'Hasta 20 mensajes por minuto por número. Si te pasas, el bot te pide esperar.'],
          ['Tiempo por respuesta', 'Unos 25 segundos. Si el modelo no llega a tiempo, te contesta con el resumen por reglas y te dice por qué.'],
        ] },
        { t: 'note', tone: 'info', text: 'Una instalación puede dejar el bot en un modo genérico que no usa el modelo de lenguaje y contesta con un mensaje fijo. Si el bot te responde siempre lo mismo, pregúntale a quien administra el servidor.' },
      ],
    },

    'asistente/privacidad-del-asistente': {
      title: 'Privacidad del asistente',
      nav: 'Privacidad',
      description:
        'Qué datos salen hacia el proveedor de IA cuando preguntas, qué no sale nunca, y qué pasa si la instalación no tiene el asistente activado.',
      blocks: [
        { t: 'p', text: 'StockAI corre en su servidor: tus ventas, tu stock, tus proveedores y tus órdenes de compra viven en esa base de datos y en ese disco. El asistente es una de las pocas cosas que abre una conexión hacia afuera, y solo si la instalación lo configuró.' },

        { t: 'h2', id: 'one-provider', text: 'Un solo proveedor: DeepSeek' },
        { t: 'p', text: 'Todas las funciones de IA de StockAI — el asistente web, el bot de WhatsApp, los resúmenes escritos y el diagnóstico de datos — usan un único proveedor: **DeepSeek**, por HTTPS. No hay un segundo proveedor ni una cadena de respaldo: si la llave falta o está mal escrita, la función se apaga; tus datos nunca terminan en otro proveedor por accidente.' },

        { t: 'h2', id: 'what-is-sent', text: 'Qué se envía con cada pregunta' },
        { t: 'ul', items: [
          'La pregunta que escribiste y los últimos mensajes de esa conversación.',
          'El contexto de tu cuenta necesario para responderla: códigos y nombres de productos, cantidades, costos y nombres de proveedores de los productos que tienen que ver con la pregunta.',
          'Tu nombre de pila, tu rol y el nombre de tu empresa, para que la respuesta te hable a ti.',
          'Lo que devuelven las herramientas de lectura cuando el asistente consulta un producto, un proveedor o una orden.',
        ] },
        { t: 'p', text: 'El resumen de la cuenta tiene un tope de unos 7.000 caracteres por pregunta: es una selección de lo relevante, no tu base de datos completa.' },

        { t: 'h2', id: 'what-is-not-sent', text: 'Qué no se envía' },
        { t: 'ul', items: [
          'Tus archivos de ventas originales ni tu historial completo.',
          'Contraseñas, llaves de API ni credenciales.',
          'Nada cuando no estás preguntando: no hay telemetría ni envíos en segundo plano hacia el proveedor de IA.',
        ] },
        { t: 'p', text: 'El pronóstico, el semáforo, las recomendaciones de compra y las alertas se calculan en el servidor de StockAI y nunca pasan por el modelo de lenguaje.' },

        { t: 'h2', id: 'without-key', text: 'Si la instalación no lo activa' },
        { t: 'p', text: 'Sin la llave de DeepSeek no sale nada hacia el proveedor de IA. El asistente no da error: la pantalla avisa que no está disponible y, si preguntas, contesta con un resumen escrito por reglas a partir de tus datos, que empieza diciendo «El asistente con IA no está activado en esta instalación, así que te respondo directo con tus datos:». Lo que pierdes es la conversación; el resto de StockAI funciona igual.' },
        { t: 'p', text: 'La activación la hace quien administra el servidor, en [Instalación](/docs/administracion/instalacion), tarjeta «Asistente (DeepSeek)».' },

        { t: 'h2', id: 'mcp', text: 'Tu propio cliente de IA (MCP)' },
        { t: 'p', text: 'Es la dirección contraria: si conectas tu propio cliente de IA a StockAI por [MCP](/docs/integraciones/mcp), StockAI no abre ninguna conexión; tu cliente llama, lee con tu llave de API y se va. Ese cliente puede leer tu stock, tus costos y los nombres de tus proveedores, así que trata esa llave como cualquier otra credencial y revócala si deja de usarse.' },

        { t: 'p', text: 'El tratamiento de datos completo está en la [Política de privacidad](/privacidad).' },
      ],
    },
  },

  en: {
    'asistente/asistente-ia': {
      title: 'The AI assistant',
      nav: 'AI assistant',
      description:
        'A chat that answers from your own account: what is at risk today, which orders are late, how a supplier is performing. It reads; it never changes anything.',
      blocks: [
        { t: 'p', text: 'The assistant lives under [AI Assistant](app:/asistente), in the Analysis group of the menu (on a phone, in the "Assistant" tab). The screen is titled "AI Analyst". It is not a search box for generic answers: before every question it reads your account the same way your screens do, and it answers about your products, your orders and your suppliers.' },
        { t: 'shot', key: 'asistente', alt: 'The AI assistant screen with the conversation list on the left and an answer about at-risk products' },

        { t: 'h2', id: 'what-it-knows', text: 'What it knows about your account' },
        { t: 'p', text: 'With every message the assistant builds a live brief of your account, with the parts closest to your question first:' },
        { t: 'ul', items: [
          'Who you are (your name, your role and your company) and how recent your forecast is.',
          "Today's red and amber products, with the suggested quantity, cover, supplier and lead time.",
          'Overstock and the money tied up in it.',
          'Your open purchase orders and the overdue ones.',
          "Your suppliers' real lead times and reliability.",
          'The demand trend, upcoming peaks, and what you did in StockAI lately.',
        ] },
        { t: 'p', text: 'When it needs something more specific — a product, its forecast, a supplier, a purchase order, your product list or your activity — it looks it up with **read-only** tools, just like the screens.' },
        { t: 'p', text: 'The red and amber products in the brief come from the same stock signal you see in [Purchasing panel](app:/compras) and [Inventory](app:/inventario): the active update, at your account\'s planning grain. To understand that signal, read [The stock signal](/docs/conceptos/semaforo).' },

        { t: 'h2', id: 'checked-numbers', text: 'Checked figures' },
        { t: 'p', text: "Every number in an answer has to appear in your data, in what a tool returned, or in your own message. StockAI checks this after the model answers. If a figure does not match, it asks for a correction; if one is still unsupported after that, it is not hidden: the answer ends with a warning like \"I could not verify these figures against your data\" and lists them." },
        { t: 'p', text: 'Every answer carries a label saying where it came from:' },
        { t: 'dl', items: [
          ['Your data', 'The model wrote the answer and every figure in it was found in your account.'],
          ['Check the figures', 'The answer carries at least one figure that could not be verified. Confirm it on the screen before you decide.'],
          ['Summary of your data', 'The model did not answer — it is not configured, it failed or it ran out of time — so StockAI wrote the summary by rules from the same data. The answer opens by saying why.'],
        ] },

        { t: 'h2', id: 'read-only', text: 'It reads, never changes anything' },
        { t: 'p', text: 'The assistant cannot create, send, approve or receive purchase orders, nor edit stock, costs or suppliers. That is deliberate: a chat cannot show you a confirmation or give you an undo button. When you ask for an action, it says what it would do and points you to the screen where you do it:' },
        { t: 'table', head: ['To…', 'Screen'], rows: [
          ['Decide what to order today and build the purchase order', '[Purchasing panel](app:/compras)'],
          ['Send an order, record its arrival, track what is coming', '[Orders](app:/pedidos)'],
          ['Stock, costs, lead times and minimum orders', '[Inventory](app:/inventario)'],
          ['Suppliers and their scorecard', '[Suppliers](app:/proveedores)'],
          ["A product's forecast chart", '[Forecasts](app:/pronosticos)'],
          ['Upload sales and recalculate', '[My sales](app:/ventas)'],
          ['The impact of following the recommendations', '[Impact](app:/impacto)'],
        ] },

        { t: 'h2', id: 'conversations', text: 'Conversations' },
        { t: 'ul', items: [
          'An empty conversation greets you by name, sums up what it sees in your account today and offers questions built from your own at-risk products, under "Ask me about your account". One click sends them.',
          'Type at the bottom and send with Enter; Shift+Enter adds a new line.',
          'The left column keeps your conversations, with "New Chat" at the top and "Search chats…" below it. They are grouped into "Favorites" and "Recent": the star moves a conversation to favorites.',
          'A new conversation titles itself from your first question.',
          'The bin asks for confirmation and, once deleted, lets you "Undo" for a few seconds from the notice.',
          'Each person sees only their own conversations.',
        ] },
        { t: 'note', tone: 'info', text: 'The assistant always answers about the whole account and the active update. There is no longer a session to pick or a "data sources" filter to set before asking.' },

        { t: 'h2', id: 'limits', text: 'Limits' },
        { t: 'dl', items: [
          ['Question length', 'Up to 4,000 characters.'],
          ['Messages per minute', 'Up to 20 messages per minute for the whole company. Past that you see "Too many requests — please wait a moment before sending another message."'],
          ['Conversation memory', 'Each answer takes the last 8 messages of the thread into account. Anything said much further up is no longer included.'],
          ['Time per answer', 'Each answer has a time budget of a little over 20 seconds. If it runs out, the screen says so and your question stays in the box so you can retry it, or ask a shorter one.'],
        ] },

        { t: 'h2', id: 'unavailable', text: 'When the assistant is unavailable' },
        { t: 'p', text: 'The assistant needs a language model configured on the installation. Without one, the screen says so before you type: "This installation has no language model configured, so the assistant cannot answer. The rest of StockAI is unaffected: forecasts, the stock signal and purchase orders do not depend on it." Whoever administers the server turns it on under [Installation](/docs/administracion/instalacion).' },
        { t: 'p', text: 'Forecasts, the stock signal, recommendations and purchase orders are computed without the assistant. Which data goes to the AI provider is covered in [Assistant privacy](/docs/asistente/privacidad-del-asistente).' },
      ],
    },

    'asistente/asistente-por-whatsapp': {
      title: 'The assistant on WhatsApp',
      nav: 'On WhatsApp',
      description:
        'The same assistant, from your WhatsApp: message it from your verified number and it answers from your account data, in short messages.',
      blocks: [
        { t: 'p', text: 'The WhatsApp bot runs on the same core as the web chat: the same account brief, the same read-only tools and the same figure check. The same question gets the same answer on both; only the format changes.' },

        { t: 'h2', id: 'requirements', text: 'What you need' },
        { t: 'ul', items: [
          'WhatsApp connected on the installation (Twilio). Without it no WhatsApp is sent or received. Whoever administers the server sets it up under [Installation](/docs/administracion/instalacion).',
          'A language model configured on the installation. Without it the bot answers with the rule-based summary, like the web chat.',
          'Your number linked and verified in [My account](app:/mi-cuenta). The verified number is your credential: it is the only thing that tells the bot who you are and which company you belong to.',
        ] },

        { t: 'h2', id: 'link-number', text: 'Link your number' },
        { t: 'steps', items: [
          'Open [My account](app:/mi-cuenta) and find the "Link WhatsApp" card.',
          'Type your number with the country code, for example `+50688887777`.',
          'Press "Send code". A 6-digit code arrives on WhatsApp.',
          'Type it in the same card and confirm. The code expires after 15 minutes and allows 5 attempts; if you miss it, ask for another.',
          'Once the card shows your number as verified, you can message the bot.',
        ] },
        { t: 'p', text: 'That same number is where your WhatsApp inventory alerts arrive. If you message the bot from a number that is not linked, it replies (in Spanish) that it does not recognise the number and asks you to link it from your StockAI profile — and it reads nothing from any account.' },

        { t: 'h2', id: 'what-to-ask', text: 'What to ask it' },
        { t: 'p', text: 'The same as the web chat: what to order today, which order is late, how long a supplier really takes, how a product is doing. Answers are written to be read on a phone:' },
        { t: 'ul', items: [
          'Plain text, a few short lines, no tables.',
          'Up to about 1,200 characters per answer.',
          'When it points you to a screen, it names its path (for example `/compras`) so you can open it in StockAI.',
          'Spanish only, even if your account is set to English.',
        ] },

        { t: 'h2', id: 'read-only', text: 'It only reads' },
        { t: 'p', text: 'Nothing is approved, sent or received over WhatsApp either. If you ask it to record an arrival or approve an order, it tells you where to do it in the app. That way a misunderstood answer can never move your stock or your purchase orders.' },

        { t: 'h2', id: 'limits', text: 'Limits' },
        { t: 'dl', items: [
          ['Messages per minute', 'Up to 20 messages per minute per number. Past that, the bot asks you to wait.'],
          ['Time per answer', 'About 25 seconds. If the model is not done in time, it replies with the rule-based summary and says why.'],
        ] },
        { t: 'note', tone: 'info', text: 'An installation can put the bot in a generic mode that skips the language model and replies with a fixed message. If the bot always answers the same thing, ask whoever administers the server.' },
      ],
    },

    'asistente/privacidad-del-asistente': {
      title: 'Assistant privacy',
      nav: 'Privacy',
      description:
        'Which data goes to the AI provider when you ask a question, what never leaves, and what happens if the installation has not turned the assistant on.',
      blocks: [
        { t: 'p', text: 'StockAI runs on its server: your sales, stock, suppliers and purchase orders live in that database and on that disk. The assistant is one of the few things that opens a connection to the outside, and only if the installation configured it.' },

        { t: 'h2', id: 'one-provider', text: 'One provider: DeepSeek' },
        { t: 'p', text: "Every AI feature in StockAI — the web assistant, the WhatsApp bot, the written summaries and the data diagnosis — uses a single provider: **DeepSeek**, over HTTPS. There is no second provider and no fallback chain: if the key is missing or mistyped, the feature turns off; your data never ends up at another provider by accident." },

        { t: 'h2', id: 'what-is-sent', text: 'What is sent with each question' },
        { t: 'ul', items: [
          'The question you typed and the latest messages of that conversation.',
          'The account context needed to answer it: product codes and names, quantities, costs and supplier names for the products the question is about.',
          'Your first name, your role and your company name, so the answer speaks to you.',
          'What the read-only tools return when the assistant looks up a product, a supplier or an order.',
        ] },
        { t: 'p', text: 'The account brief is capped at about 7,000 characters per question: it is a selection of what is relevant, not your whole database.' },

        { t: 'h2', id: 'what-is-not-sent', text: 'What is not sent' },
        { t: 'ul', items: [
          'Your original sales files or your full history.',
          'Passwords, API keys or credentials.',
          'Anything while you are not asking: there is no telemetry and no background traffic to the AI provider.',
        ] },
        { t: 'p', text: 'The forecast, the stock signal, purchase recommendations and alerts are computed on the StockAI server and never go through the language model.' },

        { t: 'h2', id: 'without-key', text: 'If the installation does not turn it on' },
        { t: 'p', text: 'Without the DeepSeek key, nothing goes to the AI provider. The assistant does not error out: the screen says it is unavailable and, if you ask anyway, it answers with a rule-based summary of your data that opens with "The AI assistant is not enabled on this installation, so here is what your data says directly:". What you lose is the conversation; the rest of StockAI works the same.' },
        { t: 'p', text: 'Turning it on is done by whoever administers the server, under [Installation](/docs/administracion/instalacion), card "Assistant (DeepSeek)".' },

        { t: 'h2', id: 'mcp', text: 'Your own AI client (MCP)' },
        { t: 'p', text: 'This is the opposite direction: if you connect your own AI client to StockAI over [MCP](/docs/integraciones/mcp), StockAI opens no connection; your client calls in, reads with your API key and leaves. That client can read your stock, your costs and your supplier names, so treat the key like any other credential and revoke it once it is no longer used.' },

        { t: 'p', text: 'The full data-processing terms are in the [Privacy policy](/privacidad).' },
      ],
    },
  },
}

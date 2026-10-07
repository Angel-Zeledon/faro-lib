/**
 * The legal documents added on 2026-10-02, in both languages: acceptable
 * use, the data processing agreement (DPA), how StockAI uses AI, the
 * vulnerability disclosure policy, the accessibility statement, the
 * commercial conditions and the source-code licence (/uso-aceptable,
 * /procesamiento-de-datos, /ia, /divulgacion-responsable, /accesibilidad,
 * /condiciones-comerciales, /licencia).
 *
 * DRAFT — TO BE REVIEWED BY A LAWYER BEFORE IT IS RELIED ON. Same status and
 * same rules as ./legal.ts, which spreads these into its `docs`: written by
 * the product team from what the code does today, not legal advice, and every
 * value in square brackets is a placeholder the owner must fill in — visible
 * on the page on purpose so an unfinished document cannot pass for a finished
 * one.
 *
 * Where each factual claim comes from (keep in step with the code):
 *   limits, trial 24 h, paid ceilings       backend/entitlements/plans.py
 *   over-limit keeps data, blocks additions backend/entitlements/service.py enforce_limit
 *   source-code offer (USD 14,999, one-time) Frontend/src/i18n/landing.ts `source` (owner's decision 2026-10-06)
 *   120 calls/min per key                   terms (legal.ts) / API rate limiter
 *   tenant isolation, roles, bcrypt, Fernet backend/auth/*, backend/service_config/crypto.py
 *   export ZIP / full erasure (admin)       backend/api/v1/tenant_data.py, backend/tenants/data_export.py
 *   TLS                                     deploy/Caddyfile.split (Caddy automatic HTTPS)
 *   nightly backups, 14 days                owner's production host (not in the repo), as in /privacidad
 *   AI consumers and what they send         backend/assistant/, backend/ai/narrative_service.py,
 *                                           backend/api/v1/ai_insights.py, configuration.py _llm_health_narrative
 *   number check on assistant replies       backend/assistant/grounding.py (assistant only, not the summaries)
 *   assistant is read-only                  backend/assistant/__init__.py, whatsapp/tools.py WRITE_TOOLS = {}
 *   Panel summary requested on open         Frontend/src/app/compras/page.tsx getMorningNarrative
 *   accessibility measures                  globals.css (reduced motion, :focus-visible), ConfirmDialog.tsx
 *                                           (focus trap), components/mobile/README.md (44px), layout.tsx
 *                                           (lang, zoom not blocked), SignalBadge.tsx (icon + label)
 *   source-code licence                     root LICENSE (proprietary since 2026-10-02; MIT before),
 *                                           THIRD_PARTY_NOTICES.md (scripts/gen_third_party_notices.py),
 *                                           provider keys are the operator's own (/instalacion, service_config)
 *   accessibility gaps                      echarts canvas charts (ChartPanel, SalesPatternPanel) have no
 *                                           text alternative; <html lang> stays "es" in English mode
 */
import type { LegalDoc } from './legal'

type ExtraKey = 'acceptableUse' | 'dpa' | 'ai' | 'disclosure' | 'accessibility' | 'commercial' | 'license'

const MAIL = '[contacto@stockai.es](mailto:contacto@stockai.es)'

// ─────────────────────────────────────────────────────────────────────────────
// Español
// ─────────────────────────────────────────────────────────────────────────────

export const LEGAL_EXTRA_ES: Record<ExtraKey, LegalDoc> = {
  // ── Uso aceptable ─────────────────────────────────────────────────────────
  acceptableUse: {
    label: 'Uso aceptable',
    title: 'Política de uso aceptable',
    intro: 'Lo que no se puede hacer con StockAI —en la aplicación, la API, el asistente y las cuentas de prueba— y qué pasa si alguien lo hace.',
    summary: [
      'Usa StockAI para gestionar tu propio inventario, con datos que tengas derecho a usar.',
      'No abuses de las cuentas de prueba ni de la API, no copies el servicio y no lo uses para enviar mensajes no deseados.',
      'Si encuentras un fallo de seguridad, repórtalo según la [política de divulgación responsable](/divulgacion-responsable) en vez de explotarlo.',
      'Si alguien incumple esta política, podemos revocar claves, limitar o suspender la cuenta, como dicen los [términos](/terminos#termination).',
    ],
    sections: [
      {
        id: 'scope',
        title: 'A qué se aplica',
        blocks: [
          'Esta política desarrolla la sección de uso aceptable de los [términos del servicio](/terminos#acceptable-use) y forma parte de ellos. Se aplica a todo el que use StockAI: la aplicación web, la API, el servidor MCP, el asistente (en la aplicación o por WhatsApp) y las cuentas de prueba.',
          'Si administras una cuenta, también respondes por lo que hagan las personas que invitas y las integraciones a las que das una clave de API.',
        ],
      },
      {
        id: 'trial',
        title: 'Cuentas de prueba',
        blocks: [
          {
            list: [
              'La cuenta de prueba sirve para conocer StockAI. Dura 24 horas y tiene límites pequeños a propósito.',
              'No crees cuentas de prueba una tras otra para trabajar con ellas de forma continua ni para saltarte los límites de la cuenta de prueba. Contamos las cuentas de prueba creadas desde cada dirección IP para frenar ese abuso.',
              'No subas a una cuenta de prueba datos que no quieras perder ni datos personales sensibles: se borra entera al terminar.',
            ],
          },
        ],
      },
      {
        id: 'api',
        title: 'La API y el servidor MCP',
        blocks: [
          {
            list: [
              'Respeta los límites: hasta 120 llamadas por minuto por clave. No repartas el trabajo entre varias cuentas o claves para saltártelos.',
              'Cada clave es una credencial. No la publiques, no la incluyas en código que otros puedan ver y no la compartas fuera de tu empresa.',
              'Usa la API para conectar tus propios sistemas. No la uses para ofrecer a terceros un servicio construido sobre StockAI sin un acuerdo escrito con nosotros.',
            ],
          },
        ],
      },
      {
        id: 'scraping',
        title: 'Extracción automática y copia del servicio',
        blocks: [
          'Para automatizar, usa la API. No extraigas datos de la aplicación ni del sitio con robots o scripts que simulen a un usuario, salvo la indexación normal de los buscadores en las páginas públicas.',
          'No intentes copiar el servicio alojado: descompilar o desofuscar el código que se sirve al navegador, saltarte los controles de acceso, llamar a direcciones que no son públicas o reconstruir el producto para competir con él. Esto no limita lo que te permita una licencia escrita que hayas firmado aparte con nosotros.',
        ],
      },
      {
        id: 'content',
        title: 'Lo que subes',
        blocks: [
          'Los archivos, documentos y datos que subas deben ser tuyos o debes tener derecho a usarlos. En particular, no subas:',
          {
            list: [
              'contenido ilegal, o datos obtenidos sin derecho (por ejemplo, la base de clientes de otra empresa);',
              'datos personales que no necesites para gestionar tu inventario, y menos aún datos sensibles (salud, datos financieros de personas, documentos de identidad);',
              'archivos con software malicioso o preparados para atacar el servicio.',
            ],
          },
        ],
      },
      {
        id: 'messages',
        title: 'Mensajes a terceros',
        blocks: [
          'StockAI puede enviar por correo las órdenes de compra a tus proveedores, y avisos por correo, WhatsApp o SMS. Esos mensajes salen en tu nombre, así que:',
          {
            list: [
              'envíalos solo a proveedores y contactos con los que tienes una relación comercial;',
              'no los uses para publicidad, mensajes masivos no solicitados ni para hacerte pasar por otra persona o empresa;',
              'si alguien te pide que no le escribas más, deja de hacerlo.',
            ],
          },
        ],
      },
      {
        id: 'security',
        title: 'Pruebas de seguridad',
        blocks: [
          'No pruebes la seguridad del servicio, no escanees nuestros servidores y no intentes entrar a cuentas o datos ajenos, salvo dentro de las reglas de la [política de divulgación responsable](/divulgacion-responsable), que te da permiso para investigar de buena fe con ciertos límites.',
          'Nunca está permitido saturar el servicio (denegación de servicio) ni engañar a nuestro equipo o a otros clientes para obtener acceso.',
        ],
      },
      {
        id: 'enforcement',
        title: 'Qué hacemos si se incumple',
        blocks: [
          'Según la gravedad, podemos borrar el contenido que incumple, revocar una clave de API, limitar funciones, borrar una cuenta de prueba antes de tiempo o suspender o cerrar la cuenta, como prevé la sección de [suspensión y terminación](/terminos#termination) de los términos. Salvo urgencia —por ejemplo, un ataque en curso o un riesgo para otros clientes— te avisamos antes y te damos oportunidad de corregirlo.',
          'Si el incumplimiento puede ser un delito, podemos informar a las autoridades.',
        ],
      },
      {
        id: 'report',
        title: 'Cómo reportar un abuso',
        blocks: [
          `Si ves que alguien usa StockAI contra esta política —por ejemplo, recibiste un correo no deseado enviado desde StockAI— escríbenos a ${MAIL} con lo que sepas: el mensaje recibido, la fecha y la dirección de origen.`,
        ],
      },
    ],
  },

  // ── DPA ───────────────────────────────────────────────────────────────────
  dpa: {
    label: 'Procesamiento de datos (DPA)',
    title: 'Acuerdo de procesamiento de datos',
    intro: 'Las condiciones con las que StockAI trata, por cuenta de tu empresa, los datos personales que hay en los datos de negocio que subes.',
    summary: [
      'Para los datos de negocio que subes, **tu empresa es la responsable** y StockAI es el **encargado**: los tratamos solo para darte el servicio y según tus instrucciones.',
      'Te decimos qué proveedores (subencargados) usamos, dónde están, y te avisamos antes de agregar uno.',
      'Si hay una brecha de seguridad que afecte tus datos, te avisamos en [PLAZO DE NOTIFICACIÓN — confirmar].',
      'Al terminar, te entregamos una copia completa de tus datos y los borramos.',
    ],
    sections: [
      {
        id: 'parties',
        title: 'Partes y objeto',
        blocks: [
          'Este acuerdo se celebra entre la empresa cliente que usa StockAI (en adelante, «el cliente») y [RAZÓN SOCIAL], [CÉDULA JURÍDICA / NIF] (en adelante, «StockAI»). Forma parte de los [términos del servicio](/terminos) y se aplica desde que el cliente crea una cuenta y sube datos.',
          'Si el cliente necesita una versión firmada de este acuerdo, puede pedirla a ' + MAIL + '. [VERSIÓN FIRMADA — confirmar si se ofrece y quién la firma]',
          'Si algo de este acuerdo contradice los términos en lo que toca a datos personales, prevalece este acuerdo.',
        ],
      },
      {
        id: 'roles',
        title: 'Quién es responsable y quién encargado',
        blocks: [
          '**El cliente es el responsable** de los datos personales que hay en los datos de negocio que sube o conecta: decide qué datos se cargan y para qué. **StockAI es el encargado**: los trata por cuenta del cliente, solo para prestarle el servicio.',
          'Para los datos de las cuentas de usuario (nombre, correo, número de WhatsApp) que usamos para operar el servicio —por ejemplo, para el inicio de sesión y los correos del servicio—, StockAI actúa como responsable, como explica la [política de privacidad](/privacidad).',
        ],
      },
      {
        id: 'details',
        title: 'Datos, personas y finalidad',
        blocks: [
          {
            table: {
              head: ['Aspecto', 'Detalle'],
              rows: [
                ['Personas afectadas', 'Usuarios de la cuenta del cliente; contactos de sus proveedores; clientes del cliente cuando aparecen en el historial de ventas.'],
                ['Tipos de datos', 'Nombres, correos y teléfonos de contacto; nombres o códigos de clientes en ventas; cualquier otro dato personal que el cliente incluya en sus archivos, documentos o conexiones.'],
                ['Finalidad', 'Prestar el servicio: guardar los datos, entrenar los pronósticos de la cuenta, calcular el semáforo, generar y enviar órdenes de compra, responder al asistente y mostrar los resultados.'],
                ['Operaciones', 'Almacenamiento, consulta, cálculo, envío de mensajes que el cliente activa, copia de seguridad y borrado.'],
                ['Duración', 'Mientras la cuenta exista, más el plazo de las copias de seguridad (14 días).'],
              ],
            },
          },
          'El cliente no debe subir datos personales que no necesite para gestionar su inventario, y en particular datos de categorías especiales (salud, origen étnico, datos biométricos y similares).',
        ],
      },
      {
        id: 'instructions',
        title: 'Instrucciones del cliente',
        blocks: [
          'StockAI trata los datos solo según las instrucciones documentadas del cliente. Son instrucciones: los términos, este acuerdo y lo que el cliente configura y hace en la aplicación o por la API (por ejemplo, subir un archivo, activar un aviso por WhatsApp o pedir una exportación).',
          'Si una instrucción nos parece contraria a la ley, se lo diremos al cliente antes de seguirla. Si una ley nos obliga a tratar datos de otro modo, se lo diremos antes, salvo que esa misma ley lo prohíba.',
        ],
      },
      {
        id: 'confidentiality',
        title: 'Confidencialidad',
        blocks: [
          'Solo acceden a los datos del cliente las personas de StockAI que lo necesitan para operar, mantener o dar soporte al servicio, y están obligadas a guardar confidencialidad. [COMPROMISOS DE CONFIDENCIALIDAD DEL PERSONAL — confirmar cómo se documentan]',
          'No miramos los datos de una cuenta salvo para resolver un problema que el cliente nos plantea, para mantener el servicio o cuando la ley nos lo exige.',
        ],
      },
      {
        id: 'security',
        title: 'Medidas de seguridad',
        blocks: [
          'StockAI aplica, como mínimo, estas medidas:',
          {
            list: [
              '**Aislamiento por cuenta:** cada consulta a la base de datos va filtrada por la cuenta de quien la hace; una empresa no puede ver datos de otra.',
              '**Permisos por rol:** administrador, analista y lector. Solo un analista o un administrador puede cambiar datos, y solo un administrador puede exportar o borrar la cuenta entera.',
              '**Contraseñas con bcrypt** y claves de API guardadas solo como huella: nadie puede leerlas.',
              '**Credenciales cifradas con Fernet:** las contraseñas de bases de datos y servicios que el cliente conecta se guardan cifradas.',
              '**Sesiones cortas:** el acceso caduca a los 15 minutos y se renueva mientras la pestaña está abierta; al cambiar la contraseña se cierran las sesiones anteriores.',
              '**Conexión cifrada (TLS):** el tráfico con la aplicación y la API va por HTTPS, con certificados gestionados por el servidor web (Caddy).',
              '**Copias de seguridad** de la base de datos cada noche, que se conservan 14 días.',
              '**Cuentas de prueba** borradas por completo a las 24 horas.',
            ],
          },
          'StockAI no tiene hoy certificaciones (como ISO 27001 o SOC 2). Podemos cambiar estas medidas siempre que el nivel de protección no baje.',
        ],
      },
      {
        id: 'subprocessors',
        title: 'Subencargados',
        blocks: [
          'El cliente autoriza a StockAI a usar estos subencargados, cada uno solo para su tarea:',
          {
            table: {
              head: ['Subencargado', 'Para qué', 'Cuándo', 'Dónde'],
              rows: [
                ['Hostinger', 'Servidor (VPS) donde corren la aplicación y la base de datos; correo de stockai.es', 'Siempre', '[UBICACIÓN DEL SERVIDOR — confirmar con el propietario]'],
                ['DeepSeek', 'Modelo de lenguaje del asistente, los resúmenes, las explicaciones y el diagnóstico de archivos (ver [cómo usamos la IA](/ia))', 'Solo cuando se usan esas funciones', 'China'],
                ['Proveedor de correo: Hostinger (SMTP) o Resend, según la configuración', 'Enviar correos del servicio, avisos y órdenes de compra', 'Cuando se envía un correo', '[UBICACIÓN — confirmar con el propietario]'],
                ['Twilio', 'Enviar y recibir mensajes de WhatsApp y SMS', 'Solo si WhatsApp o SMS están activados', 'Estados Unidos'],
              ],
            },
          },
          'Exigimos a cada subencargado obligaciones de protección de datos equivalentes a las de este acuerdo, en la medida en que sus condiciones lo permitan. [CONTRATOS CON SUBENCARGADOS — confirmar que existen y dónde constan]',
          `Antes de agregar o cambiar un subencargado, actualizamos esta lista y avisamos al cliente con [PLAZO DE AVISO — confirmar] de antelación por correo. Si el cliente se opone por motivos razonables y no encontramos una solución, puede terminar el servicio sin penalización.`,
        ],
      },
      {
        id: 'transfers',
        title: 'Transferencias internacionales',
        blocks: [
          'Por los subencargados de arriba, los datos pueden tratarse fuera del país del cliente: en [UBICACIÓN DEL SERVIDOR — confirmar con el propietario], en China (DeepSeek, solo si se usan las funciones de IA) y en Estados Unidos (Twilio, solo si se activan WhatsApp o SMS).',
          'Para clientes en la Unión Europea o en España: China no tiene una decisión de adecuación de la Comisión Europea. [MECANISMO DE TRANSFERENCIA — cláusulas contractuales tipo u otro, confirmar con un abogado para DeepSeek y Twilio]. Si el cliente no puede aceptar la transferencia a China, puede pedirnos que su cuenta no use las funciones de IA escribiendo a ' + MAIL + '.',
        ],
      },
      {
        id: 'breaches',
        title: 'Brechas de seguridad',
        blocks: [
          'Si StockAI conoce una brecha de seguridad que afecte a datos personales del cliente, se lo notificará sin demora indebida y en un plazo máximo de [PLAZO DE NOTIFICACIÓN — confirmar] desde que la conozca, al correo del administrador de la cuenta.',
          'La notificación dirá, en lo que se sepa en ese momento: qué pasó, qué datos y cuántas personas pueden estar afectadas, qué consecuencias son probables, qué hemos hecho y qué recomendamos hacer. Lo que no se sepa al principio lo enviaremos en cuanto lo sepamos.',
          'El cliente, como responsable, decide si debe avisar a la autoridad y a las personas afectadas; le daremos la información que necesite para hacerlo.',
        ],
      },
      {
        id: 'assistance',
        title: 'Ayuda con los derechos de las personas',
        blocks: [
          'Si una persona nos pide acceder, corregir o borrar sus datos que están en la cuenta del cliente, se lo trasladaremos al cliente y no responderemos por nuestra cuenta, salvo que la ley nos obligue.',
          'El cliente puede corregir la mayoría de los datos desde la aplicación. Para responder a una solicitud, un administrador puede descargar una copia completa de la cuenta, y le ayudaremos con lo que no pueda hacer él mismo. También ayudaremos, en lo razonable, con evaluaciones de impacto y consultas a la autoridad.',
        ],
      },
      {
        id: 'end',
        title: 'Devolución y borrado al terminar',
        blocks: [
          `Al terminar el servicio, el cliente puede pedir una copia completa de sus datos: un archivo ZIP con cada tabla de su empresa (sin contraseñas, huellas de claves ni secretos). Un administrador puede descargarla él mismo con la API, o pedírnosla a ${MAIL}.`,
          'Después borramos la cuenta: todas sus tablas y archivos se eliminan de una vez. Las copias de seguridad que aún los contengan desaparecen solas en un plazo de 14 días. Solo conservaremos algo si una ley nos obliga, y solo lo que esa ley exija.',
        ],
      },
      {
        id: 'audits',
        title: 'Auditorías',
        blocks: [
          'A petición razonable del cliente, le daremos la información necesaria para demostrar que cumplimos este acuerdo: por ejemplo, la descripción de las medidas de seguridad y la lista de subencargados.',
          'Si eso no basta, el cliente puede hacer una auditoría, por sí mismo o con un auditor obligado a confidencialidad, con [PREAVISO — confirmar] de antelación, en horario laboral, sin afectar al servicio ni a los datos de otros clientes, y a su cargo. [FRECUENCIA Y CONDICIONES DE AUDITORÍA — confirmar]',
        ],
      },
      {
        id: 'liability',
        title: 'Responsabilidad',
        blocks: [
          'La responsabilidad de cada parte por este acuerdo se rige por la sección de [límite de responsabilidad](/terminos#liability) de los términos, salvo en lo que la ley aplicable no permita limitar.',
        ],
      },
      {
        id: 'law',
        title: 'Marco legal',
        blocks: [
          'Este acuerdo se basa en la Ley 8968 de Costa Rica (Protección de la Persona frente al Tratamiento de sus Datos Personales). Para clientes en la Unión Europea o en España, cumple además la función del contrato de encargado del artículo 28 del Reglamento General de Protección de Datos (RGPD) y se interpreta conforme a él y a la Ley Orgánica 3/2018 (LOPDGDD).',
        ],
      },
    ],
  },

  // ── IA ────────────────────────────────────────────────────────────────────
  ai: {
    label: 'Inteligencia artificial',
    title: 'Cómo usa StockAI la inteligencia artificial',
    intro: 'Qué funciones usan inteligencia artificial, qué datos salen de StockAI para eso, qué límites tiene y cómo evitarla.',
    summary: [
      'Hay dos tipos de IA en StockAI: **los modelos de pronóstico**, que se entrenan dentro de StockAI con los datos de tu cuenta, y **un modelo de lenguaje (DeepSeek)**, para el asistente y los textos explicativos.',
      'Los pronósticos no salen de StockAI ni se mezclan con los de otras empresas.',
      'El asistente **solo lee**: no puede crear, cambiar ni borrar nada en tu cuenta.',
      'La IA se equivoca. Las cifras del asistente se comparan con tus datos, pero **la decisión es siempre tuya**.',
    ],
    sections: [
      {
        id: 'forecasting',
        title: 'Los modelos de pronóstico',
        blocks: [
          'Para pronosticar la demanda, StockAI entrena modelos estadísticos y de aprendizaje automático (como LightGBM, XGBoost, Prophet, ARIMA, ETS, Croston y redes LSTM) con el historial de ventas que subes.',
          {
            list: [
              'Se entrenan **en nuestro propio servidor**, no en un servicio de terceros: tu historial no se envía a nadie para pronosticar.',
              'Se entrenan **solo con los datos de tu cuenta**. No mezclamos datos de distintas empresas ni usamos los tuyos para los pronósticos de otro cliente.',
              'Los modelos entrenados se guardan en tu cuenta y se borran con ella.',
            ],
          },
        ],
      },
      {
        id: 'language-model',
        title: 'El modelo de lenguaje',
        blocks: [
          'Algunas funciones usan un modelo de lenguaje de **DeepSeek**, un proveedor con sede en China, para escribir texto:',
          {
            table: {
              head: ['Función', 'Cuándo se usa', 'Qué se envía'],
              rows: [
                ['Asistente (en la aplicación y por WhatsApp)', 'Cuando le escribes una pregunta', 'Tu pregunta, las últimas frases de la conversación, tu nombre de pila, tu rol y el de tu empresa, y los datos de tu cuenta que hacen falta para responder: productos, cantidades, semáforo, cobertura, proveedores, plazos, costos y órdenes de compra'],
                ['Preguntas sobre documentos', 'Cuando preguntas sobre documentos que subiste al asistente', 'Tu pregunta y los fragmentos de esos documentos que tienen relación con ella'],
                ['Resumen del Panel de compras', '**Automáticamente al abrir esa pantalla**', 'El resumen del día: productos en riesgo y sus cantidades, sobrestock, montos y proveedores'],
                ['Explicaciones de pronósticos e inventario', 'Cuando pides la explicación', 'Las cifras del producto o del inventario que se explican'],
                ['Diagnóstico de calidad de un archivo', 'Al revisar la calidad de un historial subido', 'Un resumen del archivo (número de filas, fechas, puntuación de calidad) y los avisos de hasta 10 productos con sus códigos. No se envía el archivo.'],
              ],
            },
          },
          'Nunca enviamos contraseñas, claves de API ni credenciales. La conexión con DeepSeek va cifrada (HTTPS).',
          'Si el servicio no tiene configurado DeepSeek, no se envía nada: el asistente avisa que no está disponible y las demás pantallas muestran texto calculado por reglas.',
        ],
      },
      {
        id: 'read-only',
        title: 'El asistente solo lee',
        blocks: [
          'El asistente puede consultar tu cuenta, pero **no puede actuar**: no aprueba órdenes de compra, no registra recepciones, no cambia datos ni borra nada, ni en la aplicación ni por WhatsApp. Cuando una respuesta implica hacer algo, te dice en qué pantalla hacerlo, y lo haces tú.',
          'Lo mismo vale para el servidor MCP que puedes conectar a tu propio asistente de IA con una clave de API: solo tiene herramientas de lectura.',
        ],
      },
      {
        id: 'checks',
        title: 'Cómo comprobamos lo que dice',
        blocks: [
          'Antes de mostrarte una respuesta del asistente, StockAI compara cada cifra que contiene con los datos que el asistente tenía delante. Si alguna no coincide, le pide que corrija la respuesta una vez; si aún queda alguna cifra que no se puede comprobar, te la señala en la respuesta en vez de ocultarlo.',
          'Esa comprobación se hace en el asistente. Los resúmenes y explicaciones de las demás pantallas se generan con instrucciones de usar solo las cifras que reciben, pero no pasan por esa revisión: contrástalos con los números de la pantalla.',
        ],
      },
      {
        id: 'limits',
        title: 'Límites: la IA se equivoca',
        blocks: [
          'Un modelo de lenguaje puede malinterpretar una pregunta, omitir algo importante o redactar algo que suena seguro y no lo es. Un pronóstico puede fallar porque la demanda cambia por razones que no están en tus datos.',
          'Por eso todo lo que produce la IA en StockAI es **apoyo para decidir**, no una instrucción. Revisa las cifras y las órdenes antes de actuar. Como dicen los [términos](/terminos#decisions), la decisión de compra es tuya.',
        ],
      },
      {
        id: 'training',
        title: 'Entrenamiento de modelos de terceros',
        blocks: [
          'Nosotros no usamos tus datos para entrenar modelos de lenguaje ni los compartimos para ese fin. DeepSeek recibe los datos para responder a cada petición; lo que DeepSeek hace con ellos se rige por sus propias condiciones. [CONDICIONES DE DEEPSEEK SOBRE USO DE DATOS PARA ENTRENAMIENTO — verificar el contrato o los términos de la API vigentes y resumirlos aquí]',
        ],
      },
      {
        id: 'opt-out',
        title: 'Cómo evitar la IA',
        blocks: [
          {
            list: [
              'Si no escribes al asistente ni pides explicaciones, esas funciones no envían nada.',
              '**El resumen del Panel de compras se pide solo, al abrir esa pantalla.** Si no quieres que esos datos lleguen a DeepSeek, no abras el Panel de compras: el inventario, los pronósticos y las órdenes se pueden usar desde sus propias pantallas.',
              `Hoy la IA no se puede apagar para una sola cuenta desde la aplicación. Si necesitas que tu cuenta no la use en absoluto, escríbenos a ${MAIL} antes de subir datos.`,
            ],
          },
          'Sin la IA sigues teniendo los pronósticos, el semáforo, las recomendaciones de compra, las órdenes y los avisos: todo eso se calcula en StockAI sin el modelo de lenguaje.',
        ],
      },
    ],
  },

  // ── Divulgación responsable ───────────────────────────────────────────────
  disclosure: {
    label: 'Divulgación responsable',
    title: 'Política de divulgación responsable de vulnerabilidades',
    intro: 'Cómo reportarnos un fallo de seguridad en StockAI, qué puedes probar y qué no, y qué hacemos cuando lo recibimos.',
    summary: [
      `Si encuentras una vulnerabilidad, escríbenos a ${MAIL} con el asunto «Vulnerabilidad: …».`,
      'Puedes investigar stockai.es, app.stockai.es y la API, con tus propias cuentas y sin dañar a nadie.',
      'Si sigues estas reglas de buena fe, no emprenderemos acciones legales contra ti por esa investigación.',
      'Danos tiempo para corregirlo antes de publicarlo.',
    ],
    sections: [
      {
        id: 'report',
        title: 'Cómo reportar',
        blocks: [
          `Escríbenos a ${MAIL} con el asunto **«Vulnerabilidad: resumen breve»**. Incluye, si puedes:`,
          {
            list: [
              'qué encontraste y en qué dirección o endpoint;',
              'los pasos para reproducirlo, y una prueba de concepto si la tienes;',
              'qué impacto crees que tiene;',
              'cómo quieres que te mencionemos, si quieres que lo hagamos.',
            ],
          },
          'También publicamos estos datos en [stockai.es/.well-known/security.txt](https://stockai.es/.well-known/security.txt), siguiendo el estándar RFC 9116. Hoy no tenemos una clave PGP; si el informe contiene datos delicados, avísanos y acordamos cómo enviarlo.',
        ],
      },
      {
        id: 'scope',
        title: 'Qué puedes probar',
        blocks: [
          {
            list: [
              '**stockai.es** (el sitio público);',
              '**app.stockai.es** (la aplicación);',
              '**la API** y el servidor MCP que se sirven desde app.stockai.es.',
            ],
          },
          'Usa tus propias cuentas: puedes registrarte o usar una cuenta de prueba. Si necesitas dos cuentas para probar el aislamiento entre empresas, crea dos tuyas.',
        ],
      },
      {
        id: 'out-of-scope',
        title: 'Qué queda fuera',
        blocks: [
          {
            list: [
              'ataques de denegación de servicio o pruebas de carga;',
              'ingeniería social a nuestro equipo o a nuestros clientes, y phishing;',
              'ataques físicos a oficinas o equipos;',
              'los servicios de nuestros proveedores (Hostinger, DeepSeek, Resend, Twilio): repórtaselos a ellos;',
              'informes de escáneres automáticos sin una explotación demostrada, y la falta de buenas prácticas sin impacto real (por ejemplo, una cabecera ausente que no se pueda explotar).',
            ],
          },
        ],
      },
      {
        id: 'rules',
        title: 'Reglas para investigar',
        blocks: [
          {
            list: [
              'No accedas, cambies ni borres datos que no son tuyos. Si te encuentras datos de otra persona, detente, no los guardes y avísanos.',
              'Respeta los límites de la API y no hagas pruebas que degraden el servicio para los demás.',
              'No uses lo que encuentres para nada más que demostrar el problema.',
              'Mantén el hallazgo en reserva hasta que lo hayamos corregido o hasta que pasen [PLAZO DE DIVULGACIÓN — p. ej. 90 días, confirmar] desde tu informe, lo que ocurra antes, y coordina la publicación con nosotros.',
            ],
          },
        ],
      },
      {
        id: 'safe-harbour',
        title: 'Protección para quien investiga de buena fe',
        blocks: [
          'Si investigas y reportas de buena fe siguiendo esta política, consideraremos tu investigación autorizada, no emprenderemos acciones legales contra ti por ella y, si un tercero lo hace, dejaremos claro que actuaste con nuestro permiso. [ALCANCE DEL PUERTO SEGURO — revisar con un abogado]',
          'Esta protección no cubre a quien incumple las reglas de arriba, por ejemplo accediendo a datos de otros clientes más allá de lo necesario para demostrar el problema.',
        ],
      },
      {
        id: 'response',
        title: 'Qué hacemos nosotros',
        blocks: [
          {
            list: [
              'Acusamos recibo de tu informe en un plazo de [PLAZO DE ACUSE DE RECIBO — confirmar].',
              'Te damos una primera valoración en un plazo de [PLAZO DE VALORACIÓN — confirmar].',
              'Te mantenemos al tanto mientras lo corregimos y te avisamos cuando esté resuelto.',
              'Si quieres, te mencionamos como descubridor cuando lo publiquemos.',
            ],
          },
          'No tenemos un programa de recompensas económicas. [PROGRAMA DE RECOMPENSAS — confirmar con el propietario]',
        ],
      },
    ],
  },

  // ── Accesibilidad ─────────────────────────────────────────────────────────
  accessibility: {
    label: 'Accesibilidad',
    title: 'Declaración de accesibilidad',
    intro: 'Qué hacemos para que StockAI se pueda usar con teclado, lector de pantalla, zoom o movimiento reducido, qué falta todavía y cómo avisarnos de un problema.',
    summary: [
      'Nuestro objetivo es cumplir las pautas **WCAG 2.1 en el nivel AA**.',
      'Hoy cumplimos **parcialmente**: hay partes que aún no son accesibles, y las listamos abajo.',
      'No hemos hecho una auditoría externa; esta declaración se basa en nuestra propia revisión.',
      `Si algo te impide usar StockAI, escríbenos a ${MAIL}.`,
    ],
    sections: [
      {
        id: 'commitment',
        title: 'Nuestro compromiso',
        blocks: [
          'Queremos que cualquier persona pueda usar StockAI, también desde el teléfono en una bodega, con poca luz o con una tecnología de apoyo. Tomamos como referencia las Pautas de Accesibilidad para el Contenido Web (WCAG) 2.1, nivel AA, para el sitio stockai.es y la aplicación app.stockai.es.',
        ],
      },
      {
        id: 'done',
        title: 'Lo que ya hacemos',
        blocks: [
          {
            list: [
              '**Zoom:** la página no bloquea el zoom con los dedos en el teléfono.',
              '**Objetivos táctiles:** en el teléfono, los botones y enlaces miden al menos 44 píxeles de alto.',
              '**Movimiento reducido:** si tu sistema pide reducir el movimiento, las animaciones se desactivan.',
              '**Teclado:** el elemento con el foco se marca con un contorno visible, que se mantiene en el modo de alto contraste de Windows.',
              '**Diálogos:** las ventanas de confirmación y las hojas del teléfono retienen el foco mientras están abiertas, se cierran con Esc y devuelven el foco a donde estaba.',
              '**Lectores de pantalla:** los diálogos, avisos y pestañas usan roles ARIA; los mensajes de estado se anuncian; las tablas ordenables indican el orden actual.',
              '**Color:** el semáforo de stock muestra siempre un icono y el nombre del estado, no solo el color, y sus colores están ajustados a un contraste de al menos 4,5:1.',
              '**Idioma:** puedes usar StockAI en español o en inglés.',
            ],
          },
        ],
      },
      {
        id: 'limitations',
        title: 'Limitaciones conocidas',
        blocks: [
          {
            list: [
              '**Gráficos:** los gráficos de pronóstico y de patrón de ventas se dibujan como imagen y no tienen todavía una alternativa en texto para lectores de pantalla.',
              '**Idioma de la página:** cuando eliges inglés, la página sigue declarándose en español, así que un lector de pantalla puede pronunciar el texto con la voz equivocada.',
              '**Saltar al contenido:** no hay todavía un enlace para saltar la navegación e ir directo al contenido.',
              '**Documentos PDF:** los manuales descargables pueden no estar etiquetados para lectores de pantalla.',
              'No hemos probado todas las pantallas con lectores de pantalla ni con una auditoría independiente, así que puede haber otros problemas que no conocemos.',
            ],
          },
          'Estas limitaciones están en nuestra lista de trabajo. [FECHA PREVISTA DE CORRECCIÓN — confirmar con el propietario]',
        ],
      },
      {
        id: 'report',
        title: 'Cómo avisarnos de un problema',
        blocks: [
          `Escríbenos a ${MAIL} con el asunto «Accesibilidad». Cuéntanos en qué pantalla estabas, qué intentabas hacer y qué tecnología usas (por ejemplo, el lector de pantalla y el navegador). Te respondemos en un plazo de [PLAZO DE RESPUESTA — confirmar] y, mientras lo corregimos, buscamos otra forma de darte la información o hacer la tarea.`,
        ],
      },
      {
        id: 'statement',
        title: 'Sobre esta declaración',
        blocks: [
          'Esta declaración se preparó el 2 de octubre de 2026 a partir de una revisión del propio equipo de StockAI. La revisaremos cuando cambie la aplicación de forma importante.',
        ],
      },
    ],
  },

  // ── Condiciones comerciales ───────────────────────────────────────────────
  commercial: {
    label: 'Compra del código fuente',
    title: 'Compra del código fuente',
    intro: 'Cómo se compra StockAI: se vende únicamente como código fuente, en un pago único.',
    summary: [
      'StockAI se vende **únicamente como código fuente**, para instalarlo en tu propia infraestructura y operarlo como tuyo.',
      'El precio es de **USD 14.999**, en un **pago único**.',
      'Los detalles de la entrega se acuerdan escribiéndonos.',
    ],
    sections: [
      {
        id: 'plans',
        title: 'Qué se vende y a qué precio',
        blocks: [
          'Se vende el código fuente completo de StockAI, con todas las funciones del producto, incluidas la API, el servidor MCP y el bot de WhatsApp. El precio es de **USD 14.999**, en un pago único.',
          'Lo instalas en tu propia infraestructura y lo operas tú.',
        ],
      },
      {
        id: 'full',
        title: 'Cómo se cierra la compra',
        blocks: [
          `Escríbenos a ${MAIL} y cerramos la compra y los detalles de la entrega contigo.`,
          'Licencia: las condiciones de uso del código están en la [licencia de código fuente](/licencia).',
          '[ENTREGA, SOPORTE, FORMA DE PAGO, IMPUESTOS Y REEMBOLSO DE LA COMPRA — confirmar con el propietario]',
        ],
      },
      {
        id: 'trial',
        title: 'La cuenta de prueba',
        blocks: [
          'Puedes entrar sin registrarte a una cuenta de prueba con datos de ejemplo. Dura **24 horas** y luego se borra entera.',
        ],
      },
    ],
  },
  // ── Licencia de código fuente ─────────────────────────────────────────────
  license: {
    label: 'Licencia de código fuente',
    title: 'Licencia de código fuente de StockAI',
    intro: 'Las condiciones con las que una organización puede instalar, usar y modificar su propia copia del código de StockAI.',
    summary: [
      'Es una **licencia comercial y privada**, no de código abierto. Solo tiene derechos quien firmó un contrato de licencia con nosotros.',
      'Puedes instalar StockAI, usarlo, modificarlo para uso interno y alojarlo para tu propio negocio.',
      '**No puedes** revenderlo, redistribuirlo, sublicenciarlo ni ofrecerlo como servicio a terceros sin un acuerdo escrito.',
      'El código sigue siendo nuestro y es confidencial. Los componentes de código abierto que incluye conservan sus propias licencias.',
    ],
    sections: [
      {
        id: 'scope',
        title: 'Qué es y a quién se aplica',
        blocks: [
          'Estas condiciones rigen la copia del código fuente de StockAI que [NOMBRE DEL LICENCIANTE] (en adelante, «el licenciante», «nosotros») entrega a una organización que la contrata (en adelante, «el licenciatario», «tú»). Se aplican junto con el contrato de licencia que firmamos contigo; **si el contrato dice algo distinto, prevalece el contrato**.',
          'El «software» es el código fuente y el código compilado de StockAI —la aplicación web, la API, el motor de pronóstico—, su documentación y las actualizaciones que te entreguemos. Estas condiciones no se aplican al servicio alojado en app.stockai.es, que se rige por los [términos del servicio](/terminos).',
          'Sin un contrato de licencia firmado no tienes ningún derecho sobre el software, aunque hayas obtenido una copia.',
        ],
      },
      {
        id: 'grant',
        title: 'Lo que puedes hacer',
        blocks: [
          'Mientras el contrato esté vigente y cumplas estas condiciones, te damos una licencia **no exclusiva, intransferible y para tu organización** para:',
          {
            list: [
              '**instalar y ejecutar** el software en servidores tuyos o de un proveedor de alojamiento que contrates y controles;',
              '**modificarlo** para adaptarlo a tus necesidades internas;',
              '**alojarlo para tu propio negocio**, para que lo usen tus empleados y colaboradores;',
              'hacer las copias que necesites para lo anterior, incluidas copias de seguridad y entornos de prueba.',
            ],
          },
          'Si así lo acordamos por escrito, la licencia puede extenderse a tus sociedades filiales o subsidiarias. El número de instalaciones, organizaciones o usuarios, el precio y la duración son los del contrato: [PRECIO/VIGENCIA según contrato].',
        ],
      },
      {
        id: 'restrictions',
        title: 'Lo que no puedes hacer',
        blocks: [
          'Salvo que lo acordemos por escrito, no puedes:',
          {
            list: [
              'vender, revender, alquilar, prestar o ceder el software o tu licencia;',
              'redistribuirlo o publicarlo, completo o en parte, con o sin cambios, incluso en repositorios públicos;',
              'sublicenciarlo o permitir que lo use otra organización;',
              'ofrecerlo a terceros como servicio alojado o en la nube (SaaS), ni operarlo por cuenta de clientes tuyos;',
              'usarlo, o usar lo que aprendas de él, para crear un producto que compita con StockAI;',
              'quitar o cambiar los avisos de copyright, licencia o marca del software;',
              'usar o compartir las claves, credenciales o cuentas de nuestros servicios. Para el asistente, el correo o WhatsApp debes contratar y configurar tus propias cuentas con esos proveedores.',
            ],
          },
        ],
      },
      {
        id: 'ownership',
        title: 'Propiedad',
        blocks: [
          'El software, sus copias y la marca StockAI son y siguen siendo del licenciante. Te licenciamos el software, no te lo vendemos, y no adquieres ningún derecho que no esté escrito aquí o en el contrato.',
          'Las modificaciones que hagas solo puedes usarlas junto con el software y dentro de esta licencia. [TITULARIDAD DE LAS MODIFICACIONES DEL LICENCIATARIO — confirmar con un abogado]',
          'Si nos envías sugerencias o correcciones, podemos usarlas sin obligación hacia ti.',
        ],
      },
      {
        id: 'updates',
        title: 'Actualizaciones y soporte',
        blocks: [
          'Solo te entregamos actualizaciones, correcciones o soporte si el contrato lo prevé, y en los términos que diga. Sin eso, recibes el software en la versión entregada y no tenemos obligación de mantenerlo. Las actualizaciones que te entreguemos quedan cubiertas por esta misma licencia.',
        ],
      },
      {
        id: 'confidentiality',
        title: 'Confidencialidad del código',
        blocks: [
          'El código fuente es información confidencial nuestra. Debes protegerlo al menos con el mismo cuidado que tu propia información confidencial, y solo pueden acceder a él tus empleados y contratistas que lo necesiten para los usos permitidos y que estén obligados a guardar confidencialidad.',
          'Esta obligación sigue vigente después de que termine la licencia. [PLAZO DE CONFIDENCIALIDAD TRAS LA TERMINACIÓN — confirmar]',
        ],
      },
      {
        id: 'third-party',
        title: 'Componentes de código abierto',
        blocks: [
          'El software usa componentes de código abierto de terceros (por ejemplo, Next.js, React, FastAPI, pandas, LightGBM o Prophet). **Esos componentes no son nuestros ni están cubiertos por esta licencia**: cada uno sigue bajo su propia licencia (MIT, BSD, Apache 2.0 y otras), y sus condiciones prevalecen sobre estas para ese componente.',
          'La lista de componentes directos y su licencia está en el archivo `THIRD_PARTY_NOTICES.md` que se entrega con el código. Si la necesitas antes, pídela a [contacto@stockai.es](mailto:contacto@stockai.es).',
        ],
      },
      {
        id: 'warranty',
        title: 'Garantía',
        blocks: [
          'Salvo lo que diga expresamente el contrato, el software se entrega **«tal cual»**, sin garantías de ningún tipo, expresas o implícitas, incluidas las de comerciabilidad, idoneidad para un fin concreto y no infracción. Los pronósticos que produce son estimaciones y las decisiones que tomes con ellos son tuyas.',
          'Tú eres responsable de instalar, operar, respaldar y asegurar tu copia, y de la configuración y los proveedores que elijas.',
        ],
      },
      {
        id: 'liability',
        title: 'Límite de responsabilidad',
        blocks: [
          'En la medida en que la ley lo permita, no respondemos por daños indirectos, lucro cesante, pérdida de datos o de ventas, ni por decisiones comerciales tomadas con el software, y nuestra responsabilidad total por esta licencia queda limitada a lo que nos pagaste por ella en los 12 meses anteriores al hecho que la origina.',
          'Nada de esto limita la responsabilidad que la ley aplicable no permita limitar.',
        ],
      },
      {
        id: 'audit',
        title: 'Verificación del cumplimiento',
        blocks: [
          'Con un aviso razonable de [PREAVISO — confirmar], podemos pedirte una declaración escrita de que cumples esta licencia (por ejemplo, cuántas instalaciones tienes y quién las usa) y, si hay motivos fundados, verificarlo nosotros o un auditor obligado a confidencialidad, en horario laboral y sin interferir de más en tu operación. [FRECUENCIA, COSTO Y CONDICIONES DE LA VERIFICACIÓN — confirmar]',
        ],
      },
      {
        id: 'termination',
        title: 'Terminación',
        blocks: [
          'La licencia termina al vencer el plazo del contrato si no se renueva, o antes si incumples estas condiciones o el contrato y no lo corriges dentro de [PLAZO DE SUBSANACIÓN — confirmar] desde que te avisemos. Un incumplimiento grave de las restricciones o de la confidencialidad la termina sin necesidad de plazo.',
          'Al terminar, debes **dejar de usar el software y borrar todas sus copias**, incluidas las modificadas y las de respaldo, y confirmarnos por escrito que lo hiciste. Los datos de tu negocio que estén en tu instalación son tuyos: expórtalos antes de borrarla.',
          'Las secciones sobre propiedad, confidencialidad, garantía, responsabilidad y ley aplicable siguen vigentes después de la terminación.',
        ],
      },
      {
        id: 'earlier-versions',
        title: 'Versiones anteriores',
        blocks: [
          'Algunas versiones anteriores de este código se distribuyeron con la licencia MIT. Quien recibió esas copias conserva los derechos de la licencia MIT sobre ellas. Las versiones distribuidas desde el 2 de octubre de 2026 se rigen por esta licencia.',
        ],
      },
      {
        id: 'law',
        title: 'Ley aplicable',
        blocks: [
          'Esta licencia se rige por las leyes de [JURISDICCIÓN], y cualquier disputa se resolverá ante los tribunales que indique el contrato o, en su defecto, los de [JURISDICCIÓN].',
        ],
      },
    ],
  },
}

// ─────────────────────────────────────────────────────────────────────────────
// English
// ─────────────────────────────────────────────────────────────────────────────

export const LEGAL_EXTRA_EN: Record<ExtraKey, LegalDoc> = {
  // ── Acceptable use ────────────────────────────────────────────────────────
  acceptableUse: {
    label: 'Acceptable use',
    title: 'Acceptable use policy',
    intro: 'What may not be done with StockAI — in the app, the API, the assistant and trial accounts — and what happens if someone does it.',
    summary: [
      'Use StockAI to manage your own inventory, with data you have the right to use.',
      'Do not abuse trial accounts or the API, do not copy the service and do not use it to send unwanted messages.',
      'If you find a security flaw, report it under the [responsible disclosure policy](/divulgacion-responsable) instead of exploiting it.',
      'If someone breaks this policy, we may revoke keys, limit or suspend the account, as the [terms](/terminos#termination) say.',
    ],
    sections: [
      {
        id: 'scope',
        title: 'What it applies to',
        blocks: [
          'This policy expands on the acceptable-use section of the [terms of service](/terminos#acceptable-use) and is part of them. It applies to everyone who uses StockAI: the web app, the API, the MCP server, the assistant (in the app or over WhatsApp) and trial accounts.',
          'If you administer an account, you are also responsible for what the people you invite and the integrations you give an API key to do.',
        ],
      },
      {
        id: 'trial',
        title: 'Trial accounts',
        blocks: [
          {
            list: [
              'The trial account is for getting to know StockAI. It lasts 24 hours and its limits are small on purpose.',
              'Do not create trial account after trial account to work in them continuously or to get around the trial account\'s limits. We count the trial accounts created from each IP address to stop that abuse.',
              'Do not upload to a trial account data you do not want to lose or sensitive personal data: it is erased entirely when it ends.',
            ],
          },
        ],
      },
      {
        id: 'api',
        title: 'The API and the MCP server',
        blocks: [
          {
            list: [
              'Respect the limits: up to 120 calls per minute per key. Do not spread the work across several accounts or keys to get around them.',
              'Each key is a credential. Do not publish it, do not put it in code others can see and do not share it outside your company.',
              'Use the API to connect your own systems. Do not use it to offer third parties a service built on StockAI without a written agreement with us.',
            ],
          },
        ],
      },
      {
        id: 'scraping',
        title: 'Automated extraction and copying the service',
        blocks: [
          'To automate, use the API. Do not extract data from the app or the website with bots or scripts that imitate a user, except normal search-engine indexing of the public pages.',
          'Do not try to copy the hosted service: decompiling or deobfuscating the code served to the browser, bypassing access controls, calling addresses that are not public or rebuilding the product to compete with it. This does not limit what a written licence you have signed separately with us allows.',
        ],
      },
      {
        id: 'content',
        title: 'What you upload',
        blocks: [
          'The files, documents and data you upload must be yours or you must have the right to use them. In particular, do not upload:',
          {
            list: [
              'illegal content, or data obtained without the right to it (for example, another company\'s customer base);',
              'personal data you do not need to manage your inventory, let alone sensitive data (health, people\'s financial data, identity documents);',
              'files containing malware or crafted to attack the service.',
            ],
          },
        ],
      },
      {
        id: 'messages',
        title: 'Messages to third parties',
        blocks: [
          'StockAI can email purchase orders to your suppliers, and send alerts by email, WhatsApp or SMS. Those messages go out in your name, so:',
          {
            list: [
              'send them only to suppliers and contacts you have a business relationship with;',
              'do not use them for advertising, unsolicited bulk messages or to impersonate another person or company;',
              'if someone asks you to stop writing to them, stop.',
            ],
          },
        ],
      },
      {
        id: 'security',
        title: 'Security testing',
        blocks: [
          'Do not test the security of the service, scan our servers or try to get into other people\'s accounts or data, except within the rules of the [responsible disclosure policy](/divulgacion-responsable), which gives you permission to research in good faith within certain limits.',
          'Flooding the service (denial of service) or deceiving our team or other customers to gain access is never allowed.',
        ],
      },
      {
        id: 'enforcement',
        title: 'What we do if it is broken',
        blocks: [
          'Depending on how serious it is, we may delete the offending content, revoke an API key, limit features, erase a trial account early, or suspend or close the account, as provided in the [suspension and termination](/terminos#termination) section of the terms. Except in an emergency — for example, an ongoing attack or a risk to other customers — we warn you first and give you a chance to fix it.',
          'If the breach may be a crime, we may report it to the authorities.',
        ],
      },
      {
        id: 'report',
        title: 'How to report abuse',
        blocks: [
          `If you see someone using StockAI against this policy — for example, you received an unwanted email sent from StockAI — write to us at ${MAIL} with what you know: the message received, the date and the sending address.`,
        ],
      },
    ],
  },

  // ── DPA ───────────────────────────────────────────────────────────────────
  dpa: {
    label: 'Data processing (DPA)',
    title: 'Data processing agreement',
    intro: 'The terms under which StockAI processes, on your company\'s behalf, the personal data contained in the business data you upload.',
    summary: [
      'For the business data you upload, **your company is the controller** and StockAI is the **processor**: we process it only to provide the service and on your instructions.',
      'We tell you which providers (sub-processors) we use and where they are, and we warn you before adding one.',
      'If a security breach affects your data, we notify you within [PLAZO DE NOTIFICACIÓN — confirmar].',
      'At the end, we hand you a complete copy of your data and delete it.',
    ],
    sections: [
      {
        id: 'parties',
        title: 'Parties and purpose',
        blocks: [
          'This agreement is between the customer company that uses StockAI ("the customer") and [RAZÓN SOCIAL], [CÉDULA JURÍDICA / NIF] ("StockAI"). It is part of the [terms of service](/terminos) and applies from the moment the customer creates an account and uploads data.',
          'If the customer needs a signed version of this agreement, it can ask for one at ' + MAIL + '. [VERSIÓN FIRMADA — confirmar si se ofrece y quién la firma]',
          'Where this agreement contradicts the terms on personal data, this agreement prevails.',
        ],
      },
      {
        id: 'roles',
        title: 'Who is controller and who is processor',
        blocks: [
          '**The customer is the controller** of the personal data contained in the business data it uploads or connects: it decides which data is loaded and why. **StockAI is the processor**: it processes that data on the customer\'s behalf, only to provide the service.',
          'For user-account data (name, email, WhatsApp number) that we use to run the service — for example, for sign-in and service emails — StockAI acts as controller, as the [privacy policy](/privacidad) explains.',
        ],
      },
      {
        id: 'details',
        title: 'Data, people and purpose',
        blocks: [
          {
            table: {
              head: ['Aspect', 'Detail'],
              rows: [
                ['Data subjects', 'Users of the customer\'s account; contacts at its suppliers; the customer\'s own customers when they appear in the sales history.'],
                ['Types of data', 'Contact names, emails and phone numbers; customer names or codes in sales; any other personal data the customer includes in its files, documents or connections.'],
                ['Purpose', 'Providing the service: storing the data, training the account\'s forecasts, computing the semáforo, generating and sending purchase orders, answering the assistant and showing the results.'],
                ['Operations', 'Storage, retrieval, computation, sending the messages the customer turns on, backup and deletion.'],
                ['Duration', 'For as long as the account exists, plus the backup period (14 days).'],
              ],
            },
          },
          'The customer should not upload personal data it does not need to manage its inventory, and in particular special-category data (health, ethnic origin, biometric data and the like).',
        ],
      },
      {
        id: 'instructions',
        title: 'The customer\'s instructions',
        blocks: [
          'StockAI processes the data only on the customer\'s documented instructions. The instructions are: the terms, this agreement, and what the customer configures and does in the app or through the API (for example, uploading a file, turning on a WhatsApp alert or requesting an export).',
          'If an instruction seems unlawful to us, we will tell the customer before following it. If a law requires us to process data differently, we will tell the customer first, unless that same law forbids it.',
        ],
      },
      {
        id: 'confidentiality',
        title: 'Confidentiality',
        blocks: [
          'Only the StockAI staff who need it to run, maintain or support the service have access to the customer\'s data, and they are bound to confidentiality. [COMPROMISOS DE CONFIDENCIALIDAD DEL PERSONAL — confirmar cómo se documentan]',
          'We do not look at an account\'s data except to solve a problem the customer raises, to maintain the service or when the law requires it.',
        ],
      },
      {
        id: 'security',
        title: 'Security measures',
        blocks: [
          'StockAI applies, at least, these measures:',
          {
            list: [
              '**Isolation per account:** every database query is filtered by the account of whoever makes it; one company cannot see another\'s data.',
              '**Role permissions:** admin, analyst and viewer. Only an analyst or an admin can change data, and only an admin can export or delete the whole account.',
              '**Passwords hashed with bcrypt** and API keys stored only as a hash: nobody can read them.',
              '**Credentials encrypted with Fernet:** the passwords of databases and services the customer connects are stored encrypted.',
              '**Short sessions:** access expires after 15 minutes and renews while the tab is open; changing the password closes earlier sessions.',
              '**Encrypted connection (TLS):** traffic with the app and the API goes over HTTPS, with certificates managed by the web server (Caddy).',
              '**Nightly database backups**, kept for 14 days.',
              '**Trial accounts** erased entirely after 24 hours.',
            ],
          },
          'StockAI holds no certifications today (such as ISO 27001 or SOC 2). We may change these measures as long as the level of protection does not drop.',
        ],
      },
      {
        id: 'subprocessors',
        title: 'Sub-processors',
        blocks: [
          'The customer authorises StockAI to use these sub-processors, each only for its task:',
          {
            table: {
              head: ['Sub-processor', 'What for', 'When', 'Where'],
              rows: [
                ['Hostinger', 'Server (VPS) running the app and the database; stockai.es email', 'Always', '[UBICACIÓN DEL SERVIDOR — confirmar con el propietario]'],
                ['DeepSeek', 'Language model behind the assistant, summaries, explanations and file diagnosis (see [how we use AI](/ia))', 'Only when those features are used', 'China'],
                ['Email provider: Hostinger (SMTP) or Resend, depending on the configuration', 'Sending service emails, alerts and purchase orders', 'When an email is sent', '[UBICACIÓN — confirmar con el propietario]'],
                ['Twilio', 'Sending and receiving WhatsApp and SMS messages', 'Only if WhatsApp or SMS is turned on', 'United States'],
              ],
            },
          },
          'We require each sub-processor to meet data-protection obligations equivalent to those in this agreement, as far as its terms allow. [CONTRATOS CON SUBENCARGADOS — confirmar que existen y dónde constan]',
          'Before adding or changing a sub-processor, we update this list and notify the customer by email [PLAZO DE AVISO — confirmar] in advance. If the customer objects on reasonable grounds and we cannot find a solution, it may end the service without penalty.',
        ],
      },
      {
        id: 'transfers',
        title: 'International transfers',
        blocks: [
          'Because of the sub-processors above, data may be processed outside the customer\'s country: in [UBICACIÓN DEL SERVIDOR — confirmar con el propietario], in China (DeepSeek, only if the AI features are used) and in the United States (Twilio, only if WhatsApp or SMS is turned on).',
          'For customers in the European Union or Spain: China has no adequacy decision from the European Commission. [MECANISMO DE TRANSFERENCIA — cláusulas contractuales tipo u otro, confirmar con un abogado para DeepSeek y Twilio]. If the customer cannot accept the transfer to China, it can ask us for its account not to use the AI features by writing to ' + MAIL + '.',
        ],
      },
      {
        id: 'breaches',
        title: 'Security breaches',
        blocks: [
          'If StockAI becomes aware of a security breach affecting the customer\'s personal data, it will notify the customer without undue delay and within [PLAZO DE NOTIFICACIÓN — confirmar] of becoming aware, at the account administrator\'s email.',
          'The notice will say, as far as is known at the time: what happened, which data and how many people may be affected, the likely consequences, what we have done and what we recommend doing. Whatever is not known at first we will send as soon as we know it.',
          'The customer, as controller, decides whether to notify the authority and the people affected; we will give it the information it needs to do so.',
        ],
      },
      {
        id: 'assistance',
        title: 'Help with people\'s rights',
        blocks: [
          'If a person asks us to access, correct or delete their data held in the customer\'s account, we will pass the request to the customer and not answer it ourselves, unless the law requires us to.',
          'The customer can correct most data from the app. To answer a request, an admin can download a complete copy of the account, and we will help with whatever it cannot do itself. We will also help, within reason, with impact assessments and consultations with the authority.',
        ],
      },
      {
        id: 'end',
        title: 'Return and deletion at the end',
        blocks: [
          `When the service ends, the customer can ask for a complete copy of its data: a ZIP file with every table of its company (without passwords, key hashes or secrets). An admin can download it directly through the API, or ask us at ${MAIL}.`,
          'Then we delete the account: all its tables and files are removed at once. Backups that still contain them disappear on their own within 14 days. We will only keep something if a law requires it, and only what that law requires.',
        ],
      },
      {
        id: 'audits',
        title: 'Audits',
        blocks: [
          'On the customer\'s reasonable request, we will provide the information needed to show that we comply with this agreement: for example, the description of the security measures and the list of sub-processors.',
          'If that is not enough, the customer may carry out an audit, itself or through an auditor bound to confidentiality, with [PREAVISO — confirmar] notice, during business hours, without affecting the service or other customers\' data, and at its own cost. [FRECUENCIA Y CONDICIONES DE AUDITORÍA — confirmar]',
        ],
      },
      {
        id: 'liability',
        title: 'Liability',
        blocks: [
          'Each party\'s liability under this agreement is governed by the [limitation of liability](/terminos#liability) section of the terms, except where applicable law does not allow it to be limited.',
        ],
      },
      {
        id: 'law',
        title: 'Legal framework',
        blocks: [
          'This agreement is based on Costa Rica\'s Law 8968 (Protection of Persons with regard to the Processing of their Personal Data). For customers in the European Union or Spain, it also serves as the processor contract required by Article 28 of the General Data Protection Regulation (GDPR) and is interpreted in accordance with it and with Organic Law 3/2018 (LOPDGDD).',
        ],
      },
    ],
  },

  // ── AI ────────────────────────────────────────────────────────────────────
  ai: {
    label: 'Artificial intelligence',
    title: 'How StockAI uses artificial intelligence',
    intro: 'Which features use artificial intelligence, what data leaves StockAI for it, what its limits are and how to avoid it.',
    summary: [
      'There are two kinds of AI in StockAI: **the forecasting models**, trained inside StockAI on your account\'s data, and **a language model (DeepSeek)**, for the assistant and the explanatory texts.',
      'Forecasts never leave StockAI and are never mixed with other companies\'.',
      'The assistant **only reads**: it cannot create, change or delete anything in your account.',
      'AI makes mistakes. The assistant\'s figures are checked against your data, but **the decision is always yours**.',
    ],
    sections: [
      {
        id: 'forecasting',
        title: 'The forecasting models',
        blocks: [
          'To forecast demand, StockAI trains statistical and machine-learning models (such as LightGBM, XGBoost, Prophet, ARIMA, ETS, Croston and LSTM networks) on the sales history you upload.',
          {
            list: [
              'They are trained **on our own server**, not on a third-party service: your history is not sent to anyone to be forecast.',
              'They are trained **only on your account\'s data**. We do not mix data from different companies or use yours for another customer\'s forecasts.',
              'The trained models are stored in your account and deleted with it.',
            ],
          },
        ],
      },
      {
        id: 'language-model',
        title: 'The language model',
        blocks: [
          'Some features use a language model from **DeepSeek**, a provider based in China, to write text:',
          {
            table: {
              head: ['Feature', 'When it is used', 'What is sent'],
              rows: [
                ['Assistant (in the app and over WhatsApp)', 'When you write it a question', 'Your question, the last lines of the conversation, your first name, your role and your company\'s name, and the account data needed to answer: products, quantities, semáforo, cover, suppliers, lead times, costs and purchase orders'],
                ['Questions about documents', 'When you ask about documents you uploaded to the assistant', 'Your question and the passages of those documents related to it'],
                ['Purchasing Panel summary', '**Automatically when that screen opens**', 'The day\'s summary: products at risk and their quantities, overstock, amounts and suppliers'],
                ['Forecast and inventory explanations', 'When you ask for the explanation', 'The figures of the product or inventory being explained'],
                ['File quality diagnosis', 'When the quality of an uploaded history is reviewed', 'A summary of the file (number of rows, dates, quality score) and the warnings for up to 10 products with their codes. The file itself is not sent.'],
              ],
            },
          },
          'We never send passwords, API keys or credentials. The connection to DeepSeek is encrypted (HTTPS).',
          'If the service has no DeepSeek configured, nothing is sent: the assistant says it is unavailable and the other screens show rule-based text.',
        ],
      },
      {
        id: 'read-only',
        title: 'The assistant only reads',
        blocks: [
          'The assistant can look things up in your account, but **it cannot act**: it does not approve purchase orders, register receptions, change data or delete anything, neither in the app nor over WhatsApp. When an answer involves doing something, it tells you which screen to do it on, and you do it.',
          'The same holds for the MCP server you can connect to your own AI assistant with an API key: it only has read tools.',
        ],
      },
      {
        id: 'checks',
        title: 'How we check what it says',
        blocks: [
          'Before showing you an assistant reply, StockAI compares every figure in it with the data the assistant had in front of it. If one does not match, it asks the assistant to correct the reply once; if a figure still cannot be verified, the reply points it out to you instead of hiding it.',
          'That check is done in the assistant. The summaries and explanations on other screens are generated with instructions to use only the figures they receive, but they do not go through that review: compare them with the numbers on the screen.',
        ],
      },
      {
        id: 'limits',
        title: 'Limits: AI makes mistakes',
        blocks: [
          'A language model can misread a question, leave out something important or write something that sounds certain and is not. A forecast can be wrong because demand changes for reasons that are not in your data.',
          'That is why everything AI produces in StockAI is **support for deciding**, not an instruction. Check the figures and the orders before acting. As the [terms](/terminos#decisions) say, the purchasing decision is yours.',
        ],
      },
      {
        id: 'training',
        title: 'Training third-party models',
        blocks: [
          'We do not use your data to train language models, nor do we share it for that purpose. DeepSeek receives the data to answer each request; what DeepSeek does with it is governed by its own terms. [CONDICIONES DE DEEPSEEK SOBRE USO DE DATOS PARA ENTRENAMIENTO — verificar el contrato o los términos de la API vigentes y resumirlos aquí]',
        ],
      },
      {
        id: 'opt-out',
        title: 'How to avoid AI',
        blocks: [
          {
            list: [
              'If you do not write to the assistant or ask for explanations, those features send nothing.',
              '**The Purchasing Panel summary is requested on its own, when that screen opens.** If you do not want that data to reach DeepSeek, do not open the Purchasing Panel: inventory, forecasts and orders can all be used from their own screens.',
              `Today AI cannot be turned off for a single account from the app. If you need your account not to use it at all, write to us at ${MAIL} before uploading data.`,
            ],
          },
          'Without AI you still have forecasts, the semáforo, purchase recommendations, orders and alerts: all of that is computed inside StockAI without the language model.',
        ],
      },
    ],
  },

  // ── Responsible disclosure ────────────────────────────────────────────────
  disclosure: {
    label: 'Responsible disclosure',
    title: 'Vulnerability disclosure policy',
    intro: 'How to report a security flaw in StockAI to us, what you may and may not test, and what we do when we receive it.',
    summary: [
      `If you find a vulnerability, write to us at ${MAIL} with the subject "Vulnerability: …".`,
      'You may research stockai.es, app.stockai.es and the API, with your own accounts and without harming anyone.',
      'If you follow these rules in good faith, we will not take legal action against you for that research.',
      'Give us time to fix it before publishing it.',
    ],
    sections: [
      {
        id: 'report',
        title: 'How to report',
        blocks: [
          `Write to us at ${MAIL} with the subject **"Vulnerability: short summary"**. Include, if you can:`,
          {
            list: [
              'what you found and at which address or endpoint;',
              'the steps to reproduce it, and a proof of concept if you have one;',
              'what impact you think it has;',
              'how you would like to be credited, if at all.',
            ],
          },
          'We also publish these details at [stockai.es/.well-known/security.txt](https://stockai.es/.well-known/security.txt), following the RFC 9116 standard. We do not have a PGP key today; if the report contains sensitive data, let us know and we will agree how to send it.',
        ],
      },
      {
        id: 'scope',
        title: 'What you may test',
        blocks: [
          {
            list: [
              '**stockai.es** (the public website);',
              '**app.stockai.es** (the application);',
              '**the API** and the MCP server served from app.stockai.es.',
            ],
          },
          'Use your own accounts: you can sign up or use a trial account. If you need two accounts to test the isolation between companies, create two of your own.',
        ],
      },
      {
        id: 'out-of-scope',
        title: 'What is out of scope',
        blocks: [
          {
            list: [
              'denial-of-service attacks or load testing;',
              'social engineering of our team or our customers, and phishing;',
              'physical attacks on offices or equipment;',
              'our providers\' services (Hostinger, DeepSeek, Resend, Twilio): report those to them;',
              'automated scanner reports without a demonstrated exploit, and missing best practices with no real impact (for example, an absent header that cannot be exploited).',
            ],
          },
        ],
      },
      {
        id: 'rules',
        title: 'Rules for research',
        blocks: [
          {
            list: [
              'Do not access, change or delete data that is not yours. If you come across someone else\'s data, stop, do not keep it and tell us.',
              'Respect the API limits and do not run tests that degrade the service for others.',
              'Do not use what you find for anything other than demonstrating the problem.',
              'Keep the finding confidential until we have fixed it or until [PLAZO DE DIVULGACIÓN — p. ej. 90 días, confirmar] have passed since your report, whichever comes first, and coordinate publication with us.',
            ],
          },
        ],
      },
      {
        id: 'safe-harbour',
        title: 'Protection for good-faith research',
        blocks: [
          'If you research and report in good faith following this policy, we will consider your research authorised, we will not take legal action against you for it and, if a third party does, we will make clear that you acted with our permission. [ALCANCE DEL PUERTO SEGURO — revisar con un abogado]',
          'This protection does not cover anyone who breaks the rules above, for example by accessing other customers\' data beyond what is needed to demonstrate the problem.',
        ],
      },
      {
        id: 'response',
        title: 'What we do',
        blocks: [
          {
            list: [
              'We acknowledge your report within [PLAZO DE ACUSE DE RECIBO — confirmar].',
              'We give you a first assessment within [PLAZO DE VALORACIÓN — confirmar].',
              'We keep you informed while we fix it and tell you when it is resolved.',
              'If you wish, we credit you as the finder when we publish it.',
            ],
          },
          'We do not have a monetary reward programme. [PROGRAMA DE RECOMPENSAS — confirmar con el propietario]',
        ],
      },
    ],
  },

  // ── Accessibility ─────────────────────────────────────────────────────────
  accessibility: {
    label: 'Accessibility',
    title: 'Accessibility statement',
    intro: 'What we do so StockAI can be used with a keyboard, a screen reader, zoom or reduced motion, what is still missing and how to tell us about a problem.',
    summary: [
      'Our goal is to meet the **WCAG 2.1 guidelines at level AA**.',
      'Today we are **partially conformant**: some parts are not yet accessible, and we list them below.',
      'We have not had an external audit; this statement is based on our own review.',
      `If something stops you from using StockAI, write to us at ${MAIL}.`,
    ],
    sections: [
      {
        id: 'commitment',
        title: 'Our commitment',
        blocks: [
          'We want anyone to be able to use StockAI, including from a phone in a warehouse, in poor light or with assistive technology. We take the Web Content Accessibility Guidelines (WCAG) 2.1, level AA, as the reference for the stockai.es website and the app.stockai.es application.',
        ],
      },
      {
        id: 'done',
        title: 'What we already do',
        blocks: [
          {
            list: [
              '**Zoom:** the page does not block pinch-zoom on phones.',
              '**Touch targets:** on phones, buttons and links are at least 44 pixels tall.',
              '**Reduced motion:** if your system asks for reduced motion, animations are turned off.',
              '**Keyboard:** the focused element is marked with a visible outline, which survives Windows high-contrast mode.',
              '**Dialogs:** confirmation dialogs and phone sheets keep focus inside while open, close with Esc and return focus to where it was.',
              '**Screen readers:** dialogs, alerts and tabs use ARIA roles; status messages are announced; sortable tables state the current sort.',
              '**Colour:** the stock semáforo always shows an icon and the state\'s name, not just a colour, and its colours are tuned to a contrast of at least 4.5:1.',
              '**Language:** you can use StockAI in Spanish or English.',
            ],
          },
        ],
      },
      {
        id: 'limitations',
        title: 'Known limitations',
        blocks: [
          {
            list: [
              '**Charts:** the forecast and sales-pattern charts are drawn as images and do not yet have a text alternative for screen readers.',
              '**Page language:** when you choose English, the page still declares itself as Spanish, so a screen reader may read the text with the wrong voice.',
              '**Skip to content:** there is no link yet to skip the navigation and go straight to the content.',
              '**PDF documents:** the downloadable manuals may not be tagged for screen readers.',
              'We have not tested every screen with screen readers or through an independent audit, so there may be other problems we do not know about.',
            ],
          },
          'These limitations are on our work list. [FECHA PREVISTA DE CORRECCIÓN — confirmar con el propietario]',
        ],
      },
      {
        id: 'report',
        title: 'How to tell us about a problem',
        blocks: [
          `Write to us at ${MAIL} with the subject "Accessibility". Tell us which screen you were on, what you were trying to do and what technology you use (for example, the screen reader and the browser). We reply within [PLAZO DE RESPUESTA — confirmar] and, while we fix it, we find another way to give you the information or get the task done.`,
        ],
      },
      {
        id: 'statement',
        title: 'About this statement',
        blocks: [
          'This statement was prepared on 2 October 2026 from a review by the StockAI team itself. We will review it when the application changes significantly.',
        ],
      },
    ],
  },

  // ── Commercial conditions ─────────────────────────────────────────────────
  commercial: {
    label: 'Source code purchase',
    title: 'Source code purchase',
    intro: 'How StockAI is bought: it is sold only as source code, as a one-time payment.',
    summary: [
      'StockAI is sold **only as source code**, to install on your own infrastructure and operate as your own.',
      'The price is **USD 14,999**, as a **one-time payment**.',
      'The details of delivery are agreed by writing to us.',
    ],
    sections: [
      {
        id: 'plans',
        title: 'What is sold and at what price',
        blocks: [
          'What is sold is the complete StockAI source code, with every feature of the product, including the API, the MCP server and the WhatsApp bot. The price is **USD 14,999**, as a one-time payment.',
          'You install it on your own infrastructure and you operate it.',
        ],
      },
      {
        id: 'full',
        title: 'How the purchase is closed',
        blocks: [
          `Write to us at ${MAIL} and we will close the purchase and the details of delivery with you.`,
          'Licence: the terms for using the code are in the [source-code licence](/licencia).',
          '[ENTREGA, SOPORTE, FORMA DE PAGO, IMPUESTOS Y REEMBOLSO DE LA COMPRA — confirmar con el propietario]',
        ],
      },
      {
        id: 'trial',
        title: 'The trial account',
        blocks: [
          'You can enter a trial account with sample data without signing up. It lasts **24 hours** and is then erased entirely.',
        ],
      },
    ],
  },
  // ── Source-code licence ───────────────────────────────────────────────────
  license: {
    label: 'Source-code licence',
    title: 'StockAI source-code licence',
    intro: 'The terms under which an organisation may install, use and modify its own copy of the StockAI code.',
    summary: [
      'This is a **commercial, proprietary licence**, not an open-source one. Only those who have signed a licence agreement with us have any rights.',
      'You may install StockAI, use it, modify it for internal use and host it for your own business.',
      'You **may not** resell, redistribute, sublicense or offer it as a service to third parties without a written agreement.',
      'The code remains ours and is confidential. The open-source components it includes keep their own licences.',
    ],
    sections: [
      {
        id: 'scope',
        title: 'What it is and who it applies to',
        blocks: [
          'These terms govern the copy of the StockAI source code that [NOMBRE DEL LICENCIANTE] ("the licensor", "we") delivers to an organisation that contracts it ("the licensee", "you"). They apply together with the licence agreement we sign with you; **where the agreement says something different, the agreement prevails**.',
          'The "software" is the source and compiled code of StockAI — the web application, the API, the forecasting engine — its documentation and the updates we deliver to you. These terms do not apply to the hosted service at app.stockai.es, which is governed by the [terms of service](/terminos).',
          'Without a signed licence agreement you have no rights to the software, even if you have obtained a copy.',
        ],
      },
      {
        id: 'grant',
        title: 'What you may do',
        blocks: [
          'While the agreement is in force and you comply with these terms, we grant you a **non-exclusive, non-transferable licence for your organisation** to:',
          {
            list: [
              '**install and run** the software on your own servers or those of a hosting provider you contract and control;',
              '**modify it** to fit your internal needs;',
              '**host it for your own business**, for use by your employees and collaborators;',
              'make the copies you need for the above, including backups and test environments.',
            ],
          },
          'If we agree so in writing, the licence may extend to your affiliates or subsidiaries. The number of installations, organisations or users, the price and the term are those in the agreement: [PRECIO/VIGENCIA según contrato].',
        ],
      },
      {
        id: 'restrictions',
        title: 'What you may not do',
        blocks: [
          'Unless we agree otherwise in writing, you may not:',
          {
            list: [
              'sell, resell, rent, lend or assign the software or your licence;',
              'redistribute or publish it, in whole or in part, with or without changes, including in public repositories;',
              'sublicense it or let another organisation use it;',
              'offer it to third parties as a hosted or cloud service (SaaS), or operate it on behalf of your customers;',
              'use it, or what you learn from it, to build a product that competes with StockAI;',
              'remove or alter the copyright, licence or trademark notices in the software;',
              'use or share the keys, credentials or accounts of our services. For the assistant, email or WhatsApp you must contract and configure your own accounts with those providers.',
            ],
          },
        ],
      },
      {
        id: 'ownership',
        title: 'Ownership',
        blocks: [
          'The software, its copies and the StockAI brand are and remain the licensor\'s. We license the software to you, we do not sell it, and you acquire no right that is not written here or in the agreement.',
          'You may use your modifications only together with the software and within this licence. [TITULARIDAD DE LAS MODIFICACIONES DEL LICENCIATARIO — confirmar con un abogado]',
          'If you send us suggestions or fixes, we may use them without obligation to you.',
        ],
      },
      {
        id: 'updates',
        title: 'Updates and support',
        blocks: [
          'We deliver updates, fixes or support only if the agreement provides for them, and on the terms it sets. Without that, you receive the software in the version delivered and we have no obligation to maintain it. Updates we deliver are covered by this same licence.',
        ],
      },
      {
        id: 'confidentiality',
        title: 'Confidentiality of the code',
        blocks: [
          'The source code is our confidential information. You must protect it with at least the care you give your own confidential information, and only your employees and contractors who need it for the permitted uses, and who are bound to confidentiality, may access it.',
          'This obligation survives the end of the licence. [PLAZO DE CONFIDENCIALIDAD TRAS LA TERMINACIÓN — confirmar]',
        ],
      },
      {
        id: 'third-party',
        title: 'Open-source components',
        blocks: [
          'The software uses third-party open-source components (for example, Next.js, React, FastAPI, pandas, LightGBM or Prophet). **Those components are not ours and are not covered by this licence**: each remains under its own licence (MIT, BSD, Apache 2.0 and others), and its terms prevail over these for that component.',
          'The list of direct components and their licences is in the `THIRD_PARTY_NOTICES.md` file delivered with the code. If you need it before then, ask for it at [contacto@stockai.es](mailto:contacto@stockai.es).',
        ],
      },
      {
        id: 'warranty',
        title: 'Warranty',
        blocks: [
          'Except as the agreement expressly states, the software is provided **"as is"**, without warranties of any kind, express or implied, including those of merchantability, fitness for a particular purpose and non-infringement. The forecasts it produces are estimates, and the decisions you make with them are yours.',
          'You are responsible for installing, operating, backing up and securing your copy, and for the configuration and providers you choose.',
        ],
      },
      {
        id: 'liability',
        title: 'Limitation of liability',
        blocks: [
          'To the extent the law allows, we are not liable for indirect damages, lost profits, loss of data or sales, or business decisions made with the software, and our total liability under this licence is limited to what you paid us for it in the 12 months before the event giving rise to it.',
          'Nothing here limits liability that applicable law does not allow to be limited.',
        ],
      },
      {
        id: 'audit',
        title: 'Verifying compliance',
        blocks: [
          'With reasonable notice of [PREAVISO — confirmar], we may ask you for a written statement that you comply with this licence (for example, how many installations you have and who uses them) and, if there are well-founded reasons, verify it ourselves or through an auditor bound to confidentiality, during business hours and without undue interference with your operations. [FRECUENCIA, COSTO Y CONDICIONES DE LA VERIFICACIÓN — confirmar]',
        ],
      },
      {
        id: 'termination',
        title: 'Termination',
        blocks: [
          'The licence ends when the agreement\'s term expires and is not renewed, or earlier if you breach these terms or the agreement and do not cure it within [PLAZO DE SUBSANACIÓN — confirmar] of our notice. A serious breach of the restrictions or of confidentiality ends it without any cure period.',
          'When it ends, you must **stop using the software and delete all copies of it**, including modified ones and backups, and confirm to us in writing that you did. Your business data in your installation is yours: export it before deleting the installation.',
          'The sections on ownership, confidentiality, warranty, liability and governing law survive termination.',
        ],
      },
      {
        id: 'earlier-versions',
        title: 'Earlier versions',
        blocks: [
          'Some earlier versions of this code were distributed under the MIT License. Whoever received those copies keeps the MIT License rights over them. Versions distributed from 2 October 2026 onward are governed by this licence.',
        ],
      },
      {
        id: 'law',
        title: 'Governing law',
        blocks: [
          'This licence is governed by the laws of [JURISDICCIÓN], and any dispute will be resolved before the courts named in the agreement or, failing that, those of [JURISDICCIÓN].',
        ],
      },
    ],
  },
}

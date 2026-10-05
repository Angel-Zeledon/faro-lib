// Copy for /desarrolladores, the developer reference on the landing.
//
// Its own file, like landing.ts and for the same reason: these are paragraphs
// owned by whoever owns the pitch, not short interface strings looked up by
// key. Typed by one interface, so a string missing in either language is a
// compile error rather than a blank on the page.
//
// The endpoint reference itself (paths, parameters, descriptions) is NOT here:
// it is generated from the backend's OpenAPI into src/data/public-api.json and
// stays in English, the language of the code it documents.
import type { Lang } from '@/i18n/translations'
import type { SchemaLabels } from '@/components/apidocs/SchemaTree'

export interface DevelopersCopy {
  breadcrumb: string
  home: string
  label: string
  title: string
  intro: string
  facts: { base: string; auth: string; endpoints: string; endpointsValue: (n: number) => string }
  toc: string
  guideNav: string
  auth: { title: string; body: string; note: string }
  scopes: {
    title: string
    body: string
    read: string
    readDesc: string
    write: string
    writeDesc: string
    howToGet: string
  }
  never: { title: string; body: string; items: string[]; outward: string }
  limits: {
    title: string
    perMinute: (n: number) => string
    noApi: string
    perDayPaid: (n: number) => string
    perDayCorporate: string
    over: string
    noHeaders: string
  }
  billing: { title: string; body: string; pricingLink: string; contact: (email: string) => string }
  errors: { title: string; body: string; codes: [string, string][]; shapeTitle: string; shapes: [string, string][]; sampleTitle: string }
  pagination: { title: string; body: string; items: [string, string][] }
  idempotency: { title: string; body: string; header: string }
  envelope: { title: string; body: string; fileNote: string; rpcNote: string; formatsTitle: string; formats: [string, string][] }
  reference: {
    title: string
    lead: string
    read: string
    write: string
    params: string
    body: string
    example: string
    curl: string
    noParams: string
    required: string
    returns: (status: number, types: string) => string
    tags: Record<string, string>
  }
  mcp: { title: string; body: string }
  /** The schema tree (request bodies, parameters, responses). */
  schema: SchemaLabels
  /** The interactive reference (sidebar, endpoint, code panel). */
  workspace: {
    browse: string
    search: string
    noResults: string
    close: string
    count: (n: number) => string
    sections: { request: string; responses: string }
    paramGroups: { path: string; query: string; header: string }
    headersTitle: string
    authValue: string
    optional: string
    noBody: string
    noContent: string
    bodyTitle: string
    bodyRequired: string
    bodyOptional: string
    successTitle: string
    errorsTitle: string
    errorsLead: string
    responseBody: string
    notCaptured: string
    fileResponse: (types: string) => string
    rpcResponse: string
    exampleNote: string
    request: string
    response: string
    languages: string
    copy: string
    copied: string
    copyUrl: string
    copyLink: string
    stats: { read: (n: number) => string; write: (n: number) => string }
    scopeRead: string
    scopeWrite: string
    clear: string
  }
}

export const DEVELOPERS: Record<Lang, DevelopersCopy> = {
  es: {
    breadcrumb: 'Ruta de navegación',
    home: 'Inicio',
    label: 'Desarrolladores',
    title: 'La API de StockAI',
    intro:
      'Casi todo lo que haces dentro de StockAI también se puede hacer por API: subir ventas, entrenar, leer el semáforo, registrar órdenes, administrar proveedores y bodegas. Así puedes conectarlo a tu ERP, tu POS o cualquier sistema propio.',
    facts: {
      base: 'URL base',
      auth: 'Autenticación',
      endpoints: 'Endpoints',
      endpointsValue: n => `${n} documentados`,
    },
    toc: 'En esta página',
    guideNav: 'Guía',
    auth: {
      title: 'Autenticación',
      body:
        'Cada llamada lleva una API key en la cabecera Authorization. Las claves se crean en la app, en Automatización → API Keys, y se muestran una sola vez: StockAI guarda solo un hash.',
      note: 'Una clave actúa como sí misma, no como la persona que la creó: sigue funcionando si esa persona deja la empresa.',
    },
    scopes: {
      title: 'Claves de lectura y de escritura',
      body: 'Al crear una clave eliges qué puede hacer. Cada endpoint de la referencia dice qué tipo de clave necesita.',
      read: 'Lectura',
      readDesc: 'Lee todo lo que un usuario visor ve: semáforo, pronósticos, proveedores, órdenes, reportes.',
      write: 'Lectura y escritura',
      writeDesc: 'Además crea y modifica, como un analista: sube archivos, entrena, registra órdenes, edita inventario y proveedores.',
      howToGet: 'Una clave de lectura que intenta escribir recibe 403 con error_code api_key_scope_insufficient, y no cambia nada.',
    },
    never: {
      title: 'Lo que una clave nunca puede hacer',
      body: 'Algunas acciones son de personas, no de sistemas. Con una API key responden 403 api_key_route_not_exposed:',
      items: [
        'Iniciar sesión, refrescar tokens o recuperar contraseñas.',
        'Crear, invitar o modificar usuarios y sus permisos.',
        'Crear, listar o revocar API keys: una clave no puede crear otra.',
        'Configurar la instalación o los canales de la cuenta.',
        'Exportar o borrar la cuenta.',
        'Lo que es de una persona: su bandeja de mensajes, sus conversaciones con el asistente, sus preferencias.',
        'Lo que solo un administrador puede hacer (por ejemplo, cambiar la moneda o el período activo).',
      ],
      outward:
        'Enviar una orden o una alerta por correo o WhatsApp con una clave de escritura exige que la cuenta tenga al menos un administrador con el correo verificado (si no: 403 api_key_tenant_unverified).',
    },
    limits: {
      title: 'Límites',
      perMinute: n => `${n} llamadas por minuto por clave, en los planes que incluyen la API.`,
      noApi: 'Plan gratis y cuentas de prueba: la API no está incluida; empieza en el plan completo.',
      perDayPaid: n => `Plan completo: ${n} llamadas por día por clave (ventana de 24 horas).`,
      perDayCorporate: 'Plan corporativo: sin tope diario.',
      over: 'Al pasarte recibes 429 con la cabecera Retry-After (segundos). El mismo 429 sirve para el límite por minuto y para el tope diario, y su cuerpo no trae error_code.',
      noHeaders: 'No hay cabeceras X-RateLimit-*: la única señal es el 429 con Retry-After. Cuenta tus llamadas o espera el 429.',
    },
    billing: {
      title: 'Medición y precio',
      body:
        'La API viene desde el plan completo, con un tope diario de llamadas por clave; el volumen mayor se acuerda con nosotros. Contamos cada llamada con una API key que llegó a un endpoint, por día (UTC) y por clave; las rechazadas (clave inválida, sin permiso o por encima del límite) no cuentan. Un administrador ve el consumo del mes en la app, en la pantalla API.',
      pricingLink: 'Ver precios',
      contact: email => `No hay checkout ni tarjeta: para hablar del precio, escríbenos a ${email}.`,
    },
    errors: {
      title: 'Errores',
      body: 'Casi todo error trae un error_code estable y, si aplica, error_params con los valores: ramifica por el código, no por el texto de detail, que es una ayuda en inglés y puede cambiar. Las respuestas del propio framework (cabecera ausente, ruta inexistente, método incorrecto) y el 429 solo traen detail.',
      codes: [
        ['api_key_invalid', '401 — la clave no existe, fue revocada o venció.'],
        ['api_key_route_not_exposed', '403 — ese endpoint no se puede usar con una API key.'],
        ['api_key_scope_insufficient', '403 — la clave es de lectura y el endpoint escribe.'],
        ['api_key_tenant_unverified', '403 — enviar algo fuera de StockAI exige un administrador verificado.'],
        ['validation_error', '422 — el cuerpo o los parámetros no son válidos; detail es una lista con un objeto por campo.'],
        ['PLAN_LIMIT_REACHED', '403 — llegaste a un tope del plan; detail es un objeto y error_params dice cuál.'],
        ['server_busy', '503 — el servidor está saturado; reintenta según Retry-After.'],
        ['(sin error_code)', '401 — falta la cabecera Authorization o no es “Bearer sk_live_…”: detail es “Not authenticated”.'],
        ['(sin error_code)', '429 — límite por minuto o tope diario: espera Retry-After segundos.'],
        ['(sin error_code)', '404 / 405 — ruta inexistente o método no permitido.'],
      ],
      shapeTitle: 'La forma de detail',
      shapes: [
        ['texto', 'La mayoría de los errores: una frase en inglés.'],
        ['lista', '422 validation_error: [{ type, loc, msg, input }], uno por campo inválido; loc dice dónde (por ejemplo ["body", "name"]).'],
        ['objeto', 'PLAN_LIMIT_REACHED: { code, limit, current, max, tier }, el mismo contenido que error_params.'],
      ],
      sampleTitle: 'Ejemplo: clave de lectura que escribe',
    },
    pagination: {
      title: 'Paginación',
      body: 'Hay tres formas, y cada endpoint la declara en sus parámetros:',
      items: [
        ['skip y limit', 'Sesiones, resumen de sesiones, fuentes de datos y datasets. La respuesta trae items, total, skip y limit.'],
        ['limit y offset', 'La actividad de alertas (/alerts/activity). La respuesta trae items, total, limit y offset.'],
        ['solo limit', 'Los más recientes primero, hasta limit: alertas, historial de órdenes, brechas de configuración, mermas e historial de reentrenamientos.'],
        ['sin paginación', 'El resto devuelve el conjunto completo y se acota con sus filtros, por ejemplo signal o supplier en /inventory/status.'],
      ],
    },
    idempotency: {
      title: 'Idempotencia',
      body: 'POST /inventory/log-po y POST /inventory/po aceptan la cabecera Idempotency-Key: un valor único (un UUID) por orden que quieres colocar. Si repites la petición con la misma clave recibes la orden que ya se creó (200 con replayed: true) en vez de una segunda; la misma clave con otras líneas responde 409 po_idempotency_key_reused. Los demás endpoints de escritura no son idempotentes: antes de reintentar una escritura cuyo resultado no viste, consulta si ya se aplicó.',
      header: 'Idempotency-Key: 3f1c9a5e-7d62-4b0e-9d6a-2c8f4a1b5e70',
    },
    envelope: {
      title: 'El formato de respuesta',
      body: 'Toda respuesta JSON viene envuelta: { "success": true, "data": …, "meta": { "timestamp": … } }. Lo tuyo está en data, que según el endpoint es un objeto o una lista; los listados paginados traen la lista en data.items.',
      fileNote: 'Los endpoints que devuelven un archivo (Excel, PDF, CSV) responden el archivo directamente, sin envoltorio.',
      rpcNote: 'El endpoint MCP (POST /mcp) habla JSON-RPC 2.0 y no usa este envoltorio.',
      formatsTitle: 'Tipos y formatos',
      formats: [
        ['Fechas', 'Las fechas son YYYY-MM-DD; los instantes, ISO 8601 en UTC, por ejemplo 2026-10-01T08:00:00+00:00.'],
        ['Números', 'Cantidades y montos son números JSON, no textos. Los montos van en la moneda de la cuenta (GET /tenant/currency).'],
        ['Sin valor', 'Un campo sin valor suele llegar como null en lugar de omitirse.'],
        ['Identificadores', 'Cadenas opacas con prefijo (sess_…, ds_…, sup_…): guárdalas tal cual.'],
        ['Señales', 'El semáforo usa PEDIR_YA, PEDIR_PRONTO, OK y SOBRESTOCK: valores fijos que no se traducen.'],
      ],
    },
    reference: {
      title: 'Referencia de endpoints',
      lead: 'Generada a partir del propio servicio: si un endpoint está aquí, se puede llamar con una API key. Las respuestas de ejemplo son llamadas reales a una cuenta de prueba. Las descripciones están en inglés, como el código.',
      read: 'Lectura',
      write: 'Escritura',
      params: 'Parámetros',
      body: 'Cuerpo',
      example: 'Ejemplo',
      curl: 'curl',
      noParams: 'Sin parámetros.',
      required: 'obligatorio',
      returns: (status, types) => `Responde ${status} · ${types}`,
      tags: {
        'ai-insights': 'Narrativas con IA',
        alerts: 'Alertas',
        'bi-datasets': 'Datos planos para BI',
        analyst: 'Analista',
        artifacts: 'Artefactos de entrenamiento',
        configuration: 'Configuración de sesión',
        currency: 'Moneda',
        'data-sources': 'Fuentes de datos',
        datasets: 'Datasets',
        documents: 'Documentos',
        entitlements: 'Plan y límites',
        forecasts: 'Pronósticos',
        freshness: 'Frescura de datos',
        inventory: 'Inventario, compras y proveedores',
        'inventory-recommendation-log': 'Historial de recomendaciones',
        'inventory-reversals': 'Reversas de órdenes',
        mcp: 'MCP (clientes de IA)',
        planning: 'Planificación',
        reports: 'Reportes',
        scenarios: 'Escenarios',
        schedule: 'Reentrenamiento programado',
        sessions: 'Sesiones',
        timezone: 'Zona horaria',
        training: 'Entrenamiento',
        webhooks: 'Webhooks',
      },
    },
    mcp: {
      title: 'Para clientes de IA (MCP)',
      body: 'La misma clave abre un servidor MCP en POST /mcp con cinco herramientas de solo lectura, para que un asistente de IA consulte tu semáforo. Por diseño no escribe nada. GET /mcp responde 405: el servidor no abre un flujo hacia el cliente.',
    },
    schema: {
      required: 'obligatorio',
      optional: 'opcional',
      nullable: 'admite null',
      expandAll: 'Expandir todo',
      collapseAll: 'Contraer todo',
      item: 'Cada elemento de la lista',
      eachValue: 'Cada valor',
      freeForm: 'Objeto libre: no declara campos.',
      oneOf: 'Una de estas formas',
      recursive: 'Estructura recursiva: se repite el mismo objeto.',
      constraint: { min: 'mín.', max: 'máx.', default: 'por defecto', minLength: 'largo mín.', maxLength: 'largo máx.' },
      more: n => `+${n} más`,
      rootArray: 'Una lista. Los campos son los de cada elemento.',
      rootObject: 'Un objeto.',
      rootMap: 'Un mapa: las claves son datos.',
      empty: 'Sin campos.',
    },
    workspace: {
      browse: 'Explorar endpoints',
      search: 'Buscar por ruta o acción',
      noResults: 'Ningún endpoint coincide con esa búsqueda.',
      close: 'Cerrar',
      count: n => `${n} endpoints`,
      sections: { request: 'Petición', responses: 'Respuestas' },
      paramGroups: { path: 'Parámetros de ruta', query: 'Parámetros de consulta', header: 'Cabeceras' },
      headersTitle: 'Cabeceras',
      authValue: 'Tu API key. Siempre obligatoria.',
      optional: 'opcional',
      noBody: 'Este endpoint no lleva cuerpo.',
      noContent: 'Responde sin cuerpo.',
      bodyTitle: 'Cuerpo',
      bodyRequired: 'El cuerpo es obligatorio.',
      bodyOptional: 'El cuerpo es opcional.',
      successTitle: 'Si sale bien',
      errorsTitle: 'Errores posibles',
      errorsLead: 'Además de los errores de cada endpoint, toda llamada con clave puede responder:',
      responseBody: 'Cuerpo de la respuesta',
      notCaptured: 'Para este endpoint no se capturó una respuesta de ejemplo. Toda respuesta JSON trae este envoltorio; lo que va en data depende del endpoint.',
      fileResponse: types => `Devuelve un archivo (${types}), sin envoltorio.`,
      rpcResponse: 'Responde JSON-RPC 2.0, no el envoltorio habitual.',
      exampleNote: 'Respuesta real de una cuenta de prueba, recortada: las listas muestran un elemento y los textos largos se acortan.',
      request: 'Petición',
      response: 'Respuesta',
      languages: 'Lenguaje del ejemplo',
      copy: 'Copiar',
      copied: 'Copiado',
      copyUrl: 'Copiar URL',
      copyLink: 'Copiar enlace',
      stats: { read: n => `${n} de lectura`, write: n => `${n} de escritura` },
      scopeRead: 'Clave de lectura',
      scopeWrite: 'Clave de escritura',
      clear: 'Borrar búsqueda',
    },
  },
  en: {
    breadcrumb: 'Breadcrumb',
    home: 'Home',
    label: 'Developers',
    title: 'The StockAI API',
    intro:
      'Almost everything you do inside StockAI can also be done through the API: upload sales, train, read the traffic light, record orders, manage suppliers and warehouses. Connect it to your ERP, your POS or any system of your own.',
    facts: {
      base: 'Base URL',
      auth: 'Authentication',
      endpoints: 'Endpoints',
      endpointsValue: n => `${n} documented`,
    },
    toc: 'On this page',
    guideNav: 'Guide',
    auth: {
      title: 'Authentication',
      body:
        'Every call carries an API key in the Authorization header. Keys are created in the app, under Automation → API Keys, and shown once: StockAI stores only a hash.',
      note: 'A key acts as itself, not as the person who created it: it keeps working if that person leaves the company.',
    },
    scopes: {
      title: 'Read keys and write keys',
      body: 'When you create a key you choose what it may do. Every endpoint in the reference says which kind of key it needs.',
      read: 'Read',
      readDesc: 'Reads everything a viewer sees: traffic light, forecasts, suppliers, orders, reports.',
      write: 'Read and write',
      writeDesc: 'Also creates and changes, like an analyst: uploads files, trains, records orders, edits inventory and suppliers.',
      howToGet: 'A read key that tries to write gets 403 with error_code api_key_scope_insufficient, and nothing changes.',
    },
    never: {
      title: 'What a key can never do',
      body: 'Some actions belong to people, not systems. With an API key they answer 403 api_key_route_not_exposed:',
      items: [
        'Sign in, refresh tokens or recover passwords.',
        'Create, invite or change users and their permissions.',
        'Create, list or revoke API keys: a key cannot mint another.',
        'Configure the installation or the account\'s channels.',
        'Export or delete the account.',
        'What belongs to one person: their inbox, their assistant conversations, their preferences.',
        'What only an administrator can do (for example, change the currency or the active period).',
      ],
      outward:
        'Sending an order or an alert by email or WhatsApp with a write key requires the account to have at least one administrator with a verified email (otherwise: 403 api_key_tenant_unverified).',
    },
    limits: {
      title: 'Limits',
      perMinute: n => `${n} calls per minute per key, on the plans that include the API.`,
      noApi: 'Free plan and trial accounts: the API is not included; it starts on the Full plan.',
      perDayPaid: n => `Full plan: ${n} calls per day per key (a 24-hour window).`,
      perDayCorporate: 'Corporate plan: no daily cap.',
      over: 'Over the limit you get 429 with a Retry-After header (seconds). The same 429 serves the per-minute limit and the daily cap, and its body has no error_code.',
      noHeaders: 'There are no X-RateLimit-* headers: the only signal is the 429 with Retry-After. Count your own calls or wait for the 429.',
    },
    billing: {
      title: 'Metering and pricing',
      body:
        'The API comes with the Full plan, with a daily cap of calls per key; larger volume is agreed with us. We count every API-key call that reached an endpoint, by day (UTC) and by key; refused calls (invalid key, no permission or over the limit) do not count. An administrator sees the month\'s usage in the app, on the API screen.',
      pricingLink: 'See pricing',
      contact: email => `There is no checkout and no card: to talk about pricing, write to us at ${email}.`,
    },
    errors: {
      title: 'Errors',
      body: 'Almost every error carries a stable error_code and, where it applies, error_params with the values: branch on the code, not on the detail text, which is an English hint and may change. The framework\'s own answers (missing header, unknown route, wrong method) and the 429 carry only detail.',
      codes: [
        ['api_key_invalid', '401 — the key does not exist, was revoked or has expired.'],
        ['api_key_route_not_exposed', '403 — that endpoint cannot be called with an API key.'],
        ['api_key_scope_insufficient', '403 — the key is read-only and the endpoint writes.'],
        ['api_key_tenant_unverified', '403 — sending anything outside StockAI needs a verified administrator.'],
        ['validation_error', '422 — the body or parameters are invalid; detail is a list with one object per field.'],
        ['PLAN_LIMIT_REACHED', '403 — a plan ceiling was reached; detail is an object and error_params says which.'],
        ['server_busy', '503 — the server is saturated; retry after Retry-After.'],
        ['(no error_code)', '401 — the Authorization header is missing or is not “Bearer sk_live_…”: detail is “Not authenticated”.'],
        ['(no error_code)', '429 — per-minute limit or daily cap: wait Retry-After seconds.'],
        ['(no error_code)', '404 / 405 — unknown route or method not allowed.'],
      ],
      shapeTitle: 'The shape of detail',
      shapes: [
        ['string', 'Most errors: one English sentence.'],
        ['list', '422 validation_error: [{ type, loc, msg, input }], one per invalid field; loc says where (for example ["body", "name"]).'],
        ['object', 'PLAN_LIMIT_REACHED: { code, limit, current, max, tier }, the same content as error_params.'],
      ],
      sampleTitle: 'Example: a read key that writes',
    },
    pagination: {
      title: 'Pagination',
      body: 'There are three forms, and each endpoint declares its own in its parameters:',
      items: [
        ['skip and limit', 'Sessions, session summaries, data sources and datasets. The response has items, total, skip and limit.'],
        ['limit and offset', 'Alert activity (/alerts/activity). The response has items, total, limit and offset.'],
        ['limit only', 'Newest first, up to limit: alerts, order history, setup gaps, shrinkage and retraining history.'],
        ['no pagination', 'The rest return the whole set and are narrowed with their filters, for example signal or supplier on /inventory/status.'],
      ],
    },
    idempotency: {
      title: 'Idempotency',
      body: 'POST /inventory/log-po and POST /inventory/po accept the Idempotency-Key header: one unique value (a UUID) per order you mean to place. Repeating the request with the same key returns the order that was already created (200 with replayed: true) instead of a second one; the same key with different lines answers 409 po_idempotency_key_reused. Every other write endpoint is not idempotent: before retrying a write whose result you did not see, check whether it was applied.',
      header: 'Idempotency-Key: 3f1c9a5e-7d62-4b0e-9d6a-2c8f4a1b5e70',
    },
    envelope: {
      title: 'Response format',
      body: 'Every JSON response is wrapped: { "success": true, "data": …, "meta": { "timestamp": … } }. Your payload is in data, which is an object or a list depending on the endpoint; paginated lists carry their list in data.items.',
      fileNote: 'Endpoints that return a file (Excel, PDF, CSV) answer with the file itself, with no wrapper.',
      rpcNote: 'The MCP endpoint (POST /mcp) speaks JSON-RPC 2.0 and does not use this wrapper.',
      formatsTitle: 'Types and formats',
      formats: [
        ['Dates', 'Dates are YYYY-MM-DD; instants are ISO 8601 in UTC, for example 2026-10-01T08:00:00+00:00.'],
        ['Numbers', 'Quantities and amounts are JSON numbers, not strings. Amounts are in the account\'s currency (GET /tenant/currency).'],
        ['Empty values', 'A field with no value usually arrives as null instead of being omitted.'],
        ['Identifiers', 'Opaque prefixed strings (sess_…, ds_…, sup_…): store them as they are.'],
        ['Signals', 'The traffic light uses PEDIR_YA, PEDIR_PRONTO, OK and SOBRESTOCK: fixed values that are not translated.'],
      ],
    },
    reference: {
      title: 'Endpoint reference',
      lead: 'Generated from the service itself: if an endpoint is listed here, it can be called with an API key. Example responses are real calls to a test account.',
      read: 'Read',
      write: 'Write',
      params: 'Parameters',
      body: 'Body',
      example: 'Example',
      curl: 'curl',
      noParams: 'No parameters.',
      required: 'required',
      returns: (status, types) => `Returns ${status} · ${types}`,
      tags: {
        'ai-insights': 'AI narratives',
        alerts: 'Alerts',
        'bi-datasets': 'Flat datasets for BI',
        analyst: 'Analyst',
        artifacts: 'Training artifacts',
        configuration: 'Session configuration',
        currency: 'Currency',
        'data-sources': 'Data sources',
        datasets: 'Datasets',
        documents: 'Documents',
        entitlements: 'Plan and limits',
        forecasts: 'Forecasts',
        freshness: 'Data freshness',
        inventory: 'Inventory, purchasing and suppliers',
        'inventory-recommendation-log': 'Recommendation history',
        'inventory-reversals': 'Order reversals',
        mcp: 'MCP (AI clients)',
        planning: 'Planning',
        reports: 'Reports',
        scenarios: 'Scenarios',
        schedule: 'Scheduled retraining',
        sessions: 'Sessions',
        timezone: 'Time zone',
        training: 'Training',
        webhooks: 'Webhooks',
      },
    },
    mcp: {
      title: 'For AI clients (MCP)',
      body: 'The same key opens an MCP server at POST /mcp with five read-only tools, so an AI assistant can query your traffic light. By design it writes nothing. GET /mcp answers 405: the server does not open a stream to the client.',
    },
    schema: {
      required: 'required',
      optional: 'optional',
      nullable: 'nullable',
      expandAll: 'Expand all',
      collapseAll: 'Collapse all',
      item: 'Each list item',
      eachValue: 'Each value',
      freeForm: 'Free-form object: it declares no fields.',
      oneOf: 'One of these shapes',
      recursive: 'Recursive structure: the same object repeats.',
      constraint: { min: 'min', max: 'max', default: 'default', minLength: 'min length', maxLength: 'max length' },
      more: n => `+${n} more`,
      rootArray: 'A list. The fields are those of each item.',
      rootObject: 'An object.',
      rootMap: 'A map: the keys are data.',
      empty: 'No fields.',
    },
    workspace: {
      browse: 'Browse endpoints',
      search: 'Search by path or action',
      noResults: 'No endpoint matches that search.',
      close: 'Close',
      count: n => `${n} endpoints`,
      sections: { request: 'Request', responses: 'Responses' },
      paramGroups: { path: 'Path parameters', query: 'Query parameters', header: 'Headers' },
      headersTitle: 'Headers',
      authValue: 'Your API key. Always required.',
      optional: 'optional',
      noBody: 'This endpoint takes no body.',
      noContent: 'Answers with no body.',
      bodyTitle: 'Body',
      bodyRequired: 'The body is required.',
      bodyOptional: 'The body is optional.',
      successTitle: 'On success',
      errorsTitle: 'Possible errors',
      errorsLead: 'Besides the errors of each endpoint, any call made with a key can answer:',
      responseBody: 'Response body',
      notCaptured: 'No example response was captured for this endpoint. Every JSON answer comes in this envelope; what data holds depends on the endpoint.',
      fileResponse: types => `Returns a file (${types}), with no wrapper.`,
      rpcResponse: 'Answers JSON-RPC 2.0, not the usual envelope.',
      exampleNote: 'A real answer from a test account, trimmed: lists show one item and long texts are shortened.',
      request: 'Request',
      response: 'Response',
      languages: 'Example language',
      copy: 'Copy',
      copied: 'Copied',
      copyUrl: 'Copy URL',
      copyLink: 'Copy link',
      stats: { read: n => `${n} read`, write: n => `${n} write` },
      scopeRead: 'Read key',
      scopeWrite: 'Write key',
      clear: 'Clear search',
    },
  },
}

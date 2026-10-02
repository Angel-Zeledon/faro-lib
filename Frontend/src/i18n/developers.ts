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

export interface DevelopersCopy {
  breadcrumb: string
  home: string
  label: string
  title: string
  intro: string
  facts: { base: string; auth: string; endpoints: string; endpointsValue: (n: number) => string }
  toc: string
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
    perDayFree: (n: number) => string
    perDayPaid: string
    over: string
  }
  billing: { title: string; body: string; pricingLink: string; contact: (email: string) => string }
  errors: { title: string; body: string; codes: [string, string][] }
  pagination: { title: string; body: string }
  envelope: { title: string; body: string }
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
  /** The interactive reference (sidebar, request bar, code panel). */
  workspace: {
    browse: string
    search: string
    noResults: string
    close: string
    count: (n: number) => string
    tabs: { params: string; body: string; headers: string; response: string }
    noBody: string
    headersLead: string
    authValue: string
    optional: string
    responseLead: string
    responseNote: string
    noContent: string
    request: string
    response: string
    languages: string
    copy: string
    copied: string
    copyUrl: string
    stats: { read: (n: number) => string; write: (n: number) => string }
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
      perMinute: n => `${n} llamadas por minuto por clave, en cualquier plan.`,
      perDayFree: n => `Plan gratis: además, ${n} llamadas por día por clave (ventana de 24 horas).`,
      perDayPaid: 'Plan completo: sin tope diario.',
      over: 'Al pasarte recibes 429 con la cabecera Retry-After: espera esos segundos antes de reintentar.',
    },
    billing: {
      title: 'Medición y precio',
      body:
        'La API se cobra por llamada. Contamos cada llamada con una API key que llegó a un endpoint, por día (UTC) y por clave; las rechazadas (clave inválida, sin permiso o por encima del límite) no cuentan. Un administrador ve el consumo del mes en la app, en la pantalla API.',
      pricingLink: 'Ver precios',
      contact: email => `No hay checkout ni tarjeta: para hablar del precio, escríbenos a ${email}.`,
    },
    errors: {
      title: 'Errores',
      body: 'Todo error trae un error_code estable y, si aplica, error_params con los valores. El texto de detail es una ayuda en inglés y puede cambiar: ramifica por el código.',
      codes: [
        ['api_key_route_not_exposed', '403 — ese endpoint no se puede usar con una API key.'],
        ['api_key_scope_insufficient', '403 — la clave es de lectura y el endpoint escribe.'],
        ['api_key_tenant_unverified', '403 — enviar algo fuera de StockAI exige un administrador verificado.'],
        ['validation_error', '422 — el cuerpo o los parámetros no son válidos; detail lista los campos.'],
        ['PLAN_LIMIT_REACHED', '403 — llegaste a un tope del plan gratis; error_params dice cuál.'],
        ['server_busy', '503 — el servidor está saturado; reintenta según Retry-After.'],
      ],
    },
    pagination: {
      title: 'Paginación',
      body: 'Los listados que pueden crecer sin límite (sesiones, fuentes de datos, datasets) aceptan skip y limit. El resto devuelve el conjunto completo y se acota con sus filtros, por ejemplo signal o supplier en /inventory/status.',
    },
    envelope: {
      title: 'El formato de respuesta',
      body: 'Toda respuesta JSON viene envuelta: { "success": true, "data": …, "meta": { "timestamp": … } }. Lo tuyo está en data. Los endpoints que devuelven un archivo (Excel, PDF, CSV) responden el archivo directamente.',
    },
    reference: {
      title: 'Referencia de endpoints',
      lead: 'Generada a partir del propio servicio: si un endpoint está aquí, se puede llamar con una API key. Las descripciones están en inglés, como el código.',
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
      body: 'La misma clave abre un servidor MCP en /mcp con cinco herramientas de solo lectura, para que un asistente de IA consulte tu semáforo. Por diseño no escribe nada.',
    },
    workspace: {
      browse: 'Explorar endpoints',
      search: 'Buscar por ruta o acción',
      noResults: 'Ningún endpoint coincide con esa búsqueda.',
      close: 'Cerrar',
      count: n => `${n} endpoints`,
      tabs: { params: 'Parámetros', body: 'Cuerpo', headers: 'Cabeceras', response: 'Respuesta' },
      noBody: 'Este endpoint no lleva cuerpo.',
      headersLead: 'Cabeceras que lleva esta llamada.',
      authValue: 'Tu API key. Siempre obligatoria.',
      optional: 'opcional',
      responseLead: 'Si todo sale bien:',
      responseNote: 'Toda respuesta JSON viene en este sobre; lo que trae data depende de cada endpoint. Si algo falla, recibes un error_code estable (ver Errores).',
      noContent: 'Responde sin cuerpo.',
      request: 'Petición',
      response: 'Respuesta',
      languages: 'Lenguaje del ejemplo',
      copy: 'Copiar',
      copied: 'Copiado',
      copyUrl: 'Copiar URL',
      stats: { read: n => `${n} de lectura`, write: n => `${n} de escritura` },
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
      perMinute: n => `${n} calls per minute per key, on any plan.`,
      perDayFree: n => `Free plan: also ${n} calls per day per key (a 24-hour window).`,
      perDayPaid: 'Full plan: no daily cap.',
      over: 'Over the limit you get 429 with a Retry-After header: wait that many seconds before retrying.',
    },
    billing: {
      title: 'Metering and pricing',
      body:
        'The API is billed per call. We count every API-key call that reached an endpoint, by day (UTC) and by key; refused calls (invalid key, no permission or over the limit) do not count. An administrator sees the month\'s usage in the app, on the API screen.',
      pricingLink: 'See pricing',
      contact: email => `There is no checkout and no card: to talk about pricing, write to us at ${email}.`,
    },
    errors: {
      title: 'Errors',
      body: 'Every error carries a stable error_code and, where it applies, error_params with the values. The detail text is an English hint and may change: branch on the code.',
      codes: [
        ['api_key_route_not_exposed', '403 — that endpoint cannot be called with an API key.'],
        ['api_key_scope_insufficient', '403 — the key is read-only and the endpoint writes.'],
        ['api_key_tenant_unverified', '403 — sending anything outside StockAI needs a verified administrator.'],
        ['validation_error', '422 — the body or parameters are invalid; detail lists the fields.'],
        ['PLAN_LIMIT_REACHED', '403 — a free-plan ceiling was reached; error_params says which.'],
        ['server_busy', '503 — the server is saturated; retry after Retry-After.'],
      ],
    },
    pagination: {
      title: 'Pagination',
      body: 'Lists that can grow without bound (sessions, data sources, datasets) take skip and limit. The rest return the whole set and are narrowed with their filters, for example signal or supplier on /inventory/status.',
    },
    envelope: {
      title: 'Response format',
      body: 'Every JSON response is wrapped: { "success": true, "data": …, "meta": { "timestamp": … } }. Your payload is in data. Endpoints that return a file (Excel, PDF, CSV) answer with the file itself.',
    },
    reference: {
      title: 'Endpoint reference',
      lead: 'Generated from the service itself: if an endpoint is listed here, it can be called with an API key.',
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
      body: 'The same key opens an MCP server at /mcp with five read-only tools, so an AI assistant can query your traffic light. By design it writes nothing.',
    },
    workspace: {
      browse: 'Browse endpoints',
      search: 'Search by path or action',
      noResults: 'No endpoint matches that search.',
      close: 'Close',
      count: n => `${n} endpoints`,
      tabs: { params: 'Parameters', body: 'Body', headers: 'Headers', response: 'Response' },
      noBody: 'This endpoint takes no body.',
      headersLead: 'Headers this call carries.',
      authValue: 'Your API key. Always required.',
      optional: 'optional',
      responseLead: 'When it succeeds:',
      responseNote: 'Every JSON answer comes in this envelope; what data holds depends on the endpoint. When something fails you get a stable error_code (see Errors).',
      noContent: 'Answers with no body.',
      request: 'Request',
      response: 'Response',
      languages: 'Example language',
      copy: 'Copy',
      copied: 'Copied',
      copyUrl: 'Copy URL',
      stats: { read: n => `${n} read`, write: n => `${n} write` },
    },
  },
}

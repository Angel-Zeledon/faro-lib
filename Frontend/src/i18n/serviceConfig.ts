/**
 * Copy for the installation panel (`/instalacion`), in both languages.
 *
 * Why this is NOT in `translations.ts`, for the same reason `landing.ts` is
 * not: that catalogue is short interface strings looked up by a runtime key,
 * and a typo there fails silently by echoing the key back. This is paragraphs,
 * one per service and one per variable, and every one of them has to exist in
 * both languages — so the shape is a TYPE keyed by the backend's own service
 * and field keys. A key the registry adds and nobody translates is a COMPILE
 * error, and a key that no longer exists is too.
 *
 * The backend sends the same text in English inside each field's `doc`, since
 * it also generates `.env.example` and `docs/configuracion.md` from one source.
 * The panel shows THIS instead, because a screen a person reads is end-user
 * copy and the house rule is that end-user copy is localized. Keep the two
 * saying the same thing: `backend/tests/test_service_config_i18n.py` asserts
 * the key sets match, not the wording, so a translation may be shorter — it may
 * not be about something else.
 */
import type { Lang } from './translations'

/** Service keys, exactly as `backend/service_config/registry.py` declares them. */
export type ServiceKey =
  | 'core' | 'llm' | 'email' | 'whatsapp' | 'sms' | 'rag'
  | 'secret_storage' | 'contact' | 'billing' | 'social_login' | 'inbound_email' | 'enterprise_sso' | 'worker' | 'limits' | 'api_surface' | 'operations'
  | 'secret_storage' | 'contact' | 'social_login' | 'inbound_email' | 'enterprise_sso' | 'worker' | 'limits' | 'sql_sources' | 'api_surface' | 'operations'

/** Field keys, exactly as the registry declares them (= `Settings` attributes). */
export type FieldKey =
  | 'secret_key' | 'database_url' | 'frontend_url' | 'allowed_origins'
  | 'instance_admin_emails' | 'environment' | 'app_name' | 'app_version'
  | 'access_token_expire_minutes' | 'algorithm' | 'storage_path' | 'testing_mode'
  | 'deepseek_api_key' | 'deepseek_model' | 'deepseek_base_url'
  | 'resend_api_key' | 'email_from' | 'smtp_server' | 'smtp_port'
  | 'smtp_user' | 'smtp_pass'
  | 'twilio_account_sid' | 'twilio_auth_token' | 'twilio_whatsapp_from'
  | 'whatsapp_webhook_base_url' | 'whatsapp_bot_generic_mode'
  | 'twilio_sms_from'
  | 'voyageai_api_key' | 'pinecone_api_key' | 'pinecone_index' | 'pinecone_environment'
  | 'integrations_secret_key'
  | 'contact_whatsapp' | 'contact_email' | 'upgrade_notify_email'
  | 'stripe_secret_key' | 'stripe_webhook_secret' | 'stripe_price_id_full'
  | 'paypal_client_id' | 'paypal_client_secret' | 'paypal_webhook_id'
  | 'paypal_plan_id_full' | 'paypal_mode' | 'billing_price_usd_full'
  | 'social_login_enabled' | 'google_oauth_client_id' | 'google_oauth_client_secret'
  | 'microsoft_oauth_client_id' | 'microsoft_oauth_client_secret'
  | 'apple_oauth_service_id' | 'apple_oauth_team_id' | 'apple_oauth_key_id'
  | 'apple_oauth_private_key'
  | 'inbound_email_domain' | 'inbound_email_secret'
  | 'enterprise_sso_enabled'
  | 'worker_enabled' | 'scheduler_enabled' | 'worker_id'
  | 'max_concurrent_jobs' | 'worker_poll_interval_seconds'
  | 'max_upload_size_mb' | 'dataset_editor_max_rows' | 'dataset_editor_max_mb'
  | 'sql_materialize_max_rows' | 'accuracy_degradation_threshold_pct'
  | 'reforecast_full_refit_days'
  | 'sql_sources_allow_private_hosts' | 'sql_sources_max_concurrent_per_tenant'
  | 'public_api_only'
  // Operations thresholds (installation status panel)
  | 'ops_queue_wait_degraded_minutes' | 'ops_running_job_degraded_minutes'
  | 'ops_worker_heartbeat_stale_seconds' | 'ops_disk_free_min_percent'
  | 'ops_backup_max_age_hours' | 'ops_pool_saturation_percent'
  | 'ops_latency_slo_ms' | 'ops_slow_query_ms'
  | 'backup_status_path' | 'backup_dir'

export interface ServiceCopy {
  /** Short name for the card header. */
  name: string
  /** One line: what the service is. */
  summary: string
  /** What the user loses while it is off. Shown under a service that is not ready. */
  whatBreaks: string
  /** Anything a reader needs that the fields do not say. Optional. */
  note?: string
}

export interface ServiceConfigCopy {
  services: Record<ServiceKey, ServiceCopy>
  fields: Record<FieldKey, string>
  ui: {
    title: string
    lead: string
    tabInstance: string
    tabTenant: string
    /** States. */
    stateReady: string
    stateNotConfigured: string
    stateDegraded: string
    stateOff: string
    stateOn: string
    /** Where a value is coming from. */
    sourceTenant: string
    sourceInstance: string
    sourceEnv: string
    sourceDefault: string
    sourceLabel: string
    /** Field affordances. */
    missingLabel: string
    orElse: string
    envOnly: string
    envOnlyHelp: string
    secretSet: string
    secretInherited: string
    secretEmpty: string
    secretPlaceholder: string
    clearHint: string
    required: string
    /** Actions. */
    save: string
    saving: string
    saved: string
    test: string
    testing: string
    reset: string
    resetConfirm: string
    /** Probe outcomes, by the backend's stable code. */
    probeOk: string
    probeNotConfigured: string
    probeAuthFailed: string
    probeUnreachable: string
    probeTimeout: string
    probeRejected: string
    probeMissingDependency: string
    probeUnexpected: string
    lastCheck: string
    neverChecked: string
    /** Access and store conditions. */
    bootstrapNotice: string
    operatorDisabledTitle: string
    operatorDisabledBody: string
    notOperatorTitle: string
    notOperatorBody: string
    storeUnavailable: string
    encryptionUnavailable: string
    encryptionGenerated: string
    undocumented: string
    tenantLead: string
    tenantEmpty: string
    noneEditable: string
  }
  /** Installation status panel (operators). */
  ops: {
    title: string
    refresh: string
    overall: Record<'ok' | 'degraded' | 'unknown', string>
    checks: Record<string, string>
    queueLine: string
    oldestQueued: string
    running: string
    failed24h: string
    noFailures: string
    heartbeatLine: string
    never: string
    latencyTitle: string
    latencyScope: string
    noTraffic: string
    slowQueries: string
    loadError: string
  }
}

const es: ServiceConfigCopy = {
  services: {
    core: {
      name: 'Núcleo',
      summary: 'Identidad del proceso, base de datos y llave de firma.',
      whatBreaks: 'No arranca nada. Se leen una sola vez, al iniciar.',
      note: 'Solo por entorno, a propósito: el pool de conexiones y el firmador de tokens las leen al importar, así que un valor escrito desde el panel no tomaría efecto hasta reiniciar — y uno equivocado impediría que el reinicio funcione.',
    },
    llm: {
      name: 'Asistente (DeepSeek)',
      summary: 'DeepSeek, el único motor de lenguaje. Mueve todas las funciones de IA.',
      whatBreaks: 'El asistente deja de responder, el resumen de la mañana y la lectura de inventario vuelven a su texto por reglas, el analista de documentos se apaga y el diagnóstico de datos reporta solo sus revisiones deterministas. Nada da error: cada pantalla dice que el asistente no está disponible.',
      note: 'Hay un solo proveedor y ninguna cadena de respaldo. Una versión anterior usaba la llave que estuviera puesta, así que un error de tipeo en el nombre de la variable producía funciones de IA que respondían desde otro proveedor — visible solo en la factura.',
    },
    email: {
      name: 'Correo',
      summary: 'Correo transaccional — Resend primero, SMTP como respaldo.',
      whatBreaks: 'No sale ningún correo: verificación de cuenta, recuperación de contraseña, invitaciones, el resumen diario de inventario, el recuento mensual y las órdenes de compra al proveedor. Cada envío queda registrado y se reporta como no entregado — la app nunca dice que mandó algo que no mandó.',
      note: 'Resend gana cuando su llave está puesta; si no, se usa SMTP. Sin ninguno de los dos el envío falla en voz alta en vez de callar. Lo que un tenant configure aquí aplica SOLO al correo dirigido a su propia gente y a sus proveedores: la verificación, el reseteo de contraseña y las invitaciones siempre salen por el transporte de la instalación, porque un tenant no puede mandar el mensaje que da acceso a una cuenta.',
    },
    whatsapp: {
      name: 'WhatsApp',
      summary: 'WhatsApp por Twilio — alertas diarias, mensajes a proveedores y el bot.',
      whatBreaks: 'No se manda ni se recibe ningún WhatsApp: la alerta diaria de inventario pierde ese canal (el correo sigue saliendo si está configurado), las órdenes de compra no se pueden enviar por WhatsApp y el bot nunca contesta.',
      note: '`WHATSAPP_WEBHOOK_BASE_URL` es la que parece opcional y no lo es en cuanto el bot está en uso. Twilio firma el webhook entrante sobre la URL PÚBLICA; detrás de un proxy el backend ve una interna, la firma nunca coincide y todo mensaje entrante se rechaza con 403.',
    },
    sms: {
      name: 'SMS',
      summary: 'SMS por Twilio — aviso de los mensajes del equipo.',
      whatBreaks: 'No se avisa por SMS de un mensaje del equipo. El mensaje se entrega igual dentro de la app; lo que se pierde es el empujón.',
      note: 'Comparte las credenciales de Twilio con WhatsApp y agrega una sola: el remitente. Un número con prefijo «whatsapp:» NO puede mandar SMS, y por eso es una variable aparte y no una reutilización.',
    },
    rag: {
      name: 'Búsqueda en documentos',
      summary: 'Búsqueda en documentos — embeddings de Voyage AI sobre un índice de Pinecone.',
      whatBreaks: 'Los documentos que subas dejan de indexarse y el analista ya no puede citarlos. El asistente sigue respondiendo con los datos del tenant; simplemente no tiene documentos que citar.',
      note: 'Necesita las llaves y además los paquetes `voyageai` y `pinecone` instalados — un paquete faltante apaga el servicio igual que una llave faltante, y dice cuál. El índice debe ser de 1024 dimensiones y métrica coseno.',
    },
    secret_storage: {
      name: 'Cifrado de secretos',
      summary: 'La llave Fernet que cifra todo secreto que guarda /instalación.',
      whatBreaks: 'Nada, de inmediato — y eso es lo que conviene saber. Con la variable vacía la instalación GENERA una llave en storage/instance_secret.key, así que el panel sigue guardando secretos; dice «sin configurar» porque la variable está vacía, no porque la función esté apagada, y más abajo te dice cuál llave está en uso. Lo que se pierde es durabilidad: hay que respaldar ese archivo junto con storage/, y dos procesos en discos distintos generan llaves DISTINTAS y no pueden leer lo que guardó el otro. Solo cuando tampoco se puede escribir una llave — un disco de solo lectura — empiezan a rechazarse los campos secretos, y lo dicen en voz alta en vez de guardar algo sin cifrar.',
      note: '`INTEGRATIONS_SECRET_KEY` es una llave Fernet y es solo de entorno a propósito: cifra los secretos que escribe este mismo panel, así que un panel capaz de reescribirla dejaría ilegibles sus propios secretos con un clic. Perderla significa volver a ingresar todos los secretos guardados. El nombre es histórico y se conserva a propósito: renombrarla dejaría huérfanos los secretos de toda instalación existente.',
    },
    contact: {
      name: 'Contacto comercial',
      summary: 'Cómo te contacta un cliente para levantar los techos del plan gratis.',
      whatBreaks: 'Desaparecen los botones de «escríbenos». Un tenant gratis que llega a un techo se queda sin forma de pedir más espacio. Si el pago en línea no está configurado, esa es toda la superficie comercial del producto — y el plan corporativo solo se vende así.',
      note: 'Un canal vacío se OCULTA en vez de mostrarse roto: un botón que abre un enlace de WhatsApp en blanco es peor que ningún botón. Configura al menos uno.',
    },
    billing: {
      name: 'Pago en línea (Stripe y PayPal)',
      summary: 'Contratar el plan completo en línea, con tarjeta (Stripe) o PayPal, en sus propias páginas.',
      whatBreaks: 'Desaparecen «Pasar al plan completo» y el pago de la sección Plan y pago; el estado dice que el pago en línea está apagado y qué variables faltan. Todo lo demás sigue igual: los clientes te escriben y tú cambias el plan a mano, como antes. Las suscripciones ya vendidas conservan su plan, pero si borras el secreto del webhook sus renovaciones y cancelaciones dejan de aplicarse: nunca vacíes un proveedor que tiene clientes.',
      note: 'Solo páginas alojadas: ningún número de tarjeta, CVC ni contraseña de PayPal pasa por este servidor ni por el JavaScript de la app. Lo ÚNICO que cambia el plan de una empresa es un webhook con firma verificada (Stripe: HMAC-SHA256 de Stripe-Signature, 5 minutos de tolerancia; PayPal: la API verify-webhook-signature). URLs de webhook para registrar: <FRONTEND_URL>/api/v1/billing/stripe/webhook y <FRONTEND_URL>/api/v1/billing/paypal/webhook. Solo se vende el plan completo, mensual; el corporativo nunca se compra en línea. Con un pago vencido se conserva el plan 7 días; cuando la suscripción termina la empresa pasa al plan gratis y no se borra nada. Un plan completo puesto a mano nunca lo toca el pago en línea.',
    },
    social_login: {
      name: 'Inicio de sesión con Google, Microsoft y Apple',
      summary: 'Entrar con Google, Microsoft o Apple, además de correo y contraseña.',
      whatBreaks: 'Desaparecen los botones «Continuar con Google / Microsoft / Apple» del inicio de sesión y del registro; correo y contraseña siguen funcionando igual. Quien solo entraba con un proveedor debe usar «¿Olvidaste tu contraseña?» para crear una. Viene apagado: una instalación nueva muestra solo el formulario de correo hasta que configuras un proveedor Y enciendes SOCIAL_LOGIN_ENABLED.',
      note: 'Cada proveedor muestra su botón solo con el interruptor encendido y TODOS sus campos llenos; uno a medias nunca se ofrece. La URL de redirección que pide cada consola sale de FRONTEND_URL: <FRONTEND_URL>/api/v1/auth/oauth/google/callback, <FRONTEND_URL>/api/v1/auth/oauth/microsoft/callback y <FRONTEND_URL>/api/v1/auth/oauth/apple/callback. Paso a paso: docs/social-login.md. Una cuenta existente solo se vincula por un correo que el PROVEEDOR verificó; si aquí ese correo nunca se había verificado, se le quita la contraseña al vincular, porque quien la eligió nunca demostró ser dueño del buzón. Microsoft no verifica el correo de las cuentas de trabajo o escuela: ahí solo cuenta como verificado si envía `xms_edov` (agrégalo como reclamación opcional del token de ID en el registro de la app) o `email_verified`; las cuentas personales de Microsoft (outlook.com, hotmail.com…) se aceptan porque Microsoft verifica esos correos. Sin esa prueba, Microsoft no puede crear ni vincular una cuenta, solo entrar a una identidad vinculada antes.',
    },
    inbound_email: {
      name: 'Ventas por correo',
      summary: 'Recibir archivos de ventas reenviados por correo a una dirección privada de cada cuenta.',
      whatBreaks: 'La tarjeta «Ventas por correo» avisa que esta instalación todavía no puede recibir correo, y POST /api/v1/inbound/email responde el error estructurado `inbound_email_disabled`. Todo lo demás sigue igual: los archivos se suben a mano.',
      note: 'Necesita un proveedor de correo que reenvíe el correo entrante a un webhook (Postmark, Mailgun, Resend o un relay) y un registro MX para el dominio de entrada. El webhook se autentica con INBOUND_EMAIL_SECRET: una cabecera HMAC `X-StockAI-Signature` o HTTP Basic cuya contraseña es el secreto. Paso a paso: docs/inbound-email.md.',
    },
    enterprise_sso: {
      name: 'Inicio de sesión de empresa (OpenID Connect)',
      summary: 'Cada empresa entra con su propio proveedor de identidad, además de correo y contraseña.',
      whatBreaks: 'Desaparece «Entrar con tu empresa» del inicio de sesión y los administradores de cada empresa no pueden configurar un proveedor. Correo y contraseña siguen funcionando para todos, y cualquier «exigir inicio de sesión de empresa» que una empresa ya hubiera guardado queda suspendido mientras esto esté apagado (nadie se queda fuera). Viene apagado: una instalación nueva muestra solo el formulario de correo.',
      note: 'Cada administrador configura su proveedor dentro de la app (emisor, Client ID y secreto, dominios de correo); el secreto se guarda cifrado, así que necesita almacenamiento de secretos. La URL de redirección que se registra en el proveedor sale de FRONTEND_URL: <FRONTEND_URL>/api/v1/auth/sso/callback. Las personas se crean al entrar, solo dentro de la empresa dueña de su dominio y nunca como administradores. Solo OpenID Connect: no hay SAML.',
    },
    worker: {
      name: 'Worker y tareas programadas',
      summary: 'Worker de entrenamiento y los ciclos programados.',
      whatBreaks: 'Con el worker apagado, los entrenamientos quedan en cola para siempre: se aceptan y nunca corren. Con el programador apagado, la alerta de inventario de las 8:00 UTC, los recálculos programados y el corte mensual nunca se disparan.',
      note: 'Los dos vienen encendidos para que un `uvicorn backend.main:app` pelado se comporte como el entorno de desarrollo. En un despliegue partido el contenedor de API apaga ambos y un contenedor de worker corre los ciclos. El programador debe estar encendido en EXACTAMENTE UNA instancia: dos programadores mandan cada alerta diaria dos veces.',
    },
    limits: {
      name: 'Techos de tamaño',
      summary: 'Techos de tamaño que protegen la memoria y la base de datos.',
      whatBreaks: 'No se apaga nada. Son rechazos, no funciones: pasarse de uno siempre es un rechazo explicado, nunca un recorte silencioso.',
      note: 'Son techos de infraestructura y NO son los límites comerciales del plan. Esos viven en `backend/entitlements/plans.py`.',
    },
    sql_sources: {
      name: 'Bases de datos de clientes',
      summary: 'Conexiones a las bases de datos propias de cada cliente (fuentes SQL).',
      whatBreaks: 'No se apaga nada. Deciden a qué direcciones de red puede conectarse una fuente SQL y cuántas conexiones puede tener abiertas un mismo cliente; una dirección rechazada o los cupos llenos siempre se explican en pantalla, nunca fallan en silencio.',
      note: 'En el servicio alojado las redes privadas quedan rechazadas: un cliente no debe poder apuntar una «base de datos» a la red interna de este servidor. Una instalación propia que se conecta a un ERP en su red local pone SQL_SOURCES_ALLOW_PRIVATE_HOSTS=true. Las direcciones link-local y de metadatos de la nube (169.254.0.0/16, fe80::/10 y las IP de metadatos conocidas) se rechazan siempre.',
    },
    api_surface: {
      name: 'Modo solo-API',
      summary: 'Modo de solo API pública.',
      whatBreaks: 'Encendido, esta instancia sirve ÚNICAMENTE los endpoints que el sistema de un cliente está invitado a llamar, más /health. La aplicación web servida desde este host deja de funcionar por completo — que es justamente el punto: está pensado para correr como una segunda instancia de la misma imagen.',
      note: 'Lo que NO compra: aislamiento de la base de datos. Las dos instancias siguen compartiendo un Postgres, así que un problema de base tumba la integración del cliente y la app juntas.',
    },
    // Operations
    operations: {
      name: 'Operación',
      summary: 'Umbrales del panel de estado de la instalación (cola, worker, disco, respaldo, latencia).',
      whatBreaks: 'No se apaga nada. Deciden cuándo el panel llama «con problemas» a una lectura; nunca bloquean una petición ni un trabajo.',
      note: 'La latencia se mide en el proceso de la API que responde (ventana en memoria, se pierde al reiniciar). Cola, latido del worker y fallidos salen de la base. Las lecturas de respaldo necesitan que el marcador del script sea visible para el contenedor de la API: ver deploy/RESTORE.md.',
    },
  },
  fields: {
    secret_key: 'Firma cada token de acceso y de refresco. Cambiarla cierra la sesión de todo el mundo al instante. Usa una cadena larga y aleatoria.',
    database_url: 'Cadena de conexión a PostgreSQL. La cola de trabajos ES una tabla de esta base, así que el worker y la API tienen que apuntar a la misma.',
    frontend_url: 'URL pública de la aplicación web. Todo enlace que el backend manda por correo se construye desde acá, así que un valor equivocado manda correo que funciona a una dirección muerta.',
    allowed_origins: 'Orígenes CORS autorizados a llamar la API desde un navegador. El origen del frontend tiene que estar en la lista o todas las llamadas fallan en el navegador mientras funcionan perfecto desde curl.',
    instance_admin_emails: 'Direcciones autorizadas a ver y editar la configuración de esta instalación. `admin` es un rol dentro de un tenant, así que no puede dar acceso a todo el despliegue. Vacío significa que nadie la edita desde la app.',
    environment: 'development | staging | production. En producción el servidor se NIEGA a arrancar con TESTING_MODE=true.',
    app_name: 'Nombre del producto en los asuntos de correo y en el título de la documentación de la API.',
    app_version: 'Versión que reportan /health y el documento OpenAPI.',
    access_token_expire_minutes: 'Duración del token de acceso. El frontend lo renueva en silencio, así que esto es una ventana de seguridad, no de experiencia.',
    algorithm: 'Algoritmo de firma del JWT. Déjalo en HS256 salvo que también cambies el material de la llave.',
    storage_path: 'Carpeta con los archivos subidos, los artefactos de modelo y los documentos. Postgres guarda los metadatos; acá están los bytes. NO entra en el respaldo de la base — respáldala aparte.',
    testing_mode: 'Saltea TODOS los cupos, límites de tasa y topes de subida. Solo para pruebas de carga y funcionales. El servidor se niega a arrancar con esto encendido si ENVIRONMENT=production.',
    deepseek_api_key: 'Llave de API de DeepSeek. Sin ella toda función de IA se reporta no disponible en vez de responder desde otro lado.',
    deepseek_model: '«deepseek-chat» es el modelo general y barato. «deepseek-reasoner» cuesta más y devuelve su razonamiento aparte.',
    deepseek_base_url: 'Base de la API. Apúntala a una pasarela compatible para enrutar por tu propio proxy — el formato es el de OpenAI.',
    resend_api_key: 'Llave de Resend. Cuando está puesta, Resend es el transporte y no se consulta SMTP.',
    email_from: 'Remitente que ve quien recibe. «onboarding@resend.dev» funciona sin verificar dominio y sirve para probar; un despliegue real debería mandar desde su propio dominio verificado.',
    smtp_server: 'Servidor SMTP del transporte de respaldo.',
    smtp_port: 'Puerto SMTP. 587 es STARTTLS, que es lo que usa el cliente.',
    smtp_user: 'Usuario SMTP; también es la dirección remitente por esa vía.',
    smtp_pass: 'Contraseña SMTP. En Gmail es una contraseña de aplicación, no la de la cuenta.',
    twilio_account_sid: 'SID de la cuenta de Twilio. Compartido con el canal de SMS.',
    twilio_auth_token: 'Token de Twilio. Es también la llave con la que Twilio firma los webhooks entrantes, así que un valor viejo rechaza los mensajes que llegan además de fallar al enviar.',
    twilio_whatsapp_from: 'Remitente, CON el prefijo «whatsapp:». El número de pruebas de Twilio sirve para probar y solo alcanza a quien se unió a esa sandbox.',
    whatsapp_webhook_base_url: 'Esquema y host públicos a los que Twilio hace POST, usados para reconstruir la URL firmada detrás de un proxy. Ponlo siempre que el bot corra detrás de TLS o del proxy del frontend.',
    whatsapp_bot_generic_mode: 'Encendido, el bot se salta el modelo de lenguaje y contesta un mensaje genérico, rápido y honesto. Las confirmaciones se siguen ejecutando igual. Es un parche para cuando no hay modelo pago.',
    twilio_sms_from: 'Remitente E.164 para SMS, sin el prefijo «whatsapp:».',
    voyageai_api_key: 'Llave de Voyage AI, usada para representar tanto los documentos como las preguntas.',
    pinecone_api_key: 'Llave de API de Pinecone para el almacén vectorial.',
    pinecone_index: 'Nombre del índice. Tiene que ser de 1024 dimensiones, coseno, serverless.',
    pinecone_environment: 'Región de Pinecone. Informativa para índices serverless.',
    integrations_secret_key: 'Llave Fernet que cifra todo secreto escrito desde el panel de configuración. Solo por entorno. El nombre es histórico y se conserva a propósito.',
    contact_whatsapp: 'E.164 sin el «+», como lo quiere wa.me.',
    contact_email: 'Dirección que abre el botón de «escríbenos».',
    upgrade_notify_email: 'A dónde se envían por correo las solicitudes de más espacio. Si está vacío usa CONTACT_EMAIL. La solicitud también queda guardada en la base, así que un correo fallido nunca pierde el pedido.',
    stripe_secret_key: 'Llave secreta de la API de Stripe (sk_live_… o sk_test_… en modo de prueba). Crea las sesiones de pago y del portal de clientes, y lee las suscripciones cuando llegan sus webhooks.',
    stripe_webhook_secret: 'Secreto de firma del webhook de Stripe registrado en <FRONTEND_URL>/api/v1/billing/stripe/webhook. Sin él no se puede verificar ningún evento de Stripe, así que no se aplica ninguno.',
    stripe_price_id_full: 'ID del precio recurrente MENSUAL del plan completo en Stripe. Su monto debe ser igual a BILLING_PRICE_USD_FULL.',
    paypal_client_id: 'Client ID de la app REST de PayPal (Developer Dashboard > Apps & Credentials), del modo que diga PAYPAL_MODE.',
    paypal_client_secret: 'Secreto de esa app REST de PayPal.',
    paypal_webhook_id: 'ID que PayPal le da al webhook registrado en <FRONTEND_URL>/api/v1/billing/paypal/webhook. Cada evento se verifica contra él con la API verify-webhook-signature de PayPal.',
    paypal_plan_id_full: 'ID del plan de facturación (mensual) del plan completo en PayPal. Su precio debe ser igual a BILLING_PRICE_USD_FULL.',
    paypal_mode: '«sandbox» o «live». Decide a qué API de PayPal pertenecen las credenciales de arriba; cualquier otro valor apaga PayPal.',
    billing_price_usd_full: 'Precio mensual del plan completo en USD, tal como lo MUESTRA la app. Lo que se cobra es el precio de Stripe o el plan de PayPal: mantenlos iguales. La página de precios solo ofrece la compra en línea mientras coincida con su propia cifra.',
    enterprise_sso_enabled: 'Interruptor general del inicio de sesión de empresa. En false oculta la opción y suspende el proveedor y el «exigir» de cada empresa sin borrarlos.',
    social_login_enabled: 'Interruptor general. En false oculta todos los botones sin borrar las credenciales de abajo, para pausar y reanudar la función.',
    google_oauth_client_id: 'ID de cliente OAuth de tipo «Aplicación web» en Google Cloud Console. URI de redirección autorizado: <FRONTEND_URL>/api/v1/auth/oauth/google/callback.',
    google_oauth_client_secret: 'Secreto de ese cliente OAuth de Google. Sin él (o sin el ID) no aparece el botón de Google.',
    microsoft_oauth_client_id: 'ID de aplicación (cliente) de un registro de app en Microsoft Entra cuyos tipos de cuenta admitidos son «Cuentas en cualquier directorio organizativo y cuentas personales de Microsoft». URI de redirección web: <FRONTEND_URL>/api/v1/auth/oauth/microsoft/callback. Agrega la reclamación opcional del token de ID `xms_edov`, o las cuentas de trabajo y escuela no podrán crear ni vincular cuentas.',
    microsoft_oauth_client_secret: 'VALOR del secreto de cliente (no el ID del secreto) de ese registro de app. Vence (24 meses como máximo): renuévalo antes o el botón de Microsoft empieza a fallar. Sin él (o sin el ID) no aparece el botón de Microsoft.',
    apple_oauth_service_id: 'Identificador del SERVICES ID de Sign in with Apple (no el App ID). Return URL: <FRONTEND_URL>/api/v1/auth/oauth/apple/callback. Apple solo acepta URLs https.',
    apple_oauth_team_id: 'Team ID de 10 caracteres de Apple Developer; firma el client secret que este servidor genera en cada inicio de sesión con Apple.',
    apple_oauth_key_id: 'Key ID de la llave privada de Sign in with Apple (.p8).',
    apple_oauth_private_key: 'Contenido del archivo .p8, con las líneas BEGIN/END. Pegarlo en una sola línea está bien: los saltos de línea se reconstruyen. Sin ella no se le puede pedir un token a Apple y su botón no aparece.',
    inbound_email_domain: 'Dominio donde viven las direcciones de cada cuenta (sales+<token>@<dominio>). Su registro MX debe apuntar a tu proveedor de correo entrante.',
    inbound_email_secret: 'Secreto compartido que autentica las llamadas del webhook del proveedor. Usa una cadena larga y aleatoria; si lo cambias, actualiza también el webhook del proveedor.',
    worker_enabled: 'Corre en este proceso el ciclo que toma y entrena los trabajos.',
    scheduler_enabled: 'Corre los ciclos programados: trabajos agendados, alertas diarias y corte mensual. Solo una instancia puede tenerlo encendido.',
    worker_id: 'Identidad con la que se toman trabajos y se recuperan los que quedaron corriendo tras una caída. Vacío usa el nombre del host — dale un id FIJO a un worker de larga vida para que sus trabajos huérfanos se sigan reconociendo después de recrear el contenedor.',
    max_concurrent_jobs: 'Entrenamientos que este worker corre a la vez.',
    worker_poll_interval_seconds: 'Segundos entre consultas a la tabla de trabajos.',
    max_upload_size_mb: 'Tope duro de un archivo subido, por encima del límite del propio plan.',
    dataset_editor_max_rows: 'Filas que el editor de datos abre. Se revisa contra el conteo guardado ANTES de leer el archivo, así que uno enorme nunca se carga en memoria solo para descubrir que no cabía.',
    dataset_editor_max_mb: 'El mismo resguardo, por tamaño de archivo.',
    sql_materialize_max_rows: 'Tope de filas al convertir una consulta SQL en un archivo. Pasarse es un rechazo, nunca un recorte.',
    sql_sources_allow_private_hosts: 'Deja que las fuentes SQL se conecten a direcciones locales y de redes privadas (RFC 1918, CGNAT, IPv6 de uso local). Apagado por defecto; enciéndelo solo en una instalación propia cuyas bases de datos estén en su propia red.',
    sql_sources_max_concurrent_per_tenant: 'Conexiones que un mismo cliente puede tener abiertas a la vez hacia sus bases de datos (pruebas, consultas, exportaciones, actualizaciones). La siguiente espera hasta 10 segundos y luego se rechaza con un mensaje claro.',
    accuracy_degradation_threshold_pct: 'Cuánto peor (en porcentaje relativo) debe rendir un pronóstico contra las ventas reales, comparado con su precisión al entrenarse, para que la app avise una sola vez. Es solo un aviso: nada se reentrena solo.',
    reforecast_full_refit_days: 'Edad en días a partir de la cual un reentrenamiento programado en modo «actualizar a diario, reajustar periódicamente» deja de usar los modelos guardados y los entrena de nuevo. Con menos edad, las ventas nuevas solo adelantan el pronóstico.',
    public_api_only: 'Servir en esta instancia únicamente la superficie pública de integración.',
    // Operations thresholds
    ops_queue_wait_degraded_minutes: 'Minutos que un trabajo puede esperar en cola antes de que la cola se marque con problemas.',
    ops_running_job_degraded_minutes: 'Minutos de ejecución a partir de los cuales un trabajo se reporta como posiblemente trabado.',
    ops_worker_heartbeat_stale_seconds: 'Segundos sin latido del worker a partir de los cuales se considera que nadie toma trabajos.',
    ops_disk_free_min_percent: 'Porcentaje mínimo de espacio libre en el volumen de storage o de respaldos.',
    ops_backup_max_age_hours: 'Horas máximas desde el último respaldo exitoso. 36 tolera una noche perdida.',
    ops_pool_saturation_percent: 'Porcentaje del pool de conexiones en uso a partir del cual se marca con problemas.',
    ops_latency_slo_ms: 'Milisegundos de p95 por encima de los cuales una familia de rutas se marca con problemas.',
    ops_slow_query_ms: 'Milisegundos a partir de los cuales una consulta cuenta como lenta (solo se guarda el conteo y la peor; nunca los parámetros).',
    backup_status_path: 'Ruta, vista desde el contenedor de la API, del marcador JSON que escribe el script de respaldo. Vacía = la lectura de respaldo queda «desconocida».',
    backup_dir: 'Carpeta de respaldos, vista desde el contenedor de la API, para medir su espacio libre. Vacía omite esa lectura.',
  },
  ui: {
    title: 'Instalación',
    lead: 'Qué servicios tiene este despliegue, cuáles están encendidos y qué se pierde con los que no.',
    tabInstance: 'Esta instalación',
    tabTenant: 'Mis canales',
    stateReady: 'Listo',
    stateNotConfigured: 'Sin configurar',
    stateDegraded: 'Con problemas',
    stateOff: 'Apagado',
    stateOn: 'Encendido',
    sourceTenant: 'de tu empresa',
    sourceInstance: 'de este panel',
    sourceEnv: 'del entorno',
    sourceDefault: 'valor por defecto',
    sourceLabel: 'Manda',
    missingLabel: 'Falta',
    orElse: 'o bien',
    envOnly: 'Solo entorno',
    envOnlyHelp: 'Se cambia en el archivo .env y requiere reiniciar. El panel la muestra, nunca la escribe.',
    secretSet: 'Guardada',
    secretInherited: 'La usa la instalación. Deja esto vacío para seguir usándola.',
    secretEmpty: 'Sin guardar',
    secretPlaceholder: 'Pega el valor nuevo',
    clearHint: 'Déjalo vacío y guarda para volver al valor del entorno.',
    required: 'Obligatoria',
    save: 'Guardar',
    saving: 'Guardando…',
    saved: 'Guardado',
    test: 'Probar conexión',
    testing: 'Probando…',
    reset: 'Volver al entorno',
    resetConfirm: '¿Borrar lo que este panel guardó para este servicio y volver a lo que dice el entorno?',
    probeOk: 'Responde correctamente.',
    probeNotConfigured: 'Falta configurarlo.',
    probeAuthFailed: 'El proveedor rechazó la credencial.',
    probeUnreachable: 'No se pudo alcanzar al proveedor (red o DNS).',
    probeTimeout: 'El proveedor aceptó la conexión y no respondió a tiempo.',
    probeRejected: 'El proveedor respondió con un error.',
    probeMissingDependency: 'Falta instalar un paquete en el servidor.',
    probeUnexpected: 'Error inesperado al probar.',
    lastCheck: 'Última prueba',
    neverChecked: 'Sin probar todavía',
    bootstrapNotice: 'Administras esta instalación porque tu empresa es la única que hay en ella. Ese acceso se termina en cuanto entre una segunda: antes de invitar a alguien, pon tu correo en INSTANCE_ADMIN_EMAILS (en el archivo .env del servidor) y reinicia, o nadie podrá volver a abrir esta pestaña.',
    operatorDisabledTitle: 'Esta instalación no tiene operador',
    operatorDisabledBody: 'Nadie puede editar la configuración de servicios desde la app hasta que INSTANCE_ADMIN_EMAILS tenga al menos una dirección. Se pone en el archivo .env del backend y requiere reiniciar.',
    notOperatorTitle: 'Esta cuenta no opera la instalación',
    notOperatorBody: 'La configuración de servicios pertenece a quien administra el despliegue, no al rol admin de una empresa. Puedes configurar tus propios canales en la otra pestaña.',
    storeUnavailable: 'No se pudo leer la configuración guardada en la base. Lo que ves viene del entorno.',
    encryptionUnavailable: 'Sin INTEGRATIONS_SECRET_KEY no se puede guardar ningún secreto desde acá: se rechaza en vez de guardarse sin cifrar.',
    encryptionGenerated: 'Esta instalación generó su propia llave de cifrado en storage/instance_secret.key, porque INTEGRATIONS_SECRET_KEY está vacía. Funciona, pero respalda esa carpeta: si se pierde el archivo hay que volver a ingresar todos los secretos guardados. Y antes de correr un segundo proceso en otro volumen, copia su contenido a INTEGRATIONS_SECRET_KEY o cada uno hará la suya y no podrá leer la del otro.',
    undocumented: 'Hay ajustes sin documentar en el registro',
    tenantLead: 'Los canales que llevan tu identidad a tu propia gente. Lo que dejes vacío usa lo que tenga la instalación.',
    tenantEmpty: 'Esta instalación no expone ningún canal configurable por empresa.',
    noneEditable: 'Este servicio se configura solo por entorno.',
  },
  // Panel Estado de la instalación
  ops: {
    title: 'Estado de la instalación',
    refresh: 'Actualizar',
    overall: { ok: 'Todo en orden', degraded: 'Hay algo fuera de su umbral', unknown: 'Hay lecturas que no se pueden tomar' },
    checks: {
      queue: 'Cola de trabajos', running_jobs: 'Trabajos en curso', worker_heartbeat: 'Latido del worker',
      db_pool: 'Conexiones a la base', disk_storage: 'Disco de storage', disk_backup: 'Disco de respaldos',
      backup: 'Último respaldo', latency: 'Latencia',
    },
    queueLine: 'En cola',
    oldestQueued: 'El más antiguo espera',
    running: 'En ejecución',
    failed24h: 'Fallidos en 24 h por clase de error',
    noFailures: 'Ninguno',
    heartbeatLine: 'Último latido',
    never: 'nunca',
    latencyTitle: 'Latencia por familia de rutas',
    latencyScope: 'Solo este proceso de la API, últimos 15 minutos; se pierde al reiniciar.',
    noTraffic: 'Sin tráfico en la ventana.',
    slowQueries: 'Consultas lentas en la última hora',
    loadError: 'No se pudo leer el estado de la instalación.',
  },
}

const en: ServiceConfigCopy = {
  services: {
    core: {
      name: 'Core',
      summary: 'Process identity, database and signing key.',
      whatBreaks: 'Nothing starts. These are read once, at boot.',
      note: 'Environment only, on purpose: the connection pool and the token signer read them at import time, so a value written from the panel would not take effect until a restart — and a wrong one would stop the restart from succeeding.',
    },
    llm: {
      name: 'Assistant (DeepSeek)',
      summary: 'DeepSeek, the only language model. Powers every AI feature.',
      whatBreaks: 'The assistant stops answering, the morning summary and the inventory read fall back to rule-based text, the document analyst goes off, and the data diagnosis reports only its deterministic checks. Nothing errors: each screen says the assistant is unavailable.',
      note: 'One provider, no fallback chain. An earlier version used whichever key happened to be set, so a typo in the variable name produced working AI features answered by a different vendor — visible only on the invoice.',
    },
    email: {
      name: 'Email',
      summary: 'Transactional email — Resend first, SMTP as the fallback.',
      whatBreaks: 'No email leaves: account verification, password reset, invitations, the daily inventory digest, the monthly recap and purchase orders to suppliers. Every send is logged and reported as not delivered — the app never claims it mailed something it did not.',
      note: 'Resend wins when its key is set; otherwise SMTP. With neither, a send fails out loud instead of quietly. What a tenant sets here applies ONLY to mail addressed to its own people and suppliers: verification, password reset and invitations always leave through the installation transport, because a tenant must not send the message that grants access to an account.',
    },
    whatsapp: {
      name: 'WhatsApp',
      summary: 'WhatsApp through Twilio — daily alerts, supplier messages and the bot.',
      whatBreaks: 'No WhatsApp message is sent or received: the daily inventory alert loses that channel (email still goes if configured), purchase orders cannot be sent over WhatsApp, and the bot never replies.',
      note: '`WHATSAPP_WEBHOOK_BASE_URL` is the one that looks optional and is not, once the bot is in use. Twilio signs the inbound webhook over the PUBLIC url; behind a proxy the backend sees an internal one, the signature never matches, and every inbound message is rejected with 403.',
    },
    sms: {
      name: 'SMS',
      summary: 'SMS through Twilio — a heads-up for team messages.',
      whatBreaks: 'No text goes out for a team message. The message itself is still delivered in the app; only the nudge is lost.',
      note: 'Shares the Twilio credentials with WhatsApp and adds one: the sender. A number with the "whatsapp:" prefix CANNOT send SMS, which is why this is a separate variable rather than a reuse.',
    },
    rag: {
      name: 'Document search',
      summary: 'Document search — Voyage AI embeddings over a Pinecone index.',
      whatBreaks: 'Uploaded documents stop being indexed and the analyst can no longer cite them. The assistant still answers from the tenant’s own data; it just has nothing to quote.',
      note: 'Needs the keys and the `voyageai` and `pinecone` packages installed — a missing package disables the service the same way a missing key does, and says which. The index must be 1024 dimensions, cosine metric.',
    },
    secret_storage: {
      name: 'Secret encryption',
      summary: 'The Fernet key that encrypts every secret /instalacion stores.',
      whatBreaks: 'Nothing, immediately — and that is the part worth knowing. With the variable unset the installation GENERATES a key into storage/instance_secret.key, so the panel keeps saving secrets; it reads “not configured” because the variable is empty, not because the feature is off, and it says below which key is in effect. What is lost is durability: that file must be backed up with storage/, and two processes on separate volumes generate DIFFERENT keys and cannot read each other’s secrets. Only when no key can be written either — a read-only disk — do the secret fields start refusing, and they do it out loud rather than storing anything unencrypted.',
      note: '`INTEGRATIONS_SECRET_KEY` is a Fernet key and is environment-only on purpose: it encrypts the secrets this very panel writes, so a panel that could rewrite it would make its own stored secrets unreadable with one click. Losing it means re-entering every stored secret. The name is historical and kept on purpose: renaming it would orphan every existing deployment’s stored secrets.',
    },
    contact: {
      name: 'Commercial contact',
      summary: 'How a customer reaches you to lift the free tier’s ceilings.',
      whatBreaks: 'The "write to us" buttons disappear. A free tenant that hits a ceiling then has no way to ask for more room. Unless online payment is configured, that is the entire commercial surface of the product — and the corporate plan is only ever sold this way.',
      note: 'An empty channel is HIDDEN rather than shown broken: a button opening a blank WhatsApp link is worse than no button. Configure at least one.',
    },
    billing: {
      name: 'Online payment (Stripe and PayPal)',
      summary: 'Buy the full plan online, by card (Stripe) or PayPal, on their own pages.',
      whatBreaks: 'The "Move to the full plan" button and the checkout in Plan and payment disappear; the status says online payment is off and which variables are missing. Everything else is unchanged: customers write to you and you set the plan by hand, as before. Subscriptions already sold keep their plan, but if you remove the webhook secret their renewals and cancellations stop being applied: never empty a provider that has customers.',
      note: 'Hosted pages only: no card number, CVC or PayPal password ever passes through this server or the app\'s JavaScript. The ONLY thing that changes a company\'s plan is a webhook with a verified signature (Stripe: HMAC-SHA256 of Stripe-Signature, 5-minute tolerance; PayPal: the verify-webhook-signature API). Webhook URLs to register: <FRONTEND_URL>/api/v1/billing/stripe/webhook and <FRONTEND_URL>/api/v1/billing/paypal/webhook. Only the full plan is sold, monthly; corporate is never bought online. A past-due payment keeps the plan for 7 days; when the subscription ends the company moves to the free plan and nothing is deleted. A full plan set by hand is never touched by online payment.',
    },
    social_login: {
      name: 'Sign in with Google, Microsoft and Apple',
      summary: 'Sign in with Google, Microsoft or Apple, next to email + password.',
      whatBreaks: 'The "Continue with Google / Microsoft / Apple" buttons disappear from the login and signup screens; email + password keeps working exactly as before. People who only signed in with a provider must use "Forgot password?" to set one. Off by default: a new install shows only the email form until you configure a provider AND turn SOCIAL_LOGIN_ENABLED on.',
      note: 'Each provider shows its button only when the switch is on AND every one of its fields is set; a half-filled provider is never offered. The redirect URL each console asks for is built from FRONTEND_URL: <FRONTEND_URL>/api/v1/auth/oauth/google/callback, <FRONTEND_URL>/api/v1/auth/oauth/microsoft/callback and <FRONTEND_URL>/api/v1/auth/oauth/apple/callback. Step by step: docs/social-login.md. An existing account is linked only through an email the PROVIDER verified; if that email had never been verified here, its password is removed on linking, because whoever chose it never proved they own the mailbox. Microsoft does not verify the email claim of work or school accounts: there an address counts as verified only when Microsoft sends `xms_edov` (add it as an optional ID-token claim in the app registration) or `email_verified`; personal Microsoft accounts (outlook.com, hotmail.com...) are accepted because Microsoft verifies those addresses itself. Without that proof Microsoft sign-in cannot create or link an account, only sign in an identity linked earlier.',
    },
    inbound_email: {
      name: 'Sales by e-mail',
      summary: 'Receive sales files forwarded by e-mail to a private per-account address.',
      whatBreaks: 'The "Sales by e-mail" card says this installation cannot receive e-mail yet, and POST /api/v1/inbound/email answers the structured `inbound_email_disabled` error. Everything else keeps working: files are still uploaded by hand.',
      note: 'Needs a mail provider that can forward inbound mail to a webhook (Postmark, Mailgun, Resend or a relay) and an MX record for the inbound domain. The webhook is authenticated by INBOUND_EMAIL_SECRET: an `X-StockAI-Signature` HMAC header or HTTP Basic auth whose password is the secret. Step by step: docs/inbound-email.md.',
    },
    enterprise_sso: {
      name: 'Company sign-in (OpenID Connect)',
      summary: 'Each company signs in with its own identity provider, next to email + password.',
      whatBreaks: 'The "Sign in with your company" option disappears from the login screen and each company\'s administrators cannot configure a provider. Email + password keeps working for everyone, and any "require company sign-in" a company already saved is suspended while this is off (so nobody is locked out). Off by default: a new install shows only the email form.',
      note: 'Each administrator configures their provider inside the app (issuer, Client ID and secret, email domains); the secret is stored encrypted, so it needs secret storage. The redirect URL registered at the provider is built from FRONTEND_URL: <FRONTEND_URL>/api/v1/auth/sso/callback. People are created when they sign in, only inside the company that owns their domain and never as administrators. OpenID Connect only: there is no SAML.',
    },
    worker: {
      name: 'Worker and scheduled jobs',
      summary: 'Training worker and the scheduled loops.',
      whatBreaks: 'With the worker off, training sessions queue forever: accepted and never run. With the scheduler off, the 08:00 UTC inventory alert, scheduled recalculations and the monthly snapshot never fire.',
      note: 'Both ship on so a bare `uvicorn backend.main:app` behaves like the development setup. In a split deployment the API container turns both off and one worker container runs the loops. The scheduler must be on in EXACTLY ONE instance: two schedulers send every daily alert twice.',
    },
    limits: {
      name: 'Size ceilings',
      summary: 'Size ceilings that protect memory and the database.',
      whatBreaks: 'Nothing turns off. These are refusals, not features: exceeding one is always a stated rejection, never a silent truncation.',
      note: 'These are infrastructure ceilings and NOT the commercial tier limits. Those live in `backend/entitlements/plans.py`.',
    },
    sql_sources: {
      name: 'Customer databases',
      summary: 'Connections to customers’ own databases (SQL data sources).',
      whatBreaks: 'Nothing turns off. They decide which network addresses a SQL data source may reach and how many connections one tenant may hold open at once; a refused address or a full set of slots is always explained on screen, never a silent failure.',
      note: 'Hosted deployments keep private hosts refused: a tenant must not be able to point a “database” at this server’s own network. A self-hosted installation that connects to an ERP database on its LAN sets SQL_SOURCES_ALLOW_PRIVATE_HOSTS=true. Link-local and cloud metadata addresses (169.254.0.0/16, fe80::/10 and the known metadata IPs) are refused either way.',
    },
    api_surface: {
      name: 'Public-API-only mode',
      summary: 'Public-API-only mode.',
      whatBreaks: 'With it on, this instance serves ONLY the endpoints a customer’s own system is invited to call, plus /health. The web app served from this host stops working entirely — which is the point: it is meant to run as a second instance of the same image.',
      note: 'What it does NOT buy: isolation from the database. Both instances still share one Postgres, so a database problem takes down the customer’s integration and the app together.',
    },
    // Operations
    operations: {
      name: 'Operations',
      summary: 'Thresholds behind the installation status panel (queue, worker, disk, backup, latency).',
      whatBreaks: 'Nothing turns off. They decide when the panel calls a reading degraded; they never block a request or a job.',
      note: 'Latency is measured in the API process that answers (in-memory window, lost on restart). Queue, worker heartbeat and failures come from the database. The backup readings need the backup script marker to be visible to the API container: see deploy/RESTORE.md.',
    },
  },
  fields: {
    secret_key: 'Signs every access and refresh token. Changing it logs everyone out immediately. Use a long random string.',
    database_url: 'PostgreSQL connection string. The job queue IS a table in this database, so the worker and the API must point at the same one.',
    frontend_url: 'Public base URL of the web app. Every link the backend emails is built from it, so a wrong value sends working mail to a dead address.',
    allowed_origins: 'CORS origins allowed to call the API from a browser. The frontend’s own origin must be in the list or every call fails in the browser while working perfectly from curl.',
    instance_admin_emails: 'Addresses allowed to see and edit this installation’s configuration. `admin` is a role inside a tenant, so it cannot grant deployment-wide access. Empty means nobody edits it from the app.',
    environment: 'development | staging | production. In production the server REFUSES to boot with TESTING_MODE=true.',
    app_name: 'Product name in email subjects and in the API documentation title.',
    app_version: 'Version reported by /health and the OpenAPI document.',
    access_token_expire_minutes: 'Access-token lifetime. The frontend refreshes silently, so this is a security window, not a UX one.',
    algorithm: 'JWT signing algorithm. Leave it at HS256 unless you are also changing the key material.',
    storage_path: 'Directory holding uploaded files, model artifacts and documents. Postgres holds the metadata; these are the bytes. It is NOT in the database backup — back it up separately.',
    testing_mode: 'Bypasses ALL quotas, rate limits and upload caps. For load and functional testing only. The server refuses to boot with this on when ENVIRONMENT=production.',
    deepseek_api_key: 'DeepSeek API key. Without it every AI feature reports itself unavailable instead of answering from somewhere else.',
    deepseek_model: '"deepseek-chat" is the cheap general model. "deepseek-reasoner" costs more and returns its reasoning separately.',
    deepseek_base_url: 'API base. Point it at a compatible gateway to route through your own proxy — the wire format is OpenAI-shaped.',
    resend_api_key: 'Resend API key. When set, Resend is the transport and SMTP is not consulted.',
    email_from: 'Sender shown to the recipient. "onboarding@resend.dev" works without domain verification and is fine for a trial; a real deployment should send from its own verified domain.',
    smtp_server: 'SMTP host for the fallback transport.',
    smtp_port: 'SMTP port. 587 is STARTTLS, which is what the client uses.',
    smtp_user: 'SMTP username; also the From address on that path.',
    smtp_pass: 'SMTP password. For Gmail this is an app password, not the account password.',
    twilio_account_sid: 'Twilio account SID. Shared with the SMS channel.',
    twilio_auth_token: 'Twilio auth token. Also the key Twilio signs inbound webhooks with, so an outdated value rejects incoming messages as well as failing to send.',
    twilio_whatsapp_from: 'Sender, WITH the "whatsapp:" prefix. Twilio’s sandbox number works for testing and only reaches people who joined that sandbox.',
    whatsapp_webhook_base_url: 'Public scheme and host Twilio POSTs to, used to rebuild the signed url behind a proxy. Set it whenever the bot runs behind TLS termination or the frontend proxy.',
    whatsapp_bot_generic_mode: 'When on, the bot skips the language model and replies with a fast, honest generic message. Confirmations still execute. A stopgap for when no model is funded.',
    twilio_sms_from: 'Plain E.164 sender for SMS, without the "whatsapp:" prefix.',
    voyageai_api_key: 'Voyage AI key, used to embed both documents and questions.',
    pinecone_api_key: 'Pinecone API key for the vector store.',
    pinecone_index: 'Index name. Must be 1024 dimensions, cosine, serverless.',
    pinecone_environment: 'Pinecone region. Informational for serverless indexes.',
    integrations_secret_key: 'Fernet key encrypting every secret written from the configuration panel. Environment only. The name is historical and kept on purpose.',
    contact_whatsapp: 'E.164 without the "+", the way wa.me wants it.',
    contact_email: 'Address the "write to us" button opens.',
    upgrade_notify_email: 'Where in-app requests for more room are emailed. Falls back to CONTACT_EMAIL when empty. The request is also stored, so a failed email never loses the ask.',
    stripe_secret_key: 'Stripe secret API key (sk_live_... or sk_test_... in test mode). Creates the checkout and customer-portal sessions and reads subscriptions when their webhooks arrive.',
    stripe_webhook_secret: 'Signing secret of the Stripe webhook registered at <FRONTEND_URL>/api/v1/billing/stripe/webhook. Without it no Stripe event can be verified, so none is applied.',
    stripe_price_id_full: 'ID of the full plan\'s recurring MONTHLY Stripe price. Its amount must equal BILLING_PRICE_USD_FULL.',
    paypal_client_id: 'Client ID of the PayPal REST app (Developer Dashboard > Apps & Credentials), for the mode set in PAYPAL_MODE.',
    paypal_client_secret: 'Secret of that PayPal REST app.',
    paypal_webhook_id: 'ID PayPal gives the webhook registered at <FRONTEND_URL>/api/v1/billing/paypal/webhook. Every event is verified against it with PayPal\'s verify-webhook-signature API.',
    paypal_plan_id_full: 'ID of the full plan\'s (monthly) PayPal billing plan. Its price must equal BILLING_PRICE_USD_FULL.',
    paypal_mode: '"sandbox" or "live". Decides which PayPal API the credentials above belong to; any other value turns PayPal off.',
    billing_price_usd_full: 'Monthly price of the full plan in USD, as the app SHOWS it. What is charged is the Stripe price or PayPal plan: keep them equal. The pricing page offers online purchase only while this matches its own figure.',
    enterprise_sso_enabled: 'Master switch for company sign-in. False hides the option and suspends every company\'s provider and "require" setting without deleting them.',
    social_login_enabled: 'Master switch. False hides every social button without deleting the credentials below, so the feature can be paused and resumed.',
    google_oauth_client_id: 'OAuth client ID of a "Web application" client in Google Cloud Console. Authorized redirect URI: <FRONTEND_URL>/api/v1/auth/oauth/google/callback.',
    google_oauth_client_secret: 'Client secret of that Google OAuth client. Without it (or the ID) the Google button is not shown.',
    microsoft_oauth_client_id: 'Application (client) ID of a Microsoft Entra app registration whose supported account types are "Accounts in any organizational directory and personal Microsoft accounts". Web redirect URI: <FRONTEND_URL>/api/v1/auth/oauth/microsoft/callback. Add the optional ID-token claim `xms_edov`, or work and school accounts cannot create or link accounts.',
    microsoft_oauth_client_secret: 'Client secret VALUE (not the secret ID) of that app registration. It expires (24 months at most): renew it before then or the Microsoft button starts failing. Without it (or the ID) the Microsoft button is not shown.',
    apple_oauth_service_id: 'Identifier of the Sign in with Apple SERVICES ID (not the App ID). Return URL: <FRONTEND_URL>/api/v1/auth/oauth/apple/callback. Apple only accepts https URLs.',
    apple_oauth_team_id: '10-character Apple Developer Team ID; the issuer of the client secret this server signs for every Apple sign-in.',
    apple_oauth_key_id: 'Key ID of the Sign in with Apple private key (.p8).',
    apple_oauth_private_key: 'Contents of the .p8 key file, BEGIN/END lines included. Pasting it on one line is fine; the line breaks are restored. Without it Apple cannot be asked for a token and its button is not shown.',
    inbound_email_domain: 'Domain the per-account addresses live on (sales+<token>@<domain>). Its MX record must point at your inbound mail provider.',
    inbound_email_secret: 'Shared secret that authenticates the webhook calls from the mail provider. Use a long random string; changing it requires updating the webhook settings at the provider too.',
    worker_enabled: 'Runs the job-claim and training loop in this process.',
    scheduler_enabled: 'Runs the scheduled loops: scheduled jobs, daily alerts, monthly snapshot. Exactly one instance may have this on.',
    worker_id: 'Identity used to claim jobs and to recover the ones left running after a crash. Empty falls back to the host name — give a long-lived worker a FIXED id so its orphans are still recognised after the container is recreated.',
    max_concurrent_jobs: 'Training jobs this worker runs at once.',
    worker_poll_interval_seconds: 'Seconds between polls of the jobs table.',
    max_upload_size_mb: 'Hard ceiling on an uploaded file, above the tier’s own limit.',
    dataset_editor_max_rows: 'Rows the in-app data editor will open. Checked against the stored row count BEFORE reading the file, so a huge one is never loaded into memory just to find out it did not fit.',
    dataset_editor_max_mb: 'The same guard, by file size.',
    sql_materialize_max_rows: 'Row ceiling when turning a SQL query into a file. Exceeding it is a refusal, never a truncation.',
    sql_sources_allow_private_hosts: 'Let SQL data sources connect to loopback and private-network addresses (RFC 1918, CGNAT, IPv6 unique-local). Off by default; turn it on only on a self-hosted installation whose databases live on its own network.',
    sql_sources_max_concurrent_per_tenant: 'Connections one tenant may have open to its databases at the same time (tests, queries, exports, refreshes). The next one waits up to 10 seconds, then is refused with a clear message.',
    accuracy_degradation_threshold_pct: 'How much worse (relative percent) a forecast must perform against real sales, compared with its accuracy at training, before the app raises its single alert. A notice only: nothing retrains by itself.',
    reforecast_full_refit_days: 'Age in days after which a scheduled retrain set to "update daily, refit periodically" stops using the stored models and trains them again. Younger than that, new sales only advance the forecast.',
    public_api_only: 'Serve only the public integration surface on this instance.',
    // Operations thresholds
    ops_queue_wait_degraded_minutes: 'Minutes a job may wait in the queue before the queue is called degraded.',
    ops_running_job_degraded_minutes: 'Minutes of running time after which a job is reported as possibly stuck.',
    ops_worker_heartbeat_stale_seconds: 'Seconds without a worker heartbeat after which nobody is considered to be claiming jobs.',
    ops_disk_free_min_percent: 'Minimum free-space percentage on the storage or backup volume.',
    ops_backup_max_age_hours: 'Maximum hours since the last successful backup. 36 tolerates one missed night.',
    ops_pool_saturation_percent: 'Share of the connection pool in use at which the pool is called degraded.',
    ops_latency_slo_ms: 'p95 milliseconds above which a route family is called degraded.',
    ops_slow_query_ms: 'Milliseconds from which a statement counts as slow (only the count and the worst are kept; never the parameters).',
    backup_status_path: 'Path, as the API container sees it, of the JSON marker the backup script writes. Empty = the backup reading reports unknown.',
    backup_dir: 'Backup folder, as the API container sees it, used to measure its free space. Empty skips that reading.',
  },
  ui: {
    title: 'Installation',
    lead: 'What services this deployment has, which are on, and what is lost with the ones that are not.',
    tabInstance: 'This installation',
    tabTenant: 'My channels',
    stateReady: 'Ready',
    stateNotConfigured: 'Not configured',
    stateDegraded: 'Failing',
    stateOff: 'Off',
    stateOn: 'On',
    sourceTenant: 'from your company',
    sourceInstance: 'from this panel',
    sourceEnv: 'from the environment',
    sourceDefault: 'built-in default',
    sourceLabel: 'In effect',
    missingLabel: 'Missing',
    orElse: 'or else',
    envOnly: 'Environment only',
    envOnlyHelp: 'Changed in the .env file and requires a restart. The panel reports it, never writes it.',
    secretSet: 'Stored',
    secretInherited: 'The installation provides it. Leave this empty to keep using it.',
    secretEmpty: 'Not stored',
    secretPlaceholder: 'Paste the new value',
    clearHint: 'Leave it empty and save to go back to the environment value.',
    required: 'Required',
    save: 'Save',
    saving: 'Saving…',
    saved: 'Saved',
    test: 'Test connection',
    testing: 'Testing…',
    reset: 'Back to the environment',
    resetConfirm: 'Delete what this panel stored for this service and go back to what the environment says?',
    probeOk: 'Answers correctly.',
    probeNotConfigured: 'Still needs configuring.',
    probeAuthFailed: 'The provider rejected the credential.',
    probeUnreachable: 'The provider could not be reached (network or DNS).',
    probeTimeout: 'The provider accepted the connection and did not answer in time.',
    probeRejected: 'The provider answered with an error.',
    probeMissingDependency: 'A package is missing on the server.',
    probeUnexpected: 'Unexpected error while testing.',
    lastCheck: 'Last test',
    neverChecked: 'Not tested yet',
    bootstrapNotice: 'You administer this installation because yours is the only company on it. That access ends the moment a second one signs up: before inviting anybody, put your address in INSTANCE_ADMIN_EMAILS (in the server’s .env file) and restart, or nobody will be able to open this tab again.',
    operatorDisabledTitle: 'This installation has no operator',
    operatorDisabledBody: 'Nobody can edit service configuration from the app until INSTANCE_ADMIN_EMAILS holds at least one address. It goes in the backend’s .env file and requires a restart.',
    notOperatorTitle: 'This account does not operate the installation',
    notOperatorBody: 'Service configuration belongs to whoever administers the deployment, not to a company’s admin role. You can configure your own channels in the other tab.',
    storeUnavailable: 'The configuration stored in the database could not be read. What you see comes from the environment.',
    encryptionUnavailable: 'Without INTEGRATIONS_SECRET_KEY no secret can be stored from here: it is refused rather than stored unencrypted.',
    encryptionGenerated: 'This installation generated its own encryption key at storage/instance_secret.key, because INTEGRATIONS_SECRET_KEY is empty. It works, but back that folder up: losing the file means re-entering every stored secret. And before running a second process on another volume, copy its contents into INTEGRATIONS_SECRET_KEY, or each one will make its own and be unable to read the other’s.',
    undocumented: 'There are settings missing from the registry',
    tenantLead: 'The channels that carry your identity to your own people. Anything left empty uses what the installation has.',
    tenantEmpty: 'This installation exposes no per-company channel.',
    noneEditable: 'This service is configured by environment only.',
  },
  // Installation status panel
  ops: {
    title: 'Installation status',
    refresh: 'Refresh',
    overall: { ok: 'All within thresholds', degraded: 'Something is outside its threshold', unknown: 'Some readings cannot be taken' },
    checks: {
      queue: 'Job queue', running_jobs: 'Running jobs', worker_heartbeat: 'Worker heartbeat',
      db_pool: 'Database connections', disk_storage: 'Storage disk', disk_backup: 'Backup disk',
      backup: 'Last backup', latency: 'Latency',
    },
    queueLine: 'Queued',
    oldestQueued: 'Oldest waits',
    running: 'Running',
    failed24h: 'Failed in 24 h by error class',
    noFailures: 'None',
    heartbeatLine: 'Last heartbeat',
    never: 'never',
    latencyTitle: 'Latency per route family',
    latencyScope: 'This API process only, last 15 minutes; lost on restart.',
    noTraffic: 'No traffic in the window.',
    slowQueries: 'Slow queries in the last hour',
    loadError: 'The installation status could not be read.',
  },
}

export const SERVICE_CONFIG: Record<Lang, ServiceConfigCopy> = { es, en }

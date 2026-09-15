"""Technical manual — the platform the product runs on.

Written against the code on 2026-09-14. The "what to check" entries are not
criticism for its own sake: they are the things an engineer will find in week
one, and finding them unannounced is what makes the rest of a document feel
like marketing.
"""

CHAPTER = {
    "id": "platform",
    "es": {
        "title": "5. La plataforma: multi-tenant, trabajos, configuración y API",
        "intro": (
            "Tres capas con una separación estricta, una cola de trabajos que ES una "
            "tabla, y aislamiento por tenant que es disciplina de consulta, no una "
            "garantía de la base. Este capítulo dice dónde están los candados y "
            "también dónde NO están."
        ),
        "topics": [
            {
                "name": "Capas y el único límite que se verifica solo",
                "where": "backend/tests/test_no_pandas_in_backend.py:33",
                "what": (
                    "Motor ML, API de orquestación y UI. De las tres reglas, solo una "
                    "la comprueba una máquina: pandas y numpy no pueden importarse en "
                    "`backend/` fuera de tres módulos."
                ),
                "table": [
                    ("Permitidos", "todo bajo backend/dataframes/, backend/utils/temporal_agg.py y backend/workers/runner.py"),
                    ("Qué cruza el límite", "Entra una ruta o bytes; sale Python plano — list[dict], dict, list[str], con NaN convertido a None"),
                    ("Las cuatro excepciones", "read_dataframe, dataframe_from_records, filter_dataframe_by_date y last_row_per_group: los únicos puntos donde un DataFrame llega al motor, cada uno etiquetado como tal"),
                ],
                "caveats": [
                    "NO existe un test equivalente que prohíba lógica de negocio en el frontend ni ML en el backend. Esa mitad es convención.",
                ],
            },
            {
                "name": "Multi-tenancy",
                "where": "backend/auth/guards.py:148 · get_current_user",
                "what": (
                    "El tenant viaja en el JWT y se hace cumplir consulta por "
                    "consulta. Cada función de servicio recibe `tenant_id` como primer "
                    "argumento y cada sentencia lleva `AND tenant_id = %s`."
                ),
                "caveats": [
                    "NO hay row-level security en la base. El aislamiento es disciplina, no garantía: un solo `AND tenant_id` olvidado lo rompe.",
                    "El middleware de contexto de tenant es SOLO observabilidad — su propio docstring lo dice. La autoridad es CurrentUser.tenant_id, que nunca se lee del cuerpo de la petición.",
                    "La red de seguridad son tests que leen la realidad: uno recorre la tabla de rutas de FastAPI y falla cualquier mutación sin guardia; otro lee el catálogo de Postgres y exige que toda tabla con tenant_id tenga FK con ON DELETE CASCADE (nació de 1,3 millones de filas huérfanas de 24.794 tenants muertos).",
                    "Un id de otro tenant devuelve 404, no 403: no confirma que exista.",
                ],
            },
            {
                "name": "Autenticación",
                "where": "backend/auth/jwt_handler.py",
                "what": "JWT propio, no Supabase. Dos credenciales distintas, una persona y una máquina.",
                "table": [
                    ("Token de acceso", "15 minutos, HS256, claims sub/tenant_id/role/email_verified/jti."),
                    ("Token de refresco", "7 días. NO es un JWT: 64 bytes aleatorios guardados solo como SHA-256. Máximo 5 vivos por usuario."),
                    ("Revocación", "Dos mecanismos: lista negra por jti para el logout, y corte por tiempo (sessions_invalid_before) para el cambio de contraseña, que no puede conocer un jti."),
                    ("Llaves de API", "sk_live_ + 32 bytes. SHA-256 sin sal (32 bytes de entropía no la necesitan). Nunca pueden ser admin: solo viewer o analyst."),
                    ("Límites de la llave", "120 por minuto y el techo diario del plan, decididos bajo un advisory lock — contar y luego insertar dejaba pasar 16 de 20 llamadas contra un techo de 5."),
                ],
                "caveats": [
                    "REVISAR: `ACCESS_TOKEN_EXPIRE_MINUTES` existe, está documentada y registrada... y nadie la lee. Los 15 minutos son una constante en el código. Cambiar esa variable no hace nada.",
                    "El límite de llaves FALLA ABIERTO ante un error de base: se prefiere servir a bloquear. Es deliberado y está documentado.",
                    "Una llave actúa con SU propio rol, no con el de quien la creó, y toda escritura suya deja fila de auditoría con actor `api_key:<id>`.",
                ],
            },
            {
                "name": "Trabajos y el worker",
                "where": "backend/training/queue.py:50",
                "what": (
                    "La cola es la tabla `jobs`. `enqueue()` es un no-op explícito: "
                    "insertar la fila ES encolar."
                ),
                "formulas": [
                    ("La reclamación",
                     "UPDATE jobs SET status='RUNNING', worker_id=%s\n WHERE id = (SELECT id FROM jobs WHERE status='QUEUED'\n             ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)",
                     "SKIP LOCKED es lo que hace que dos réplicas tomen trabajos DISTINTOS. Reemplazó a un leer-luego-escribir que entrenó la misma sesión dos veces."),
                ],
                "table": [
                    ("job-worker", "Sondea cada 2 s mientras haya cupo. Máximo de trabajos concurrentes: 2 por proceso."),
                    ("job-scheduler", "Cada 60 s: trabajos programados vencidos, con cron evaluado en la zona horaria del tenant."),
                    ("integration-sync", "06:00 UTC. Antes de las alertas, a propósito."),
                    ("inventory-alerts", "08:00 UTC: digest de quiebres, desvíos de plazo de proveedor y recordatorios de datos viejos."),
                    ("overstock-snapshot", "Día 1, 00:05 UTC: foto de sobrestock y después los correos de recuento mensual. El orden importa."),
                ],
                "caveats": [
                    "SCHEDULER_ENABLED debe estar activo en EXACTAMENTE UNA instancia o cada alerta diaria sale dos veces.",
                    "La recuperación de huérfanos corre al arrancar el WORKER, no la API: un redespliegue de la API no debe matar entrenamientos vivos de otro proceso.",
                    "REVISAR: el tope de trabajos por proceso es 2 y el tope por plan es 8. En un despliegue de un solo proceso manda el 2.",
                ],
            },
            {
                "name": "Estado de la sesión y los seis bloques de configuración",
                "where": "backend/sessions/state_machine.py:26",
                "what": (
                    "DRAFT → … → MODELS_CONFIGURED → QUEUED → RUNNING → COMPLETED/"
                    "FAILED. Seis blobs JSONB en `session_configs` que el runner "
                    "ensambla en un diccionario para el motor."
                ),
                "table": [
                    ("columns_cfg", "Mapeo de columnas del usuario al esquema canónico."),
                    ("features_cfg", "Rezagos, ventanas, calendario, país de feriados."),
                    ("models_cfg", "Los modelos que el usuario seleccionó — la selección que el enrutamiento solo puede estrechar."),
                    ("validation_cfg", "Partición, cortes, horizonte."),
                    ("forecast_cfg", "Horizonte y cuantiles."),
                    ("business_cfg", "Nivel de servicio 0.95, lead time, 20% de bodegaje, multiplicador de quiebre 3.0."),
                ],
                "caveats": [
                    "REVISAR: `RUNNING` está en la tabla de transiciones pero NINGÚN código de producción lo pone en una sesión — solo `jobs.status` pasa a RUNNING. El runner usa `force_status`, que se salta la máquina entera.",
                    "REVISAR: la llave primaria de `session_configs` es solo `session_id`; `tenant_id` es columna, no parte de la llave.",
                    "REVISAR: `business.lead_time_days` vale 7 en el runner y 15 en la semilla del asistente. Los dos defaults no coinciden.",
                    "La compuerta de datos (`data_gate.enforce`) bloquea el LANZAMIENTO, no el guardado, y tiene un solo punto de llamada — lo que cubre entrenar, el demo, la sincronización ERP y el script de semilla a la vez.",
                ],
            },
            {
                "name": "Configuración de servicios",
                "where": "backend/service_config/",
                "what": (
                    "Un registro único del que se generan el panel, `.env.example` y "
                    "la referencia. Cuatro capas de precedencia y el panel dice cuál "
                    "ganó en cada campo."
                ),
                "how": [
                    "Precedencia: override del tenant (solo servicios de canal) > override de instancia > entorno > default de Settings.",
                    "Un secreto se cifra con Fernet; sin llave la escritura se RECHAZA con motivo, nunca se degrada a texto plano.",
                    "Una lectura NUNCA lanza: si la base falla, se reporta el entorno y un despliegue solo-.env sigue funcionando.",
                    "Caché de 10 segundos: una llave pegada en el contenedor de API llega al de worker sin reiniciar.",
                ],
                "caveats": [
                    "Solo por entorno, a propósito: todo el núcleo (una UI que puede reescribir su propia URL de base deja a todos fuera) y la llave Fernet (un panel que pudiera reescribirla volvería ilegibles sus propios secretos).",
                    "`WHATSAPP_WEBHOOK_BASE_URL` es solo de instancia aunque su servicio sea por tenant: el webhook verifica la firma ANTES de saber de qué tenant es el mensaje.",
                    "Arranque en frío: mientras el despliegue tenga EXACTAMENTE UN tenant, sus admins lo operan. Se acaba en dos. Una base inalcanzable no otorga nada.",
                ],
            },
            {
                "name": "Superficie pública y forma de los errores",
                "where": "backend/api/public_surface.py:26",
                "what": (
                    "El contrato publicado son ocho pares (método, ruta). Todo lo demás "
                    "—271 rutas— es alcanzable con una llave pero nadie prometió "
                    "mantenerlo."
                ),
                "formulas": [
                    ("Envoltura de éxito", '{"success": true, "data": …, "meta": {"timestamp": …}}', "En 33 de 35 módulos."),
                    ("Envoltura de error", '{"detail": "<inglés>", "error_code": "<snake_case estable>", "error_params": {…}}',
                     "El frontend renderiza `errors.<error_code>` interpolando los params; `detail` es el respaldo."),
                ],
                "table": [
                    ("PUBLIC_API_ONLY", "Monta toda la app y después PODA las rutas a la lista pública más /health. Podar después de montar es deliberado: elegir routers dejaría colarse a un vecino interno."),
                    ("400 malformed_path", "Un NUL en la ruta se rechaza de entrada; antes llegaba a psycopg2 y salía como 500."),
                    ("503 server_busy", "Pool de conexiones agotado, con Retry-After. Ocupado no es roto."),
                    ("422 validation_error", "Con el input saneado: un NaN en el eco hacía que Starlette convirtiera el 422 en un 500 sin cuerpo."),
                ],
                "caveats": [
                    "PUBLIC_API_ONLY no cambia nada de autenticación ni permisos: es menos alcance, no un segundo modelo de seguridad. Y NO aísla la base — las dos instancias comparten un Postgres.",
                    "REVISAR: los conteos de rutas en los comentarios (246, 261) están desactualizados; hoy hay 271 decoradores.",
                ],
            },
            {
                "name": "El frontend",
                "where": "Frontend/src/",
                "what": (
                    "Next.js 14 App Router, 24 páginas con rutas en español, un cliente "
                    "de API de 226 funciones y tres catálogos de i18n con formas "
                    "distintas a propósito."
                ),
                "table": [
                    ("translations.ts", "3.034 claves por idioma, búsqueda en tiempo de ejecución. Un typo devuelve la clave."),
                    ("landing.ts / serviceConfig.ts", "Tipados: una traducción faltante es error de COMPILACIÓN, que es la garantía que translations.ts necesita un script para dar."),
                    ("Proxy", "/api/:path* → ${BACKEND_URL}/api/v1/:path*. Ojo: el navegador llama /api/... SIN el v1; el proxy lo agrega."),
                    ("BACKEND_URL", "Por defecto 127.0.0.1:8010 — IPv4 explícito, porque Node resuelve localhost a ::1 y uvicorn escucha en IPv4, produciendo un 500 sin cuerpo."),
                ],
                "caveats": [
                    "NO HAY TESTS DE FRONTEND. Cero. `tsc --noEmit` verifica tipos, no comportamiento. Lo que hay son tres guiones de Playwright que se corren a mano.",
                    "Las rutas en español son canónicas; las inglesas viejas redirigen con 308 permanente.",
                    "Los rewrites se evalúan en tiempo de CONSTRUCCIÓN: una imagen Docker debe construirse con BACKEND_URL puesta.",
                ],
            },
        ],
    },
    "en": {
        "title": "5. The platform: multi-tenancy, jobs, configuration and API",
        "intro": (
            "Three layers with a strict separation, a job queue that IS a table, and "
            "tenant isolation that is query discipline rather than a database "
            "guarantee. This chapter says where the locks are, and also where they "
            "are NOT."
        ),
        "topics": [
            {
                "name": "Layers and the one boundary a machine checks",
                "where": "backend/tests/test_no_pandas_in_backend.py:33",
                "what": (
                    "ML engine, orchestration API and UI. Of the three rules, only one "
                    "is mechanically enforced: pandas and numpy may not be imported in "
                    "`backend/` outside three modules."
                ),
                "table": [
                    ("Allowed", "everything under backend/dataframes/, backend/utils/temporal_agg.py and backend/workers/runner.py"),
                    ("What crosses", "A path or bytes go in; plain Python comes back — list[dict], dict, list[str], with NaN turned into None"),
                    ("The four exceptions", "read_dataframe, dataframe_from_records, filter_dataframe_by_date and last_row_per_group: the only points where a DataFrame reaches the engine, each labelled as such"),
                ],
                "caveats": [
                    "There is NO equivalent test forbidding business logic in the frontend or ML in the backend. That half is convention.",
                ],
            },
            {
                "name": "Multi-tenancy",
                "where": "backend/auth/guards.py:148 · get_current_user",
                "what": (
                    "The tenant travels in the JWT and is enforced query by query. "
                    "Every service function takes `tenant_id` as its first argument "
                    "and every statement carries `AND tenant_id = %s`."
                ),
                "caveats": [
                    "There is NO row-level security in the database. Isolation is discipline, not a guarantee: one forgotten `AND tenant_id` breaks it.",
                    "The tenant-context middleware is observability ONLY — its own docstring says so. The authority is CurrentUser.tenant_id, which is never read from a request body.",
                    "The safety net is tests that read reality: one walks FastAPI's own route table and fails any mutation without a guard; another reads the Postgres catalog and demands that every table with a tenant_id has an ON DELETE CASCADE FK (born of 1.3M orphan rows across 24,794 dead tenants).",
                    "An id belonging to another tenant returns 404, not 403: it does not confirm the row exists.",
                ],
            },
            {
                "name": "Authentication",
                "where": "backend/auth/jwt_handler.py",
                "what": "Its own JWT, not Supabase. Two distinct credentials, one human and one machine.",
                "table": [
                    ("Access token", "15 minutes, HS256, claims sub/tenant_id/role/email_verified/jti."),
                    ("Refresh token", "7 days. NOT a JWT: 64 random bytes stored only as SHA-256. At most 5 live per user."),
                    ("Revocation", "Two mechanisms: a jti blocklist for logout, and a time cut (sessions_invalid_before) for password changes, which cannot know a jti."),
                    ("API keys", "sk_live_ + 32 bytes. Unsalted SHA-256 (32 bytes of entropy needs no salt). They can never be admin: viewer or analyst only."),
                    ("Key limits", "120 per minute plus the plan's daily ceiling, decided under an advisory lock — count-then-insert let 16 of 20 concurrent calls past a ceiling of 5."),
                ],
                "caveats": [
                    "CHECK THIS: `ACCESS_TOKEN_EXPIRE_MINUTES` exists, is documented and is registered... and nothing reads it. The 15 minutes are a constant in the code. Changing that variable does nothing.",
                    "Key rate limiting FAILS OPEN on a database error: serving is preferred to blocking. Deliberate and documented.",
                    "A key acts with ITS own role, not its creator's, and each of its writes leaves an audit row with actor `api_key:<id>`.",
                ],
            },
            {
                "name": "Jobs and the worker",
                "where": "backend/training/queue.py:50",
                "what": (
                    "The queue is the `jobs` table. `enqueue()` is an explicit no-op: "
                    "inserting the row IS enqueuing."
                ),
                "formulas": [
                    ("The claim",
                     "UPDATE jobs SET status='RUNNING', worker_id=%s\n WHERE id = (SELECT id FROM jobs WHERE status='QUEUED'\n             ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)",
                     "SKIP LOCKED is what makes two replicas take DIFFERENT jobs. It replaced a read-then-write that trained the same session twice."),
                ],
                "table": [
                    ("job-worker", "Polls every 2 s while there is room. Concurrent jobs per process: 2."),
                    ("job-scheduler", "Every 60 s: due scheduled jobs, with cron evaluated in the tenant's timezone."),
                    ("integration-sync", "06:00 UTC. Before the alerts, deliberately."),
                    ("inventory-alerts", "08:00 UTC: stockout digest, supplier lead-time deviations and stale-data reminders."),
                    ("overstock-snapshot", "Day 1, 00:05 UTC: overstock snapshot, then the monthly recap emails. The order is load-bearing."),
                ],
                "caveats": [
                    "SCHEDULER_ENABLED must be on in EXACTLY ONE instance or every daily alert goes out twice.",
                    "Orphan recovery runs at WORKER startup, not the API's: an API redeploy must not kill another process's live trainings.",
                    "CHECK THIS: the per-process job cap is 2 and the per-plan cap is 8. In a single-process deployment the 2 binds first.",
                ],
            },
            {
                "name": "Session state and the six config blobs",
                "where": "backend/sessions/state_machine.py:26",
                "what": (
                    "DRAFT → … → MODELS_CONFIGURED → QUEUED → RUNNING → COMPLETED/"
                    "FAILED. Six JSONB blobs in `session_configs` that the runner "
                    "assembles into one dict for the engine."
                ),
                "table": [
                    ("columns_cfg", "Mapping of the user's columns onto the canonical schema."),
                    ("features_cfg", "Lags, windows, calendar, holiday country."),
                    ("models_cfg", "The models the user selected — the selection routing may only narrow."),
                    ("validation_cfg", "Split, folds, horizon."),
                    ("forecast_cfg", "Horizon and quantiles."),
                    ("business_cfg", "Service level 0.95, lead time, 20% holding, stockout multiplier 3.0."),
                ],
                "caveats": [
                    "CHECK THIS: `RUNNING` is in the transition table but NO production code sets it on a session — only `jobs.status` goes RUNNING. The runner uses `force_status`, which bypasses the machine entirely.",
                    "CHECK THIS: the primary key of `session_configs` is `session_id` alone; `tenant_id` is a column, not part of the key.",
                    "CHECK THIS: `business.lead_time_days` is 7 in the runner and 15 in the wizard seed. The two defaults disagree.",
                    "The data gate (`data_gate.enforce`) blocks the LAUNCH, not the save, and has a single call site — which covers training, the demo, ERP sync and the seed script at once.",
                ],
            },
            {
                "name": "Service configuration",
                "where": "backend/service_config/",
                "what": (
                    "One registry from which the panel, `.env.example` and the "
                    "reference are generated. Four precedence layers, and the panel "
                    "says which one won for every field."
                ),
                "how": [
                    "Precedence: tenant override (channel services only) > instance override > environment > Settings default.",
                    "A secret is Fernet-encrypted; with no key the write is REFUSED with a stated reason, never downgraded to plaintext.",
                    "A read NEVER raises: if the database fails, the environment is reported and a .env-only deployment keeps working.",
                    "Ten-second cache: a key pasted on the API container reaches the worker container without a restart.",
                ],
                "caveats": [
                    "Environment-only on purpose: the whole core (a UI that can rewrite its own database URL can lock everyone out) and the Fernet key (a panel that could rewrite it would make its own stored secrets unreadable).",
                    "`WHATSAPP_WEBHOOK_BASE_URL` is instance-only even though its service is tenant-scoped: the webhook verifies the signature BEFORE it knows which tenant the message belongs to.",
                    "Cold start: while the deployment has EXACTLY ONE tenant, its admins operate it. It stops at two. An unreachable database grants nothing.",
                ],
            },
            {
                "name": "Public surface and the shape of errors",
                "where": "backend/api/public_surface.py:26",
                "what": (
                    "The published contract is eight (method, path) pairs. Everything "
                    "else — 271 routes — is reachable with a key but nobody promised "
                    "to keep it."
                ),
                "formulas": [
                    ("Success envelope", '{"success": true, "data": …, "meta": {"timestamp": …}}', "In 33 of 35 modules."),
                    ("Error envelope", '{"detail": "<English>", "error_code": "<stable snake_case>", "error_params": {…}}',
                     "The frontend renders `errors.<error_code>` interpolating the params; `detail` is the fallback."),
                ],
                "table": [
                    ("PUBLIC_API_ONLY", "Mounts the whole app and then PRUNES the routes to the public list plus /health. Pruning after mounting is deliberate: picking routers would let an internal neighbour ride along."),
                    ("400 malformed_path", "A NUL in the path is refused up front; it used to reach psycopg2 and surface as a 500."),
                    ("503 server_busy", "Connection pool exhausted, with Retry-After. Busy is not broken."),
                    ("422 validation_error", "With the echoed input sanitised: a NaN in it made Starlette turn the 422 into an empty-bodied 500."),
                ],
                "caveats": [
                    "PUBLIC_API_ONLY changes nothing about authentication or permissions: narrower reach, not a second security model. And it does NOT isolate the database — both instances share one Postgres.",
                    "CHECK THIS: the route counts in the comments (246, 261) are stale; there are 271 decorators today.",
                ],
            },
            {
                "name": "The frontend",
                "where": "Frontend/src/",
                "what": (
                    "Next.js 14 App Router, 24 pages on Spanish routes, a 226-function "
                    "API client, and three i18n catalogues with deliberately different "
                    "shapes."
                ),
                "table": [
                    ("translations.ts", "3,034 keys per language, runtime lookup. A typo returns the key."),
                    ("landing.ts / serviceConfig.ts", "Typed: a missing translation is a COMPILE error, which is the guarantee translations.ts needs a script to give."),
                    ("Proxy", "/api/:path* → ${BACKEND_URL}/api/v1/:path*. Note: the browser calls /api/... WITHOUT the v1; the proxy adds it."),
                    ("BACKEND_URL", "Defaults to 127.0.0.1:8010 — explicit IPv4, because Node resolves localhost to ::1 while uvicorn binds IPv4, producing a 500 with an empty body."),
                ],
                "caveats": [
                    "THERE ARE NO FRONTEND TESTS. None. `tsc --noEmit` checks types, not behaviour. What exists is three Playwright scripts run by hand.",
                    "The Spanish routes are canonical; the old English ones 308-redirect permanently.",
                    "Rewrites are evaluated at BUILD time: a Docker image must be built with BACKEND_URL set.",
                ],
            },
        ],
    },
}

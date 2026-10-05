# StockAI: visión general de seguridad

*Documento para cuestionarios de seguridad. Escrito para el propietario; los identificadores
(rutas, nombres de tablas, variables) quedan en inglés tal como están en el código.*

**Regla de este documento:** cada control que se afirma está implementado y lleva una cita
`archivo:línea · símbolo` que un test (`backend/tests/test_compliance_citations_are_real.py`)
comprueba que sigue existiendo. Lo que **no** está implementado se dice como **BRECHA**, no se
omite. Lo que no se pudo comprobar desde el repositorio se marca **NO VERIFICADO** (por ejemplo,
cualquier cosa que viva solo en el servidor de Hostinger).

Estados usados en todo el pack: **Implementado**, **Parcial**, **BRECHA**, **NO VERIFICADO**.

Alcance: el código de este repositorio y `deploy/`. El servicio alojado (`stockai.es`,
`app.stockai.es`) corre ese mismo código en un VPS de Hostinger (`deploy/README.md`); lo que solo
existe en ese VPS (cron del backup, firewall del proveedor, cifrado de disco) no se puede
verificar desde aquí y se marca como tal.

---

## 1. Arquitectura y flujo de datos

```
navegador ──HTTPS──> Caddy (TLS automático, :80/:443)
                       └─> Next.js (:5000) ──proxy /api/*──> FastAPI (API)
                                                              ├─> Postgres (volumen pgdata)
                                                              └─> storage/ (volumen: datasets, artefactos, documentos)
worker (contenedor propio): cola de jobs en la tabla `jobs`, entrenamiento y loops cron
```

- Solo Caddy publica puertos al exterior (`deploy/docker-compose.prod.yml:25 · "443:443"`). La API
  no se expone: Caddy envía todo a Next.js, que reenvía `/api/*` por la red interna
  (`deploy/Caddyfile:6 · reverse_proxy frontend:5000`).
- La capa ML (`ForecastingCore/`) corre dentro del proceso del worker, en la CPU del propio
  servidor; los modelos no se entrenan en terceros.
- Salidas a internet (todas apagadas hasta que se configura su credencial): DeepSeek (IA), Resend
  o SMTP (correo), Twilio (WhatsApp/SMS). No hay telemetría ni analítica
  (`docs/data-that-leaves.md`). Detalle en `data-processing-and-subprocessors.md`.
- Un deploy a producción rechaza arrancar si `TESTING_MODE` está activo
  (`backend/config.py:242 · def _refuse_testing_mode_in_production`) y si las migraciones fallan
  (`backend/main.py:112 · refusing to boot`).
- Los contenedores de API y frontend corren como usuario no root
  (`backend/Dockerfile:42 · USER faro`; `Frontend/Dockerfile` hace lo mismo).

## 2. Cifrado en tránsito

| Tramo | Estado | Evidencia / nota |
|---|---|---|
| Navegador a Caddy | **Implementado** | TLS con Let's Encrypt automático (`deploy/Caddyfile:1 · TLS is automatic`). |
| HSTS | **BRECHA** | Ni el Caddyfile ni `Frontend/next.config.mjs` envían `Strict-Transport-Security`. Esfuerzo: 1 línea en Caddy. |
| Next.js a API, API a Postgres (red interna de compose) | **BRECHA (aceptada por diseño)** | Tráfico en claro dentro de la red privada de Docker; no hay `sslmode` en el repo. Con Postgres administrado externo (`deploy/README.md`, ruta 2) habría que exigir TLS en `DATABASE_URL`. |
| Hacia DeepSeek, Resend, Twilio | **Implementado** | Todas las URL base son `https://` (`docs/data-that-leaves.md`, comando de verificación). |

## 3. Cifrado en reposo (lo que realmente se hace)

| Dato | Cómo se guarda | Estado |
|---|---|---|
| Contraseñas de usuario | bcrypt con sal por contraseña (`backend/auth/password.py:6 · bcrypt.hashpw`) | Implementado |
| Refresh tokens | solo su SHA-256 (`backend/auth/jwt_handler.py:64 · def create_refresh_token`) | Implementado |
| API keys `sk_live_*` | solo su SHA-256 (`backend/auth/api_key_auth.py:32 · def hash_key`) | Implementado |
| Credenciales de servicios (DeepSeek, Twilio, SMTP...) guardadas desde `/instalacion` | Fernet con `INTEGRATIONS_SECRET_KEY` o `storage/instance_secret.key` (`backend/service_config/crypto.py:40 · from cryptography.fernet import Fernet`); sin llave el panel **rechaza** guardar, no guarda en claro | Implementado |
| Contraseñas de conexiones SQL del cliente (fuentes de datos) | Fernet con llave derivada de `SECRET_KEY` (`backend/datasources/service.py:100 · def _fernet`) | Implementado, con riesgo: la misma `SECRET_KEY` firma los JWT; rotarla invalida esas contraseñas |
| Datasets, artefactos de modelos, documentos | Archivos en `storage/` **sin cifrado a nivel de aplicación** | **BRECHA** |
| Filas de negocio en Postgres | **Sin cifrado a nivel de aplicación** | **BRECHA** |
| Cifrado de disco del VPS | Depende de Hostinger | **NO VERIFICADO** |
| Copias de seguridad (dump + tar de `storage/`) | `deploy/ops/backup.sh` no cifra; el tar incluye `instance_secret.key` junto a los datos | **BRECHA** |

Los modelos entrenados se serializan con `joblib` y se cargan desde disco
(`ForecastingCore/forecasting_core/engine.py:171 · payload = joblib.load(path)`); solo los escribe el
propio servidor, pero quien pudiera escribir en `storage/` podría ejecutar código al cargarlos.
Es un riesgo de integridad del volumen, no de red (**BRECHA menor**, mitigada por no exponer el
volumen).

## 4. Manejo de secretos

- Un único registro declara cada variable, si es secreta y qué deja de funcionar sin ella
  (`backend/service_config/registry.py:821 · OPERATIONS = Service(` es la última entrada; el registro
  completo genera `backend/.env.example` y `docs/configuration.md`, y un test falla si algo queda
  sin declarar).
- Un secreto guardado desde el panel nunca se devuelve: la API da a lo sumo los 4 últimos
  caracteres (`backend/api/v1/service_config.py`, docstring del módulo).
- Quién puede editar configuración de la instalación: solo los operadores nombrados en
  `INSTANCE_ADMIN_EMAILS`, **no** el rol `admin` de un tenant
  (`backend/service_config/access.py:137 · def require_instance_operator`).
- Los secretos de entorno (`SECRET_KEY`, `DATABASE_URL`, `POSTGRES_PASSWORD`) viven en `deploy/.env`
  en el servidor; el repo solo trae `.env.example`.
- **BRECHA:** no hay procedimiento automático de rotación. Rotar `SECRET_KEY` cierra todas las
  sesiones y deja ilegibles las contraseñas SQL de las fuentes de datos (hay que reingresarlas);
  rotar `INTEGRATIONS_SECRET_KEY` obliga a reingresar todos los secretos del panel
  (`deploy/RESTORE.md`, sección "Taking the key out of the equation").

## 5. Autenticación y gestión de sesiones

- Email + contraseña. Política: mínimo 8 caracteres, al menos una letra y un dígito, máximo 72
  bytes (`backend/auth/password.py:16 · def validate_strength`).
- Access token JWT HS256 de 15 minutos (`backend/auth/jwt_handler.py:17 · _ACCESS_EXPIRE_MIN = 15`);
  refresh token opaco de 7 días (`backend/auth/jwt_handler.py:18 · _REFRESH_EXPIRE_DAYS = 7`) guardado
  como hash.
- `POST /auth/refresh` emite un access token nuevo pero **no rota** el refresh token
  (`backend/api/v1/auth.py:367 · async def refresh`). **BRECHA:** un refresh token robado sirve hasta su
  vencimiento o hasta un logout/cambio de contraseña.
- Logout: revoca el `jti` del access token en la lista `revoked_tokens`
  (`backend/auth/blocklist.py:13 · CREATE TABLE IF NOT EXISTS revoked_tokens`) y borra los refresh
  tokens del usuario (`backend/api/v1/auth.py:495 · async def logout`).
- Cambiar o restablecer la contraseña invalida los tokens emitidos antes
  (`backend/auth/guards.py:159 · "SELECT sessions_invalid_before FROM users`).
- Suspender o desactivar un usuario borra sus refresh tokens y le impide iniciar sesión
  (`backend/users/service.py:275 · def update_status`); un access token ya emitido sigue valiendo hasta
  15 minutos.
- Verificación de correo: existe; un correo sin verificar no bloquea el login pero limita acciones
  de salida (`require_verified_*` en `backend/auth/guards.py`).
- Recuperación de contraseña: código de 6 dígitos que vence a los 15 minutos, con contador de
  intentos (`backend/api/v1/auth.py`, `forgot-password/verify`).
- **BRECHA:** no hay segundo factor (MFA/TOTP) ni SSO/SAML/OIDC. Login social (Google/Apple/Facebook)
  existe pero viene **apagado por defecto** (`backend/auth/social/`, `SOCIAL_LOGIN_ENABLED`).
- **BRECHA:** el access token se guarda en `localStorage` y el refresh en `sessionStorage`
  (`Frontend/src/lib/auth.ts:13 · export function getToken()`); un XSS los leería. La CSP lo agrava:
  permite `'unsafe-inline'` y `'unsafe-eval'` en scripts
  (`Frontend/next.config.mjs:28 · script-src 'self' 'unsafe-inline' 'unsafe-eval'`).
- Cabeceras que sí se envían: CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy` y `Permissions-Policy` (`Frontend/next.config.mjs:40 · X-Frame-Options`).
- CORS limitado a `FRONTEND_URL` y dos orígenes de desarrollo
  (`backend/main.py:158 · allow_origins=[settings.frontend_url`). Swagger/ReDoc/OpenAPI se desactivan en
  producción (`backend/main.py:151 · openapi_url=None if settings.environment == "production"`).

## 6. Control de acceso por rol (RBAC)

- Tres roles por tenant: `admin`, `analyst`, `viewer`. Toda ruta que modifica datos exige analista
  o superior (`backend/auth/guards.py:255 · def require_analyst_or_above`); exportar o borrar el tenant
  completo, leer la auditoría y gestionar usuarios exigen `admin`
  (`backend/auth/guards.py:252 · require_admin = require_role("admin")`).
- Operador de la instalación (rol distinto del `admin`): ve cifras de todos los tenants
  (`GET /service-config/ops`, `backend/api/v1/service_config.py:116 · @router.get("/ops")`) y edita la
  configuración global. Una API key **nunca** es operador
  (`backend/service_config/access.py:113 · def is_instance_operator`).
- Cada ruta que escribe tiene un test de par de permisos (viewer rechazado y estado sin cambios,
  analyst exitoso): estándar obligatorio del repo (`CLAUDE.md`, "Testing Standards"); ejemplo:
  `backend/tests/test_permission_audit.py:2 · Permission-pair + cross-tenant audit`.
- No hay permisos finos por recurso salvo `user_permissions` (`/users/{id}/permissions`).

## 7. Trazabilidad / auditoría

- Un middleware escribe una fila `audit.<sustantivo>.<verbo>` tras cada llamada exitosa a una ruta
  catalogada (`backend/middleware/audit_trail.py:41 · class AuditMiddleware`,
  `backend/audit/catalog.py:42 · ROUTES: dict`): quién, qué, sobre qué objeto y cuándo; para llamadas
  con API key el actor es la llave, no la persona que la creó.
- Solo se registran éxitos; las lecturas no se registran. Los eventos previos (`record_event`) se
  muestran en el mismo formato.
- Solo un `admin` la lee (`backend/api/v1/audit.py:25 · router = APIRouter(prefix="/audit"`), con filtros
  y exportación CSV que neutraliza fórmulas. Test:
  `backend/tests/test_audit_trail.py:174 · def test_only_an_admin_may_read_it` y
  `backend/tests/test_audit_trail.py:222 · def test_it_never_shows_another_tenants_trail`.
- **BRECHA:** la tabla `activity_logs` no es inmutable (a diferencia de `session_manifests`, que sí
  tiene un trigger), no tiene política de retención (`docs/stability.md`, "What is deliberately NOT
  here") y se borra con el tenant. No se envía a un SIEM.
- Registro de acceso HTTP: una línea por petición con método, ruta, estado, milisegundos, tenant y
  llave (`backend/middleware/request_logger.py:10 · log = logging.getLogger("access")`). El plazo de
  retención de estos logs en el servidor **NO está definido** (placeholder en la política de
  privacidad, `Frontend/src/i18n/legal.ts`).

## 8. Límites de uso y protección contra abuso

- Login: 5 intentos por correo cada 5 minutos; reenvío de verificación 3 cada 15; código OTP 10 cada
  10 (`backend/api/v1/auth.py:296 · _check_rate(f"login:`). Se guarda en Postgres (`auth_rate_events`),
  así que sobrevive a reinicios.
- **BRECHA:** el límite de login es **por correo, no por IP**, y no hay límite global por IP en
  Caddy ni en la app para rutas autenticadas. Sin WAF/anti-DDoS propio (puede ofrecerlo el
  proveedor: NO VERIFICADO).
- API keys: límite por minuto para todos y por día en el plan `free`
  (`backend/auth/api_key_auth.py:102 · def check_rate`). **Falla abierto** si la base de datos no responde
  (decisión documentada: no tumbar integraciones por un fallo del limitador).
- Cuentas de prueba: creación limitada por IP (`backend/api/v1/trial.py:32 · _check_rate(`) y borrado
  automático a las 24 h (`backend/workers/worker.py:522 · def _trial_reaper_loop`).
- Subidas: lista de extensiones permitidas (`backend/datasets/service.py:40 · if suffix not in ALLOWED_EXTENSIONS`)
  y techo de tamaño por plan (25 MB `free`).
- Descarga de artefactos con guarda contra path traversal
  (`backend/api/v1/artifacts.py:54 · full = (base / artifact_path).resolve()`).

## 9. API keys y superficie pública

- Prefijo `sk_live_`, 32 bytes aleatorios, solo se guarda el hash; alcance `read` (actúa como viewer)
  o `write` (como analyst); vencimiento opcional; revocación con `DELETE /api-keys/{id}`; cada
  llamada se mide por día (`backend/auth/api_key_auth.py:194 · def meter`).
- Una key solo alcanza rutas cuyo tag esté en `EXPOSED_TAGS`; nunca rutas de auth, usuarios, keys,
  configuración o admin (`backend/api/public_surface.py`; test
  `backend/tests/test_public_api_surface.py:13 · EXPOSED_TAGS`).
- El servidor MCP (`/api/v1/mcp`) solo tiene herramientas de lectura y un test lo exige
  (`backend/tests/test_mcp_server.py:150 · def test_every_tool_actually_only_calls_GET_endpoints`).
- Firmas: los webhooks entrantes de Twilio se validan por firma
  (`backend/api/v1/whatsapp.py:110 · signature = request.headers.get("X-Twilio-Signature"`); los webhooks
  salientes se firman con HMAC-SHA256 (`backend/api/v1/webhooks.py:94 · hmac.new(hook["secret"]`).

## 10. Aislamiento entre tenants

- Diseño: toda consulta de negocio lleva `tenant_id` tomado del token (no del cuerpo de la petición).
  Es una disciplina del código, **no** una barrera de la base de datos: no se usa Row Level Security
  de Postgres (**BRECHA de defensa en profundidad**).
- Evidencia automatizada: pares tenant A/tenant B en
  `backend/tests/test_permission_audit.py:2 · Permission-pair + cross-tenant audit`, la exportación no
  filtra filas ajenas (`backend/tests/test_tenant_data.py`), la auditoría nunca muestra otro tenant
  (test citado arriba), y todas las tablas con `tenant_id` tienen cascada desde `tenants`
  (`backend/tests/test_tenant_cascade_fk.py:55 · def test_every_tenant_scoped_table_cascades_from_tenants`).
- No existe una prueba de penetración de terceros (**BRECHA**, ver `soc2-readiness-gap-analysis.md`).
- La fuente de datos SQL del cliente (la app se conecta al servidor que el cliente indica) no tiene
  controles anti-SSRF documentados en el código (**BRECHA**, a evaluar).

## 11. Operación y monitoreo

- `/health` informa base de datos, estado por servicio y último pase de cada loop cron
  (`backend/main.py:404 · @app.get("/health"`).
- Superficie para operadores `GET /service-config/ops` y panel "Estado de la instalación" en
  `/instalacion`: cola, jobs corriendo, latido del worker, fallos 24 h por clase de error, pool de
  conexiones, consultas lentas, disco, último backup y latencia por familia de rutas, con umbrales
  declarados en el registro (`backend/service_config/ops.py:226 · def snapshot`).
- Un correo diario al operador resume lo que falló en las últimas 24 h en todos los tenants
  (`backend/notifications/operator_digest.py`).
- **BRECHA:** no hay alertas push en tiempo real, ni monitoreo externo de disponibilidad, ni rotación
  de logs gestionada por la app.

## 12. Resumen de brechas de este documento

| # | Brecha | Esfuerzo estimado |
|---|---|---|
| 1 | HSTS no configurado | Muy bajo (1 línea Caddy) |
| 2 | Sin cifrado de datos de negocio/archivos a nivel app; cifrado de disco NO VERIFICADO | Medio (verificar con Hostinger; cifrar backups: bajo) |
| 3 | Backups sin cifrar y en el mismo host (ver `business-continuity.md`) | Bajo-medio |
| 4 | Sin MFA ni SSO | Alto |
| 5 | Refresh token sin rotación | Bajo |
| 6 | Tokens en storage del navegador + CSP con `unsafe-inline`/`unsafe-eval` | Alto (migrar a cookies httpOnly y quitar inline) |
| 7 | Auditoría mutable, sin retención ni SIEM | Medio |
| 8 | Sin límite por IP / WAF | Bajo-medio |
| 9 | Sin RLS en Postgres | Alto |
| 10 | Sin pentest de terceros ni escaneo de dependencias (CI/CD fuera de alcance por instrucción del propietario) | Medio (externo) |
| 11 | Sin rotación automatizada de secretos | Medio |
| 12 | Anti-SSRF en fuentes SQL no evaluado | Bajo-medio |

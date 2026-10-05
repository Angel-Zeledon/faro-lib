# StockAI: hoja de respuestas tipo SIG-Lite

*Respuestas para copiar a cuestionarios de seguridad de clientes. Formato: **Sí / No / Parcial /
NO VERIFICADO** + evidencia en el repositorio. "NO VERIFICADO" significa que depende de algo que
solo existe fuera del código (el servidor de Hostinger, políticas del propietario) y debe
confirmarse antes de contestar "Sí". No responder "Sí" a lo que aquí dice otra cosa.*

Detalle y brechas en `security-overview.md`, `data-processing-and-subprocessors.md`,
`soc2-readiness-gap-analysis.md`, `incident-response.md` y `business-continuity.md`.

## A. Gobierno y gestión de riesgos

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 1 | ¿Tiene una política de seguridad de la información documentada y aprobada? | Parcial | Reglas técnicas internas en `CLAUDE.md` y este pack; sin política formal aprobada |
| 2 | ¿Hay un responsable de seguridad designado? | NO VERIFICADO | Los operadores se nombran en `INSTANCE_ADMIN_EMAILS` (`backend/service_config/access.py:113 · def is_instance_operator`); no hay rol formal |
| 3 | ¿Capacitación de seguridad para el personal? | NO VERIFICADO | Sin evidencia en el repositorio |
| 4 | ¿Verificación de antecedentes del personal? | NO VERIFICADO | Sin evidencia en el repositorio |
| 5 | ¿Tiene certificaciones (SOC 2, ISO 27001)? | No | El DPA publicado lo dice; ruta en `soc2-readiness-gap-analysis.md` |
| 6 | ¿Evalúa riesgos periódicamente? | Parcial | Listas de brechas de este pack y `docs/stability.md`; sin registro de riesgos formal |
| 7 | ¿Evalúa a sus proveedores? | Parcial | Salidas enumeradas en `docs/data-that-leaves.md`; sin evaluación formal ni contratos verificables |

## B. Control de acceso

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 8 | ¿Identificadores únicos por usuario? | Sí | Tabla `users`, un id y un correo por usuario; llaves de API con id propio |
| 9 | ¿Control de acceso basado en roles? | Sí | `admin/analyst/viewer` (`backend/auth/guards.py:255 · def require_analyst_or_above`) |
| 10 | ¿Mínimo privilegio y separación de funciones? | Parcial | RBAC y llaves `read/write`; el operador ve todos los tenants sin registro de accesos |
| 11 | ¿Autenticación multifactor? | No | No hay MFA en el código |
| 12 | ¿SSO (SAML/OIDC)? | No | Solo login social opcional, apagado por defecto (`backend/auth/social/`) |
| 13 | ¿Política de contraseñas? | Parcial | 8+ caracteres, letra y dígito, máx. 72 bytes (`backend/auth/password.py:16 · def validate_strength`); sin lista de contraseñas filtradas ni expiración |
| 14 | ¿Cómo se almacenan las contraseñas? | Sí | bcrypt con sal (`backend/auth/password.py:6 · bcrypt.hashpw`) |
| 15 | ¿Bloqueo o limitación por intentos fallidos? | Parcial | 5 intentos por correo cada 5 min (`backend/api/v1/auth.py:296 · _check_rate(f"login:`); es limitación, no bloqueo, y es por correo, no por IP |
| 16 | ¿Caducidad de sesión? | Sí | Access token 15 min (`backend/auth/jwt_handler.py:17 · _ACCESS_EXPIRE_MIN = 15`); refresh 7 días |
| 17 | ¿Se pueden revocar sesiones? | Sí | Logout con lista de revocados (`backend/auth/blocklist.py:13 · CREATE TABLE IF NOT EXISTS revoked_tokens`); cambio de contraseña invalida tokens previos (`backend/auth/guards.py:159 · "SELECT sessions_invalid_before FROM users`); suspender usuario borra refresh tokens |
| 18 | ¿Rotación del refresh token? | No | `POST /auth/refresh` no lo rota (`backend/api/v1/auth.py:367 · async def refresh`) |
| 19 | ¿Acceso privilegiado separado de usuarios normales? | Sí | `INSTANCE_ADMIN_EMAILS`, no el rol `admin` (`backend/service_config/access.py:137 · def require_instance_operator`) |
| 20 | ¿Revisión periódica de accesos? | NO VERIFICADO | Proceso organizativo; no hay evidencia en el repositorio |
| 21 | ¿Autenticación de API y alcance de credenciales? | Sí | Llaves `sk_live_*` con hash, alcance `read/write`, vencimiento opcional (`backend/auth/api_key_auth.py:32 · def hash_key`); solo tags expuestos (`backend/tests/test_public_api_surface.py:13 · EXPOSED_TAGS`) |
| 22 | ¿Pueden las integraciones modificar datos vía IA/MCP? | No | El MCP es de solo lectura y un test lo exige (`backend/tests/test_mcp_server.py:150 · def test_every_tool_actually_only_calls_GET_endpoints`) |

## C. Protección de datos

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 23 | ¿Cifrado en tránsito desde el usuario? | Sí | TLS automático en Caddy (`deploy/Caddyfile:1 · TLS is automatic`); HSTS **no** configurado |
| 24 | ¿Cifrado del tráfico interno (app a base de datos)? | No | Red privada de Docker en claro; sin `sslmode` en el repo |
| 25 | ¿Cifrado en reposo de los datos de clientes? | Parcial | Nada a nivel de aplicación para datos de negocio y archivos; cifrado de disco del VPS NO VERIFICADO |
| 26 | ¿Cifra credenciales y secretos guardados? | Sí | Fernet (`backend/service_config/crypto.py:40 · from cryptography.fernet import Fernet`); sin llave, rechaza guardar |
| 27 | ¿Gestión de llaves y rotación? | Parcial | Llave en variable de entorno o archivo; rotar obliga a reingresar secretos; sin rotación automática |
| 28 | ¿Segregación de datos entre clientes? | Sí | Lógica por `tenant_id` del token; tests cruzados (`backend/tests/test_permission_audit.py:2 · Permission-pair + cross-tenant audit`); sin RLS |
| 29 | ¿Clasificación de datos y DLP? | No | No hay clasificación ni prevención de fuga de datos |
| 30 | ¿Política de retención? | Parcial | Reglas en `data-processing-and-subprocessors.md`; auditoría y logs sin plazo; las sesiones son permanentes (`backend/db/migrations.py:1426 · Sessions are permanent (2026-10-04)`) |
| 31 | ¿Puede el cliente exportar sus datos? | Sí | `GET /tenant/export` (`backend/api/v1/tenant_data.py:29 · @router.get("/export")`) |
| 32 | ¿Puede el cliente borrar todos sus datos? | Sí | `DELETE /tenant` (`backend/api/v1/tenant_data.py:45 · @router.delete("")`); no alcanza backups (14 días) ni subencargados |
| 33 | ¿Hay copias de seguridad? | Sí | Nocturnas, retención 14 días (`deploy/README.md`); script real en el servidor: NO VERIFICADO |
| 34 | ¿Los backups están cifrados? | No | `deploy/ops/backup.sh` no cifra |
| 35 | ¿Hay copia fuera del sitio principal? | No | Sin copia externa documentada |
| 36 | ¿Se prueban las restauraciones? | Parcial | `scripts/restore_drill.py` + `deploy/RESTORE.md`; restauración manual del 2026-09-16; el simulacro aún no se ha corrido contra producción |
| 37 | ¿Dónde se alojan los datos (país)? | NO VERIFICADO | La ubicación del servidor de Hostinger es un marcador sin completar en la política |
| 38 | ¿Lista de subencargados? | Parcial | Hostinger, DeepSeek, Resend, Twilio publicados; Voyage AI y Pinecone (RAG opcional) usados por el código pero **no** publicados |
| 39 | ¿Se entrenan modelos con datos de un cliente para otros? | No | Los modelos se entrenan solo con los datos del propio tenant (política de privacidad, sección "purposes"; el entrenamiento corre localmente) |
| 40 | ¿Los datos van a un proveedor de IA externo? | Parcial | Solo si `DEEPSEEK_API_KEY` está configurada; qué retiene DeepSeek: NO VERIFICADO; apagado por instalación, no por cuenta (`docs/data-that-leaves.md`) |

## D. Seguridad de la aplicación y desarrollo

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 41 | ¿Ciclo de desarrollo seguro documentado? | Parcial | Reglas en `CLAUDE.md`, pruebas obligatorias de permisos y de estado; sin SDLC formal |
| 42 | ¿Revisión de código obligatoria? | NO VERIFICADO | Sin evidencia de proceso de revisión; sin CI por instrucción del propietario |
| 43 | ¿Análisis estático (SAST) o escaneo de dependencias? | No | No hay |
| 44 | ¿Pruebas de penetración independientes? | No | No hay |
| 45 | ¿Política de divulgación de vulnerabilidades? | Sí | `/divulgacion-responsable` y `security.txt` (`vulnerability-disclosure.md`); plazos sin completar |
| 46 | ¿Protección contra inyección SQL? | Parcial | Consultas con parámetros separados; no se hizo auditoría exhaustiva de cada consulta |
| 47 | ¿Cabeceras de seguridad y CSP? | Parcial | CSP, XFO, nosniff, Referrer-Policy (`Frontend/next.config.mjs:40 · X-Frame-Options`); CSP con `unsafe-inline`/`unsafe-eval` (`Frontend/next.config.mjs:28 · script-src 'self' 'unsafe-inline' 'unsafe-eval'`); sin HSTS |
| 48 | ¿Dónde se guardan los tokens en el navegador? | Parcial | `localStorage` y `sessionStorage` (`Frontend/src/lib/auth.ts:13 · export function getToken()`), expuestos si hay XSS; no son cookies, por lo que CSRF clásico no aplica |
| 49 | ¿Validación de archivos subidos? | Parcial | Lista de extensiones y tamaño por plan (`backend/datasets/service.py:40 · if suffix not in ALLOWED_EXTENSIONS`); sin antivirus |
| 50 | ¿Protección contra path traversal? | Sí | Guarda en la descarga de artefactos (`backend/api/v1/artifacts.py:54 · full = (base / artifact_path).resolve()`) |
| 51 | ¿Límites de tasa? | Parcial | Login, verificación, OTP, trial y API keys; sin límite global por IP (`backend/auth/api_key_auth.py:102 · def check_rate` falla abierto ante fallo de BD) |
| 52 | ¿Documentación de API expuesta en producción? | No | OpenAPI/Docs apagados en producción (`backend/main.py:151 · openapi_url=None if settings.environment == "production"`) |
| 53 | ¿Separación de entornos y configuración de producción forzada? | Parcial | `ENVIRONMENT`; producción se niega a arrancar con `TESTING_MODE` (`backend/config.py:242 · def _refuse_testing_mode_in_production`) |
| 54 | ¿Hay secretos en el repositorio? | No | `.env*` ignorados, solo `.env.example` versionado (`.gitignore`) |

## E. Infraestructura y operación

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 55 | ¿Segmentación de red? | Parcial | Solo Caddy publica puertos (`deploy/docker-compose.prod.yml:25 · "443:443"`); firewall del VPS NO VERIFICADO |
| 56 | ¿Contenedores sin privilegios? | Sí | Usuario no root (`backend/Dockerfile:42 · USER faro`) |
| 57 | ¿WAF o protección DDoS? | No | No hay en el repo; el proveedor podría ofrecerla: NO VERIFICADO |
| 58 | ¿Proceso de parches? | NO VERIFICADO | Sin proceso documentado; imágenes base `python:3.12-slim` y `node:20-alpine` |
| 59 | ¿Registros de auditoría de acciones de usuario? | Parcial | Auditoría por middleware, solo éxitos, solo `admin` la lee (`backend/middleware/audit_trail.py:41 · class AuditMiddleware`); mutable y sin retención |
| 60 | ¿Registro de acceso HTTP y su retención? | Parcial | Se registra (`backend/middleware/request_logger.py:10 · log = logging.getLogger("access")`); el plazo de retención no está definido |
| 61 | ¿Monitoreo y alertas? | Parcial | Panel y `GET /service-config/ops` (`backend/service_config/ops.py:226 · def snapshot`), correo diario de fallos; sin alertas inmediatas ni monitoreo externo |
| 62 | ¿SIEM? | No | No hay |

## F. Continuidad y respuesta a incidentes

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 63 | ¿Plan de continuidad / recuperación ante desastres? | Parcial | `business-continuity.md` y `deploy/RESTORE.md`; no ensayado en producción |
| 64 | ¿RTO y RPO definidos? | No | No hay objetivos fijados; el simulacro mide el tiempo y el panel la antigüedad del backup. Declarar "no medido en producción" |
| 65 | ¿SLA de disponibilidad? | No | Hay texto de disponibilidad en los términos; sin monitoreo externo que lo respalde |
| 66 | ¿Alta disponibilidad / sin punto único de falla? | No | Un solo VPS |
| 67 | ¿Plan de respuesta a incidentes? | Parcial | `incident-response.md`; sin ensayo ni guardia |
| 68 | ¿Plazo de notificación de brechas a clientes? | No | Marcador `[PLAZO DE NOTIFICACIÓN — confirmar]` en el DPA publicado |
| 69 | ¿Monitoreo de capacidad? | Sí | Panel: cola, pool de conexiones, disco con umbrales del registro (`backend/service_config/registry.py:821 · OPERATIONS = Service(`) |

## G. Privacidad

| # | Pregunta | Resp. | Evidencia / nota |
|---|---|---|---|
| 70 | ¿Política de privacidad publicada? | Sí | `/privacidad` (`Frontend/src/i18n/legal.ts`); con marcadores sin completar (razón social, servidor, logs) |
| 71 | ¿Acuerdo de tratamiento de datos (DPA) disponible? | Sí | `Frontend/src/i18n/legalExtra.ts` (clave `dpa`); contratos con subencargados: marcador sin confirmar |
| 72 | ¿Atiende solicitudes de acceso, rectificación, supresión y portabilidad? | Parcial | Export ZIP y borrado de tenant; sin flujo de solicitud por individuo ni anonimización por usuario |
| 73 | ¿Cookies de publicidad o analítica? | No | No se usan (política de privacidad y de cookies) |
| 74 | ¿Se recogen datos de menores? | No | Servicio para empresas (política, sección `minors`) |
| 75 | ¿Puede el cliente elegir la región de sus datos? | No | Una sola instalación; ubicación NO DEFINIDA |

# StockAI: plan de respuesta a incidentes

*Es un procedimiento operativo construido solo con herramientas que el producto ya tiene. No
inventa plazos: donde el texto legal publicado deja un marcador `[confirmar]`, aquí también queda
como decisión pendiente del propietario. Nunca se ha ensayado completo (**BRECHA**, ver §8).*

Quién actúa: los operadores de la instalación, es decir las direcciones de `INSTANCE_ADMIN_EMAILS`
(`backend/service_config/access.py:113 · def is_instance_operator`). Sin esa variable nadie opera desde
la app; por eso es el primer prerrequisito de este plan.

---

## 1. Qué cuenta como incidente

Evento de seguridad (acceso no autorizado, fuga de datos, credencial expuesta, abuso de una API
key, código malicioso) o de disponibilidad/integridad grave (app caída, datos corruptos, backups
fallando). Los niveles de severidad y los tiempos objetivo **no están definidos** (decisión del
propietario); un criterio mínimo para decidir: ¿hay datos de más de un tenant en juego, hay
credenciales comprometidas, hay pérdida de datos?

## 2. Detección: dónde mirar

| Señal | Dónde |
|---|---|
| Estado general en un lugar | Panel "Estado de la instalación" en `/instalacion` o `GET /service-config/ops` (`backend/api/v1/service_config.py:116 · @router.get("/ops")`): cola, jobs corriendo, latido del worker, fallos de 24 h por clase de error, pool de BD, consultas lentas, disco, último backup, latencia |
| Servicios apagados o degradados | `GET /health` (`backend/main.py:404 · @app.get("/health"`) |
| Loops cron que no corrieron | `/health` → `loops` (`backend/workers/loop_state.py`) |
| Qué falló en las últimas 24 h en todos los tenants | Correo diario al operador (`backend/notifications/operator_digest.py`) |
| Qué hizo un usuario o una API key | Auditoría del tenant: `GET /audit` y `/audit/export` (CSV), solo `admin` (`backend/api/v1/audit.py:25 · router = APIRouter(prefix="/audit"`) |
| Tráfico anómalo | Log de acceso, una línea por petición con ruta, estado, tenant y llave (`backend/middleware/request_logger.py:10 · log = logging.getLogger("access")`); uso por llave: `GET /api-keys/usage` |
| Reporte externo | Buzón de `security.txt` y página `/divulgacion-responsable` (ver `vulnerability-disclosure.md`) |

**BRECHA:** no hay alerta inmediata ni guardia; el correo diario significa que un incidente puede
pasar hasta ~24 h sin que el operador lo sepa si nadie mira el panel.

## 3. Contención: acciones disponibles hoy

| Situación | Acción | Efecto y límite |
|---|---|---|
| API key comprometida | `DELETE /api-keys/{id}` (analista o superior del tenant dueño) | Deja de autenticar al instante; la resolución consulta la base en cada llamada (`backend/auth/api_key_auth.py:32 · def hash_key` guarda solo el hash) |
| Usuario comprometido o ex-empleado | `PATCH /users/{id}/status` a `suspended`/`inactive` (`backend/users/service.py:275 · def update_status`) | Borra sus refresh tokens y bloquea el login; un access token ya emitido vale hasta 15 min |
| Forzar cierre de sesiones de un usuario | Restablecer su contraseña: marca `sessions_invalid_before` (`backend/auth/guards.py:159 · "SELECT sessions_invalid_before FROM users`) | Invalida tokens emitidos antes |
| Sesión propia sospechosa | `POST /auth/logout` (`backend/api/v1/auth.py:495 · async def logout`) | Revoca el `jti` y los refresh tokens |
| Credencial de un proveedor expuesta (DeepSeek, Resend, Twilio) | Revocarla en el proveedor y reingresar la nueva en `/instalacion` (efecto inmediato, sin reinicio) | Vaciar el campo apaga esa función en vez de usar otra (`docs/data-that-leaves.md`) |
| `SECRET_KEY` comprometida | Cambiar `SECRET_KEY` en `deploy/.env` y reiniciar | Cierra **todas** las sesiones de todos los tenants; además deja ilegibles las contraseñas SQL de las fuentes de datos (se derivan de esa llave, `backend/datasources/service.py:100 · def _fernet`): los clientes deben reingresarlas |
| `INTEGRATIONS_SECRET_KEY` o `instance_secret.key` comprometida | Generar otra llave y reingresar todos los secretos del panel | Los almacenados quedan ilegibles por diseño (`deploy/RESTORE.md`) |
| Abuso de una cuenta de prueba | Se borra sola a las 24 h (`backend/workers/worker.py:522 · def _trial_reaper_loop`) | |
| Parar el trabajo en segundo plano sin tumbar la app | `WORKER_ENABLED=false` y reiniciar la API | Avisar antes y con duración al cliente |
| Bloqueo por IP | Solo en Caddy o el firewall del VPS | **No hay mecanismo en la app** |

## 4. Preservación de evidencia (antes de limpiar nada)

1. Copiar los logs de los contenedores (`docker compose logs api worker frontend caddy`), que
   contienen el log de acceso.
2. Exportar la auditoría del tenant afectado (`/audit/export`) y la de `activity_logs` por consulta
   directa; **la auditoría no es inmutable** (ver `security-overview.md` §7), así que la copia
   temprana importa.
3. Guardar un dump de la base y un tar de `storage/` con el procedimiento de backup
   (`deploy/ops/backup.sh`) antes de modificar datos.

## 5. Erradicación y recuperación

- Corregir la causa, desplegar con `deploy/deploy.sh`; el rollback de versión es barato porque las
  migraciones son aditivas (`deploy/UPGRADE.md`).
- Si hay pérdida o corrupción de datos: restaurar según `deploy/RESTORE.md` y verificar con
  `scripts/restore_drill.py` (ver `business-continuity.md`). Tras restaurar, el panel muestra loops
  con estado antiguo; es lo esperado.
- Re-ejecutar los entrenamientos que estaban RUNNING cuando se tomó el backup.

## 6. Comunicación

- **Clientes afectados:** el DPA publicado promete notificar "sin demora indebida y en un plazo
  máximo de `[PLAZO DE NOTIFICACIÓN — confirmar]`" al correo del administrador de la cuenta
  (`Frontend/src/i18n/legalExtra.ts`). **El plazo no está fijado: decisión pendiente del
  propietario.** El contenido mínimo de la notificación: qué ocurrió, qué datos y qué tenants, qué
  se hizo, qué debe hacer el cliente (por ejemplo rotar sus API keys).
- **Autoridades:** la política de privacidad dice que se notificará "donde la ley lo exija". Hay
  marcos con plazos propios (por ejemplo, el RGPD fija 72 horas para el responsable ante la
  autoridad; la Ley 8968 de Costa Rica tiene sus propias obligaciones). **Confirmar con asesoría
  legal cuáles aplican a cada cliente antes de un incidente real.**
- **Subencargados:** si el incidente pasa por Hostinger, DeepSeek, Resend o Twilio, abrir caso con
  ellos y pedir su registro.

## 7. Después del incidente

Registrar: línea de tiempo, causa raíz, datos afectados, acciones, qué control faltó y cambio
concreto para cerrarlo (entra al backlog de `docs/stability.md`). Si el incidente expuso una
brecha de este pack, actualizar `security-overview.md` y `soc2-readiness-gap-analysis.md`.

## 8. Brechas de este plan

| Brecha | Esfuerzo |
|---|---|
| No existen severidades, tiempos objetivo ni guardia | M (decisión del propietario) |
| Nunca se ha hecho un ejercicio de mesa ni un ensayo | S por ejercicio |
| Sin alertas inmediatas (solo correo diario) | M |
| Auditoría mutable (la evidencia puede alterarse) | M |
| Sin plantilla de notificación aprobada por asesoría legal | S |
| Sin bloqueo por IP desde la app | S-M |

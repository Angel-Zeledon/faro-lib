# StockAI: continuidad del negocio y recuperación ante desastres

*Los objetivos RTO y RPO de este documento NO son promesas inventadas: son lo que se mide con
`scripts/restore_drill.py` y con el marcador de éxito del backup. Hasta que el propietario corra el
simulacro contra el backup de producción, **el RTO medido de producción es desconocido** y así debe
declararse en cualquier cuestionario.*

---

## 1. Qué hay que poder reconstruir

Dos mitades, y una sin la otra da una instalación que parece restaurada y no lo está
(`deploy/RESTORE.md`):

| Mitad | Contiene | Si se pierde |
|---|---|---|
| Dump de Postgres | todas las filas: tenants, usuarios, pronósticos, stock, órdenes, auditoría | todo |
| Directorio `STORAGE_PATH` | datasets, artefactos de modelos, documentos **y `instance_secret.key`** | los archivos y la capacidad de leer cada credencial guardada en la base |

## 2. Cómo se hace el backup hoy

- Cron nocturno en el servidor; en producción lo ejecuta `/opt/stockai-ops/backup.sh` con retención
  de 14 días (`deploy/README.md`, sección "Backups"). **Ese archivo vive solo en el servidor: su
  contenido real y su horario NO SE PUDIERON VERIFICAR desde el repositorio.**
- La copia de referencia del script está en `deploy/ops/backup.sh`: escribe a un nombre temporal y
  renombra solo si `pg_dump` y `gzip` terminaron bien; rechaza un tar de `storage/` sospechosamente
  pequeño (volumen vacío, `deploy/ops/backup.sh:68 · storage_archive_too_small_check_volume_name`); y escribe
  `last_success.json` **al final y de forma atómica** (`deploy/ops/backup.sh:82 · last_success.json.tmp`)
  con fecha, tamaños y SHA-256 de ambas mitades. Una ejecución fallida deja `last_failure.json` y no
  toca el marcador de éxito, de modo que la antigüedad del marcador es la alarma.
- El panel "Estado de la instalación" lee ese marcador (variable `BACKUP_STATUS_PATH`) y marca
  degradado si tiene más de `OPS_BACKUP_MAX_AGE_HOURS` (36 por defecto) o si el tar es sospechoso.
  Sin marcador visible informa **desconocido**, nunca "ok".

**Para activarlo en el servidor (pasos del propietario, no se han hecho):** ver
`deploy/RESTORE.md`, sección "Updating the server's backup script and wiring the marker".

## 3. RPO (cuánto dato se puede perder)

- **Medida:** la edad del backup usado; el simulacro la imprime (`backup age`) y el panel muestra la
  antigüedad del último éxito.
- **Peor caso por diseño:** el intervalo entre backups (nocturno según `deploy/README.md`, o sea
  hasta un día de datos entre dos dumps) más el desfase entre el dump (03:00) y el tar de storage
  (04:00) del ejemplo del README. No hay replicación continua ni WAL archivado (**BRECHA**): un
  fallo del VPS entre dos backups pierde esas horas.
- No se declara un número objetivo hasta confirmar el horario real del cron del servidor.

## 4. RTO (cuánto tarda en volver)

El simulacro (`scripts/restore_drill.py`) restaura el último backup en una base y un directorio
descartables y cronometra cada paso (restauración de la base, extracción de storage,
verificaciones). Imprime:

- tiempo medido de restauración (base + storage) y de todo el simulacro;
- lo que el número **excluye**: aprovisionar un servidor de reemplazo, copiar los backups a él,
  cambios de DNS/certificado, y el tiempo humano antes de empezar.

**Cómo fijar el RTO publicable:** correr el simulacro una vez al mes contra el backup real (ver
`deploy/RESTORE.md`), guardar la salida `--json`, y usar el peor de los últimos tres resultados
como tiempo medido; sumarle por separado, con estimación honesta, lo que el simulacro excluye. Una
corrida de desarrollo con una base casi vacía tomó segundos y **no es representativa**.

## 5. Escenarios

| Escenario | Respuesta | Estado |
|---|---|---|
| Se pierde el VPS | Servidor nuevo + `deploy/` + restaurar ambas mitades (`deploy/RESTORE.md`) | Documentado. **BRECHA:** los backups están en el mismo VPS; si el disco se pierde, se pierden con él. Copia externa: **no existe documentada** |
| Corrupción de la base | Restaurar el dump más reciente bueno | Documentado; verificar con el simulacro |
| Se pierde `storage/` o su volumen | Restaurar el tar; sin `instance_secret.key` hay que reingresar credenciales | Documentado y probado el 2026-09-16 (`deploy/RESTORE.md`) |
| Despliegue defectuoso | Volver a la imagen previa; las migraciones son aditivas (`deploy/UPGRADE.md`) | Documentado |
| Un job de entrenamiento queda colgado tras un reinicio | Recuperación de huérfanos por `WORKER_ID` (`backend/workers/worker.py:41 · def recover_orphaned_jobs`) | Implementado |
| Se pierde la llave Fernet | Reingresar secretos del panel | Documentado |
| Cae DeepSeek, Resend o Twilio | La función afectada se apaga o degrada a texto por reglas; forecasting, semáforo y órdenes siguen (`backend/ai/local_llm.py:199 · class LLMNotConfigured`) | Implementado |
| Cae Postgres | `/health` responde con `database: false` en vez de morir; el pool devuelve 503 con `Retry-After` cuando se satura (`backend/db/connection.py:130 · class PoolExhausted`) | Implementado |
| Cae Hostinger/el VPS entero | No hay segundo sitio ni conmutación | **BRECHA** (punto único de falla) |

## 6. Prueba periódica

- Frecuencia propuesta: **mensual** (`deploy/RESTORE.md`, "Monthly restore drill"). Hoy: **nunca se
  ha corrido contra producción** (la única restauración documentada es manual, del 2026-09-16, con
  conteos y sin cronometraje).
- Criterio de aprobado: cero `fail` en el informe; los `warn` se revisan y se archivan. Un `fail` en
  `row_counts`, `foreign_keys`, `datasets` o `archives` es un backup roto y es un incidente
  (`incident-response.md`).

## 7. Brechas de continuidad

| Brecha | Esfuerzo |
|---|---|
| Backups solo en el mismo VPS: añadir copia externa cifrada (otro proveedor o almacenamiento de objetos) | M |
| Backups sin cifrar | S-M |
| Sin archivado continuo de WAL (RPO mejor que 24 h) | L |
| Punto único de falla (un VPS) | XL |
| RTO de producción sin medir; horario real del cron NO VERIFICADO | S (correr el simulacro; leer el crontab) |
| Sin monitoreo externo que avise si el sitio cae | S-M |

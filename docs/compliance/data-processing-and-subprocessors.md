# StockAI: datos personales, retención, borrado, exportación y subencargados

*Para cuestionarios de seguridad y de protección de datos. Citas verificadas por test; ver
`security-overview.md` para el significado de los estados.*

Documentos públicos relacionados (texto legal en `Frontend/src/i18n/legal.ts` y
`Frontend/src/i18n/legalExtra.ts`): política de privacidad, acuerdo de tratamiento de datos (DPA),
política de IA, política de cookies. Este documento los **contrasta con el código**; donde no
coinciden, se dice.

---

## 1. Qué datos se guardan

| Categoría | Contenido | Dónde |
|---|---|---|
| Cuenta | nombre, correo, número de WhatsApp, nombre de la empresa, rol, fecha/versión de términos aceptados; contraseña solo como hash bcrypt | tablas `users`, `tenants` |
| Datos del negocio | historial de ventas, inventario, productos, costos, proveedores y sus contactos, órdenes de compra, recepciones, documentos subidos al asistente, conversaciones con el asistente, conexiones SQL configuradas (contraseña cifrada) | Postgres y `storage/` |
| Actividad | quién hizo qué y cuándo (auditoría, alertas enviadas o fallidas) | `activity_logs` |
| Uso de API | llamadas por día y por llave; de cada llave solo el hash y los últimos 4 caracteres | `api_usage_daily`, `api_keys` |
| Técnicos | IP, fecha, ruta, estado, tenant en el log de acceso (`backend/middleware/request_logger.py:10 · log = logging.getLogger("access")`); intentos de login por correo y creación de cuentas de prueba por IP (`auth_rate_events`) | logs del servidor y Postgres |

Datos personales de terceros: los archivos del cliente pueden mencionar personas (contacto de un
proveedor, nombre de un cliente en ventas). Según la política de privacidad, para esos datos el
responsable es el cliente y StockAI es encargado. No existe en el producto un clasificador ni un
campo "dato personal": StockAI no sabe qué columnas lo son (**BRECHA** para quien exija
inventario de datos personales por campo).

No se guardan datos de pago (no hay Stripe ni checkout, `CLAUDE.md`). No hay analítica, pixeles ni
cookies de publicidad.

## 2. Retención

| Dato | Regla | Evidencia |
|---|---|---|
| Cuenta y datos del negocio | mientras exista la cuenta | política de privacidad (`legal.ts`, sección "retention") |
| **Sesiones de pronóstico** | **son permanentes**: "Eliminar" se volvió "archivar"; la fila, sus resultados y sus artefactos quedan y solo salen de la lista de trabajo hasta que alguien las restaure | `backend/db/migrations.py:1426 · Sessions are permanent (2026-10-04)` |
| Cuenta de prueba (`/prueba`) | 24 h y se borra entera, salvo que haya pedido contacto (solicitud `new`) | `backend/workers/worker.py:522 · def _trial_reaper_loop` |
| Tenant borrado | todas sus tablas y archivos se borran a la vez | `backend/tenants/data_export.py:233 · def delete_tenant` |
| Copias de seguridad | nocturnas, 14 días (cifra de la política de privacidad y del DPA) | `deploy/README.md` (sección "Backups"); el script real vive en el servidor: **NO VERIFICADO** |
| Eventos de intentos (login, trial) | solo dentro de su ventana (minutos a 24 h) | `backend/api/v1/auth.py::_check_rate` |
| `activity_logs` (auditoría) | **sin política de retención**: crece sin límite | `docs/stability.md`, "What is deliberately NOT here" (**BRECHA**) |
| Logs técnicos del servidor | **plazo no definido** (la política publicada trae el marcador `[PLAZO DE LOS REGISTROS DEL SERVIDOR]`) | `Frontend/src/i18n/legal.ts` (**BRECHA**) |
| Tokens revocados | se purgan al vencer (`backend/auth/blocklist.py`, `purge_expired`) | |

Consecuencia directa de "las sesiones son permanentes": un cliente no puede quitar una sesión
(sus datos de ventas derivados y artefactos de modelo) por la vía de "eliminar"; solo el borrado del
tenant las elimina. Hay que decirlo a quien pregunte por "derecho de supresión granular".

## 3. Exportación y borrado (portabilidad y supresión)

- **Exportar:** `GET /tenant/export` (solo `admin`) devuelve un ZIP con una tabla por archivo,
  limitado al `tenant_id` del token (`backend/api/v1/tenant_data.py:29 · @router.get("/export")`,
  `backend/tenants/data_export.py:110 · def build_export_zip`). Los secretos (hashes de contraseña y de
  tokens/llaves, secretos de webhooks) se excluyen con una lista explícita de columnas. La propia
  exportación queda en la auditoría.
- **Borrar:** `DELETE /tenant` (solo `admin`, exige escribir el slug del tenant o `DELETE`;
  irreversible) (`backend/api/v1/tenant_data.py:45 · @router.delete("")`). Borra tabla por tabla
  dentro de una transacción y luego los directorios de `storage/`
  (`backend/tenants/data_export.py:212 · _STORAGE_CATEGORIES`). Un test falla si aparece una tabla con
  `tenant_id` que el borrado no cubre
  (`backend/tests/test_tenant_data.py:211 · def test_delete_order_covers_every_tenant_scoped_table`).
- **Lo que el borrado NO hace (BRECHAS):**
  1. No toca las copias de seguridad: los datos desaparecen de ellas solos al vencer los 14 días.
  2. No queda ningún registro del borrado en la base (se borra con el tenant); solo una línea
     `log.warning` con tenant y usuario en el log del servidor.
  3. No solicita borrado a los subencargados (DeepSeek, Resend, Twilio, y Voyage/Pinecone si
     están activos).
  4. No existe borrado de un **usuario individual** como derecho de supresión de datos personales:
     `DELETE /users/{user_id}` elimina al usuario del tenant
     ({{backend/api/v1/users.py::@router.delete("/{user_id}")}}) pero no hay anonimización de su rastro
     en `activity_logs`.
- No hay endpoint de "acceso" (DSAR) distinto del export completo del tenant.

## 4. Subencargados (los realmente usados por el código)

Todos son opcionales salvo el servidor: sin credencial, la función se apaga y el resto del producto
funciona (`docs/data-that-leaves.md`; cada servicio reporta su estado en `/health`).

| Subencargado | Para qué | Qué datos recibe | Cuándo | Evidencia |
|---|---|---|---|---|
| **Hostinger** | VPS donde corren app, base de datos y `storage/`; SMTP/correo de `stockai.es` | todos los datos del servicio | siempre en el servicio alojado | `deploy/README.md`; ubicación del servidor: **NO DEFINIDA** (placeholder en la política) |
| **DeepSeek** (China) | asistente, RAG, resúmenes, explicación de pronósticos, diagnóstico de calidad | la pregunta del usuario y el contexto necesario: códigos y nombres de producto, cantidades, costos, estado del semáforo, proveedores, montos. No contraseñas ni credenciales | solo si `DEEPSEEK_API_KEY` está configurada; un único proveedor y sin cadena de respaldo (`backend/ai/local_llm.py:199 · class LLMNotConfigured`) | `docs/data-that-leaves.md` |
| **Resend** (o SMTP propio/Hostinger) | alertas, órdenes de compra a proveedores, invitaciones, cambios de contraseña | dirección del destinatario, asunto, cuerpo y PDF de la orden | solo si `RESEND_API_KEY` o SMTP están configurados (`backend/notifications/email.py:127 · def _send_resend`) | `docs/data-that-leaves.md` |
| **Twilio** (EE. UU.) | WhatsApp y SMS | número y texto del mensaje | solo si `TWILIO_*` están configurados | `docs/data-that-leaves.md` |
| **Voyage AI + Pinecone** | búsqueda semántica sobre documentos que el cliente sube al asistente (RAG) | el **texto de los fragmentos de los documentos** (embeddings en Voyage; vectores y texto en un índice Pinecone, un namespace por tenant) | solo si `VOYAGEAI_API_KEY`, `PINECONE_API_KEY` e índice están configurados | `backend/ai/document_indexer.py`, `backend/service_config/registry.py` (servicio `rag`) |

**Discrepancia que hay que resolver (hallazgo de este trabajo):** Voyage AI y Pinecone **no aparecen**
ni en `docs/data-that-leaves.md` (que dice "tres destinos") ni en las tablas de encargados de la
política de privacidad y del DPA, pero el código los usa para indexar documentos cuando se
configuran. Si en producción esas llaves están puestas (**NO VERIFICADO**: no se puede leer
`/instalacion` del servidor desde aquí), hay un tratamiento por subencargado no declarado. Acción:
(a) confirmar con `/health` → `services.rag` en producción; (b) si está activo, agregarlos a
`legal.ts`, `legalExtra.ts` y a `docs/data-that-leaves.md`; si no está activo, agregar la nota
"opcional, apagado".

Otros puntos pendientes de los textos publicados (marcadores `[... confirmar con el propietario]`
presentes en `legal.ts`/`legalExtra.ts`): ubicación del servidor, ubicación del proveedor de correo,
contratos con subencargados, plazo de aviso de cambio de subencargados, **plazo de notificación de
brechas**, plazo de los logs, razón social/NIF. Un cuestionario que los pida no puede contestarse
con los textos actuales.

## 5. Transferencias internacionales

DeepSeek (China) y Twilio (EE. UU.) son las salidas declaradas; la política dice que China no tiene
decisión de adecuación de la Comisión Europea y ofrece no usar las funciones de IA. Hoy la IA **no se
puede apagar por cuenta** desde la aplicación (la política lo dice: hay que escribir al propietario);
se apaga para toda la instalación dejando vacía `DEEPSEEK_API_KEY` (**Parcial**).

## 6. Quién puede ver los datos dentro de StockAI

- Cada tenant solo ve sus datos (ver `security-overview.md` §10).
- Los operadores de la instalación ven agregados de todos los tenants (cola, fallos con id de tenant
  y de sesión) y el correo diario de fallos; no hay un modo de "soporte con consentimiento" ni
  registro de accesos del operador a datos de un tenant (**BRECHA**; el operador con acceso a la base
  o al disco ve todo).

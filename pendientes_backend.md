# Pendientes — Backend

Estado al 2026-05-24 — **TODOS LOS ÍTEMS CRÍTICOS Y MEDIANOS COMPLETADOS**

---

## ✅ Completados

### 1. `_generate_forecast_series` usa `engine.get_forecast()`
`workers/runner.py` — usa forecasts reales de LightGBM/ARIMA/etc. devueltos por `engine.get_forecast()`. Guarda `{sku: {model: {"historical": [...], "forecast": [...]}}}` en `session_results.forecasts`.

### 2. Endpoint drift monitoring
`api/v1/forecasts.py` — `POST /sessions/{id}/drift` acepta CSV nuevo y corre `DriftDetector` (PSI + KS). Usa dataset de referencia del training.

### 3. Jobs en RUNNING no se recuperan al reiniciar → RESUELTO
`main.py::_recover_running_jobs()` — en startup, marca todos los jobs RUNNING como FAILED y transiciona la sesión a FAILED.

### 4. Paginación en endpoints de lista
`GET /sessions` y `GET /datasets` devuelven `{items, total, skip, limit}`. Frontend (`api.ts::getSessions`) ya maneja el formato paginado.

### 5. JWT logout stateless → RESUELTO
`auth/blocklist.py` — revocación de tokens con tabla PostgreSQL `revoked_tokens`. Verificado en `auth/guards.py::get_current_user`.

### 6. PDF report con layout
`api/v1/reports.py` — PDF con `reportlab` (tablas, colores, metadata). Fallback a texto plano si no está instalado.

### 7. Migración JSON → PostgreSQL
Completa. `db/connection.py` + `db/session_store.py` — psycopg2 ThreadedConnectionPool, sin fallbacks a JSON en disco.

---

## Pendientes (prioridad baja)

### 8. Rate limiting con slowapi
Existe un límite soft de 3 jobs concurrentes por tenant en `api/v1/training.py`. Para rate limiting por IP/minuto se necesitaría `slowapi`. **No bloqueante.**

### 9. Notificaciones email reales
`notifications/email.py` actualmente solo loguea la URL. Integrar Resend o SendGrid cuando se despliegue en producción.

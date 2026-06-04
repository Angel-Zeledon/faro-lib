# Pendientes — Frontend

Estado al 2026-05-24 — **TODOS LOS ÍTEMS COMPLETADOS**

---

## ✅ Completados

### 1. Comparaciones de estado en UPPER_CASE
Todos los `s.status === '...'` usan UPPER_CASE (`DATASET_LOADED`, `COMPLETED`, etc.). `USABLE_STATUSES` definido en `forecast/page.tsx`.

### 2. Polling de training usa `GET /jobs/{job_id}`
`forecast/page.tsx` Step7 — `startTraining()` devuelve `job_id`, se guarda y se usa en `getJob(job_id)` cada 2 segundos.

### 3. Upload en wizard sigue flujo correcto del Backend
Step1 y `data/page.tsx` — 3 pasos: `uploadDataset(fd)` → `attachDataset(sessionId, ds.id)` → `inspectSession(sessionId)`.

### 4. `getColumns` reemplazado por `inspectSession`
Step2 usa `inspection.column_options` pasado desde Step1. No hay llamada a `getColumns` ni `/configure/columns`.

### 5. `getConfigSchema` y `getAvailableModels` usan `session_id`
Step3 lee `inspection.config_schema.features`; Step4 llama `getAvailableModels(sessionId)`.

### 6. Config page defaults pre-llenan el wizard
Steps 3, 4 y 6 leen `localStorage.getItem('forecast_defaults')` en la inicialización del estado.

### 7. Routing Preview — Step 5 del wizard
`forecast/page.tsx::Step5` muestra matriz de modelos × tipos de series, stats del dataset, y warnings.

### 8. Data quality preview antes de entrenar — Step 7
`forecast/page.tsx::Step7` muestra un panel de calidad con rows/SKUs/freq y warnings antes del botón "Start Training".

### 9. `analyst/page.tsx` verificado correcto
Usa `res.answer` (no `res.data.answer`). Filtra sesiones COMPLETED. Fallback local si la API falla.

### 10. Archivos Vite legacy eliminados
No quedan archivos `.jsx`, `vite.config.js`, `index.html`, `api.js` en `Frontend/src/`.

### 11. Error boundaries
`Frontend/src/app/error.tsx` — `GlobalError` con botón "Try again" y mensaje de error.

### 12. Paginación en tabla de resultados
Step8 usa `PAGE_SIZE = 50` con controles de página ← →.

# ROI — Evolución mensual (feature 1.5)

> Fecha: 2026-07-11
> Origen: `docs/features_propuestas_faro_2026-07-05.md`, Horizonte 1, item 1.5 ("Faro te ahorró $X")
> Contexto: `/inventory/roi` ya existe y muestra métricas acumuladas (POs generados, adopción, comparación simple mes actual/anterior). Esta spec lo evoluciona para mostrar quiebres evitados, capital liberado de sobrestock y % de adopción desglosados por mes.

## Alcance

Solo en la página — sin envío proactivo por email/WhatsApp. El usuario consulta la evolución mensual cuando entra a `/inventory/roi`. El canal de Resend/WhatsApp del briefing diario (feature 1.1) no se toca.

## Decisiones de diseño

1. **"Capital liberado de sobrestock" se mide con un snapshot mensual nuevo**, no con una aproximación retroactiva sobre `inventory_snapshots` existente. Razón: `inventory_snapshots` no guarda costo histórico ni el semáforo histórico por SKU, así que cualquier aproximación retroactiva sería frágil. En vez de eso, un job mensual guarda el valor total en SOBRESTOCK (ya calculado por el motor) el día 1 de cada mes. La métrica de "capital liberado" solo existe hacia adelante — no hay backfill de meses pasados.
2. **Meses sin snapshot muestran "—"** en la columna de capital liberado (mínimo 2 snapshots consecutivos para calcular un delta).
3. **Se reemplaza el bloque `MonthKPIs` actual** (que solo compara conteo de pedidos mes actual vs. anterior) por una tabla de evolución de últimos 6 meses, que ya incluye esa comparación como sus dos primeras filas.

## 1. Modelo de datos

Nueva tabla, migración idempotente en `backend/db/migrations.py` (mismo patrón que las tablas `inventory_*` existentes):

```sql
CREATE TABLE IF NOT EXISTS inventory_overstock_snapshots (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    overstock_value FLOAT NOT NULL,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS inventory_overstock_snapshots_tenant_idx
    ON inventory_overstock_snapshots (tenant_id, recorded_at DESC);
```

`overstock_value` = suma de `valor_inventario` (stock_actual × costo_unitario, ya calculado en `get_inventory_status`) de todos los SKUs con signal `SOBRESTOCK`, para la última sesión COMPLETED del tenant.

## 2. Backend

### `backend/inventory/service.py`
- `run_monthly_overstock_snapshot() -> None`: mismo patrón que `run_daily_inventory_alerts()`. Itera `get_tenants_with_active_sessions()`; para cada tenant, toma `get_latest_completed_session(tid)`, corre `get_inventory_status`, suma `valor_inventario` de items con `signal == "SOBRESTOCK"`, inserta una fila en `inventory_overstock_snapshots`. Tenants sin sesión completada se saltan (igual que el loop diario).

### `backend/workers/worker.py`
- Nuevo `_monthly_overstock_snapshot_loop()`: calcula el próximo día 1 del mes a las 00:05 UTC, duerme hasta esa hora (mismo patrón de `_inventory_alert_loop`), llama a `run_monthly_overstock_snapshot()`, repite. Se registra en `start()` junto a los loops existentes.

### `backend/inventory/roi_service.py`
- Nueva `get_monthly_summary(tenant_id: str, months: int = 6) -> list[dict]`:
  - Agrupa `inventory_po_log` por `date_trunc('month', generated_at)` de los últimos `months` meses: `pos_count`, `skus_pedir_ya` (suma → "riesgos de quiebre atendidos"), `total_value` (suma → "valor gestionado"), `suggested_count`/`approved_count` (suma → `adoption_rate` del mes).
  - Para cada mes, busca el `overstock_value` de `inventory_overstock_snapshots` más cercano al inicio de ese mes y al inicio del mes siguiente; `capital_liberado = overstock_value(mes_anterior) - overstock_value(mes_actual)` si ambos existen y el resultado es positivo, si no `None`.
  - Devuelve lista ordenada de más reciente a más antiguo, cada fila: `{month, pos_count, skus_pedir_ya, total_value, adoption_rate, capital_liberado}`.

### `backend/api/v1/inventory.py`
- Nuevo `GET /inventory/roi/monthly?months=6` → `ok(get_monthly_summary(user.tenant_id, months))`. Mismo permiso que `/inventory/roi` (`get_current_user`, es solo lectura — viewer incluido).

## 3. Frontend

- `Frontend/src/lib/api.ts`: `getROIMonthly(months = 6): Promise<ROIMonthlyRow[]>`.
- `Frontend/src/lib/types.ts`: `ROIMonthlyRow { month: string; pos_count: number; skus_pedir_ya: number; total_value: number | null; adoption_rate: number | null; capital_liberado: number | null }`.
- `Frontend/src/app/inventory/roi/page.tsx`:
  - Elimina `MonthKPIs` y su bloque de sección "Section 2".
  - Nuevo componente `MonthlyEvolutionTable` (mismo estilo visual que `POHistoryTable`): columnas Mes | Pedidos | Riesgos de quiebre atendidos | Valor gestionado | % adopción | Capital liberado de sobrestock. Filas con `capital_liberado === null` muestran "—" con un tooltip/nota breve ("aún no hay suficiente historial").
  - Se carga junto con `roi` y `history` en el `load()` existente (`Promise.all`).
- `Frontend/src/i18n/translations.ts`: nuevas claves bajo `roi.*` (`roi.monthly_evolution_title`, `roi.col_month`, `roi.col_capital_freed`, `roi.capital_freed_pending`, etc.) en ambos idiomas ya soportados.

## 4. Testing

Sigue `TESTING_GUIDELINES.md` — estado real vs. status codes:

- `get_monthly_summary`: insertar filas directamente en `inventory_po_log`/`inventory_po_items`/`inventory_overstock_snapshots` con fechas controladas (mock de 2-3 meses), verificar que la agregación por mes y el cálculo de `capital_liberado` sean correctos, incluyendo el caso de un mes sin snapshot previo (`capital_liberado is None`).
- `run_monthly_overstock_snapshot`: crear tenant + sesión COMPLETED + stock con SKUs en SOBRESTOCK, correr el job, verificar con `query_one` que se insertó la fila con el `overstock_value` esperado.
- Permisos de `GET /inventory/roi/monthly`: viewer_headers → 200 (es lectura), sin auth → 401.

## Fuera de alcance

- Envío proactivo (email/WhatsApp) del resumen mensual — queda para una iteración futura si el usuario lo pide explícitamente.
- Backfill de capital liberado para meses anteriores a la existencia de esta feature.

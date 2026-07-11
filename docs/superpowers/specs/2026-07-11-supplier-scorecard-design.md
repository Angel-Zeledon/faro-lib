# Scorecard de proveedores (feature 2.5)

> Fecha: 2026-07-11
> Origen: `docs/features_propuestas_faro_2026-07-05.md`, Horizonte 2, item 2.5 ("Scorecard de proveedores")
> Contexto: la recepción de PO (feature 1.4) ya registra cuándo llega cada pedido y aprende el lead time real por proveedor en `supplier_lead_time_obs`. Esta spec construye la pantalla que expone ese historial como poder de negociación para el comprador — sin ningún servicio o API externa.

## Alcance

Solo lectura, página nueva. No requiere email/WhatsApp, no requiere integraciones externas (Siigo/Alegra) — todo se calcula de datos que Faro ya registra al recibir un pedido.

## Decisiones de diseño

1. **El "rango" de lead time es solo para la visualización del scorecard**, no cambia el motor de inventario. `/inventory` (semáforo, punto de reorden, días de cobertura) sigue usando `lead_time_dias` declarado como número único, sin cambios. El scorecard muestra el lead time REAL observado como rango (mínimo–máximo de las recepciones registradas) en vez de un promedio único que esconde la variabilidad.
2. **"A tiempo" = lead time real ≤ lead time declarado**, sin margen de tolerancia. Compara la promesa del proveedor (su ficha) contra la realidad (lo que Faro observó), sin mezclar con `lead_time_std` (que representa variabilidad esperada, un concepto distinto).
3. **Un proveedor solo aparece en el scorecard una vez que tiene al menos una recepción registrada** — antes de eso no hay nada que mostrar. Mismo criterio que ya usa (sin consumidores) `get_supplier_lead_time_stats` hoy.
4. **Fill rate y valor comprado solo cuentan órdenes con al menos un evento de recepción** (`inventory_po_log.reception_status <> 'pending'`) — así un pedido que legítimamente todavía no ha llegado (porque no le ha dado tiempo) no arrastra el fill rate hacia abajo.

## 1. Backend

### `backend/inventory/reception_service.py`
Reemplaza `get_supplier_lead_time_stats(tenant_id)` (existe, sin consumidores en el frontend) por `get_supplier_scorecard(tenant_id) -> list[dict]`:

- Query 1 (lead time + a tiempo), agrupando `supplier_lead_time_obs` por proveedor, join contra `suppliers` por nombre (case-insensitive, mismo patrón ya usado):
  ```sql
  SELECT o.proveedor,
         COUNT(*)::int                      AS n_recepciones,
         MIN(o.lead_time_days)              AS lead_time_real_min,
         MAX(o.lead_time_days)              AS lead_time_real_max,
         AVG(o.lead_time_days)              AS lead_time_real_avg,
         MAX(o.observed_at)                 AS ultima_recepcion,
         s.lead_time_dias                   AS lead_time_declarado,
         AVG(CASE WHEN o.lead_time_days <= s.lead_time_dias THEN 1.0 ELSE 0.0 END)
             FILTER (WHERE s.lead_time_dias IS NOT NULL) AS on_time_rate
  FROM supplier_lead_time_obs o
  LEFT JOIN suppliers s
    ON s.tenant_id = o.tenant_id AND LOWER(s.name) = LOWER(o.proveedor)
  WHERE o.tenant_id = %s
  GROUP BY o.proveedor, s.lead_time_dias
  ORDER BY n_recepciones DESC, o.proveedor
  ```
- Query 2 (fill rate + valor), agrupando `inventory_po_items` por proveedor, join contra `inventory_po_log` para excluir órdenes aún `pending`:
  ```sql
  SELECT poi.proveedor,
         COALESCE(SUM(poi.cantidad_recibida), 0) AS total_recibido,
         COALESCE(SUM(poi.cantidad_final), 0)    AS total_pedido,
         COALESCE(SUM(poi.cantidad_final * poi.costo_unitario), 0) AS valor_comprado
  FROM inventory_po_items poi
  JOIN inventory_po_log pol ON pol.id = poi.po_log_id
  WHERE poi.tenant_id = %s
    AND poi.status IN ('approved', 'modified')
    AND poi.proveedor IS NOT NULL AND poi.proveedor <> ''
    AND pol.reception_status <> 'pending'
  GROUP BY poi.proveedor
  ```
- Combina ambas por `proveedor` en Python (mismo patrón de dos-queries-y-merge ya usado en `get_monthly_summary`, feature 1.5). `fill_rate = total_recibido / total_pedido` si `total_pedido > 0`, si no `None`. Filas de la query 2 sin proveedor coincidente en la query 1 se ignoran (el scorecard está anclado a proveedores con lead time observado).
- Devuelve lista de dicts: `{proveedor, n_recepciones, lead_time_real_min, lead_time_real_max, lead_time_real_avg, lead_time_declarado, on_time_rate, fill_rate, valor_comprado, ultima_recepcion, desviacion_dias}` (mantiene `desviacion_dias` = `lead_time_real_avg - lead_time_declarado`, ya existente).

### `backend/api/v1/inventory.py`
Renombra el endpoint existente `GET /suppliers/lead-times` → `GET /suppliers/scorecard`, mismo permiso (`get_current_user`, solo lectura).

## 2. Frontend

- `Frontend/src/lib/types.ts`: reemplaza `SupplierLeadTimeStat` por `SupplierScorecardRow` con los campos nuevos (`fill_rate`, `valor_comprado`, `on_time_rate`).
- `Frontend/src/lib/api.ts`: reemplaza `getSupplierLeadTimes` por `getSupplierScorecard()` → `GET /inventory/suppliers/scorecard`.
- `Frontend/src/app/inventory/suppliers/scorecard/page.tsx` (nueva): tabla con columnas Proveedor | Recepciones | Lead time real (rango "mín–máx d") | Lead time declarado | % A tiempo | % Fill rate | Valor comprado | Última recepción. Mismo lenguaje visual que `MonthlyEvolutionTable`/`POHistoryTable` de `/inventory/roi` (tabla con cabecera `C.card`, filas alternas). Estado vacío: "Aún no hay recepciones registradas" con link de vuelta a Proveedores.
- `Frontend/src/app/inventory/suppliers/page.tsx`: agrega un link "Scorecard" hacia la página nueva, mismo patrón visual que el link "Impact" en `/inventory`.
- Traducciones nuevas bajo `supplier_scorecard.*` en `Frontend/src/i18n/translations.ts`, bloques `es` y `en`.

## 3. Testing

Sigue `TESTING_GUIDELINES.md` — estado real vs. mocks:
- `get_supplier_scorecard`: insertar directamente en `suppliers`, `inventory_po_log`, `inventory_po_items` (con `cantidad_recibida` seteada) y `supplier_lead_time_obs` con valores controlados; verificar rango min/max, `on_time_rate` (con un caso a tiempo y uno tarde), `fill_rate` (con una orden `pending` que debe excluirse), y `valor_comprado`.
- Endpoint `GET /inventory/suppliers/scorecard`: permiso de lectura (viewer 200, sin auth 401).

## Fuera de alcance

- Cambiar `lead_time_dias` en el motor de inventario (semáforo, punto de reorden) a un rango — el rango es solo de visualización en el scorecard.
- Envío de scorecard por email/PDF, o cualquier canal externo.
- Conector Siigo/Alegra u otra integración externa.

# Diseño — Multibodega + Optimizador MILP (compras y transferencias)

**Fecha:** 2026-07-12
**Alcance:** sub-proyectos 4-6 de la decomposición del Data Alignment Wizard —
dimensión de bodega en el modelo de datos, semilla de datos mock coherente, motor de
optimización MILP (compras + transferencias inter-bodega), y la superficie
frontend/API que expone sus recomendaciones.

**Decisiones del usuario (confirmadas):**
- Construir contra **datos sintéticos/demo**, y mantener la **DB siempre llena de mock
  data coherente con la lógica de negocio** (ver memoria `feedback-mock-data-seeding`).
- El MILP decide **órdenes de compra + transferencias inter-bodega**.
- Esquema: **tabla `warehouses` + columna `bodega` en `inventory_stock`**, unique
  `(tenant, sku, bodega)`; CSV/plantilla gana columna `bodega` opcional (default
  `"principal"`, no rompe lo existente).
- Solver: **`scipy.optimize.milp`** (ya instalado, sin dependencia nueva).

Se implementa en **tres planes** por dependencia:
- **Plan MW-1 (fundación):** esquema de bodega + migración + upsert/import + seed mock.
- **Plan MW-2 (optimizador):** motor MILP puro en el layer de negocio.
- **Plan MW-3 (superficie):** endpoint + UI de compras/transferencias recomendadas.

---

## Sub-proyecto 4 — Dimensión de bodega (Plan MW-1)

### Esquema
- Nueva tabla `warehouses (id, tenant_id, name, is_default BOOL, created_at)`.
  Cada tenant tiene al menos una bodega `"principal"` (`is_default = true`).
- `inventory_stock` gana columna `bodega TEXT NOT NULL DEFAULT 'principal'`.
- **Migración de la constraint única** (delicada, pasos ordenados):
  1. `ADD COLUMN bodega TEXT NOT NULL DEFAULT 'principal'` (backfill implícito).
  2. `DROP CONSTRAINT` de la unique `(tenant_id, sku)` existente.
  3. `ADD CONSTRAINT UNIQUE (tenant_id, sku, bodega)`.
  Todo con `IF EXISTS`/`IF NOT EXISTS` donde el motor lo permita, y como migraciones
  aditivas separadas en `backend/db/migrations.py` (el orden importa — la columna antes
  del cambio de constraint).
- `upsert_stock`'s `ON CONFLICT (tenant_id, sku)` → `ON CONFLICT (tenant_id, sku, bodega)`;
  `bodega` entra al set `allowed` (default `"principal"` si no se provee).

### Import / plantilla
- `bulk_import` y el CSV template ganan la columna `bodega` (opcional; filas sin ella →
  `"principal"`). Una bodega nueva mencionada en un CSV se auto-crea en `warehouses`.

### Compatibilidad
- Todo el código actual que consulta `inventory_stock` por `(tenant, sku)` sigue
  funcionando para tenants de una sola bodega (todo cae en `"principal"`). Los agregados
  existentes (semáforo, valor de inventario) se calculan **sumando entre bodegas** por SKU
  cuando no hay contexto de bodega, o filtrando por bodega cuando lo hay.

### Semilla de datos mock (directiva "DB siempre llena")
- Script idempotente `backend/db/seed_mock.py` que puebla un tenant demo coherente:
  N bodegas, M SKUs con stock por bodega, historia de ventas suficiente por SKU×bodega,
  proveedores, y algo de historial de PO — todo respetando relaciones reales (costo <
  precio_venta, stock ≥ 0, lead times razonables, demanda correlacionada con stock). Se
  ejecuta al bootstrap y es re-ejecutable sin duplicar. **Nunca** falsea respuestas de API
  (eso viola `feedback-no-mock-data`); solo inserta filas reales.

---

## Sub-proyecto 5 — Motor MILP (Plan MW-2)

Módulo nuevo `ForecastingCore/forecasting_core/business/optimizer.py` (función pura, sin
DB — el backend lo orquesta, igual que `InventoryAdvisor`). Usa `scipy.optimize.milp`.

### Modelo
Índices: SKU `i`, bodega `w`, bucket de tiempo `t ∈ {1..H}`.

Variables de decisión:
- `order[i,w,t]` — unidades a comprar a proveedor hacia bodega `w` (entero ≥ 0).
- `transfer[i,a,b,t]` — unidades a mover de bodega `a` a `b`, `a≠b` (entero ≥ 0).
- `inv[i,w,t]` — nivel de inventario proyectado (continuo ≥ 0).
- `short[i,w,t]` — faltante/quiebre (continuo ≥ 0, variable de holgura).

Balance de inventario (restricción de igualdad, por `i,w,t`):
```
inv[i,w,t] = inv[i,w,t-1]
           + order[i,w, t - lead_time]        (llega tras el lead time; 0 si t-LT < 1)
           + Σ_a transfer[i,a,w,t]            (entradas por transferencia)
           - Σ_b transfer[i,w,b,t]            (salidas por transferencia)
           - demand[i,w,t]
           + short[i,w,t]
```
`inv[i,w,0]` = stock actual de `inventory_stock` para ese SKU×bodega.
`demand[i,w,t]` = forecast por SKU×bodega en el bucket `t` (del motor de forecast; si el
forecast es a nivel SKU sin bodega, se reparte proporcional al stock/histórico por bodega).

Función objetivo (minimizar costo total):
```
min  Σ holding_cost[i]  · inv[i,w,t]
   + Σ stockout_penalty[i] · short[i,w,t]      (penalización >> holding, del business_cfg)
   + Σ transfer_cost        · transfer[i,a,b,t]
```

Restricciones adicionales:
- No-negatividad (bounds en todas las variables).
- Lead time: `order` colocada en `t` solo suma al inventario en `t + lead_time`.
- (Opcional, fase posterior) capacidad de bodega: `Σ_i inv[i,w,t] ≤ cap[w]`.

### Salida
`optimize(...)` devuelve, por SKU×bodega×bucket: cantidad a comprar, transferencias
sugeridas (origen→destino→cantidad), inventario proyectado y quiebres esperados, más un
resumen de costo. Solo lectura — no persiste nada.

### Escala / robustez
Para la escala SMB (decenas de SKUs, pocas bodegas, horizonte corto) el MILP es tratable.
Si el problema excede un umbral de tamaño (variables > N), se hace fallback a resolver
**por SKU independientemente** (cada SKU es un sub-problema separable en el objetivo), y si
aún así falla/timeout, se cae a la heurística ROP actual por bodega (nunca deja al usuario
sin recomendación).

---

## Sub-proyecto 6 — Superficie de compras/transferencias (Plan MW-3)

- Endpoint backend `POST /inventory/optimize?session_id=...` (o `GET`) que arma los inputs
  (stock por SKU×bodega, forecast, costos del `business_cfg`) y llama al optimizer, y
  devuelve las recomendaciones estructuradas. Requiere `require_analyst_or_above` para
  ejecutar (es cómputo pesado) o `get_current_user` si es solo lectura idempotente — a
  decidir en el plan MW-3.
- Frontend: una vista que muestra, por bodega, las órdenes de compra sugeridas y una
  sección de **transferencias recomendadas** ("mové 40 uds de SKU-A de Bodega Norte a
  Bodega Sur antes de comprar"), integrada con `/hoy`/`/pedidos`. i18n es/en.

---

## Pruebas (TESTING_GUIDELINES.md)

- **Esquema (MW-1):** tras la migración, upsert de un mismo SKU en dos bodegas distintas
  crea DOS filas (no colisiona); assert con query directa a `inventory_stock`. Import CSV
  con columna `bodega` persiste la bodega y auto-crea el registro en `warehouses`. Par de
  permisos en los endpoints mutantes.
- **Seed (MW-1):** el seeder es idempotente (correrlo dos veces no duplica), y produce
  datos coherentes (assert: toda fila tiene costo < precio_venta, stock ≥ 0, cada SKU
  tiene ventas en ≥1 bodega).
- **MILP (MW-2):** casos deterministas con óptimo conocido a mano — (a) una bodega con
  quiebre y otra con exceso del mismo SKU → el optimizer sugiere una transferencia, no una
  compra, cuando `transfer_cost < holding+stockout` evitado; (b) sin exceso en ninguna
  bodega → compra; (c) el balance de inventario se respeta bucket a bucket. Assert sobre
  los valores de decisión, no solo que el solver devolvió algo.
- **Fallback (MW-2):** problema forzado a fallar/timeout → cae a la heurística por bodega
  y devuelve recomendaciones no vacías.

## Riesgos / notas

- El cambio de constraint única en `inventory_stock` es la parte más delicada — toca
  `upsert_stock`, `bulk_import`, `_record_snapshot`, y cualquier query que asuma unicidad
  por `(tenant, sku)`. El Plan MW-1 debe auditar todos los call sites de `inventory_stock`.
- El forecast hoy es por SKU (o SKU único). Repartir demanda por bodega requiere una
  heurística (proporcional al histórico/stock por bodega) hasta que exista forecast por
  SKU×bodega — documentado como aproximación de primera fase.
- `scipy.optimize.milp` requiere aplanar el modelo a matrices `c/A_ub/b_ub/A_eq/b_eq/
  integrality/bounds`; el Plan MW-2 debe encapsular ese armado en un builder testeable por
  separado del solve, para poder verificar la matriz sin correr el solver.
- La escala real puede requerir el fallback por-SKU; el plan MW-2 lo construye desde el
  inicio, no como afterthought.

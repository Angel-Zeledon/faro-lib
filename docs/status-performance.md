# Rendimiento de `/inventory/status`

Autorizado por el dueño como trabajo de escalabilidad empresarial. Este documento
dice dónde se iba el tiempo, qué se cambió, cuándo se sirve (y cuándo nunca se
sirve) una foto guardada, y las mediciones antes y después.

## 1. Dónde se iba el tiempo

Medido con un tenant sintético (pronóstico de N SKUs sembrado con
`seed_completed_session`, filas en `inventory_stock`, cinco puntos de historia en
`inventory_snapshots` para la mitad de los SKUs, 40 proveedores) y `cProfile`
sobre una sola llamada a `GET /inventory/status?limit=50`.

A 500 SKUs, una llamada hacía 1.470 consultas y 6,9 s; el 78 % del tiempo era una
sola línea del bucle por SKU:

| Costo | Detalle | Parte |
|---|---|---|
| N+1 de historia | `get_stock_history(tenant, sku, 14)` por cada SKU con stock: 3 consultas por SKU (filas de la ventana, nivel de apertura por bodega, filas legadas) para pintar el sparkline | 1.440 de 1.470 consultas, ~5,3 s de 6,6 s |
| Cuadrático en el bucle | `sum(... for (i_sku, _wh), q in incoming_qty.items() if i_sku == sku)` e igual para `incoming_sources`: recorre todo el mapa de "en camino" por cada SKU | invisible con pocas OC abiertas, crece con N × (SKUs con OC) |
| Un commit por consulta | cada `query()` toma una conexión del pool y hace `commit` (1,5 s de 6,2 s a 500 SKUs) | se paga por consulta, así que desaparece con el N+1 |
| Lectura del blob | `session_results.forecasts` y `training_result` se leen y se parsean **una vez** por llamada (no por fila), pero a 2.500 SKUs son ~1,5 s de `json.loads` más 1,6 s de red | piso de cada recálculo |
| Aprendizaje de lead time, mapas de proveedor, reglas, eventos | una consulta por mapa para todo el tenant (ya estaban agrupadas); ~10 consultas en total | despreciable |
| Python puro por SKU | `resolve_field` ×4, `_calc_*`, `build_explanation`: ~0,7 ms por SKU | ~1,8 s a 2.500 SKUs |
| `recommendation_log` | una escritura por día y tenant | 0,8 s a 2.500 SKUs, una vez al día |

Conclusión: el problema no eran los cálculos sino los viajes a la base. Quitar el
N+1 (y el recorrido cuadrático) baja de 7.230 a 33 consultas a 2.500 SKUs. Lo
que queda es el piso del recálculo (parseo del pronóstico y Python por SKU), y
eso es lo que la foto guardada deja de pagar en cada solicitud.

## 2. Qué se cambió

1. `get_stock_history_batch` (`backend/inventory/service.py`): las mismas tres
   consultas para **todos** los SKUs a la vez, agrupadas por SKU, alimentando la
   misma `tenant_wide_daily_levels`. La serie de cada SKU es idéntica a la del
   lector uno a uno (lo verifica `test_history_batch_equals_the_one_at_a_time_reader`).
2. El mapa de "en camino" se indexa por SKU una vez antes del bucle
   (`incoming_by_sku`, `incoming_sources_by_sku`), con el mismo orden de
   iteración, así que las sumas en coma flotante y las listas de referencias
   salen igual.
3. **Foto persistida** (`backend/inventory/status_snapshot.py`, tablas
   `inventory_status_snapshot` y `inventory_status_snapshot_meta`): una fila por
   SKU con la fila completa en `item` (JSONB), más columnas para filtrar y
   ordenar (`signal`, `supplier_lc`, `search_text`, `has_stock`, `has_forecast`,
   `inventory_value`, `urgency_pos`, `sort_keys`) y `computed_at`. El endpoint
   resuelve filtros (`signal`, `supplier`, `skus`, `q`), resumen y paginación
   desde SQL; para los órdenes por columna usa la misma `sort_status_items` sobre
   las llaves de orden y trae las filas completas solo de la página.
4. La respuesta conserva su forma. Se agrega una clave aditiva, `computed_at`,
   en `data` (la foto la trae de la base; el cálculo en vivo pone la hora de la
   solicitud). Ninguna ruta cambió, así que no hay que regenerar
   `public-api.json`.

La vista `by_warehouse=true` no usa la foto: sigue calculándose en vivo.

## 3. Cuándo se sirve la foto (reglas de invalidación)

Una foto se sirve solo si **todas** estas condiciones se cumplen; ante cualquier
duda se recalcula, nunca se sirve de todos modos.

1. **Ninguna fila de una tabla que el cálculo lee cambió para ese tenant desde
   que se tomó la foto.** Triggers de sentencia (`AFTER INSERT/UPDATE/DELETE`,
   con tablas de transición) en las 14 tablas de `STATUS_INPUT_TABLES` agregan
   una fila a `status_input_bumps` por tenant afectado. La invalidación vive en
   la base de datos: no depende de que cada endpoint (presente o futuro) o un
   script SQL se acuerde de invalidar. Las tablas: `inventory_stock`,
   `warehouses`, `inventory_po_items`, `inventory_po_log`,
   `inventory_transfer_items`, `inventory_transfer_log`,
   `supplier_lead_time_obs`, `suppliers`, `sku_suppliers`, `stock_defaults`
   (incluye los multiplicadores del semáforo), `inventory_events`,
   `inventory_event_multipliers`, `inventory_snapshots` y `session_results`
   (pronóstico y resultado de entrenamiento). Un test lee el SQL de las funciones
   que alimentan el cálculo y falla si empiezan a leer una tabla que no está en
   la lista.
2. **Se calculó hoy** (el cálculo lee `date.today()`: eventos declarados,
   ventana del sparkline).
3. **Tiene menos de una hora** (`MAX_AGE`): la ventana de 14 días del sparkline
   se desliza con el reloj. Es el límite de cuánto puede diferir una foto
   servida de un cálculo hecho en ese instante cuando ninguna fila cambió.
4. **El código que define los números no cambió** (`code_hash`: digest de
   `service.py`, `stock_defaults_service.py`, `signal_thresholds.py`,
   `defaults.py`, `supplier_service.py`, `series.py`, `warehouse_service.py` y el
   propio módulo). Un despliegue invalida todo solo.
5. Es la foto del mismo `(session_id, period, service_level)`.

La versión de entradas se lee **antes** de calcular. Si una escritura confirma
mientras se calcula, la versión guardada queda más vieja que la viva y la
siguiente lectura recalcula. Lo contrario (versión más nueva que los datos) es
imposible porque el cambio y su `bump` confirman juntos. El ledger es
solo-inserción a propósito: un contador por tenant haría que todos los
escritores del tenant hicieran fila detrás de una fila bloqueada.

**Recálculo concurrente**: `pg_advisory_xact_lock(hashtext(tenant:session:period:nivel))`
(una llave distinta de `take_tenant_lock`, para que un recálculo nunca haga
esperar las escrituras del tenant). El segundo lector espera, ve la generación
fresca del primero y no recalcula (`test_concurrent_refreshes_compute_once`). El
recálculo escribe una generación nueva, cambia la meta y conserva la anterior
para los lectores que la eligieron un instante antes.

**Fallback seguro**: si construir o leer la foto lanza cualquier excepción,
`read_status` devuelve `None`, se registra y el endpoint responde con el cálculo
en vivo completo. La foto es un acelerador, no una segunda fuente de verdad.

**Granularidad**: el recálculo es por generación completa
`(tenant, sesión, periodo, nivel de servicio)`, no por fila. Se evaluó y se
descartó el refresco incremental por fila: la clase ABC es un ranking sobre
todas las filas, y casi todas las entradas (proveedores, reglas, eventos, el
pronóstico) son del tenant entero, así que habría que demostrar qué filas no
puede alcanzar una edición; el modo de falla es una fila vieja servida como
fresca, justo lo que este diseño evita. Tampoco hay refresco en segundo plano:
hoy el recálculo es síncrono y barato en comparación con lo anterior.

## 4. Mediciones

Mismo tenant sintético, misma máquina (Windows, Postgres en Docker, con otros
~10 agentes compartiendo la máquina, así que los segundos son ruidosos; las
consultas no). Una llamada `GET /inventory/status?session_id=...` con el cliente
de pruebas.

| Escenario | Antes | Después |
|---|---|---|
| 2.500 SKUs, primera llamada del día (recalcula y guarda) | 35,7 s, 7.230 consultas | 9,6 s (7,6 a 15,6 s según la carga), 46 consultas |
| 2.500 SKUs, página `limit=50` con foto fresca | 35,7 s, 7.230 consultas | 0,29 s, 17 consultas |
| 2.500 SKUs, página ordenada por columna (`sort=stock`) | 35,7 s | 0,48 s |
| 2.500 SKUs, filtro `signal` + `q` | 35,7 s | 0,26 s |
| 2.500 SKUs, sin paginar (2.500 filas completas) | 35,7 s | 3,7 s (el costo es serializar 2.500 filas) |
| 10.000 SKUs, primera llamada del día | 155 s, 28.830 consultas | 39 s, 46 consultas |
| 10.000 SKUs, página `limit=50` con foto fresca | 155 s | 0,59 s |
| 10.000 SKUs, página ordenada por columna | 155 s | 1,4 s |
| 10.000 SKUs, filtro `signal` + `q` | 155 s | 0,72 s |
| 10.000 SKUs, sin paginar | 155 s | 13,8 s |

Las 17 consultas de una lectura fresca son casi todas del andamiaje de la
solicitud (autenticación, plan, periodo, SKUs excluidos); la foto aporta cinco
(versión, meta, agregados, filas). El `tests/test_large_status_paging.py`
(2.500 SKUs, ~14 minutos antes) corre completo junto con otras 226 pruebas
relacionadas en 7 min 22 s.

El costo que queda es la primera llamada de cada hora o tras un cambio: parseo
del bloque de pronóstico (~1,5 s a 2.500 SKUs), Python por SKU (~0,7 ms) y la
escritura de las filas (~2 s a 2.500 SKUs). Si hiciera falta bajarlo más, el
siguiente paso sería extraer del JSONB solo los campos del pronóstico que se
usan, y un refresco en segundo plano disparado por la propia escritura.

## 5. No cubierto / pendiente

* La vista `by_warehouse` y los demás llamadores de `get_inventory_status`
  (briefing, PDF, alertas, asistente) siguen calculando en vivo; ya ganan el
  fin del N+1, pero no leen la foto. Pasarlos a la foto exigiría revisar los
  tests que parchean funciones internas del cálculo.
* El ledger `status_input_bumps` se poda en cada recálculo del tenant (filas de
  más de un día excepto la última); un tenant que escribe pero nunca consulta
  acumula filas hasta su próxima lectura.
* La vista lee `computed_at` pero el frontend todavía no lo muestra.

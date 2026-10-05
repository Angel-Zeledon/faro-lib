# Reentrenamiento y preparación para clientes grandes

*Investigación y diseño, 2026-10-04. Contrastado contra el código de `main` (commit `d7864c8`). No se tocó código de producto: todo lo que dice "candidato" es una propuesta para que el dueño elija, no trabajo hecho (regla de `CLAUDE.md`: estabilidad antes que alcance).*

Dos preguntas del dueño, dos partes:

1. **¿Cada cuánto se reentrena?** ¿Todos los días, cada vez que llega un día de ventas, o hay mejores formas? ¿Está bien la elección mensual/semanal?
2. **¿Qué hace falta para clientes grandes** con una base de datos por tienda y por bodega, y con decisiones óptimas **por bodega**, no solo por empresa?

---

# Parte 1 — Cadencia de reentrenamiento

## 1.1 Respuesta corta

- **No reentrenar todos los días, ni con cada día nuevo de ventas.** Un día más de historia casi nunca cambia un modelo; cambia el *origen* del pronóstico. Reentrenar (refit) y volver a pronosticar (re-forecast) son dos operaciones distintas, y hoy el producto solo tiene la cara.
- **Política recomendada por defecto:** refit **semanal** si llegan ventas a diario o semanalmente, **mensual** si el cliente sube un archivo al mes (el caso típico del distribuidor LatAm, y lo que ya asume `notifications/freshness_service.py`); más **disparadores por evento** (degradación de precisión, SKU nuevo, ruptura estructural) que adelantan el refit; y **nunca** reentrenar si no entró data nueva.
- **Lo que más importa no es la cadencia, es lo que el producto hoy no hace:** (a) no guarda los modelos entrenados, así que "re-pronosticar con modelos fijos" es imposible; (b) el reentrenamiento programado no mira si hay datos nuevos; (c) no hay ningún monitor de precisión conectado a un disparador. Detalle abajo.
- **Granularidad:** la lógica actual (grano más fino que el dato soporta, con mínimo de 20 buckets) es razonable como *default por empresa*, pero es **un solo grano para todos los SKU**. Lo correcto para un distribuidor es elegir por SKU según intermitencia y horizonte de decisión. Es un candidato, no un bug.

## 1.2 Qué hace el producto hoy (con evidencia)

### Cómo entra la data nueva y qué la dispara

| Pieza | Dónde | Qué hace |
|---|---|---|
| Entrenar | `api/v1/training.py` → `sessions/family_service.py::launch_training_family` | Un lanzamiento crea **una familia de sesiones**, una por grano (diario/semanal/mensual) con ≥ 20 buckets de historia (`MIN_BUCKETS_FOR_GRANULARITY = 20`, `plan_family`). Cada una se entrena con el mismo conjunto de modelos y se pre-pronostica a un alcance generoso (`GENEROUS_REACH = {daily: 90, weekly: 26, monthly: 12}`). Es decir, **un "reentrenamiento" son hasta 3 entrenamientos completos.** |
| Reentrenar por calendario | `workers/worker.py::_run_due_scheduled_jobs` → `sessions/retrain_service.py::launch_scheduled_retrain` | Tabla `scheduled_jobs` con cron (presets en `api/v1/schedule.py`: lunes 6am, medianoche, **cada hora**, domingo, laborables, día 1 del mes). Cada disparo crea una sesión **nueva** a partir de la plantilla, entrena, y la app cambia a ella solo si completa. Poda sus corridas previas (acota a 2 sesiones por programación). Diseño de 2026-09-16, bien hecho para no tumbar el producto. |
| Alertas diarias | `workers/worker.py::_inventory_alert_loop` (08:00 UTC) | Semáforo, desvío de lead time de proveedores y recordatorio de frescura. **No entrena nada.** |
| Frescura | `notifications/freshness_service.py` | Ventas: aviso a 14 d, recordatorio a 35 d, "ciego" a 45 d. Stock: aviso a 7 d, ciego a 21 d. Un semáforo `degraded` cuando cualquiera está ciego. El comentario del módulo ya dice "a distributor uploads once a month". |

### Lo que cambia sin reentrenar (y está bien)

El semáforo (`inventory/service.py::get_inventory_status_*`) se calcula **al pedir**, sobre el pronóstico guardado en la sesión más el stock actual de `inventory_stock`. Por eso una recepción de OC, una transferencia o un conteo de stock se reflejan al instante sin ningún entrenamiento. Esto es exactamente la separación correcta: el **estado** (stock, órdenes en tránsito) se refresca siempre; el **modelo de demanda** se refresca poco.

### Lo que falta, verificado

1. **No hay re-pronóstico con modelos fijos.** `ForecastEngine.predict()` (`engine.py:867`) sabe regenerar desde modelos en caché "sin reentrenar", pero el backend nunca persiste el motor ni los modelos: `runner.py` guarda solo `session_config.json` en `artifacts_dir` (línea ~1680) y los resultados JSONB (`session_results`). Cualquier refresco = entrenamiento completo. Consecuencia: el pronóstico guardado tiene fechas absolutas y **envejece**; a los 45 días sin archivo el producto lo declara ciego en vez de poder avanzar el origen.
2. **El reentrenamiento programado no pregunta si hay datos nuevos.** `launch_scheduled_retrain` copia `dataset_id` de la plantilla y entrena. No compara hash, `updated_at`, ni fecha máxima del archivo. Un preset "medianoche" sobre un archivo que nadie tocó reentrena lo mismo cada noche (cómputo desperdiciado y reemplazo de la sesión que sirve por un resultado equivalente).
3. **Una programación no recoge un SQL nuevo.** `datasources/service.py::materialize_sql_source` crea un dataset **nuevo** (`parent_id` = la fuente) en cada ejecución; la plantilla sigue apuntando al dataset viejo. Solo `POST /data-sources/{id}/file` (`replace_file_source`) reemplaza el contenido **en el mismo id** y por tanto lo ve el reentrenamiento programado. Para una fuente SQL, hoy "programar" no refresca la data (hallazgo a verificar con el dueño en un caso real, pero el código es inequívoco).
4. **El monitoreo existe a medias y desconectado.**
   - `forecasting_core/monitoring/drift.py` tiene PSI/KS y `DriftDetector.performance_decay(historical_maes, current_mae, threshold_pct=0.20)` que devuelve `retrain_recommended`. **Nada en `backend/` llama a `performance_decay`.**
   - `POST /sessions/{id}/drift` (`api/v1/forecasts.py:~801`) es manual y compara contra un dataset de referencia.
   - `accuracy_snapshots` solo se llena subiendo un CSV de reales a mano (`forecasts.py:~300`, endpoint de reconcile).
   - `training_run_metrics` (`training/metrics_history.py`) guarda WAPE/MAE por modelo en cada entrenamiento (historial entre corridas), pero mide el *backtest del entrenamiento*, no el error real contra lo vendido después.
   - Hay un `policy_backtest` por corrida (`runner.py`, `engine.get_policy_backtest()`): fill rate y quiebres simulados. Útil, también *in-sample*.
   - Resultado: **el producto no puede decir "tu pronóstico de hace 3 semanas erró 31%"**, que es el disparador de reentrenamiento más honesto que existe.
5. **No hay disparador por SKU nuevo ni por evento.** Un SKU que aparece en el archivo nuevo entra en el siguiente entrenamiento completo; hay un gate de historia mínima (`min_history`, `_compute_excluded_skus`) que lo deja fuera y lo informa, pero no hay "cuando junte N días, entrénalo". Los eventos/promos (`inventory_events`, multiplicadores) ajustan la decisión, no disparan nada.

### Costo por corrida en este producto

- **Multiplicador por familia:** hasta 3 sesiones (D/S/M), cada una con el set por defecto `global_lgbm, lightgbm, prophet, croston, xgboost` (`sessions/defaults.py`) y `wfv_splits: 8` de validación walk-forward. Prophet y el walk-forward dominan.
- **Cola:** hilos en proceso, `max_concurrent_jobs = 8` global y por tenant (`entitlements/plans.py`, `workers/worker.py::_tenant_at_concurrent_job_limit`). Una familia ocupa 3 de esos 8 cupos.
- **No hay un número de tiempo de pared medido en el repo.** `jobs.started_at` y `completed_at` ya existen; medir mediana y p95 por tamaño de catálogo es el primer paso barato antes de decidir cadencias (ver candidato C1).
- **Memoria:** todo el CSV se carga en pandas dentro del hilo del worker (`runner.py`). Para el catálogo de un cliente grande (sección 2) la memoria, no el tiempo, es el límite.
- **Costo de licencia:** reentrenar no consume la API de DeepSeek (la IA solo narra). El costo es CPU del servidor del cliente (producto auto-hospedado: `docs/data-that-leaves.md` dice "StockAI runs on your server"), así que una cadencia agresiva se paga en latencia de la app para ese cliente, no en facturas.

## 1.3 Marco: refit vs re-forecast, y los disparadores

Práctica estándar en pronóstico de demanda (referencias de método: Hyndman & Athanasopoulos, *Forecasting: Principles and Practice*; Syntetos–Boylan para intermitentes; Rob Hyndman sobre "rolling origin"):

| Operación | Qué hace | Costo | Cuándo |
|---|---|---|---|
| **Re-forecast** | Mismos modelos/parámetros, historia actualizada (más lags reales), avanza el origen | Segundos | Cada vez que llegan ventas (diario si hay feed). Para modelos de series de tiempo y recursivos es casi gratis. |
| **Refit** | Reestima pesos/parámetros y vuelve a validar | Minutos–horas por catálogo | Periódico o por disparador. Reestimar a diario añade ruido, no señal: un parámetro estimado con 18 meses de datos no cambia por 1 día más. |
| **Re-selección de modelos / hiperparámetros** | Re-corre la competencia entre modelos y el enrutador | El más caro | Mensual o trimestral, o ante ruptura. |

Disparadores, de más a menos recomendables para este producto:

1. **Por calendario ligado a la cadencia de datos** (semanal/mensual). Es la red de seguridad. Debe cancelarse si no hay datos nuevos (hoy no se cancela).
2. **Por volumen de datos nuevo**: refit cuando entraron ≥ N días nuevos (p. ej. ≥ 7 días o ≥ 5% de filas) desde el último ajuste. Evita reentrenar sin señal y evita no reentrenar un archivo que trae 6 meses de golpe.
3. **Por degradación de precisión** (el mejor disparador): comparar el pronóstico vigente contra lo realmente vendido (WAPE/sesgo móvil por SKU y agregado). Si el WAPE de las últimas k semanas supera ~20% sobre la línea base (el umbral que ya trae `performance_decay`) o el sesgo sostenido pasa de ±10%, adelantar el refit. Requiere cerrar el lazo `pronóstico guardado` → `ventas reales` automáticamente; hoy es CSV manual.
4. **Por SKU nuevo**: un SKU que cruza `min_history` entra a un refit pequeño (solo esos SKU, o el modelo global que aprende entre series: `global_lgbm` ya existe justo para esto).
5. **Por ruptura estructural / promoción**: cambio de nivel (CUSUM sobre el residuo), cambio de proveedor o de precio, lanzamiento de promo. Las promos conocidas no deberían disparar refit sino entrar como regresor/evento (el producto ya tiene `inventory_events` con multiplicadores para la decisión). Las rupturas no anunciadas sí: alerta + refit del SKU.
6. **Drift de features (PSI/KS)**: el más débil para demanda de distribuidor. Detecta cambios de distribución de variables, que no equivalen a pérdida de precisión; usarlo como señal informativa, no como gatillo.

## 1.4 ¿Es correcta la granularidad mensual/semanal?

**Qué hace hoy:** `plan_family` entrena todos los granos que el historial permite (≥ 20 buckets: ≈ 5 meses para semanal, **≈ 20 meses para mensual**). El grano activo es **uno por empresa** (`tenants.settings.planning`, `sessions/planning_service.py::_period_choice`), por defecto el más fino que soporta el dato (`natural_frequency`). El comentario de `_period_choice` explica por qué *no* se elige por menor error: agregar siempre baja el error, así que "el que mejor puntúe" daría el más grueso y un plan demasiado grueso para reordenar. Ese razonamiento es **correcto** y es lo que hay que conservar.

**Qué dice la práctica:** el grano debe elegirse por la **decisión**, no por el error del modelo:

- La demanda que importa para una orden es la acumulada sobre **lead time + período de revisión** (`L + R`). El producto ya lo sabe: `optimizer_service.effective_horizon_buckets` extiende el horizonte de cada SKU a `max(horizonte, lead_time + revisión)`.
- El grano del modelo debe ser **≤ el período de revisión** (no tiene sentido pronosticar por mes si se compra cada semana, ni por día si se compra cada mes) y lo bastante grueso para que la serie deje de ser intermitente.
- La intermitencia se mide con ADI (intervalo medio entre demandas) y CV² del tamaño de demanda (clasificación Syntetos–Boylan: ADI ≥ 1.32 = intermitente; CV² ≥ 0.49 = errática/lumpy). Un SKU "lumpy" diario suele volverse "smooth" semanal. El producto usa un criterio más tosco: `zero_ratio ≥ 0.4` (`ForecastingCore/forecasting_core/data/quality.py`), evaluado en el grano en que se entrena, y enruta a Croston/ETS.

**Aplicado a un distribuidor LatAm PYME** (lead times de 7–45 días con tramo de importación, revisión semanal o quincenal, historia de 1–3 años, muchas colas largas lentas):

| Situación del SKU | Grano recomendado | Por qué |
|---|---|---|
| Movedor rápido, ADI < 1.32, tienda con ventas diarias | **Semanal** como grano de decisión (diario solo si la revisión es diaria) | El error semanal es menor y la compra es semanal; el patrón día-de-semana se aprende como feature, no como grano |
| Intermitente (ADI ≥ 1.32), lead time ≥ 15 d | **Semanal o mensual** con Croston/SBA | A nivel diario la serie es casi toda ceros y ningún modelo la aprende |
| Lead time ≥ 30–45 d, importado, revisión mensual | **Mensual** si hay ≥ 24 meses; si no, semanal acumulado a `L+R` | El horizonte relevante es de meses |
| Historia < 20 meses | **Semanal** (el mensual ni se ofrece) | Correcto: no hay 20 buckets mensuales |

**Veredicto:** la elección actual no es incorrecta como valor por defecto y su regla de no elegir por error es la buena. Lo que le falta es **granularidad por SKU** (hoy es global por empresa) y **un criterio de intermitencia estándar (ADI/CV²) en vez de la proporción de ceros**. Para el SKU de movimiento rápido y lead time corto, un tenant puede estar planeando mensual cuando compra semanal, o diario sobre una serie escasa. También conviene que el producto muestre **por qué** eligió el grano por SKU, igual que ya lo hace a nivel empresa con `period_reason`.

## 1.5 Política por defecto recomendada

Para el default de un tenant nuevo, sin configuración:

| Qué | Política |
|---|---|
| **Refresco de estado** (stock, OC, transferencias) | Inmediato, sin modelos (ya es así) |
| **Re-forecast** (avanzar origen con ventas nuevas, modelos fijos) | Con cada carga de ventas. *Falta construirlo* (requiere persistir modelos) |
| **Refit programado** | Semanal si el feed de ventas es diario/semanal; mensual si el cliente sube un archivo mensual. **Se omite si no hay datos nuevos desde el último ajuste** |
| **Refit por disparador** | Antes del siguiente refit programado si: WAPE móvil > línea base +20%, o sesgo sostenido > ±10% en un SKU de clase A; SKU nuevo supera `min_history`; ruptura detectada |
| **Re-selección completa de modelos** | Mensual |
| **Grano** | Por SKU según ADI/CV² y `L+R`; por defecto semanal; mensual solo con ≥ 24 meses y `L ≥ 30 d` |
| **Nunca** | Reentrenar diario "por si acaso", ni cada hora (el preset "cada hora" de `schedule.py` solo sirve para pruebas) |

## 1.6 Lista de candidatos (no implementados)

Ordenados por valor/riesgo; todos son "decisión del dueño":

| # | Candidato | Tipo | Esfuerzo | Riesgo |
|---|---|---|---|---|
| C1 | Medir y mostrar duración de cada corrida (`jobs.started_at/completed_at` ya existen) y mediana por tamaño | Observabilidad, sin función nueva | S | Bajo |
| C2 | Que `launch_scheduled_retrain` **omita** si el dataset no cambió desde el último ajuste exitoso (hash o fecha máxima de ventas), y lo registre ("sin datos nuevos") en la bitácora, no en silencio | **Corrección** de comportamiento existente | S | Bajo |
| C3 | Que la programación sobre una **fuente SQL** re-materialice antes de entrenar (o que `materialize` reemplace en el mismo `dataset_id`) | **Corrección**: hoy programar no refresca SQL | M | Medio (snapshot reproducible vs. cambio de id) |
| C4 | Presets: quitar "cada hora", añadir "semanal si hay datos nuevos" como valor sugerido | Copy/config | S | Bajo |
| C5 | Lazo automático de precisión: al entrar ventas nuevas, llenar `accuracy_snapshots` contra el pronóstico vigente (hoy CSV manual) | Función nueva | M | Medio |
| C6 | Disparador por degradación: conectar `DriftDetector.performance_decay` a C5 y a un aviso/refit | Función nueva | M | Medio |
| C7 | Persistir modelos entrenados (artefactos en `storage/`) y endpoint/paso de **re-forecast** sin reentrenar | Función nueva, grande | L | Alto (versionado de artefactos, espacio, compatibilidad) |
| C8 | Grano por SKU con ADI/CV² y `L+R` | Función nueva | L | Medio-alto (cambia lo que lee todo `/inventario`) |
| C9 | Refit parcial: solo SKU nuevos/degradados | Función nueva | L | Alto |

**Orden sugerido:** C2 y C3 primero (son correcciones: hoy el cliente cree que se está refrescando y no necesariamente es así), luego C1 (sin él cualquier decisión de cadencia es a ciegas), luego C5+C6. C7/C8/C9 solo si un cliente real lo pide.

---

# Parte 2 — Preparación empresarial (por tienda y por bodega)

## 2.1 Qué existe hoy (inventario del código)

| Área | Qué hay | Dónde | Límite |
|---|---|---|---|
| **Fuentes SQL** | Conexión a PostgreSQL, MySQL, SQL Server (pyodbc); el backend acepta también `oracle` en `SQL_ENGINES`. Contraseña cifrada con Fernet. Consulta guardada, vista previa, **materializar** a un CSV snapshot (`parent_id` = fuente), tope `sql_materialize_max_rows = 500 000` (rechaza, no trunca) | `datasources/service.py`, `api/v1/datasources.py`, `config.py` | **Oracle figura en `SQL_ENGINES` y el mapa de drivers (`oracle+cx_oracle`) pero `requirements.txt` no instala ningún driver Oracle**; no hay AS400/DB2, SAP HANA, ni conexión de solo salida. El servidor debe poder **alcanzar** la BD del cliente. No hay lista de destinos permitidos (un `host` arbitrario es un vector SSRF si alguien aloja el producto) |
| **Subida de archivos** | CSV/XLSX/XLS/Parquet/JSON; `POST /data-sources/file` y `POST /{id}/file` (reemplazo en el mismo id) | `datasources/service.py` | Es siempre **snapshot completo**. Techo de tamaño por plan: free 25 MB, paid 2000 MB (`entitlements/plans.py`) |
| **Bodegas** | Tabla `warehouses` (`UNIQUE (tenant_id, name)`), creadas implícitamente al escribir stock; stock por `(tenant_id, sku, warehouse)` | `inventory/warehouse_service.py`, `db/migrations.py` | La **clave natural es el nombre** (con normalización de mayúsculas y caracteres invisibles: `resolve_canonical_name`); no hay un código externo estable |
| **Stock masivo** | `POST /inventory/bulk/preview` y `/bulk` (CSV/Excel con alias de columnas, coma decimal, columna `warehouse` o parámetro `warehouse`) | `api/v1/inventory.py`, `utils/stock_import.py` | Es un *upsert* por `(sku, warehouse)` (idempotente por clave), pero sin versión/`as_of`: la última escritura gana |
| **Stock desde ventas** | El runner copia el último valor por SKU de columnas de inventario del archivo de ventas a `inventory_stock` | `inventory/service.py::sync_stock_from_dataset` | **Siempre en la bodega `principal`** ("dataset rows never carry an explicit warehouse") |
| **Pronóstico por tienda** | Si el mapeo canónico incluye `store`, el motor pronostica por `sku│store` (`ForecastingCore/forecasting_core/data/canonical.py::series_key`); la vista por bodega casa tienda↔bodega por nombre sin distinguir mayúsculas | `inventory/series.py`, `inventory/service.py::get_inventory_status_by_warehouse` | Si no hay `store`, la demanda por bodega es **un reparto manual** `warehouses.demand_share` (0–100) del pronóstico por SKU |
| **Jerarquía** | `HierarchicalReconciler` (bottom-up / top-down) configurable por `hierarchy_levels`; `aggregation/rollup.py` suma SKU×tienda a SKU | `ForecastingCore/forecasting_core/hierarchy.py`, `engine.py:755` | Sin reconciliación óptima (MinT), sin "middle-out"; la vista por SKU suma tiendas (equivale a bottom-up) |
| **Censura por quiebre** | `recover_censored_demand` eleva los buckets con stock 0 si el usuario mapeó una columna de inventario | `forecasting_core/data/censoring.py`, `pipelines/pipeline.py:248` | Necesita inventario histórico por (SKU, tienda) en el archivo de ventas |
| **Devoluciones** | Política de negativos (`net`, etc.) | `runner.py::_apply_negative_policy` | Por (día, producto/tienda) |
| **Transferencias** | Ciclo enviar→recibir con parciales, cierre y cancelación; rutas `transfer_lanes` con lead time y costo; sugerencia de transferencia en vez de compra (`_network_transfer_pass`) | `inventory/transfer_service.py`, `transfer_lane_service.py` | Mueve stock; **no reescribe ventas**. Si el archivo de ventas del cliente incluye salidas por traslado como "ventas", se cuenta la demanda dos veces (ver 2.2) |
| **Lead time y proveedor** | Lead time por fila de stock (SKU×bodega) y por proveedor/SKU; aprendido por proveedor desde recepciones (`supplier_lead_time_obs`) | `inventory/reception_service.py::_effective_lead_time`, `supplier_health_service.py` | El aprendizaje es **por proveedor**, sin bodega destino; una OC sí lleva `destination_warehouse` |
| **API pública / llaves** | Llaves `sk_live_*` con alcance `read`/`write` llaman a ~211 operaciones; medición diaria; límite `max_api_calls_per_day` (500 en free) | `api/public_surface.py`, `api/v1/api_keys.py` | Llaves son **por empresa**, no por bodega |
| **MCP** | 5 herramientas de solo lectura | `backend/mcp/` | Solo lectura por diseño (no cambiar) |
| **Idempotencia** | Cabecera `Idempotency-Key` en la creación de OC (`/inventory/po`, `log-po`) con huella por contenido | `api/v1/inventory.py:~1441` | **No existe** para stock, ventas ni transferencias |
| **Permisos** | Roles admin/analyst/viewer más `user_permissions` | `db/migrations.py`, `users/service.py` | Por empresa; `user_permissions` es `(user_id, permission)` **sin alcance de bodega** |
| **Zona horaria, moneda** | Una por empresa (`api/v1/timezone.py`, `api/v1/currency.py`); moneda **solo etiqueta, no convierte** | idem | Un grupo con una tienda en México y otra en Colombia no se expresa |
| **Frescura** | Edad de ventas y de stock **de toda la empresa** | `notifications/freshness_service.py`, `api/v1/freshness.py` | No hay frescura **por bodega/fuente** |
| **Límites por plan** | `max_skus` cuenta **filas de stock** `(sku, bodega)` (`count_stock`); `max_locations` cuenta bodegas | `inventory/service.py::upsert_stock` | 100 SKU × 3 bodegas agota los 100 "SKU" del plan free: el techo es de pares, no de SKU (a decidir si es lo que se quiere) |
| **SSO** | Solo Google/Apple/Facebook, apagado por defecto (`auth/social/`) | | No hay SAML/OIDC corporativo |
| **Eliminación de integraciones ERP** | Alegra/Siigo removidos 2026-09-20: "Do not reintroduce an ERP connector without an account to verify it against" | `CLAUDE.md` | Vale para todo lo de abajo: **ningún conector sin una cuenta real contra la cual probarlo** |

**Dato estructural clave:** el pronóstico y el entrenamiento son **por empresa**. La bodega es una dimensión de **stock** y de **reparto de demanda**, no de entrenamiento. Si el archivo de ventas trae `store`, la bodega obtiene pronóstico propio; si no, hereda una fracción del pronóstico del SKU. Eso es lo que hay que reforzar, no reescribir.

## 2.2 Catálogo de dificultades del mundo real

Agrupadas, con cómo se ve hoy en el producto (**E** = ya cubierto, **P** = parcial, **F** = falta).

### A. Conectividad y fuentes

| Dificultad | Detalle | Hoy |
|---|---|---|
| BD heterogéneas | Oracle, SQL Server, MySQL, Postgres, SAP (HANA/B1), AS400/DB2, Informix, Firebird (POS locales), Access, Excel en carpetas compartidas | **P**: 3 motores funcionan; Oracle declarado sin driver; el resto, vía exportación a archivo |
| Red cerrada, sin entrada | La BD de la tienda está tras NAT/VPN y el servidor de StockAI no puede abrir una conexión hacia ella | **F**: el diseño actual es de *pull* desde el servidor |
| VPN / firewall / listas de IP | El TI del cliente exige IP fija de salida y puertos abiertos | **F**: no hay agente saliente |
| Agente local (on-prem) | Un proceso en la red del cliente que lee y **empuja** HTTPS saliente | **F**: la API pública permite empujar archivos, pero no hay agente |
| Carga incremental / CDC vs volcado total | Cambios desde un marcador (`updated_at`, binlog, LSN) en vez de re-exportar todo | **F**: solo snapshots completos (`materialize` y reemplazo de archivo). Tres años de ventas diarias de 5 000 SKU ya son ~5.5 M de filas, más tiendas: supera el tope de 500 000 de `materialize` |
| Esquemas distintos por tienda | Una tienda con POS A, otra con POS B; columnas y nombres distintos | **P**: mapeo canónico por sesión (un archivo, un mapeo); no hay un mapeo por fuente reutilizable |
| Excel manual en carpeta compartida | Plantillas editadas a mano, columnas que cambian de orden | **P**: alias tolerantes en `stock_import.py` y wizard de mapeo; falta detectar "cambió el esquema respecto al mes pasado" |

### B. Identidad y semántica de los datos

| Dificultad | Detalle | Hoy |
|---|---|---|
| Códigos de SKU distintos por tienda/ERP | El mismo producto es `A-100` en una tienda y `0100A` en otra; EAN/GTIN como puente | **F**: no hay tabla de equivalencias; el SKU es el texto del archivo |
| Códigos de bodega/tienda inestables | Se renombran, se duplican con mayúsculas o espacios distintos | **P**: `resolve_canonical_name` normaliza en la escritura; no hay código externo inmutable |
| Unidades de medida y empaques | Caja vs unidad vs docena | **P**: existe `unit_of_measure` como texto informativo, no como factor de conversión |
| Monedas | Costos en USD, ventas en moneda local, varias monedas por país | **F**: moneda por empresa y solo etiqueta |
| Zonas horarias | "El día" de venta cambia por país; cortes de turno | **P**: una zona por empresa |
| Calendarios fiscales y comerciales | Cierre fiscal no calendario, semanas 4-4-5, feriados por país | **P**: catálogo de calendarios (`inventory/calendar_catalog.py`, claves tipo `co_quincena_15`); no hay calendario por bodega |
| Devoluciones y ventas negativas | Notas de crédito en otro período | **E/P**: `_apply_negative_policy` (neto/ignorar/…) |
| Datos tardíos y corregidos | La tienda sube las ventas de la semana pasada con ajustes | **F**: el reemplazo de archivo es total; no hay "versión de un dato" |

### C. Demanda y decisión por bodega

| Dificultad | Detalle | Hoy |
|---|---|---|
| Quiebre censura la demanda **por bodega** | Una bodega sin stock vende 0; ese cero no es demanda | **P**: censura existe; requiere inventario histórico por (SKU, tienda) dentro del archivo de ventas |
| Transferencias entre bodegas no son demanda | La salida de A y la entrada en B no deben sumarse como ventas | **P**: las transferencias del producto no tocan ventas; **riesgo en el dato del cliente** si el ERP las clasifica como venta |
| Series por bodega muy escasas | SKU×tienda es casi todo ceros aunque el SKU consolidado sea denso | **P**: `global_lgbm` aprende entre series, pero no hay estrategia explícita |
| Jerarquía SKU×bodega → SKU → empresa y conciliación | La suma de las bodegas debe igualar el total comprado al proveedor | **P**: bottom-up por suma; sin reconciliación estadística; el reparto manual `demand_share` es estático |
| Lead times por bodega y por ruta | El mismo proveedor llega a Norte en 5 días y a Sur en 12 | **P**: lead time por fila de stock y por ruta de transferencia; **el aprendizaje es por proveedor**, no por proveedor×bodega |
| Proveedores distintos por bodega | Norte compra a A, Sur compra a B | **P**: `sku_suppliers` es por SKU de la empresa, sin bodega |
| Compra centralizada vs local | Una OC consolidada que se reparte en bodegas | **E**: la línea de OC trae su `warehouse` y `destination_warehouse` |

### D. Gobierno y operación

| Dificultad | Detalle | Hoy |
|---|---|---|
| Permisos por ubicación | El jefe de Norte ve y edita solo Norte | **F**: permisos por empresa |
| Residencia de datos / cumplimiento | Ley de datos personales (CR, MX, CO, BR/LGPD), contratos que prohíben salir del país | **E por diseño**: auto-alojado; la lista de lo que sale está en `docs/data-that-leaves.md` (IA, correo, WhatsApp, todos apagados por defecto) |
| Auditoría | Quién cambió qué y cuándo | **P**: bitácora de actividad (`activity/events.py`); sin vista por bodega |
| Límites de tasa | Un agente que empuja 5 000 filas por minuto | **P**: medición diaria y límite por minuto de llaves (`RATE_MAX_PER_MINUTE`); sin cuota por fuente |
| Idempotencia y reintentos | Un agente reintenta tras un corte y duplica | **F** para stock/ventas/transferencias (existe solo en OC) |
| Backfills | Cargar 3 años históricos de una bodega nueva | **P**: archivo grande dentro de los topes; sin carga por lotes con progreso |
| SSO corporativo | Entra con Azure AD/Okta | **F** |
| Saber qué tan fresca está **cada** bodega | Una bodega lleva 12 días sin reportar y el semáforo de la empresa luce "actual" | **F**: frescura global (`api/v1/freshness.py`) |

## 2.3 Arquitectura recomendada

### Principio rector

**Separar tres cosas que hoy viajan juntas en un archivo:** *conectar* (traer bytes desde sistemas ajenos), *mapear* (traducir a lo canónico: SKU, bodega, unidad, moneda, zona horaria) y *decidir* (pronóstico y semáforo por bodega). Hoy el wizard hace las tres en una pantalla y por sesión. Para un cliente con 8 tiendas hay que poder hacer las dos primeras **una vez, por fuente**, y repetir sin humano.

### Lo que NO se recomienda (y por qué)

- **No un conector por ERP** (SAP, Oracle EBS, Dynamics, Siigo...) escrito sin cuenta de prueba. Es exactamente lo que el dueño ya retiró el 2026-09-20.
- **No entrenar un modelo por bodega como regla general.** Multiplica el costo por N y las series por bodega son escasas. Se entrena por empresa y se **descompone** por bodega (abajo).
- **No pedir a StockAI que abra conexiones entrantes a la red del cliente** como camino único: en redes cerradas es inviable.
- **No MCP con escritura** (`CLAUDE.md` lo prohíbe).

### Modelo de ingesta

**Una "fuente" por sistema/ubicación**, no por archivo:

```
source (id, tenant, kind: file|sql|push, warehouse_scope[], schema_mapping_id, cursor, status)
  └─ sync_run (id, source, started_at, finished_at, rows_in, rows_new, rows_changed, rows_rejected, error, as_of)
```

Hoy `datasets` hace de fuente y de snapshot a la vez (`source_type`, `parent_id`). Candidato: una tabla de *corridas de sincronización* con contadores y error, para que "esta bodega lleva 12 días sin reportar" sea un dato, no una deducción.

**Tres modos de ingesta, por orden de preferencia:**

1. **Empuje por API (push)** — el cliente (o un agente suyo) llama a la API pública con una llave `write`. Funciona en redes cerradas porque solo requiere **salida HTTPS**. Es el modo por defecto para clientes grandes. Requiere idempotencia (abajo).
2. **Agente ligero on-prem** (opcional, más adelante) — un proceso pequeño (Python/Go, un ejecutable) que corre la consulta del cliente sobre su BD y **empuja** el resultado por el modo 1. Pasa por la misma API; no necesita código especial en el servidor. Es donde caben Oracle, AS400/DB2, SAP, Firebird: un solo agente con drivers, no N conectores en el servidor.
3. **Pull SQL desde el servidor** (lo que ya existe) — para clientes con BD alcanzable (VPN site-to-site o instalación dentro de su red). Mantener, pero con la mejora de consulta incremental.

### Claves de upsert idempotentes

Para que un reintento o un reenvío no duplique:

| Entidad | Clave natural recomendada |
|---|---|
| Stock | `(tenant, sku, warehouse)` + `as_of` (momento del conteo); aplicar solo si `as_of` ≥ el último. Hoy: último en llegar gana |
| Ventas (hechos) | `(tenant, source, sku, store/warehouse, date)` con valor **reemplazable por ventana** (re-enviar un rango de fechas reemplaza ese rango). Mejor que "agregar filas" |
| Transferencias | `(tenant, source, external_transfer_id)` |
| Recepciones | `(tenant, po_log_id, line)` + `Idempotency-Key` (ya existe el patrón en OC) |

Regla: **reemplazo por ventana** para ventas (re-enviar la semana corrige la semana) y `Idempotency-Key` + huella de contenido, reutilizando el patrón ya probado en `api/v1/inventory.py` para OC.

### Capa de mapeo (una vez por fuente)

Tabla de equivalencias reutilizable, mantenida en la UI, con auditoría:

- **SKU**: `(source, external_sku) → canonical_sku` con EAN/GTIN opcional. Los no mapeados **no se descartan**: quedan en una bandeja "sin mapear" y el semáforo los marca (misma filosofía de `skus_missing_stock` en `optimizer_service`).
- **Bodega/tienda**: `(source, external_location_code) → warehouse` con código externo inmutable (el nombre pasa a ser etiqueta).
- **Unidad**: factor de conversión por SKU y fuente (caja = 12).
- **Moneda y zona horaria**: por fuente (hoy son por empresa); conversión **explícita** con tipo de cambio de la fecha, nunca adivinada (coherente con `currency.py`: "never guesses an exchange rate").
- **Tipo de movimiento**: qué códigos del ERP son venta, devolución, traslado, merma. **Esto evita la doble contabilización de transferencias.**

### Estado de sincronización y frescura por bodega (en la UI)

Tabla `Fuentes y bodegas` con una fila por (fuente, bodega): último dato recibido, filas nuevas/rechazadas, estado (`al día` / `atrasada` / `sin datos` / `error`) y el motivo. Extiende lo que `freshness_service` ya sabe hacer (dos relojes, umbrales) **por bodega** en lugar de global, y alimenta al semáforo: una bodega atrasada pasa a "desactualizado" y las demás siguen con su color. Las cadenas de texto van por código + parámetros y las traduce `translations.ts`, como en el resto del producto.

### Decisión por bodega: enfoque jerárquico

Entrenar **una vez por empresa**, descomponer por bodega según la densidad de la serie:

| Serie | Cómo se obtiene la demanda de la bodega |
|---|---|
| Rápida/densa en la bodega | **Directa** SKU×bodega (NO existe: con `store` mapeado y varias tiendas por SKU, el runner suma las tiendas y pronostica el total del SKU — `PREP_STORES_SUMMED`) |
| Lenta/intermitente en la bodega | **Top-down**: pronóstico del SKU (denso) × participación reciente de la bodega (promedio móvil de las últimas N semanas), en lugar del `demand_share` manual y estático |
| SKU recién llegado a una bodega | Participación del grupo de bodegas similares o del SKU global hasta tener historia |

Y **conciliación**: la suma por bodega debe igualar el total del SKU. Con top-down es exacto por construcción; con directo hay que reconciliar (el `HierarchicalReconciler` ya tiene bottom-up y top-down; MinT es excesivo para PYME y se difiere).

Esto es un cambio de *método* sobre piezas que ya existen (`for_store`, `demand_share`, `HierarchicalReconciler`), no un módulo nuevo.

**Decisión de compra por bodega:** el motor por bodega ya existe (`get_inventory_status_by_warehouse` + `_network_transfer_pass` que prefiere transferir antes que comprar). Las piezas que faltarían: lead time aprendido **por proveedor×bodega**, proveedores por bodega, y el reparto de una OC consolidada por bodega destino con la demanda por bodega como regla (hoy lo decide el usuario línea a línea).

### Permisos y auditoría por bodega

`user_permissions` (`UNIQUE (user_id, permission)`) pasa a admitir un alcance (`warehouse` nulo = toda la empresa). Toda lectura y escritura de `inventory/*` filtra por el alcance. Una llave `sk_live_*` también puede llevar alcance de bodegas (un agente por tienda no debería poder escribir en otra). El riesgo está en que **cada ruta** de `inventory.py` (≈ 50 operaciones) debe respetarlo; el sitio de pruebas de "viewer denegado + analista permitido" que exige `CLAUDE.md` se extiende con "bodega ajena denegada".

## 2.4 Ruta por fases desde lo que existe

Esfuerzo: S ≈ días, M ≈ 1–3 semanas, L ≈ más de 3 semanas. Cada fase se entrega y se verifica en navegador con un cliente piloto antes de abrir la siguiente.

| Fase | Contenido | Esfuerzo | Riesgo | Qué aporta |
|---|---|---|---|---|
| **0 — Receta con lo existente** (sin código) | Guía para el cliente: consulta SQL guardada con `GROUP BY sku, tienda, fecha` y ventana móvil de 3 años (cabe bajo 500 000 filas), columna `store` mapeada a los nombres de las bodegas, columna de inventario para activar censura, excluir traslados en el `WHERE`, empuje nocturno por `POST /data-sources/{id}/file` con llave `write`, programación semanal | S (documento) | Bajo | Un piloto real hoy, sin construir nada |
| **1 — Correcciones de lo existente** | C2, C3 (Parte 1); driver y prueba real de Oracle o retirarlo del selector; `sync_stock_from_dataset` por bodega cuando el archivo trae columna de bodega; decidir si `max_skus` cuenta pares | S–M | Bajo | Elimina falsas promesas (SQL que no refresca, Oracle que no conecta, stock siempre en `principal`) |
| **2 — Ingesta confiable** | Tabla `sync_run`; idempotencia por ventana para ventas y `Idempotency-Key` para stock/transferencias; frescura por bodega en `/data-freshness` y UI | M | Medio | "Esta bodega no reporta" visible; reintentos seguros |
| **3 — Mapeo reutilizable** | Equivalencias de SKU y de bodega (código externo), bandeja de no mapeados, factor de unidad, tipo de movimiento, moneda/zona por fuente | M–L | Medio-alto (toca el flujo de carga) | Es lo que hace repetible un segundo y tercer cliente sin trabajo manual |
| **4 — Demanda por bodega mejorada** | Participación móvil en lugar de `demand_share` manual; elección directo/top-down por densidad; lead time aprendido por proveedor×bodega | M | Medio | Mejor decisión por bodega con series escasas |
| **5 — Permisos y llaves por bodega** | Alcance en `user_permissions` y en llaves; filtro en todas las rutas de inventario; pruebas de bodega ajena | L | Alto (superficie amplia) | Gobierno para clientes grandes |
| **6 — Agente on-prem** | Ejecutable que corre la consulta y empuja por la API pública | L | Alto (distribución, soporte, drivers) | Redes cerradas y motores exóticos |

## 2.5 Qué diferir

- **Conectores nativos por ERP** (SAP, Dynamics, Siigo, etc.): regla del 2026-09-20; solo con cuenta real de verificación.
- **CDC verdadero** (binlog/LSN): el reemplazo por ventana cubre el 90% con una fracción del costo.
- **SSO SAML/OIDC**: hasta que un cliente concreto lo pida con contrato.
- **Reconciliación MinT, pronóstico probabilístico jerárquico**: refinamiento sin demanda comprobada.
- **Multimoneda con conversión automática**: el producto decidió no adivinar tipos de cambio.
- **Un modelo entrenado por bodega**: costo × N y series escasas; no mejora el resultado para PYME.
- **Fase 5 y 6** hasta que haya un cliente que las necesite; son las más caras y las que más soporte generan.

## 2.6 Preguntas para el dueño

Las respuestas fijan el alcance; sin ellas cualquier fase de arriba es especulación.

1. **¿Cuál es el cliente "grande" concreto?** ¿Cuántas tiendas y bodegas, cuántos SKU, y cuántos años de historia? (define si el tope de 500 000 filas y 2 GB alcanzan, y si importa el rendimiento.)
2. **¿Qué sistemas y qué motores usan** (Oracle, SQL Server, SAP, AS400, Excel)? Sin una cuenta o base de prueba por cada motor, no se construye conector.
3. **¿La red del cliente permite conexión entrante desde nuestro servidor**, o solo salida? Eso decide entre pull SQL, push por API y agente.
4. **¿StockAI correría dentro de la red del cliente (instalación propia) o en nuestro alojamiento?** Cambia radicalmente las fases 3 y 6 y la residencia de datos.
5. **¿Qué es una "bodega" para ellos?** ¿Una tienda, un CEDI, una bodega dentro de una tienda? ¿Las ventas se registran por tienda y el stock por bodega, o es lo mismo?
6. **¿Quién compra?** ¿Una persona central compra para todas, o cada bodega compra a sus propios proveedores? (decide permisos por bodega y proveedores por bodega.)
7. **¿Las transferencias entre bodegas aparecen como ventas en su sistema?** Si sí, hay que filtrarlas en el origen antes que cualquier cosa.
8. **¿Hay varios países/monedas/zonas horarias en un mismo grupo?** Si no, se difiere toda la capa de moneda y zona por fuente.
9. **¿Cada cuánto llegan las ventas** (diario, semanal, mensual) **y cuánto retraso tienen?** Fija la cadencia de refit de la Parte 1 y los umbrales de frescura por bodega.
10. **¿Quién mapea los códigos de SKU** entre sistemas y quién es dueño de esa tabla? (si es un trabajo recurrente de un humano del cliente, la fase 3 es obligatoria; si es una vez, basta la receta de fase 0.)
11. **¿Se quiere que `max_skus` del plan free cuente pares SKU×bodega** (como hoy) o SKU únicos? Un cliente con 3 bodegas llega al techo 3 veces antes.
12. **¿SSO, auditoría formal o contrato de cumplimiento** son condición de venta, o solo deseables?

---

## Resumen de lo verificado frente a lo supuesto

**Verificado en el código:** la familia D/S/M por entrenamiento; la ausencia de persistencia de modelos y de re-pronóstico; el reentrenamiento programado sin chequeo de datos nuevos; `materialize_sql_source` creando un dataset nuevo en cada ejecución mientras la plantilla apunta al viejo; `performance_decay` sin ningún llamador en `backend/`; Oracle declarado sin driver instalado; `sync_stock_from_dataset` escribiendo siempre en `principal`; permisos y llaves sin alcance de bodega; frescura global; `max_skus` contando pares.

**No medido (hay que medirlo antes de fijar cadencias):** el tiempo real de una corrida completa en un catálogo de 1 000 y 5 000 SKU, y la memoria pico del hilo del worker. Las cifras de ingesta (~5.5 M de filas) son aritmética, no prueba.

**No hecho:** ninguna ejecución de la app en navegador ni de la suite: este documento es lectura de código, y por la regla de `CLAUDE.md` ("testear es usar la app") cada afirmación sobre comportamiento de pantalla debe confirmarse con un piloto antes de convertirse en trabajo.

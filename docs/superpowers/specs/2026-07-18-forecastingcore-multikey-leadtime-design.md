# Diseño — ForecastingCore: forecast multi-key (sku×bodega) + varianza de lead time

**Fecha:** 2026-07-18
**Alcance:** dos mejoras independientes y en paralelo, ambas dentro de
`ForecastingCore/forecasting_core/` únicamente. `ForecastingCore` se trata como una
**librería externa versionada** consumida por `backend/`: todo cambio debe ser
estrictamente aditivo (nuevos parámetros opcionales con default = comportamiento
actual exacto), nunca un cambio de firma/forma que rompa un call site existente.

---

## Contexto

`FeatureEngineer` y `Trainer` ya soportan `group_cols` multi-clave (p.ej.
`["sku", "store"]` / `["sku", "bodega"]`) desde el trabajo de canonical-schema
(`series_key`/`parse_series_key` en `data/canonical.py`, tests en
`test_trainer_multigroup.py`, `test_feature_engineer_group_cols.py`). Multi-bodega
(`feat/multi-warehouse`, ya mergeado) depende de esto para tener forecasts
correctos por (sku, bodega), no solo por sku.

Sin embargo, `pipelines/pipeline.py` y `inference/predictor.py` tienen un helper
`_primary_group(c)` (devuelve solo `group_keys[0]`) usado en ~15 sitios. Para los
modelos ML (LightGBM/XGBoost) esto no importa — `Trainer` ya agrupa internamente por
la lista completa. Pero quedan tres puntos donde el uso de `_primary_group` en vez
de `group_keys` completo produce resultados incorrectos (no solo subóptimos) en
cuanto un tenant configura una segunda clave:

1. **`_maybe_resample` (Estrategia B)** — ya documentado en un comentario existente
   (`pipeline.py:171-176`): `resample_to_frequency` suma demanda cruzando la
   segunda clave (p.ej. sku×bodega → solo sku), mezclando bodegas.
2. **Loops de modelos estadísticos** (ARIMA/Prophet/ETS/Croston/LSTM/SARIMAX) —
   agrupan por `_primary_group(c)` únicamente. Un SKU presente en 2 bodegas con
   patrones de demanda distintos (una intermitente, otra no) obtiene una sola
   serie mezclada en vez de una serie por bodega.
3. **`predictor.py::predict_all_skus`** — filtra `raw_df` por `_primary_group(c)`
   tanto para el buffer de historia del forecast recursivo ML como para las fechas
   futuras del forecast estadístico. Con multi-clave, el buffer de historia para
   un modelo entrenado en (sku, bodega) se construye con filas de TODAS las
   bodegas de ese sku — el forecast recursivo "ve" una historia que nunca
   entrenó. Además las filas de forecast estadístico nunca llevan columna
   `store`, a diferencia de las filas ML (que sí la tienen vía `Trainer`).

Ningún test actual ejercita un `Pipeline.run()` completo con `group_keys` de 2
elementos hasta forecast_df — los tests existentes (`test_pipeline_group_keys.py`,
`test_trainer_multigroup.py`) verifican piezas sueltas (wiring, rollups sobre un
`forecast_df` construido a mano), no el pipeline end-to-end. Por eso el bug no
está cubierto por regresión hoy.

---

## Tarea A — Forecast respeta `group_keys` completo, no solo la primera clave

### A.1 `resample_to_frequency` (`data/resampler.py`)
- `group_col: Optional[str]` → `group_col: Optional[str | List[str]]`. Cuando es
  lista, `groupby(group_col)` (pandas ya soporta lista directo); columnas de
  salida = `[date_col, *group_col, target_col]`. Cuando es `str` o `None`,
  comportamiento **byte-idéntico** al actual (mismo branch de código).
- `Pipeline._maybe_resample`: pasar `group_cols` (la lista ya resuelta a columnas
  presentes) en vez de `_primary_group(c)`. Elimina el comentario "NOTE: only the
  PRIMARY group key..." porque deja de ser cierto.

### A.2 Modelos estadísticos (`pipelines/pipeline.py`)
- Cuando `len(group_cols) == 2`: antes de cada loop de stat model, construir una
  columna compuesta `_series_key` en el subset (`series_key(row[sku_col],
  row[store_col])`, mismo helper que ya usa `Trainer`), pasar el nombre de esa
  columna como `group` a `run_arima_core`/`run_prophet_core`/`run_ets_core`/
  `run_croston_core`/`run_lstm_core`/`run_sarimax_core` (su parámetro `group` ya
  es genérico — cualquier nombre de columna sirve, no requiere tocar esos
  archivos). Los resultados quedan en `results_stat[model_name]` keyeados por la
  clave compuesta (mismo formato `"sku│store"` que usa `Trainer`).
- Cuando `len(group_cols) <= 1`: comportamiento actual sin cambios (branch
  separado, no una generalización que toque el caso existente).
- El conteo diagnóstico `n_valid = df[_primary_group(c)].nunique()` (usado solo
  para logging/metadata, no afecta resultados) se deja igual — cosmético, fuera
  de alcance.

### A.3 `predictor.py::predict_all_skus`
- Nueva función interna `_filter_series(raw_df, group_cols, entry_sku, entry_store)`:
  si `group_cols` tiene 1 elemento (o `entry_store` es `None`), filtra solo por
  sku (idéntico a hoy). Si tiene 2, filtra por sku **y** store.
- Sección ML: usar `_filter_series` en vez del filtro actual por
  `_primary_group(c)` para construir el buffer de historia — así el forecast
  recursivo de un modelo (sku, bodega) usa solo la historia de esa bodega.
- Sección estadística: si la clave en `stat_forecasts[model_name]` contiene el
  separador de `series_key` (viene de A.2), `parse_series_key` la separa en
  (sku, store); usar `_filter_series` igual que en ML; escribir `store` en cada
  punto del resultado igual que ya hace la sección ML (hoy sección estadística no
  emite `store` — filas ML sí). Si la clave NO contiene separador (caso de
  siempre, single-key), comportamiento actual sin cambios.

### Compatibilidad
- `group_keys` de 0 o 1 elemento (100% de la configuración actual en producción)
  no activa ningún branch nuevo — incluso el `import` de `series_key`/
  `parse_series_key` solo se usa dentro del `if len(group_cols) == 2:`.
- Ningún cambio de firma pública: `resample_to_frequency` acepta lo que ya
  aceptaba más un tipo adicional; `predict_all_skus`/`Pipeline.run()` mantienen su
  firma exacta.

### Tests (nuevos, `Pipeline.run()` end-to-end, no solo wiring)
- Dataset sintético: 1 sku en 2 bodegas con demanda claramente distinta (una casi
  siempre cero → intermitente → Croston; otra estable → ARIMA/ETS) usando
  `group_keys=["sku","bodega"]`.
  - Estrategia B activa: el bucket resampleado de cada bodega NO es la suma de
    ambas (assert valores por separado).
  - `forecast_df` tiene filas para ambas bodegas del mismo sku, con `store`
    poblado en TODAS las filas (ML y estadísticas), y los valores de forecast
    de cada bodega son distintos entre sí (no la misma serie duplicada).
  - Regresión: mismo dataset con `group_keys=["sku"]` (bodega colapsada) sigue
    dando el resultado de HEAD (snapshot antes del cambio) — pin explícito.
- `test_resampler.py`: caso nuevo con `group_col=["sku","bodega"]` — cada
  (sku,bodega) resamplea independiente; caso `group_col` string sigue igual
  (test existente, no tocar).
- Suite completa `ForecastingCore/tests/` en verde antes y después (regresión).

---

## Tarea B — Varianza de lead time en `InventoryAdvisor`

### Problema
`business/inventory.py::InventoryAdvisor.recommend()` calcula:
```
safety_stock = z * demand_std * sqrt(lead_time_days)
```
— asume lead time determinístico (varianza cero). `backend` ya observa lead times
reales por proveedor (`supplier_lead_time_obs`, poblada en
`reception_service.py::receive_po`) y expone `lead_time_real_min/max/avg` y un
`lead_time_std` declarado en el schema de `suppliers` (`backend/api/v1/inventory.py`),
pero nada de esa variabilidad llega a la fórmula — un proveedor errático y uno
puntual generan el mismo safety stock si su lead time promedio coincide.

### Cambio (aditivo)
- `InventoryAdvisor.__init__`: nuevo parámetro `lead_time_std: float = 0.0`.
- Fórmula combinada (Silver/Pyke/Peterson, estándar en teoría de inventarios),
  activa siempre pero **matemáticamente idéntica** a la actual cuando
  `lead_time_std == 0`:
  ```
  safety_stock = z * sqrt(lead_time_days * demand_std**2
                           + daily_demand**2 * lead_time_std**2)
  ```
  (con `lead_time_std=0` el segundo término se anula → queda
  `z * sqrt(lead_time_days) * demand_std`, la fórmula actual exacta).
- `stockout_risk`: el término `lead_demand_std` usa la misma varianza combinada
  (`sqrt(lead_time_days * demand_std**2 + daily_demand**2 * lead_time_std**2)`)
  en vez de solo `demand_std * sqrt(lead_time_days)`.
- `details` gana la clave `"lead_time_std"` (para trazabilidad, igual que ya
  existen `"lead_time_days"` / `"demand_std"`).
- `config/config.py::BusinessConfig`: nuevo campo `lead_time_std: float = 0.0`.
- `pipelines/pipeline.py::Pipeline._inventory`: pasa `b.lead_time_std` al
  construir `InventoryAdvisor` (hoy solo pasa 4 de los args ya existentes).

### Fuera de alcance (explícito)
Wire-up de `backend` para poblar `business_cfg.lead_time_std` desde
`get_supplier_scorecard()` (`lead_time_real_avg` de std observado, o el
`lead_time_std` declarado en `suppliers`) es trabajo de `backend/`, no de esta
tarea — ForecastingCore solo necesita aceptar y usar el parámetro correctamente
una vez provisto. Se deja anotado como siguiente paso natural, no se implementa
aquí.

### Tests (`ForecastingCore/tests/test_inventory.py`)
- `lead_time_std=0.0` (default) da el mismo `safety_stock`/`reorder_point`/
  `stockout_risk` que antes del cambio, para los mismos inputs que ya cubren los
  tests existentes — pin de regresión explícito, no solo "no crashea".
- Dos advisors con mismo `demand`/`lead_time_days` pero `lead_time_std` distinto
  (p.ej. 0 vs 5) producen `safety_stock` estrictamente mayor para el de mayor
  varianza — assert de valores, no solo "cambió".
- `details["lead_time_std"]` refleja el valor configurado.

---

## Riesgos / notas

- **Tarea A es el cambio de mayor superficie** (toca `pipeline.py`,
  `predictor.py`, `resampler.py`) — el branch `len(group_cols) <= 1` debe quedar
  textualmente idéntico al código actual (no una generalización de una sola
  rama), precisamente para no arriesgar el 100% de la configuración en
  producción hoy (single-key). Si el test de regresión con `group_keys=["sku"]`
  no es bit-idéntico al snapshot pre-cambio, es un blocker — no se ajusta el
  test, se corrige la implementación.
- **Tarea B es aislada y de bajo riesgo** — un parámetro nuevo con default que
  colapsa matemáticamente a la fórmula actual; el mayor cuidado es no olvidar
  ningún call site de `InventoryAdvisor(...)` posicional (hoy `Pipeline._inventory`
  la instancia con args posicionales `b.service_level, b.lead_time_days,
  b.holding_cost_pct, b.stockout_cost_multiplier` — el nuevo param debe ir
  **después**, como keyword, para no desplazar posicionales existentes en
  ningún otro call site).
- Ambas tareas son independientes entre sí y pueden implementarse/testearse en
  cualquier orden o en paralelo.

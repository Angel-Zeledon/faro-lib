# Diseño — Data Alignment Wizard (detección de granularidad, conciliación y horizonte flexible)

**Fecha:** 2026-07-12
**Alcance de esta ronda:** Sub-proyectos 1-3 de la decomposición acordada — detección de
granularidad heterogénea por SKU (backend), modal de conciliación no invasivo (frontend),
motor de resampling (ForecastingCore) y configuración flexible del horizonte.
**Fuera de alcance (sub-proyectos 4-6, fases posteriores):** dimensión de bodega en el
modelo de datos, motor de optimización MILP multi-bodega, y la traducción de su output a
PO/transferencias accionables. Esta ronda deja el contrato de datos (frecuencia por SKU,
horizonte por frecuencia) listo para que esos sub-proyectos lo consuman después, pero no
construye el solver ni el modelo multi-bodega.

## Contexto (verificado en el código)

- `ForecastingCore/forecasting_core/data/profiler.py::_detect_freq` hoy solo muestrea el
  **primer SKU** del dataset para inferir la frecuencia — nunca compara entre SKUs, así
  que hoy no hay forma de detectar el caso heterogéneo.
- El horizonte de forecast es un único entero global (`ForecastConfigRequest.horizon = 14`
  en `backend/schemas/configuration.py`), sin segmentación por frecuencia.
- La sesión de entrenamiento ya guarda 6 blobs JSONB en `session_configs`
  (`columns_cfg`, `features_cfg`, `models_cfg`, `validation_cfg`, `forecast_cfg`,
  `business_cfg`), ensamblados por `backend/workers/runner.py::build_engine_config`.
- El wizard de `/quick-start` ya usa `GET /sessions/{id}/inspect` (profiler) para
  recomendar columnas antes de que el usuario las confirme en el paso 2.

## Regla de negocio

Antes de entrenar, el sistema detecta si los SKUs de un dataset comparten la misma
granularidad temporal. Si no la comparten, el pipeline **se detiene** y el usuario debe
resolver el conflicto explícitamente (mantener frecuencias nativas o agregar al máximo
común denominador) antes de poder configurar el horizonte y entrenar.

## A. Clasificación de frecuencia por SKU

Para cada SKU (grupo), se calcula la mediana del gap entre fechas consecutivas — la misma
técnica de `_detect_freq`, pero corrida **por SKU**, no solo para el primero — y se
clasifica:

| Gap mediano | Bucket |
|---|---|
| 1 día | `D` (Diaria) |
| 6-8 días | `W` (Semanal) |
| 13-15 días | `2W` (Catorcenal) |
| 28-35 días | `MS` (Mensual) |
| cualquier otro / historia insuficiente (<3 puntos) | `IRREGULAR` (advertencia aparte, no entra al conflicto de granularidad) |

- **Homogéneo:** todos los SKUs con bucket asignado (excluyendo `IRREGULAR`) caen en el
  mismo bucket → se avanza sin gate.
- **Heterogéneo:** 2+ buckets distintos presentes → se bloquea y se devuelve el payload
  de conflicto (Sección B).

## B. Contrato del payload (extensión de `GET /inspect`, sin endpoint nuevo)

Se agrega un campo `granularity` a la respuesta existente de `/inspect`. Por decisión del
usuario, el payload incluye la **lista completa de SKUs por bucket** (no solo conteos),
para que el modal pueda mostrar el detalle completo si el usuario lo necesita:

```json
"granularity": {
  "status": "homogeneous" | "conflict",
  "detected": ["D", "W"],
  "skus_by_frequency": {
    "D": ["SKU001", "SKU002", "..."],
    "W": ["SKU045", "SKU046", "..."]
  },
  "suggested_target": "W"
}
```

`suggested_target` = el bucket más grueso presente (preselecciona la Estrategia B en el
modal). Cuando `status = "homogeneous"`, `detected` tiene un solo elemento y
`skus_by_frequency` puede omitirse o venir con una sola clave. `detected` siempre viene
**ordenado de más fino a más grueso** (`D` → `W` → `2W` → `MS`) para que el frontend
nunca tenga que reordenar ni adivinar cuál es la frecuencia "mayor".

Los SKUs `IRREGULAR` (Sección A) no participan en `detected`/`skus_by_frequency` ni en la
determinación de conflicto — quedan fuera del gate. Bajo Estrategia A siguen su
comportamiento actual sin cambios; bajo Estrategia B se resamplean a `target_freq` igual
que el resto (el resampling no requiere que el SKU de origen tenga un patrón regular).

## C. Re-validación si el usuario corrige columnas

`/inspect` detecta la granularidad con las columnas fecha/SKU que el profiler
auto-detecta. Si el usuario las corrige en el paso 2 del wizard, `POST
/configure/columns` **vuelve a correr la misma detección** con las columnas confirmadas.
Si aparece un conflicto nuevo (o desaparece uno detectado antes), el modal se dispara ahí
también — es una red de seguridad barata sobre la misma función de detección, no un
endpoint ni una lógica nueva.

## D. Estrategias de resolución y resampling

**Nuevo blob de configuración `granularity_cfg`** (séptimo blob en `session_configs`,
mismo patrón que los 6 existentes):

```json
{ "strategy": "native" | "aggregate", "target_freq": "W" }
```
(`target_freq` solo aplica cuando `strategy = "aggregate"`; por defecto es el
`suggested_target` del payload de conflicto.)

- **Estrategia A — Nativa:** no se transforma el dataframe; cada SKU se entrena en su
  propia serie (el pipeline ya entrena por grupo). El `FeatureEngineer` elige lags/rolling
  windows según la frecuencia nativa de cada SKU (diario → días; semanal → semanas;
  mensual → meses) en vez de un único `features_cfg` global para todos.
- **Estrategia B — Agregación (downsampling):** un módulo nuevo
  `forecasting_core/data/resampler.py` agrupa cada SKU por `target_freq` y suma la
  demanda (`groupby(sku).resample(target_freq).sum()`), **en memoria, una sola vez por
  ejecución de entrenamiento** — el dataset original en disco no se modifica. El resto
  del pipeline (Trainer, modelos) no sabe que hubo resampling: recibe un dataframe ya
  homogéneo.

**Invocación:** `runner.py::build_engine_config` lee `granularity_cfg` igual que los otros
6 blobs; si `strategy == "aggregate"`, el pipeline de `ForecastingCore` llama al resampler
como primer paso antes de profiling/feature engineering.

## E. Configuración flexible del horizonte

**Extensión de `forecast_cfg`** (mismo blob existente, hoy `{horizon, quantiles}`):

```json
// Modalidad 1 — Global unificado
{ "horizon_mode": "unified", "horizon": 6, "quantiles": [0.1, 0.9] }

// Modalidad 2 — Segmentado por frecuencia (solo si hubo conflicto Y se eligió Estrategia A)
{ "horizon_mode": "segmented", "horizon_by_freq": { "D": 10, "W": 4 }, "quantiles": [0.1, 0.9] }
```

**Qué modalidad se muestra** (regla determinística):
- Sin conflicto nunca → Modalidad 1 (un campo, unidad de la única frecuencia detectada).
- Conflicto + Estrategia B → Modalidad 1 (un campo, unidad de `target_freq`).
- Conflicto + Estrategia A → Modalidad 2 (un campo por cada frecuencia en `detected`,
  generado dinámicamente — no hardcodeado).

**Límites de horizonte por bucket** (validación tanto en frontend como en Pydantic):

| Bucket | Rango permitido |
|---|---|
| `D` | 1-30 días |
| `W` | 1-12 semanas |
| `2W` | 1-6 catorcenas |
| `MS` | 1-12 meses |

**Consumo en el pipeline:** en `unified`, se pasa un escalar (como hoy). En `segmented`,
el pipeline busca el horizonte por el bucket nativo de cada SKU (mismo mapa por-SKU de la
Sección A) en vez de un escalar único — sin cambios en cómo Prophet/XGBoost usan
`horizon` internamente.

## Frontend — Modal de conciliación

Un modal/overlay (no una redirección) que se dispara cuando `/inspect` (o
`/configure/columns`) devuelve `granularity.status = "conflict"`. Reutiliza el patrón de
modal ya existente en la app (overlay + `stopPropagation`, ver
`Frontend/src/app/inventory/page.tsx`'s delete-confirmation modal de la Tarea 3 de UX).

- **Texto dinámico obligatorio**, construido desde `granularity.detected` — nunca un
  mensaje genérico. Ejemplo con 2 frecuencias: *"Detectamos SKUs en formato {A} y {B}.
  ¿Deseas convertir los datos de **{A} a {B}**?"* (donde `{A}` es la frecuencia más fina y
  `{B}` la más gruesa, tomadas del array `detected` ordenado).
- Con 3+ frecuencias en conflicto, el texto lista todas: *"Detectamos SKUs en formato
  {A}, {B} y {C}. ¿Deseas convertir todo a **{C}** (la más agregada)?"* — la Estrategia B
  siempre agrega al máximo común denominador (el bucket más grueso), no par por par.
- Lista completa de SKUs por bucket (decisión del usuario), agrupada y colapsable si es
  larga.
- Micro-copy educativo fijo por par de conversión (ej. *"Nota: al agrupar de Diario a
  Semanas, el motor de forecast se ejecutará en bloques de 7 días. Las alertas de compra
  se consolidarán semanalmente."*).
- Botones: "Mantener frecuencias nativas" (Estrategia A) / "Agregar a {bucket sugerido}"
  (Estrategia B, preseleccionada).
- Tras elegir, el modal se transforma (o el wizard avanza) a la pantalla de horizonte
  (Modalidad 1 o 2 según la Sección E).

## Pruebas (TESTING_GUIDELINES.md)

- **Backend:** dataset sintético con 2 SKUs diarios + 1 semanal → `/inspect` devuelve
  `status="conflict"`, `detected=["D","W"]`, `skus_by_frequency` correcto,
  `suggested_target="W"`. Dataset homogéneo → `status="homogeneous"`. SKU con <3 puntos →
  clasificado `IRREGULAR`, no cuenta para el conflicto.
- **Backend:** `POST /configure/columns` con columnas SKU/fecha corregidas que cambian el
  resultado de granularidad → la re-validación lo refleja.
- **Backend (resampler):** dataframe diario + semanal con `target_freq="W"` →
  `resample_to_frequency` produce una serie semanal por SKU con suma correcta de demanda,
  sin filas huérfanas.
- **Backend (horizonte):** `forecast_cfg` con `horizon_by_freq` fuera de los rangos de la
  tabla de límites → rechazado (422), asserts sobre el valor persistido, no solo el
  status code.
- **Frontend:** el modal renderiza el texto dinámico correcto para 2 y 3+ frecuencias en
  conflicto (verificación manual, ya que no hay test runner en Frontend/); Modalidad 1 vs
  2 se muestra según la regla determinística.

## Riesgos / notas

- El `FeatureEngineer` necesita defaults de lags/rolling por frecuencia para la
  Estrategia A — esto es una extensión de lógica existente, no una reescritura, pero
  debe implementarse con cuidado para no romper el comportamiento actual de sesiones
  homogéneas (que siguen usando `features_cfg` tal cual).
- El resampling con `sum()` es correcto para series de demanda/ventas; no se contempla
  otra función de agregación en esta ronda (podría añadirse después si aparece un caso de
  uso que la necesite).
- Los sub-proyectos 4-6 (bodega, MILP, PO/transferencias) quedan fuera de esta ronda; el
  contrato de datos que dejan listo (`granularity_cfg`, `forecast_cfg.horizon_by_freq`,
  el mapa por-SKU de frecuencia) es lo que esos sub-proyectos consumirán, pero su diseño
  detallado se hace en su propio ciclo de brainstorming.

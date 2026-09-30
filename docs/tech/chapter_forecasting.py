"""Technical manual — the forecasting engine.

Written against the code on 2026-09-14. The numbers here are the defaults in
`forecasting_core/config/config.py` unless a line says otherwise.
"""

CHAPTER = {
    "id": "forecasting",
    "es": {
        "title": "3. Pronóstico: de la serie a un número con intervalo",
        "intro": (
            "El motor vive completo en `ForecastingCore/` y no toca la base de datos: "
            "recibe una configuración, devuelve diccionarios y DataFrames, y es el "
            "backend quien persiste. Entrenar es por SKU, con validación hacia "
            "adelante, y el campeón se elige con una regla que el backend comparte "
            "literalmente — cuando divergieron, las dos capas discrepaban en 8 de 13 "
            "productos."
        ),
        "topics": [
            {
                "name": "Punto de entrada y orden del pipeline",
                "where": "forecasting_core/engine.py:68 → forecasting_core/pipelines/pipeline.py:162 · run",
                "what": (
                    "`ForecastEngine` expone una API fluida; `Pipeline.run()` hace el "
                    "trabajo en 16 etapas. El único puente desde el backend es "
                    "`backend/workers/runner.py:1312`."
                ),
                "how": [
                    "Lectura del archivo → validaciones (en modo AVISO, nunca abortan) → autocorrección → demanda censurada.",
                    "Calidad por SKU, clasificación de series y descarte de series cortas.",
                    "Enrutamiento de modelos → ingeniería de características → baselines.",
                    "Entrenamiento por SKU con validación hacia adelante, modelo global, modelos estadísticos. El modelo que se sirve se reajusta sobre TODO el histórico; el que se califica se queda en el corte.",
                    "Ensamble ponderado → tabla de métricas → pronósticos futuros → recomendaciones de inventario → registro de la corrida.",
                ],
                "caveats": [
                    "La reconciliación jerárquica (bottom_up / top_down / mint) existe pero está APAGADA por defecto: hierarchy.levels llega vacío (config.py:164).",
                    "El motor nunca escribe en base de datos. ModelRegistry.log_run escribe un JSON en disco (registry.json por defecto).",
                ],
            },
            {
                "name": "Enrutamiento de modelos",
                "where": "forecasting_core/training/router.py:30",
                "what": (
                    "La clasificación de la serie decide qué modelos tiene sentido "
                    "entrenarle. La regla que gobierna todo: el enrutamiento solo "
                    "puede ESTRECHAR la selección del usuario, nunca ampliarla."
                ),
                "table": [
                    ("short", "naive, seasonal_naive"),
                    ("intermittent", "croston, ets"),
                    ("seasonal", "prophet, ets, lightgbm, sarimax, lstm"),
                    ("stable", "lightgbm, xgboost, arima, sarimax, lstm"),
                    ("volatile", "lightgbm, xgboost, prophet, arima, ets, sarimax"),
                ],
                "formulas": [
                    ("La regla", "run = assigned & declared        # intersección, nunca unión",
                     "`declared` es la selección del usuario, verbatim. Si la intersección queda vacía, se cae a `declared` completo en vez de dejar el SKU sin modelo (router.py:80)."),
                ],
                "caveats": [
                    "Las etiquetas se unen antes de intersecar: una serie {seasonal, volatile} recibe la unión de ambas filas.",
                    "HUECO REAL: `naive` y `seasonal_naive` aparecen en la tabla para series cortas, pero NO existen como modelos entrenables en ModelFactory — solo como baselines. Una serie corta siempre cae en el fallback de intersección vacía.",
                    "`global_lgbm` está en UNIVERSAL_MODELS y se salta el estrechamiento, pero sigue condicionado a que el usuario lo haya declarado.",
                ],
            },
            {
                "name": "Modelos implementados",
                "where": "forecasting_core/models/factory.py:27 · ModelFactory",
                "what": (
                    "Nueve modelos entrenables más tres baselines. El paralelismo está "
                    "en el bucle de SKUs, por eso cada modelo corre con n_jobs=1."
                ),
                "table": [
                    ("lightgbm / xgboost", "n_estimators 300, learning_rate 0.05 por defecto. Los únicos con espacio de búsqueda en Optuna."),
                    ("global_lgbm", "Un LightGBM sobre todo el catálogo, multi-horizonte directo (el horizonte es una característica). 400 árboles, 63 hojas. Matriz apilada con techo de 2.000.000 de filas."),
                    ("arima", "statsmodels, orden (5,1,2). Reajusta sobre la serie completa para el futuro; conf_int(alpha=0.2) da p10/p90."),
                    ("sarimax", "Orden (1,1,1), estacional (1,1,1,período). Solo corre si el usuario lo seleccionó explícitamente."),
                    ("prophet", "Estacionalidades anual y semanal en 'auto' de Prophet — la anual solo con 2 años de historia, la semanal solo si los cubos son más finos que una semana. Diaria apagada. Recibe el calendario de feriados del tenant."),
                    ("ets", "Tendencia aditiva; estacionalidad aditiva solo si hay 2 períodos completos y toda la serie es positiva."),
                    ("croston", "Croston con la corrección Syntetos-Boylan, alpha 0.1. El cociente de dos suavizados es un estimador sesgado hacia arriba; SBA lo corrige multiplicando por (1 - alpha/2). No es TSB."),
                    ("lstm", "TensorFlow OPCIONAL: si no está instalado devuelve vacío con un aviso. Ventana 14, 50 épocas, parada temprana."),
                    ("baselines", "naive, seasonal_naive, historical_avg. Se puntúan para comparar y están EXCLUIDOS de ser campeón."),
                ],
                "caveats": [
                    "El esquema de sesión que expone la configuración (config.py:348) lista 8 modelos y OMITE global_lgbm, mientras que ModelFactory sí lo tiene. La selección por defecto del backend sí lo incluye.",
                    "Optuna está apagado por defecto y solo tiene espacio de búsqueda para lightgbm y xgboost; cualquier otro nombre devuelve parámetros intactos.",
                ],
            },
            {
                "name": "Características y la guardia de fuga",
                "where": "forecasting_core/features/engineer.py:47",
                "what": (
                    "Calendario, Fourier opcional, rezagos, diferencias, ventanas "
                    "móviles y EWM. Todo lo que mira hacia atrás se construye sobre "
                    "shift(1) para que la fila no pueda reconstruir su propio objetivo."
                ),
                "how": [
                    "Calendario: 19 columnas, el mismo constructor para entrenamiento e inferencia. Feriados por país desde la librería `holidays`, país por defecto CR (Costa Rica, decisión del dueño del 2026-09-30; antes CO); si falta la librería degrada a Pascua y Navidad, nunca a un conjunto vacío.",
                    "Rezagos [1, 7, 14]; diferencias [1, 7] calculadas sobre shift(1); ventanas [7, 14, 28] con media, desviación, mínimo, máximo y CV.",
                    "El dropna de calentamiento corre SOLO sobre columnas generadas y el objetivo — un dropna general llegó a vaciar datasets enteros.",
                ],
                "formulas": [
                    ("Guardia de fuga (trainer.py:266)",
                     "leak_cols = [c for c in X if is_numeric(X[c]) and X[c].astype(float).equals(y)]",
                     "Cualquier columna idénticamente igual al objetivo se descarta con un aviso que nombra el SKU. Apunta al alias canónico `demand`, que es copia literal de la columna del usuario."),
                ],
                "caveats": [
                    "`_drop_unservable_features` quita columnas que existen en el histórico pero no se pueden reproducir para una fecha futura (inventory, price, cost).",
                    "PARCIALMENTE INERTE: validation/leakage.py se llama sin `feature_columns`, así que solo dispara la comprobación de fechas desordenadas. Las comprobaciones de fuga por correlación (> 0.999) son inalcanzables desde el pipeline.",
                ],
            },
            {
                "name": "Validación y elección del campeón",
                "where": "forecasting_core/pipelines/pipeline.py:778 · _select_champions",
                "what": (
                    "Validación hacia adelante con ventana expansiva y un HUECO igual "
                    "al horizonte, para que la ventana de entrenamiento nunca toque "
                    "datos que el pronóstico de producción no habría tenido."
                ),
                "how": [
                    "ML por SKU: 3 cortes por defecto, entrenamiento mínimo del 40% de la serie, hueco = horizonte. Si no hay cortes viables, cae a una partición cronológica simple.",
                    "Dos puntajes por modelo a propósito: las métricas de corte son a 1 paso, y `horizon_metrics` corre el camino de inferencia REAL hacia adelante desde el último corte.",
                    "Modelo global: origen rodante sobre el eje de fechas, reajustando en cada corte.",
                    "Modelos estadísticos: una sola partición cronológica; su costo ya es un número a h pasos.",
                ],
                "formulas": [
                    ("Métrica principal", "WAPE = Σ|y − ŷ| / (Σ|y| + 1e-8)", "Además: MAE, RMSE, sesgo, MAPE, sMAPE, pinball."),
                    ("Costo asimétrico", "cost = mean(sobrante + 3.0 · faltante)",
                     "DEFAULT_STOCKOUT_MULTIPLIER = 3.0. Quedarse sin producto cuesta el triple que sobrar."),
                    ("Regla del campeón",
                     "metric = primera de (cost_horizon, cost, wape, mae) presente;  champion = argmin(metric)",
                     "Se excluyen las filas de baseline: un pronóstico ingenuo nunca puede ser campeón. Sin desempate explícito."),
                ],
                "caveats": [
                    "Si el mejor baseline le gana a todos los modelos, el SKU se anota en `outperformed_by_baseline` y aun así recibe su mejor modelo real.",
                    "El ensamble emite pronóstico pero NO tiene fila de métricas, así que nunca puede ser campeón.",
                    "MASE y CRPS no están implementadas en ninguna parte.",
                    "CHAMPION_METRIC_ORDER se comparte literalmente con backend/inventory/service.py::best_model_by_sku. Dejarlas divergir hizo que las dos capas discreparan en 8 de 13 SKUs.",
                ],
            },
            {
                "name": "Intervalos de predicción",
                "where": "forecasting_core/inference/predictor.py:29",
                "what": (
                    "Tres mecanismos distintos, y cuál te toca depende del modelo. "
                    "Esto importa más de lo que parece: el inventario extrae su sigma "
                    "de estos intervalos."
                ),
                "table": [
                    ("Aproximación normal", "bound = max(0, valor + z_q · std(residuos)). Banco de residuos fuera de muestra cuando existe. Es el camino de LightGBM/XGBoost, ETS, Croston y SARIMAX. Mismo ancho en el paso 1 y en el paso 30: no conoce el horizonte."),
                    ("Conformal por horizonte", "Solo global_lgbm. Cuantiles empíricos de los residuos del backtest, SEPARADOS por horizonte, mancomunados en unidades escaladas. Mínimo 30 residuos por horizonte; por debajo, toma prestado del banco general."),
                    ("Bandas propias", "ARIMA (IC al 80%), Prophet (yhat_lower/upper), LSTM (Monte-Carlo Dropout, 30 pasadas)."),
                ],
                "caveats": [
                    "Si no hay datos de calibración, el conformal devuelve una banda DEGENERADA (inferior = superior = valor) en vez de inventar un ancho.",
                    "`enforce_monotonic` repara cuantiles cruzados con un máximo corrido; `enforce_horizon_monotonic` hace no decrecientes las bandas acumuladas.",
                    "`lead_time_demand_quantile` entrega el cuantil de la demanda ACUMULADA sobre el lead time: es lo que consume el punto de reorden.",
                ],
            },
        ],
    },
    "en": {
        "title": "3. Forecasting: from a series to a number with an interval",
        "intro": (
            "The engine lives entirely in `ForecastingCore/` and never touches the "
            "database: it takes a configuration, returns dicts and DataFrames, and the "
            "backend persists them. Training is per SKU with walk-forward validation, "
            "and the champion is chosen by a rule the backend shares verbatim — when "
            "the two drifted, the layers disagreed on 8 of 13 products."
        ),
        "topics": [
            {
                "name": "Entry point and pipeline order",
                "where": "forecasting_core/engine.py:68 → forecasting_core/pipelines/pipeline.py:162 · run",
                "what": (
                    "`ForecastEngine` exposes a fluent API; `Pipeline.run()` does the "
                    "work in 16 stages. The only bridge from the backend is "
                    "`backend/workers/runner.py:1312`."
                ),
                "how": [
                    "File read → validations (WARNING mode, they never abort) → auto-correct → censored demand.",
                    "Per-SKU quality, series classification, and dropping series that are too short.",
                    "Model routing → feature engineering → baselines.",
                    "Per-SKU training with walk-forward validation, the global model, the statistical models. The model that is served is refitted on ALL the history; the one that is graded stops at the cutoff.",
                    "Weighted ensemble → metrics table → future forecasts → inventory recommendations → run registry.",
                ],
                "caveats": [
                    "Hierarchical reconciliation (bottom_up / top_down / mint) exists but is OFF by default: hierarchy.levels arrives empty (config.py:164).",
                    "The engine never writes to a database. ModelRegistry.log_run writes a JSON file on disk (registry.json by default).",
                ],
            },
            {
                "name": "Model routing",
                "where": "forecasting_core/training/router.py:30",
                "what": (
                    "The series classification decides which models are worth training "
                    "for it. The rule that governs everything: routing may only NARROW "
                    "the user's selection, never widen it."
                ),
                "table": [
                    ("short", "naive, seasonal_naive"),
                    ("intermittent", "croston, ets"),
                    ("seasonal", "prophet, ets, lightgbm, sarimax, lstm"),
                    ("stable", "lightgbm, xgboost, arima, sarimax, lstm"),
                    ("volatile", "lightgbm, xgboost, prophet, arima, ets, sarimax"),
                ],
                "formulas": [
                    ("The rule", "run = assigned & declared        # intersection, never union",
                     "`declared` is the user's selection, verbatim. If the intersection comes out empty it falls back to the full `declared` rather than leaving the SKU with no model (router.py:80)."),
                ],
                "caveats": [
                    "Labels are unioned before intersecting: a {seasonal, volatile} series gets the union of both rows.",
                    "REAL GAP: `naive` and `seasonal_naive` appear in the table for short series but do NOT exist as trainable models in ModelFactory — only as baselines. A short series therefore always hits the empty-intersection fallback.",
                    "`global_lgbm` is in UNIVERSAL_MODELS and bypasses the narrowing, but is still gated on the user having declared it.",
                ],
            },
            {
                "name": "Implemented models",
                "where": "forecasting_core/models/factory.py:27 · ModelFactory",
                "what": (
                    "Nine trainable models plus three baselines. Parallelism lives in "
                    "the SKU loop, which is why every model runs with n_jobs=1."
                ),
                "table": [
                    ("lightgbm / xgboost", "n_estimators 300, learning_rate 0.05 by default. The only two with an Optuna search space."),
                    ("global_lgbm", "One LightGBM over the whole catalogue, direct multi-horizon (the horizon is a feature). 400 trees, 63 leaves. Stacked matrix capped at 2,000,000 rows."),
                    ("arima", "statsmodels, order (5,1,2). Refits on the full series for the future forecast; conf_int(alpha=0.2) gives p10/p90."),
                    ("sarimax", "Order (1,1,1), seasonal (1,1,1,period). Only runs when the user selected it explicitly."),
                    ("prophet", "Yearly and weekly seasonality at Prophet's own 'auto' — yearly only with two years of history, weekly only when the buckets are finer than a week. Daily off. It receives the tenant's holiday calendar."),
                    ("ets", "Additive trend; additive seasonality only with 2 full periods and an all-positive series."),
                    ("croston", "Croston with the Syntetos-Boylan correction, alpha 0.1. The ratio of two smoothed quantities is biased upward; SBA corrects it by multiplying by (1 - alpha/2). Not TSB."),
                    ("lstm", "TensorFlow is OPTIONAL: without it the model returns empty with a logged warning. Window 14, 50 epochs, early stopping."),
                    ("baselines", "naive, seasonal_naive, historical_avg. Scored for comparison and EXCLUDED from being champion."),
                ],
                "caveats": [
                    "The session schema that exposes the configuration (config.py:348) lists 8 models and OMITS global_lgbm, while ModelFactory has it. The backend's own default selection does include it.",
                    "Optuna is off by default and only has a search space for lightgbm and xgboost; any other name gets its parameters back untouched.",
                ],
            },
            {
                "name": "Features and the leakage guard",
                "where": "forecasting_core/features/engineer.py:47",
                "what": (
                    "Calendar, optional Fourier, lags, diffs, rolling windows and EWM. "
                    "Everything backward-looking is built on shift(1) so a row cannot "
                    "reconstruct its own target."
                ),
                "how": [
                    "Calendar: 19 columns, the same builder for training and inference. Holidays per country from the `holidays` library, default country CR (Costa Rica, owner's decision of 2026-09-30; CO before); without the library it degrades to Easter and Christmas, never to an empty set.",
                    "Lags [1, 7, 14]; diffs [1, 7] computed on shift(1); rolling [7, 14, 28] with mean, std, min, max and CV.",
                    "The warm-up dropna runs ONLY over generated columns and the target — a blanket dropna once emptied whole datasets.",
                ],
                "formulas": [
                    ("Leakage guard (trainer.py:266)",
                     "leak_cols = [c for c in X if is_numeric(X[c]) and X[c].astype(float).equals(y)]",
                     "Any column identical to the target is dropped with a warning naming the SKU. It targets the canonical `demand` alias, a literal copy of the user's mapped column."),
                ],
                "caveats": [
                    "`_drop_unservable_features` removes columns present in history but not reproducible for a future date (inventory, price, cost).",
                    "PARTIALLY INERT: validation/leakage.py is called without `feature_columns`, so only the unsorted-dates check fires. The correlation-based leakage checks (> 0.999) are unreachable from the pipeline.",
                ],
            },
            {
                "name": "Validation and champion selection",
                "where": "forecasting_core/pipelines/pipeline.py:778 · _select_champions",
                "what": (
                    "Walk-forward validation with an expanding window and a GAP equal "
                    "to the horizon, so the training window never touches data the "
                    "production forecast could not have had."
                ),
                "how": [
                    "Per-SKU ML: 3 splits by default, minimum training window 40% of the series, gap = horizon. With no viable splits it falls back to a single chronological split.",
                    "Two scores per model, deliberately: fold metrics are 1-step, and `horizon_metrics` runs the REAL inference path forward from the final cutoff.",
                    "Global model: rolling origin over the date axis, refitting at each cutoff.",
                    "Statistical models: a single chronological split; their cost is already an h-step number.",
                ],
                "formulas": [
                    ("Primary metric", "WAPE = Σ|y − ŷ| / (Σ|y| + 1e-8)", "Also: MAE, RMSE, bias, MAPE, sMAPE, pinball."),
                    ("Asymmetric cost", "cost = mean(over + 3.0 · under)",
                     "DEFAULT_STOCKOUT_MULTIPLIER = 3.0. Running out costs three times what sitting on stock does."),
                    ("Champion rule",
                     "metric = first of (cost_horizon, cost, wape, mae) present;  champion = argmin(metric)",
                     "Baseline rows are excluded: a naive forecast can never be crowned. No explicit tie-break."),
                ],
                "caveats": [
                    "If the best baseline beats every model, the SKU is recorded in `outperformed_by_baseline` and still gets its best real model.",
                    "The ensemble emits a forecast but has NO metrics row, so it can never be champion.",
                    "MASE and CRPS are not implemented anywhere.",
                    "CHAMPION_METRIC_ORDER is shared verbatim with backend/inventory/service.py::best_model_by_sku. Letting them drift made the two layers disagree on 8 of 13 SKUs.",
                ],
            },
            {
                "name": "Prediction intervals",
                "where": "forecasting_core/inference/predictor.py:29",
                "what": (
                    "Three different mechanisms, and which one you get depends on the "
                    "model. This matters more than it looks: the inventory layer "
                    "extracts its sigma from these intervals."
                ),
                "table": [
                    ("Normal approximation", "bound = max(0, value + z_q · std(residuals)). Out-of-fold residual bank when available. This is the path for LightGBM/XGBoost, ETS, Croston and SARIMAX. Same width at step 1 and at step 30: nothing in the expression knows which step it is."),
                    ("Per-horizon conformal", "global_lgbm only. Empirical quantiles of the backtest residuals, computed SEPARATELY per horizon and pooled in scaled units. Minimum 30 residuals per horizon; below that the horizon borrows the pooled bank."),
                    ("Model-supplied bands", "ARIMA (80% CI), Prophet (yhat_lower/upper), LSTM (Monte-Carlo Dropout, 30 passes)."),
                ],
                "caveats": [
                    "With no calibration data at all, the conformal path returns a DEGENERATE band (lower = upper = value) rather than fabricating a width.",
                    "`enforce_monotonic` repairs crossed quantiles with a running max; `enforce_horizon_monotonic` makes cumulative bands non-decreasing in h.",
                    "`lead_time_demand_quantile` gives the quantile of CUMULATIVE demand over the lead time: that is what the reorder point consumes.",
                ],
            },
        ],
    },
}
